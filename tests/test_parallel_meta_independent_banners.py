"""Parallel Meta publishing and independent banner configuration regression tests."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import UploadFile
from PIL import Image

from app import main
from app import instagram_studio as studio
from app import telegram_studio as tg


class ParallelInstagramTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_instagram_accounts_can_publish_in_same_wave(self):
        """Distinct Meta slots no longer contend on one global semaphore."""
        started = set()
        both = asyncio.Event()

        async def fake_upload(slot, video_url, caption, cover_url=None):
            started.add(slot)
            if len(started) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), timeout=1.0)
            return {"id": "media-" + str(slot)}

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            video = base / "source.mp4"
            video.write_bytes(b"test source")
            for slot in (11, 12):
                (base / f"{slot}.jpg").write_bytes(b"jpeg")
            with (
                patch.object(main, "COVER_DIR", base),
                patch.object(main.store, "get", return_value={"username": "test"}),
                patch.object(main.publish_state, "video_key", return_value="digest"),
                patch.object(main.publish_state, "is_completed", return_value=False),
                patch.object(main.publish_state, "cooldown_remaining", return_value=0),
                patch.object(main.publish_state, "mark_completed"),
                patch.object(main, "instagram_caption", return_value="caption"),
                patch.object(main, "instagram_upload", new=AsyncMock(side_effect=fake_upload)),
                patch.object(main, "_expire_media_token", new=AsyncMock()),
                patch.dict(main.INSTAGRAM_META_LOCKS, {}, clear=True),
            ):
                results = await asyncio.wait_for(asyncio.gather(
                    main._publish_single_target(video, "A", "instagram:11"),
                    main._publish_single_target(video, "B", "instagram:12"),
                ), timeout=2.0)
        self.assertEqual(started, {11, 12})
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item["ok"] for item in results))

    async def test_same_instagram_account_is_serialized(self):
        active = 0
        max_active = 0
        async def fake_upload(slot, video_url, caption, cover_url=None):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.03)
            active -= 1
            return {"id": "id"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "11.jpg").write_bytes(b"image")
            video = root / "video.mp4"
            video.write_bytes(b"test")
            with (
                patch.object(main, "COVER_DIR", root),
                patch.object(main.store, "get", return_value={"username": "test"}),
                patch.object(main.publish_state, "video_key", return_value="digest"),
                patch.object(main.publish_state, "is_completed", return_value=False),
                patch.object(main.publish_state, "cooldown_remaining", return_value=0),
                patch.object(main.publish_state, "mark_completed"),
                patch.object(main, "instagram_caption", return_value="caption"),
                patch.object(main, "instagram_upload", new=AsyncMock(side_effect=fake_upload)),
                patch.object(main, "_expire_media_token", new=AsyncMock()),
                patch.dict(main.INSTAGRAM_META_LOCKS, {}, clear=True),
            ):
                await asyncio.gather(
                    main._publish_single_target(video, "A", "instagram:11"),
                    main._publish_single_target(video, "B", "instagram:11"),
                )
        self.assertEqual(max_active, 1)


class SeparateBannerTests(unittest.IsolatedAsyncioTestCase):
    def test_video_banner_geometry_is_independent_of_telegram_circle(self):
        tg_layout = tg.RenderDesign(banner_enabled=True, banner_width=470,
                                    banner_x=120, banner_y=70)
        layout = studio.InstagramLayout(
            banner_enabled=True, banner_width=760, banner_x=230, banner_y=870,
        )
        with patch.object(studio, "saved_design", return_value=tg_layout):
            normal = studio.video_banner_design(layout)
        self.assertEqual((normal.banner_width, normal.banner_x, normal.banner_y),
                         (760, 230, 870))
        self.assertEqual(tg_layout.banner_width, 470)

    def test_fractional_banner_drag_coordinates_round_to_integer_pixels(self):
        layout = studio.InstagramLayout.model_validate({
            "banner_x": 209.40005012511728,
            "banner_y": 1213.200232388847,
            "banner_width": 539.8,
        })
        self.assertEqual((layout.banner_x, layout.banner_y, layout.banner_width),
                         (209, 1213, 540))
        self.assertIsInstance(layout.banner_x, int)
        self.assertIsInstance(layout.banner_y, int)

    def test_legacy_null_banner_fields_and_geometry_limits_still_validate(self):
        from pydantic import ValidationError
        legacy = studio.InstagramLayout()
        self.assertIsNone(legacy.banner_x)
        self.assertIsNone(legacy.banner_y)
        self.assertIsNone(legacy.banner_width)
        with self.assertRaises(ValidationError):
            studio.InstagramLayout.model_validate({"banner_x": 1081.5})
        with self.assertRaises(ValidationError):
            studio.InstagramLayout.model_validate({"banner_width": 50.4})

    def test_previous_video_layout_migrates_without_disabling_existing_banner(self):
        with patch.object(studio, "saved_design", return_value=tg.RenderDesign(
            banner_enabled=True, banner_width=500,
        )):
            inherited = studio.video_banner_design(studio.InstagramLayout())
        self.assertTrue(inherited.banner_enabled)
        self.assertEqual(inherited.banner_width, 500)

    async def test_video_banner_upload_does_not_replace_circle_banner(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ad_folder = root / "video_banners"
            ad_folder.mkdir()
            source = root / "ig_sources.json"
            image = Image.new("RGBA", (100, 200), "#597aba")
            raw = BytesIO()
            image.save(raw, "PNG")
            raw.seek(0)
            with (
                patch.object(studio, "VIDEO_BANNERS", ad_folder),
                patch.object(studio, "CONFIG", source),
                patch.dict(studio.JOB, {"status": "idle"}),
            ):
                result = await studio.upload_video_banner(UploadFile(
                    filename="ad.png", file=raw,
                ))
                self.assertTrue(result["available"])
                saved = studio.load()
                self.assertTrue(saved["banner_file"].endswith(".png"))
                self.assertTrue(studio.video_banner_file().exists())
                self.assertTrue(studio.video_banner_design().banner_enabled)

    async def test_regular_telegram_uses_video_banner_not_circle_banner(self):
        normal = studio.InstagramLayout(banner_enabled=True, banner_width=710)
        inherited = tg.RenderDesign(banner_enabled=True, banner_width=360)
        with (
            patch.object(studio, "video_banner_file", return_value=Path("video_banner.png")),
            patch.object(studio, "video_banner_design", return_value=tg.RenderDesign(
                banner_enabled=True, banner_width=710,
            )),
        ):
            design = studio.video_banner_design(normal)
        self.assertEqual(design.banner_width, 710)
        self.assertEqual(inherited.banner_width, 360)
