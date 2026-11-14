"""Tests for passing Ely.by skins to Horizon's offline-player skin hook."""

import json
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crit_launcher.core.images import png_uri
from crit_launcher.horizon import releases as horizon_releases
from crit_launcher.horizon import skins as horizon_skins
from crit_launcher.horizon.constants import HORIZON_MARKER
from crit_launcher.horizon.releases import horizon_supports_offline_skin
from crit_launcher.launch.api import LaunchApi
from crit_launcher.launch.api_horizon import HorizonLaunchApi


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


class HorizonElySkinTests(unittest.TestCase):
    def setUp(self):
        self.account = {"type": "ely", "name": "ExampleUser", "uuid": "abcd-1234"}
        self.image = _png()
        self.profile = {"image": png_uri(self.image), "variant": "slim"}

    def test_writes_skin_and_model_for_horizon(self):
        with tempfile.TemporaryDirectory(prefix="crit-horizon-skin-") as tmp:
            root = Path(tmp)
            with patch.object(horizon_skins, "instance_dir", return_value=root), \
                    patch.object(horizon_skins, "ely_skin_for", return_value=self.profile) as lookup:
                result = horizon_skins.prepare_horizon_ely_skin("Horizon", self.account)

            path = root / "horizon-offline-skin.png"
            metadata = json.loads((root / "horizon-offline-skin.json").read_text(encoding="utf-8"))
            self.assertEqual(result, (path.resolve(), "slim"))
            self.assertEqual(path.read_bytes(), self.image)
            self.assertEqual(metadata["account_key"], "abcd1234")
            self.assertEqual(metadata["model"], "slim")
            self.assertNotIn("mc_token", metadata)
            lookup.assert_called_once_with("ExampleUser")

    def test_reuses_only_the_same_accounts_last_skin_when_lookup_misses(self):
        with tempfile.TemporaryDirectory(prefix="crit-horizon-skin-cache-") as tmp:
            root = Path(tmp)
            with patch.object(horizon_skins, "instance_dir", return_value=root), \
                    patch.object(horizon_skins, "ely_skin_for", side_effect=[self.profile, None, None]):
                first = horizon_skins.prepare_horizon_ely_skin("Horizon", self.account)
                second = horizon_skins.prepare_horizon_ely_skin("Horizon", self.account)
                another = horizon_skins.prepare_horizon_ely_skin(
                    "Horizon", dict(self.account, uuid="different-account")
                )

            self.assertEqual(first, (root.joinpath("horizon-offline-skin.png").resolve(), "slim"))
            self.assertEqual(second, first)
            self.assertIsNone(another)

    def test_invalid_live_image_falls_back_to_verified_cache(self):
        with tempfile.TemporaryDirectory(prefix="crit-horizon-skin-invalid-") as tmp:
            root = Path(tmp)
            with patch.object(horizon_skins, "instance_dir", return_value=root), \
                    patch.object(horizon_skins, "ely_skin_for", side_effect=[self.profile, {"image": "bad"}]), \
                    self.assertLogs(level="WARNING"):
                first = horizon_skins.prepare_horizon_ely_skin("Horizon", self.account)
                second = horizon_skins.prepare_horizon_ely_skin("Horizon", self.account)

            self.assertEqual(second, first)

    def test_ignores_non_ely_accounts(self):
        with patch.object(horizon_skins, "ely_skin_for") as lookup:
            self.assertIsNone(horizon_skins.prepare_horizon_ely_skin(
                "Horizon", {"type": "microsoft", "name": "ExampleUser"}
            ))
        lookup.assert_not_called()

    def test_rejects_wrong_skin_dimensions(self):
        with tempfile.TemporaryDirectory(prefix="crit-horizon-skin-size-") as tmp:
            with patch.object(horizon_skins, "instance_dir", return_value=Path(tmp)), \
                    patch.object(horizon_skins, "ely_skin_for", return_value={
                        "image": png_uri(_png(32, 32)), "variant": "classic"
                    }), \
                    self.assertLogs(level="WARNING"):
                result = horizon_skins.prepare_horizon_ely_skin("Horizon", self.account)
            self.assertIsNone(result)


