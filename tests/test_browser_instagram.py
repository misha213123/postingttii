"""Safety and functionality regressions for standalone browser Instagram posting."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app import browser_instagram as browser


def request(method="GET", ip="127.0.0.1", origin=None):
    headers = []
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request({"type": "http", "client": (ip, 6010),
                    "method": method, "path": "/api/browser-instagram",
                    "headers": headers})


class BrowserAccountTests(unittest.TestCase):
    def test_browser_accounts_do_not_change_meta_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(browser, "DATA_DIR", root),
                patch.object(browser, "ACCOUNTS_FILE", root / "browser.enc"),
                patch.object(browser, "PROFILES_DIR", root / "profiles"),
                patch.object(browser.store, "save") as meta_save,
            ):
                first = browser.add_account(browser.BrowserAccount(username="@demoaccount"), request("POST"))
                second = browser.add_account(browser.BrowserAccount(username="otheraccount"), request("POST"))
                self.assertEqual(len(browser._read()), 2)
                self.assertEqual(first["username"], "demoaccount")
                self.assertEqual(second["username"], "otheraccount")
                meta_save.assert_not_called()
                content = (root / "browser.enc").read_bytes()
                self.assertNotIn(b"demoaccount", content)
                self.assertNotIn(b"otheraccount", content)

    def test_invalid_username_and_duplicate_account_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(browser, "DATA_DIR", root), patch.object(
                browser, "ACCOUNTS_FILE", root / "browser.enc"
            ):
                browser.add_account(browser.BrowserAccount(username="@myaccount"), request("POST"))
                with self.assertRaises(HTTPException) as caught:
                    browser.add_account(browser.BrowserAccount(username="myaccount"), request("POST"))
                self.assertEqual(caught.exception.status_code, 409)
                with self.assertRaises(HTTPException):
                    browser.add_account(browser.BrowserAccount(username="bad/username"), request("POST"))

    def test_local_browser_api_denies_nonlocal_and_cross_site(self):
        with self.assertRaises(HTTPException) as outside:
            browser._local(request(ip="192.0.2.5"))
        self.assertEqual(outside.exception.status_code, 403)
        with self.assertRaises(HTTPException) as bad_origin:
            browser._local(request(origin="https://external.example"))
        self.assertEqual(bad_origin.exception.status_code, 403)
        browser._local(request(origin=f"http://127.0.0.1:{browser.settings.port}"))

    def test_private_profiles_are_scoped_to_valid_identifiers(self):
        with self.assertRaises(HTTPException):
            browser._profile_dir("../different-account")


class BrowserPostingTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_confirmation_required(self):
        with self.assertRaises(HTTPException) as caught:
            await browser.publish(browser.PublishInput(
                accounts=["abc"], filenames=["clip.mp4"],
                confirmed=False,
            ), request("POST"))
        self.assertEqual(caught.exception.status_code, 400)

    async def test_no_duplicate_auto_retry_after_uncertain_share(self):
        job = {"status": "running", "cancel": False, "items": [],
               "progress": 0, "accounts": ["a"], "total": 2}
        with (
            patch.object(browser, "_context", new=AsyncMock(return_value=object())),
            patch.object(browser, "_read", return_value={"a": {"username": "demo"}}),
            patch.object(browser, "_publish_via_browser",
                         new=AsyncMock(return_value="needs_review")) as uploader,
        ):
            await browser._run_job(job, [("a", Path("first.mp4")), ("a", Path("second.mp4"))], "text")
        self.assertEqual(job["status"], "needs_review")
        self.assertEqual(uploader.await_count, 1)
        self.assertEqual(job["progress"], 1)

    async def test_browser_failure_stops_next_post(self):
        job = {"status": "running", "cancel": False, "items": [],
               "progress": 0, "accounts": ["a"], "total": 2}
        with (
            patch.object(browser, "_context", new=AsyncMock(side_effect=Exception("failure"))),
            patch.object(browser, "_read", return_value={"a": {"username": "demo"}}),
        ):
            await browser._run_job(job, [("a", Path("first.mp4")), ("a", Path("second.mp4"))], "")
        self.assertEqual(job["status"], "stopped")
        self.assertEqual(job["progress"], 1)
        self.assertNotIn("failure", json.dumps(job))

    async def test_manual_publish_uses_registered_folder_and_deduplicates_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clip.mp4").write_bytes(b"fake video bytes")
            store = {"acct": {"username": "sample"}}
            with (
                patch.object(browser, "_read", return_value=store),
                patch.object(browser, "video_path", return_value=root/"clip.mp4") as resolve,
                patch.object(browser, "CURRENT_TASK", None),
                patch.object(browser, "CURRENT_JOB", None),
                patch.object(browser.asyncio, "create_task") as task,
            ):
                coro_holder=[]
                def create_task(coro):
                    coro_holder.append(coro)
                    coro.close()
                    return type("StubTask", (), {"done": lambda self: False})()
                task.side_effect=create_task
                result = await browser.publish(browser.PublishInput(
                    accounts=["acct", "acct"], filenames=["clip.mp4", "clip.mp4"],
                    folder_id="custom-folder", confirmed=True,
                ), request("POST"))
            self.assertEqual(result["total"], 1)
            resolve.assert_called_once_with("custom-folder", "clip.mp4")
