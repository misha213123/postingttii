from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.account_creator.services.browser_service import close_profile, find_browser


SIGNUP_URL = "https://www.tiktok.com/signup/phone-or-email/email"


@dataclass(slots=True)
class TikTokSession:
    playwright: Playwright
    context: BrowserContext
    page: Page


_SESSIONS: dict[int, TikTokSession] = {}


class TikTokSetupError(RuntimeError):
    pass


def _profile_is_locked(profile: Path) -> bool:
    lock_names = ("SingletonLock", "SingletonCookie", "SingletonSocket")
    return any((profile / name).exists() for name in lock_names)


async def _first_visible(page: Page, selectors: list[str]):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def _text_visible(page: Page, pattern: str) -> bool:
    try:
        locator = page.get_by_text(re.compile(pattern, re.I)).first
        return bool(await locator.count()) and await locator.is_visible()
    except Exception:
        return False


async def _looks_like_birthday_step(page: Page) -> bool:
    if await _text_visible(page, r"birthday|date of birth|день рождения"):
        return True
    try:
        combos = page.locator('select, [role="combobox"]')
        return await combos.count() >= 3
    except Exception:
        return False


async def _looks_like_verification_step(page: Page) -> bool:
    locator = await _first_visible(
        page,
        [
            'input[placeholder*="code" i]',
            'input[aria-label*="code" i]',
            'input[maxlength="6"]',
            'input[inputmode="numeric"]',
        ],
    )
    if locator:
        return True
    return await _text_visible(
        page,
        r"verification code|enter.*code|код подтверждения|проверочный код",
    )


async def _looks_like_captcha(page: Page) -> bool:
    try:
        if await page.locator('iframe[src*="captcha" i], iframe[title*="captcha" i]').count():
            return True
    except Exception:
        pass
    return await _text_visible(page, r"captcha|verify to continue|подтвердите.*человек")


async def _looks_logged_in(page: Page) -> bool:
    if "login" in page.url.lower() or "signup" in page.url.lower():
        return False
    locator = await _first_visible(
        page,
        [
            '[data-e2e="profile-icon"]',
            '[data-e2e="nav-profile"]',
            'a[href*="/@"]',
        ],
    )
    return locator is not None


async def _fill_if_visible(page: Page, selectors: list[str], value: str) -> bool:
    locator = await _first_visible(page, selectors)
    if not locator:
        return False
    try:
        await locator.fill(value)
        return True
    except Exception:
        return False


async def _click_by_text(page: Page, pattern: str) -> bool:
    try:
        button = page.get_by_role("button", name=re.compile(pattern, re.I)).first
        if await button.count() and await button.is_visible() and await button.is_enabled():
            await button.click()
            return True
    except Exception:
        pass
    return False


async def _fill_profile_prompts(page: Page, account: dict[str, Any]) -> bool:
    changed = False

    display_name = str(account.get("display_name") or "").strip()
    username = str(account.get("username") or "").strip()
    bio = str(account.get("bio") or "").strip()

    if display_name:
        changed |= await _fill_if_visible(
            page,
            [
                'input[name="nickname"]',
                'input[placeholder*="nickname" i]',
                'input[placeholder*="name" i]',
            ],
            display_name,
        )

    if username:
        changed |= await _fill_if_visible(
            page,
            [
                'input[name="username"]',
                'input[placeholder*="username" i]',
            ],
            username,
        )

    if bio:
        changed |= await _fill_if_visible(
            page,
            [
                'textarea[name="bio"]',
                'textarea[placeholder*="bio" i]',
            ],
            bio,
        )

    if changed:
        await _click_by_text(page, r"save|next|continue|submit|далее|сохранить")
    return changed


