"""Regression tests for background Camoufox and Instagram file-picker uploads."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from starlette.requests import Request
from fastapi import HTTPException

from app import browser_instagram as browser


def request():
    return Request({
        "type": "http", "client": ("127.0.0.1", 44599),
        "method": "PUT", "path": "/api/browser-instagram/account/a/headless",
        "headers": [(b"origin", f"http://127.0.0.1:{browser.settings.port}".encode())],
    })


class HeadlessSettingsTests(unittest.TestCase):
    def test_existing_edge_and_camoufox_accounts_default_to_visible(self):
        self.assertFalse(browser._public_account("a" * 24, {
            "username": "edge_user", "browser": "edge",
        })["headless"])
        self.assertFalse(browser._public_account("b" * 24, {
            "username": "fox_user", "browser": "camoufox",
        })["headless"])
        self.assertTrue(browser._public_account("c" * 24, {
            "username": "fox_user", "browser": "camoufox", "headless": True,
        })["headless"])

    def test_background_setting_is_saved_for_camoufox(self):
        key = "d" * 24
        accounts = {key: {"username": "demo", "browser": "camoufox"}}
        with (
            patch.object(browser, "_read", return_value=accounts),
            patch.object(browser, "_write") as write,
            patch.object(browser, "CURRENT_JOB", None),
            patch.dict(browser.CONTEXTS, {}, clear=True),
            patch.dict(browser.CAMOUFOX_HANDLES, {}, clear=True),
        ):
            result = browser.configure_camoufox_headless(
                key, browser.HeadlessInput(enabled=True), request()
            )
            self.assertTrue(result["headless"])
            write.assert_called_once_with(accounts)
            self.assertTrue(accounts[key]["headless"])

    def test_cannot_switch_live_session_or_edge(self):
        key = "a" * 24
        accounts = {key: {"username": "demo", "browser": "camoufox"}}
        with (
            patch.object(browser, "_read", return_value=accounts),
            patch.object(browser, "CURRENT_JOB", None),
            patch.dict(browser.CONTEXTS, {key: object()}, clear=True),
        ):
            with self.assertRaises(HTTPException) as error:
                browser.configure_camoufox_headless(
                    key, browser.HeadlessInput(enabled=True), request()
                )
            self.assertEqual(error.exception.status_code, 409)
        accounts[key]["browser"] = "edge"
        with (
            patch.object(browser, "_read", return_value=accounts),
            patch.object(browser, "CURRENT_JOB", None),
            patch.dict(browser.CONTEXTS, {}, clear=True),
        ):
            with self.assertRaises(HTTPException) as error:
                browser.configure_camoufox_headless(
                    key, browser.HeadlessInput(enabled=True), request()
                )
            self.assertEqual(error.exception.status_code, 400)


class MockControl:
    def __init__(self, parent, action="missing"):
        self.parent = parent
        self.action = action

    @property
    def first(self): return self

    @property
    def last(self): return self

    async def is_visible(self):
        return False

    async def click(self, timeout=0):
        if self.action == "post":
            self.parent.upload_ready = True
        elif self.action == "pick":
            self.parent.chooser_triggered = True
        else:
            raise TimeoutError("button not visible")

    async def wait_for(self, state="", timeout=0):
        if self.action != "video_input" or not self.parent.upload_ready:
            raise TimeoutError("no input")

    async def set_input_files(self, filename, timeout=0):
        if not self.parent.upload_ready:
            raise TimeoutError("input not yet mounted")
        self.parent.files.append(filename)


class MockChooser:
    def __init__(self, parent):
        self.parent = parent

    async def set_files(self, filename):
        self.parent.files.append(filename)


class MockFileChooserEvent:
    def __init__(self, parent):
        self.parent = parent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        if not self.parent.chooser_triggered:
            raise TimeoutError("no file chooser")

    @property
    def value(self):
        async def get():
            return MockChooser(self.parent)
        return get()


class MockPage:
    def __init__(self, *, chooser=False, initial_input=False, menu=False):
        self.chooser = chooser
        self.upload_ready = initial_input
        self.menu = menu
        self.chooser_triggered = False
        self.files = []

    def get_by_role(self, kind, name=None):
        if kind == "button" and self.chooser:
            return MockControl(self, "pick")
        return MockControl(self)

    def get_by_text(self, pattern):
        if self.menu and pattern.search("Post"):
            return MockControl(self, "post")
        return MockControl(self)

    def locator(self, selector):
        if selector.startswith("input[type=") or selector.startswith("[role="):
            return MockControl(self, "video_input")
        return MockControl(self)

    def expect_file_chooser(self, timeout=0):
        return MockFileChooserEvent(self)


class FilePickerTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_chooser_upload_works_without_visible_file_input(self):
        page = MockPage(chooser=True)
        await browser._choose_instagram_video(page, Path("first.mp4"))
        self.assertEqual(page.files, ["first.mp4"])

    async def test_attached_input_upload_works_when_chooser_absent(self):
        page = MockPage(initial_input=True)
        await browser._choose_instagram_video(page, Path("second.mp4"))
        self.assertEqual(page.files, ["second.mp4"])

    async def test_post_menu_is_opened_before_selecting_file(self):
        page = MockPage(menu=True)
        await browser._choose_instagram_video(page, Path("third.mp4"))
        self.assertTrue(page.upload_ready)
        self.assertEqual(page.files, ["third.mp4"])

    async def test_unknown_instagram_ui_stops_with_safe_error(self):
        page = MockPage()
        with self.assertRaises(RuntimeError) as error:
            await browser._choose_instagram_video(page, Path("fourth.mp4"))
        self.assertIn("Select from computer", str(error.exception))
        self.assertEqual(page.files, [])


if __name__ == "__main__":
    unittest.main()
