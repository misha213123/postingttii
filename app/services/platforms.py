from __future__ import annotations

import asyncio
import hashlib
import math
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.store import store


_TIKTOK_CODE_VERIFIERS: dict[str, str] = {}


def _now() -> int:
    return int(time.time())


def clean_video_title(path: Path) -> str:
    title = path.stem

    # Remove technical suffixes added by the clipper, e.g.
    # _d7f872b4b53a or -d7f872b4b53a
    title = re.sub(r"[_-][0-9a-fA-F]{8,}$", "", title)

    # Turn filename separators into normal spaces.
    title = re.sub(r"[_-]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()

    return title[:95] or "Short"


def _api_error(response: httpx.Response, platform: str, action: str) -> RuntimeError:
    try:
        payload = response.json()
    except Exception:
        payload = None

    details = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code") or ""
            message = error.get("message") or ""
            log_id = error.get("log_id") or error.get("logid") or ""
            details = " | ".join(x for x in (str(code), str(message), f"log_id={log_id}" if log_id else "") if x)
        elif error:
            details = str(error)
        elif payload.get("error_description"):
            details = str(payload.get("error_description"))
        elif payload.get("message"):
            details = str(payload.get("message"))

    if not details:
        details = response.text[:1200].strip()

    suffix = f": {details}" if details else ""
    return RuntimeError(f"{platform} {action}: HTTP {response.status_code}{suffix}")


def youtube_auth_url(state: str) -> str:
    params = {
        "client_id": settings.youtube_client_id,
        "redirect_uri": settings.youtube_redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


async def youtube_exchange(code: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.youtube_redirect_uri,
            },
        )
        if token_response.is_error:
            raise _api_error(token_response, "YouTube", "OAuth token request")
        token = token_response.json()

        profile_response = await client.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        if profile_response.is_error:
            raise _api_error(profile_response, "YouTube", "channel profile")
        items = profile_response.json().get("items", [])
        channel = items[0] if items else {}

    return {
        "id": channel.get("id", ""),
        "label": channel.get("snippet", {}).get("title", "YouTube"),
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token", ""),
        "expires_at": _now() + int(token.get("expires_in", 3600)),
    }


async def _youtube_access_token(slot: int) -> tuple[str, dict]:
    account = store.get("youtube", slot)
    if not account:
        raise RuntimeError(f"YouTube #{slot} не подключен")

    if int(account.get("expires_at", 0)) > _now() + 120:
        return account["access_token"], account

    if not account.get("refresh_token"):
        raise RuntimeError(f"YouTube #{slot}: нет refresh_token, переподключи аккаунт")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "refresh_token": account["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        if response.is_error:
            raise _api_error(response, "OAuth", "token request")
        refreshed = response.json()

    account["access_token"] = refreshed["access_token"]
    account["expires_at"] = _now() + int(refreshed.get("expires_in", 3600))
    store.save("youtube", slot, {k: v for k, v in account.items() if k != "slot"})
    return account["access_token"], account


async def youtube_upload(slot: int, video_path: Path, caption: str) -> dict:
    token, _ = await _youtube_access_token(slot)
    title = clean_video_title(video_path)
    body = {
        "snippet": {
            "title": title,
            "description": caption[:5000],
            "categoryId": "20",
        },
        "status": {
            "privacyStatus": settings.youtube_privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(video_path.stat().st_size),
        "X-Upload-Content-Type": "video/mp4",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        init = await client.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers=headers,
            json=body,
        )
        if init.is_error:
            raise _api_error(init, "YouTube", "init upload")
        upload_url = init.headers.get("Location")
        if not upload_url:
            raise RuntimeError("YouTube не вернул URL загрузки")

        with video_path.open("rb") as f:
            upload = await client.put(
                upload_url,
                headers={"Content-Type": "video/mp4"},
                content=f.read(),
            )
        if upload.is_error:
            raise _api_error(upload, "YouTube", "upload video")
        data = upload.json()

    return {"id": data.get("id"), "platform": "youtube", "slot": slot}


def tiktok_auth_url(state: str) -> str:
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = hashlib.sha256(code_verifier.encode("utf-8")).hexdigest()
    _TIKTOK_CODE_VERIFIERS[state] = code_verifier

    params = {
        "client_key": settings.tiktok_client_key,
        "scope": "user.info.basic,video.publish",
        "response_type": "code",
        "redirect_uri": settings.tiktok_redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)


async def tiktok_exchange(code: str, state: str) -> dict:
    code_verifier = _TIKTOK_CODE_VERIFIERS.pop(state, None)
    if not code_verifier:
        raise RuntimeError("TikTok PKCE session expired. Подключи аккаунт еще раз.")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.tiktok_redirect_uri,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.is_error:
            raise _api_error(response, "OAuth", "token request")
        token = response.json()

        info = await client.get(
            "https://open.tiktokapis.com/v2/user/info/",
            params={"fields": "open_id,union_id,avatar_url,display_name"},
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        if info.is_error:
            raise _api_error(info, "TikTok", "user info")
        user = info.json().get("data", {}).get("user", {})

    return {
        "id": token.get("open_id", user.get("open_id", "")),
        "label": user.get("display_name") or "TikTok",
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token", ""),
        "expires_at": _now() + int(token.get("expires_in", 86400)),
        "scope": token.get("scope", ""),
    }


async def _tiktok_access_token(slot: int) -> tuple[str, dict]:
    account = store.get("tiktok", slot)
    if not account:
        raise RuntimeError(f"TikTok #{slot} не подключен")

    if int(account.get("expires_at", 0)) > _now() + 120:
        return account["access_token"], account

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": account.get("refresh_token", ""),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.is_error:
            raise _api_error(response, "OAuth", "token request")
        refreshed = response.json()

    account["access_token"] = refreshed["access_token"]
    account["refresh_token"] = refreshed.get("refresh_token", account.get("refresh_token", ""))
    account["expires_at"] = _now() + int(refreshed.get("expires_in", 86400))
    store.save("tiktok", slot, {k: v for k, v in account.items() if k != "slot"})
    return account["access_token"], account


async def tiktok_upload(slot: int, video_path: Path, caption: str) -> dict:
    token, _ = await _tiktok_access_token(slot)
    size = video_path.stat().st_size

    async with httpx.AsyncClient(timeout=120) as client:
        creator = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
        if creator.is_error:
            raise _api_error(creator, "TikTok", "creator info")
        creator_json = creator.json()
        creator_error = creator_json.get("error", {})
        if creator_error.get("code") not in (None, "", "ok"):
            raise RuntimeError(
                f"TikTok creator info: {creator_error.get('code')}: "
                f"{creator_error.get('message', '')}"
            )

        creator_data = creator_json.get("data", {})
        allowed_privacy = creator_data.get("privacy_level_options", [])
        privacy = settings.tiktok_privacy_level

        # Unaudited TikTok clients are limited to private posts.
        if "SELF_ONLY" in allowed_privacy:
            privacy = "SELF_ONLY"
        elif allowed_privacy and privacy not in allowed_privacy:
            privacy = allowed_privacy[0]

        if size <= 64 * 1024 * 1024:
            chunk_size = size
            count = 1
        else:
            # TikTok requires total_chunk_count = floor(video_size/chunk_size);
            # the final chunk may contain the remainder.
            chunk_size = 32 * 1024 * 1024
            count = max(1, size // chunk_size)

        init = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": {
                    "title": caption[:2200],
                    "privacy_level": privacy,
                    "disable_duet": bool(creator_data.get("duet_disabled", False)),
                    "disable_comment": bool(creator_data.get("comment_disabled", False)),
                    "disable_stitch": bool(creator_data.get("stitch_disabled", False)),
                    "brand_content_toggle": False,
                    "brand_organic_toggle": False,
                    "is_aigc": False,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": count,
                },
            },
        )
        if init.is_error:
            raise _api_error(init, "TikTok", "init Direct Post")
        init_json = init.json()
        error = init_json.get("error", {})
        if error.get("code") not in (None, "", "ok"):
            raise RuntimeError(f"TikTok: {error.get('code')}: {error.get('message', '')}")

        data = init_json.get("data", {})
        upload_url = data.get("upload_url")
        publish_id = data.get("publish_id")
        if not upload_url:
            raise RuntimeError("TikTok не вернул upload_url")

        mime = {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
        }.get(video_path.suffix.lower(), "video/mp4")

        with video_path.open("rb") as f:
            start = 0
            for index in range(count):
                if index == count - 1:
                    chunk = f.read()
                else:
                    chunk = f.read(chunk_size)

                if not chunk:
                    raise RuntimeError("TikTok upload: empty chunk")

                end = start + len(chunk) - 1
                uploaded = await client.put(
                    upload_url,
                    content=chunk,
                    headers={
                        "Content-Type": mime,
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                    },
                )
                if uploaded.is_error:
                    raise _api_error(uploaded, "TikTok", f"upload chunk {index + 1}/{count}")
                start = end + 1

        if start != size:
            raise RuntimeError(
                f"TikTok upload incomplete: sent {start} of {size} bytes"
            )

    return {"id": publish_id, "platform": "tiktok", "slot": slot}


def instagram_auth_url(state: str) -> str:
    params = {
        "client_id": settings.instagram_client_id,
        "redirect_uri": settings.instagram_redirect_uri,
        "response_type": "code",
        "scope": "instagram_business_basic,instagram_business_content_publish",
        "state": state,
        "enable_fb_login": "0",
        "force_authentication": "1",
    }
    return "https://www.instagram.com/oauth/authorize?" + urlencode(params)


async def instagram_exchange(code: str) -> dict:
    code = code.replace("#_", "")
    async with httpx.AsyncClient(timeout=30) as client:
        short = await client.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": settings.instagram_client_id,
                "client_secret": settings.instagram_client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": settings.instagram_redirect_uri,
                "code": code,
            },
        )
        if short.is_error:
            raise _api_error(short, "Instagram", "OAuth token request")
        short_token = short.json()

        long_response = await client.get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.instagram_client_secret,
                "access_token": short_token["access_token"],
            },
        )
        if long_response.is_error:
            raise _api_error(long_response, "Instagram", "long-lived token exchange")
        long_token = long_response.json()

        profile = await client.get(
            "https://graph.instagram.com/me",
            params={
                "fields": "user_id,username,account_type",
                "access_token": long_token["access_token"],
            },
        )
        if profile.is_error:
            raise _api_error(profile, "Instagram", "profile")
        user = profile.json()

    return {
        "id": str(user.get("user_id") or short_token.get("user_id", "")),
        "label": user.get("username") or "Instagram",
        "username": user.get("username", ""),
        "access_token": long_token["access_token"],
        "expires_at": _now() + int(long_token.get("expires_in", 5184000)),
    }


