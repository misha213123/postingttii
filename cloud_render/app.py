"""Single-user, password-gated mobile uploader for one-off Viral Reels Pro A render.

Only this FastAPI app is mounted on Render. The local PostingTTII dashboard,
OAuth accounts, original Telegram Studio routes, and data are never published.
Uploaded input is deleted after rendering; completed outputs are ephemeral.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import os
import secrets
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from PIL import Image, ImageDraw
from app import viral_reels_pro as pro


app = FastAPI(title="Private Viral Reels Pro Mobile", docs_url=None, redoc_url=None,
              openapi_url=None)
PASSCODE = os.getenv("CLOUD_RENDER_PASSCODE", "")
SESSION_SECONDS = 12 * 60 * 60
MAX_VIDEO_BYTES = 75 * 1024 * 1024
MAX_BG_BYTES = 10 * 1024 * 1024
MAX_SECONDS = 30
LOCK = asyncio.Lock()
STATE: dict = {"status": "idle", "stage": "Загрузи видео для рендера", "progress": 0}
FAILED_LOGINS: dict[str, list[float]] = {}
DEFAULT_BG = pro.OWN_BACKGROUNDS / "mobile_background.jpg"


def _create_default_background():
    if DEFAULT_BG.exists():
        return
    image = Image.new("RGB", (540, 960))
    pixels = image.load()
    for y in range(960):
        t = y / 959
        color = (int(15 + t * 12), int(23 + t * 8), int(46 + t * 17))
        for x in range(540):
            pixels[x, y] = color
    image.save(DEFAULT_BG, quality=88)


_create_default_background()


def _session() -> str:
    expires = str(int(time.time() + SESSION_SECONDS))
    signature = hmac.new(PASSCODE.encode(), expires.encode(), hashlib.sha256).hexdigest()
    return expires + "." + signature


def _logged_in(req: Request) -> bool:
    if not PASSCODE:
        return False
    value = req.cookies.get("vp_session", "")
    try:
        expires, signature = value.split(".", 1)
        if int(expires) < time.time() or int(expires) > time.time() + SESSION_SECONDS + 60:
            return False
        expected = hmac.new(PASSCODE.encode(), expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


def _require(req: Request):
    if not _logged_in(req):
        raise HTTPException(401, "Войди с кодом доступа")
    if req.method in ("POST", "PUT", "DELETE"):
        origin = req.headers.get("origin", "")
        if origin and urlparse(origin).netloc != req.headers.get("host", ""):
            raise HTTPException(403, "Чужой сайт не может отправлять задания")


LOGIN_PAGE = """<!doctype html><html lang="ru"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta charset="utf-8"><title>Viral Reels Pro — приватный вход</title>
<style>body{background:#0d1525;color:#eaf0ff;font:16px system-ui;margin:0;padding:24px}
main{max-width:460px;margin:8vh auto;background:#18233a;border-radius:20px;padding:24px}
input,button{padding:14px;border-radius:12px;font:inherit;width:100%;box-sizing:border-box;margin:12px 0}
input{background:#0b1221;color:#fff;border:1px solid #526484}button{background:#5485ef;color:white;border:0}
small{color:#a7b7d2}</style>
<main><h1>Viral Reels Pro №3</h1><p>Облачный рендер одного видео с телефона.</p>
<form action="/login" method="post"><label>Код доступа<input type="password" name="passcode" autocomplete="off" required></label>
<button type="submit">Открыть редактор</button></form>
<small>Приватный инструмент. Код не хранится в GitHub.</small></main></html>"""

MAIN_PAGE = """<!doctype html><html lang="ru"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta charset="utf-8"><title>Viral Reels Pro — рендер на телефоне</title>
<style>
body{margin:0;background:#0b1221;color:#e9f1ff;font:15px system-ui}
main{max-width:620px;margin:auto;padding:22px 14px}
.card{background:#18243a;padding:18px;border-radius:18px;margin:16px 0}
label{display:block;margin:15px 0 5px}input,select,button{box-sizing:border-box;width:100%;padding:12px;border-radius:11px;font:inherit}
input,select{background:#0c182c;color:#fff;border:1px solid #3e5679}
button{border:0;background:#467de9;color:#fff;font-weight:700;margin:16px 0}
button:disabled{opacity:.45}a{color:#9bc2ff}
small,.hint{color:#a4b3d0;line-height:1.55}progress{width:100%;height:16px}
video{width:100%;max-height:450px;background:#000;border-radius:12px}
</style>
<main><h1>Viral Reels Pro №3</h1>
<p class="hint">Разовый облачный MP4-рендер — шаблон A. Домашний компьютер не нужен.</p>
<div class="card"><h2>1. Загрузить исходник</h2>
<form id="render">
<label>Видео с телефона (MP4 / MOV / WebM, до 75 МБ)</label>
<input type="file" name="video" accept="video/mp4,video/quicktime,video/webm,.mov" required>
<label>Фон — изображение JPG/PNG/WebP (необязательно)</label>
<input type="file" name="background" accept="image/jpeg,image/png,image/webp">
<label>Сколько секунд обработать (максимум 30)</label>
<select name="seconds"><option value="6">Первые 6 секунд — быстрый тест</option>
<option value="15" selected>Первые 15 секунд</option><option value="30">Первые 30 секунд</option></select>
<button id="submit">▶ Запустить рендер</button></form>
<small>Если видео короче выбранной длительности, обработается целиком. Фон по умолчанию — тёмный градиент. Используется круглая маска, 540×960, 24 FPS, AAC (экономный режим).</small>
</div>
<div class="card"><h2>2. Результат</h2>
<p id="status">Проверка статуса...</p>
<progress id="progress" max="100" value="0"></progress>
<div id="result" hidden><video id="player" controls playsinline preload="metadata"></video>
<p><a id="download" href="/download">⬇ Скачать готовый MP4 на телефон</a></p>
<small>Скачай результат до перезапуска облачного сервиса: на бесплатном Render временные файлы не гарантированно сохраняются.</small></div>
</div>
</main><script>
const form=document.getElementById('render'),button=document.getElementById('submit');
const statusLine=document.getElementById('status');
async function refresh(){
 try{
  const response=await fetch('/api/status',{cache:'no-store'});
  const data=await response.json();
  if(!response.ok)throw Error(data.detail||'Нет доступа');
  statusLine.textContent=(data.stage||data.status)+(data.error?' — '+data.error:'');
  document.getElementById('progress').value=data.progress||0;
  button.disabled=['running','queued'].includes(data.status);
  const done=data.status==='completed';
  document.getElementById('result').hidden=!done;
  if(done&&document.getElementById('player').dataset.job!==data.id){
   document.getElementById('player').dataset.job=data.id;
   document.getElementById('player').src='/download?view=1&id='+encodeURIComponent(data.id);
   document.getElementById('download').href='/download?id='+encodeURIComponent(data.id);
  }
 }catch(err){statusLine.textContent='Связь с Render прервана. Обнови страницу; если задание исчезло, запусти короткий 6-секундный рендер.'}
}
form.addEventListener('submit',async ev=>{
 ev.preventDefault();button.disabled=true;statusLine.textContent='Отправляю видео с телефона...';
 try{
  const response=await fetch('/api/render',{method:'POST',body:new FormData(form)});
  const result=await response.json();
  if(!response.ok)throw Error(result.detail||'Не получилось начать');
  statusLine.textContent='Рендер запущен. Открой результат ниже.';
 }catch(err){statusLine.textContent='Ошибка: '+err.message}
 finally{await refresh()}
});
refresh();setInterval(refresh,2500);
</script></html>"""


@app.get("/healthz")
def healthz():
    return {"ok": True, "ffmpeg": bool(shutil.which("ffmpeg"))}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not PASSCODE:
        return HTMLResponse("CLOUD_RENDER_PASSCODE не настроен", status_code=503)
    return HTMLResponse(MAIN_PAGE if _logged_in(request) else LOGIN_PAGE,
                        headers={"Cache-Control": "no-store"})


@app.post("/login")
def login(request: Request, passcode: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    recent = [t for t in FAILED_LOGINS.get(ip, []) if now - t < 600]
    if len(recent) >= 6:
        raise HTTPException(429, "Слишком много попыток; попробуй позже")
    if not PASSCODE or not hmac.compare_digest(passcode, PASSCODE):
        FAILED_LOGINS[ip] = recent + [now]
        return HTMLResponse("Неверный код доступа. <a href='/'>Назад</a>", status_code=403)
    FAILED_LOGINS.pop(ip, None)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("vp_session", _session(), secure=True, httponly=True,
                        samesite="strict", max_age=SESSION_SECONDS)
    return response


async def _read_upload(upload: UploadFile, dest: Path, max_bytes: int):
    count = 0
    try:
        with dest.open("wb") as out:
            while part := await upload.read(1024 * 1024):
                count += len(part)
                if count > max_bytes:
                    raise HTTPException(413, "Файл слишком большой")
                out.write(part)
        if count < 1024:
            raise HTTPException(400, "Файл пустой или слишком маленький")
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


async def _render_mobile(job: dict, source: Path, background: Path, seconds: float) -> Path:
    """Lightweight circle and background compositor for Render free (512 MB).

    Avoid geq on full-HD frames, zoompan and multiple FFmpeg filter threads.
    A small static alpha mask is reused, and the original audio is kept.
    """
    width, height, size, fps = 540, 960, 340, 24
    work = pro.TMP / job["id"]
    work.mkdir(parents=True, exist_ok=True)
    mask_path = work / "round-mask.png"
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, size - 2, size - 2), fill=255)
    mask.save(mask_path)
    output_dir = pro.EXPORTS / "mobile"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / (job["id"] + ".mp4")
    info = await asyncio.to_thread(pro._probe, source)
    filters = (
        f"[0:v]fps={fps},scale={size}:{size}:"
        "force_original_aspect_ratio=increase:flags=fast_bilinear,"
        f"crop={size}:{size},setsar=1,format=yuv420p[face];"
        "[2:v]format=gray[mask];"
        "[face][mask]alphamerge[round];"
        f"[1:v]scale={width}:{height}:"
        "force_original_aspect_ratio=increase:flags=fast_bilinear,"
        f"crop={width}:{height},setsar=1,format=yuv420p[back];"
        "[back][round]overlay=100:152:shortest=1:format=auto,"
        "format=yuv420p[v]"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-y",
        "-filter_threads", "1", "-filter_complex_threads", "1",
        "-threads", "1", "-i", str(source),
        "-loop", "1", "-framerate", str(fps), "-i", str(background),
        "-loop", "1", "-framerate", str(fps), "-i", str(mask_path),
    ]
    if not info["audio"]:
        cmd += ["-f", "lavfi", "-t", str(seconds),
                "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += [
        "-filter_complex", filters, "-map", "[v]",
        "-map", "0:a:0" if info["audio"] else "3:a:0",
        "-t", str(seconds), "-r", str(fps),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
        "-threads", "1", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ar", "44100",
        "-movflags", "+faststart", str(output),
    ]
    job["stage"] = "Экономный рендер: 540×960, круг и звук"
    try:
        await pro._ffmpeg(job, cmd, output, seconds, 0, 1, cwd=work)
        result = await asyncio.to_thread(pro._probe, output)
        if (result["width"], result["height"]) != (width, height):
            raise RuntimeError("Проверка MP4: неверное разрешение")
        if not result["audio"] or output.stat().st_size < 1024:
            raise RuntimeError("Проверка MP4: отсутствует звук или файл повреждён")
        if abs(result["duration"] - seconds) > 1.0:
            raise RuntimeError("Проверка MP4: неверная длительность")
        return output
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def _process(job: dict, project: dict, media: Path, background: Path | None):
    try:
        async with LOCK:
            job["status"] = "running"
            cfg = pro.Config.model_validate(project["config"])
            seconds = cfg.clips[0].end or 6.0
            selected_bg = pro.OWN_BACKGROUNDS / cfg.background_name
            job["stage"] = "Готовлю экономный MP4 (меньше расход памяти)"
            output = await _render_mobile(job, media, selected_bg, seconds)
            job["output"] = str(output)
            job["status"] = "completed"
            job["stage"] = "Готово: MP4 можно скачать на телефон"
            job["progress"] = 100
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)[-700:]
        job["stage"] = "Ошибка обработки — можно попробовать 6 секунд"
    finally:
        media.unlink(missing_ok=True)
        if background:
            background.unlink(missing_ok=True)


@app.post("/api/render")
async def start_render(request: Request, video: UploadFile = File(...),
                       background: UploadFile | None = File(None),
                       seconds: int = Form(15)):
    _require(request)
    if STATE.get("status") in ("queued", "running"):
        raise HTTPException(409, "Уже обрабатывается другое видео")
    if Path(video.filename or "").suffix.lower() not in pro.MEDIA_EXT:
        raise HTTPException(400, "Нужен MP4, MOV, M4V или WebM")
    if seconds not in (6, 15, 30):
        raise HTTPException(400, "Выбери 6, 15 или 30 секунд")
    if background is not None and background.filename and (
        Path(background.filename).suffix.lower() not in pro.IMAGE_EXT
    ):
        raise HTTPException(400, "Фон должен быть JPG/PNG/WebP")
    media_id = uuid.uuid4().hex
    file_path = pro.MEDIA / (media_id + ".mp4")
    bg_path = None
    try:
        await _read_upload(video, file_path, MAX_VIDEO_BYTES)
        details = await asyncio.to_thread(pro._probe, file_path)
        if not (0.4 <= details["duration"] <= 600):
            raise HTTPException(400, "Исходник должен длиться от 0.4 до 600 секунд")
        bg_name = DEFAULT_BG.name
        if background and background.filename:
            suffix = Path(background.filename).suffix.lower()
            bg_name = uuid.uuid4().hex + suffix
            bg_path = pro.OWN_BACKGROUNDS / bg_name
            await _read_upload(background, bg_path, MAX_BG_BYTES)
            try:
                with Image.open(bg_path) as img:
                    img.verify()
            except Exception:
                raise HTTPException(400, "Не удалось прочитать картинку фона")
        if STATE.get("output"):
            Path(STATE["output"]).unlink(missing_ok=True)
        job_id = uuid.uuid4().hex
        cfg = pro.Config(
            clips=[pro.Clip(media_id=media_id, start=0,
                            end=min(float(seconds), details["duration"]))],
            background_source="local", background_name=bg_name,
            circle_size=620, circle_x=230, circle_y=260,
            smooth="off", fps=30, bitrate_mbps=4,
            denoise=False, loudness=False, banner_enabled=False,
        )
        project = {"id": job_id, "config": cfg.model_dump(), "exports": [],
                   "analysis": {}, "metrics": []}
        STATE.clear()
        STATE.update({"id": job_id, "status": "queued", "progress": 0,
                      "stage": "Рендер в очереди", "notes": [], "cancel": False})
        asyncio.create_task(_process(STATE, project, file_path, bg_path))
        return JSONResponse({"id": job_id, "status": "queued"}, status_code=202)
    except Exception:
        file_path.unlink(missing_ok=True)
        if bg_path:
            bg_path.unlink(missing_ok=True)
        raise


@app.get("/api/status")
def status(request: Request):
    _require(request)
    return {key: value for key, value in STATE.items() if key not in ("output",)}


@app.get("/download")
def download(request: Request, id: str, view: int = 0):
    _require(request)
    if STATE.get("status") != "completed" or id != STATE.get("id"):
        raise HTTPException(404, "Результат ещё не готов")
    file_path = Path(STATE["output"])
    if not file_path.is_file():
        raise HTTPException(404, "Файл уже удалён сервером")
    return FileResponse(
        file_path, media_type="video/mp4",
        filename="viral_reel_" + id[:8] + ".mp4",
        content_disposition_type="inline" if view else "attachment",
        headers={"Cache-Control": "no-store"},
    )
