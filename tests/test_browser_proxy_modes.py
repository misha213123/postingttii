"""Explicit Direct / Proxy launch paths for existing browser Instagram accounts."""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import browser_instagram as browser


def local_request() -> Request:
    return Request({
        "type": "http", "client": ("127.0.0.1", 46212),
        "method": "PUT", "path": "/api/browser-instagram/account/demo/proxy-mode",
        "headers": [(b"origin", f"http://127.0.0.1:{browser.settings.port}".encode())],
    })


class ProxyModesTests(unittest.TestCase):
    def test_legacy_accounts_inherit_current_network_mode(self):
        self.assertEqual(browser._proxy_mode({"username": "alice", "proxy": ""}), "direct")
        self.assertEqual(
            browser._proxy_mode({"username": "alice", "proxy": "example.org:5000:a:b"}),
            "proxy",
        )

    def test_direct_does_not_apply_saved_proxy(self):
        account = {
            "username": "alice", "proxy_mode": "direct",
            "proxy": "example.org:5000:a:private-secret",
        }
        self.assertEqual(browser._launch_proxy_options(account), {})
        view = browser._public_account("a" * 24, account)
        self.assertEqual(view["proxy_mode"], "direct")
        self.assertTrue(view["proxy_configured"])
        self.assertNotIn("private-secret", str(view))

    def test_proxy_mode_loads_credentials_only_when_requested(self):
        account = {
            "username": "alice", "proxy_mode": "proxy",
            "proxy": "example.org:5000:a:private-secret",
        }
        proxy = browser._launch_proxy_options(account)["proxy"]
        self.assertEqual(proxy["server"], "http://example.org:5000")
        self.assertEqual(proxy["username"], "a")
        self.assertEqual(proxy["password"], "private-secret")

    def test_proxy_mode_without_saved_proxy_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            browser._launch_proxy_options({"proxy_mode": "proxy", "proxy": ""})
        self.assertEqual(raised.exception.status_code, 400)

    def test_toggle_direct_retains_saved_proxy(self):
        key = "d" * 24
        accounts = {key: {
            "username": "alice", "browser": "camoufox",
            "proxy_mode": "proxy", "proxy": "example.org:5000:a:secret",
        }}
        with (
            patch.object(browser, "_read", return_value=accounts),
            patch.object(browser, "_write") as save,
            patch.object(browser, "CURRENT_JOB", None),
            patch.dict(browser.CONTEXTS, {}, clear=True),
            patch.dict(browser.CAMOUFOX_HANDLES, {}, clear=True),
        ):
            result = browser.set_account_proxy_mode(
                key, browser.ProxyModeInput(mode="direct"), local_request(),
            )
            self.assertEqual(result["proxy_mode"], "direct")
            self.assertEqual(accounts[key]["proxy"], "example.org:5000:a:secret")
            browser.set_account_proxy_mode(
                key, browser.ProxyModeInput(mode="proxy"), local_request(),
            )
            self.assertEqual(accounts[key]["proxy_mode"], "proxy")
            save.assert_called()

    def test_open_browser_must_be_closed_before_switch(self):
        key = "f" * 24
        with (
            patch.dict(browser.CONTEXTS, {key: object()}, clear=True),
            patch.object(browser, "CURRENT_JOB", None),
        ):
            with self.assertRaises(HTTPException) as raised:
                browser.set_account_proxy_mode(
                    key, browser.ProxyModeInput(mode="direct"), local_request(),
                )
            self.assertEqual(raised.exception.status_code, 409)

    def test_old_proxy_endpoint_still_enables_proxy_or_direct(self):
        key = "c" * 24
        accounts = {key: {"username": "alice", "browser": "camoufox"}}
        with (
            patch.dict(browser.CONTEXTS, {}, clear=True),
            patch.dict(browser.CAMOUFOX_HANDLES, {}, clear=True),
            patch.object(browser, "_read", return_value=accounts),
            patch.object(browser, "_write"),
            patch.object(browser, "CURRENT_JOB", None),
        ):
            browser.set_account_proxy(
                key, browser.ProxyInput(proxy="example.org:5000:a:secret"),
                local_request(),
            )
            self.assertEqual(accounts[key]["proxy_mode"], "proxy")
            browser.set_account_proxy(
                key, browser.ProxyInput(proxy=""), local_request(),
            )
            self.assertEqual(accounts[key]["proxy_mode"], "direct")


class DirectCamoufoxLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_mode_explicitly_disables_saved_firefox_manual_proxy(self):
        account_id = "e" * 24
        opts = {}
        class Context:
            pages = []
            def on(self, *_): pass
        class Manager:
            def __init__(self, **kwargs):
                opts.update(kwargs)
            async def __aenter__(self):
                return Context()
            async def __aexit__(self, *_):
                pass
        module = types.ModuleType("camoufox")
        async_api = types.ModuleType("camoufox.async_api")
        async_api.AsyncCamoufox = Manager
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with (
                patch.dict(sys.modules, {"camoufox": module, "camoufox.async_api": async_api}),
                patch.object(browser, "_account", return_value={
                    "username": "alice", "browser": "camoufox",
                    "proxy_mode": "direct", "proxy": "example.org:5000:a:secret",
                    "profile_path": str(path),
                }),
                patch.object(browser, "_external_profile", return_value=str(path)),
                patch.dict(browser.CONTEXTS, {}, clear=True),
                patch.dict(browser.CAMOUFOX_HANDLES, {}, clear=True),
            ):
                await browser._context(account_id)
                self.assertNotIn("proxy", opts)
                self.assertEqual(opts["firefox_user_prefs"]["network.proxy.type"], 0)
                await browser.close_account_context(account_id)


if __name__ == "__main__":
    unittest.main()
