"""Regression tests for Telegram Studio 9:16 design, banner, and chromakey."""
from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from PIL import Image
from pydantic import ValidationError

from app import telegram_studio as studio


class TelegramRenderDesignTests(unittest.TestCase):
    def test_circle_is_larger_by_default_and_chromakey_is_optional(self):
        design = studio.RenderDesign()
        self.assertEqual(design.circle_size, 1040)
        self.assertFalse(design.chromakey_enabled)
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
                with self.subTest(color=color), (
                    patch.object(studio, "duration", side_effect=[8.0, 20.0]),
                    patch.object(studio.subprocess, "run") as run,
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
                        "chromakey=0x" + color[1:] + ":0.310:0.130,",
                        graph,
                    )
                    circle, rest = graph.split("[circle];", 1)
                    self.assertNotIn("chromakey=", circle)
                    self.assertIn("[2:v]", rest)
                    self.assertIn("chromakey=", rest.split("[2:v]", 1)[1])
                    self.assertIn("0:a?", args)

    def test_without_banner_chromakey_never_changes_telegram_circle(self):
        with (
            patch.object(studio, "duration", side_effect=[8.0, 20.0]),
            patch.object(studio.subprocess, "run") as run,
        ):
            studio.render(
                Path("tg-circle.mp4"), Path("background.mp4"),
                Path("render.mp4"), studio.RenderDesign(
                    chromakey_enabled=True, chromakey_color="#237af0",
                ),
            )
        args = run.call_args.args[0]
        graph = args[args.index("-filter_complex") + 1]
        self.assertNotIn("chromakey=", graph)

    def test_default_green_still_applies_for_old_saved_designs(self):
        older = studio.RenderDesign.model_validate({"chromakey_enabled": True})
        self.assertEqual(older.chromakey_color, "#00ff00")
        self.assertEqual(older.chromakey_similarity, .20)
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
            patch.object(studio.subprocess, "run") as run,
        ):
            studio.render(Path("circle.mp4"), Path("background.mp4"), Path("output.mp4"))
        args = run.call_args.args[0]
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("scale=1040:1040", graph)
        self.assertIn("crop=1080:1920", graph)
        self.assertNotIn("chromakey=", graph)
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
                patch.object(studio.subprocess, "run") as run,
            ):
                studio.render(Path("circle.mp4"), Path("bg.mp4"), Path("out.mp4"), d, banner)
            args = run.call_args.args[0]
            graph = args[args.index("-filter_complex") + 1]
            self.assertIn("scale=1060:1060", graph)
            self.assertIn("chromakey=0x00ff00", graph)
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
                patch.object(studio.subprocess, "run") as run,
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
            self.assertIn("[2:v]scale=", graph)
            self.assertIn("chromakey=0x1a4cdd:0.200:0.070,", graph)
            self.assertNotIn("chromakey=", graph.split("[circle];")[0])
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
                    patch.object(studio.subprocess, "run") as run,
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
            patch.object(studio.subprocess, "run") as run,
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
                patch.object(studio.subprocess, "run", side_effect=CalledProcessError(1, "ffprobe")),
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


if __name__ == "__main__":
    unittest.main()
