"""Regression tests for Telegram Studio 9:16 design, banner, and chromakey."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageDraw
from pydantic import ValidationError

from app import telegram_studio as studio


class TelegramRingAndQualityTests(unittest.TestCase):
    def test_all_twelve_templates_validate_and_generate_ring_artwork(self):
        styles = ("classic", "orbit_dots", "orbit_arc", "pulse_ring",
                  "double_orbit", "dashed_spin", "ripple", "comet_tail",
                  "halo_breathe", "twin_comets", "starlight", "radar_sweep")
        for style in styles:
            with self.subTest(style=style):
                design = studio.RenderDesign(visual_template=style)
                self.assertEqual(design.visual_template, style)
                if style != "classic":
                    sprite = studio.ring_sprite_path(600, style)
                    self.assertTrue(sprite.is_file())
                    with Image.open(sprite) as img:
                        self.assertEqual(img.mode, "RGBA")
                        self.assertGreater(img.getbbox()[2], 0)

    def test_maximum_quality_is_not_downgraded_by_quiet_mode(self):
        design = studio.RenderDesign(
            quality_preset="maximum", render_speed="quiet", output_fps=30,
        )
        performance = studio.render_performance(design)
        self.assertEqual(performance["fps"], 30)
        self.assertFalse(performance["motion"])
        self.assertEqual(performance["quality"], "maximum")
        self.assertIn("17", performance["encoder"])

    def test_old_banner_chromakey_migrates_but_manual_off_is_preserved(self):
        old = {"banner_file": "old.png", "render_design": {
            "chromakey_enabled": False,
        }}
        self.assertTrue(studio.effective_design(old).chromakey_enabled)
        newer = {"banner_file": "old.png", "render_design": {
            "chromakey_enabled": False, "chromakey_version": 1,
        }}
        self.assertFalse(studio.effective_design(newer).chromakey_enabled)

    def test_adjustable_quality_filters_and_export_color_metadata(self):
        self.assertEqual(studio.quality_filter_segment("standard", 3, 1.5), "")
        self.assertNotIn("hqdn3d", studio.quality_filter_segment("maximum", 0, 0.4))
        self.assertNotIn("unsharp", studio.quality_filter_segment("maximum", 1, 0))
        with (patch.object(studio, "duration", side_effect=[8.0, 20.0]),
              patch.object(studio, "run_ffmpeg_render") as run):
            studio.render(Path("circle.mp4"), Path("background.mp4"),
                          Path("output.mp4"), studio.RenderDesign(
                              quality_preset="maximum", render_speed="original",
                              smooth_motion=True, denoise_strength=1.4,
                              sharpening_strength=0.7))
        args = run.call_args.args[0]
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("minterpolate=fps=60", graph)
        self.assertIn("hqdn3d=1.68", graph)
        self.assertIn("unsharp=5:5:0.70", graph)
        self.assertIn("-color_primaries", args)
        self.assertIn("bt709", args)
        self.assertIn("192k", args)

    def test_4k_export_is_opt_in_and_uses_2160x3840(self):
        self.assertEqual(studio.RenderDesign().export_resolution, 1080)
        with self.assertRaises(ValidationError):
            studio.RenderDesign(export_resolution=1440)
        with (patch.object(studio, "duration", side_effect=[8.0, 20.0]),
              patch.object(studio, "run_ffmpeg_render") as run):
            studio.render(Path("circle.mp4"), Path("background.mp4"),
                          Path("output.mp4"), studio.RenderDesign(
                              export_resolution=2160, quality_preset="maximum"))
        args = run.call_args.args[0]
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("scale=2160:3840:flags=lanczos", graph)
        self.assertEqual(args[args.index("-level:v")+1], "5.2")

    def test_chromakey_is_enabled_by_default(self):
        design = studio.RenderDesign()
        self.assertTrue(design.chromakey_enabled)


class TelegramUltraRenderSettingsTests(unittest.TestCase):
    def test_advanced_green_blue_and_neutral_chroma_preserve_banner_fps(self):
        for color, spill in (("#00ff00", "green"), ("#1474ef", "blue"),
                             ("#ffffff", None)):
            with self.subTest(color=color):
                design = studio.RenderDesign(
                    banner_enabled=True, chromakey_enabled=True,
                    chromakey_color=color, chromakey_similarity=.10,
                    chromakey_mode="advanced", quality_preset="maximum",
                )
                graph = studio.banner_overlay_filter(design, 2, 90)
                self.assertIn(f"colorkey=0x{color[1:]}:0.220", graph)
                self.assertIn("boxblur=lr=0:lp=0:cr=0:cp=0:ar=1:ap=1", graph)
                self.assertIn("[2:v]format=rgba,", graph)
                self.assertNotIn("[2:v]fps=", graph)
                self.assertNotIn("hqdn3d", graph)
                self.assertNotIn("unsharp", graph)
                self.assertIn("fps=90", graph)
                if spill:
                    self.assertIn(f"despill=type={spill}", graph)
                else:
                    self.assertNotIn("despill=", graph)

    def test_4k_banner_is_overlaid_at_full_export_resolution(self):
        design = studio.RenderDesign(
            banner_enabled=True, banner_width=540,
            banner_x=270, banner_y=1550, export_resolution=2160,
        )
        graph = studio.banner_overlay_filter(design, 2, 60)
        self.assertIn("[composite]scale=2160:3840:flags=lanczos[composite_hi]", graph)
        self.assertIn("scale=1080:-1:flags=lanczos", graph)
        self.assertIn("[composite_hi][banner]overlay=540:3100", graph)
        self.assertNotIn("fps=60,scale=2160", graph)

    def test_standard_key_is_still_available(self):
        d = studio.RenderDesign(chromakey_mode="standard",
                                chromakey_similarity=.10)
        graph = studio.banner_overlay_filter(d, 2)
        self.assertIn("colorkey=0x00ff00:0.100:0.070", graph)
        self.assertNotIn("despill=", graph)
        self.assertNotIn("boxblur=", graph)

    def test_maximum_matches_native_source_fps_without_downsampling(self):
        design = studio.RenderDesign(quality_preset="maximum",
                                     render_speed="original",
                                     output_fps=60, source_max_fps=True)
        with patch.object(studio, "video_frame_rate",
                          side_effect=[30.0, 24.0, 90.0]):
            fps, native = studio.render_frame_rate(
                design.model_copy(update={"banner_enabled": True}),
                studio.render_performance(design),
                Path("circle.mp4"), Path("bg.mp4"), Path("banner.mp4"),
            )
        self.assertEqual(fps, 90)
        self.assertEqual(native["banner"], 90)
        self.assertEqual(native["circle"], 30)

    def test_high_fps_banner_is_kept_even_in_quiet_mode(self):
        design = studio.RenderDesign(banner_enabled=True, quality_preset="standard",
                                     render_speed="quiet", source_max_fps=True)
        with patch.object(studio, "video_frame_rate",
                          side_effect=[30.0, 30.0, 120.0]):
            fps, _ = studio.render_frame_rate(
                design, studio.render_performance(design),
                Path("circle.mp4"), Path("bg.mp4"), Path("banner.mp4"),
            )
        self.assertEqual(fps, 120)

    def test_120fps_is_capped_and_4k_h264_level_is_sufficient(self):
        d = studio.RenderDesign(quality_preset="maximum",
                                render_speed="original",
                                export_resolution=2160, output_fps=120,
                                smooth_motion=False)
        with (patch.object(studio, "duration", side_effect=[1.0, 2.0]),
              patch.object(studio, "video_frame_rate", return_value=120.0),
              patch.object(studio, "run_ffmpeg_render") as run):
            studio.render(Path("circle.mp4"), Path("bg.mp4"),
                          Path("out.mp4"), d)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-level:v")+1], "6.0")
        graph = args[args.index("-filter_complex")+1]
        self.assertIn("fps=120", graph)
        self.assertNotIn("minterpolate", graph)

    def test_circle_cleanup_precedes_composite_and_never_blurs_banner(self):
        with tempfile.TemporaryDirectory() as folder:
            banner = Path(folder) / "ad.png"
            banner.write_bytes(b"placeholder")
            with (patch.object(studio, "duration", side_effect=[1.0, 2.0]),
                  patch.object(studio, "run_ffmpeg_render") as run):
                studio.render(Path("circle.mp4"), Path("bg.mp4"),
                              Path("out.mp4"), studio.RenderDesign(
                                  quality_preset="maximum", smooth_motion=False,
                                  banner_enabled=True), banner)
            args = run.call_args.args[0]
            graph = args[args.index("-filter_complex")+1]
            self.assertIn("hqdn3d", graph.split("[inside];")[0])
            self.assertNotIn("hqdn3d", graph.split("[composite];")[1])
            self.assertNotIn("unsharp", graph.split("[composite];")[1])

    def test_browser_exposes_one_click_maximum_and_auto_fps(self):
        js = (Path(__file__).resolve().parents[1] /
              "static/studio-circle-ui.js").read_text(encoding="utf-8")
        self.assertIn("studioUltraQuality", js)
        self.assertIn("source_max_fps", js)
        self.assertIn("[30,60,120]", js)


class TelegramProgressStyleTests(unittest.TestCase):
    def _graph(self, **kwargs):
        with (patch.object(studio, "duration", side_effect=[2.0, 10.0]),
              patch.object(studio, "run_ffmpeg_render") as run):
            studio.render(Path("circle.mp4"), Path("background.mp4"),
                          Path("out.mp4"), studio.RenderDesign(**kwargs))
        args = run.call_args.args[0]
        return args[args.index("-filter_complex") + 1]

    def test_progress_can_be_hidden_entirely_in_classic_and_animated_templates(self):
        for template in ("classic", "orbit_dots", "comet_tail"):
            for disabled in ({"progress_enabled": False},
                             {"progress_style": "none"}):
                with self.subTest(template=template, disabled=disabled):
                    graph = self._graph(visual_template=template,
                                        timecode_enabled=False, **disabled)
                    self.assertNotIn("[progress_track]", graph)
                    self.assertNotIn("[loading_track]", graph)
                    self.assertNotIn("[progress_fill]", graph)
                    self.assertNotIn("[loading_fill]", graph)

    def test_all_seven_visible_bar_styles_generate_real_ffmpeg_filters(self):
        styles = ("classic", "thin", "capsule", "segmented",
                  "glow", "dual", "pulse")
        for template in ("classic", "comet_tail"):
            for style in styles:
                with self.subTest(template=template, style=style):
                    graph = self._graph(visual_template=template,
                                        progress_style=style,
                                        timecode_enabled=False)
                    self.assertIn("[with_progress]", graph)
                    self.assertIn("min(T/2.000", graph)
                    if template != "classic":
                        self.assertIn("[loading_fill]", graph)
                    if style == "capsule":
                        self.assertIn("clip(X", graph)
                    if style == "segmented":
                        self.assertIn("ceil(min(T/", graph)
                    if style == "glow":
                        self.assertIn("0x6deeff", graph)
                    if style == "pulse":
                        self.assertIn("sin(T*6)", graph)

    def test_progress_style_validation_and_backward_compatibility(self):
        self.assertEqual(studio.RenderDesign().progress_style, "classic")
        self.assertTrue(studio.RenderDesign().progress_enabled)
        with self.assertRaises(ValidationError):
            studio.RenderDesign(progress_style="not-a-style")
        old = studio.RenderDesign.model_validate({"progress_enabled": False})
        self.assertFalse(old.progress_enabled)
        self.assertEqual(old.progress_style, "classic")

    def test_browser_off_option_and_new_circle_choices_exist(self):
        static = Path(__file__).resolve().parents[1] / "static"
        js = (static / "studio-circle-ui.js").read_text(encoding="utf-8")
        self.assertIn("studioProgressStyle", js)
        self.assertIn("Скрыть полностью", js)
        self.assertIn("progress.hidden=!state.progress_enabled", js)
        self.assertIn("Шаблон 12", js)


class TelegramRerenderSelectionTests(unittest.TestCase):
    def test_existing_output_is_skipped_by_default_but_not_when_forced(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "tg_channel_123.mp4"
            target.write_bytes(b"previous valid render")
            completed = ["channel:123"]

            self.assertTrue(studio.should_skip_existing_render(
                "channel:123", target, completed,
            ))
            self.assertFalse(studio.should_skip_existing_render(
                "channel:123", target, completed, force_rerender=True,
            ))

    def test_known_source_hash_is_bypassed_only_for_forced_rerender(self):
        hashes = {"same-source-sha256"}
        self.assertTrue(studio.should_skip_known_source(
            "same-source-sha256", hashes,
        ))
        self.assertFalse(studio.should_skip_known_source(
            "same-source-sha256", hashes, force_rerender=True,
        ))

    def test_render_request_force_rerender_defaults_off_and_can_be_enabled(self):
        self.assertFalse(studio.RenderRequest().force_rerender)
        self.assertTrue(studio.RenderRequest(force_rerender=True).force_rerender)


class TelegramRenderDesignTests(unittest.TestCase):
    def test_circle_is_larger_by_default_and_chromakey_is_optional(self):
        design = studio.RenderDesign()
        self.assertEqual(design.circle_size, 1040)
        self.assertTrue(design.chromakey_enabled)
        self.assertFalse(design.banner_enabled)
        with self.assertRaises(ValidationError):
            studio.RenderDesign(circle_size=1200)
        with self.assertRaises(ValidationError):
            studio.RenderDesign(banner_width=0)

    def test_chromakey_color_only_affects_ad_banner_not_circle(self):
        with tempfile.TemporaryDirectory() as folder:
            banner = Path(folder) / "promo.mp4"
            banner.write_bytes(b"mock banner")
            for color in ("#2078ca", "#ffffff", "#171717", "#FF00A4"):
                with (
                    self.subTest(color=color),
                    patch.object(studio, "duration", side_effect=[8.0, 20.0]),
                    patch.object(studio, "run_ffmpeg_render") as run,
                ):
                    design = studio.RenderDesign(
                        banner_enabled=True, chromakey_enabled=True,
                        chromakey_color=color,
                        chromakey_similarity=0.31, chromakey_blend=0.13,
                    )
                    studio.render(
                        Path("tg-circle.mp4"), Path("background.mp4"),
                        Path("render.mp4"), design, banner,
                    )
                    args = run.call_args.args[0]
                    graph = args[args.index("-filter_complex") + 1]
                    self.assertIn(
                        "colorkey=0x" + color[1:] + ":0.310:0.130,",
                        graph,
                    )
                    circle, rest = graph.split("[circle];", 1)
                    self.assertNotIn("colorkey=", circle)
                    self.assertIn("[2:v]", rest)
                    self.assertIn("colorkey=", rest.split("[2:v]", 1)[1])
                    self.assertIn("0:a?", args)

    def test_without_banner_chromakey_never_changes_telegram_circle(self):
        with (
            patch.object(studio, "duration", side_effect=[8.0, 20.0]),
            patch.object(studio, "run_ffmpeg_render") as run,
        ):
            studio.render(
                Path("tg-circle.mp4"), Path("background.mp4"),
                Path("render.mp4"), studio.RenderDesign(
                    chromakey_enabled=True, chromakey_color="#237af0",
                ),
            )
        args = run.call_args.args[0]
        graph = args[args.index("-filter_complex") + 1]
        self.assertNotIn("colorkey=", graph)

    def test_default_green_still_applies_for_old_saved_designs(self):
        older = studio.RenderDesign.model_validate({"chromakey_enabled": True})
        self.assertEqual(older.chromakey_color, "#00ff00")
        self.assertEqual(older.chromakey_similarity, .22)
        self.assertEqual(older.chromakey_blend, .07)

    def test_rejects_invalid_colors_and_ffmpeg_filter_injection(self):
        for invalid in ("green", "#ABC", "#AABBCC;hue=s=0", "AABBCC", "#12345g"):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                studio.RenderDesign(chromakey_color=invalid)
        with self.assertRaises(ValidationError):
            studio.RenderDesign(chromakey_similarity=1.3)
        with self.assertRaises(ValidationError):
            studio.RenderDesign(chromakey_blend=-.1)

    def test_render_without_banner_preserves_background_and_audio(self):
        with (
            patch.object(studio, "duration", side_effect=[8.0, 20.0]),
            patch.object(studio.random, "uniform", return_value=2.0),
            patch.object(studio, "run_ffmpeg_render") as run,
        ):
            studio.render(Path("circle.mp4"), Path("background.mp4"), Path("output.mp4"))
        args = run.call_args.args[0]
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("scale=1040:1040", graph)
        self.assertIn("crop=1080:1920", graph)
        self.assertNotIn("colorkey=", graph)
        self.assertNotIn("[banner]", graph)
        self.assertIn("0:a?", args)
        self.assertEqual(args.count("-i"), 2)

    def test_chromakey_banner_overlay_uses_saved_coordinates(self):
        with tempfile.TemporaryDirectory() as folder:
            banner = Path(folder) / "banner.png"
            banner.write_bytes(b"placeholder")
            d = studio.RenderDesign(
                banner_enabled=True, banner_x=125, banner_y=1570,
                banner_width=620, chromakey_enabled=True, circle_size=1060,
            )
            with (
                patch.object(studio, "duration", side_effect=[8.0, 20.0]),
                patch.object(studio.random, "uniform", return_value=0),
                patch.object(studio, "run_ffmpeg_render") as run,
            ):
                studio.render(Path("circle.mp4"), Path("bg.mp4"), Path("out.mp4"), d, banner)
            args = run.call_args.args[0]
            graph = args[args.index("-filter_complex") + 1]
            self.assertIn("scale=1060:1060", graph)
            self.assertIn("colorkey=0x00ff00", graph)
            self.assertIn("scale=620:-1", graph)
            self.assertIn("overlay=125:1570", graph)
            self.assertEqual(args.count("-i"), 3)
            self.assertEqual(args[args.index("-loop")+1], "1")
            self.assertIn("0:a?", args)

    def test_static_png_banner_is_keyed_without_changing_circle(self):
        with tempfile.TemporaryDirectory() as folder:
            banner = Path(folder) / "promo.png"
            banner.write_bytes(b"placeholder")
            with (
                patch.object(studio, "duration", side_effect=[8.0, 20.0]),
                patch.object(studio, "run_ffmpeg_render") as run,
            ):
                studio.render(
                    Path("circle.mp4"), Path("bg.mp4"), Path("output.mp4"),
                    studio.RenderDesign(
                        banner_enabled=True, chromakey_enabled=True,
                        chromakey_color="#1a4cdd",
                    ), banner,
                )
            args = run.call_args.args[0]
            graph = args[args.index("-filter_complex") + 1]
            self.assertIn("[2:v]format=rgba,", graph)
            self.assertIn("colorkey=0x1a4cdd:0.220:0.070,", graph)
            self.assertNotIn("colorkey=", graph.split("[circle];")[0])
            self.assertIn("-loop", args)

    def test_video_banner_repeats_frames_but_keeps_only_telegram_audio(self):
        for extension in (".mp4", ".webm"):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as folder:
                banner = Path(folder) / ("ad" + extension)
                banner.write_bytes(b"mock-video")
                d = studio.RenderDesign(
                    banner_enabled=True, banner_x=120,
                    banner_y=1460, banner_width=610,
                )
                with (
                    patch.object(studio, "duration", side_effect=[8.0, 20.0]),
                    patch.object(studio, "run_ffmpeg_render") as run,
                ):
                    studio.render(Path("circle.mp4"), Path("bg.mp4"), Path("reel.mp4"), d, banner)
                args = run.call_args.args[0]
                graph = args[args.index("-filter_complex") + 1]
                self.assertEqual(args.count("-i"), 3)
                self.assertEqual(args.count("-stream_loop"), 2)  # background + video banner
                self.assertNotIn("-loop", args)
                self.assertIn("overlay=120:1460", graph)
                self.assertEqual(args[args.index("-map") + 1], "[v]")
                self.assertEqual(args[args.index("-map", args.index("-map")+1) + 1], "0:a?")
                self.assertIn(str(banner), args)

    def test_missing_enabled_banner_fails_before_running_ffmpeg(self):
        with (
            patch.object(studio, "duration", side_effect=[5.0, 12.0]),
            patch.object(studio, "run_ffmpeg_render") as run,
        ):
            with self.assertRaisesRegex(ValueError, "баннер"):
                studio.render(
                    Path("circle.mp4"), Path("bg.mp4"), Path("out.mp4"),
                    studio.RenderDesign(banner_enabled=True),
                )
            run.assert_not_called()


class TelegramDesignApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_banner_upload_and_save_keeps_telegram_channels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            banner_folder = root / "banners"
            banner_folder.mkdir()
            conf = root / "config.json"
            conf.write_text(
                '{"channels":["mychannel"],"processed":[],"backgrounds_disabled":[]}',
                encoding="utf-8",
            )
            image = Image.new("RGBA", (800, 180), (255, 255, 255, 200))
            content = BytesIO()
            image.save(content, format="PNG")
            content.seek(0)
            with (
                patch.object(studio, "CONFIG", conf),
                patch.object(studio, "BANNERS", banner_folder),
                patch.dict(studio.JOB, {"status": "idle"}),
            ):
                status = await studio.upload_banner(
                    UploadFile(filename="promo.png", file=content)
                )
                self.assertTrue(status["banner_available"])
                self.assertTrue(studio.current_banner().exists())
                design = studio.RenderDesign(
                    circle_size=1060, banner_enabled=True,
                    banner_width=620, banner_x=100, banner_y=1400,
                )
                saved = studio.update_design(design)
                self.assertTrue(saved["design"]["banner_enabled"])
                self.assertEqual(saved["design"]["banner_x"], 100)
                self.assertEqual(studio.config()["channels"], ["mychannel"])
                self.assertIsNotNone(studio.banner_preview())
                removed = studio.delete_banner()
                self.assertFalse(removed["banner_available"])
                self.assertFalse(removed["design"]["banner_enabled"])
                self.assertEqual(studio.config()["channels"], ["mychannel"])

    async def test_video_upload_keeps_original_file_and_detects_kind(self):
        import json
        from types import SimpleNamespace
        for ext in (".mp4", ".webm"):
            with self.subTest(ext=ext), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                banner_folder = root / "banners"
                banner_folder.mkdir()
                conf = root / "config.json"
                conf.write_text('{"channels":["keep"]}', encoding="utf-8")
                probe_result = SimpleNamespace(stdout=json.dumps({
                    "streams":[{"codec_type":"video","width":720,"height":200}],
                    "format":{"duration":"5.0"}
                }))
                with (
                    patch.object(studio, "CONFIG", conf),
                    patch.object(studio, "BANNERS", banner_folder),
                    patch.object(studio.subprocess, "run", return_value=probe_result) as probe,
                    patch.dict(studio.JOB, {"status": "idle"}),
                ):
                    uploaded = await studio.upload_banner(
                        UploadFile(filename="ad"+ext, file=BytesIO(b"video-bytes"))
                    )
                    self.assertTrue(uploaded["banner_available"])
                    self.assertEqual(uploaded["banner_kind"], "video")
                    self.assertEqual(studio.current_banner().suffix, ext)
                    self.assertEqual(studio.current_banner().read_bytes(), b"video-bytes")
                    self.assertEqual(studio.config()["channels"], ["keep"])
                    preview = studio.banner_preview()
                    self.assertEqual(preview.media_type, "video/mp4" if ext==".mp4" else "video/webm")
                    self.assertIn("ffprobe", probe.call_args.args[0])
                    removed = studio.delete_banner()
                    self.assertIsNone(removed["banner_kind"])

    async def test_rejects_invalid_video_without_activating_it(self):
        from subprocess import CalledProcessError
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            banner_folder=root/"banners"
            banner_folder.mkdir()
            conf=root/"config.json"
            with (
                patch.object(studio, "CONFIG", conf),
                patch.object(studio, "BANNERS", banner_folder),
                patch.object(studio, "run_ffmpeg_render", side_effect=CalledProcessError(1, "ffprobe")),
                patch.dict(studio.JOB, {"status": "idle"}),
            ):
                with self.assertRaises(HTTPException) as err:
                    await studio.upload_banner(
                        UploadFile(filename="broken.mp4",file=BytesIO(b"not-real-mp4"))
                    )
                self.assertEqual(err.exception.status_code, 400)
                self.assertFalse(list(banner_folder.iterdir()))
                self.assertFalse(conf.exists())

    async def test_bad_file_rejected_without_mutating_studio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            root.joinpath("banners").mkdir()
            with (
                patch.object(studio, "CONFIG", root / "config.json"),
                patch.object(studio, "BANNERS", root / "banners"),
                patch.dict(studio.JOB, {"status": "idle"}),
            ):
                with self.assertRaises(HTTPException) as e:
                    await studio.upload_banner(
                        UploadFile(filename="unsafe.svg", file=BytesIO(b"<svg/>"))
                    )
                self.assertEqual(e.exception.status_code, 400)
                self.assertIsNone(studio.current_banner())

    async def test_design_is_locked_while_rendering(self):
        with patch.dict(studio.JOB, {"status": "running"}):
            with self.assertRaises(HTTPException) as e:
                studio.update_design(studio.RenderDesign(banner_enabled=True))
            self.assertEqual(e.exception.status_code, 409)


class TelegramAnimatedRingRenderTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg and FFprobe required")
    def test_rotating_arc_and_pulse_ring_render_to_mp4(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.mp4"
            background = root / "background.png"
            Image.new("RGB", (1080, 1920), (32, 80, 160)).save(background)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x320:r=30:d=0.5",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(source),
            ], check=True, capture_output=True)
            for style in ("orbit_arc", "pulse_ring", "comet_tail", "halo_breathe"):
                with self.subTest(style=style):
                    output = root / f"{style}.mp4"
                    design = studio.RenderDesign(
                        visual_template=style, circle_size=400,
                        circle_x=300, circle_y=600,
                        progress_enabled=False, timecode_enabled=False,
                        render_speed="quiet", quality_preset="standard",
                    )
                    studio.render(source, background, output, design)
                    self.assertTrue(output.is_file())
                    self.assertGreater(output.stat().st_size, 1000)


class TelegramProgressStyleRenderIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg and FFprobe required")
    def test_capsule_progress_and_hidden_progress_both_render_to_mp4(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.mp4"
            background = root / "background.png"
            Image.new("RGB", (1080, 1920), (32, 80, 160)).save(background)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x320:r=30:d=0.6",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(source),
            ], check=True, capture_output=True)
            for style in ("capsule", "none"):
                with self.subTest(style=style):
                    target = root / f"{style}.mp4"
                    studio.render(source, background, target,
                                  studio.RenderDesign(
                                      visual_template="comet_tail",
                                      circle_size=400, circle_x=300, circle_y=600,
                                      progress_style=style, progress_x=350,
                                      progress_y=1200, progress_width=250,
                                      progress_height=12, timecode_enabled=False,
                                      render_speed="quiet", quality_preset="standard"))
                    self.assertTrue(target.is_file())
                    self.assertGreater(target.stat().st_size, 1000)


class TelegramNativeBannerFpsRenderIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg and FFprobe required")
    def test_video_banner_90fps_is_preserved_in_90fps_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, banner = root / "circle.mp4", root / "banner.mp4"
            bg, output = root / "bg.png", root / "out.mp4"
            Image.new("RGB", (1080, 1920), (30, 80, 150)).save(bg)
            for target, rate, color in ((source, 30, "black"),
                                        (banner, 90, "green")):
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i",
                    f"color=c={color}:s=160x160:r={rate}:d=0.4",
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(target),
                ], check=True, capture_output=True)
            design = studio.RenderDesign(
                banner_enabled=True, banner_width=220,
                banner_x=30, banner_y=1400,
                chromakey_enabled=True, chromakey_mode="advanced",
                quality_preset="standard", render_speed="quiet",
                smooth_motion=False, source_max_fps=True,
                timecode_enabled=False,
            )
            studio.render(source, bg, output, design, banner)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 1000)
            rate = studio.video_frame_rate(output)
            self.assertIsNotNone(rate)
            self.assertAlmostEqual(rate, 90, delta=.1)


class TelegramTemplateSaveGuardTests(unittest.TestCase):
    def test_failed_preset_save_blocks_render_instead_of_using_stale_template(self):
        html = (Path(__file__).resolve().parents[1] /
                "static/telegram-studio.html").read_text(encoding="utf-8")
        self.assertIn("designSaveError=err", html)
        self.assertIn("if(designSaveError)throw Error('Шаблон не сохранён:", html)
        self.assertIn("await designSaveQueue;", html)


class TelegramTemplateRenderFallbackTests(unittest.TestCase):
    def test_optical_flow_failure_retries_same_template_with_banner_and_fps(self):
        with tempfile.TemporaryDirectory() as folder:
            banner = Path(folder) / "ad.png"
            banner.write_bytes(b"placeholder")
            error = subprocess.CalledProcessError(
                1, ["ffmpeg"], stderr="Failed to configure output pad on auto_scale"
            )
            with (patch.object(studio, "duration", return_value=1.0),
                  patch.object(studio, "video_frame_rate", return_value=30.0),
                  patch.object(studio, "ring_sprite_path",
                               return_value=Path("orbit.png")),
                  patch.object(studio, "run_ffmpeg_render",
                               side_effect=[error, subprocess.CompletedProcess(
                                   ["ffmpeg"], 0)]) as run,
                  patch.dict(studio.JOB, {"notes": []})):
                studio.render(
                    Path("circle.mp4"), Path("background.png"),
                    Path(folder) / "output.mp4",
                    studio.RenderDesign(
                        visual_template="orbit_dots", quality_preset="maximum",
                        render_speed="original",
                        smooth_motion=True, banner_enabled=True,
                        timecode_enabled=False,
                    ), banner,
                )
                self.assertEqual(run.call_count, 2)
                first, second = [c.args[0] for c in run.call_args_list]
                first_graph = first[first.index("-filter_complex") + 1]
                second_graph = second[second.index("-filter_complex") + 1]
                self.assertIn("minterpolate=", first_graph)
                self.assertNotIn("minterpolate=", second_graph)
                self.assertIn("[orbit_ring]", second_graph)
                self.assertIn("[banner]", second_graph)
                self.assertIn("colorkey=", second_graph)
                self.assertIn("fps=60", second_graph)
                self.assertTrue(studio.JOB["notes"])

    def test_advanced_key_failure_keeps_template_and_uses_standard_key(self):
        with tempfile.TemporaryDirectory() as folder:
            banner = Path(folder) / "ad.png"
            banner.write_bytes(b"placeholder")
            error = subprocess.CalledProcessError(
                1, ["ffmpeg"], stderr="No such filter: despill"
            )
            with (patch.object(studio, "duration", return_value=1.0),
                  patch.object(studio, "video_frame_rate", return_value=30.0),
                  patch.object(studio, "ring_sprite_path",
                               return_value=Path("orbit.png")),
                  patch.object(studio, "run_ffmpeg_render",
                               side_effect=[error, subprocess.CompletedProcess(
                                   ["ffmpeg"], 0)]) as run,
                  patch.dict(studio.JOB, {"notes": []})):
                studio.render(
                    Path("circle.mp4"), Path("background.png"),
                    Path(folder) / "output.mp4",
                    studio.RenderDesign(
                        visual_template="comet_tail", quality_preset="standard",
                        smooth_motion=False, banner_enabled=True,
                        timecode_enabled=False,
                    ), banner,
                )
                self.assertEqual(run.call_count, 2)
                second = run.call_args.args[0]
                graph = second[second.index("-filter_complex") + 1]
                self.assertIn("[orbit_ring]", graph)
                self.assertIn("[banner]", graph)
                self.assertIn("colorkey=", graph)
                self.assertNotIn("despill=", graph)

    def test_interpolation_is_after_scaling_not_on_large_original_frames(self):
        with (patch.object(studio, "duration", return_value=1.0),
              patch.object(studio, "video_frame_rate", return_value=30.0),
              patch.object(studio, "run_ffmpeg_render") as run):
            studio.render(
                Path("circle.mp4"), Path("background.mp4"),
                Path("output.mp4"), studio.RenderDesign(
                    quality_preset="maximum", render_speed="original",
                    smooth_motion=True, timecode_enabled=False,
                ),
            )
        args = run.call_args.args[0]
        graph = args[args.index("-filter_complex") + 1]
        circle = graph.split("[inside];")[0]
        background = graph.split("[1:v]")[1].split("[bg];")[0]
        self.assertLess(circle.index("crop="), circle.index("minterpolate="))
        self.assertIn("fps=", background)
        self.assertNotIn("minterpolate=", background)


class TelegramAllTemplatesFFmpegMatrixTests(unittest.TestCase):
    """Decode real FFmpeg filter graphs for all twelve templates, not mocks."""

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_all_twelve_templates_render_real_frames(self):
        styles = ("classic", "orbit_dots", "orbit_arc", "pulse_ring",
                  "double_orbit", "dashed_spin", "ripple", "comet_tail",
                  "halo_breathe", "twin_comets", "starlight", "radar_sweep")
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.mp4"
            background = root / "background.png"
            Image.new("RGB", (540, 960), (26, 54, 100)).save(background)
            banner = root / "banner.png"
            ad = Image.new("RGB", (100, 50), (10, 240, 10))
            ImageDraw.Draw(ad).rectangle((40, 10, 65, 40), fill=(230, 20, 20))
            ad.save(banner)
            real_run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=s=160x160:r=30:d=1",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
            ], check=True, capture_output=True)

            def verify_graph(command, **kwargs):
                # Run actual FFmpeg on the full 1080x1920 filtergraph, but
                # stop after a few frames to keep CI fast.
                i = command.index("-filter_complex")
                check_cmd = command[:i] + [
                    "-filter_complex", command[i + 1],
                    "-map", "[v]", "-frames:v", "4",
                    "-an", "-f", "null", "-",
                ]
                return real_run(check_cmd, **kwargs)

            progress_styles = ("classic", "thin", "capsule", "segmented",
                               "glow", "dual", "pulse")
            for index, style in enumerate(styles):
                with self.subTest(style=style):
                    use_banner = index % 2 == 0
                    design = studio.RenderDesign(
                        visual_template=style, circle_size=400,
                        circle_x=100, circle_y=300,
                        timecode_enabled=False, progress_enabled=True,
                        progress_style=progress_styles[index % len(progress_styles)],
                        banner_enabled=use_banner, banner_width=220,
                        quality_preset="standard",
                        render_speed="quiet", source_max_fps=False,
                        smooth_motion=False,
                    )
                    with (patch.object(studio, "duration", return_value=1.0),
                          patch.object(studio, "run_ffmpeg_render",
                                       side_effect=verify_graph)):
                        studio.render(source, background, root / "output.mp4",
                                      design, banner if use_banner else None)

            # Regression for the exact "Maximum" setting: animated ring,
            # optical-flow circle, advanced key, and progress together.
            maximum = studio.RenderDesign(
                visual_template="pulse_ring", circle_size=400,
                circle_x=100, circle_y=300, progress_style="capsule",
                timecode_enabled=False, banner_enabled=True,
                banner_width=220, quality_preset="maximum",
                render_speed="original", smooth_motion=True,
                source_max_fps=False,
            )
            with (patch.object(studio, "duration", return_value=1.0),
                  patch.object(studio, "video_frame_rate", return_value=30.0),
                  patch.object(studio, "run_ffmpeg_render",
                               side_effect=verify_graph)):
                studio.render(source, background, root / "output.mp4",
                              maximum, banner)


class TelegramChromaKeyRenderIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "FFmpeg and FFprobe are required for the real render regression test",
    )
    def test_rgb_key_removes_green_banner_in_real_mp4_and_keeps_logo(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.mp4"
            background = root / "background.png"
            banner = root / "banner.png"
            output = root / "rendered.mp4"
            frame = root / "rendered-frame.png"

            Image.new("RGB", (1080, 1920), (32, 80, 160)).save(background)
            banner_image = Image.new("RGB", (100, 50), (20, 242, 12))
            ImageDraw.Draw(banner_image).rectangle(
                (40, 15, 60, 35), fill=(225, 32, 38)
            )
            banner_image.save(banner)

            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=320x320:r=30:d=1",
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(source),
                ],
                check=True, capture_output=True,
            )

            design = studio.RenderDesign(
                circle_size=400, circle_x=650, circle_y=1400,
                progress_enabled=False, timecode_enabled=False,
                render_speed="quiet",
                banner_enabled=True, banner_x=0, banner_y=0, banner_width=200,
                chromakey_enabled=True, chromakey_color="#00ff00",
                chromakey_similarity=0.20, chromakey_blend=0.07,
            )
            studio.render(source, background, output, design, banner)

            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", "0.7", "-i", str(output),
                    "-frames:v", "1", str(frame),
                ],
                check=True, capture_output=True,
            )
            with Image.open(frame) as rendered:
                rendered = rendered.convert("RGB")
                keyed_background_pixel = rendered.getpixel((20, 20))
                preserved_logo_pixel = rendered.getpixel((100, 50))

            background_error = sum(
                abs(actual - expected)
                for actual, expected in zip(keyed_background_pixel, (32, 80, 160))
            )
            logo_error = sum(
                abs(actual - expected)
                for actual, expected in zip(preserved_logo_pixel, (225, 32, 38))
            )
            self.assertLess(
                background_error, 100,
                f"Green banner background remained: {keyed_background_pixel}",
            )
            self.assertLess(
                logo_error, 100,
                f"Non-key logo was damaged: {preserved_logo_pixel}",
            )


if __name__ == "__main__":
    unittest.main()
