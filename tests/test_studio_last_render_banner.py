"""Regression coverage: final rendered MP4 previews and real banner overlays."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app import instagram_studio as ig
from app import telegram_studio as tg


class BannerRenderingTests(unittest.TestCase):
    def test_instagram_video_banner_uses_ffmpeg_overlay_and_chromakey(self):
        with tempfile.TemporaryDirectory() as directory:
            banner = Path(directory) / "ad.png"
            banner.write_bytes(b"mock-banner")
            design = tg.RenderDesign(
                banner_enabled=True, chromakey_enabled=True,
                chromakey_color="#59be58", chromakey_similarity=.01,
                chromakey_blend=.03, banner_x=82, banner_y=1100,
                banner_width=750,
            )
            with (
                patch.object(ig, "duration", side_effect=[8.0, 18.0]),
                patch.object(ig, "cmd") as command,
            ):
                ig.render(Path("video.mp4"), Path("out.mp4"), Path("bg.mp4"),
                          ig.InstagramLayout(), banner=banner, banner_design=design)
            args = command.call_args.args[0]
            graph = args[args.index("-filter_complex") + 1]
            self.assertIn("[2:v]scale=750:-1", graph)
            self.assertIn("chromakey=0x59be58:0.010:0.030,", graph)
            self.assertIn("[composite][banner]overlay=82:1100", graph)
            self.assertNotIn("chromakey=", graph.split("[front];")[0])
            self.assertIn("0:a?", args)
            self.assertEqual(args[args.index("-loop") + 1], "1")

    def test_video_banner_loops_in_normal_video_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            banner = Path(directory) / "ad.mp4"
            banner.write_bytes(b"sample")
            design = tg.RenderDesign(banner_enabled=True)
            with (
                patch.object(ig, "duration", side_effect=[8.0, 20.0]),
                patch.object(ig, "cmd") as command,
            ):
                ig.render(Path("video.mp4"), Path("out.mp4"), Path("bg.mp4"),
                          ig.InstagramLayout(), banner=banner, banner_design=design)
            args = command.call_args.args[0]
            self.assertEqual(args.count("-stream_loop"), 2)
            self.assertIn("[2:v]scale=", args[args.index("-filter_complex") + 1])

    def test_blurred_normal_video_places_banner_at_input_one(self):
        with tempfile.TemporaryDirectory() as directory:
            banner = Path(directory) / "ad.png"
            banner.write_bytes(b"image")
            with (
                patch.object(ig, "duration", return_value=6.0),
                patch.object(ig, "cmd") as command,
            ):
                ig.render(
                    Path("video.mp4"), Path("out.mp4"), None,
                    ig.InstagramLayout(background_mode="blur"),
                    banner=banner, banner_design=tg.RenderDesign(banner_enabled=True),
                )
            args = command.call_args.args[0]
            self.assertIn("[1:v]scale=", args[args.index("-filter_complex") + 1])
            self.assertEqual(args.count("-i"), 2)

    def test_legacy_instagram_without_banner_remains_unchanged(self):
        with (
            patch.object(ig, "duration", side_effect=[8.0, 20.0]),
            patch.object(ig, "cmd") as command,
        ):
            ig.render(Path("video.mp4"), Path("out.mp4"), Path("bg.mp4"),
                      ig.InstagramLayout())
        args = command.call_args.args[0]
        graph = args[args.index("-filter_complex") + 1]
        self.assertNotIn("[banner]", graph)
        self.assertNotIn("chromakey=", graph)
        self.assertEqual(args.count("-i"), 2)


class LatestRenderedVideoTests(unittest.TestCase):
    def test_tg_circles_and_regular_videos_persist_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            circle = home / "tg_channel_12.mp4"
            regular = home / "tgvideo_channel_14.mp4"
            circle.write_bytes(b"rendered circle")
            regular.write_bytes(b"rendered normal video")
            data = {"last_outputs": {
                "circles": {"path": str(circle), "updated_at": 123, "banner_applied": True},
                "videos": {"path": str(regular), "updated_at": 456, "banner_applied": False},
            }}
            with (
                patch.object(tg, "config", return_value=data),
                patch.object(tg, "settings", SimpleNamespace(upload_dir=home, posted_dir=Path(directory) / 'posted')),
            ):
                self.assertEqual(tg.latest_render_path("circles")[0], circle)
                self.assertEqual(tg.latest_render_path("videos")[0], regular)
                preview = tg.last_render("circles")
                self.assertTrue(preview["available"])
                self.assertEqual(preview["banner_applied"], True)
                self.assertIn("last-render/poster?mode=circles", preview["poster_url"])
                response = tg.last_render_video("videos")
                self.assertIsInstance(response, FileResponse)
                self.assertEqual(Path(response.path), regular)

    def test_instagram_last_render_reloads_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ig_1234567890.mp4"
            output.write_bytes(b"rendered")
            with (
                patch.object(ig, "load", return_value={"last_output": {
                    "path": str(output), "updated_at": 234,
                    "banner_applied": True,
                }}),
                patch.object(tg, "settings", SimpleNamespace(upload_dir=Path(directory, posted_dir=Path(directory) / 'posted'))),
            ):
                data = tg.last_render("instagram")
            self.assertTrue(data["available"])
            self.assertTrue(data["banner_applied"])

    def test_untrusted_preview_filename_is_not_served(self):
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory) / "unrelated.mp4"
            other.write_bytes(b"not renderer output")
            with (
                patch.object(tg, "config", return_value={"last_outputs": {
                    "circles": {"path": str(other)},
                }}),
                patch.object(tg, "settings", SimpleNamespace(upload_dir=Path(directory, posted_dir=Path(directory) / 'posted'))),
            ):
                self.assertFalse(tg.last_render("circles")["available"])
                with self.assertRaises(HTTPException) as exc:
                    tg.last_render_video("circles")
                self.assertEqual(exc.exception.status_code, 404)

    def test_poster_created_from_actual_result_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "tg_demo_1.mp4"
            output.write_bytes(b"fake-mp4")
            thumbs = Path(directory) / "previews"
            thumbs.mkdir()
            def fake_ffmpeg(command, **kwargs):
                Path(command[-1]).write_bytes(b"jpeg")
            with (
                patch.object(tg, "config", return_value={
                    "last_outputs": {"circles": {"path": str(output)}},
                }),
                patch.object(tg, "settings", SimpleNamespace(upload_dir=Path(directory, posted_dir=Path(directory) / 'posted'))),
                patch.object(tg, "RENDER_PREVIEWS", thumbs),
                patch.object(tg.subprocess, "run", side_effect=fake_ffmpeg) as run,
            ):
                first = tg.last_render_poster("circles")
                second = tg.last_render_poster("circles")
            self.assertEqual(first.media_type, "image/jpeg")
            self.assertEqual(second.media_type, "image/jpeg")
            self.assertEqual(run.call_count, 1)
