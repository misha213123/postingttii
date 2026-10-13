"""Unit tests for optional Tor Browser OAuth configuration (no Tor process needed)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.instagram_tor_oauth import (
    TorOAuthBrowserError,
    _tor_installation,
    tor_oauth_enabled,
)


class TorOAuthConfigurationTests(unittest.TestCase):
    def _fake_installation(self, directory: Path) -> Path:
        root = directory / "Tor Browser"
        (root / "Browser" / "TorBrowser" / "Data" / "Browser" / "profile.default").mkdir(
            parents=True
        )
        (root / "Browser" / "firefox.exe").touch()
        return root

    def test_tor_is_opt_in(self) -> None:
        with patch.dict(os.environ, {"INSTAGRAM_OAUTH_BROWSER": "default"}):
            self.assertFalse(tor_oauth_enabled())
        with patch.dict(os.environ, {"INSTAGRAM_OAUTH_BROWSER": "tor"}):
            self.assertTrue(tor_oauth_enabled())

    def test_separate_installations_per_slot(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            slot1 = self._fake_installation(Path(base) / "slot1")
            slot2 = self._fake_installation(Path(base) / "slot2")
            with patch.dict(os.environ, {
                "INSTAGRAM_TOR_BROWSER_1": str(slot1),
                "INSTAGRAM_TOR_BROWSER_2": str(slot2),
            }):
                root1, exe1 = _tor_installation(1)
                root2, exe2 = _tor_installation(2)
                self.assertNotEqual(root1, root2)
                self.assertEqual(exe1, slot1 / "Browser" / "firefox.exe")
                self.assertEqual(exe2, slot2 / "Browser" / "firefox.exe")

    def test_rejects_shared_installation(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            root = self._fake_installation(Path(base))
            with patch.dict(os.environ, {
                "INSTAGRAM_TOR_BROWSER_1": str(root),
                "INSTAGRAM_TOR_BROWSER_2": str(root / "Browser" / "firefox.exe"),
            }):
                with self.assertRaisesRegex(TorOAuthBrowserError, "отдельная"):
                    _tor_installation(1)

    def test_rejects_missing_browser(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            with patch.dict(os.environ, {
                "INSTAGRAM_TOR_BROWSER_1": str(Path(base) / "missing"),
                "INSTAGRAM_TOR_BROWSER_2": "",
            }):
                with self.assertRaises(TorOAuthBrowserError):
                    _tor_installation(1)


if __name__ == "__main__":
    unittest.main()
