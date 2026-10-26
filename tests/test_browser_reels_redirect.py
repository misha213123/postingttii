"""Reels feed must never be confused with Instagram Create -> Post upload."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from app import browser_instagram as browser


class Control:
    def __init__(self, page, role="missing"):
        self.page = page
        self.role = role

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    async def click(self, **_):
        if self.role == "reels_redirect":
            self.page.url = "https://www.instagram.com/reels/"
        elif self.role == "post":
            self.page.post_selected = True
        else:
            raise TimeoutError("no such action")

    async def is_visible(self):
        return self.role == "post" and self.page.menu_open

    async def wait_for(self, **_):
        if not self.page.post_selected and not self.page.initial_file:
            raise TimeoutError("upload field not mounted")

    async def set_input_files(self, path, **_):
        if not self.page.post_selected:
            raise TimeoutError("no Create Post form yet")
        self.page.selected_files.append(path)


class ReelsRedirectPage:
    def __init__(self, *, redirect_from_create=False, start_in_reels=False):
        self.url = ("https://www.instagram.com/reels/" if start_in_reels
                    else "https://www.instagram.com/")
        self.redirect_from_create = redirect_from_create
        self.initial_file = False
        self.post_selected = False
        self.menu_open = False
        self.selected_files = []
        self.visited = []
        self.reels_text_was_clicked = False

    def locator(self, selector):
        if selector == 'input[type="file"]' or 'input[type="file"]' in selector:
            return Control(self, "file")
        if selector.startswith('svg[aria-label=') and self.redirect_from_create:
            return Control(self, "reels_redirect")
        return Control(self)

    def get_by_role(self, role, name=None):
        if role == "menuitem" and name and name.search("Post"):
            return Control(self, "post")
        return Control(self)

    def get_by_text(self, name):
        if hasattr(name, "search") and name.search("Reels"):
            self.reels_text_was_clicked = True
        return Control(self)

    async def goto(self, url, **_):
        self.visited.append(url)
        self.url = url


class ReelsRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_reels_feed_blocks_upload_without_navigating_to_fake_create_url(self):
        page = ReelsRedirectPage(start_in_reels=True)
        with self.assertRaises(RuntimeError) as error:
            await browser._choose_instagram_video(page, Path("reel.mp4"))
        self.assertIn("Reels", str(error.exception))
        self.assertEqual(page.selected_files, [])
        self.assertEqual(page.visited, [])
        self.assertFalse(page.reels_text_was_clicked)

    async def test_create_icon_redirect_to_feed_is_rejected_without_profile_url(self):
        page = ReelsRedirectPage(redirect_from_create=True)
        with self.assertRaises(RuntimeError) as error:
            await browser._open_instagram_create(page)
        self.assertIn("Reels", str(error.exception))
        self.assertEqual(page.visited, [])

    async def test_create_and_upload_never_click_global_reels_link(self):
        page = ReelsRedirectPage()
        page.menu_open = True
        await browser._choose_instagram_video(page, Path("uploaded.mp4"))
        self.assertFalse(page.reels_text_was_clicked)
        self.assertEqual(page.visited, [])
        self.assertEqual(page.selected_files, ["uploaded.mp4"])


if __name__ == "__main__":
    unittest.main()
