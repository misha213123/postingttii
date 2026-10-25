"""Remove a confirmed uploaded video from the source queue without losing the MP4.

The destination is a local "posted" directory. If Meta still needs its public
media URL, retarget the active in-memory media token to the relocated file.
No cleanup happens for a failed or unconfirmed upload.
"""
from __future__ import annotations

import errno
import os
import secrets
import shutil
from pathlib import Path
from typing import MutableMapping

from app.config import settings
from app.publish_state import publish_state

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}


def posted_folder_for(source: Path) -> Path:
    if source.parent.resolve() == settings.upload_dir.resolve():
        return settings.posted_dir.resolve()
    return (source.parent / "posted").resolve()


def move_confirmed_video(
    source: Path,
    *,
    expected_key: str | None = None,
    media_tokens: MutableMapping[str, Path] | None = None,
) -> Path:
    """Move exactly the expected file; never remove an unrelated replacement.

    This should be invoked only after a confirmed platform result, or after
    publication deduplication has verified a previously completed target.
    """
    original = source.resolve(strict=True)
    if original.suffix.lower() not in VIDEO_EXTENSIONS or not original.is_file():
        raise ValueError("Неизвестный формат видео")
    if expected_key is not None and publish_state.video_key(original) != expected_key:
        raise ValueError("Видео изменилось после публикации; исходник сохранён")
    destination_folder = posted_folder_for(original)
    destination_folder.mkdir(parents=True, exist_ok=True)
    if destination_folder == original.parent:
        raise ValueError("Папка хранения совпадает с исходной папкой")
    destination = destination_folder / original.name
    while destination.exists():
        destination = destination_folder / (
            original.stem + "_" + secrets.token_hex(4) + original.suffix
        )
    # Fast, atomic on one volume. A cross-volume destination is copied before
    # the source is removed, and a failure retains the source.
    try:
        os.replace(original, destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copy2(original, destination)
        try:
            os.unlink(original)
        except OSError:
            destination.unlink(missing_ok=True)
            raise

    if media_tokens is not None:
        for token, mapped in tuple(media_tokens.items()):
            if mapped.resolve() == original:
                media_tokens[token] = destination
    return destination
