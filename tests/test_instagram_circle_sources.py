"""Instagram Reels -> saved Telegram Studio circle templates (offline tests).

No Instagram network calls or actual FFmpeg render occur in these tests.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import instagram_studio as importer
from app import telegram_studio as circles


class InstagramCircleModeTests(unittest.IsolatedAsyncioTestCase):
    def test_existing_imports_remain_regular_video_by_default(self):
        settings = importer.Run()
        self.assertEqual(settings.render_mode, "video")
        self.assertEqual(settings.circle_template, "saved")

    def test_choose_both_circle_templates(self):
        base = circles.RenderDesign()
        first = base.model_copy(update={"visual_template": "classic"})
        second = base.model_copy(update={"visual_template": "orbit_dots"})
        self.assertEqual(first.visual_template, "classic")
        self.assertEqual(second.visual_template, "orbit_dots")
        self.assertNotEqual(first.visual_template, second.visual_template)

    async def test_same_instagram_reel_can_render_once_as_video_and_as_circle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "ready"
            output.mkdir()
            source_dir = root / "cache"
            source_dir.mkdir()
            account = "https://www.instagram.com/example/"
            link = "https://www.instagram.com/reel/ABCDEFGHIJK/"
            state = {"accounts": [account], "disabled": [],
                     "processed": [], "hashes": [], "links": []}
            cache = {"state": state}
            def load_state():
                import copy
                return copy.deepcopy(cache["state"])
            def save_state(data):
                import copy
                cache["state"] = copy.deepcopy(data)
            def fake_download(link, source):
                source.write_bytes(b"mock original video")
            def fake_render(source, target, background, layout, **kwargs):
                self.assertEqual(source.suffix, ".mp4")
                target.write_bytes(b"ordinary mp4")
            designs = circles.RenderDesign(
                visual_template="orbit_dots", background_mode="shuffle",
            )
            circle_calls = []
            def fake_circle(source, background, target, design, banner):
                circle_calls.append((source, background, design.visual_template, banner))
                target.write_bytes(b"circle mp4")
            lock = asyncio.Lock()
            with (
                patch.object(importer, "ROOT", source_dir),
                patch.object(importer, "CHROME_PROFILE", root / "no_chrome_profile"),
                patch.object(importer, "load", side_effect=load_state),
                patch.object(importer, "save", side_effect=save_state),
                patch.object(importer, "discover", return_value=[link]),
                patch.object(importer, "download_reel", side_effect=fake_download),
                patch.object(importer, "duration", return_value=12.0),
                patch.object(importer, "render", side_effect=fake_render),
                patch.object(importer, "render_circle", side_effect=fake_circle),
                patch.object(importer, "choose_backgrounds", return_value=[root / "background.mp4"]),
                patch.object(importer, "record_download"),
                patch.object(importer, "LOCK", lock),
            ):
                await lock.acquire()
                await importer.process(
                    1, importer.InstagramLayout(background_mode="none"),
                    output_dir=output, render_mode="video",
                )
                self.assertEqual(importer.JOB["status"], "done")
                self.assertEqual(importer.JOB["done"], 1)
                self.assertEqual(len(list(output.glob("ig_*.mp4"))), 1)

                await lock.acquire()
                await importer.process(
                    1, importer.InstagramLayout(background_mode="none"),
                    output_dir=output, render_mode="circles",
                    circle_design=designs, circle_banner=None,
                )
                self.assertEqual(importer.JOB["status"], "done")
                self.assertEqual(importer.JOB["done"], 1)
                self.assertEqual(importer.JOB["visual_template"], "orbit_dots")
                self.assertEqual(len(circle_calls), 1)
                self.assertEqual(circle_calls[0][2], "orbit_dots")
                self.assertEqual(len(list(output.glob("igc_*.mp4"))), 1)
                self.assertIn("last_output", cache["state"])
                self.assertIn("last_output_circles", cache["state"])
                self.assertIn("instagram_circles:folder:" + str(output.resolve()),
                              cache["state"]["processed_by_target"])


if __name__ == "__main__":
    unittest.main()
