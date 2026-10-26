"""Checks for 9:16 Instagram browser upload and preselected per-video covers."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, UploadFile
from PIL import Image
from starlette.datastructures import Headers
from starlette.requests import Request

from app import browser_instagram as browser


def request(method: str = "POST") -> Request:
    return Request({
        "type": "http", "client": ("127.0.0.1", 57231),
        "method": method, "path": "/api/browser-instagram/cover",
        "headers": [(b"origin", f"http://127.0.0.1:{browser.settings.port}".encode())],
    })


class BrowserCoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_cover_is_scaled_to_1080_by_1920_and_kept_on_disk(self):
        picture = Image.new("RGB", (540, 960), (52, 82, 118))
        buffer = io.BytesIO()
        picture.save(buffer, format="PNG")
        upload = UploadFile(
            filename="cover.png", file=io.BytesIO(buffer.getvalue()),
            headers=Headers({"content-type": "image/png"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(browser, "COVERS_DIR", Path(directory)):
                response = await browser.upload_cover(request(), upload)
                self.assertTrue(response["ok"])
                self.assertEqual((response["width"], response["height"]), (1080, 1920))
                saved = browser._cover_path(response["cover_id"])
                with Image.open(saved) as checked:
                    self.assertEqual(checked.size, (1080, 1920))
                self.assertIn(response["cover_id"], response["preview_url"])

    def test_cover_id_cannot_escape_local_folder(self):
        with self.assertRaises(HTTPException) as failure:
            browser._cover_path("../.env")
        self.assertEqual(failure.exception.status_code, 400)


class VerticalReelTests(unittest.IsolatedAsyncioTestCase):
    async def test_9_16_validation_accepts_vertical_and_rejects_square(self):
        good = SimpleNamespace(stdout=json.dumps(
            {"streams": [{"width": 1080, "height": 1920}]}
        ))
        bad = SimpleNamespace(stdout=json.dumps(
            {"streams": [{"width": 1080, "height": 1080}]}
        ))
        with patch.object(browser.subprocess, "run", return_value=good):
            self.assertEqual(browser._validate_vertical_reel(Path("reel.mp4")), (1080, 1920))
        with patch.object(browser.subprocess, "run", return_value=bad):
            with self.assertRaises(RuntimeError) as error:
                browser._validate_vertical_reel(Path("square.mp4"))
            self.assertIn("9:16", str(error.exception))

    async def test_9_16_crop_option_selected_before_next(self):
        seen = []
        class Locator:
            def __init__(self, label):
                self.label = label
            @property
            def first(self): return self
            @property
            def last(self): return self
            async def click(self, **kwargs):
                if self.label not in {"crop", "ratio"}:
                    raise TimeoutError("not found")
                seen.append(self.label)
        class Page:
            def get_by_role(self, kind, name=None):
                if kind == "button" and "Crop" in str(getattr(name, "pattern", "")):
                    return Locator("crop")
                return Locator("missing")
            def locator(self, selector):
                return Locator("missing")
            def get_by_text(self, name):
                if name.search("9:16"):
                    return Locator("ratio")
                return Locator("missing")
        await browser._select_vertical_crop(Page())
        self.assertEqual(seen, ["crop", "ratio"])

    async def test_cover_not_uploaded_as_second_media_input(self):
        uploaded = []
        class Locator:
            @property
            def last(self): return self
            async def click(self, **kwargs): pass
        class Chooser:
            async def set_files(self, path):
                uploaded.append(path)
        class EventInfo:
            @property
            def value(self):
                async def chosen():
                    return Chooser()
                return chosen()
        class EventManager:
            async def __aenter__(self): return EventInfo()
            async def __aexit__(self, *_): pass
        class Page:
            def get_by_role(self, kind, name):
                return Locator()
            def get_by_text(self, name):
                return Locator()
            def expect_file_chooser(self, **kwargs):
                return EventManager()
            def locator(self, selector):
                raise AssertionError("Cover must never set input[type=file] directly")
        await browser._set_instagram_cover(Page(), Path("selected_cover.jpg"))
        self.assertEqual(uploaded, ["selected_cover.jpg"])


if __name__ == "__main__":
    unittest.main()
