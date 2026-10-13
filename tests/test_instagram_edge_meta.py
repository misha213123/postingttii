"""Tests for the local, persistent Microsoft Edge Instagram connector."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from app import main


def request(client: str = "127.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/instagram/edge/6",
            "scheme": "http",
            "headers": [],
            "query_string": b"",
            "client": (client, 49999),
            "server": ("127.0.0.1", 8765),
        }
    )


class InstagramEdgeMetaTests(unittest.TestCase):
    def test_all_slots_have_independent_persistent_edge_profiles(self):
        with tempfile.TemporaryDirectory() as folder:
            config = SimpleNamespace(
                data_dir=Path(folder),
                port=8765,
                instagram_client_id="first-app",
                instagram_client_secret="first-secret",
                instagram_client_id_2="second-app",
                instagram_client_secret_2="second-secret",
            )
            with (
                patch.object(main, "settings", config),
                patch.object(main, "_edge_executable", return_value="msedge.exe"),
                patch.object(main.subprocess, "Popen") as launch,
            ):
                seen_profiles = set()
                for slot in range(1, 9):
                    result = main.open_instagram_edge(slot, request(), "connect")
                    self.assertEqual(result["meta_app"], 1)
                    command = launch.call_args.args[0]
                    self.assertEqual(command[-1], f"http://127.0.0.1:8765/connect/instagram/{slot}?meta_app=1")
                    self.assertIn("--new-window", command)
                    profile = f"--user-data-dir={Path(folder) / 'edge_instagram_profiles' / f'slot_{slot}'}"
                    self.assertIn(profile, command)
                    self.assertNotIn(profile, seen_profiles)
                    seen_profiles.add(profile)

                main.open_instagram_edge(6, request(), "connect")
                self.assertIn(
                    f"--user-data-dir={Path(folder) / 'edge_instagram_profiles' / 'slot_6'}",
                    launch.call_args.args[0],
                )

    def test_remote_client_cannot_launch_browser(self):
        with self.assertRaises(HTTPException) as error:
            main.open_instagram_edge(6, request("203.0.113.25"), "connect")
        self.assertEqual(error.exception.status_code, 403)

    def test_first_app_is_sufficient_for_slots_6_to_8(self):
        with tempfile.TemporaryDirectory() as folder:
            config = SimpleNamespace(
                data_dir=Path(folder), port=8765,
                instagram_client_id="first", instagram_client_secret="first-secret",
                instagram_client_id_2="", instagram_client_secret_2="",
            )
            with (
                patch.object(main, "settings", config),
                patch.object(main, "_edge_executable", return_value="msedge.exe"),
                patch.object(main.subprocess, "Popen") as launch,
            ):
                for slot in (6, 7, 8):
                    result = main.open_instagram_edge(slot, request(), "connect")
                    self.assertEqual(result["meta_app"], 1)
                    self.assertTrue(launch.called)

    def test_second_app_must_be_configured_when_explicitly_selected(self):
        with patch.object(
            main,
            "settings",
            SimpleNamespace(
                instagram_client_id="first",
                instagram_client_secret="first-secret",
                instagram_client_id_2="",
                instagram_client_secret_2="",
            ),
        ):
            with self.assertRaises(HTTPException) as error:
                main.open_instagram_edge(6, request(), "connect", meta_app=2)
        self.assertEqual(error.exception.status_code, 400)



class InstagramMetaOAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_query_meta_app_number_is_accepted_for_edge_and_oauth(self):
        # A browser always serializes query params as strings. Literal[1, 2]
        # was rejected as HTTP 422 before Edge could even launch.
        with tempfile.TemporaryDirectory() as folder:
            config = SimpleNamespace(
                data_dir=Path(folder), port=8765,
                instagram_client_id="first", instagram_client_secret="first-secret",
                instagram_redirect_uri="https://first.example/auth/instagram/callback",
                instagram_client_id_2="", instagram_client_secret_2="",
            )
            transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 49999))
            with (
                patch.object(main, "settings", config),
                patch.object(main, "_edge_executable", return_value="msedge.exe"),
                patch.object(main.subprocess, "Popen") as launch,
            ):
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
                    edge = await client.post("/api/instagram/edge/6?mode=connect&meta_app=1")
                    self.assertEqual(edge.status_code, 200, edge.text)
                    self.assertEqual(edge.json()["meta_app"], 1)
                    self.assertTrue(launch.called)
                    oauth = await client.get("/connect/instagram/6?meta_app=1")
                    self.assertEqual(oauth.status_code, 307, oauth.text)
                    self.assertIn("client_id=first", oauth.headers["location"])
                    invalid = await client.post("/api/instagram/edge/6?meta_app=3")
                    self.assertEqual(invalid.status_code, 400)

    async def test_slot_6_oauth_uses_first_app_and_saves_correct_app(self):
        config = SimpleNamespace(
            instagram_client_id="first-app", instagram_client_secret="first-secret",
            instagram_redirect_uri="https://example.com/auth/instagram/callback",
            instagram_client_id_2="second-app", instagram_client_secret_2="second-secret",
            instagram_redirect_uri_2="https://elsewhere.example/auth/instagram/callback",
        )
        from app.services import platforms
        with patch.object(main, "settings", config), patch.object(platforms, "settings", config):
            response = await main.connect("instagram", 6, meta_app=1)
            oauth_query = parse_qs(urlparse(response.headers["location"]).query)
            self.assertEqual(oauth_query["client_id"], ["first-app"])
            self.assertEqual(
                oauth_query["redirect_uri"],
                ["https://example.com/auth/instagram/callback"],
            )
            state = oauth_query["state"][0]
            with (
                patch.object(main, "instagram_exchange", new_callable=AsyncMock) as exchange,
                patch.object(main.store, "save") as save,
            ):
                exchange.return_value = {"id": "123", "label": "new-user", "access_token": "token"}
                await main.instagram_callback("code", state)
                exchange.assert_awaited_once_with("code", 6, meta_app=1)
                saved = save.call_args.args
                self.assertEqual(saved[:2], ("instagram", 6))
                self.assertEqual(saved[2]["meta_app"], 1)

    async def test_optional_meta_2_keeps_old_slot_mapping(self):
        config = SimpleNamespace(
            instagram_client_id="first-app", instagram_client_secret="first-secret",
            instagram_redirect_uri="https://example.com/auth/instagram/callback",
            instagram_client_id_2="second-app", instagram_client_secret_2="second-secret",
            instagram_redirect_uri_2="https://elsewhere.example/auth/instagram/callback",
        )
        from app.services import platforms
        with patch.object(main, "settings", config), patch.object(platforms, "settings", config):
            response = await main.connect("instagram", 7, meta_app=2)
            oauth_query = parse_qs(urlparse(response.headers["location"]).query)
            self.assertEqual(oauth_query["client_id"], ["second-app"])
            self.assertEqual(
                oauth_query["redirect_uri"],
                ["https://elsewhere.example/auth/instagram/callback"],
            )
            # Consume the test state, as a real callback would do.
            state = oauth_query["state"][0]
            main._consume_state(state, "instagram")
            main.OAUTH_INSTAGRAM_META_APPS.pop(state, None)


if __name__ == "__main__":
    unittest.main()
