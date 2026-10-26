"""Regression test: a public Reels asset must answer GET and HEAD.

Cloudflare media_gateway passes HEAD probes to the local /media endpoint.
Meta may inspect asset headers before downloading the complete MP4.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

from app.main import app, MEDIA_TOKENS


class InstagramMediaTransportTests(unittest.TestCase):
    def test_mp4_supports_head_get_and_byte_ranges(self):
        async def exercise():
            token = "AbCdEf0123456789_AbCdEf012345678"  # 32 URL-safe chars
            content = b"sample-reel-test-payload"
            with tempfile.TemporaryDirectory() as directory:
                video = Path(directory) / "test.mp4"
                video.write_bytes(content)
                MEDIA_TOKENS[token] = video
                try:
                    transport = httpx.ASGITransport(app=app)
                    async with httpx.AsyncClient(
                        transport=transport, base_url="http://testserver"
                    ) as client:
                        head = await client.head(f"/media/{token}")
                        self.assertEqual(head.status_code, 200)
                        self.assertEqual(head.headers["content-type"], "video/mp4")
                        self.assertEqual(int(head.headers["content-length"]), len(content))

                        get = await client.get(f"/media/{token}")
                        self.assertEqual(get.status_code, 200)
                        self.assertEqual(get.content, content)

                        ranged = await client.get(
                            f"/media/{token}", headers={"Range": "bytes=0-3"}
                        )
                        self.assertEqual(ranged.status_code, 206)
                        self.assertEqual(ranged.content, content[:4])
                        self.assertTrue(
                            ranged.headers.get("content-range", "").startswith("bytes 0-3/")
                        )
                finally:
                    MEDIA_TOKENS.pop(token, None)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
