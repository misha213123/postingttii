"""Prevent Telegram circles / ordinary videos mode desynchronization."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.telegram_studio import RenderRequest, telegram_media_matches

HTML = Path(__file__).resolve().parents[1] / "static" / "telegram-studio.html"


class TelegramModeClassificationTests(unittest.TestCase):
    def test_native_round_video_note_with_document_attribute(self):
        note = SimpleNamespace(
            video_note=None,
            video=object(),
            document=SimpleNamespace(
                mime_type="video/mp4",
                attributes=[SimpleNamespace(round_message=True)]
            ),
        )
        self.assertTrue(telegram_media_matches(note, "circles"))
        self.assertFalse(telegram_media_matches(note, "videos"))

    def test_regular_video_document_never_downloaded_in_circles_mode(self):
        ordinary = SimpleNamespace(
            video_note=None,
            video=object(),
            document=SimpleNamespace(
                mime_type="video/mp4",
                attributes=[SimpleNamespace(round_message=False)]
            ),
        )
        self.assertFalse(telegram_media_matches(ordinary, "circles"))
        self.assertTrue(telegram_media_matches(ordinary, "videos"))

    def test_round_video_note_cannot_become_normal_video(self):
        round_message = SimpleNamespace(
            video_note=object(),
            video=object(),
            document=SimpleNamespace(mime_type="video/mp4", attributes=[]),
        )
        self.assertTrue(telegram_media_matches(round_message, "circles"))
        self.assertFalse(telegram_media_matches(round_message, "videos"))

    def test_circles_is_default_for_api_request(self):
        self.assertEqual(RenderRequest().media_mode, "circles")


class TelegramModeFrontendWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = HTML.read_text(encoding="utf-8")

    def test_editor_tab_sets_download_mode(self):
        self.assertIn("function switchRenderEditor(name)", self.page)
        self.assertIn("el('tgMediaMode').value=circles?'circles':'videos';", self.page)
        self.assertIn("updateTelegramMediaMode();", self.page)

    def test_dropdown_and_editor_use_shared_mode(self):
        self.assertIn("const mode=el('tgMediaMode').value==='videos'?'videos':'circles';", self.page)
        self.assertIn("el('tgEditor').hidden=!circles;", self.page)
        self.assertIn("el('igEditor').hidden=circles;", self.page)
        self.assertIn("localStorage.setItem('postingttii_tg_media_mode',mode);", self.page)

    def test_submission_sends_and_checks_mode(self):
        self.assertIn("media_mode:mode", self.page)
        self.assertIn("response.media_mode!==mode", self.page)
        self.assertIn("Скачать и смонтировать только кружки", self.page)


if __name__ == "__main__":
    unittest.main()
