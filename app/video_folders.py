"""Local-only folder registry and per-account render/publish routing.

Never exposes arbitrary filesystem browsing to Cloudflare tunnel: this router
is mounted exclusively on the loopback dashboard (port 8765).
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.store import store

router = APIRouter(prefix="/api/video-folders", tags=["video-folders"])
CONFIG = settings.data_dir / "video_folder_settings.json"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}


def _read() -> dict:
    try:
        obj = json.loads(CONFIG.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return {
                "folders": obj.get("folders", {}),
                "account_folders": obj.get("account_folders", {}),
                "account_backgrounds": obj.get("account_backgrounds", {}),
            }
    except (OSError, ValueError, TypeError):
        pass
    return {"folders": {}, "account_folders": {}, "account_backgrounds": {}}


def _write(data: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temp = CONFIG.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, CONFIG)


def folder_path(folder_id: str = "default", *, writable: bool = False) -> Path:
    if folder_id == "default":
        directory = settings.upload_dir.resolve()
    else:
        record = _read()["folders"].get(folder_id)
        if not isinstance(record, dict):
            raise ValueError("Папка не зарегистрирована. Выбери другую.")
        directory = Path(record["path"]).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"Папка недоступна: {directory}")
    if writable and not os.access(directory, os.W_OK):
        raise ValueError(f"Нет прав на запись в папку: {directory}")
    return directory


def list_videos(folder_id: str = "default") -> list[Path]:
    folder = folder_path(folder_id)
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
                   and p.resolve().parent == folder),
                  key=lambda p: p.stat().st_mtime)


def video_path(folder_id: str, filename: str) -> Path:
    if (not filename or Path(filename).name != filename
            or "/" in filename or "\\" in filename or filename in {".", ".."}):
        raise ValueError("Некорректное имя видео")
    folder = folder_path(folder_id)
    result = (folder / filename).resolve()
    if result.parent != folder:
        raise ValueError("Видео должно находиться непосредственно в выбранной папке")
    if not result.is_file() or result.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Видео не найдено в выбранной папке")
    return result


def account_key(slot: int) -> str:
    account = store.get("instagram", slot)
    if not account:
        raise ValueError("Instagram-аккаунт не подключён")
    return f"{slot}:{account.get('id', '')}"


def account_folder(slot: int) -> str:
    key = account_key(slot)
    folder_id = _read()["account_folders"].get(key) or "default"
    folder_path(folder_id)
    return folder_id


def account_background(slot: int, studio: Literal["telegram", "instagram"]) -> str:
    key = account_key(slot)
    record = _read()["account_backgrounds"].get(key) or {}
    return str(record.get(studio) or "")


def _localhost(request: Request):
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Выбор локальных папок доступен только на этом компьютере")


class NewFolder(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=1024)


class Binding(BaseModel):
    folder_id: str = "default"


class BackgroundBinding(BaseModel):
    studio: Literal["telegram", "instagram"]
    background_name: str = ""


@router.get("")
def folders(request: Request):
    _localhost(request)
    data = _read()
    accounts = store.list_accounts().get("instagram", [])
    folder_items = [{"id": "default", "label": "Стандартная папка inbox",
                     "path": str(settings.upload_dir.resolve()), "available": settings.upload_dir.is_dir()}]
    for folder_id, folder in sorted(data["folders"].items(), key=lambda item: item[1]["label"].lower()):
        path = Path(folder["path"]).expanduser()
        folder_items.append({"id": folder_id, "label": folder["label"], "path": str(path),
                             "available": path.is_dir()})
    bindings = {}
    for a in accounts:
        slot = int(a["slot"])
        key = f"{slot}:{a.get('id','')}"
        bindings[str(slot)] = {
            "account_id": str(a.get("id") or ""),
            "folder_id": data["account_folders"].get(key, "default"),
            "backgrounds": data["account_backgrounds"].get(key, {}),
        }
    return {"folders": folder_items, "accounts": accounts, "bindings": bindings}


@router.post("")
def register_folder(body: NewFolder, request: Request):
    _localhost(request)
    path = Path(body.path.strip().strip('"')).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(400, "Папка должна существовать на этом компьютере")
    if not os.access(path, os.R_OK):
        raise HTTPException(400, "Нет доступа к папке")
    data = _read()
    for folder_id, entry in data["folders"].items():
        if Path(entry["path"]).resolve() == path:
            return {"id": folder_id, "path": str(path), "already_exists": True}
    folder_id = secrets.token_hex(6)
    data["folders"][folder_id] = {"label": body.label.strip(), "path": str(path)}
    _write(data)
    return {"id": folder_id, "path": str(path), "already_exists": False}


@router.post("/pick")
def pick_folder(request: Request):
    _localhost(request)
    # Run only locally. tkinter opens Windows' native folder picker, which is
    # separate from browser file upload; no video files are uploaded.
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            selected = filedialog.askdirectory(parent=root, title="Выбери папку для PostingTTII")
        finally:
            root.destroy()
        return {"path": selected or ""}
    except Exception:
        raise HTTPException(503, "Системный выбор папки недоступен. Введи путь вручную.")


@router.put("/account/{slot}")
def bind_folder(slot: int, body: Binding, request: Request):
    _localhost(request)
    try:
        key = account_key(slot)
        folder_path(body.folder_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    data = _read()
    if body.folder_id == "default":
        data["account_folders"].pop(key, None)
    else:
        data["account_folders"][key] = body.folder_id
    _write(data)
    return {"slot": slot, "folder_id": body.folder_id}


@router.put("/account/{slot}/background")
def bind_background(slot: int, body: BackgroundBinding, request: Request):
    _localhost(request)
    try:
        key = account_key(slot)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.background_name:
        # Both studios use the same uploaded set of active Telegram MP4 backgrounds.
        from app.telegram_studio import BACKGROUNDS, backgrounds
        if body.background_name not in [item["name"] for item in backgrounds() if item["enabled"]]:
            raise HTTPException(400, "Выбранный MP4-фон не найден или выключен")
        if not (BACKGROUNDS / body.background_name).is_file():
            raise HTTPException(400, "Файл фона отсутствует")
    data = _read()
    record = data["account_backgrounds"].setdefault(key, {})
    if body.background_name:
        record[body.studio] = body.background_name
    else:
        record.pop(body.studio, None)
    _write(data)
    return {"slot": slot, "studio": body.studio, "background_name": body.background_name}
