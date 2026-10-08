"""Instagram owned-content importer inside Telegram Studio.

Public account access depends on Instagram and yt-dlp; failures are reported,
not silently bypassed. Does not automate logins or circumvent restrictions.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import random
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import settings
from app.telegram_studio import BACKGROUNDS, backgrounds, duration

router = APIRouter(prefix="/api/telegram-studio/instagram", tags=["studio-instagram"])
ROOT = settings.data_dir / "instagram_studio"
ROOT.mkdir(parents=True, exist_ok=True)
CONFIG = ROOT / "sources.json"
JOB = {"status": "idle", "done": 0, "total": 0, "errors": []}
LOCK = asyncio.Lock()
TASK = None

class Source(BaseModel):
    url: str

class Run(BaseModel):
    limit_per_account: int = 5

class LinkInput(BaseModel):
    url: str

def load():
    if not CONFIG.exists():
        return {"accounts": [], "disabled": [], "processed": [], "hashes": [], "links": []}
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    for key in ("accounts", "disabled", "processed", "hashes", "links"):
        data.setdefault(key, [])
    return data

def save(data):
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG)

def normalize(url):
    url = url.strip()
    if not url.startswith("http"):
        url = "https://www.instagram.com/" + url.lstrip("@/")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ("instagram.com", "www.instagram.com"):
        raise HTTPException(400, "Нужна ссылка на Instagram-профиль")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 1 or not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", parts[0]):
        raise HTTPException(400, "Укажи ссылку на профиль, а не отдельный пост")
    return "https://www.instagram.com/" + parts[0] + "/"

@router.get("/status")
def status():
    d = load()
    return {"accounts": d["accounts"], "disabled": d["disabled"], "links": d["links"], "job": JOB, "processed_count": len(d["processed"])}

def normalize_post(url):
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in ("www.instagram.com", "instagram.com"):
        raise HTTPException(400, "Нужна прямая HTTPS-ссылка на Instagram-публикацию")
    match = re.fullmatch(r"/(p|reel|tv)/([A-Za-z0-9_-]+)/?", parsed.path)
    if not match:
        raise HTTPException(400, "Поддерживаются ссылки /p/, /reel/ и /tv/")
    return f"https://www.instagram.com/{match.group(1)}/{match.group(2)}/"

@router.post("/links")
def add_link(body: LinkInput):
    url = normalize_post(body.url)
    d = load()
    if url not in d["links"] and url not in d["processed"]:
        d["links"].append(url)
        save(d)
    return {"links": d["links"]}

@router.delete("/links/{shortcode}")
def delete_link(shortcode: str):
    d = load()
    d["links"] = [url for url in d["links"] if urlparse(url).path.rstrip("/").split("/")[-1] != shortcode]
    save(d)
    return {"links": d["links"]}

@router.post("/accounts")
def add(body: Source):
    url = normalize(body.url)
    d = load()
    if url not in d["accounts"]:
        d["accounts"].append(url)
        save(d)
    return {"accounts": d["accounts"]}

class Toggle(BaseModel):
    enabled: bool

@router.post("/accounts/{username}/toggle")
def toggle(username: str, body: Toggle):
    url = normalize(username)
    d = load()
    if url not in d["accounts"]:
        raise HTTPException(404, "Аккаунт не найден")
    disabled = set(d["disabled"])
    if body.enabled:
        disabled.discard(url)
    else:
        disabled.add(url)
    d["disabled"] = sorted(disabled)
    save(d)
    return {"disabled": d["disabled"]}

@router.delete("/accounts/{username}")
def remove(username: str):
    url = normalize(username)
    d = load()
    d["accounts"] = [x for x in d["accounts"] if x != url]
    save(d)
    return {"accounts": d["accounts"]}

def cmd(args, timeout=300):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError("Не найден yt-dlp или FFmpeg. Установи зависимости и перезапусти приложение.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Истекло время ожидания Instagram/FFmpeg") from exc
    if result.returncode:
        details = (result.stderr or result.stdout or "").strip()
        lines = [line.strip() for line in details.splitlines() if line.strip()]
        error = next((line for line in reversed(lines) if "ERROR:" in line), lines[-1] if lines else "Неизвестная ошибка")
        raise RuntimeError(error[:600])
    return result

def render(source, target, background):
    seconds = duration(source)
    if seconds <= 0:
        raise ValueError("Видео без корректной длительности")
    if background is None:
        filt = ("[0:v]split=2[base][fg];[base]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,boxblur=40:10[bg];[fg]scale=1000:1700:"
                "force_original_aspect_ratio=decrease[front];"
                "[bg][front]overlay=(W-w)/2:(H-h)/2:shortest=1,fps=30,format=yuv420p[v]")
        args = ["ffmpeg", "-y", "-i", str(source), "-filter_complex", filt]
    else:
        filt = ("[1:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920[bg];[0:v]scale=1000:1700:"
                "force_original_aspect_ratio=decrease[front];"
                "[bg][front]overlay=(W-w)/2:(H-h)/2:shortest=1,fps=30,format=yuv420p[v]")
        offset = random.uniform(0, max(0, duration(background) - seconds))
        args = ["ffmpeg", "-y", "-i", str(source), "-stream_loop", "-1",
                "-ss", str(offset), "-i", str(background), "-filter_complex", filt]
    args += ["-map", "[v]", "-map", "0:a?", "-t", str(seconds),
             "-c:v", "libx264", "-preset", "medium", "-crf", "19",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", str(target)]
    cmd(args, timeout=3600)

def discover_instaloader(account):
    import instaloader
    from itertools import islice
    username = urlparse(account).path.strip("/")
    loader = instaloader.Instaloader(quiet=True)
    profile = instaloader.Profile.from_username(loader.context, username)
    posts = profile.get_posts()
    links = []
    for post in islice(posts, 150):
        if post.is_video:
            links.append(f"https://www.instagram.com/p/{post.shortcode}/")
    return links


def discover(account):
    result = cmd(["yt-dlp", "--flat-playlist", "--dump-single-json",
                  "--playlist-end", "100", account], timeout=120)
    data = json.loads(result.stdout)
    links = []
    for entry in data.get("entries") or []:
        if not entry:
            continue
        url = entry.get("webpage_url") or entry.get("url") or ""
        if not url.startswith("https://www.instagram.com/"):
            ident = entry.get("id", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{5,30}", ident):
                continue
            url = "https://www.instagram.com/reel/" + ident + "/"
        parsed = urlparse(url)
        if parsed.hostname in ("www.instagram.com", "instagram.com") and re.fullmatch(
            r"/(reel|p|tv)/[A-Za-z0-9_-]+/?", parsed.path
        ):
            links.append(url)
    return list(dict.fromkeys(links))

def is_profile_extraction_error(message):
    return "Unable to extract data" in message and "instagram:user" in message


async def process(limit):
    JOB.update(status="running", done=0, total=0, errors=[])
    try:
        d = load()
        known = set(d["processed"])
        hashes = set(d["hashes"])
        candidates = []
        for account in d["accounts"]:
            if account in d["disabled"]:
                continue
            try:
                try:
                    links = await asyncio.to_thread(discover, account)
                except Exception as first_error:
                    try:
                        links = await asyncio.to_thread(discover_instaloader, account)
                    except Exception as second_error:
                        raise RuntimeError(f"yt-dlp: {first_error}; Instaloader: {second_error}") from second_error
                random.shuffle(links)
                candidates.extend((account, link) for link in links if link not in known)
            except Exception as exc:
                JOB["errors"].append(f"{account}: "+ ("Instagram сейчас не отдает список Reels этого профиля через yt-dlp. Обнови yt-dlp; если ошибка повторится, используй официальный экспорт своих видео или импорт отдельных MP4. " if is_profile_extraction_error(str(exc)) else "") + str(exc)[:500])
        candidates.extend(("direct", link) for link in d["links"] if link not in known)
        random.shuffle(candidates)
        counts = {}
        selected = []
        for account, link in candidates:
            if account != "direct" and counts.get(account, 0) >= limit:
                continue
            counts[account] = counts.get(account, 0) + 1
            selected.append((account, link))
        JOB["total"] = len(selected)
        for account, link in selected:
            stem = "ig_" + hashlib.sha256(link.encode()).hexdigest()[:18]
            source = ROOT / (stem + ".mp4")
            output = settings.upload_dir / (stem + ".mp4")
            temp = ROOT / (stem + ".rendering.mp4")
            try:
                if output.exists() and output.stat().st_size:
                    known.add(link)
                    continue
                if not source.exists():
                    await asyncio.to_thread(cmd, ["yt-dlp", "--no-playlist",
                        "--max-filesize", "500M", "-f", "bv*+ba/b",
                        "--merge-output-format", "mp4", "-o", str(source), link], 900)
                if not source.exists() or duration(source) <= 0:
                    raise ValueError("Instagram не предоставил доступный MP4")
                digest = await asyncio.to_thread(lambda: hashlib.sha256(source.read_bytes()).hexdigest())
                if digest in hashes:
                    known.add(link)
                    continue
                bgs = [BACKGROUNDS / b["name"] for b in backgrounds() if b["enabled"]]
                bg = random.choice(bgs) if bgs and random.choice([True, False]) else None
                await asyncio.to_thread(render, source, temp, bg)
                if duration(temp) <= 0:
                    raise ValueError("Рендер не прошёл проверку")
                temp.replace(output)
                hashes.add(digest)
                known.add(link)
                JOB["done"] += 1
            except Exception as exc:
                JOB["errors"].append(f"{link}: {str(exc)[:600]}")
            finally:
                temp.unlink(missing_ok=True)
                d["processed"] = sorted(known)
                d["links"] = [url for url in d["links"] if url not in known]
                d["hashes"] = sorted(hashes)
                save(d)
        JOB["status"] = "done" if not JOB["errors"] else "partial"
    except Exception as exc:
        JOB["status"] = "error"
        JOB["errors"].append(str(exc)[:250])
    finally:
        LOCK.release()

@router.post("/run")
async def run(body: Run):
    global TASK
    if not 1 <= body.limit_per_account <= 100:
        raise HTTPException(400, "Лимит 1–100")
    if LOCK.locked():
        raise HTTPException(409, "Загрузка Instagram уже запущена")
    if not load()["accounts"] and not load()["links"]:
        raise HTTPException(400, "Добавь Instagram-аккаунт или ссылку на публикацию")
    await LOCK.acquire()
    TASK = asyncio.create_task(process(body.limit_per_account))
    return {"started": True}