class HorizonLaunchSkinTests(unittest.TestCase):
    def test_skin_required_refresh_asks_setup_for_compatible_build(self):
        api = HorizonLaunchApi()
        api._proc_lock = threading.Lock()
        api._installing = {"Horizon"}
        api.emit = lambda *args, **kwargs: None
        api.push_state = lambda: None
        with (
            patch("crit_launcher.launch.api_horizon.ProgressTracker"),
            patch("crit_launcher.launch.api_horizon.horizon_setup",
                  return_value={"horizon_sounds": True}) as setup,
            patch("crit_launcher.launch.api_horizon.update_instance_meta"),
        ):
            api._horizon_worker("Horizon", require_offline_skin=True)

        self.assertTrue(setup.call_args.kwargs["require_offline_skin"])

    def test_ely_skin_environment_is_passed_to_horizon(self):
        with tempfile.TemporaryDirectory(prefix="crit-horizon-launch-skin-") as tmp:
            root = Path(tmp)
            vdir = root / "build"
            resources = root / "resources"
            game_dir = root / "game"
            instance_root = root / "instance"
            skin_path = root / "horizon-offline-skin.png"
            for directory in (vdir, resources, game_dir, instance_root):
                directory.mkdir(parents=True, exist_ok=True)
            (vdir / "horizon-client").write_bytes(b"client")

            account = {"type": "ely", "name": "ExampleUser", "uuid": "abcd-1234"}
            api = HorizonLaunchApi()
            api._proc_lock = threading.Lock()
            api._processes = {}
            api._sessions = {}
            api._watch_process = lambda *args: None
            api.push_state = lambda: None
            with (
                patch("crit_launcher.launch.api_horizon.horizon_exe", return_value="horizon-client"),
                patch("crit_launcher.launch.api_horizon.ensure_game_layout"),
                patch("crit_launcher.launch.api_horizon.instance_game_dir", return_value=game_dir),
                patch("crit_launcher.launch.api_horizon.instance_dir", return_value=instance_root),
                patch("crit_launcher.launch.api_horizon.settings_store.read", return_value={}),
                patch("crit_launcher.launch.api_horizon.accounts_store.read", return_value=[account]),
                patch("crit_launcher.launch.api_horizon.find_account", return_value=account),
                patch("crit_launcher.launch.api_horizon.prepare_horizon_ely_skin",
                      return_value=(skin_path, "slim")),
                patch("crit_launcher.launch.api_horizon.update_instance_meta"),
                patch("crit_launcher.launch.api_horizon.subprocess.Popen", return_value=object()) as popen,
                patch("crit_launcher.launch.api_horizon.threading.Thread"),
            ):
                result = api._do_horizon_launch("Horizon", {
                    "horizon_dir": str(vdir), "horizon_resources": str(resources)
                })

            self.assertEqual(result, {"success": True})
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["HORIZON_OFFLINE_SKIN"], str(skin_path))
            self.assertEqual(env["HORIZON_OFFLINE_SKIN_MODEL"], "slim")
            self.assertEqual(env["HORIZON_HOME"], str(game_dir))
            popen.call_args.kwargs["stdout"].close()


