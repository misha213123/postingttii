"""Instagram Studio download cache must never retain successful originals."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import instagram_studio as ig


class InstagramImportSourceCleanupTests(unittest.TestCase):
    def test_original_removed_after_output_is_saved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = root / "ig_123.mp4"
            output = root / "export" / "ig_123.mp4"
            output.parent.mkdir()
            original.write_bytes(b"downloaded-source")
            output.write_bytes(b"rendered-result")
            self.assertTrue(ig.cleanup_instagram_download(original, output))
            self.assertFalse(original.exists())
            self.assertEqual(output.read_bytes(), b"rendered-result")

    def test_if_output_equals_original_file_it_is_not_removed(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "ig_123.mp4"
            source.write_bytes(b"sole-copy")
            self.assertFalse(ig.cleanup_instagram_download(source, source))
            self.assertEqual(source.read_bytes(), b"sole-copy")

    def test_windows_file_lock_preserves_recorded_success(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "ig_123.mp4"
            source.write_bytes(b"source")
            rendered = Path(td) / "finished.mp4"
            rendered.write_bytes(b"finished")
            with patch.object(Path, "unlink", side_effect=PermissionError("in use")), \
                 patch.object(ig, "JOB", {"cleanup_warnings": []}):
                self.assertFalse(ig.cleanup_instagram_download(source, rendered))
                self.assertTrue(ig.JOB["cleanup_warnings"])
            self.assertTrue(source.exists())
            self.assertTrue(rendered.exists())


if __name__ == "__main__":
    unittest.main()
