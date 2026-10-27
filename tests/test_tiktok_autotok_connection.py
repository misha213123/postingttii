"""TikTok AutoTok account linking and session validation without uploads."""
from __future__ import annotations

import asyncio
import os
import unittest
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
