from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


INSTAGRAM_SIGNUP_URL = "https://www.instagram.com/accounts/emailsignup/"


def _fill_email(page, email: str) -> bool:
    selectors = [
        'input[name="emailOrPhone"]',
        'input[type="email"]',
        'input[autocomplete="email"]',
    ]

    for selector in selectors:
        try:
            field = page.locator(selector).first
            if field.count() and field.is_visible(timeout=1500):
                field.fill(email)
                return True
        except (PlaywrightError, PlaywrightTimeoutError):
            continue

    keywords = (
        "email",
        "e-mail",
        "mail",
        "эл. адрес",
        "электрон",
        "почт",
        "adres e-mail",
        "telefon",
    )

    try:
        inputs = page.locator("input")
        count = min(inputs.count(), 30)
        for index in range(count):
            field = inputs.nth(index)
            try:
                if not field.is_visible(timeout=500):
                    continue
                haystack = " ".join(
                    str(field.get_attribute(name) or "")
                    for name in ("name", "aria-label", "placeholder", "autocomplete", "type")
                ).lower()
                if any(keyword in haystack for keyword in keywords):
                    field.fill(email)
                    return True
            except (PlaywrightError, PlaywrightTimeoutError):
                continue
    except PlaywrightError:
        pass

    return False


def run(edge_path: str, profile_path: str, email: str) -> None:
    profile = Path(profile_path).resolve()
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            executable_path=edge_path,
            headless=False,
            no_viewport=True,
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
            ],
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                INSTAGRAM_SIGNUP_URL,
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            page.wait_for_timeout(1500)
            _fill_email(page, email)
            page.bring_to_front()

            while True:
                try:
                    if not context.pages:
                        break
                except PlaywrightError:
                    break
                time.sleep(1)
        finally:
            try:
                context.close()
            except PlaywrightError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edge", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    run(args.edge, args.profile, args.email)


if __name__ == "__main__":
    main()
