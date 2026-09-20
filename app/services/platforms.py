from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.store import store


def _now() -> int:
    return int(time.time())


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
        token_response.raise_for_status()
        token = token_response.json()

        profile_response = await client.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        profile_response.raise_for_status()
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
        response.raise_for_status()
        refreshed = response.json()

    account["access_token"] = refreshed["access_token"]
    account["expires_at"] = _now() + int(refreshed.get("expires_in", 3600))
    store.save("youtube", slot, {k: v for k, v in account.items() if k != "slot"})
    return account["access_token"], account


async def youtube_upload(slot: int, video_path: Path, caption: str) -> dict:
    token, _ = await _youtube_access_token(slot)
    title = video_path.stem.replace("_", " ").strip()[:95] or "Short"
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
        init.raise_for_status()
        upload_url = init.headers.get("Location")
        if not upload_url:
            raise RuntimeError("YouTube не вернул URL загрузки")

        with video_path.open("rb") as f:
            upload = await client.put(
                upload_url,
                headers={"Content-Type": "video/mp4"},
                content=f.read(),
            )
        upload.raise_for_status()
        data = upload.json()

    return {"id": data.get("id"), "platform": "youtube", "slot": slot}


def tiktok_auth_url(state: str) -> str:
    params = {
        "client_key": settings.tiktok_client_key,
        "scope": "user.info.basic,video.publish",
        "response_type": "code",
        "redirect_uri": settings.tiktok_redirect_uri,
        "state": state,
    }
    return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)


async def tiktok_exchange(code: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.tiktok_redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        token = response.json()

        info = await client.get(
            "https://open.tiktokapis.com/v2/user/info/",
            params={"fields": "open_id,union_id,avatar_url,display_name"},
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        info.raise_for_status()
        user = info.json().get("data", {}).get("user", {})

    return {
        "id": token.get("open_id", user.get("open_id", "")),
        "label": user.get("display_name") or user.get("username") or "TikTok",
        "username": user.get("username", ""),
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
        response.raise_for_status()
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
        creator.raise_for_status()
        creator_data = creator.json().get("data", {})
        allowed_privacy = creator_data.get("privacy_level_options", [])
        privacy = settings.tiktok_privacy_level
        if allowed_privacy and privacy not in allowed_privacy:
            privacy = allowed_privacy[0]

        if size <= 64 * 1024 * 1024:
            chunk_size = size
            count = 1
        else:
            chunk_size = 32 * 1024 * 1024
            count = math.ceil(size / chunk_size)

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
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": count,
                },
            },
        )
        init.raise_for_status()
        init_json = init.json()
        error = init_json.get("error", {})
        if error.get("code") not in (None, "", "ok"):
            raise RuntimeError(f"TikTok: {error.get('code')}: {error.get('message', '')}")

        data = init_json.get("data", {})
        upload_url = data.get("upload_url")
        publish_id = data.get("publish_id")
        if not upload_url:
            raise RuntimeError("TikTok не вернул upload_url")

        with video_path.open("rb") as f:
            start = 0
            while start < size:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                end = start + len(chunk) - 1
                uploaded = await client.put(
                    upload_url,
                    content=chunk,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                    },
                )
                uploaded.raise_for_status()
                start = end + 1

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
        short.raise_for_status()
        short_token = short.json()

        long_response = await client.get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.instagram_client_secret,
                "access_token": short_token["access_token"],
            },
        )
        long_response.raise_for_status()
        long_token = long_response.json()

        profile = await client.get(
            "https://graph.instagram.com/me",
            params={
                "fields": "id,username,account_type",
                "access_token": long_token["access_token"],
            },
        )
        profile.raise_for_status()
        user = profile.json()

    return {
        "id": user.get("id") or str(short_token.get("user_id", "")),
        "label": user.get("username") or "Instagram",
        "username": user.get("username", ""),
        "access_token": long_token["access_token"],
        "expires_at": _now() + int(long_token.get("expires_in", 5184000)),
    }


async def instagram_upload(slot: int, video_url: str, caption: str) -> dict:
    account = store.get("instagram", slot)
    if not account:
        raise RuntimeError(f"Instagram #{slot} не подключен")
    if not video_url:
        raise RuntimeError(
            "Для Instagram нужен PUBLIC_BASE_URL: локальный файл должен быть доступен Meta по HTTPS."
        )

    token = account["access_token"]
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
        create.raise_for_status()
        container_id = create.json().get("id")
        if not container_id:
            raise RuntimeError(f"Instagram: не создан контейнер: {create.text}")

        status = "IN_PROGRESS"
        for _ in range(30):
            check = await client.get(
                f"{base}/{container_id}",
                params={"fields": "status_code", "access_token": token},
            )
            check.raise_for_status()
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
        publish.raise_for_status()
        media_id = publish.json().get("id")

    return {"id": media_id, "platform": "instagram", "slot": slot}
