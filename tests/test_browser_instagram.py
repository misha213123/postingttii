"""Independent browser Instagram posting: proxy security, files, and job guards."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app import browser_instagram as browser


def local_request(method: str = "GET", origin: str | None = None) -> Request:
    headers = []
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request({"type": "http", "client": ("127.0.0.1", 6060),
                    "method": method, "path": "/api/browser-instagram",
                    "headers": headers})


class BrowserProxyTests(unittest.TestCase):
    def test_host_port_user_password_and_url_proxy_parsing(self):
        parsed = browser._parse_proxy("res.example.org:8899:login:secret123")
        self.assertEqual(parsed["server"], "http://res.example.org:8899")
        self.assertEqual(parsed["username"], "login")
        self.assertEqual(parsed["password"], "secret123")
        self.assertEqual(browser._masked_proxy("res.example.org:8899:login:secret123"),
                         "http://res.example.org:8899")
        self.assertEqual(browser._parse_proxy("http://user:pass@res.example.org:443")["username"], "user")

    def test_proxy_credentials_never_in_public_accounts(self):
        value = browser._public_account("a"*24, {
            "username": "sample", "proxy": "res.example.org:8899:login:topsecret",
        })
        self.assertNotIn("topsecret", json.dumps(value))
        self.assertNotIn("login", json.dumps(value))
        self.assertEqual(value["proxy"], "http://res.example.org:8899")

    def test_invalid_proxy_and_socks_auth_rejected(self):
        for address in ("http://missing-port.example.com", "not-a-proxy",
                        "ftp://proxy.example.com:8080", "socks5://login:pw@proxy.example.com:1080"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    browser._parse_proxy(address)

    def test_auth_proxy_url_encoded_safely(self):
        parsed = browser._parse_proxy("res.example.org:8000:user:p@ss:word")
        url = browser._httpx_proxy_url(parsed)
        self.assertIn("p%40ss%3Aword", url)
        self.assertEqual(browser._masked_proxy(url), "http://res.example.org:8000")


class BrowserAccountTests(unittest.TestCase):
    def test_encrypted_accounts_dont_write_credentials_in_plaintext(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.object(browser, "DATA_DIR", root), patch.object(
                browser, "ACCOUNTS_FILE", root/"browser-accounts.enc"
            ), patch.object(browser, "PROFILES_DIR", root/"profiles"):
                created = browser.add_account(browser.BrowserAccount(
                    username="@accountname",
                    proxy="proxy.example.com:8080:username:password-to-hide",
                ), local_request("POST"))
                self.assertEqual(created["username"], "accountname")
                self.assertNotIn("password-to-hide", str(created))
                raw = (root/"browser-accounts.enc").read_bytes()
                self.assertNotIn(b"password-to-hide", raw)
                self.assertEqual(len(browser._read()), 1)

    def test_non_local_api_blocked(self):
        outside = Request({"type": "http", "client": ("192.0.2.25", 4000),
                           "method": "POST", "path": "/api/browser-instagram",
                           "headers": []})
        with self.assertRaises(HTTPException) as err:
            browser._local(outside)
        self.assertEqual(err.exception.status_code, 403)


class BrowserPostingTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_confirmation_required_before_publish(self):
        with self.assertRaises(HTTPException) as err:
            await browser.publish(browser.PublishInput(
                accounts=["abc"], filenames=["file.mp4"], confirmed=False,
            ), local_request("POST"))
        self.assertEqual(err.exception.status_code, 400)

    async def test_publishing_stops_on_uncertain_share_result(self):
        job = {"status": "running", "cancel": False, "items": [],
               "progress": 0, "accounts": ["a"], "total": 2}
        with (
            patch.object(browser, "_context", new=AsyncMock(return_value=object())),
            patch.object(browser, "_read", return_value={"a": {"username": "demo"}}),
            patch.object(browser, "_publish_via_browser", new=AsyncMock(return_value="needs_review")) as pub,
        ):
            await browser._run_job(job, [("a", Path("a.mp4")), ("a", Path("b.mp4"))], "text")
        self.assertEqual(job["status"], "needs_review")
        self.assertEqual(pub.await_count, 1)
        self.assertEqual(job["progress"], 1)

    async def test_browser_account_session_is_closed_before_proxy_edit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with (patch.object(browser, "DATA_DIR", root),
                  patch.object(browser, "ACCOUNTS_FILE", root/"saved.enc"),
                  patch.object(browser, "PROFILES_DIR", root/"profiles")):
                profile = browser.add_account(browser.BrowserAccount(
                    username="otheraccount", proxy=""
                ), local_request("POST"))
                key = profile["id"]
                with patch.dict(browser.CONTEXTS, {key: object()}, clear=True):
                    with self.assertRaises(HTTPException) as error:
                        browser.set_proxy(key, browser.ProxyInput(proxy="proxy.example:8080"), local_request("PUT"))
                self.assertEqual(error.exception.status_code, 409)
