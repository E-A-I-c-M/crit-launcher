"""Regression tests for macOS path selection and launch helpers."""

import os
import plistlib
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class MacOSPathTests(unittest.TestCase):
    def test_frozen_macos_uses_user_data_and_standard_page(self):
        with tempfile.TemporaryDirectory(prefix="crit-macos-paths-") as tmp:
            root = Path(tmp)
            home = root / "home"
            resources = root / "bundle" / "Contents" / "Resources"
            app_executable = root / "Applications" / "Crit Launcher.app" / "Contents" / "MacOS" / "Crit Launcher"
            home.mkdir()
            resources.mkdir(parents=True)
            app_executable.parent.mkdir(parents=True)
            (resources / "index.html").write_text("standard page", encoding="utf-8")
            (resources / "index.linux.html").write_text("linux page", encoding="utf-8")

            code = textwrap.dedent("""
                import os, sys, urllib.request
                from pathlib import Path
                sys.platform = "darwin"
                sys.frozen = True
                sys.executable = os.environ["FAKE_EXECUTABLE"]
                sys._MEIPASS = os.environ["FAKE_RESOURCES"]
                from crit_launcher.config.paths import DATA_DIR, IS_LINUX, IS_OSX, PAGE_DIR, RESOURCE_DIR
                from crit_launcher.ui.assets import UI_ASSETS_DIR
                from crit_launcher.ui.page import prepare_page

                home = Path.home()
                assert IS_OSX and not IS_LINUX
                assert DATA_DIR == home / "Library" / "Application Support" / "crit-launcher"
                assert UI_ASSETS_DIR == DATA_DIR / "ui_assets"
                assert PAGE_DIR == DATA_DIR / "web"
                assert RESOURCE_DIR == Path(os.environ["FAKE_RESOURCES"])
                page = prepare_page()
                assert page == PAGE_DIR / "index.html"
                assert page.read_text(encoding="utf-8") == "standard page"
            """)
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["FAKE_EXECUTABLE"] = str(app_executable)
            env["FAKE_RESOURCES"] = str(resources)
            env.pop("CRIT_LAUNCHER_DATA", None)
            env.pop("XDG_DATA_HOME", None)
            subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True)

    def test_custom_data_override_is_preserved_on_macos(self):
        with tempfile.TemporaryDirectory(prefix="crit-macos-override-") as tmp:
            override = Path(tmp) / "custom launcher data"
            code = textwrap.dedent("""
                import os, sys
                from pathlib import Path
                sys.platform = "darwin"
                from crit_launcher.config.paths import DATA_DIR, IS_OSX
                assert IS_OSX
                assert DATA_DIR == Path(os.environ["CRIT_LAUNCHER_DATA"])
            """)
            env = os.environ.copy()
            env["CRIT_LAUNCHER_DATA"] = str(override)
            subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True)


