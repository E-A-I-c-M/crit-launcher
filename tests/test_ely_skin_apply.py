"""Regression tests for Ely.by and Microsoft skin-apply flows."""

import base64
import struct
import sys
import types
import unittest
import zlib
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crit_launcher.accounts.ely import _ELY_SKIN_CACHE
from crit_launcher.core.images import png_uri
from crit_launcher.skins import api as skins_api
from crit_launcher.skins import ely as ely_skins


def _png(width=64, height=64):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    row = b"\x00" + bytes((0x24, 0x78, 0xD1, 0xFF)) * width
    image = b"\x89PNG\r\n\x1a\n"
    image += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    image += chunk(b"IDAT", zlib.compress(row * height))
    image += chunk(b"IEND", b"")
    return image


class _FakeEvent:
    def __init__(self):
        self.callbacks = []

    def __iadd__(self, callback):
        self.callbacks.append(callback)
        return self

    def fire(self):
        for callback in self.callbacks:
            callback()


class _FakeWindow:
    def __init__(self, *, probe_results=None, upload_results=None, model_results=None,
                 wear_results=None, confirm=True, url=ely_skins.ELY_SKINS_PAGE):
        self.events = types.SimpleNamespace(closed=_FakeEvent())
        self.probe_results = list(probe_results or [{"status": "done"}])
        self.upload_results = list(upload_results or [{"status": "done", "skin_id": 41}])
        self.model_results = list(model_results or [{"status": "done"}])
        self.wear_results = list(wear_results or [{"status": "done"}])
        self.confirm = confirm
        self.url = url
        self.scripts = []
        self.confirmations = []
        self.loaded_urls = []
        self.titles = []
        self.destroyed = False

    def get_current_url(self):
        return self.url

    def evaluate_js(self, script):
        self.scripts.append(script)
        if "'/skins/remove/0'" in script:
            return self.probe_results.pop(0)
        if "call('/skins/upload'" in script:
            return self.upload_results.pop(0)
        if "`/skins/s${id}/edit`" in script:
            return self.model_results.pop(0)
        if "call('/skins/wear'" in script:
            return self.wear_results.pop(0)
        raise AssertionError("Unexpected script sent to Ely.by window")

    def set_title(self, title):
        self.titles.append(title)

    def show(self):
        pass

    def load_url(self, url):
        self.loaded_urls.append(url)
        # Model the redirect back from Ely.by's single-sign-on page.
        self.url = "https://ely.by/skins"

    def create_confirmation_dialog(self, title, message):
        self.confirmations.append((title, message))
        return self.confirm

    def destroy(self):
        self.destroyed = True


class ElyWebsiteSkinTests(unittest.TestCase):
    def setUp(self):
        self.account = {"type": "ely", "name": "grokkyguy", "mc_token": "launcher-token"}
        self.png = _png()
        self.window = _FakeWindow()
        self.webview = types.SimpleNamespace(
            create_window=lambda *args, **kwargs: self.window,
        )
        self.webview_patch = patch.dict(sys.modules, {"webview": self.webview})
        self.webview_patch.start()
        self.addCleanup(self.webview_patch.stop)

    def test_upload_sets_model_and_wears_skin_in_ely_session(self):
        result = ely_skins.apply_ely_skin(self.account, self.png, "slim")

        self.assertEqual(result, {"success": True})
        self.assertEqual(len(self.window.confirmations), 1)
        self.assertIn("grokkyguy", self.window.confirmations[0][1])
        self.assertEqual(len(self.window.loaded_urls), 0)
        self.assertTrue(self.window.destroyed)
        self.assertEqual(len(self.window.scripts), 4)  # session probe, upload, model, wear
        upload = next(script for script in self.window.scripts if "call('/skins/upload'" in script)
        self.assertIn(base64.b64encode(self.png).decode("ascii"), upload)
        self.assertIn("credentials: 'same-origin'", upload)
        model = next(script for script in self.window.scripts if "`/skins/s${id}/edit`" in script)
        self.assertIn("isSlim: '1'", model)
        wear = next(script for script in self.window.scripts if "call('/skins/wear'" in script)
        self.assertIn("skinId: '41'", wear)
        self.assertNotIn("launcher-token", "\n".join(self.window.scripts))

    def test_signs_in_to_ely_website_then_applies(self):
        self.window.probe_results = [{"status": "signed_out"}, {"status": "done"}]

        result = ely_skins.apply_ely_skin(self.account, self.png, "classic")

        self.assertEqual(result, {"success": True})
        self.assertEqual(self.window.loaded_urls, [ely_skins.ELY_LOGIN_PAGE])
        self.assertEqual(len(self.window.confirmations), 1)
        self.assertEqual(len(self.window.scripts), 5)
        self.assertTrue(self.window.destroyed)

    def test_cancelled_account_confirmation_makes_no_change(self):
        self.window.confirm = False

        result = ely_skins.apply_ely_skin(self.account, self.png, "classic")

        self.assertFalse(result["success"])
        self.assertIn("cancelled", result["error"].lower())
        self.assertEqual(len(self.window.scripts), 1)
        self.assertFalse(self.window.destroyed)
        self.assertIn("switch accounts", self.window.titles[-1].lower())

    def test_reauthentication_after_upload_does_not_upload_a_duplicate(self):
        self.window.model_results = [{"status": "signed_out"}, {"status": "done"}]
        self.window.probe_results = [{"status": "done"}, {"status": "done"}]

        result = ely_skins.apply_ely_skin(self.account, self.png, "slim")

        self.assertEqual(result, {"success": True})
        self.assertEqual(sum("call('/skins/upload'" in script for script in self.window.scripts), 1)
        self.assertEqual(self.window.loaded_urls, [ely_skins.ELY_LOGIN_PAGE])
        self.assertEqual(len(self.window.confirmations), 2)

    def test_model_failure_reports_uploaded_but_unapplied_and_does_not_wear(self):
        self.window.model_results = [{"status": "failed", "message": "edit refused"}]

        result = ely_skins.apply_ely_skin(self.account, self.png, "classic")

        self.assertFalse(result["success"])
        self.assertIn("uploaded to Ely.by but not applied", result["error"])
        self.assertIn("edit refused", result["error"])
        self.assertFalse(any("call('/skins/wear'" in script for script in self.window.scripts))

    def test_refuses_non_https_or_foreign_ely_page(self):
        for url in ("http://ely.by/skins", "https://ely.by.evil/skins", "https://ely.by:8443/skins"):
            with self.subTest(url=url):
                self.window = _FakeWindow(url=url)
                result = ely_skins.apply_ely_skin(self.account, self.png, "classic")

                self.assertFalse(result["success"])
                self.assertIn("securely", result["error"])
                self.assertEqual(self.window.scripts, [])
                self.assertTrue(self.window.destroyed)


