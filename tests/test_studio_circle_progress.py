"""Regressions for styled Telegram circles and time-synchronised progress UI."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
from app import telegram_studio as studio


def graph_for(design: studio.RenderDesign) -> tuple[str, list[str]]:
    with (
        patch.object(studio, "duration", side_effect=[1.6, 10.0]),
        patch.object(studio, "is_image_background", return_value=False),
        patch.object(studio.random, "uniform", return_value=0.0),
        patch.object(studio.subprocess, "run") as call,
    ):
        studio.render(Path("circle.mp4"), Path("background.mp4"), Path("reel.mp4"), design)
    args = call.call_args.args[0]
    return args[args.index("-filter_complex") + 1], args


class StyledCircleTests(unittest.TestCase):
    def test_default_circle_frame_progress_and_timestamp(self):
        design = studio.RenderDesign()
        self.assertEqual(design.output_fps, 60)
        self.assertTrue(design.progress_enabled)
        self.assertTrue(design.timecode_enabled)
        graph, args = graph_for(design)
        self.assertIn("scale=1040:1040", graph)
        self.assertIn("color=c=0xffffff", graph)
        self.assertIn("[ring][inside]overlay=9:9", graph)
        self.assertIn("overlay=20:90", graph)
        self.assertIn("[progress_fill]", graph)
        self.assertIn("min(T/1.600", graph)
        self.assertIn("drawtext=text=", graph)
        self.assertIn("[composite]fps=60", graph)
        self.assertEqual(args[args.index("-map") + 1], "[v]")
        self.assertIn("0:a?", args)

    def test_reels_geometry_is_clamped_and_optional_overlays_disable(self):
        design = studio.RenderDesign(
            circle_size=1080, circle_x=1080, circle_y=1920,
            circle_border_width=0, progress_enabled=False,
            timecode_enabled=False, output_fps=30,
        )
        graph, _ = graph_for(design)
        self.assertIn("overlay=0:840", graph)
        self.assertNotIn("[progress_fill]", graph)
        self.assertNotIn("drawtext=text=", graph)
        self.assertIn("[composite]fps=30", graph)

    def test_progress_geometry_is_clamped_inside_1080x1920(self):
        design = studio.RenderDesign(progress_x=1050, progress_y=1900,
                                     progress_width=450, progress_height=22)
        graph, _ = graph_for(design)
        self.assertIn("drawbox=x=630:y=1900:w=450:h=22", graph)
        self.assertIn("overlay=630:1900", graph)

    def test_optical_flow_is_only_used_when_enabled_at_60_fps(self):
        graph, _ = graph_for(studio.RenderDesign(smooth_motion=True))
        self.assertIn("minterpolate=fps=60", graph)
        graph_30, _ = graph_for(studio.RenderDesign(output_fps=30, smooth_motion=True))
        self.assertNotIn("minterpolate=", graph_30)

    def test_invalid_visual_options_are_rejected(self):
        for update in (
            {"circle_border_color": "x;movie=evil"},
            {"progress_width": 0},
            {"timecode_font_size": 1000},
            {"output_fps": 120},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                studio.RenderDesign(**update)

    def test_banner_keeps_its_own_chromakey_and_fps(self):
        design = studio.RenderDesign(banner_enabled=True, output_fps=60)
        with patch.object(studio, "duration", side_effect=[6.0, 12.0]), \
             patch.object(studio, "is_image_background", return_value=False), \
             patch.object(studio.random, "uniform", return_value=0.0), \
             patch.object(studio.subprocess, "run") as call, \
             patch.object(Path, "is_file", return_value=True):
            studio.render(Path("circle.mp4"), Path("background.mp4"),
                          Path("render.mp4"), design, Path("banner.png"))
        args = call.call_args.args[0]
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("[2:v]scale=540:-1", graph)
        self.assertIn("fade=t=in:st=0:d=0.30:alpha=1", graph)
        self.assertIn("fps=60", graph)


if __name__ == "__main__":
    unittest.main()
