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


class ProxySettingsTests(unittest.TestCase):
    def test_residential_proxy_host_port_credentials_parsed(self):
        from app import browser_proxy as proxy
        parsed = proxy.parse_proxy("res.example.com:9000:hello:s3cret:tail")
        self.assertEqual(parsed["server"], "http://res.example.com:9000")
        self.assertEqual(parsed["username"], "hello")
        self.assertEqual(parsed["password"], "s3cret:tail")
        self.assertEqual(proxy.parse_proxy("res.example.com:9000")["server"],
                         "http://res.example.com:9000")

    def test_uri_proxy_and_socks5_no_auth(self):
        from app import browser_proxy as proxy
        parsed = proxy.parse_proxy("http://name:pa%40ss@host.example:8080")
        self.assertEqual(parsed["username"], "name")
        self.assertEqual(parsed["password"], "pa@ss")
        self.assertEqual(proxy.parse_proxy("socks5://host.example:1080")["server"],
                         "socks5://host.example:1080")
        self.assertIsNone(proxy.parse_proxy(""))

    def test_provider_format_with_and_without_http_scheme(self):
        from app import browser_proxy as proxy
        suffix="user-country-af"
        plain=proxy.parse_proxy("198.51.100.15:5000:"+suffix+":somepass")
        prefixed=proxy.parse_proxy("http://198.51.100.15:5000:"+suffix+":somepass")
        for parsed in (plain,prefixed):
            self.assertEqual(parsed["server"],"http://198.51.100.15:5000")
            self.assertEqual(parsed["username"],suffix)
            self.assertEqual(parsed["password"],"somepass")

    def test_provider_format_escapes_special_chars_in_password(self):
        from app import browser_proxy as proxy
        expected="a:@?#%tail"
        parsed=proxy.parse_proxy("http://res.example:9000:username:"+expected)
        self.assertEqual(parsed["password"],expected)
        self.assertEqual(parsed["server"],"http://res.example:9000")
        self.assertEqual(proxy.masked_proxy("res.example:9000:username:"+expected),
                         "http://res.example:9000")
        url=proxy.httpx_proxy(parsed)
        self.assertIn("a%3A%40%3F%23%25tail",url)
        self.assertNotIn(expected,url)

    def test_uri_user_password_format_without_scheme(self):
        from app import browser_proxy as proxy
        parsed=proxy.parse_proxy("name:pa%40ss@res.example:8080")
        self.assertEqual(parsed["server"],"http://res.example:8080")
        self.assertEqual(parsed["username"],"name")
        self.assertEqual(parsed["password"],"pa@ss")

    def test_malformed_prefixed_proxy_reports_safe_format_hint(self):
        from app import browser_proxy as proxy
        with self.assertRaises(ValueError) as ex:
            proxy.parse_proxy("http://198.51.100.12:invalid:user:secret")
        msg=str(ex.exception)
        self.assertNotIn("secret",msg)
        self.assertIn("IP:порт:логин:пароль",msg)

    def test_no_secret_in_displayed_fields(self):
        from app import browser_proxy as proxy
        full = "residential.example:10000:user:secret-password"
        masked = proxy.masked_proxy(full)
        self.assertEqual(masked, "http://residential.example:10000")
        self.assertNotIn("user", masked)
        self.assertNotIn("secret-password", masked)
        safe = browser._public_account("a"*24, {"username": "mytest", "proxy": full})
        self.assertTrue(safe["proxy_configured"])
        self.assertEqual(safe["proxy_label"], masked)
        self.assertNotIn("secret-password", json.dumps(safe))

    def test_rejects_invalid_and_unsupported_authentication(self):
        from app import browser_proxy as proxy
        values = [
            "residential.example:0",
            "residential.example:70000",
            "not-valid",
            "ftp://host.example:3128",
            "http://host.example",
            "socks5://user:password@residential.example:1080",
            "http://user@residential.example:8000",
        ]
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    proxy.parse_proxy(value)

    def test_proxy_url_encodes_credentials_only_in_memory(self):
        from app import browser_proxy as proxy
        spec = proxy.parse_proxy("res.example:9000:hello:p@ss:word")
        url = proxy.httpx_proxy(spec)
        self.assertIn("p%40ss%3Aword", url)
        self.assertEqual(proxy.masked_proxy(url), "http://res.example:9000")

    def test_existing_account_stays_valid_without_proxy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(browser, "PROFILES_DIR", root):
                safe=browser._public_account("a"*24, {"username": "existing"})
                self.assertFalse(safe["proxy_configured"])
                self.assertEqual(safe["proxy_label"], "Без прокси")

    def test_saved_proxy_is_encrypted_and_does_not_touch_meta(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with (
                patch.object(browser, "DATA_DIR", root),
                patch.object(browser, "ACCOUNTS_FILE", root/"accounts.enc"),
                patch.object(browser, "PROFILES_DIR", root/"profiles"),
                patch.object(browser.store, "save") as meta_save,
            ):
                created=browser.add_account(browser.BrowserAccount(
                    username="proxyowner",
                    proxy="res.example.com:8080:alice:secretpass",
                ), request("POST"))
                raw=(root/"accounts.enc").read_bytes()
                self.assertNotIn(b"secretpass", raw)
                self.assertNotIn("secretpass", json.dumps(created))
                self.assertEqual(browser._read()[created["id"]]["proxy"],
                                 "res.example.com:8080:alice:secretpass")
                meta_save.assert_not_called()

    def test_proxy_change_requires_closed_profile_and_can_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with (
                patch.object(browser, "DATA_DIR", root),
                patch.object(browser, "ACCOUNTS_FILE", root/"accounts.enc"),
                patch.object(browser, "PROFILES_DIR", root/"profiles"),
                patch.object(browser, "CURRENT_JOB", None),
            ):
                created=browser.add_account(browser.BrowserAccount(username="changeme"), request("POST"))
                account_id=created["id"]
                with patch.dict(browser.CONTEXTS, {account_id: object()}, clear=True):
                    with self.assertRaises(HTTPException) as caught:
                        browser.set_account_proxy(
                            account_id,
                            browser.ProxyInput(proxy="res.example.com:8080"),
                            request("PUT"),
                        )
                    self.assertEqual(caught.exception.status_code, 409)
                selected=browser.set_account_proxy(
                    account_id,
                    browser.ProxyInput(proxy="res.example.com:8080:username:password"),
                    request("PUT"),
                )
                self.assertTrue(selected["proxy_configured"])
                cleared=browser.set_account_proxy(
                    account_id,browser.ProxyInput(proxy=""),request("PUT"),
                )
                self.assertFalse(cleared["proxy_configured"])

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


class BrowserProxyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_edge_context_uses_correct_per_account_proxy(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as folder:
            context=object()
            launch=AsyncMock(return_value=context)
            fake_driver=SimpleNamespace(chromium=SimpleNamespace(
                launch_persistent_context=launch,
            ))
            account_id="a"*24
            with (
                patch.object(browser, "PROFILES_DIR", Path(folder)),
                patch.object(browser, "_driver", new=AsyncMock(return_value=fake_driver)),
                patch.object(browser, "_account", return_value={
                    "username":"demo",
                    "proxy":"res.example.com:8080:user:secret",
                }),
                patch.dict(browser.CONTEXTS, {}, clear=True),
            ):
                selected=await browser._context(account_id)
                self.assertIs(selected,context)
                options=launch.await_args.kwargs
                self.assertEqual(options["proxy"]["server"],"http://res.example.com:8080")
                self.assertEqual(options["proxy"]["username"],"user")
                self.assertEqual(options["proxy"]["password"],"secret")

    async def test_proxy_ip_check_reports_ip_and_latency_without_auth(self):
        from app import browser_proxy as proxy
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        class FakeResponse:
            def raise_for_status(self): pass
            def json(self): return {"ip":"203.0.113.12"}
        class FakeClient:
            def __init__(self,**kwargs):
                self.options=kwargs
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def get(self,url):
                self.called_url=url
                return FakeResponse()
        with patch.object(proxy.httpx,"AsyncClient",side_effect=FakeClient) as fake:
            result=await proxy.check_proxy("res.example:8080:hello:s3cret")
        self.assertTrue(result["ok"])
        self.assertEqual(result["ip"],"203.0.113.12")
        self.assertGreaterEqual(result["latency_ms"],0)
        self.assertNotIn("s3cret",json.dumps(result))
        self.assertIn("hello:s3cret@", fake.call_args.kwargs["proxy"])

    async def test_proxy_connect_timeout_reports_specific_message_without_secrets(self):
        from app import browser_proxy as proxy
        class DelayedClient:
            def __init__(self,**kwargs): pass
            async def __aenter__(self):
                raise proxy.httpx.ConnectTimeout("private credential should not appear")
            async def __aexit__(self,*args): pass
        with patch.object(proxy.httpx, "AsyncClient", side_effect=DelayedClient):
            result=await proxy.check_proxy("res.example:8080:owner:privatepass")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "connect_timeout")
        self.assertIn("Тайм-аут",result["message"])
        self.assertNotIn("privatepass",json.dumps(result))

    async def test_proxy_auth_407_is_reported(self):
        from app import browser_proxy as proxy
        class AuthFailure:
            status_code = 407
        class FakeClient:
            def __init__(self,**kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def get(self,url): return AuthFailure()
        with patch.object(proxy.httpx, "AsyncClient", side_effect=FakeClient):
            result=await proxy.check_proxy("res.example:8080:owner:secretpass")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "auth_required")
        self.assertEqual(result["http_status"], 407)
        self.assertNotIn("secretpass", json.dumps(result))

    async def test_proxy_read_timeout_is_not_confused_with_bad_credentials(self):
        from app import browser_proxy as proxy
        class FakeClient:
            def __init__(self,**kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def get(self,url):
                raise proxy.httpx.ReadTimeout("proxy owner:password@host")
        with patch.object(proxy.httpx,"AsyncClient",side_effect=FakeClient):
            result=await proxy.check_proxy("res.example:8080:owner:password")
        self.assertEqual(result["code"], "read_timeout")
        self.assertNotIn("owner:password",json.dumps(result))

    async def test_proxy_invalid_ip_response_has_diagnostic_category(self):
        from app import browser_proxy as proxy
        class FakeResponse:
            status_code = 200
            def json(self): return {"ip": "not-an-ip"}
        class FakeClient:
            def __init__(self,**kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def get(self,url): return FakeResponse()
        with patch.object(proxy.httpx,"AsyncClient",side_effect=FakeClient):
            result=await proxy.check_proxy("res.example:8080")
        self.assertEqual(result["code"], "bad_ip_response")

    async def test_proxy_connection_error_does_not_expose_credentials(self):
        from app import browser_proxy as proxy
        class BrokenClient:
            def __init__(self,**kwargs): pass
            async def __aenter__(self):
                raise proxy.httpx.ConnectError("proxy user:password@host:80 failed")
            async def __aexit__(self,*args): pass
        with patch.object(proxy.httpx,"AsyncClient",side_effect=BrokenClient):
            result=await proxy.check_proxy("res.example:8080:user:password")
        self.assertFalse(result["ok"])
        self.assertNotIn("password",json.dumps(result))
