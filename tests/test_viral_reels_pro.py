"""Viral Reels Pro v3: project persistence, fingerprints, captions, API and FFmpeg.

The real full 1080x1920 FFmpeg integration test is opt-in:
  VIRAL_PRO_TEST_RENDER=1 python -m unittest tests.test_viral_reels_pro
Default tests do not modify legacy Telegram templates.
"""
from __future__ import annotations
import asyncio
import io
import json
import os
import shutil
import subprocess
import tempfile
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from starlette.requests import Request
from PIL import Image, ImageDraw
import numpy as np
from app import viral_reels_pro as pro
from app import telegram_studio as old


def local(method="GET"):
    return Request({
        "type": "http", "client": ("127.0.0.1", 48621),
        "method": method, "path": "/api/viral-pro", "headers": [],
    })


class ProjectPersistenceTests(unittest.TestCase):
    def test_template_three_is_separate_and_old_defaults_unchanged(self):
        self.assertEqual(old.RenderDesign().visual_template, "classic")
        self.assertEqual(old.RenderDesign(visual_template="orbit_dots").visual_template, "orbit_dots")
        self.assertEqual(pro.Config().fps, 30)
        self.assertEqual(pro.Config().smooth, "auto")

    def test_atomic_project_json_can_be_reopened(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            with patch.object(pro, "PROJECTS", root):
                p = pro.create_project(local("POST"))
                p["config"]["circle_x"] = 125
                pro._save(p)
                loaded = pro.get_project(p["id"], local())
                self.assertEqual(loaded["template"], "viral_reels_pro_v3")
                self.assertEqual(loaded["config"]["circle_x"], 125)
                self.assertTrue((root / (p["id"] + ".json")).exists())
                self.assertEqual(pro.list_projects(local())[0]["id"], p["id"])

    def test_reject_path_traversal(self):
        for invalid in ("../secrets", "%2e%2e", "", "abc", "\\" * 32):
            with self.assertRaises(HTTPException):
                pro._id(invalid)

    def test_profile_settings_validate_bounds(self):
        with self.assertRaises(ValueError):
            pro.Config(circle_size=1200)
        with self.assertRaises(ValueError):
            pro.Config(bitrate_mbps=30)
        with self.assertRaises(ValueError):
            pro.Config(fps=120)


class FingerprintTests(unittest.TestCase):
    def test_sha256_detects_exactly_identical_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / "a.bin", Path(folder) / "b.bin"
            a.write_bytes(b"one video content")
            b.write_bytes(b"one video content")
            self.assertEqual(pro._sha(a), pro._sha(b))
            b.write_bytes(b"different")
            self.assertNotEqual(pro._sha(a), pro._sha(b))

    def test_dhash_distinguishes_similar_from_identical(self):
        a = {"sha256": "abc", "frames": ["0000000000000000"] * 3, "audio_hash": "abc"}
        b = {"sha256": "def", "frames": ["0000000000000001"] * 3, "audio_hash": "abc"}
        s = pro._similarity(a, b)
        self.assertFalse(s["exact"])
        self.assertGreater(s["visual"], .97)
        self.assertTrue(s["audio_exact"])

    def test_source_hash_matching_across_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            p.joinpath("previous.json").write_text(json.dumps({
                "id": "old", "analysis": {"sources": [{
                    "media_id": "old_clip", "sha256": "abc",
                    "frames": ["fa" * 8], "audio_hash": "",
                }]},
            }))
            our = {"id": "new", "analysis": {"sources": [{
                "media_id": "new_clip", "sha256": "abc",
                "frames": ["fa" * 8], "audio_hash": "",
            }]}}
            with patch.object(pro, "PROJECTS", p):
                matches = pro._source_reuse(our)
            self.assertEqual(len(matches), 1)
            self.assertTrue(matches[0]["exact"])

    def test_variant_clip_trimming_is_conservative(self):
        c = pro.Clip(media_id="a" * 32, start=0, end=10)
        original = pro._segment_bounds(c, {"duration": 10}, "A",
                                       [{"start": 0, "end": 1}])
        dynamic = pro._segment_bounds(c, {"duration": 10}, "B",
                                      [{"start": 0, "end": 1}])
        retention = pro._segment_bounds(c, {"duration": 10}, "C",
                                        [{"start": 0, "end": 1}])
        self.assertEqual(original, (0, 10))
        self.assertGreater(dynamic[0], 0)
        self.assertGreater(retention[0], dynamic[0])


class CaptionTests(unittest.TestCase):
    def test_word_by_word_ass_has_timestamps_and_black_outline(self):
        cfg = pro.Config(caption_y=770, captions=[pro.Word(
            start=.2, end=.5, text="Привет",
        )])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "caption.ass"
            pro._make_ass(cfg.captions, target, cfg)
            content = target.read_text(encoding="utf-8-sig")
            self.assertIn("Привет", content)
            self.assertIn("0:00:00.20", content)
            self.assertIn("0:00:00.50", content)
            self.assertIn("pos(540,770)", content)
            self.assertIn("&H00000000", content)

    def test_caption_fonts_and_presets(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = pro.Config(caption_font="Impact", caption_template="highlight")
            target = Path(folder) / "caption.ass"
            pro._make_ass([pro.Word(start=.1,end=.3,text="Привет")], target, cfg)
            text = target.read_text(encoding="utf-8-sig")
            self.assertIn("Style: Viral,Impact,", text)
            self.assertIn("37E2FF", text)
            self.assertIn("Привет", text)
            with self.assertRaises(ValueError):
                pro.Config(caption_template="unknown")
            with self.assertRaises(ValueError):
                pro.Config(banner_chroma_color="wrong")

    def test_dragged_subtitle_coordinates_are_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            config = pro.Config(caption_x=375, caption_y=660, caption_font_size=88)
            target = Path(directory) / "dragged.ass"
            pro._make_ass([pro.Word(start=0.2, end=0.7, text="Мой ролик")],
                          target, config)
            data = target.read_text(encoding="utf-8-sig")
            self.assertIn("pos(375,660)", data)
            self.assertIn("Arial,88", data)

    def test_zoom_values_are_bounded(self):
        self.assertEqual(pro.Config(background_zoom=1.5).background_zoom, 1.5)
        with self.assertRaises(ValueError):
            pro.Config(background_zoom=.5)
        with self.assertRaises(ValueError):
            pro.Config(background_zoom=2.1)

    def test_word_timestamp_conversion(self):
        self.assertEqual(pro._ass_timestamp(1.25), "0:00:01.25")


class AudioDecodeCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcriber_passes_numpy_to_faster_whisper_not_a_video_path(self):
        """Regression: av.open(path, metadata_errors=...) fails on some PyAV versions."""
        seen = []
        class SampleWord:
            start = .2
            end = .5
            word = " Привет "
        class SampleSegment:
            words = [SampleWord()]
        class WhisperStub:
            def __init__(self, *args, **kwargs):
                pass
            def transcribe(self, waveform, **kwargs):
                self_type = type(waveform)
                seen.append((self_type, kwargs.get("word_timestamps")))
                if not isinstance(waveform, np.ndarray):
                    raise AssertionError("Must not pass a path to PyAV")
                return iter([SampleSegment()]), {}
        cfg = pro.Config(clips=[
            pro.Clip(media_id="a"*32, start=3, end=5),
            pro.Clip(media_id="b"*32, start=1, end=3),
        ])
        with (
            patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=WhisperStub)}),
            patch.object(pro, "_source", return_value=Path("source.mp4")),
            patch.object(pro, "_probe", return_value={"duration": 12, "audio": True}),
            patch.object(pro, "_decode_clip_audio",
                         return_value=np.ones(32000, dtype=np.float32)) as decode,
        ):
            words = await pro._transcribe_words(cfg)
        self.assertEqual([w["text"] for w in words], ["Привет", "Привет"])
        self.assertAlmostEqual(words[0]["start"], .2)
        self.assertAlmostEqual(words[1]["start"], 2.2)
        self.assertEqual([call.args[1:] for call in decode.call_args_list],
                         [(3, 2), (1, 2)])
        self.assertEqual(seen, [(np.ndarray, True), (np.ndarray, True)])

    async def test_pcm_ffmpeg_decoder_outputs_float32(self):
        samples = np.array([.2, -.3, 0, .8], dtype=np.float32)
        completed = subprocess.CompletedProcess([], 0, samples.tobytes(), b"")
        with patch.object(pro.subprocess, "run", return_value=completed) as runner:
            result = pro._decode_clip_audio(Path("clip.mp4"), 2.0, 3.0)
        self.assertEqual(result.dtype, np.float32)
        np.testing.assert_array_almost_equal(result, samples)
        command = runner.call_args.args[0]
        self.assertIn("ffmpeg", command)
        self.assertEqual(command[command.index("-ss") + 1], "2.0")
        self.assertEqual(command[command.index("-t") + 1], "3.0")
        self.assertEqual(command[command.index("-f") + 1], "f32le")


class AutoEditRepeatedMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_source_gets_distinct_scene_indices(self):
        mid = "d" * 32
        cfg = pro.Config(clips=[
            pro.Clip(media_id=mid, start=0, end=3),
            pro.Clip(media_id=mid, start=4, end=8),
        ])
        project = {"id": "e" * 32, "config": cfg.model_dump()}
        pauses = [{"start": 0, "end": .9}, {"start": 4, "end": 4.9}]
        with (
            patch.object(pro, "_project", return_value=project),
            patch.object(pro, "_source", return_value=Path("same.mp4")),
            patch.object(pro, "_probe", return_value={"duration": 9}),
            patch.object(pro, "_silences", return_value=pauses),
        ):
            result = await pro.auto_edit(project["id"], local("POST"))
        self.assertFalse(result["applied"])
        self.assertEqual([p["clip_index"] for p in result["proposals"]], [0, 1])
        self.assertEqual([p["suggested"]["media_id"] for p in result["proposals"]],
                         [mid, mid])


class AutomaticPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_click_runs_safe_steps_in_order_and_records_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            temp, exports = base / "tmp", base / "exports"
            temp.mkdir()
            exports.mkdir()
            mid, pid, job_id = "a" * 32, "b" * 32, "c" * 32
            cfg = pro.Config(clips=[pro.Clip(media_id=mid, start=0, end=2)],
                             background_name="bg.png", background_source="local")
            project = {"id": pid, "template": "viral_reels_pro_v3",
                       "config": cfg.model_dump(), "exports": [], "analysis": {}}
            job = {"id": job_id, "status": "queued", "notes": [],
                   "cancel": False, "done": [], "progress": 0, "stage": ""}
            events = []
            async def mock_transcribe(config):
                events.append("caption")
                return [pro.Word(start=.1, end=.3, text="Привет").model_dump()]
            async def mock_analysis(project):
                events.append("analyze")
            async def mock_render(project, variant, job, work, index):
                events.append("render_" + variant)
                (exports / pid).mkdir(exist_ok=True)
                return {"id": ("d" if variant == "A" else
                               "e" if variant == "B" else "f") * 32,
                        "file": "sample.mp4", "variant": variant}
            with (
                patch.object(pro, "TMP", temp),
                patch.object(pro, "EXPORTS", exports),
                patch.object(pro, "RENDER_LOCK", asyncio.Lock()),
                patch.object(pro, "_project", return_value=project),
                patch.object(pro, "_source", return_value=Path("source.mp4")),
                patch.object(pro, "_probe", return_value={"duration": 2.0}),
                patch.object(pro, "_silences", return_value=[]),
                patch.object(pro, "_save"),
                patch.object(pro, "_transcribe_words", side_effect=mock_transcribe),
                patch.object(pro, "_analyze_project", side_effect=mock_analysis),
                patch.object(pro, "_render_variant", side_effect=mock_render),
            ):
                await pro._job_runner(pid, job)
                self.assertEqual(job["status"], "completed")
                self.assertEqual(events, ["caption", "analyze", "render_A",
                                          "render_B", "render_C"])
                self.assertEqual(len(project["exports"]), 3)
                self.assertEqual(len(project["config"]["captions"]), 1)
                for item in project["exports"]:
                    self.assertTrue((exports / pid / (item["id"] + "_manifest.json")).is_file())


