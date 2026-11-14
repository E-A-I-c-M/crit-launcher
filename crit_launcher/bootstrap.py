"""One-time process setup: data folders, log file and platform fixes.

Importing launcher modules has no side effects. Call init() once before using anything that
reads or writes the data directory (app.main.main() does this; tests call it themselves).
"""
import logging
import os
import sys

from .config.paths import IS_OSX, ensure_data_dirs
from .core.logs import setup_logging
from .core.system import install_clean_env_popen

_done = False


def _configure_packaged_macos_trust_store() -> None:
    """Use macOS's native trust store for HTTPS in the frozen app.

    Python's OpenSSL defaults can point at certificates on the build machine and do not
    necessarily use the user's Keychain trust settings. Preserve an explicit CA override.
    """
    if not (IS_OSX and getattr(sys, "frozen", False)) or os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        logging.exception("install macOS native certificate trust store")
        return
    logging.info("Using macOS native certificate trust store for HTTPS")


def init() -> None:
    global _done
    if _done:
        return
    _done = True
    ensure_data_dirs()
    setup_logging()
    _configure_packaged_macos_trust_store()
    install_clean_env_popen()
