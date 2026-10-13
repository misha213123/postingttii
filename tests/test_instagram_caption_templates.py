"""Regression checks for Instagram captions sourced from a local TXT file."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import instagram_caption_templates as captions


class InstagramCaptionTemplatesTests(unittest.TestCase):
    def test_exact_blocks_emoji_and_hashtags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "templates.txt"
            original = ("日本語の説明🎬\n#日本語 #fyp", "次の動画です✨\n改行を保持します", "最後のテンプレ")
            path.write_text("\n\n---\n\n".join(original), encoding="utf-8")
            with patch.object(captions, "template_file", return_value=path):
                result = captions.load_instagram_caption_templates()
                self.assertEqual(result, original)
                chosen = captions.instagram_caption_from_file("reel1.mp4", 6)
                self.assertIn(chosen, original)
                self.assertEqual(chosen, captions.instagram_caption_from_file("reel1.mp4", 6))

    def test_missing_file_fails_instead_of_old_captions(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(captions, "template_file", return_value=Path(tmp) / "not_found.txt"):
                with self.assertRaises(captions.CaptionTemplatesError):
                    captions.instagram_caption_from_file("video.mp4", 1)

    def test_blank_template_bank_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.txt"
            path.write_text("---\n\n---", encoding="utf-8")
            with patch.object(captions, "template_file", return_value=path):
                with self.assertRaises(captions.CaptionTemplatesError):
                    captions.load_instagram_caption_templates()

    def test_excessively_long_caption_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "too_long.txt"
            path.write_text("あ" * 2201, encoding="utf-8")
            with patch.object(captions, "template_file", return_value=path):
                with self.assertRaises(captions.CaptionTemplatesError):
                    captions.load_instagram_caption_templates()


if __name__ == "__main__":
    unittest.main()
