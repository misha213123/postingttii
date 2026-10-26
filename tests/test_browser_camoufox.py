"""Camoufox browser integration: reuse existing local profile and proxy safely."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from app import browser_instagram as browser


class CamoufoxAccountTests(unittest.TestCase):
    def test_existing_profile_can_be_selected_under_home(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root) / "Camoufox" / "profiles" / "instagram_1"
            profile.mkdir(parents=True)
            with patch.object(Path, "home", return_value=Path(root)):
                self.assertEqual(browser._external_profile(str(profile)), str(profile.resolve()))

    def test_missing_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(Path, "home", return_value=Path(root)):
                with self.assertRaises(HTTPException) as error:
                    browser._external_profile(str(Path(root) / "not_found"))
                self.assertEqual(error.exception.status_code, 400)

    def test_previous_edge_account_remains_default(self):
        account = {"username": "older", "proxy": ""}
        view = browser._public_account("a" * 24, account)
        self.assertEqual(view["browser"], "edge")
        self.assertEqual(view["profile_path"], "")

    def test_camoufox_account_keeps_proxy_credentials_private(self):
        account = {
            "username": "demo",
            "browser": "camoufox",
            "profile_path": "",
            "proxy": "host.example:8080:username:private-password",
        }
        view = browser._public_account("a" * 24, account)
        self.assertEqual(view["browser"], "camoufox")
        self.assertEqual(view["proxy_label"], "http://host.example:8080")
        self.assertNotIn("private-password", str(view))


class CamoufoxLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_camoufox_session_launches_with_proxy_and_closes(self):
        profile_id = "f" * 24
        class Context:
            def __init__(self):
                self.listeners = {}
                self.pages = []
            def on(self, name, callback):
                self.listeners[name] = callback
        context = Context()
        options = {}
        class Manager:
            def __init__(self, **kwargs):
                options.update(kwargs)
                self.closed = False
            async def __aenter__(self):
                return context
            async def __aexit__(self, *_):
                self.closed = True
        module = types.ModuleType("camoufox")
        async_api = types.ModuleType("camoufox.async_api")
        async_api.AsyncCamoufox = Manager
        with tempfile.TemporaryDirectory() as root:
            external = Path(root) / "existing_profile"
            external.mkdir()
            with (
                patch.dict(sys.modules, {"camoufox": module, "camoufox.async_api": async_api}),
                patch.object(browser, "_account", return_value={
                    "username": "demo", "browser": "camoufox",
                    "profile_path": str(external),
                    "proxy": "res.example:5000:alice:secretpass",
                }),
                patch.object(browser, "_external_profile", return_value=str(external)),
                patch.dict(browser.CONTEXTS, {}, clear=True),
                patch.dict(browser.CAMOUFOX_HANDLES, {}, clear=True),
            ):
                result = await browser._context(profile_id)
                self.assertIs(result, context)
                self.assertEqual(options["user_data_dir"], str(external))
                self.assertTrue(options["persistent_context"])
                self.assertEqual(options["proxy"]["username"], "alice")
                self.assertEqual(options["proxy"]["password"], "secretpass")
                self.assertEqual(options["proxy"]["server"], "http://res.example:5000")
                self.assertNotIn("viewport", options)
                manager = browser.CAMOUFOX_HANDLES[profile_id]
                await browser.close_account_context(profile_id)
                self.assertTrue(manager.closed)
                self.assertNotIn(profile_id, browser.CONTEXTS)
                self.assertNotIn(profile_id, browser.CAMOUFOX_HANDLES)


if __name__ == "__main__":
    unittest.main()
