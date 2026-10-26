"""Account-wide Reel covers and user-selected posting pauses for browser mode."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app import browser_instagram as browser


def request(method="PUT"):
    return Request({
        "type": "http", "client": ("127.0.0.1", 56421),
        "method": method,
        "path": "/api/browser-instagram/account/cover",
        "headers": [(b"origin", f"http://127.0.0.1:{browser.settings.port}".encode())],
    })


class AccountCoverTests(unittest.TestCase):
    def test_account_cover_persists_and_is_visible_in_card(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cover_id = "f" * 24
            (root / (cover_id + ".jpg")).write_bytes(b"dummy jpeg")
            account_id = "a" * 24
            with (
                patch.object(browser, "DATA_DIR", root),
                patch.object(browser, "COVERS_DIR", root),
                patch.object(browser, "ACCOUNTS_FILE", root / "accounts.enc"),
                patch.object(browser, "CURRENT_JOB", None),
            ):
                browser._write({account_id: {"username": "test", "proxy": ""}})
                response = browser.set_account_cover(
                    account_id, browser.AccountCoverInput(cover_id=cover_id), request(),
                )
                self.assertEqual(response["cover_id"], cover_id)
                self.assertTrue(response["cover_url"].endswith("/" + cover_id))
                self.assertEqual(browser._read()[account_id]["cover_id"], cover_id)
                cleared = browser.set_account_cover(
                    account_id, browser.AccountCoverInput(cover_id=""), request(),
                )
                self.assertEqual(cleared["cover_id"], "")
                self.assertEqual(browser._read()[account_id]["cover_id"], "")

    def test_cannot_assign_invalid_cover(self):
        account_id = "b" * 24
        with (
            patch.object(browser, "CURRENT_JOB", None),
            patch.object(browser, "_read", return_value={
                account_id: {"username": "demo", "proxy": ""},
            }),
        ):
            with self.assertRaises(HTTPException) as failure:
                browser.set_account_cover(
                    account_id, browser.AccountCoverInput(cover_id="c" * 24), request()
                )
            self.assertEqual(failure.exception.status_code, 400)

    def test_cannot_change_cover_during_running_job(self):
        account_id = "d" * 24
        with patch.object(browser, "CURRENT_JOB", {"status": "running"}):
            with self.assertRaises(HTTPException) as failure:
                browser.set_account_cover(
                    account_id, browser.AccountCoverInput(cover_id=""), request()
                )
        self.assertEqual(failure.exception.status_code, 409)


class DelayAndCoverQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_cover_applied_to_each_video_of_the_same_account_with_one_pause(self):
        cover = Path("profile-cover.jpg")
        first = Path("video-one.mp4")
        second = Path("video-two.mp4")
        job = {"status": "running", "cancel": False, "items": [],
               "progress": 0, "accounts": ["account"], "total": 2}
        with (
            patch.object(browser, "_read", return_value={
                "account": {"username": "demo"},
            }),
            patch.object(browser, "_context", new=AsyncMock(return_value=object())),
            patch.object(browser, "_publish_via_browser",
                         new=AsyncMock(return_value="published")) as upload,
            patch.object(browser, "_wait_between_posts",
                         new=AsyncMock(return_value=True)) as waiting,
            patch.object(browser, "move_confirmed_video", return_value=Path("posted.mp4")),
        ):
            await browser._run_job(
                job, [("account", first), ("account", second)],
                "caption", covers={"account": cover},
                min_delay_minutes=50, max_delay_minutes=80,
            )
        self.assertEqual(job["status"], "completed")
        self.assertEqual(upload.await_count, 2)
        self.assertEqual(upload.await_args_list[0].kwargs["cover"], cover)
        self.assertEqual(upload.await_args_list[1].kwargs["cover"], cover)
        waiting.assert_awaited_once_with(job, 50, 80)

    async def test_delay_can_be_cancelled_without_next_upload(self):
        job = {"status": "running", "cancel": False, "items": [],
               "progress": 0, "accounts": ["account"], "total": 2}
        async def request_stop(*_):
            job["cancel"] = True
        with (
            patch.object(browser.random, "uniform", return_value=1.0),
            patch.object(browser.asyncio, "sleep", new=AsyncMock(side_effect=request_stop)),
        ):
            success = await browser._wait_between_posts(job, 1, 1)
        self.assertFalse(success)
        self.assertFalse(job["waiting"])
        self.assertIsNone(job["next_publish_at"])
        self.assertEqual(job["pause_remaining_seconds"], 0)

    async def test_zero_delay_does_not_sleep(self):
        job = {"cancel": False}
        with patch.object(browser.asyncio, "sleep", new=AsyncMock()) as sleeper:
            success = await browser._wait_between_posts(job, 0, 0)
        self.assertTrue(success)
        sleeper.assert_not_awaited()

    async def test_bad_min_max_rejected_before_queue_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "clip.mp4"
            clip.write_bytes(b"video")
            with (
                patch.object(browser, "_read", return_value={
                    "account": {"username": "demo", "proxy": ""},
                }),
                patch.object(browser, "video_path", return_value=clip),
                patch.object(browser, "CURRENT_TASK", None),
            ):
                with self.assertRaises(HTTPException) as failure:
                    await browser.publish(
                        browser.PublishInput(
                            accounts=["account"], filenames=["clip.mp4"],
                            min_delay_minutes=80, max_delay_minutes=50,
                            confirmed=True,
                        ), request("POST"),
                    )
                self.assertEqual(failure.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