class RenderProgressTests(unittest.TestCase):
    def test_media_time_progress_is_not_wall_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.txt"
            self.assertIsNone(pro._read_ffmpeg_seconds(path))
            path.write_text("frame=60\nout_time=00:00:02.500000\nprogress=continue\n")
            self.assertEqual(pro._read_ffmpeg_seconds(path), 2.5)
            path.write_text("out_time=00:00:01.000000\nout_time=00:00:03.250000\n")
            self.assertEqual(pro._read_ffmpeg_seconds(path), 3.25)
            path.write_text("out_time=N/A\n")
            self.assertIsNone(pro._read_ffmpeg_seconds(path))


class ShortPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_is_isolated_from_original_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("media", "backgrounds", "exports", "tmp", "projects", "previews"):
                (root / name).mkdir()
            mid, pid = "a" * 32, "b" * 32
            (root / "media" / (mid + ".mp4")).write_bytes(b"source-unmodified")
            (root / "backgrounds" / "bg.png").write_bytes(b"background")
            config = pro.Config(
                clips=[pro.Clip(media_id=mid, start=2, end=9)],
                background_source="local", background_name="bg.png",
            )
            project = {"id": pid, "config": config.model_dump(),
                       "exports": [{"id": "old-export"}], "analysis": {}}
            (root / "projects" / (pid + ".json")).write_text(json.dumps(project))
            async def fake_render(p, variant, job, work, position):
                self.assertEqual(variant, "A")
                self.assertEqual(len(p["config"]["clips"]), 1)
                self.assertEqual(p["config"]["clips"][0]["start"], 2)
                self.assertEqual(p["config"]["clips"][0]["end"], 5.0)
                outdir = root / "exports" / p["id"]
                outdir.mkdir()
                (outdir / "sample.mp4").write_bytes(b"preview-rendered")
                return {"file": "sample.mp4", "duration": 3.0}
            with (
                patch.object(pro, "MEDIA", root / "media"),
                patch.object(pro, "OWN_BACKGROUNDS", root / "backgrounds"),
                patch.object(pro, "PROJECTS", root / "projects"),
                patch.object(pro, "EXPORTS", root / "exports"),
                patch.object(pro, "PREVIEWS", root / "previews"),
                patch.object(pro, "TMP", root / "tmp"),
                patch.object(pro, "RENDER_LOCK", asyncio.Lock()),
                patch.object(pro, "JOBS", {}),
                patch.object(pro, "_probe", return_value={"duration": 12.0}),
                patch.object(pro, "_render_variant", side_effect=fake_render),
                patch.object(pro.shutil, "which", return_value="ffmpeg"),
            ):
                result = await pro.render_preview(pid, local("POST"))
                self.assertTrue(result["ok"])
                self.assertEqual((root / "previews" / (pid + ".mp4")).read_bytes(),
                                 b"preview-rendered")
                self.assertEqual((root / "media" / (mid + ".mp4")).read_bytes(),
                                 b"source-unmodified")
                self.assertEqual(json.loads((root / "projects" / (pid + ".json")).read_text()),
                                 project)
                self.assertEqual(list((root / "exports").iterdir()), [])


class UploadAssetTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_upload_is_private_and_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            img = Image.new("RGB", (100, 150), "navy")
            stream = io.BytesIO()
            img.save(stream, format="PNG")
            file = UploadFile(
                file=io.BytesIO(stream.getvalue()), filename="background.png",
                headers=Headers({"content-type": "image/png"}),
            )
            result = await pro._upload_asset(file, target, 1000000)
            self.assertTrue((target / result["name"]).exists())
            self.assertTrue(result["name"].endswith(".png"))

    async def test_file_over_limit_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            f = UploadFile(file=io.BytesIO(b"bad" * 3000),
                           filename="banner.png")
            with self.assertRaises(HTTPException):
                await pro._upload_asset(f, target, 100)
            self.assertEqual(list(target.iterdir()), [])


@unittest.skipUnless(os.environ.get("VIRAL_PRO_TEST_RENDER") == "1" and
                     shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Full FFmpeg render is opt-in (VIRAL_PRO_TEST_RENDER=1)")
class RealFfmpegExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_circle_and_background_and_mp4_export(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            media = base / "media"
            bgs = base / "backgrounds"
            exports = base / "exports"
            temp = base / "tmp"
            projects = base / "projects"
            for d in (media, bgs, exports, temp, projects):
                d.mkdir()
            mid = "a" * 32
            source = media / (mid + ".mp4")
            subprocess.run([
                "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                "-i", "testsrc2=s=240x240:r=24:d=0.8", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source),
            ], check=True, timeout=40)
            bg = bgs / "bg.png"
            Image.new("RGB", (640, 1000), "#123458").save(bg)
            banners = base / "banners"
            banners.mkdir()
            artwork=Image.new("RGB",(240,90),"#00FF00")
            ImageDraw.Draw(artwork).rectangle((50,16,190,75),fill="#e22228")
            artwork.save(banners / "ad.png")
            cfg = pro.Config(
                clips=[pro.Clip(media_id=mid, start=0, end=.7)],
                banner_enabled=True,banner_file="ad.png",
                banner_chroma_enabled=True,banner_chroma_color="#00FF00",
                banner_chroma_similarity=.22,banner_chroma_blend=.07,
                background_name="bg.png", background_source="local",
                circle_size=330, circle_x=120, circle_y=300,
                smooth="off", loudness=False, denoise=False,
                bitrate_mbps=4,
            )
            p = {"id": "b" * 32, "template": "viral_reels_pro_v3",
                 "config": cfg.model_dump(), "exports": [], "analysis": {}}
            job = {"id": "c" * 32, "status": "running", "progress": 0,
                   "notes": [], "cancel": False, "stage": ""}
            with (
                patch.object(pro, "MEDIA", media),
                patch.object(pro, "OWN_BACKGROUNDS", bgs),
                patch.object(pro, "OWN_BANNERS", banners),
                patch.object(pro, "EXPORTS", exports),
                patch.object(pro, "TMP", temp),
                patch.object(pro, "PROJECTS", projects),
            ):
                for position, choice in enumerate("ABC"):
                    variant = await pro._render_variant(p, choice, job, temp, position)
                    output = exports / p["id"] / variant["file"]
                    self.assertTrue(output.is_file())
                    self.assertEqual(variant["variant"], choice)
                    self.assertEqual(variant["width"], 1080)
                    self.assertEqual(variant["height"], 1920)
                    self.assertTrue(variant["sha256"])
                    self.assertEqual(variant["source_sha256"][0]["sha256"],
                                     pro._sha(source))
                    self.assertEqual(variant["settings"]["banner_chroma_enabled"],True)
                    self.assertGreater(output.stat().st_size, 1000)
                self.assertGreater(job["progress"], 0)


if __name__ == "__main__":
    unittest.main()
