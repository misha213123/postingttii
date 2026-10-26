"""Regression checks for quiet/fast Telegram Reels exports.

Quiet export changes the workload, never the Reel's geometry or decorations.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from app import telegram_studio as studio


class QuietExportTests(unittest.TestCase):
    def capture(self, *, speed="quiet", banner=None, **changes):
        settings = studio.RenderDesign(
            render_speed=speed, output_fps=60, smooth_motion=True,
            quality_preset="maximum", timecode_enabled=False,
            banner_enabled=bool(banner), **changes,
        )
        with (
            patch.object(studio, "duration", side_effect=[2.0, 8.0]),
            patch.object(studio, "is_image_background", return_value=False),
            patch.object(studio.random, "uniform", return_value=0.0),
            patch.object(studio, "orbit_sprite_path", return_value=Path("orbit.png")),
            patch.object(studio.subprocess, "run") as call,
            patch.object(Path, "is_file", return_value=True),
        ):
            studio.render(Path("note.mp4"), Path("background.mp4"),
                          Path("result.mp4"), settings, banner=banner)
        cmd = call.call_args.args[0]
        return cmd, cmd[cmd.index("-filter_complex") + 1]

    def test_quiet_is_default_and_capped_at_30_fps(self):
        self.assertEqual(studio.RenderDesign().render_speed, "quiet")
        perf = studio.render_performance(studio.RenderDesign())
        self.assertEqual(perf["fps"], 30)
        self.assertEqual(perf["filter_threads"], 2)
        self.assertFalse(perf["motion"])

    def test_quiet_uses_two_threads_and_fast_encoder(self):
        cmd, graph = self.capture()
        self.assertEqual(cmd[cmd.index("-filter_complex_threads") + 1], "2")
        self.assertEqual(cmd[cmd.index("-threads") + 1], "2")
        self.assertEqual(cmd[cmd.index("-preset") + 1], "veryfast")
        self.assertEqual(cmd[cmd.index("-crf") + 1], "19")
        self.assertEqual(cmd[cmd.index("-b:a") + 1], "128k")
        self.assertIn("fps=30", graph)
        self.assertNotIn("minterpolate", graph)
        self.assertNotIn("hqdn3d=", graph)
        self.assertNotIn("unsharp=", graph)
        self.assertIn("[ring][inside]overlay=", graph)
        self.assertIn("[progress_fill]", graph)

    def test_white_rotating_template_and_banner_remain_in_quiet_mode(self):
        cmd, graph = self.capture(
            speed="quiet", banner=Path("banner.png"),
            visual_template="orbit_dots",
        )
        self.assertIn("rotate=a='2*PI*t/6'", graph)
        self.assertIn("loading_fill", graph)
        self.assertIn("fade=t=in:st=0.38", graph)
        self.assertIn("[3:v]scale=", graph)
        self.assertIn("overlay=270:1550", graph)
        self.assertNotIn("drawtext", graph)
        self.assertIn("fps=30", graph)

    def test_balanced_limits_threads_but_respects_selected_fps(self):
        cmd, graph = self.capture(speed="balanced")
        self.assertEqual(cmd[cmd.index("-filter_complex_threads") + 1], "4")
        self.assertEqual(cmd[cmd.index("-threads") + 1], "4")
        self.assertIn("fps=60", graph)
        self.assertIn("hqdn3d=", graph)
        self.assertNotIn("minterpolate=", graph)

    def test_original_still_uses_optical_flow_and_full_quality(self):
        cmd, graph = self.capture(speed="original")
        self.assertNotIn("-filter_complex_threads", cmd)
        self.assertNotIn("-threads", cmd)
        self.assertIn("minterpolate=fps=60", graph)
        self.assertIn("hqdn3d=", graph)
        self.assertEqual(cmd[cmd.index("-crf") + 1], "16")

    def test_invalid_export_speed_is_rejected(self):
        with self.assertRaises(ValidationError):
            studio.RenderDesign(render_speed="superfast-boost-views")


class QuietModeUiTests(unittest.TestCase):
    def test_selector_and_cpu_notice_exist(self):
        js = (Path(__file__).resolve().parents[1] /
              "static/studio-circle-ui.js").read_text(encoding="utf-8")
        self.assertIn("Тихий и быстрый", js)
        self.assertIn("30 FPS", js)
        self.assertIn("render_speed", js)
        self.assertIn("fps.disabled=quiet;", js)
        self.assertIn("designState.render_speed=speed.value", js)


if __name__ == "__main__":
    unittest.main()
