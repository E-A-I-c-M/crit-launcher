"""Regression tests for concurrent and retryable downloads."""

import hashlib
import ssl
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from crit_launcher.core import http
from crit_launcher.minecraft import libraries


class DownloadResilienceTests(unittest.TestCase):
    def test_shared_asset_is_downloaded_once_for_concurrent_installs(self):
        content = b"shared minecraft asset"
        entry = {
            "path": "ab/shared",
            "url": "https://resources.download.minecraft.net/ab/shared",
            "size": len(content),
            "sha1": hashlib.sha1(content).hexdigest(),
        }
        calls = []
        calls_lock = threading.Lock()

        def fake_download(_url, dest, _size, _sha1, _progress):
            with calls_lock:
                calls.append(dest)
            time.sleep(0.03)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

        with tempfile.TemporaryDirectory(prefix="crit-shared-asset-") as tmp:
            root = Path(tmp)
            with patch.object(libraries, "http_download", side_effect=fake_download):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(
                        lambda _index: libraries.ensure_file(entry, root, None), range(2)))

            self.assertEqual(results, [root / entry["path"]] * 2)
            self.assertEqual(len(calls), 1)
            self.assertEqual((root / entry["path"]).read_bytes(), content)

    def test_transient_tls_eof_is_retried_with_backoff(self):
        content = b"retryable minecraft asset"
        entry = {
            "path": "cd/retry",
            "url": "https://resources.download.minecraft.net/cd/retry",
            "size": len(content),
            "sha1": hashlib.sha1(content).hexdigest(),
        }
        calls = []

        def flaky_download(_url, dest, _size, _sha1, _progress):
            calls.append(dest)
            if len(calls) < 3:
                raise ssl.SSLError("UNEXPECTED_EOF_WHILE_READING")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

        with tempfile.TemporaryDirectory(prefix="crit-retry-asset-") as tmp:
            with patch.object(libraries, "http_download", side_effect=flaky_download), \
                    patch.object(libraries.time, "sleep") as sleep:
                result = libraries.ensure_file(entry, Path(tmp), None)
                result_content = result.read_bytes()

        self.assertEqual(result_content, content)
        self.assertEqual(len(calls), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0])

    def test_file_transfers_obey_process_wide_concurrency_limit(self):
        state = {"active": 0, "peak": 0}
        state_lock = threading.Lock()

        class FakeResponse:
            def __init__(self):
                self._read = False

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                with state_lock:
                    state["active"] -= 1

            def read(self, _size):
                if self._read:
                    return b""
                self._read = True
                time.sleep(0.02)
                return b"payload"

        def fake_urlopen(_request, timeout=30):
            with state_lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            return FakeResponse()

        with tempfile.TemporaryDirectory(prefix="crit-download-slots-") as tmp:
            root = Path(tmp)
            with patch.object(http.urllib.request, "urlopen", side_effect=fake_urlopen):
                with ThreadPoolExecutor(max_workers=20) as pool:
                    list(pool.map(
                        lambda index: http.http_download(
                            "https://example.invalid/payload", root / f"{index}.bin"),
                        range(20)))
            files_exist = all((root / f"{index}.bin").exists() for index in range(20))

        self.assertLessEqual(state["peak"], 8)
        self.assertEqual(state["active"], 0)
        self.assertTrue(files_exist)


if __name__ == "__main__":
    unittest.main()
