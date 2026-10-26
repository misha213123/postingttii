"""Prepare only AAC in legacy Reels without recompressing existing H.264 video."""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import main


class LegacyReelsAudioNormalizationTests(unittest.TestCase):
    def test_legacy_191kbps_audio_gets_a_temporary_128kbps_copy(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            original = root / "source.mp4"
            original.write_bytes(b"original H264 and AAC test data")
            before = original.read_bytes()
            commands = []

            def run(cmd, **kwargs):
                commands.append(cmd)
                if cmd[0] == "ffprobe":
                    return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
                        "streams": [{
                            "codec_name": "aac", "bit_rate": "191297",
                            "sample_rate": "48000", "channels": 2,
                        }]
                    }))
                # Fake ffmpeg output without actually transcoding fixture bytes.
                Path(cmd[-1]).write_bytes(b"copy video with normalized audio")
                return subprocess.CompletedProcess(cmd, 0)

            with (patch.object(main, "settings", SimpleNamespace(data_dir=root)),
                  patch.object(main.subprocess, "run", side_effect=run)):
                prepared, cleanup = main._prepare_instagram_audio(original)

            self.assertIsNotNone(cleanup)
            self.assertNotEqual(prepared, original)
            self.assertEqual(original.read_bytes(), before)
            self.assertTrue(prepared.exists())
            ffmpeg = commands[1]
            self.assertEqual(ffmpeg[ffmpeg.index("-c:v")+1], "copy")
            self.assertEqual(ffmpeg[ffmpeg.index("-c:a")+1], "aac")
            self.assertEqual(ffmpeg[ffmpeg.index("-b:a")+1], "128k")
            self.assertEqual(ffmpeg[ffmpeg.index("-ar")+1], "48000")
            self.assertIn("+faststart", ffmpeg)
            prepared.unlink()

    def test_compliant_aac_128kbps_is_not_modified(self):
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "source.mp4"
            source.write_bytes(b"valid")
            with patch.object(main.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps({"streams": [{
                    "codec_name": "aac", "bit_rate": "127000",
                    "sample_rate": "48000", "channels": 2,
                }]}),
            )) as run:
                prepared, cleanup = main._prepare_instagram_audio(source)
            self.assertEqual(prepared, source)
            self.assertIsNone(cleanup)
            self.assertEqual(run.call_count, 1)

    def test_temporary_copy_is_deleted_after_token_expiration(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as name:
                root = Path(name)
                cache = root / "instagram_meta_prepared"
                cache.mkdir()
                temp = cache / "prepared.mp4"
                temp.write_bytes(b"converted")
                main.MEDIA_TOKENS["abc"] = temp
                with patch.object(main, "settings", SimpleNamespace(data_dir=root)):
                    await main._expire_media_token("abc", delay_seconds=0,
                                                   cleanup_path=temp)
                self.assertFalse(temp.exists())
                self.assertNotIn("abc", main.MEDIA_TOKENS)
        asyncio.run(exercise())

    def test_token_cleanup_will_not_delete_original_video(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as name:
                root = Path(name)
                source = root / "original.mp4"
                source.write_bytes(b"keep")
                main.MEDIA_TOKENS["abc"] = source
                with patch.object(main, "settings", SimpleNamespace(data_dir=root)):
                    await main._expire_media_token("abc", delay_seconds=0,
                                                   cleanup_path=source)
                self.assertTrue(source.exists())
                self.assertNotIn("abc", main.MEDIA_TOKENS)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
