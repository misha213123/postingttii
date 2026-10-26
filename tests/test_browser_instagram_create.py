"""Regression coverage for Instagram Create button on icon-only Camoufox sidebar."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from app import browser_instagram as browser


class FakeLocator:
    def __init__(self, succeeds=False, attached=False):
        self.succeeds = succeeds
        self.attached = attached
        self.click_calls = 0
        self.wait_calls = 0

    @property
    def first(self):
        return self

    async def click(self, timeout=0):
        self.click_calls += 1
        if not self.succeeds:
            raise TimeoutError("selector not found")

    async def wait_for(self, state=None, timeout=0):
        self.wait_calls += 1
        if not self.attached:
            raise TimeoutError("upload dialog not shown")


class FakePage:
    def __init__(self, *, icon=True, existing_file=False):
        self.file = FakeLocator(attached=existing_file)
        self.icon = FakeLocator(succeeds=icon)
        self.missing = FakeLocator()
        self.goto = AsyncMock()

    def locator(self, selector):
        if selector == 'input[type="file"]':
            return self.file
        if selector.startswith('svg[aria-label='):
            return self.icon
        return self.missing

    def get_by_role(self, kind, name=None):
        return self.missing

    def get_by_text(self, name):
        return self.missing


class InstagramCreateRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_icon_only_sidebar_clicks_new_post(self):
        page = FakePage(icon=True)
        await browser._open_instagram_create(page)
        self.assertEqual(page.icon.click_calls, 1)
        page.goto.assert_not_awaited()

    async def test_existing_file_dialog_does_not_click_create_twice(self):
        page = FakePage(existing_file=True)
        await browser._open_instagram_create(page)
        self.assertEqual(page.icon.click_calls, 0)
        page.goto.assert_not_awaited()

    async def test_all_missing_falls_back_to_first_party_creator_page(self):
        page = FakePage(icon=False)
        async def navigate(*_, **__):
            page.file.attached = True
        page.goto.side_effect = navigate
        await browser._open_instagram_create(page)
        page.goto.assert_awaited_once()
        self.assertIn("www.instagram.com/create/select/", page.goto.call_args.args[0])

    async def test_missing_create_reports_specific_error_without_proxy_secrets(self):
        page = FakePage(icon=False)
        with self.assertRaises(RuntimeError) as problem:
            await browser._open_instagram_create(page)
        self.assertIn("Создать", str(problem.exception))
        error = browser._brief_error(
            RuntimeError("Не найдена кнопка «Создать» в Instagram.")
        )
        self.assertIn("Создать", error)
        self.assertNotIn("Edge/Camoufox или Instagram", error)

    def test_external_browser_errors_never_include_secret_url(self):
        message = browser._brief_error(
            RuntimeError("proxy http://user:secretpassword@server.example:8000")
        )
        self.assertNotIn("secretpassword", message)


if __name__ == "__main__":
    unittest.main()
