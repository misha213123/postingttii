"""Folder routing and per-account background regression tests."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app import main
from app import telegram_studio as tg
from app import instagram_studio as ig
from app import video_folders as folders


class FolderRegistryTests(unittest.TestCase):
    def local_request(self):
        return Request({"type": "http", "client": ("127.0.0.1", 1234),
                        "method": "POST", "path": "/api/video-folders", "headers": []})

    def test_register_and_resolve_custom_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            videos = parent / "clips"
            videos.mkdir()
            (videos / "one.mp4").write_bytes(b"one")
            with patch.object(folders, "CONFIG", parent / "settings.json"):
                result = folders.register_folder(
                    folders.NewFolder(label="Clips", path=str(videos)), self.local_request())
                target = folders.video_path(result["id"], "one.mp4")
                self.assertEqual(target, videos / "one.mp4")
                self.assertEqual([p.name for p in folders.list_videos(result["id"])], ["one.mp4"])
                with self.assertRaises(ValueError):
                    folders.video_path(result["id"], "../some.mp4")
                with self.assertRaises(ValueError):
                    folders.video_path(result["id"], "C:\\Windows\\file.mp4")
                duplicate = folders.register_folder(
                    folders.NewFolder(label="Alias", path=str(videos)), self.local_request())
                self.assertEqual(duplicate["id"], result["id"])

    def test_account_bindings_are_scoped_to_account_id_not_only_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            videos = parent / "clips"
            videos.mkdir()
            with (
                patch.object(folders, "CONFIG", parent / "settings.json"),
                patch.object(folders.store, "get", return_value={"id": "ig_account_A"}),
            ):
                registered = folders.register_folder(
                    folders.NewFolder(label="A", path=str(videos)), self.local_request())
                folders.bind_folder(14, folders.Binding(folder_id=registered["id"]), self.local_request())
                self.assertEqual(folders.account_folder(14), registered["id"])
                state = json.loads(folders.CONFIG.read_text(encoding="utf-8"))
                self.assertIn("14:ig_account_A", state["account_folders"])

    def test_reject_public_access_to_local_folder_paths(self):
        request = Request({"type": "http", "client": ("192.0.2.9", 1234),
                           "method": "GET", "path": "/api/video-folders", "headers": []})
        with self.assertRaises(HTTPException) as err:
            folders.folders(request)
        self.assertEqual(err.exception.status_code, 403)


class RenderFolderTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_account_background_overrides_global_shuffle(self):
        with tempfile.TemporaryDirectory() as temp:
            target_dir = Path(temp)
            options = tg.RenderRequest(
                limit_per_channel=1, output_folder="linked", target_slot=13,
            )
            async def fake_work(*args, **kwargs):
                return None
            def close_task(coroutine):
                coroutine.close()
                return MagicMock()
            with (
                patch.object(tg, "config", return_value={"render_design": {
                    "background_mode": "shuffle", "background_name": ""}}),
                patch.object(tg, "account_folder", return_value="registered_a"),
                patch.object(tg, "account_background", return_value="purple.mp4"),
                patch.object(tg, "folder_path", return_value=target_dir),
                patch.object(tg, "choose_backgrounds", return_value=[Path("purple.mp4")]) as backgrounds,
                patch.object(tg, "current_banner", return_value=None),
                patch.object(tg.asyncio, "create_task", side_effect=close_task),
            ):
                result = await tg.run(options)
            self.assertTrue(result["started"])
            chosen_design = backgrounds.call_args.args[0]
            self.assertEqual(chosen_design.background_mode, "single")
            self.assertEqual(chosen_design.background_name, "purple.mp4")

    async def test_instagram_account_render_folder_and_bg_preset(self):
        with tempfile.TemporaryDirectory() as temp:
            target_dir = Path(temp)
            def close_task(coroutine):
                coroutine.close()
                return MagicMock()
            with (
                patch.object(ig, "load", return_value={"accounts": ["https://www.instagram.com/demo/"], "links": []}),
                patch.object(ig, "current_layout", return_value=ig.InstagramLayout()),
                patch.object(ig, "account_folder", return_value="linked_account"),
                patch.object(ig, "account_background", return_value="purple.mp4"),
                patch.object(ig, "folder_path", return_value=target_dir),
                patch.object(ig, "backgrounds", return_value=[{"enabled": True, "name": "purple.mp4"}]),
                patch.object(ig, "choose_instagram_background", return_value=Path("purple.mp4")) as choose,
                patch.object(ig.asyncio, "create_task", side_effect=close_task),
            ):
                result = await ig.run(ig.Run(limit_per_account=1, output_folder="linked", target_slot=13))
            self.assertTrue(result["started"])
            self.assertEqual(choose.call_args.args[0].background_name, "purple.mp4")


class BatchFolderTests(unittest.IsolatedAsyncioTestCase):
    async def test_linked_folder_batch_assigns_per_account_video_and_retains_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            folder_a = Path(temp) / "a"
            folder_b = Path(temp) / "b"
            folder_a.mkdir()
            folder_b.mkdir()
            (folder_a / "clip-a.mp4").write_bytes(b"account-A")
            (folder_b / "clip-b.mp4").write_bytes(b"account-B")
            def close_task(coroutine):
                coroutine.close()
                return MagicMock()
            account_list = {"youtube": [], "tiktok": [],
                            "instagram": [{"slot": 11}, {"slot": 12}]}
            with (
                patch.object(main, "_active_batch_jobs", return_value=[]),
                patch.object(main.store, "list_accounts", return_value=account_list),
                patch.object(main, "account_folder", side_effect=lambda slot: "a" if slot == 11 else "b"),
                patch.object(main, "list_videos", side_effect=lambda folder: list((folder_a if folder == "a" else folder_b).glob("*.mp4"))),
                patch.object(main, "load_instagram_caption_templates", return_value=["caption"]),
                patch.object(main, "_save_batch_job"),
                patch.object(main.asyncio, "create_task", side_effect=close_task),
            ):
                result = await main.batch_start(main.BatchPublishRequest(
                    filenames=[], targets=["instagram:11", "instagram:12"],
                    source_folder="linked",
                ))
            job = main.BATCH_JOBS.pop(result["job_id"])
            self.assertEqual(len(job["items"]), 2)
            pairs = {(item["source_folder"], next(iter(item["targets"])))
                     for item in job["items"]}
            self.assertEqual(pairs, {("a", "instagram:11"), ("b", "instagram:12")})
            self.assertEqual(job["request"]["source_folder"], "linked")

    async def test_shared_source_folder_distributes_distinct_files_fairly(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            for i in range(6):
                (source / f"{i}.mp4").write_bytes(f"video {i}".encode())
            def close_task(coroutine):
                coroutine.close()
                return MagicMock()
            with (
                patch.object(main, "_active_batch_jobs", return_value=[]),
                patch.object(main.store, "list_accounts", return_value={
                    "youtube": [], "tiktok": [],
                    "instagram": [{"slot": 9}, {"slot": 10}],
                }),
                patch.object(main, "account_folder", return_value="shared"),
                patch.object(main, "list_videos", return_value=sorted(source.glob("*.mp4"))),
                patch.object(main, "load_instagram_caption_templates", return_value=["caption"]),
                patch.object(main, "_save_batch_job"),
                patch.object(main.asyncio, "create_task", side_effect=close_task),
            ):
                response = await main.batch_start(main.BatchPublishRequest(
                    filenames=[], targets=["instagram:9", "instagram:10"], source_folder="linked",
                ))
            job = main.BATCH_JOBS.pop(response["job_id"])
            self.assertEqual(len(job["items"]), 6)
            assigned = [next(iter(item["targets"])) for item in job["items"]]
            self.assertEqual(assigned.count("instagram:9"), 3)
            self.assertEqual(assigned.count("instagram:10"), 3)
            self.assertEqual(len({item["filename"] for item in job["items"]}), 6)

    async def test_linked_mode_never_uses_tiktok_or_youtube(self):
        with (
            patch.object(main, "_active_batch_jobs", return_value=[]),
            patch.object(main.store, "list_accounts", return_value={
                "instagram": [], "youtube": [{"slot": 1}], "tiktok": [],
            }),
        ):
            with self.assertRaises(HTTPException) as error:
                await main.batch_start(main.BatchPublishRequest(
                    filenames=[], targets=["youtube:1"], source_folder="linked",
                ))
        self.assertEqual(error.exception.status_code, 400)
