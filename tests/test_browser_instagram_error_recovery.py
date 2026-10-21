"""Regression coverage for Instagram browser UI errors and stale sessions."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app import browser_instagram as browser


def req() -> Request:
    return Request({
        "type": "http", "client": ("127.0.0.1", 55000),
        "method": "POST", "path": "/api/browser-instagram/account/open",
        "headers": [(b"origin", f"http://127.0.0.1:{browser.settings.port}".encode())],
    })


class BrowserOpenRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_while_publishing_does_not_reload_upload_page(self):
        account = "a"*24
        page = SimpleNamespace(
            url="https://www.instagram.com/create/",
            goto=AsyncMock(),
            bring_to_front=AsyncMock(),
        )
        ctx = SimpleNamespace(pages=[page])
        with (
            patch.dict(browser.CONTEXTS, {account: ctx}, clear=True),
            patch.object(browser, "CURRENT_JOB", {
                "status": "running", "accounts": [account],
            }),
            patch.object(browser, "_context", new=AsyncMock(return_value=ctx)),
        ):
            answer = await browser.open_account(account, req())
        self.assertTrue(answer["ok"])
        page.goto.assert_not_awaited()
        page.bring_to_front.assert_awaited_once()

    async def test_open_during_launch_does_not_start_another_window(self):
        account = "a"*24
        with (
            patch.dict(browser.CONTEXTS, {}, clear=True),
            patch.object(browser, "CURRENT_JOB", {
                "status": "running", "accounts": [account],
            }),
            patch.object(browser, "_context", new=AsyncMock()) as context,
        ):
            with self.assertRaises(HTTPException) as error:
                await browser.open_account(account, req())
        self.assertEqual(error.exception.status_code, 409)
        context.assert_not_awaited()

    async def test_open_failed_navigation_reports_http_502_not_html_500(self):
        account = "b"*24
        page = SimpleNamespace(
            url="https://www.instagram.com/",
            goto=AsyncMock(side_effect=RuntimeError("sensitive proxy details")),
            bring_to_front=AsyncMock(),
        )
        context = SimpleNamespace(pages=[page])
        with (
            patch.object(browser, "CURRENT_JOB", None),
            patch.object(browser, "_context", new=AsyncMock(return_value=context)),
        ):
            with self.assertRaises(HTTPException) as error:
                await browser.open_account(account, req())
        self.assertEqual(error.exception.status_code, 502)
        self.assertNotIn("sensitive proxy details", str(error.exception.detail))

    async def test_manually_closed_edge_context_evicted(self):
        account = "c"*24

        class StubContext:
            def __init__(self):
                self.listeners = {}
            def on(self, name, callback):
                self.listeners[name] = callback

        context = StubContext()
        launch = AsyncMock(return_value=context)
        driver = SimpleNamespace(chromium=SimpleNamespace(
            launch_persistent_context=launch,
        ))
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(browser, "PROFILES_DIR", Path(folder)),
                patch.object(browser, "_account", return_value={
                    "username": "demo", "proxy": "",
                }),
                patch.object(browser, "_driver", new=AsyncMock(return_value=driver)),
                patch.dict(browser.CONTEXTS, {}, clear=True),
            ):
                opened = await browser._context(account)
                self.assertIs(opened, context)
                self.assertIs(browser.CONTEXTS[account], context)
                context.listeners["close"]()
                self.assertNotIn(account, browser.CONTEXTS)


class BrowserWorkflowRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_navigation_falls_back_to_explained_step(self):
        page = SimpleNamespace(
            url="https://www.instagram.com",
            goto=AsyncMock(side_effect=RuntimeError("secret-password")),
        )
        context = SimpleNamespace(pages=[page])
        item = {}
        with self.assertRaises(RuntimeError) as error:
            await browser._publish_via_browser(
                context, Path("clip.mp4"), "caption", item=item,
            )
        self.assertEqual(item["step"], "open_instagram")
        self.assertIn("instagram.com", str(error.exception))
        self.assertNotIn("secret-password", str(error.exception))

    async def test_failed_upload_stage_stops_before_next_video(self):
        job = {
            "status": "running", "cancel": False, "items": [],
            "progress": 0, "accounts": ["acct"], "total": 2,
        }
        async def failing(ctx, path, caption, item=None):
            browser._stage(item, "choose_video", "Выбираю файл видео")
            raise RuntimeError("Не удалось выбрать видео в Instagram.")
        with (
            patch.object(browser, "_context", new=AsyncMock(return_value=object())),
            patch.object(browser, "_read", return_value={
                "acct": {"username": "demo"},
            }),
            patch.object(browser, "_publish_via_browser",
                         new=AsyncMock(side_effect=failing)) as uploader,
        ):
            await browser._run_job(job, [
                ("acct", Path("first.mp4")), ("acct", Path("second.mp4")),
            ], "")
        self.assertEqual(job["status"], "stopped")
        self.assertEqual(uploader.await_count, 1)
        self.assertEqual(job["progress"], 1)
        self.assertEqual(job["items"][0]["step"], "choose_video")
        self.assertIn("выбрать видео", job["items"][0]["message"])

    def test_playwright_internal_error_does_not_reveal_proxy_password(self):
        exc = RuntimeError("http://myuser:secretpass@proxy.example.com:8000")
        message = browser._brief_error(exc)
        self.assertNotIn("secretpass", message)
        self.assertIn("Edge", message)
