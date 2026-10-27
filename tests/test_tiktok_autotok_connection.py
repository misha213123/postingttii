"""TikTok AutoTok account linking and session validation without uploads."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app.services import autotok_bridge, autotok_routes


def local_request():
    return Request({
        "type": "http",
        "client": ("127.0.0.1", 12345),
        "method": "POST",
        "path": "/api/autotok/verify/1",
        "headers": [],
    })


class AutoTokSessionCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_cli_exit_confirms_session_with_new_wording(self):
        with patch.object(
            autotok_bridge, "run_cli",
            new=AsyncMock(return_value=(0, "TikTok authentication verified")),
        ):
            await autotok_bridge.check_account("account1")

    async def test_rejected_session_not_accepted(self):
        with patch.object(
            autotok_bridge, "run_cli",
            new=AsyncMock(return_value=(0, "session rejected")),
        ):
            with self.assertRaises(RuntimeError):
                await autotok_bridge.check_account("account1")

    async def test_cli_failure_not_accepted(self):
        with patch.object(
            autotok_bridge, "run_cli",
            new=AsyncMock(return_value=(1, "access denied")),
        ):
            with self.assertRaises(RuntimeError):
                await autotok_bridge.check_account("account2")

    async def test_default_second_account_uses_standard_autotok_uploader(self):
        account = {"provider": "autotok", "autotok_account": "account2"}
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(autotok_bridge.store, "get", return_value=account),
            patch.object(autotok_bridge, "run_cli", new=AsyncMock(
                return_value=(0, "Published. Video id: tiktok_post_123")
            )) as cli,
            patch("pathlib.Path.is_file", return_value=True),
        ):
            result = await autotok_bridge.publish(2, __import__("pathlib").Path("clip.mp4"), "test")
        self.assertEqual(result["provider"], "autotok")
        args = cli.await_args.args
        self.assertEqual(args[:2], ("upload", "-u"))
        self.assertEqual(args[2], "account2")

    async def test_account5_in_slot2_uses_browser_only_when_enabled(self):
        account = {"provider": "autotok", "autotok_account": "account5"}
        clip = Path("new_clip.mp4")
        with (
            patch.dict(os.environ, {"TIKTOK_ACCOUNT2_BROWSER": "1"}),
            patch.object(autotok_bridge.store, "get", return_value=account),
            patch("pathlib.Path.is_file", return_value=True),
            patch("app.services.tiktok_browser_upload.publish_browser_account2",
                  return_value={"provider": "browser", "slot": 2}) as browser,
            patch.object(autotok_bridge, "run_cli", new=AsyncMock()) as cli,
        ):
            result = await autotok_bridge.publish(2, clip, "description")
        self.assertEqual(result["provider"], "browser")
        browser.assert_called_once_with(clip, "description", "account5")
        cli.assert_not_awaited()

    async def test_browser_mode_is_disabled_by_default_for_account5(self):
        account = {"provider": "autotok", "autotok_account": "account5"}
        with (
            patch.dict(os.environ, {"TIKTOK_ACCOUNT2_BROWSER": "0"}),
            patch.object(autotok_bridge.store, "get", return_value=account),
            patch.object(autotok_bridge, "run_cli", new=AsyncMock(
                return_value=(0, "Published. Video id: tiktok_post_456")
            )) as cli,
            patch.object(autotok_bridge, "_write_upload_diagnostic"),
            patch("pathlib.Path.is_file", return_value=True),
        ):
            result = await autotok_bridge.publish(2, Path("clip.mp4"), "test")
        self.assertEqual(result["provider"], "autotok")
        cli.assert_awaited_once()


class TikTokDiagnosticTests(unittest.TestCase):
    def test_browser_session_rejects_invalid_alias_before_access(self):
        from app.services.tiktok_browser_upload import _session
        with self.assertRaises(RuntimeError):
            _session("../account5")

    def test_diagnostic_contains_only_metadata_no_caption_or_cli_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"test video data")
            local_settings = SimpleNamespace(data_dir=Path(tmp))
            with patch.object(autotok_bridge, "settings", local_settings):
                autotok_bridge._write_upload_diagnostic(
                    2, "account5", video, "PRIVATE CAPTION TEXT", 1,
                    "TikTok rejected the post: Invalid parameters (status_code=5) SECRET_CLI",
                )
            paths = list((Path(tmp) / "tiktok_autotok_diagnostics").glob("*.json"))
            self.assertEqual(len(paths), 1)
            raw = paths[0].read_text(encoding="utf-8")
            metadata = json.loads(raw)
            self.assertEqual(metadata["status_code"], 5)
            self.assertEqual(metadata["filename"], "clip.mp4")
            self.assertEqual(metadata["result"], "rejected")
            self.assertNotIn("PRIVATE CAPTION TEXT", raw)
            self.assertNotIn("SECRET_CLI", raw)


class TikTokInvalidParametersTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_parameters_code9_is_rejected_and_never_marked_published(self):
        account = {"provider": "autotok", "autotok_account": "account5"}
        with (
            patch.object(autotok_bridge.store, "get", return_value=account),
            patch.object(autotok_bridge, "run_cli", new=AsyncMock(
                return_value=(1, "TikTok rejected the post: Invalid parameters (status_code=9)")
            )) as upload,
            patch("pathlib.Path.is_file", return_value=True),
        ):
            with self.assertRaises(RuntimeError) as failure:
                await autotok_bridge.publish(
                    2, __import__("pathlib").Path("tg_memfunny_75.mp4"), ""
                )
        self.assertIn("status_code=9", str(failure.exception))
        self.assertIn("Автоповтора нет", str(failure.exception))
        upload.assert_awaited_once()

    async def test_even_zero_exit_cannot_hide_tiktok_api_rejection(self):
        account = {"provider": "autotok", "autotok_account": "account5"}
        with (
            patch.object(autotok_bridge.store, "get", return_value=account),
            patch.object(autotok_bridge, "run_cli", new=AsyncMock(
                return_value=(0, "Invalid parameters (status_code=9)")
            )),
            patch("pathlib.Path.is_file", return_value=True),
        ):
            with self.assertRaises(RuntimeError):
                await autotok_bridge.publish(
                    2, __import__("pathlib").Path("another.mp4"), ""
                )


class TikTokIdentityDisplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_is_manual_and_keeps_existing_session_unchanged(self):
        account = {
            "provider": "autotok", "autotok_account": "account5",
            "label": "account5", "id": "account5",
        }
        with (
            patch.object(autotok_routes.store, "get", return_value=account),
            patch.object(autotok_routes.store, "save") as save,
        ):
            response = await autotok_routes.set_tiktok_identity(
                2, autotok_routes.TikTokIdentityRequest(username="@sample_tiktok"),
                local_request(),
            )
        self.assertEqual(response["username"], "sample_tiktok")
        self.assertEqual(response["source"], "manual")
        self.assertIn("/@sample_tiktok", response["profile_url"])
        payload = save.call_args.args[2]
        self.assertEqual(payload["autotok_account"], "account5")
        self.assertEqual(payload["tiktok_username"], "sample_tiktok")

    async def test_profile_url_can_be_pasted_as_username(self):
        result = autotok_routes._normalize_tiktok_username(
            "https://www.tiktok.com/@sample_tiktok?lang=ru"
        )
        self.assertEqual(result, "sample_tiktok")


class TikTokVerificationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_saved_account_success(self):
        account = {"provider": "autotok", "autotok_account": "account1"}
        with (
            patch.object(autotok_routes.store, "get", return_value=account),
            patch.object(autotok_routes.autotok_bridge, "check_account",
                         new=AsyncMock(return_value=None)) as check,
        ):
            result = await autotok_routes.verify_connected(1, local_request())
        self.assertTrue(result["ready"])
        check.assert_awaited_once_with("account1")

    async def test_verify_does_not_accept_missing_session(self):
        with patch.object(autotok_routes.store, "get", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                await autotok_routes.verify_connected(1, local_request())
        self.assertEqual(raised.exception.status_code, 404)

    async def test_completed_relogin_preserves_existing_slot(self):
        account = {"provider": "autotok", "autotok_account": "account1"}
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
        with (
            patch.object(autotok_routes.store, "get", return_value=account),
            patch.object(autotok_routes.autotok_bridge, "check_account",
                         new=AsyncMock(return_value=None)),
            patch.object(autotok_routes, "_save") as save,
        ):
            await autotok_routes._complete_login(1, "account1", process, reconnect=True)
        self.assertEqual(autotok_routes.LOGINS[1]["status"], "done")
        save.assert_not_called()
        autotok_routes.LOGINS.clear()


if __name__ == "__main__":
    unittest.main()
