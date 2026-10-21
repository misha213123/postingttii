"""Regression tests for dashboard loading and repeated account removal."""
from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app import main
from app.publish_state import PublishStateStore


class ContentHashCacheTests(unittest.TestCase):
    def setUp(self):
        PublishStateStore._cached_file_hash.cache_clear()

    def tearDown(self):
        PublishStateStore._cached_file_hash.cache_clear()

    def test_unchanged_mp4_is_hashed_once_across_status_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "reel.mp4"
            video.write_bytes(b"test video data")
            with patch.object(hashlib, "sha256", wraps=hashlib.sha256) as new_hash:
                self.assertEqual(PublishStateStore.video_key(video),
                                 hashlib.sha256(b"test video data").hexdigest())
                self.assertEqual(PublishStateStore.video_key(video),
                                 hashlib.sha256(b"test video data").hexdigest())
                # One hash for the file and two calls from the test assertion.
                self.assertEqual(new_hash.call_count, 3)
                stats = PublishStateStore._cached_file_hash.cache_info()
                self.assertGreaterEqual(stats.hits, 1)

    def test_changed_video_gets_new_hash_not_stale_status(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "reel.mp4"
            video.write_bytes(b"original")
            before = PublishStateStore.video_key(video)
            time.sleep(.01)
            video.write_bytes(b"modified media")
            after = PublishStateStore.video_key(video)
            self.assertNotEqual(before, after)
            self.assertEqual(after, hashlib.sha256(b"modified media").hexdigest())


class DashboardStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_reads_history_and_accounts_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "reel.mp4"
            video.write_bytes(b"clip")
            digest = hashlib.sha256(b"clip").hexdigest()
            past = {"targets": {"instagram:14": {"completed_at": int(time.time())}}}
            history = {digest: past}
            accounts = {"instagram": [{"slot": 14}], "youtube": [], "tiktok": []}
            settings = replace(main.settings, upload_dir=Path(folder))
            with (
                patch.object(main, "settings", settings),
                patch.object(main.store, "list_accounts", return_value=accounts) as list_accounts,
                patch.object(main.publish_state, "_read", return_value=history) as read,
                patch.object(main.autotok_bridge, "executable", return_value=None),
            ):
                result = await main.status()
            self.assertEqual(list_accounts.call_count, 1)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(result["videos"][0]["completed_targets"], ["instagram:14"])
            self.assertEqual(result["accounts"], accounts)
            self.assertIn("instagram:14", result["cooldowns"])

    async def test_delete_is_idempotent_when_account_was_removed(self):
        with (
            patch.object(main.store, "get", return_value=None),
            patch.object(main.store, "delete") as delete,
        ):
            result = await main.disconnect("instagram", 14)
        self.assertEqual(result, {"ok": True, "already_disconnected": True})
        delete.assert_not_called()

    async def test_existing_account_is_removed_only_once(self):
        with (
            patch.object(main.store, "get", side_effect=[{"slot": 14}, None]),
            patch.object(main.store, "delete") as delete,
        ):
            first = await main.disconnect("instagram", 14)
            second = await main.disconnect("instagram", 14)
        self.assertTrue(first["ok"])
        self.assertFalse(first["already_disconnected"])
        self.assertTrue(second["already_disconnected"])
        delete.assert_called_once_with("instagram", 14)