class HorizonSkinVersionTests(unittest.TestCase):
    def test_offline_skin_hook_minimum_builds(self):
        for version in (None, "v0.2.97", "nightly-2026-10-29-abc123"):
            with self.subTest(version=version):
                self.assertFalse(horizon_supports_offline_skin(version))
        for version in ("nightly-2026-10-30-abc123", "nightly-2026-11-13-9f42064",
                        "v0.2.98", "v0.3.0"):
            with self.subTest(version=version):
                self.assertTrue(horizon_supports_offline_skin(version))

    def test_ely_upgrade_selects_a_skin_capable_release(self):
        with tempfile.TemporaryDirectory(prefix="crit-horizon-release-selection-") as tmp:
            root = Path(tmp)
            unsupported = "v0.2.97"
            supported = "nightly-2026-11-13-9f42064"
            paths = {version: root / version for version in (unsupported, supported)}
            for path in paths.values():
                path.mkdir(parents=True)
                (path / HORIZON_MARKER).write_text("{}", encoding="utf-8")
            releases = [
                {"version": supported},
                {"version": unsupported},
            ]
            with (
                patch.object(horizon_releases, "horizon_asset_name", return_value="horizon-linux-x86_64.tar.gz"),
                patch.object(horizon_releases, "horizon_releases", return_value=releases),
                patch.object(horizon_releases, "horizon_version_dir", side_effect=paths.__getitem__),
            ):
                version, folder = horizon_releases.horizon_install_build(
                    tracker=None, require_offline_skin=True)

            self.assertEqual(version, supported)
            self.assertEqual(folder, paths[supported])

    def test_old_horizon_build_is_refreshed_before_play(self):
        with tempfile.TemporaryDirectory(prefix="crit-horizon-old-build-") as tmp:
            meta = {
                "loader": "horizon",
                "horizon_prepared": True,
                "horizon_resources": tmp,
                "horizon_version": "v0.2.97",
            }
            api = LaunchApi()
            api._proc_lock = threading.Lock()
            api._processes = {}
            api._installing = set()
            api._horizon_worker = lambda *args: None
            api.push_state = lambda: None
            ely_account = {"type": "ely", "name": "ExampleUser"}
            with (
                patch("crit_launcher.launch.api.read_instance_meta", return_value=meta),
                patch("crit_launcher.launch.api.accounts_store.read", return_value=[ely_account]),
                patch("crit_launcher.launch.api.settings_store.read", return_value={}),
                patch("crit_launcher.launch.api.find_account", return_value=ely_account),
                patch("crit_launcher.launch.api.horizon_sounds_pending", return_value=False),
                patch("crit_launcher.launch.api.horizon_supports_offline_skin", return_value=False),
                patch("crit_launcher.launch.api.threading.Thread") as thread,
            ):
                result = api.launch("Horizon")

            self.assertEqual(result, {"success": True, "installing": True})
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["args"], ("Horizon", True, True))

    def test_old_build_for_non_ely_account_does_not_force_skin_update(self):
        with tempfile.TemporaryDirectory(prefix="crit-horizon-other-account-") as tmp:
            meta = {
                "loader": "horizon",
                "horizon_prepared": True,
                "horizon_resources": tmp,
                "horizon_version": "v0.2.97",
            }
            account = {"type": "microsoft", "name": "ExampleUser"}
            api = LaunchApi()
            api._proc_lock = threading.Lock()
            api._processes = {}
            api._installing = set()
            api._do_horizon_launch = lambda name, saved: {"success": True, "instance": name}
            with (
                patch("crit_launcher.launch.api.read_instance_meta", return_value=meta),
                patch("crit_launcher.launch.api.accounts_store.read", return_value=[account]),
                patch("crit_launcher.launch.api.settings_store.read", return_value={}),
                patch("crit_launcher.launch.api.find_account", return_value=account),
                patch("crit_launcher.launch.api.horizon_sounds_pending", return_value=False),
                patch("crit_launcher.launch.api.horizon_supports_offline_skin", return_value=False),
            ):
                result = api.launch("Horizon")

            self.assertEqual(result, {"success": True, "instance": "Horizon"})

    def test_skin_capable_build_launches_without_reinstall(self):
        with tempfile.TemporaryDirectory(prefix="crit-horizon-current-build-") as tmp:
            meta = {
                "loader": "horizon",
                "horizon_prepared": True,
                "horizon_resources": tmp,
                "horizon_version": "nightly-2026-11-13-9f42064",
            }
            api = LaunchApi()
            api._proc_lock = threading.Lock()
            api._processes = {}
            api._installing = set()
            api._do_horizon_launch = lambda name, saved: {"success": True, "instance": name}
            account = {"type": "ely", "name": "ExampleUser"}
            with (
                patch("crit_launcher.launch.api.read_instance_meta", return_value=meta),
                patch("crit_launcher.launch.api.accounts_store.read", return_value=[account]),
                patch("crit_launcher.launch.api.settings_store.read", return_value={}),
                patch("crit_launcher.launch.api.find_account", return_value=account),
                patch("crit_launcher.launch.api.horizon_sounds_pending", return_value=False),
                patch("crit_launcher.launch.api.horizon_supports_offline_skin", return_value=True),
            ):
                result = api.launch("Horizon")

            self.assertEqual(result, {"success": True, "instance": "Horizon"})


if __name__ == "__main__":
    unittest.main()
