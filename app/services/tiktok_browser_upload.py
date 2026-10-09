"""Experimental TikTok Studio UI publisher for the second *connected* account.

Use only if AutoTok's normal web-upload API rejects that account. This calls
TikTok's ordinary creator UI in an installed Chrome browser. No raw TikTok
publish API, CAPTCHA solving, stealth modifications, or automatic retries.

Only a verified redirect to TikTok Studio's content page is treated as
success; ambiguous failures are not retried because they might duplicate posts.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.config import settings


STUDIO_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
POST_BUTTON = re.compile(r"^(Post|Publish|Опубликовать|Публикуване|Publier)$", re.I)
PUBLIC_OPTION = re.compile(r"^(Everyone|Public|Все|Для всех)$", re.I)


def _session(alias: str) -> tuple[list[dict], str | None]:
    """Read AutoTok's existing login locally; do not print/return cookie values."""
    root = Path(os.getenv("AUTOTOK_HOME") or (Path.home() / ".autotok"))
    path = root / "accounts" / f"{alias}.json"
    if not path.is_file():
        raise RuntimeError("AutoTok: отсутствует сохранённая сессия " + alias)

    data = json.loads(path.read_text(encoding="utf-8"))
    cookies = []
    for raw in data.get("cookies", []):
        name, value = raw.get("name"), raw.get("value")
        domain = str(raw.get("domain") or ".tiktok.com")
        if not name or not value or not domain.lstrip(".").endswith("tiktok.com"):
            continue
        cookie = {
            "name": str(name), "value": str(value),
            "domain": domain, "path": str(raw.get("path") or "/"),
            "secure": bool(raw.get("secure", True)),
        }
        if raw.get("httpOnly") is not None:
            cookie["httpOnly"] = bool(raw["httpOnly"])
        same_site = str(raw.get("sameSite") or "")
        if same_site in ("Strict", "Lax", "None"):
            cookie["sameSite"] = same_site
        expires = raw.get("expires")
        if isinstance(expires, (int, float)) and expires > time.time():
            cookie["expires"] = float(expires)
        cookies.append(cookie)

    if not any(c["name"] == "sessionid" for c in cookies):
        raise RuntimeError("AutoTok: в сохранённом аккаунте отсутствует sessionid")
    if data.get("proxy"):
        raise RuntimeError(
            "Для account2 настроен прокси. Браузерный маршрут не активируется, "
            "чтобы не отправлять публикацию с другого адреса."
        )
    return cookies, data.get("user_agent")


def _scopes(page):
    yield page
    for frame in page.frames:
        if frame != page.main_frame:
            yield frame


def _find_file_input(page):
    for scope in _scopes(page):
        locator = scope.locator('input[type="file"]')
        if locator.count():
            return locator.first
    return None


def _find_editor(page):
    for scope in _scopes(page):
        locators = [
            scope.locator('[contenteditable="true"][role="textbox"]'),
            scope.locator('[contenteditable="true"]'),
        ]
        for locator in locators:
            for n in range(min(locator.count(), 5)):
                element = locator.nth(n)
                if element.is_visible():
                    return element
    return None


def _find_post_button(page):
    for scope in _scopes(page):
        locator = scope.get_by_role("button", name=POST_BUTTON)
        for n in range(min(locator.count(), 8)):
            element = locator.nth(n)
            if element.is_visible():
                return element
    return None


def _dismiss_onboarding(page):
    for scope in _scopes(page):
        for label in ("Not now", "Skip", "Maybe later", "Got it"):
            button = scope.get_by_role("button", name=label, exact=True)
            if button.count() and button.first.is_visible():
                button.first.click(timeout=3000)
                return


def _public_selected(page) -> bool:
    """Fail closed if TikTok Studio does not visibly offer public audience."""
    for scope in _scopes(page):
        choice = scope.get_by_text(PUBLIC_OPTION)
        for n in range(min(choice.count(), 8)):
            if choice.nth(n).is_visible():
                return True
    return False


def _diagnostic_screenshot(page) -> str:
    destination = settings.data_dir / "tiktok_browser_errors"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"account2_{int(time.time())}.png"
    try:
        page.screenshot(path=str(path), full_page=False, timeout=10000)
        return str(path)
    except Exception:
        return "не удалось сохранить скриншот"


