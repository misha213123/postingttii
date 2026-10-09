"""Regression tests for TikTok captions; no network requests and no uploads."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import tiktok_text


class TikTokCaptionTests(unittest.TestCase):
    def test_old_technical_caption_is_rejected(self):
        bad = (
            "ig_883d47690e21c4e7faНейтральное короткое описание для видео "
            "с неизвестной темой, без домыслов и рекламы. #инстаграм #видео #тикток"
        )
        self.assertTrue(
            tiktok_text.caption_needs_regeneration(bad, "ig_883d47690e21c4e7fa.mp4")
        )
        self.assertFalse(
            tiktok_text.caption_needs_regeneration(
                "Вот это момент! 😄 #смешное #момент #реакция", "other.mp4"
            )
        )
        self.assertTrue(tiktok_text.caption_needs_regeneration("", "other.mp4"))

    def _create_fake_client(self, responses):
        mock = MagicMock()
        mock.responses.create.side_effect = [
            SimpleNamespace(output_text=json.dumps({"caption": caption}, ensure_ascii=False))
            for caption in responses
        ]
        return mock

    def test_generation_uses_frames_and_not_filename(self):
        client = self._create_fake_client(
            ["Этот поворот событий заставил улыбнуться 😄 #моменты #юмор #видео"]
        )
        with (
            patch.object(
                tiktok_text, "settings",
                SimpleNamespace(openai_api_key="fake-key", openai_model="test-model")
            ),
            patch.object(
                tiktok_text, "_video_frames",
                return_value=["data:image/jpeg;base64,AA=="] * 3
            ),
            patch.object(tiktok_text, "OpenAI", return_value=client),
        ):
            caption = tiktok_text.generate_tiktok_caption(
                Path("ig_883d47690e21c4e7fa.mp4")
            )

        self.assertTrue(caption.startswith("Этот поворот"))
        args = client.responses.create.call_args.kwargs
        blocks = args["input"][0]["content"]
        self.assertEqual(sum(b["type"] == "input_image" for b in blocks), 3)
        self.assertNotIn("ig_883d47690e21c4e7fa", str(blocks))
        self.assertEqual(args["store"], False)

    def test_regeneration_after_instruction_leak(self):
        client = self._create_fake_client([
            "Нейтральное короткое описание для видео с неизвестной темой #видео #тикток",
            "Кто тоже заметил этот момент? 👀 #моменты #видео #реакция",
        ])
        with (
            patch.object(
                tiktok_text, "settings",
                SimpleNamespace(openai_api_key="fake-key", openai_model="test-model")
            ),
            patch.object(
                tiktok_text, "_video_frames",
                return_value=["data:image/jpeg;base64,AA=="] * 3
            ),
            patch.object(tiktok_text, "OpenAI", return_value=client),
        ):
            caption = tiktok_text.generate_tiktok_caption(Path("test.mp4"))
        self.assertIn("Кто тоже заметил", caption)
        self.assertEqual(client.responses.create.call_count, 2)

    def test_never_publishes_unusable_ai_output(self):
        client = self._create_fake_client([
            "Нейтральное короткое описание для видео с неизвестной темой",
            "Описание для видео с неизвестной темой",
        ])
        with (
            patch.object(
                tiktok_text, "settings",
                SimpleNamespace(openai_api_key="fake-key", openai_model="test-model")
            ),
            patch.object(
                tiktok_text, "_video_frames",
                return_value=["data:image/jpeg;base64,AA=="] * 3
            ),
            patch.object(tiktok_text, "OpenAI", return_value=client),
        ):
            with self.assertRaisesRegex(RuntimeError, "Публикация отменена"):
                tiktok_text.generate_tiktok_caption(Path("test.mp4"))
        self.assertEqual(client.responses.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
