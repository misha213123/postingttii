"""Media diagnostics prove HEAD and byte-range GET work for BOTH video and cover."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app import main


class MediaDiagnosticTests(unittest.TestCase):
    def _run_probe(self, *, broken_video=False):
        async def run():
            with tempfile.TemporaryDirectory() as root:
                folder = Path(root)
                uploaded = folder / "uploads"
                uploaded.mkdir()
                covers = folder / "covers"
                covers.mkdir()
                (uploaded / "sample.mp4").write_bytes(b"not-a-real-video-but-good-for-http")
                (covers / "5.jpg").write_bytes(b"fake-jpeg-headers")
                before = set(main.MEDIA_TOKENS)
                calls = []
                real_client = httpx.AsyncClient

                def mock_request(request: httpx.Request):
                    token = request.url.path.rsplit("/", 1)[-1]
                    path = main.MEDIA_TOKENS[token]
                    raw = path.read_bytes()
                    is_video = path.suffix == ".mp4"
                    mime = "video/mp4" if is_video else "image/jpeg"
                    calls.append((request.method, mime))
                    if request.method == "HEAD":
                        status = 503 if broken_video and is_video else 200
                        return httpx.Response(status, headers={
                            "content-type": mime, "content-length": str(len(raw))
                        })
                    return httpx.Response(206, headers={
                        "content-type": mime,
                        "content-range": f"bytes 0-{len(raw)-1}/{len(raw)}",
                        "content-length": str(len(raw)),
                    }, content=raw)

                def tunnel_client(*args, **kwargs):
                    return real_client(transport=httpx.MockTransport(mock_request))

                fake_settings = SimpleNamespace(
                    public_base_url="https://example.trycloudflare.com",
                    upload_dir=uploaded,
                )
                with patch.object(main, "settings", fake_settings), \
                     patch.object(main, "COVER_DIR", covers), \
                     patch.object(main.httpx, "AsyncClient", side_effect=tunnel_client):
                    async with real_client(
                        transport=httpx.ASGITransport(app=main.app),
                        base_url="http://localhost:8765",
                    ) as local:
                        response = await local.post(
                            "/api/instagram/media-diagnostic",
                            json={"filename": "sample.mp4", "slot": 5},
                        )
                self.assertEqual(set(main.MEDIA_TOKENS), before)
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(len(calls), 4)
                self.assertEqual(data["video"]["range_status"], 206)
                self.assertEqual(data["cover"]["head_status"], 200)
                return data

        return asyncio.run(run())

    def test_video_and_cover_probes_succeed_and_tokens_expire(self):
        self.assertTrue(self._run_probe()["ok"])

    def test_diagnostic_exposes_transport_failure_without_meta_post(self):
        data = self._run_probe(broken_video=True)
        self.assertFalse(data["ok"])
        self.assertEqual(data["video"]["head_status"], 503)

    def test_meta_parallel_setting_is_bounded(self):
        with patch.dict(os.environ, {"INSTAGRAM_META_PARALLEL": "16"}):
            self.assertEqual(main._instagram_meta_parallel_limit(), 8)
        with patch.dict(os.environ, {"INSTAGRAM_META_PARALLEL": "bad"}):
            self.assertEqual(main._instagram_meta_parallel_limit(), 2)


if __name__ == "__main__":
    unittest.main()
