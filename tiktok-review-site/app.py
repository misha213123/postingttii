from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "review.db"

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI", "").strip()
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")
APP_SECRET = os.getenv("APP_SECRET", "").strip()
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "").strip()
OPERATOR_NAME = os.getenv("OPERATOR_NAME", "PostingTTII operator").strip()
VERIFY_FILENAME = os.getenv("TIKTOK_VERIFICATION_FILENAME", "").strip().lstrip("/")
VERIFY_CONTENT = os.getenv("TIKTOK_VERIFICATION_CONTENT", "")

app = FastAPI(title="PostingTTII TikTok Review", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _init_db() -> None:
    with _db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                slot INTEGER NOT NULL,
                verifier TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tiktok_accounts (
                slot INTEGER PRIMARY KEY,
                open_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                avatar_url TEXT NOT NULL DEFAULT '',
                access_token_enc TEXT NOT NULL,
                refresh_token_enc TEXT NOT NULL DEFAULT '',
                expires_at INTEGER NOT NULL DEFAULT 0,
                scope TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL
            );
            """
        )


_init_db()


def _fernet() -> Fernet:
    if not APP_SECRET:
        raise HTTPException(503, "APP_SECRET is not configured")
    digest = hashlib.sha256(APP_SECRET.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


def _require_config() -> None:
    missing = []
    if not CLIENT_KEY:
        missing.append("TIKTOK_CLIENT_KEY")
    if not CLIENT_SECRET:
        missing.append("TIKTOK_CLIENT_SECRET")
    if not REDIRECT_URI:
        missing.append("TIKTOK_REDIRECT_URI")
    if not APP_SECRET:
        missing.append("APP_SECRET")
    if missing:
        raise HTTPException(503, "Missing configuration: " + ", ".join(missing))


def _slot(slot: int) -> int:
    if slot not in (1, 2, 3):
        raise HTTPException(400, "TikTok account slot must be 1, 2 or 3")
    return slot


def _api_error(response: httpx.Response, action: str) -> HTTPException:
    try:
        body = response.json()
    except Exception:
        body = {}
    message = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = " | ".join(
                str(x)
                for x in (err.get("code"), err.get("message"), err.get("log_id") or err.get("logid"))
                if x
            )
        message = message or str(body.get("error_description") or body.get("message") or "")
    message = message or response.text[:1000]
    return HTTPException(response.status_code, f"TikTok {action}: {message}")


def _account_row(slot: int):
    with _db() as con:
        return con.execute(
            "SELECT * FROM tiktok_accounts WHERE slot = ?", (slot,)
        ).fetchone()


async def _access_token(slot: int) -> tuple[str, sqlite3.Row]:
    row = _account_row(_slot(slot))
    if not row:
        raise HTTPException(404, "TikTok account is not connected")

    token = _decrypt(row["access_token_enc"])
    if int(row["expires_at"] or 0) > int(time.time()) + 120:
        return token, row

    refresh = _decrypt(row["refresh_token_enc"])
    if not refresh:
        raise HTTPException(401, "TikTok session expired. Reconnect the account.")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": CLIENT_KEY,
                "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.is_error:
        raise _api_error(response, "token refresh")

    data = response.json()
    access = data["access_token"]
    new_refresh = data.get("refresh_token", refresh)
    expires_at = int(time.time()) + int(data.get("expires_in", 86400))

    with _db() as con:
        con.execute(
            """
            UPDATE tiktok_accounts
            SET access_token_enc = ?, refresh_token_enc = ?, expires_at = ?, scope = ?, updated_at = ?
            WHERE slot = ?
            """,
            (
                _encrypt(access),
                _encrypt(new_refresh),
                expires_at,
                data.get("scope", row["scope"]),
                int(time.time()),
                slot,
            ),
        )
    return access, _account_row(slot)


async def _creator_info(slot: int) -> dict:
    token, _ = await _access_token(slot)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
    if response.is_error:
        raise _api_error(response, "creator info")

    payload = response.json()
    error = payload.get("error", {})
    if error.get("code") not in (None, "", "ok"):
        raise HTTPException(
            400,
            f"TikTok creator info: {error.get('code')}: {error.get('message', '')}",
        )
    return payload.get("data", {})


def _legal_page(title: str, body: str) -> HTMLResponse:
    email = SUPPORT_EMAIL or "Support email configured in the TikTok Developer Portal"
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · PostingTTII</title>
<link rel="stylesheet" href="/static/styles.css">
</head>
<body class="legal-body">
<main class="legal">
<a class="back-link" href="/">← Back to PostingTTII</a>
<h1>{title}</h1>
<p class="legal-updated">Last updated: September 26, 2026</p>
{body}
<hr>
<p><strong>Operator:</strong> {OPERATOR_NAME}</p>
<p><strong>Contact:</strong> {email}</p>
</main>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def public_config():
    return {
        "app_name": "PostingTTII",
        "support_email": SUPPORT_EMAIL,
        "app_base_url": APP_BASE_URL,
        "tiktok_configured": bool(CLIENT_KEY and CLIENT_SECRET and REDIRECT_URI and APP_SECRET),
    }


@app.get("/api/accounts")
def accounts():
    with _db() as con:
        rows = con.execute(
            "SELECT slot, open_id, display_name, avatar_url, scope, updated_at FROM tiktok_accounts ORDER BY slot"
        ).fetchall()
    return {
        "accounts": [
            {
                "slot": row["slot"],
                "open_id": row["open_id"],
                "display_name": row["display_name"],
                "avatar_url": row["avatar_url"],
                "scope": row["scope"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    }


@app.get("/connect/tiktok/{slot}")
def connect_tiktok(slot: int):
    _require_config()
    slot = _slot(slot)

    state = secrets.token_urlsafe(32)

    with _db() as con:
        con.execute(
            "DELETE FROM oauth_states WHERE created_at < ?",
            (int(time.time()) - 1800,),
        )
        con.execute(
            "INSERT INTO oauth_states(state, slot, verifier, created_at) VALUES (?, ?, ?, ?)",
            (state, slot, "", int(time.time())),
        )

    params = {
        "client_key": CLIENT_KEY,
        "scope": "user.info.basic,video.publish",
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return RedirectResponse(
        "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)
    )


@app.get("/auth/tiktok/callback")
async def tiktok_callback(code: str, state: str):
    _require_config()

    with _db() as con:
        row = con.execute(
            "SELECT slot, verifier FROM oauth_states WHERE state = ?",
            (state,),
        ).fetchone()
        con.execute("DELETE FROM oauth_states WHERE state = ?", (state,))

    if not row:
        raise HTTPException(400, "OAuth state is invalid or expired")

    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": CLIENT_KEY,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_response.is_error:
            raise _api_error(token_response, "OAuth token exchange")
        token = token_response.json()

        user_response = await client.get(
            "https://open.tiktokapis.com/v2/user/info/",
            params={"fields": "open_id,union_id,avatar_url,display_name"},
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        if user_response.is_error:
            raise _api_error(user_response, "user info")
        user = user_response.json().get("data", {}).get("user", {})

    slot = int(row["slot"])
    with _db() as con:
        con.execute(
            """
            INSERT INTO tiktok_accounts(
                slot, open_id, display_name, avatar_url,
                access_token_enc, refresh_token_enc, expires_at, scope, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slot) DO UPDATE SET
                open_id = excluded.open_id,
                display_name = excluded.display_name,
                avatar_url = excluded.avatar_url,
                access_token_enc = excluded.access_token_enc,
                refresh_token_enc = excluded.refresh_token_enc,
                expires_at = excluded.expires_at,
                scope = excluded.scope,
                updated_at = excluded.updated_at
            """,
            (
                slot,
                token.get("open_id") or user.get("open_id") or "",
                user.get("display_name") or f"TikTok account {slot}",
                user.get("avatar_url") or "",
                _encrypt(token["access_token"]),
                _encrypt(token.get("refresh_token", "")),
                int(time.time()) + int(token.get("expires_in", 86400)),
                token.get("scope", ""),
                int(time.time()),
            ),
        )

    return RedirectResponse(f"/?connected={slot}#demo")


@app.delete("/api/accounts/{slot}")
def disconnect(slot: int):
    slot = _slot(slot)
    with _db() as con:
        con.execute("DELETE FROM tiktok_accounts WHERE slot = ?", (slot,))
    return {"ok": True}


@app.get("/api/tiktok/creator-info/{slot}")
async def creator_info(slot: int):
    slot = _slot(slot)
    data = await _creator_info(slot)
    return {"creator": data}


@app.post("/api/tiktok/publish/{slot}")
async def publish(
    slot: int,
    video: UploadFile = File(...),
    title: str = Form(""),
    privacy_level: str = Form(...),
    allow_comment: bool = Form(False),
    allow_duet: bool = Form(False),
    allow_stitch: bool = Form(False),
    paid_partnership: bool = Form(False),
    own_business: bool = Form(False),
    is_aigc: bool = Form(False),
    consent: bool = Form(False),
):
    slot = _slot(slot)
    if not consent:
        raise HTTPException(400, "Explicit user consent is required before sending content to TikTok")

    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm"}:
        raise HTTPException(400, "Use MP4, MOV or WebM video for this review demo")

    title = title.strip()
    if len(title) > 2200:
        raise HTTPException(400, "Caption must be 2200 characters or fewer")

    creator = await _creator_info(slot)
    allowed_privacy = creator.get("privacy_level_options") or []
    if privacy_level not in allowed_privacy:
        raise HTTPException(400, "Selected privacy option is not available for this TikTok creator")

    video.file.seek(0, 2)
    size = video.file.tell()
    video.file.seek(0)
    if size <= 0:
        raise HTTPException(400, "Video file is empty")
    if size > 64 * 1024 * 1024:
        raise HTTPException(
            400,
            "For the review demo, use a video up to 64 MB. The production uploader can use chunked upload.",
        )

    token, _ = await _access_token(slot)
    mime = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
    }[suffix]

    post_info = {
        "title": title,
        "privacy_level": privacy_level,
        "disable_comment": bool(creator.get("comment_disabled")) or not allow_comment,
        "disable_duet": bool(creator.get("duet_disabled")) or not allow_duet,
        "disable_stitch": bool(creator.get("stitch_disabled")) or not allow_stitch,
        "brand_content_toggle": bool(paid_partnership),
        "brand_organic_toggle": bool(own_business),
        "is_aigc": bool(is_aigc),
    }

    async with httpx.AsyncClient(timeout=120) as client:
        init = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": post_info,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                },
            },
        )
        if init.is_error:
            raise _api_error(init, "Direct Post initialization")

        init_json = init.json()
        error = init_json.get("error", {})
        if error.get("code") not in (None, "", "ok"):
            raise HTTPException(
                400,
                f"TikTok Direct Post: {error.get('code')}: {error.get('message', '')}",
            )

        data = init_json.get("data", {})
        upload_url = data.get("upload_url")
        publish_id = data.get("publish_id")
        if not upload_url or not publish_id:
            raise HTTPException(502, "TikTok did not return an upload URL and publish ID")

        payload = await video.read()
        uploaded = await client.put(
            upload_url,
            content=payload,
            headers={
                "Content-Type": mime,
                "Content-Length": str(size),
                "Content-Range": f"bytes 0-{size - 1}/{size}",
            },
        )
        if uploaded.is_error:
            raise _api_error(uploaded, "video upload")

    return {
        "ok": True,
        "publish_id": publish_id,
        "message": "Video sent to TikTok. Processing can take a few minutes.",
    }


@app.get("/api/tiktok/status/{slot}/{publish_id}")
async def publish_status(slot: int, publish_id: str):
    slot = _slot(slot)
    token, _ = await _access_token(slot)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )
    if response.is_error:
        raise _api_error(response, "post status")

    payload = response.json()
    error = payload.get("error", {})
    if error.get("code") not in (None, "", "ok"):
        raise HTTPException(
            400,
            f"TikTok post status: {error.get('code')}: {error.get('message', '')}",
        )
    return {"status": payload.get("data", {})}


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return _legal_page(
        "Privacy Policy",
        """
<h2>What we collect</h2>
<p>PostingTTII receives the TikTok profile information and authorization tokens that a user explicitly grants through TikTok Login. We also process video files, captions, privacy choices and interaction settings that the user chooses to send to TikTok.</p>

<h2>How we use TikTok data</h2>
<p>TikTok data is used only to identify the authorized account, display the current publishing options for that account, send user-selected content to TikTok, and show the publishing status.</p>

<h2>What we do not do</h2>
<p>PostingTTII does not sell TikTok user data, does not share TikTok authorization tokens with advertisers, and does not publish content without a user-initiated action and explicit consent.</p>

<h2>Storage and security</h2>
<p>Access and refresh tokens are encrypted at rest. API credentials are stored only on the server and are never embedded in the public website.</p>

<h2>Retention and deletion</h2>
<p>Users can disconnect a TikTok account from the product. Account authorization data can also be deleted by following the Data Deletion instructions.</p>

<h2>Third-party services</h2>
<p>When a user connects or publishes to TikTok, data is processed according to TikTok's own terms and privacy practices.</p>
""",
    )


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return _legal_page(
        "Terms of Service",
        """
<h2>Service</h2>
<p>PostingTTII is a creator tool that lets users connect their own TikTok accounts and send their own or properly licensed video content to those accounts using TikTok's official developer interfaces.</p>

<h2>User responsibility</h2>
<p>Users are responsible for the content they choose to publish and must have the rights and permissions required to use that content. Users must follow TikTok's Terms of Service and Community Guidelines.</p>

<h2>User control</h2>
<p>Posting is initiated by the user. Before sending content, the user selects the destination TikTok account, reviews the caption and publishing settings, and gives explicit consent.</p>

<h2>Availability</h2>
<p>The service may be unavailable during maintenance, third-party outages or API changes. TikTok may apply its own limits, moderation, privacy rules and posting restrictions.</p>

<h2>Independent service</h2>
<p>PostingTTII is an independent third-party product and is not TikTok or an affiliate of TikTok.</p>
""",
    )


@app.get("/data-deletion", response_class=HTMLResponse)
def data_deletion():
    return _legal_page(
        "Data Deletion",
        """
<h2>Disconnect in the product</h2>
<p>Open the PostingTTII demo, find the connected TikTok account and choose <strong>Disconnect</strong>. This removes the stored authorization data for that account from PostingTTII.</p>

<h2>Request deletion by email</h2>
<p>You may also request deletion of TikTok-related account data by contacting the support email listed below. Include the display name of the connected account so the request can be located.</p>

<h2>Revoke access in TikTok</h2>
<p>You can separately revoke an application's access from your TikTok account settings. Revoking access prevents future API requests using that authorization.</p>

<h2>Deletion timing</h2>
<p>Deletion requests are processed as soon as reasonably possible. Data that must be retained for legal or security reasons may be kept only for the required period.</p>
""",
    )


@app.get("/{filename}.txt", response_class=PlainTextResponse)
def tiktok_url_verification(filename: str):
    requested = f"{filename}.txt"
    if not VERIFY_FILENAME or requested != VERIFY_FILENAME or not VERIFY_CONTENT:
        raise HTTPException(404, "Not found")
    return PlainTextResponse(VERIFY_CONTENT)
