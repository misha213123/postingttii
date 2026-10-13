"""Restricted public relay for Instagram Reels media and OAuth callback.

Start the main PostingTTII app on localhost:8765, then run:
    python -m uvicorn media_gateway:app --host 127.0.0.1 --port 8766

Point the HTTPS tunnel at 127.0.0.1:8766, NOT at the main dashboard.
Only GET/HEAD /media/<random-token> is publicly available. The existing
/media route and the main app's publishing UI/API are unchanged.
"""
from __future__ import annotations

import os
import re

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

app = FastAPI(
    title="PostingTTII media gateway",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# The main app generates 24 random bytes using secrets.token_urlsafe(24);
# this produces an unpredictable URL-safe token. Never proxy an arbitrary path.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32}")
_ALLOWED_RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "etag",
    "last-modified",
)



def _local_upstream() -> str:
    """Return the strictly-local upstream; never proxy arbitrary destinations."""
    main_host = os.getenv("HOST", "127.0.0.1").strip()
    main_port = os.getenv("PORT", "8765").strip()
    if main_host not in {"127.0.0.1", "localhost"} or not main_port.isdigit():
        raise HTTPException(status_code=503, detail="Local app configuration error")
    port = int(main_port)
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=503, detail="Invalid local port")
    return f"http://127.0.0.1:{port}"


@app.get("/auth/instagram/callback")
async def instagram_oauth_callback(code: str, state: str):
    """Pass Meta's one-time authorization code to the local application's callback.

    The public endpoint has no ability to manage accounts, fetch OAuth tokens,
    upload content, or browse the dashboard. The local app checks OAuth state.
    """
    if not code or not state or len(code) > 4096 or len(state) > 1024:
        raise HTTPException(status_code=400, detail="Invalid Instagram OAuth callback")

    base = _local_upstream()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            result = await client.get(
                f"{base}/auth/instagram/callback",
                params={"code": code, "state": state},
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Local OAuth callback unavailable")

    # The main app returns /?connected=instagram after saving the OAuth token.
    # Navigate back to the local dashboard, never to a publicly exposed panel.
    if 300 <= result.status_code < 400 and result.headers.get("location") == "/?connected=instagram":
        return RedirectResponse(f"{base}/?connected=instagram", status_code=303)

    if result.status_code in (400, 422):
        raise HTTPException(status_code=400, detail="Instagram OAuth state invalid or expired")

    raise HTTPException(
        status_code=502,
        detail="Instagram OAuth callback failed; check local PostingTTII logs",
    )


@app.api_route("/media/{token}", methods=["GET", "HEAD"])
async def media(token: str, request: Request):
    if not _TOKEN.fullmatch(token):
        raise HTTPException(status_code=404, detail="Not found")

    upstream_url = f"{_local_upstream()}/media/{token}"
    headers = {}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=10.0),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        upstream = await client.send(
            client.build_request(request.method, upstream_url, headers=headers),
            stream=True,
        )
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Local video server unavailable")

    if upstream.status_code not in (200, 206):
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=404, detail="Media not found")

    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() in _ALLOWED_RESPONSE_HEADERS
    }

    async def close_upstream():
        await upstream.aclose()
        await client.aclose()

    if request.method == "HEAD":
        await close_upstream()
        return Response(status_code=upstream.status_code, headers=response_headers)

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(close_upstream),
    )
