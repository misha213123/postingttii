"""Independent 9:16 layout presets and per-video background selection."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import instagram_studio as ig
from app import telegram_studio as tg


class TelegramLayoutPlacementTests(unittest.TestCase):
    def test_circle_uses_saved_position_in_real_filter(self):
        with (
            patch.object(tg, "duration", side_effect=[6.0, 15.0]),
            patch.object(tg.random, "uniform", return_value=0.0),
            patch.object(tg.subprocess, "run") as run,
        ):
            design=tg.RenderDesign(circle_size=810,circle_x=200,circle_y=510)
            tg.render(Path("tg.mp4"),Path("background.mp4"),Path("rendered.mp4"),design)
        args=run.call_args.args[0]
        filt=args[args.index("-filter_complex")+1]
        self.assertIn("scale=810:810",filt)
        self.assertIn("overlay=200:510",filt)
        self.assertIn("0:a?",args)

    def test_circle_can_grow_up_to_1080_without_moving_out_of_canvas(self):
        with (
            patch.object(tg, "duration", side_effect=[6.0, 15.0]),
            patch.object(tg.subprocess, "run") as run,
        ):
            d=tg.RenderDesign(circle_size=1080,circle_x=850,circle_y=1800)
            tg.render(Path("tg.mp4"),Path("background.mp4"),Path("rendered.mp4"),d)
        args=run.call_args.args[0]
        filt=args[args.index("-filter_complex")+1]
        self.assertIn("overlay=0:840",filt)

    def test_selects_one_enabled_background_or_all_for_shuffle(self):
        with tempfile.TemporaryDirectory() as directory:
            bg=Path(directory)
            with (
                patch.object(tg, "BACKGROUNDS", bg),
                patch.object(tg, "backgrounds", return_value=[
                    {"name":"one.mp4","enabled":True},
                    {"name":"two.mp4","enabled":True},
                    {"name":"off.mp4","enabled":False},
                ]),
            ):
                self.assertEqual(
                    tg.choose_backgrounds(tg.RenderDesign(background_mode="shuffle")),
                    [bg/"one.mp4", bg/"two.mp4"],
                )
                self.assertEqual(
                    tg.choose_backgrounds(tg.RenderDesign(
                        background_mode="single",background_name="two.mp4")),
                    [bg/"two.mp4"],
                )
                with self.assertRaisesRegex(ValueError,"удалён или выключен"):
                    tg.choose_backgrounds(tg.RenderDesign(
                        background_mode="single",background_name="off.mp4"))


class InstagramSeparateTemplateTests(unittest.TestCase):
    def test_layout_model_accepts_independent_geometry_and_opacity(self):
        d=ig.InstagramLayout(
            video_x=201,video_y=310,video_width=540,video_height=960,
            video_opacity=.45,background_mode="single",background_name="bg.mp4",
        )
        self.assertEqual(d.video_x,201)
        self.assertEqual(d.video_opacity,.45)
        with self.assertRaises(ValidationError):
            ig.InstagramLayout(video_opacity=1.2)
        with self.assertRaises(ValidationError):
            ig.InstagramLayout(background_mode="invalid")

    def test_single_background_and_alpha_are_reflected_in_ffmpeg(self):
        with (
            patch.object(ig, "duration", side_effect=[8.0, 22.0]),
            patch.object(ig.random, "uniform", return_value=1.0),
            patch.object(ig, "cmd") as call,
        ):
            d=ig.InstagramLayout(
                video_x=120,video_y=230,video_width=600,
                video_height=1100,video_opacity=.55,background_mode="single",
                background_name="chosen.mp4",
            )
            ig.render(Path("instagram.mp4"),Path("finished.mp4"),
                      Path("chosen.mp4"),d)
        args=call.call_args.args[0]
        filt=args[args.index("-filter_complex")+1]
        self.assertIn("scale=600:1100",filt)
        self.assertIn("pad=600:1100",filt)
        self.assertIn("colorchannelmixer=aa=0.550",filt)
        self.assertIn("overlay=120:230",filt)
        self.assertEqual(args.count("-i"),2)
        self.assertIn("0:a?",args)

    def test_blur_and_original_no_background_preserve_audio(self):
        for mode in ("blur","none"):
            with self.subTest(mode=mode), (
                patch.object(ig, "duration",return_value=9.0),
                patch.object(ig, "cmd") as call,
            ):
                ig.render(Path("instagram.mp4"),Path("out.mp4"),None,
                          ig.InstagramLayout(background_mode=mode))
            args=call.call_args.args[0]
            filt=args[args.index("-filter_complex")+1]
            self.assertEqual(args.count("-i"),1)
            self.assertIn("0:a?",args)
            if mode=="blur":self.assertIn("boxblur",filt)
            else:self.assertIn("color=c=black",filt)

    def test_single_background_never_falls_back_to_other(self):
        pool=[Path("/tmp/a.mp4"),Path("/tmp/b.mp4")]
        with patch.object(ig, "BACKGROUNDS", Path("/tmp")):
            self.assertEqual(
                ig.choose_instagram_background(
                    ig.InstagramLayout(background_mode="single",background_name="b.mp4"),pool),
                pool[1],
            )
            with self.assertRaises(ValueError):
                ig.choose_instagram_background(
                    ig.InstagramLayout(background_mode="single",background_name="deleted.mp4"),pool)


class InstagramLayoutPersistenceTests(unittest.TestCase):
    def test_instagram_design_stays_separate_from_download_history(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/"sources.json"
            config.write_text(
                '{"accounts":["https://www.instagram.com/example/"],'
                '"disabled":[],"processed":["old"],"hashes":[],"links":[]}',
                encoding="utf-8",
            )
            with (
                patch.object(ig, "CONFIG", config),
                patch.dict(ig.JOB, {"status":"idle"}),
            ):
                layout=ig.InstagramLayout(
                    video_x=123,background_mode="none",video_opacity=.7,
                )
                result=ig.set_layout(layout)
                self.assertEqual(result["layout"]["video_x"],123)
                self.assertEqual(ig.current_layout().video_x,123)
                self.assertEqual(ig.load()["processed"],["old"])
                self.assertEqual(ig.load()["accounts"],
                                 ["https://www.instagram.com/example/"])


if __name__=="__main__":
    unittest.main()
