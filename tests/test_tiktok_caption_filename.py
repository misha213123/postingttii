"""TikTok captions must not leak local video filenames in any publish path."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.tiktok_text import clean_tiktok_caption, generate_tiktok_caption


class TikTokCaptionCleanupTests(unittest.TestCase):
    def test_telegram_filename_glued_to_first_word(self):
        caption = "tg_ekmzk_65Тикток-подборка: короткое видео #видео"
        self.assertEqual(
            clean_tiktok_caption(caption, "tg_ekmzk_65.mp4"),
            "Тикток-подборка: короткое видео #видео",
        )

    def test_extension_and_isolated_mentions(self):
        self.assertEqual(
            clean_tiktok_caption("tg_ekmzk_65.mp4 — новинка #видео", "tg_ekmzk_65.mp4"),
            "новинка #видео",
        )

    def test_import_filename_middle_of_caption(self):
        self.assertEqual(
            clean_tiktok_caption("Обзор tg_ekmzk_65видео #обзор", "tg_ekmzk_65.mp4"),
            "Обзор видео #обзор",
        )

    def test_non_import_word_is_preserved(self):
        self.assertEqual(
            clean_tiktok_caption("funny cats #котики", "fun.mp4"),
            "funny cats #котики",
        )

    def test_empty_after_removing_name_has_neutral_fallback(self):
        self.assertEqual(
            clean_tiktok_caption("tg_ekmzk_65.mp4", "tg_ekmzk_65.mp4"),
            "Короткое видео #видео",
        )

    def test_model_output_is_cleaned_before_return(self):
        response = SimpleNamespace(output_text="tg_ekmzk_65Нейтральный ролик #видео")
        client = MagicMock()
        client.responses.create.return_value = response
        with (
            patch("app.services.tiktok_text.OpenAI", return_value=client),
            patch("app.services.tiktok_text.settings",
                  SimpleNamespace(openai_api_key="fake", openai_model="test")),
        ):
            caption = generate_tiktok_caption("tg_ekmzk_65.mp4")
        self.assertEqual(caption, "Нейтральный ролик #видео")


if __name__ == "__main__":
    unittest.main()