async def _instagram_access_token(slot: int) -> tuple[str, dict]:
    account = store.get("instagram", slot)
    if not account:
        raise RuntimeError(f"Instagram #{slot} не подключен")

    expires_at = int(account.get("expires_at", 0))
    # Long-lived Instagram tokens are refreshed only when they are close
    # to expiration. This keeps both connected accounts working without
    # asking the user to log in again every few weeks.
    if expires_at > _now() + 3 * 24 * 60 * 60:
        return account["access_token"], account

    if expires_at and expires_at <= _now():
        raise RuntimeError(
            f"Instagram #{slot}: токен истек. Переподключи аккаунт в панели."
        )

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            "https://graph.instagram.com/refresh_access_token",
            params={
                "grant_type": "ig_refresh_token",
                "access_token": account["access_token"],
            },
        )
        if response.is_error:
            raise _api_error(response, "Instagram", "refresh access token")
        refreshed = response.json()

    account["access_token"] = refreshed.get("access_token", account["access_token"])
    account["expires_at"] = _now() + int(refreshed.get("expires_in", 5184000))
    store.save("instagram", slot, {k: v for k, v in account.items() if k != "slot"})
    return account["access_token"], account


async def instagram_upload(slot: int, video_url: str, caption: str) -> dict:
    if not video_url:
        raise RuntimeError(
            "Для Instagram нужен PUBLIC_BASE_URL: локальный файл должен быть доступен Meta по HTTPS."
        )

    token, account = await _instagram_access_token(slot)
    user_id = account["id"]
    base = f"https://graph.instagram.com/{settings.instagram_graph_version}"

    async with httpx.AsyncClient(timeout=60) as client:
        create = await client.post(
            f"{base}/{user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption[:2200],
                "share_to_feed": "true",
                "access_token": token,
            },
        )
        if create.is_error:
            raise _api_error(create, "Instagram", "create Reel container")
        container_id = create.json().get("id")
        if not container_id:
            raise RuntimeError(f"Instagram: не создан контейнер: {create.text}")

        status = "IN_PROGRESS"
        for _ in range(30):
            check = await client.get(
                f"{base}/{container_id}",
                params={"fields": "status_code", "access_token": token},
            )
            if check.is_error:
                raise _api_error(check, "Instagram", "check Reel container")
            status = check.json().get("status_code", "")
            if status == "FINISHED":
                break
            if status in {"ERROR", "EXPIRED"}:
                raise RuntimeError(f"Instagram container status: {status}")
            await asyncio.sleep(5)

        if status != "FINISHED":
            raise RuntimeError("Instagram слишком долго обрабатывает Reel")

        publish = await client.post(
            f"{base}/{user_id}/media_publish",
            data={"creation_id": container_id, "access_token": token},
        )
        if publish.is_error:
            raise _api_error(publish, "Instagram", "publish Reel")
        media_id = publish.json().get("id")

    return {"id": media_id, "platform": "instagram", "slot": slot}