class MacOSIntegrationTests(unittest.TestCase):
    def test_frozen_https_smoke_option_checks_the_changelog_endpoint(self):
        from importlib import import_module

        app_main = import_module("crit_launcher.app.main")
        endpoint = "https://api.github.com/repos/snigglaberry/crit-launcher/releases/latest"
        with patch.object(app_main, "init_environment"), \
                patch.object(app_main.sys, "argv", ["crit", "--check-https"]), \
                patch("crit_launcher.core.http.http_get_json", return_value={"tag_name": "v1.8"}) as get_json:
            self.assertEqual(app_main.main(), 0)
        get_json.assert_called_once_with(endpoint, timeout=15, retries=0)

        with patch.object(app_main, "init_environment"), \
                patch.object(app_main.sys, "argv", ["crit", "--check-https"]), \
                patch("crit_launcher.core.http.http_get_json", side_effect=OSError("offline")), \
                patch.object(app_main, "log_exception") as log_exception:
            self.assertEqual(app_main.main(), 1)
        log_exception.assert_called_once_with("HTTPS smoke test")

    def test_frozen_macos_installs_native_trust_store_for_https(self):
        from types import SimpleNamespace
        from crit_launcher import bootstrap

        calls = []
        fake_truststore = SimpleNamespace(inject_into_ssl=lambda: calls.append(True))
        with patch.dict(sys.modules, {"truststore": fake_truststore}), \
                patch.object(bootstrap, "IS_OSX", True), \
                patch.object(bootstrap.sys, "frozen", True, create=True), \
                patch.dict(os.environ, {"SSL_CERT_FILE": ""}):
            os.environ.pop("SSL_CERT_FILE")
            bootstrap._configure_packaged_macos_trust_store()
        self.assertEqual(calls, [True])

        build_script = (ROOT / "packaging" / "macos" / "build.sh").read_text(encoding="utf-8")
        self.assertIn("psutil truststore", build_script)
        self.assertIn("--collect-submodules truststore", build_script)

    def test_entitlements_are_bundled_and_used_for_signing(self):
        entitlements_path = ROOT / "packaging" / "macos" / "entitlements.plist"
        entitlements = plistlib.loads(entitlements_path.read_bytes())
        self.assertTrue(entitlements["com.apple.security.files.user-selected.read-write"])
        self.assertTrue(entitlements["com.apple.security.files.downloads.read-write"])
        self.assertTrue(entitlements["com.apple.security.network.client"])
        self.assertNotIn("com.apple.security.app-sandbox", entitlements)

        build_script = (ROOT / "packaging" / "macos" / "build.sh").read_text(encoding="utf-8")
        self.assertIn('$APP/Contents/Resources/entitlements.plist', build_script)
        self.assertIn('--entitlements "$ENTITLEMENTS_APP"', build_script)

    def test_packaged_icns_has_expected_png_sizes(self):
        data = (ROOT / "packaging" / "macos" / "crit.icns").read_bytes()
        self.assertEqual(data[:4], b"icns")
        self.assertEqual(int.from_bytes(data[4:8], "big"), len(data))
        chunks = {}
        offset = 8
        while offset < len(data):
            code = data[offset:offset + 4]
            length = int.from_bytes(data[offset + 4:offset + 8], "big")
            self.assertGreaterEqual(length, 8)
            self.assertLessEqual(offset + length, len(data))
            chunks[code] = data[offset + 8:offset + length]
            offset += length
        self.assertEqual(offset, len(data))
        self.assertIn(b"TOC ", chunks)
        for code, expected_size in ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64),
                                    (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
                                    (b"ic10", 1024)):
            png = chunks[code]
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"), code)
            self.assertEqual(int.from_bytes(png[16:20], "big"), expected_size)
            self.assertEqual(int.from_bytes(png[20:24], "big"), expected_size)

    def test_discord_ipc_candidates_include_macos_tmpdir(self):
        from crit_launcher.discord.rpc import _candidates

        with tempfile.TemporaryDirectory(prefix="crit-discord-ipc-") as tmp:
            with patch.dict(os.environ, {"TMPDIR": tmp, "XDG_RUNTIME_DIR": "", "TEMP": "", "TMP": ""}):
                with patch.object(os, "name", "posix"):
                    candidates = list(_candidates())
        self.assertIn(("unix", str(Path(tmp) / "discord-ipc-0")), candidates)

    def test_source_shortcut_quotes_instance_and_script_paths(self):
        from crit_launcher.instances import api_shortcuts

        with tempfile.TemporaryDirectory(prefix="crit-macos-shortcut-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            entry_script = Path(tmp) / "source tree" / "crit.py"
            with patch.object(api_shortcuts, "IS_LINUX", False), \
                    patch.object(api_shortcuts, "IS_OSX", True), \
                    patch.object(api_shortcuts, "read_instance_meta", return_value={"name": "My Instance"}), \
                    patch.object(api_shortcuts.Path, "home", return_value=home), \
                    patch.object(api_shortcuts, "ENTRY_SCRIPT", entry_script), \
                    patch.object(api_shortcuts.sys, "executable", "/usr/bin/python3"), \
                    patch.object(api_shortcuts.sys, "frozen", False, create=True):
                result = api_shortcuts.ShortcutsApi().create_desktop_shortcut("My Instance")

            self.assertTrue(result["success"], result)
            shortcut = Path(result["path"])
            self.assertTrue(shortcut.is_file())
            self.assertTrue(shortcut.stat().st_mode & 0o100)
            command = shortcut.read_text(encoding="utf-8").splitlines()[1]
            import shlex
            self.assertEqual(shlex.split(command.removeprefix("exec ")),
                             ["/usr/bin/python3", str(entry_script), "--launch-instance", "My Instance"])

    def test_frozen_shortcut_opens_app_bundle_with_instance_argument(self):
        from crit_launcher.instances import api_shortcuts

        with tempfile.TemporaryDirectory(prefix="crit-macos-app-shortcut-") as tmp:
            root = Path(tmp)
            home = root / "home"
            executable = root / "Applications" / "Crit Launcher.app" / "Contents" / "MacOS" / "Crit Launcher"
            home.mkdir()
            executable.parent.mkdir(parents=True)
            with patch.object(api_shortcuts, "IS_LINUX", False), \
                    patch.object(api_shortcuts, "IS_OSX", True), \
                    patch.object(api_shortcuts, "read_instance_meta", return_value={"name": "Test"}), \
                    patch.object(api_shortcuts.Path, "home", return_value=home), \
                    patch.object(api_shortcuts.sys, "executable", str(executable)), \
                    patch.object(api_shortcuts.sys, "frozen", True, create=True):
                result = api_shortcuts.ShortcutsApi().create_desktop_shortcut("Test")

            self.assertTrue(result["success"], result)
            script = Path(result["path"]).read_text(encoding="utf-8")
            self.assertIn("/usr/bin/open -n -a", script)
            self.assertIn(str(executable.parents[2]), script)
            self.assertIn("--args --launch-instance Test", script)


if __name__ == "__main__":
    unittest.main()
