"""Regression tests for new Instagram account insights dashboard."""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from app import instagram_stats as stats
from app.services.platforms import instagram_auth_url


class AnalyticsDataTests(unittest.TestCase):
    def test_instagram_insights_scope_is_explicit_opt_in(self):
        normal = parse_qs(urlsplit(instagram_auth_url("state", 12, 2)).query)
        requested = parse_qs(urlsplit(instagram_auth_url("state", 12, 2, insights=True)).query)
        self.assertNotIn("instagram_business_manage_insights", normal["scope"][0])
        self.assertIn("instagram_business_manage_insights", requested["scope"][0])
        self.assertIn("instagram_business_content_publish", requested["scope"][0])

    def test_cached_file_contains_no_access_token(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(stats, "STATS_FILE", Path(directory) / "instagram_stats.json"):
                stats._write({"version": 1, "accounts": {
                    "12": {"followers": 100, "reels": [{"views": 250}]}
                }})
                body = stats.STATS_FILE.read_text(encoding="utf-8")
                self.assertIn('"followers": 100', body)
                self.assertNotIn("access_token", body)
                self.assertNotIn("client_secret", body)

    def test_unknown_metrics_are_not_zero(self):
        self.assertIsNone(stats._number(None))
        self.assertIsNone(stats._number("missing"))
        self.assertIsNone(stats._number(-1))
        self.assertEqual(stats._number(0), 0)


class AnalyticsCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_collect_reels_and_preserve_follower_history(self):
        original = {
            "history": [{"at": int(time.time())-3600, "followers": 24}],
            "reels": [{"id": "media1", "views": 120}],
        }
        account = {
            "slot": 11, "meta_app": 2, "id": "user11",
            "username": "demo", "access_token": "fake_test_token",
            "expires_at": int(time.time()) + 7200,
        }
        reel = {
            "id": "media1", "caption": "example", "likes": 12,
            "comments": 2, "views": None, "views_change": None,
        }
        with (
            patch.object(stats, "_profile", new=AsyncMock(return_value={
                "username": "demo", "followers_count": 26, "media_count": 51,
            })),
            patch.object(stats, "_reels", new=AsyncMock(return_value=[reel])),
            patch.object(stats, "_media_views", new=AsyncMock(return_value=180)),
        ):
            report = await stats.collect_account(account, original)
        self.assertEqual(report["followers"], 26)
        self.assertEqual(report["media_count"], 51)
        self.assertEqual(report["reels"][0]["views"], 180)
        self.assertEqual(report["reels"][0]["views_change"], 60)
        self.assertEqual(report["history"][0]["followers"], 24)
        self.assertEqual(report["history"][-1]["followers"], 26)
        self.assertEqual(report["insights_status"], "available")

    async def test_insights_permission_error_does_not_hide_basic_metrics(self):
        account = {
            "slot": 3, "id": "user3", "meta_app": 1,
            "access_token": "fake_token",
            "expires_at": int(time.time()) + 7200,
        }
        request = httpx.Request("GET", "https://graph.instagram.com/v25.0/111/insights")
        response = httpx.Response(403, request=request)
        failed = httpx.HTTPStatusError("Forbidden", request=request, response=response)
        with (
            patch.object(stats, "_profile", new=AsyncMock(return_value={
                "followers_count": 48, "media_count": 4,
            })),
            patch.object(stats, "_reels", new=AsyncMock(return_value=[
                {"id": "111", "likes": 5, "views": None, "views_change": None},
            ])),
            patch.object(stats, "_media_views", new=AsyncMock(side_effect=failed)),
        ):
            report = await stats.collect_account(account)
        self.assertIsNone(report["reels"][0]["views"])
        self.assertEqual(report["reels"][0]["likes"], 5)
        self.assertEqual(report["followers"], 48)
        self.assertEqual(report["insights_status"], "permission_required")

    async def test_expired_token_does_not_make_external_request(self):
        account = {"slot": 8, "id": "u8", "access_token": "test", "expires_at": int(time.time()) - 5}
        with patch.object(stats, "_profile", new=AsyncMock()) as get_profile:
            data = await stats.collect_account(account)
        self.assertIn("истёк", data["error"])
        get_profile.assert_not_awaited()

    async def test_failed_update_retains_previous_good_values(self):
        original = {
            "followers": 99, "media_count": 12, "last_updated": 140000,
            "reels": [{"id": "m0", "views": 80}],
            "history": [{"at": 140000, "followers": 99}],
        }
        account = {"slot": 2, "id": "user2", "access_token": "test", "expires_at": int(time.time()) + 7200}
        with patch.object(stats, "_profile", new=AsyncMock(side_effect=ValueError("no profile"))):
            report = await stats.collect_account(account, original)
        self.assertEqual(report["followers"], 99)
        self.assertEqual(report["reels"], original["reels"])
        self.assertIsNotNone(report["error"])


class AnalyticsApiTests(unittest.IsolatedAsyncioTestCase):
    def local_request(self, host: str = "127.0.0.1") -> Request:
        return Request({"type": "http", "client": (host, 4545), "method": "GET",
                        "path": "/api/instagram-stats", "headers": []})

    async def test_dashboard_includes_connected_accounts_only_no_secrets(self):
        cached = {"version": 1, "accounts": {
            "9": {"account_id": "id9", "username": "social", "followers": 20, "reels": []},
            "10": {"account_id": "deleted", "username": "orphan", "followers": 55},
        }}
        listed = {"instagram": [{"slot": 9, "id": "id9", "label": "social", "meta_app": 2}]}
        with (
            patch.object(stats, "_read", return_value=cached),
            patch.object(stats.store, "list_accounts", return_value=listed),
        ):
            payload = await stats.stats_status(self.local_request())
        self.assertEqual(len(payload["accounts"]), 1)
        self.assertEqual(payload["accounts"][0]["stats"]["followers"], 20)
        self.assertNotIn("access_token", json.dumps(payload))

    async def test_api_unavailable_from_non_local_address(self):
        with self.assertRaises(HTTPException) as caught:
            await stats.stats_status(self.local_request("192.0.2.25"))
        self.assertEqual(caught.exception.status_code, 403)
