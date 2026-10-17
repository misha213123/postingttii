"""Regression tests for unlimited Meta 2 accounts and no-number Edge connection."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app import main


def local_request(host: str = "127.0.0.1") -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/api/instagram/meta2/connect",
        "scheme": "http", "headers": [], "query_string": b"",
        "client": (host, 45000), "server": ("127.0.0.1", 8765),
    })


class Meta2UnlimitedTests(unittest.IsolatedAsyncioTestCase):
    async def test_slots_auto_increment_without_manual_numbers_or_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            conf = SimpleNamespace(
                data_dir=Path(folder), port=8765,
                instagram_client_id_2="second", instagram_client_secret_2="secret",
            )
            with (
                patch.object(main, "settings", conf),
                patch.object(main.store, "list_accounts", return_value={
                    "instagram": [{"slot": 4, "id": "old1"}, {"slot": 8, "id": "old2"}],
                }),
                patch.object(main, "open_instagram_edge", return_value={"ok": True}) as opener,
            ):
                self.assertEqual((await main.connect_new_meta2(local_request()))["ok"], True)
                self.assertEqual(opener.call_args.args[0], 9)
                self.assertEqual(opener.call_args.kwargs["meta_app"], 2)
                self.assertEqual((await main.connect_new_meta2(local_request()))["ok"], True)
                self.assertEqual(opener.call_args.args[0], 10)
                # No reusing identifiers even when old account is disconnected.
                self.assertEqual(
                    (Path(folder) / "instagram_meta2_last_slot.txt").read_text(), "10",
                )

    async def test_existing_dynamic_account_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            conf = SimpleNamespace(
                data_dir=Path(folder), port=8765,
                instagram_client_id_2="second", instagram_client_secret_2="secret",
            )
            with (
                patch.object(main, "settings", conf),
                patch.object(main.store, "list_accounts", return_value={
                    "instagram": [{"slot": 4, "id": "one"}, {"slot": 21, "id": "other"}],
                }),
                patch.object(main, "open_instagram_edge", return_value={"ok": True}) as opener,
            ):
                await main.connect_new_meta2(local_request())
                self.assertEqual(opener.call_args.args[0], 22)

    async def test_meta2_connect_is_local_only(self):
        with self.assertRaises(HTTPException) as error:
            await main.connect_new_meta2(local_request("203.0.113.6"))
        self.assertEqual(error.exception.status_code, 403)

    async def test_second_meta_credentials_are_required(self):
        with patch.object(
            main, "settings",
            SimpleNamespace(instagram_client_id_2="", instagram_client_secret_2=""),
        ):
            with self.assertRaises(HTTPException) as error:
                await main.connect_new_meta2(local_request())
        self.assertEqual(error.exception.status_code, 400)

    async def test_covers_include_dynamically_added_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(main, "COVER_DIR", Path(folder)),
                patch.object(main.store, "list_accounts", return_value={
                    "instagram": [{"slot": 3}, {"slot": 9}, {"slot": 17}],
                }),
            ):
                (Path(folder) / "17.jpg").write_bytes(b"test")
                result = await main.list_instagram_covers()
                self.assertEqual(result["covers"]["17"], True)
                self.assertEqual(result["covers"]["9"], False)
                self.assertIn("3", result["covers"])

    async def test_shared_edge_browser_profile_for_new_meta2_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            conf = SimpleNamespace(
                data_dir=Path(folder), port=8765,
                instagram_client_id_2="second", instagram_client_secret_2="secret",
            )
            with (
                patch.object(main, "settings", conf),
                patch.object(main, "_edge_executable", return_value="msedge.exe"),
                patch.object(main.subprocess, "Popen") as launch,
            ):
                for slot in (9, 10, 27):
                    main.open_instagram_edge(slot, local_request(), mode="connect", meta_app=2)
                    command = launch.call_args.args[0]
                    self.assertIn(
                        f"--user-data-dir={Path(folder) / 'edge_instagram_profiles' / 'meta_2_shared'}",
                        command,
                    )
                    self.assertEqual(
                        command[-1],
                        f"http://127.0.0.1:8765/connect/instagram/{slot}?meta_app=2",
                    )


if __name__ == "__main__":
    unittest.main()