def publish_browser_account2(video: Path, caption: str, alias: str = "account2") -> dict:
    """One autonomous browser upload. Never attempt a second post automatically."""
    if alias != "account2":
        raise RuntimeError("Браузерный режим разрешён только для account2")
    if not video.is_file():
        raise RuntimeError("Не найден файл: " + video.name)
    cookies, user_agent = _session(alias)

    with sync_playwright() as playwright:
        # Google Chrome is already installed on this user's Windows computer.
        # Visible Chrome helps local diagnosis; no human clicking is required.
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context_options = {"locale": "en-US", "viewport": {"width": 1440, "height": 900}}
        if user_agent:
            context_options["user_agent"] = user_agent
        context = browser.new_context(**context_options)
        context.add_cookies(cookies)
        page = context.new_page()
        posted = False
        try:
            page.goto(STUDIO_UPLOAD_URL, wait_until="domcontentloaded", timeout=90000)
            if "login" in page.url.lower():
                raise RuntimeError("TikTok перенаправил на вход; проверь сохранённую сессию account2")

            file_input = None
            for _ in range(90):
                _dismiss_onboarding(page)
                file_input = _find_file_input(page)
                if file_input:
                    break
                if "login" in page.url.lower():
                    raise RuntimeError("Вход в TikTok Studio не подтверждён")
                page.wait_for_timeout(1000)
            if file_input is None:
                raise RuntimeError("TikTok Studio не показал поле загрузки MP4")

            file_input.set_input_files(str(video), timeout=30000)
            editor = None
            for _ in range(120):
                editor = _find_editor(page)
                if editor:
                    break
                page.wait_for_timeout(1000)
            if editor is None:
                raise RuntimeError("TikTok Studio не показал редактор описания")
            editor.fill(caption[:2200], timeout=15000)

            # Do not override an account's privacy/age-related restrictions.
            # Abort before clicking Post if the UI does not show a public option.
            if not _public_selected(page):
                raise RuntimeError("Не вижу в TikTok Studio настройки Public/Everyone; публикация отменена")

            button = None
            for _ in range(240):
                _dismiss_onboarding(page)
                button = _find_post_button(page)
                if button and button.is_enabled():
                    break
                page.wait_for_timeout(1000)
            if button is None or not button.is_enabled():
                raise RuntimeError("TikTok Studio не разрешил публикацию (обработка или ограничение)")
            button.click(timeout=15000)
            posted = True

            # Do not retry a submitted Post even if confirmation times out.
            for _ in range(80):
                if "/tiktokstudio/content" in page.url:
                    return {
                        "id": None, "platform": "tiktok", "provider": "browser",
                        "slot": 2, "confirmation": "TikTok Studio content page",
                        "visibility": "public",
                    }
                page.wait_for_timeout(1000)
            raise RuntimeError(
                "Кнопка Post нажата, но подтверждения от TikTok Studio нет. "
                "Возможно, публикация прошла — проверь account2 перед повтором."
            )
        except Exception as exc:
            screenshot = _diagnostic_screenshot(page)
            phase = "после нажатия Post" if posted else "до нажатия Post"
            raise RuntimeError(
                f"Браузер TikTok account2 ({phase}): {exc}. "
                f"Скриншот ошибки: {screenshot}"
            ) from exc
        finally:
            context.close()
            browser.close()



def probe_account2_browser() -> dict:
    """Read-only check: is account2's TikTok Studio upload form accessible?

    Does not attach a video, type a caption, click Post, or modify sessions.
    """
    cookies, user_agent = _session("account2")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        options = {"locale": "en-US", "viewport": {"width": 1440, "height": 900}}
        if user_agent:
            options["user_agent"] = user_agent
        context = browser.new_context(**options)
        context.add_cookies(cookies)
        try:
            page = context.new_page()
            page.goto(STUDIO_UPLOAD_URL, wait_until="domcontentloaded", timeout=90000)
            for _ in range(45):
                if "login" in page.url.lower():
                    return {"ready": False, "reason": "login_required"}
                if _find_file_input(page) is not None:
                    return {"ready": True, "url": page.url}
                page.wait_for_timeout(1000)
            return {"ready": False, "reason": "upload_form_not_found", "url": page.url}
        finally:
            context.close()
            browser.close()
