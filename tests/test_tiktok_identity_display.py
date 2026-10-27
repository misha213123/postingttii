"""TikTok cards: distinguish saved AutoTok session from manually labeled profile."""
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from starlette.requests import Request
from app import store as store_module
from app.services import autotok_routes


def local_request():
    return Request({"type":"http","client":("127.0.0.1",1234),"method":"PUT",
                    "path":"/api/autotok/identity/1","headers":[]})


class TikTokHandleTests(unittest.IsolatedAsyncioTestCase):
    def test_valid_handle_or_profile_link(self):
        self.assertEqual(autotok_routes._normalize_tiktok_username("@example_123"),"example_123")
        self.assertEqual(autotok_routes._normalize_tiktok_username(
            "https://www.tiktok.com/@example.123?lang=en"),"example.123")
        self.assertEqual(autotok_routes._normalize_tiktok_username(""),"")
        with self.assertRaises(HTTPException):
            autotok_routes._normalize_tiktok_username("bad profile name")

    async def test_save_profile_label_without_changing_autotok_alias(self):
        account={"slot":1,"provider":"autotok","autotok_account":"account1","label":"account1"}
        with patch.object(autotok_routes.store,"get",return_value=account), \
             patch.object(autotok_routes.store,"save") as save:
            result=await autotok_routes.set_tiktok_identity(
                1, autotok_routes.TikTokIdentityRequest(username="@real_profile"),
                local_request())
        self.assertEqual(result["source"],"manual")
        self.assertFalse(result.get("verified",False))
        args=save.call_args.args
        self.assertEqual(args[2]["autotok_account"],"account1")
        self.assertEqual(args[2]["tiktok_username"],"real_profile")

    def test_safe_account_list_exposes_manual_handle(self):
        dummy={"tiktok":[{"slot":1,"provider":"autotok","autotok_account":"account1",
                         "label":"account1","tiktok_username":"real_profile"}],
               "youtube":[],"instagram":[]}
        with patch.object(store_module.store,"_read",return_value=dummy):
            view=store_module.store.list_accounts()["tiktok"][0]
        self.assertEqual(view["tiktok_username"],"real_profile")
        self.assertEqual(view["autotok_account"],"account1")
        self.assertEqual(view["tiktok_username_source"],"manual")
        self.assertNotIn("access_token",view)


if __name__=="__main__":
    unittest.main()
