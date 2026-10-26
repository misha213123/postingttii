"""Instagram narrow-window Create popup: Post / Live video / Ad.

The real UI can render Post as a plain text node rather than a menuitem.
Always select that Post before searching for an upload input.
"""
import unittest
from pathlib import Path

from app import browser_instagram as browser


class Node:
    def __init__(self, page, name="missing"):
        self.page = page
        self.name = name

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    async def is_visible(self):
        if self.name == "live":
            return self.page.menu_open
        if self.name == "post":
            return self.page.menu_open and self.page.has_post
        return False

    async def click(self, **kwargs):
        if self.name == "post" and await self.is_visible():
            self.page.menu_open = False
            self.page.post_chosen = True
            self.page.actions.append("post_click")
            return
        raise TimeoutError("not clickable")

    async def wait_for(self, **kwargs):
        if self.name == "file" and self.page.post_chosen:
            return
        raise TimeoutError("not attached")

    async def set_input_files(self, value, **kwargs):
        if not self.page.post_chosen:
            raise AssertionError("input tried before Post")
        self.page.actions.append("upload")
        self.page.uploaded = value


class NarrowInstagramPage:
    def __init__(self, has_post=True):
        self.url = "https://www.instagram.com/"
        self.menu_open = True
        self.has_post = has_post
        self.post_chosen = False
        self.uploaded = None
        self.actions = []

    def locator(self, selector):
        if 'input[type="file"]' in selector:
            return Node(self, "file")
        return Node(self)

    def get_by_text(self, text, exact=False):
        if text == "Live video" and exact:
            return Node(self, "live")
        if text == "Post" and exact:
            return Node(self, "post")
        return Node(self)

    def get_by_role(self, kind, name=None):
        return Node(self)


class CompactCreatePopupTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_plain_text_clicked_before_mp4_upload(self):
        page = NarrowInstagramPage()
        await browser._choose_instagram_video(page, Path("clip.mp4"))
        self.assertEqual(page.actions, ["post_click", "upload"])
        self.assertEqual(page.uploaded, "clip.mp4")
        self.assertFalse(page.menu_open)

    async def test_missing_post_gives_actionable_error(self):
        page = NarrowInstagramPage(has_post=False)
        with self.assertRaises(RuntimeError) as error:
            await browser._choose_instagram_video(page, Path("clip.mp4"))
        self.assertIn("Post", str(error.exception))
        self.assertEqual(browser._brief_error(error.exception), str(error.exception))
        self.assertEqual(page.actions, [])
        self.assertIsNone(page.uploaded)


if __name__ == "__main__":
    unittest.main()
