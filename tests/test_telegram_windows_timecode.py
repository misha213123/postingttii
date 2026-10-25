"""Windows FFmpeg builds must not require a working Fontconfig installation."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from app import telegram_studio as tg


class WindowsTimecodeRecoveryTests(unittest.TestCase):
    def test_drawtext_uses_explicit_local_font_on_windows(self):
        with (
            patch.object(tg, "_is_windows", return_value=True),
            patch.object(tg, "_timecode_fontfile", return_value="_timecode_render_font.ttf"),
            patch.object(tg, "duration", side_effect=[2.0, 12.0]),
            patch.object(tg, "is_image_background", return_value=False),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg.subprocess, "run") as run,
        ):
            tg.render(Path("circle.mp4"), Path("bg.mp4"), Path("out.mp4"))
        argv = run.call_args.args[0]
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("drawtext=fontfile=_timecode_render_font.ttf:", graph)
        self.assertEqual(run.call_args.kwargs["cwd"], str(tg.ROOT))

    def test_fontconfig_crash_retries_once_with_border_and_progress(self):
        crash = subprocess.CalledProcessError(
            3221225477, ["ffmpeg"],
            stderr="Fontconfig error: Cannot load default config file: File not found",
        )
        with (
            patch.object(tg, "_is_windows", return_value=True),
            patch.object(tg, "_timecode_fontfile", return_value="_timecode_render_font.ttf"),
            patch.object(tg, "duration", side_effect=[2.0, 12.0, 2.0, 12.0]),
            patch.object(tg, "is_image_background", return_value=False),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg.subprocess, "run", side_effect=[crash, subprocess.CompletedProcess([], 0)]) as run,
            patch.object(tg, "JOB", {"notes": []}) as job,
        ):
            tg.render(Path("circle.mp4"), Path("bg.mp4"), Path("out.mp4"))
        self.assertEqual(run.call_count, 2)
        first, second = (
            call.args[0][call.args[0].index("-filter_complex") + 1]
            for call in run.call_args_list
        )
        self.assertIn("drawtext=", first)
        self.assertNotIn("drawtext=", second)
        self.assertIn("[ring][inside]overlay=", second)
        self.assertIn("[progress_fill]", second)
        self.assertTrue(job["notes"])

    def test_missing_windows_font_skips_drawtext_without_crashing(self):
        with (
            patch.object(tg, "_is_windows", return_value=True),
            patch.object(tg, "_timecode_fontfile", return_value=""),
            patch.object(tg, "duration", side_effect=[2.0, 12.0]),
            patch.object(tg, "is_image_background", return_value=False),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg.subprocess, "run") as run,
            patch.object(tg, "JOB", {"notes": []}) as job,
        ):
            tg.render(Path("circle.mp4"), Path("bg.mp4"), Path("out.mp4"))
        cmd = run.call_args.args[0]
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertNotIn("drawtext=", graph)
        self.assertIn("[progress_fill]", graph)
        self.assertTrue(job["notes"])

    def test_non_fontconfig_errors_remain_visible_and_not_retried(self):
        problem = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="No such filter")
        with (
            patch.object(tg, "_is_windows", return_value=True),
            patch.object(tg, "_timecode_fontfile", return_value="_timecode_render_font.ttf"),
            patch.object(tg, "duration", side_effect=[2.0, 12.0]),
            patch.object(tg, "is_image_background", return_value=False),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg.subprocess, "run", side_effect=problem) as run,
        ):
            with self.assertRaisesRegex(RuntimeError, "No such filter"):
                tg.render(Path("circle.mp4"), Path("bg.mp4"), Path("out.mp4"),
                          tg.RenderDesign(quality_preset="standard"))
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
