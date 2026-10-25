"""Rendering failures should show FFmpeg diagnostics, not truncated argv."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from app import telegram_studio as tg


class RenderDiagnosticsTests(unittest.TestCase):
    def test_stderr_is_visible_without_echoing_full_ffmpeg_command(self):
        simulated_error = subprocess.CalledProcessError(
            1, ["ffmpeg", "-filter_complex", "<complex graph>"],
            stderr=(
                "[Parsed_drawtext_15 @ 0x123] Fontconfig error: Cannot load default font\n"
                "Error initializing complex filters.\n"
                "Invalid argument\n"
            ),
        )
        with (
            patch.object(tg, "duration", side_effect=[1.6, 4.0]),
            patch.object(tg, "is_image_background", return_value=False),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg.subprocess, "run", side_effect=simulated_error),
        ):
            with self.assertRaises(RuntimeError) as err:
                tg.render(Path("source.mp4"), Path("background.mp4"),
                          Path("output.mp4"), tg.RenderDesign())
        message = str(err.exception)
        self.assertIn("Cannot load default font", message)
        self.assertIn("Invalid argument", message)
        self.assertNotIn("<complex graph>", message)
        self.assertIn("код 1", message)

    def test_long_build_banner_cannot_hide_final_error(self):
        failure = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr=(
                "ffmpeg version 8.0.1 Copyright (c) 2000-2026\n"
                "built with gcc 16.1.0 (Rev2, Built by MSYS2 project)\n"
                "configuration: " + "--enable-gpl " * 800 + "\n"
                "[AVFilterGraph] No such filter: 'required_filter'\n"
                "Error opening output files: Filter not found\n"
            ),
        )
        with (
            patch.object(tg, "duration", side_effect=[1.6, 4.0]),
            patch.object(tg, "is_image_background", return_value=False),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg.subprocess, "run", side_effect=failure) as call,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                tg.render(Path("circle.mp4"), Path("bg.mp4"),
                          Path("output.mp4"), tg.RenderDesign())
        message = str(ctx.exception)
        self.assertIn("No such filter", message)
        self.assertIn("Filter not found", message)
        self.assertNotIn("configuration:", message)
        self.assertIn("-hide_banner", call.call_args.args[0])
        self.assertIn("-loglevel", call.call_args.args[0])

    def test_fallback_message_if_ffmpeg_omits_stderr(self):
        simulated_error = subprocess.CalledProcessError(9, ["ffmpeg"], stderr="")
        with (
            patch.object(tg, "duration", side_effect=[1.6, 4.0]),
            patch.object(tg, "is_image_background", return_value=False),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg.subprocess, "run", side_effect=simulated_error),
        ):
            with self.assertRaisesRegex(RuntimeError, "код выхода 9 без stderr"):
                tg.render(Path("source.mp4"), Path("background.mp4"),
                          Path("output.mp4"), tg.RenderDesign())


if __name__ == "__main__":
    unittest.main()
