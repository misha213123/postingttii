"""Banner positioning outside the video canvas and faithful high-quality export."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app import telegram_studio as tg
from app import instagram_studio as ig


class BannerPositionTests(unittest.TestCase):
    def test_both_templates_accept_negative_and_off_canvas_coordinates(self):
        circle = tg.RenderDesign(banner_x=-720, banner_y=2140,
                                 banner_enabled=True)
        normal = ig.InstagramLayout(banner_x=-720, banner_y=2140,
                                    banner_enabled=True)
        self.assertEqual((circle.banner_x, circle.banner_y), (-720, 2140))
        self.assertEqual((normal.banner_x, normal.banner_y), (-720, 2140))
        for cls in (tg.RenderDesign, ig.InstagramLayout):
            with self.subTest(template=cls.__name__):
                with self.assertRaises(ValidationError):
                    cls(banner_x=-2161)
                with self.assertRaises(ValidationError):
                    cls(banner_y=3841)

    def test_negative_banner_position_flows_to_ffmpeg_overlay(self):
        design = tg.RenderDesign(banner_enabled=True,
                                 banner_x=-420, banner_y=-135,
                                 quality_preset="maximum")
        graph = tg.banner_overlay_filter(design, 2)
        self.assertIn("overlay=-420:-135", graph)
        self.assertIn("hqdn3d=", graph)
        self.assertIn("unsharp=", graph)

    def test_instagram_effective_banner_respects_separate_preset(self):
        layout = ig.InstagramLayout(quality_preset="maximum",
                                    banner_x=-700, banner_y=2010)
        with patch.object(ig, "saved_design",
                          return_value=tg.RenderDesign(quality_preset="standard")):
            effective = ig.video_banner_design(layout)
        self.assertEqual(effective.quality_preset, "maximum")
        self.assertEqual(effective.banner_x, -700)
        self.assertEqual(effective.banner_y, 2010)


class HighQualityGraphTests(unittest.TestCase):
    def test_standard_keeps_previous_encoder_profile(self):
        self.assertEqual(tg.quality_filter_segment("standard"), "")
        self.assertEqual(tg.h264_quality_args("standard"), [
            "-preset", "medium", "-crf", "18",
        ])
        self.assertEqual(tg.h264_quality_args("standard", standard_crf="19"), [
            "-preset", "medium", "-crf", "19",
        ])

    def test_maximum_preset_encodes_at_higher_fidelity(self):
        design = tg.RenderDesign(output_fps=30, timecode_enabled=False,
                                 quality_preset="maximum")
        with (
            patch.object(tg, "duration", side_effect=[2.0, 8.0]),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg.subprocess, "run") as run,
        ):
            tg.render(Path("circle.mp4"), Path("background.mp4"),
                      Path("reel.mp4"), design)
        argv = run.call_args.args[0]
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("hqdn3d=", graph)
        self.assertIn("eq=contrast=", graph)
        self.assertIn("unsharp=", graph)
        self.assertIn("-crf", argv)
        self.assertEqual(argv[argv.index("-crf") + 1], "16")
        self.assertEqual(argv[argv.index("-preset") + 1], "medium")

    def test_normal_instagram_render_uses_its_own_quality_preset(self):
        layout = ig.InstagramLayout(background_mode="none",
                                    quality_preset="maximum")
        with (
            patch.object(ig, "duration", return_value=2.0),
            patch.object(ig, "cmd") as run,
        ):
            ig.render(Path("source.mp4"), Path("out.mp4"), None, layout)
        argv = run.call_args.args[0]
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("hqdn3d=", graph)
        self.assertIn("unsharp=", graph)
        self.assertIn("format=yuv420p[v]", graph)
        self.assertEqual(argv[argv.index("-crf") + 1], "16")

    def test_unknown_quality_values_are_rejected(self):
        with self.assertRaises(ValidationError):
            tg.RenderDesign(quality_preset="boost_reach")
        with self.assertRaises(ValidationError):
            ig.InstagramLayout(quality_preset="boost_reach")


class EnhancementFallbackTests(unittest.TestCase):
    def test_telegram_missing_filter_retries_standard(self):
        failure = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="No such filter: hqdn3d",
        )
        design = tg.RenderDesign(timecode_enabled=False,
                                 quality_preset="enhanced")
        with (
            patch.object(tg, "duration", side_effect=[2.0, 8.0, 2.0, 8.0]),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg.subprocess, "run",
                         side_effect=[failure, subprocess.CompletedProcess([], 0)]) as run,
        ):
            tg.render(Path("circle.mp4"), Path("background.mp4"),
                      Path("reel.mp4"), design)
        self.assertEqual(run.call_count, 2)
        graph_first = run.call_args_list[0].args[0]
        graph_last = run.call_args_list[1].args[0]
        self.assertIn("hqdn3d=", graph_first[graph_first.index("-filter_complex") + 1])
        self.assertNotIn("hqdn3d=", graph_last[graph_last.index("-filter_complex") + 1])

    def test_instagram_missing_filter_retries_standard(self):
        layout = ig.InstagramLayout(background_mode="none",
                                    quality_preset="enhanced")
        with (
            patch.object(ig, "duration", return_value=2.0),
            patch.object(ig, "cmd",
                         side_effect=[RuntimeError("No such filter: hqdn3d"),
                                      None]) as run,
        ):
            ig.render(Path("video.mp4"), Path("out.mp4"), None, layout)
        self.assertEqual(run.call_count, 2)
        last = run.call_args_list[-1].args[0]
        self.assertEqual(last[last.index("-crf") + 1], "19")
        self.assertNotIn("hqdn3d=", last[last.index("-filter_complex") + 1])


class StudioBannerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (
            Path(__file__).resolve().parents[1] / "static" / "telegram-studio.html"
        ).read_text(encoding="utf-8")

    def test_ui_has_manual_coordinates_to_retrieve_off_screen_banner(self):
        for identifier in ("bannerPositionX", "bannerPositionY",
                           "igBannerPositionX", "igBannerPositionY"):
            self.assertIn(f'id="{identifier}"', self.html)
        self.assertIn("Math.max(-2160,Math.min(2160", self.html)
        self.assertIn("Math.max(-3840,Math.min(3840", self.html)

    def test_ui_has_independent_quality_presets(self):
        self.assertIn('id="tgQualityPreset"', self.html)
        self.assertIn('id="igQualityPreset"', self.html)
        self.assertIn("designState.quality_preset=e.target.value", self.html)
        self.assertIn("instagramLayout.quality_preset=e.target.value", self.html)


if __name__ == "__main__":
    unittest.main()
