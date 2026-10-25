"""Regression checks for immediate post-publish queue cleanup and Telegram modes."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import browser_instagram, telegram_studio
from app.publish_state import publish_state
from app import upload_cleanup


class ConfirmedUploadCleanupTests(unittest.TestCase):
    def test_moves_to_posted_and_keeps_active_meta_token(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            queued = folder / "video.mp4"
            queued.write_bytes(b"confirmed-video")
            tokens = {"token_1": queued, "other": folder / "cover.jpg"}
            with patch.object(upload_cleanup, "settings", SimpleNamespace(
                upload_dir=folder, posted_dir=folder / "posted",
            )):
                key = publish_state.video_key(queued)
                destination = upload_cleanup.move_confirmed_video(
                    queued, expected_key=key, media_tokens=tokens,
                )
            self.assertFalse(queued.exists())
            self.assertEqual(destination.read_bytes(), b"confirmed-video")
            self.assertEqual(tokens["token_1"], destination)
            self.assertEqual(tokens["other"], folder / "cover.jpg")

    def test_replaced_file_not_removed(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            queued = folder / "video.mp4"
            queued.write_bytes(b"original")
            key = publish_state.video_key(queued)
            queued.write_bytes(b"updated")
            with patch.object(upload_cleanup, "settings", SimpleNamespace(
                upload_dir=folder, posted_dir=folder / "posted",
            )):
                with self.assertRaisesRegex(ValueError, "изменилось"):
                    upload_cleanup.move_confirmed_video(queued, expected_key=key)
            self.assertTrue(queued.is_file())

    def test_same_named_posted_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "posted").mkdir()
            old = folder / "posted" / "clip.mp4"
            old.write_bytes(b"old")
            new = folder / "clip.mp4"
            new.write_bytes(b"new")
            with patch.object(upload_cleanup, "settings", SimpleNamespace(
                upload_dir=folder, posted_dir=folder / "posted",
            )):
                result = upload_cleanup.move_confirmed_video(new)
            self.assertEqual(old.read_bytes(), b"old")
            self.assertEqual(result.read_bytes(), b"new")
            self.assertNotEqual(old, result)


class BrowserUploadCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_only_removed_after_all_selected_accounts_publish(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "test.mp4"
            source.write_bytes(b"upload")
            job = {"id": "job123", "cancel": False, "items": [], "progress": 0}
            with (
                patch.object(browser_instagram, "_read", return_value={
                    "a": {"username": "first"}, "b": {"username": "second"},
                }),
                patch.object(browser_instagram, "_context", new_callable=AsyncMock),
                patch.object(browser_instagram, "_publish_via_browser",
                             new_callable=AsyncMock, side_effect=["published", "published"]),
            ):
                await browser_instagram._run_job(
                    job, [("a", source), ("b", source)], "caption"
                )
            self.assertEqual(job["status"], "completed")
            self.assertFalse(source.exists())
            self.assertTrue((Path(td) / "posted" / "test.mp4").exists())
            self.assertTrue(job["items"][-1]["removed_from_queue"])

    async def test_uncertain_second_upload_keeps_source(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "test.mp4"
            source.write_bytes(b"upload")
            job = {"id": "job124", "cancel": False, "items": [], "progress": 0}
            with (
                patch.object(browser_instagram, "_read", return_value={
                    "a": {"username": "first"}, "b": {"username": "second"},
                }),
                patch.object(browser_instagram, "_context", new_callable=AsyncMock),
                patch.object(browser_instagram, "_publish_via_browser",
                             new_callable=AsyncMock, side_effect=["published", "needs_review"]),
            ):
                await browser_instagram._run_job(
                    job, [("a", source), ("b", source)], "caption"
                )
            self.assertTrue(source.exists())
            self.assertEqual(job["status"], "needs_review")


class ExclusiveTelegramModeTests(unittest.TestCase):
    def test_video_note_never_accepted_as_normal_video(self):
        note = SimpleNamespace(video_note=object(), video=object(),
                               document=SimpleNamespace(mime_type="video/mp4"))
        normal = SimpleNamespace(video_note=None, video=object(),
                                 document=SimpleNamespace(mime_type="video/mp4"))
        self.assertTrue(telegram_studio.telegram_media_matches(note, "circles"))
        self.assertFalse(telegram_studio.telegram_media_matches(note, "videos"))
        self.assertFalse(telegram_studio.telegram_media_matches(normal, "circles"))
        self.assertTrue(telegram_studio.telegram_media_matches(normal, "videos"))
        self.assertFalse(telegram_studio.telegram_media_matches(normal, "other"))


if __name__ == "__main__":
    unittest.main()
