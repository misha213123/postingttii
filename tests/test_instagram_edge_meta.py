"""Tests for the local, persistent Microsoft Edge Instagram connector."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
