"""Regression tests for still-image backgrounds and Telegram media filtering."""
from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from PIL import Image
from pydantic import ValidationError

from app import telegram_studio as tg
from app import instagram_studio as ig


class TelegramMediaSelectionTests(unittest.TestCase):
    def test_round_video_notes_only_in_circles_mode(self):
        round_note = SimpleNamespace(video_note=object(), video=object(), document=None)
        self.assertTrue(tg.telegram_media_matches(round_note, "circles"))
        self.assertFalse(tg.telegram_media_matches(round_note, "videos"))

    def test_normal_video_and_video_document_only_in_videos_mode(self):
        video = SimpleNamespace(video_note=None, video=object(), document=object())
        file_video = SimpleNamespace(
            video_note=None, video=None,
            document=SimpleNamespace(mime_type="video/mp4"),
        )
        photo = SimpleNamespace(video_note=None, video=None, document=None)
        other = SimpleNamespace(
            video_note=None, video=None,
            document=SimpleNamespace(mime_type="application/pdf"),
        )
        for msg in (video, file_video):
            with self.subTest(msg=msg):
                self.assertTrue(tg.telegram_media_matches(msg, "videos"))
                self.assertFalse(tg.telegram_media_matches(msg, "circles"))
        for msg in (photo, other):
            self.assertFalse(tg.telegram_media_matches(msg, "videos"))
            self.assertFalse(tg.telegram_media_matches(msg, "circles"))

    def test_old_render_requests_still_default_to_circles(self):
        self.assertEqual(tg.RenderRequest().media_mode, "circles")
        self.assertEqual(tg.RenderRequest(media_mode="videos").media_mode, "videos")
        with self.assertRaises(ValidationError):
            tg.RenderRequest(media_mode="both")


class ImageBackgroundTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_png_produces_valid_jpeg_and_listed_as_image(self):
        with tempfile.TemporaryDirectory() as directory:
            img = Image.new("RGBA", (52, 82), (22, 220, 45, 125))
            encoded = BytesIO()
            img.save(encoded, "PNG")
            encoded.seek(0)
            with (
                patch.object(tg, "BACKGROUNDS", Path(directory)),
                patch.object(tg, "config", return_value={"backgrounds_disabled": []}),
            ):
                result = await tg.upload_background(UploadFile(filename="photo.png", file=encoded))
                saved = Path(directory) / result["name"]
                self.assertEqual(saved.suffix, ".jpg")
                self.assertTrue(saved.is_file())
                with Image.open(saved) as photo:
                    self.assertEqual(photo.size, (52, 82))
                    self.assertEqual(photo.mode, "RGB")
                backgrounds = tg.backgrounds()
                self.assertEqual(backgrounds[0]["kind"], "image")
                self.assertEqual(backgrounds[0]["name"], result["name"])
                preview = tg.background_preview(result["name"])
                self.assertEqual(preview.media_type, "image/jpeg")

    async def test_rejects_fake_image_data_and_does_not_leave_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(tg, "BACKGROUNDS", Path(directory)):
                with self.assertRaises(HTTPException) as exc:
                    await tg.upload_background(UploadFile(
                        filename="broken.webp", file=BytesIO(b"not really an image")))
                self.assertEqual(exc.exception.status_code, 400)
                self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_rejects_unallowed_background_extensions(self):
        with self.assertRaises(HTTPException) as exc:
            await tg.upload_background(UploadFile(
                filename="payload.svg", file=BytesIO(b"<svg></svg>")))
        self.assertEqual(exc.exception.status_code, 400)


class StillRenderTests(unittest.TestCase):
    def test_telegram_circle_uses_looped_image_without_probing_background_duration(self):
        with (
            patch.object(tg, "duration", return_value=8.0) as probe,
            patch.object(tg.subprocess, "run") as subprocess_run,
        ):
            tg.render(Path("circle.mp4"), Path("photo.jpg"), Path("render.mp4"))
        cmd = subprocess_run.call_args.args[0]
        self.assertIn(["-loop", "1", "-i", "photo.jpg"],
                      [cmd[i:i+4] for i in range(len(cmd)-3)])
        self.assertNotIn("-stream_loop", cmd)
        probe.assert_called_once_with(Path("circle.mp4"))
        self.assertIn("0:a?", cmd)
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("crop=1080:1920", graph)

    def test_instagram_normal_video_uses_photo_and_preserves_audio(self):
        with (
            patch.object(ig, "duration", return_value=12.0) as probe,
            patch.object(ig, "cmd") as command,
        ):
            ig.render(Path("source.mp4"), Path("render.mp4"), Path("photo.jpg"),
                      ig.InstagramLayout(background_mode="single", background_name="photo.jpg"))
        cmd = command.call_args.args[0]
        self.assertIn(["-loop", "1", "-i", "photo.jpg"],
                      [cmd[i:i+4] for i in range(len(cmd)-3)])
        self.assertNotIn("-stream_loop", cmd)
        probe.assert_called_once_with(Path("source.mp4"))
        self.assertIn("0:a?", cmd)
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("force_original_aspect_ratio=decrease", graph)

    def test_existing_mp4_background_still_loops(self):
        with (
            patch.object(tg, "duration", side_effect=[8.0, 23.0]),
            patch.object(tg.subprocess, "run") as command,
        ):
            tg.render(Path("circle.mp4"), Path("background.mp4"), Path("render.mp4"))
        cmd = command.call_args.args[0]
        self.assertIn("-stream_loop", cmd)
        self.assertNotIn("-loop", cmd)
