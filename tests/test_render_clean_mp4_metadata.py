"""Exported MP4s omit source metadata while keeping the edit and audio intact."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app import instagram_studio as instagram
from app import telegram_studio as telegram


class CleanMp4ExportTests(unittest.TestCase):
    def assert_metadata_not_inherited(self, command):
        self.assertEqual(command[command.index("-map_metadata") + 1], "-1")
        self.assertEqual(command[command.index("-map_metadata:s") + 1], "-1")
        self.assertEqual(command[command.index("-map_chapters") + 1], "-1")
        self.assertLess(command.index("-map_metadata"), command.index("-c:v"))
        self.assertIn("[v]", command)
        self.assertIn("0:a?", command)
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-c:a") + 1], "aac")
        self.assertIn("+faststart", command)

    def test_telegram_circle_has_metadata_safe_export(self):
        with (
            patch.object(telegram, "duration", side_effect=[2.0, 8.0]),
            patch.object(telegram.random, "uniform", return_value=0.0),
            patch.object(telegram.subprocess, "run") as run,
        ):
            telegram.render(
                Path("source.mp4"), Path("bg.mp4"), Path("output.mp4"),
                telegram.RenderDesign(timecode_enabled=False),
            )
        self.assert_metadata_not_inherited(run.call_args.args[0])

    def test_instagram_video_has_metadata_safe_export(self):
        with (
            patch.object(instagram, "duration", return_value=2.0),
            patch.object(instagram, "cmd") as run,
        ):
            instagram.render(
                Path("source.mp4"), Path("output.mp4"), None,
                instagram.InstagramLayout(background_mode="blur"),
            )
        self.assert_metadata_not_inherited(run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
