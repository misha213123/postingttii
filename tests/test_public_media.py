"""Regression tests: Meta URLs must stay valid after app restart and archiving."""
from __future__ import annotations

import secrets
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services import public_media


class MediaRegistryTests(unittest.TestCase):
    def test_token_survives_restart_and_source_archiving(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            inbox = root / "videos" / "inbox"
            posted = root / "videos" / "posted"
            data = root / "data"
            for folder in (inbox, posted, data):
                folder.mkdir(parents=True, exist_ok=True)
            fake = SimpleNamespace(upload_dir=inbox, posted_dir=posted, data_dir=data)
            video = inbox / "test.mp4"
            video.write_bytes(b"local-test-mp4")
            token = secrets.token_urlsafe(24)

            with patch.object(public_media, "settings", fake):
                registry = public_media.PersistentMediaRegistry()
                registry[token] = video
                staged = registry.get(token)
                self.assertIsNotNone(staged)
                self.assertEqual(staged.read_bytes(), b"local-test-mp4")

                # Moving the original cannot invalidate a file already
                # submitted to Meta: it has its own staged path.
                video.rename(posted / video.name)
                reloaded = public_media.PersistentMediaRegistry()
                self.assertEqual(reloaded.get(token).read_bytes(), b"local-test-mp4")
                self.assertIsNone(reloaded.get("../../index.json"))
                self.assertNotIn(video, reloaded.values())

                reloaded.pop(token)
                self.assertIsNone(reloaded.get(token))
                self.assertFalse(staged.exists())

    def test_rejects_files_outside_owned_media_dirs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            inbox = root / "inbox"
            posted = root / "posted"
            data = root / "data"
            for folder in (inbox, posted, data):
                folder.mkdir(parents=True, exist_ok=True)
            foreign = root / "private.mp4"
            foreign.write_bytes(b"not allowed")
            fake = SimpleNamespace(upload_dir=inbox, posted_dir=posted, data_dir=data)
            with patch.object(public_media, "settings", fake):
                registry = public_media.PersistentMediaRegistry()
                with self.assertRaises(ValueError):
                    registry[secrets.token_urlsafe(24)] = foreign
                self.assertIsNone(registry.get("invalid"))


if __name__ == "__main__":
    unittest.main()