class SkinsApiAccountRoutingTests(unittest.TestCase):
    def setUp(self):
        self.api = skins_api.SkinsApi()
        self.image = png_uri(_png())

    def _active_account(self, account):
        return (
            patch.object(skins_api.accounts_store, "read", return_value=[account]),
            patch.object(skins_api.settings_store, "read", return_value={"active_account": account["uuid"]}),
        )

    def test_ely_apply_uses_ely_website_flow_without_refreshing_game_token(self):
        account = {"type": "ely", "name": "grokkyguy", "uuid": "ely-uuid", "mc_token": "old-token"}
        with ExitStack() as stack:
            for context in self._active_account(account):
                stack.enter_context(context)
            refresh = stack.enter_context(patch.object(skins_api, "ensure_fresh_account"))
            apply = stack.enter_context(
                patch.object(skins_api, "apply_ely_skin", return_value={"success": True}))
            invalidate = stack.enter_context(patch.object(skins_api, "invalidate_ely_skin_cache"))
            result = self.api.set_skin(self.image, "slim")

        self.assertEqual(result, {"success": True})
        apply.assert_called_once_with(account, _png(), "slim")
        refresh.assert_not_called()
        invalidate.assert_called_once_with("grokkyguy")

    def test_microsoft_apply_still_uses_minecraft_skin_endpoint(self):
        account = {"type": "microsoft", "name": "player", "uuid": "ms-uuid", "mc_token": "old-token"}
        fresh = dict(account, mc_token="fresh-token")
        with ExitStack() as stack:
            for context in self._active_account(account):
                stack.enter_context(context)
            refresh = stack.enter_context(
                patch.object(skins_api, "ensure_fresh_account", return_value=fresh))
            multipart = stack.enter_context(
                patch.object(skins_api, "multipart_skin",
                             return_value=(b"multipart-body", "multipart/type")))
            send = stack.enter_context(patch.object(skins_api, "http_send", return_value=(204, {})))
            apply_ely = stack.enter_context(patch.object(skins_api, "apply_ely_skin"))
            result = self.api.set_skin(self.image, "slim")

        self.assertEqual(result, {"success": True})
        refresh.assert_called_once_with(account)
        multipart.assert_called_once_with("slim", _png())
        send.assert_called_once_with(
            "POST", skins_api.MC_SKINS_URL, "fresh-token", b"multipart-body", "multipart/type")
        apply_ely.assert_not_called()

    def test_ely_skin_cache_is_invalidated_after_success(self):
        from crit_launcher.accounts.ely import invalidate_ely_skin_cache

        with patch.dict(_ELY_SKIN_CACHE, {"grokkyguy": ({"image": "old"}, 0, 5)}):
            invalidate_ely_skin_cache("GrokkyGuy")
            self.assertNotIn("grokkyguy", _ELY_SKIN_CACHE)


if __name__ == "__main__":
    unittest.main()
