"""Second Reel template: white moving rim, normal loading bar, no timestamp.

The first Telegram circle template remains the default and is unchanged.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app import telegram_studio as tg


class CircleStyleTests(unittest.TestCase):
    def test_existing_classic_template_is_default(self):
        self.assertEqual(tg.RenderDesign().visual_template, "classic")

    def test_second_template_is_explicit_and_validated(self):
        model = tg.RenderDesign(visual_template="orbit_dots")
        self.assertEqual(model.visual_template, "orbit_dots")
        with self.assertRaises(ValidationError):
            tg.RenderDesign(visual_template="unknown")

    def test_separate_presets_preserve_classic_fields(self):
        original = tg.RenderDesign(circle_x=31, progress_x=320)
        classic_and_orbit = tg._design_presets({
            "render_design": original.model_dump()
        })
        self.assertEqual(classic_and_orbit["classic"]["circle_x"], 31)
        self.assertEqual(classic_and_orbit["classic"]["visual_template"], "classic")
        self.assertEqual(classic_and_orbit["orbit_dots"]["visual_template"], "orbit_dots")
        self.assertFalse(classic_and_orbit["orbit_dots"]["timecode_enabled"])

        classic_and_orbit["orbit_dots"]["circle_x"] = 103
        reread = tg._design_presets({
            "render_design": original.model_dump(),
            "render_design_presets": classic_and_orbit,
        })
        self.assertEqual(reread["classic"]["circle_x"], 31)
        self.assertEqual(reread["orbit_dots"]["circle_x"], 103)

    def test_orbit_sprite_is_cached_and_transparent_in_the_middle(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            tg, "ORBIT_ASSETS", Path(folder)
        ):
            image_path = tg.orbit_sprite_path(840)
            self.assertTrue(image_path.exists())
            self.assertEqual(tg.orbit_sprite_path(840), image_path)
            from PIL import Image
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (512, 512))
                self.assertEqual(image.getpixel((256, 256))[3], 0)
                self.assertGreater(image.getchannel("A").getbbox()[2], 400)
                rgba = image.convert("RGBA")
                pix = rgba.getpixel((256, 256))
                self.assertEqual(pix[3], 0)
                colored = [(r,g,b) for r,g,b,a in rgba.getdata() if a > 220]
                self.assertTrue(colored)
                self.assertTrue(all(r == g == b == 255 for r,g,b in colored))


class CircleFFmpegTests(unittest.TestCase):
    def _render(self, design, banner=None):
        # The extra orbit input is only added for the new template.
        # No real encode is needed to verify wiring/indexes/animation.
        with (
            patch.object(tg, "duration", side_effect=[2.0, 10.0]),
            patch.object(tg, "is_image_background", return_value=False),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg, "orbit_sprite_path", return_value=Path("orbit.png")),
            patch.object(tg.subprocess, "run") as run,
            patch.object(Path, "is_file", return_value=True),
        ):
            tg.render(Path("note.mp4"), Path("background.mp4"),
                      Path("output.mp4"), design, banner)
        argv = run.call_args.args[0]
        return argv, argv[argv.index("-filter_complex") + 1]

    def test_new_template_has_white_moving_ring_loading_bar_and_intro(self):
        # Even if a legacy profile had timecode_enabled=True, never print time.
        argv, graph = self._render(tg.RenderDesign(
            visual_template="orbit_dots", timecode_enabled=True
        ))
        self.assertIn("rotate=a='2*PI*t/6'", graph)
        self.assertIn("[2:v]format=rgba,rotate=", graph)
        self.assertIn("[orbit_ring]", graph)
        self.assertIn("[loading_track]", graph)
        self.assertIn("[loading_fill]", graph)
        self.assertIn("min(T/2.000", graph)
        self.assertIn("[circle]fade=t=in:st=0.38", graph)
        self.assertIn("fade=t=in:st=0.38:d=0.28:alpha=1[orbit_ring]", graph)
        self.assertIn("enable='gte(t,0.38)'", graph)
        self.assertNotIn("drawtext", graph)
        self.assertNotIn("[progress_dots]", graph)
        self.assertIn("orbit.png", argv)
        self.assertIn("-framerate", argv)

    def test_banner_is_fourth_ffmpeg_input_in_new_template(self):
        argv, graph = self._render(tg.RenderDesign(
            visual_template="orbit_dots",
            banner_enabled=True,
            timecode_enabled=False
        ), Path("banner.png"))
        self.assertIn("[3:v]scale=", graph)
        self.assertIn("[2:v]format=rgba,rotate=", graph)
        self.assertEqual(argv.count("banner.png"), 1)
        self.assertIn("fade=t=in:st=0.38:d=0.28:alpha=1[banner]", graph)

    def test_legacy_template_uses_same_progress_and_no_orbit(self):
        argv, graph = self._render(tg.RenderDesign(
            visual_template="classic", timecode_enabled=False
        ))
        self.assertIn("[progress_track]", graph)
        self.assertIn("[progress_fill]", graph)
        self.assertNotIn("orbit_ring", graph)
        self.assertNotIn("orbit.png", argv)


class TemplateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "static"
        cls.js = (root / "studio-circle-ui.js").read_text(encoding="utf-8")
        cls.css = (root / "studio-circle-ui.css").read_text(encoding="utf-8")
        cls.html = (root / "telegram-studio.html").read_text(encoding="utf-8")

    def test_second_template_is_not_a_separate_telegram_media_mode(self):
        self.assertIn("orbit_dots", self.js)
        self.assertIn("Шаблон 1", self.js)
        self.assertIn("Шаблон 2", self.js)
        self.assertIn("saveRenderDesign()", self.js)
        self.assertIn("window.setStudioTemplatePresets", self.js)
        self.assertIn("d.presets||{}", self.html)

    def test_preview_has_clean_white_ring_loading_and_no_time(self):
        self.assertIn("studio-orbit-ring", self.css)
        self.assertIn("studioOrbitRotate", self.css)
        self.assertIn("studio-progress-loading", self.css)
        self.assertIn("studio-intro-frame", self.js)
        self.assertIn("timecode.hidden=orbit", self.js)
        self.assertNotIn("studio-dot.filled", self.css)
        self.assertNotIn("studio-progress-dots", self.js)


if __name__ == "__main__":
    unittest.main()
