"""Regression checks for the read-only Reels archive eligibility monitor."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.services.reels_archive import (
    ReelsArchiveMonitor, _timestamp, _views,
)


class ReelsArchiveTests(unittest.TestCase):
    def test_missing_views_never_counts_as_zero(self):
        self.assertIsNone(_views({"data": []}))
        self.assertIsNone(_views({"data": [{"name": "views", "total_value": {}}]}))
        self.assertIsNone(_views({"data": [{"name": "views", "total_value": {"value": -1}}]}))
        self.assertEqual(_views({"data": [{"name": "views", "total_value": {"value": 0}}]}), 0)
        self.assertEqual(_views({"data": [{"name": "views", "values": [{"value": 2499}]}]}), 2499)

    def test_date_requires_timezone(self):
        self.assertIsNotNone(_timestamp("2026-10-20T08:00:00+0000"))
        self.assertIsNone(_timestamp("2026-10-20T08:00:00"))

    def test_24h_rule_and_strict_threshold(self):
        async def scenario():
            now = 1793000000
            old = "2026-10-01T00:00:00+0000"
            future = "2026-11-01T00:00:00+0000"
            requested = []

            def handler(req):
                requested.append(str(req.url))
                if req.url.path.endswith("/media"):
                    return httpx.Response(200, json={"data": [
                        {"id": "low", "media_product_type": "REELS",
                         "timestamp": old, "permalink": "https://www.instagram.com/reel/low/"},
                        {"id": "equal", "media_product_type": "REELS",
                         "timestamp": old, "permalink": "https://www.instagram.com/reel/equal/"},
                        {"id": "young", "media_product_type": "REELS",
                         "timestamp": future},
                        {"id": "photo", "media_product_type": "FEED",
                         "timestamp": old},
                    ]})
                if req.url.path.endswith("/low/insights"):
                    return httpx.Response(200, json={"data": [
                        {"name": "views", "total_value": {"value": 2499}}
                    ]})
                if req.url.path.endswith("/equal/insights"):
                    return httpx.Response(200, json={"data": [
                        {"name": "views", "total_value": {"value": 2500}}
                    ]})
                return httpx.Response(404, json={"error": {"code": 100}})

            with tempfile.TemporaryDirectory() as temp:
                monitor = ReelsArchiveMonitor(Path(temp) / "state.json")
                records = {}
                with patch(
                    "app.services.reels_archive._instagram_access_token",
                    new=AsyncMock(return_value=("token", {"id": "account123"})),
                ):
                    async with httpx.AsyncClient(
                        transport=httpx.MockTransport(handler)
                    ) as client:
                        await monitor._scan_account(
                            client, {"slot": 1, "label": "Test"}, records, now
                        )
                self.assertEqual(records["account123:low"]["status"], "needs_manual_archive")
                self.assertEqual(records["account123:equal"]["status"], "retained")
                self.assertEqual(records["account123:young"]["status"], "waiting_24h")
                self.assertNotIn("account123:photo", records)
                self.assertEqual(sum("/insights" in url for url in requested), 2)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
