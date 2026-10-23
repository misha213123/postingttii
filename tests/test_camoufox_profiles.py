"""Tests for local Camoufox integration without launching a real browser."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.account_creator.services import camoufox_proxy
from app.account_creator.services.instagram_service import profile_directory


class CamoufoxConfigTests(unittest.TestCase):
    def test_supported_proxy_schemes(self):
        self.assertEqual(
            camoufox_proxy._check_server("http://example.net:8080"),
            "http://example.net:8080",
        )
        self.assertEqual(
            camoufox_proxy._check_server("socks5://example.net:1080"),
            "socks5://example.net:1080",
        )

    def test_reject_embedded_credentials_and_invalid_ports(self):
        for server in (
            "http://user:password@example.net:8080",
            "ftp://example.net:21",
            "http://example.net:99999",
            "http://example.net",
            "http://example.net:8080/a",
        ):
            with self.subTest(server=server):
                with self.assertRaises(ValueError):
                    camoufox_proxy._check_server(server)

    def test_secret_config_encrypted_and_public_masked(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            with patch.object(camoufox_proxy, "KEY_PATH", base / "key"), \
                 patch.object(camoufox_proxy, "CONFIG_PATH", base / "config.enc"):
                data = {"17": {
                    "server": "socks5://proxy.example:1080",
                    "username": "tester",
                    "password": "super-secret",
                }}
                camoufox_proxy._save(data)
                self.assertNotIn(b"super-secret", (base / "config.enc").read_bytes())
                self.assertEqual(camoufox_proxy._load(), data)
                public = camoufox_proxy.public_config(17)
                self.assertTrue(public["password_set"])
                self.assertNotIn("password", public)

    def test_camoufox_separate_from_legacy_edge_directory(self):
        path = profile_directory(42)
        self.assertTrue(str(path).endswith("camoufox_profiles" + str(Path("/")) + "account_42")
                        or path.name == "account_42")
        self.assertIn("camoufox_profiles", path.parts)


if __name__ == "__main__":
    unittest.main()