async def _ensure_session(account_id: int, profile_path: str) -> TikTokSession:
    existing = _SESSIONS.get(account_id)
    if existing:
        try:
            if not existing.page.is_closed():
                return existing
        except Exception:
            pass

    close_profile(account_id)

    browser = find_browser(os.getenv("TIKTOK_BROWSER", "edge"))
    profile = Path(profile_path).resolve()
    profile.mkdir(parents=True, exist_ok=True)

    # Chrome may need a moment to release Singleton* locks after a normal
    # browser window is closed. Do not delete those locks: if they remain,
    # another Chrome process may still own this profile.
    for _ in range(8):
        if not _profile_is_locked(profile):
            break
        await asyncio.sleep(0.4)

    if _profile_is_locked(profile):
        raise TikTokSetupError(
            "Browser Profile этого Account всё ещё открыт в Chrome/Edge. "
            "Закрой окно этого Account полностью и снова нажми Start TikTok registration."
        )

    playwright = await async_playwright().start()
    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            executable_path=str(browser),
            headless=False,
            no_viewport=True,
            chromium_sandbox=True,
            color_scheme="light",
            locale="ru-RU",
            args=[
                "--start-maximized",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
    except Exception as exc:
        await playwright.stop()
        detail = str(exc).strip() or exc.__class__.__name__
        raise TikTokSetupError(
            "Не удалось открыть TikTok registration browser. "
            "Закрой другие окна этого Account и попробуй снова. "
            f"Детали: {detail}"
        ) from exc

    page = context.pages[0] if context.pages else await context.new_page()
    try:
        await page.emulate_media(color_scheme="light")
    except Exception:
        pass
    session = TikTokSession(playwright=playwright, context=context, page=page)
    _SESSIONS[account_id] = session
    return session


async def close_session(account_id: int) -> None:
    session = _SESSIONS.pop(account_id, None)
    if not session:
        return
    try:
        await session.context.close()
    except Exception:
        pass
    try:
        await session.playwright.stop()
    except Exception:
        pass


async def start_signup(
    account: dict[str, Any],
    password: str,
) -> dict[str, Any]:
    account_id = int(account["id"])
    await close_session(account_id)

    session = await _ensure_session(
        account_id,
        str(account["browser_profile_path"]),
    )
    try:
        await session.page.goto(
            SIGNUP_URL,
            wait_until="domcontentloaded",
            timeout=30_000,
        )
    except PlaywrightTimeoutError:
        pass

    await session.page.wait_for_timeout(1500)
    return await advance_signup(account, password)


async def advance_signup(
    account: dict[str, Any],
    password: str,
) -> dict[str, Any]:
    account_id = int(account["id"])
    session = await _ensure_session(
        account_id,
        str(account["browser_profile_path"]),
    )
    page = session.page

    if page.url == "about:blank":
        try:
            await page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeoutError:
            pass
        await page.wait_for_timeout(1200)

    if await _looks_logged_in(page):
        await _fill_profile_prompts(page, account)
        return {
            "state": "DONE",
            "message": "TikTok session выглядит авторизованной. Проверь профиль в открытом окне.",
            "url": page.url,
        }

    if await _looks_like_captcha(page):
        return {
            "state": "MANUAL_CAPTCHA",
            "message": "TikTok просит ручную проверку. Пройди CAPTCHA в открытом окне, затем нажми Continue.",
            "url": page.url,
        }

    if await _looks_like_verification_step(page):
        return {
            "state": "MANUAL_VERIFICATION",
            "message": "Введи код подтверждения из почты вручную, закончи этот шаг в TikTok и затем нажми Continue.",
            "url": page.url,
        }

    email_input = await _first_visible(
        page,
        [
            'input[name="email"]',
            'input[type="email"]',
            'input[placeholder*="email" i]',
        ],
    )
    password_input = await _first_visible(
        page,
        [
            'input[name="password"]',
            'input[type="password"]',
            'input[placeholder*="password" i]',
        ],
    )

    birthday_step = await _looks_like_birthday_step(page)

    if email_input:
        await email_input.fill(str(account["email"]))

    if password_input:
        await password_input.fill(password)

    # TikTok requires the user to choose their own date of birth. Do not click
    # Send code / Next while the birthday selectors are still on screen:
    # TikTok can place the form into a blocked/dimmed validation state.
    if birthday_step:
        return {
            "state": "MANUAL_BIRTHDAY",
            "message": (
                "Email и пароль заполнены. Выбери свою дату рождения вручную "
                "и сам нажми «Отправить код» в TikTok. Затем вернись в PostingTTII "
                "и нажми Continue TikTok."
            ),
            "url": page.url,
        }

    if email_input or password_input:
        clicked = await _click_by_text(
            page,
            r"send code|next|continue|sign up|отправить код|далее|продолжить|зарегистрироваться",
        )
        if clicked:
            await page.wait_for_timeout(1200)

        if await _looks_like_captcha(page):
            return {
                "state": "MANUAL_CAPTCHA",
                "message": "Поля заполнены. Пройди CAPTCHA вручную и затем нажми Continue.",
                "url": page.url,
            }

        if await _looks_like_verification_step(page):
            return {
                "state": "MANUAL_VERIFICATION",
                "message": "Email и пароль заполнены. Введи код из письма вручную и затем нажми Continue.",
                "url": page.url,
            }

        return {
            "state": "FORM_FILLED",
            "message": "Email и пароль заполнены. Если TikTok просит действие в браузере — выполни его и нажми Continue.",
            "url": page.url,
        }

    changed = await _fill_profile_prompts(page, account)
    if changed:
        return {
            "state": "PROFILE_FILLED",
            "message": "Данные профиля заполнены там, где TikTok показал соответствующие поля. Проверь окно и нажми Continue.",
            "url": page.url,
        }

    return {
        "state": "MANUAL_STEP",
        "message": "TikTok показал шаг, который автоматизация не распознала. Выполни его вручную и нажми Continue.",
        "url": page.url,
    }
