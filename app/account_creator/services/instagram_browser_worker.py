"""Manual Instagram window using Camoufox with a persistent local Firefox profile."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from camoufox.sync_api import Camoufox
from camoufox.fingerprints import generate_fingerprint

INSTAGRAM_SIGNUP_URL = "https://www.instagram.com/accounts/emailsignup/"
INSTAGRAM_HOME_URL = "https://www.instagram.com/"


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



def _identity(profile: Path) -> dict:
    """Keep the same generated fingerprint for a single long-lived profile."""
    identity_path = profile.parent / (profile.name + ".identity.json")
    if identity_path.exists():
        data = json.loads(identity_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("Некорректный файл отпечатка браузера")
        return data
    host_os = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(
        platform.system(), "windows"
    )
    generated = generate_fingerprint(os=host_os)
    temp = identity_path.with_suffix(".tmp")
    temp.write_text(json.dumps(generated, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, identity_path)
    return generated


def run(profile_path: str, mode: str, email: str = "", stop_path: str = "") -> None:
    profile = Path(profile_path).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    stop_file = Path(stop_path).resolve() if stop_path else None
    if stop_file:
        stop_file.unlink(missing_ok=True)
    # Proxy config is sent over stdin, never as a process command-line argument.
    proxy = json.loads(sys.stdin.read() or "{}")
    if not isinstance(proxy, dict):
        raise ValueError("Некорректные параметры прокси")
    identity = _identity(profile)
    launch_options = dict(
        persistent_context=True,
        user_data_dir=str(profile),
        headless=False,
        fingerprint=identity,
    )
    if proxy:
        launch_options["proxy"] = proxy
        launch_options["geoip"] = True

    with Camoufox(**launch_options) as context:
        page = context.pages[0] if context.pages else context.new_page()
        url = INSTAGRAM_SIGNUP_URL if mode == "signup" else INSTAGRAM_HOME_URL
        navigated = False
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            navigated = True
        except (PlaywrightError, PlaywrightTimeoutError):
            # Keep window open for manual login even if Instagram doesn't load.
            pass
        if navigated and mode == "signup" and email:
            try:
                _fill_email(page, email)
            except (PlaywrightError, PlaywrightTimeoutError):
                pass
        try:
            page.bring_to_front()
        except PlaywrightError:
            pass
        while True:
            if stop_file and stop_file.exists():
                break
            try:
                if not context.pages:
                    break
            except PlaywrightError:
                break
            time.sleep(0.5)
    if stop_file:
        stop_file.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--stop-file", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--mode", choices=["signup", "home"], default="home")
    args = parser.parse_args()
    run(args.profile, args.mode, args.email, args.stop_file)


if __name__ == "__main__":
    main()
