"""Regression tests for accurate counters and conservative platform pauses."""
import unittest

from app.services.batch_guard import batch_counts, platform_limit_reason, FINISHED_STATES


class BatchGuardTests(unittest.TestCase):
    def test_counts_do_not_treat_errors_as_success(self):
        items = [
            {"status": "done"} for _ in range(13)
        ] + [
            {"status": "error"} for _ in range(11)
        ] + [
            {"status": "queued"} for _ in range(288)
        ]
        counts = batch_counts(items)
        self.assertEqual(counts["successful_videos"], 13)
        self.assertEqual(counts["failed_videos"], 11)
        self.assertEqual(counts["completed_videos"], 24)
        self.assertEqual(counts["blocked_videos"], 0)

    def test_success_error_blocked_and_already_are_distinct(self):
        counts = batch_counts([
            {"status": "done"},
            {"status": "already"},
            {"status": "error"},
            {"status": "blocked"},
            {"status": "queued"},
        ])
        self.assertEqual(counts, {
            "successful_videos": 1,
            "already_videos": 1,
            "failed_videos": 1,
            "blocked_videos": 1,
            "completed_videos": 4,
        })
        self.assertIn("blocked", FINISHED_STATES)
        self.assertNotIn("queued", FINISHED_STATES)

    def test_youtube_explicit_upload_restriction(self):
        self.assertIsNotNone(platform_limit_reason(
            "youtube:1",
            "YouTube init upload: HTTP 400: 400 | The user has exceeded the number of videos they may upload.",
        ))
        self.assertIsNone(platform_limit_reason(
            "youtube:1", "YouTube init upload: HTTP 503: internal error"
        ))
        self.assertIsNone(platform_limit_reason(
            "instagram:1", "YouTube: uploadLimitExceeded"
        ))

    def test_instagram_explicit_action_restriction(self):
        self.assertIsNotNone(platform_limit_reason(
            "instagram:3",
            "Instagram publish Reel: HTTP 400: 9 | User is performing too many actions",
        ))
        self.assertIsNone(platform_limit_reason(
            "instagram:3", "Instagram #3: ERROR (container 123), попытка 3/3"
        ))
        self.assertIsNone(platform_limit_reason(
            "tiktok:1", "YouTube: the user has exceeded the number of videos they may upload"
        ))


if __name__ == "__main__":
    unittest.main()
