"""Ely.by skin handoff to Horizon's offline-player skin hook."""

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from ..accounts.ely import ely_skin_for
from ..core.images import decode_png_uri, png_uri
from ..instances.store import instance_dir


_SKIN_FILE = "horizon-offline-skin.png"
_SKIN_META_FILE = "horizon-offline-skin.json"


def _account_key(account: dict) -> str:
    value = str(account.get("uuid") or account.get("name") or "").strip()
    return value.replace("-", "").casefold()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _cached_skin(path: Path, metadata_path: Path, account_key: str):
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("account_key") != account_key:
            return None
        image = path.read_bytes()
        if hashlib.sha256(image).hexdigest() != metadata.get("sha256"):
            return None
        # Reuse the same PNG and dimension validation as the live skin response.
        decode_png_uri(png_uri(image))
        model = "slim" if metadata.get("model") == "slim" else "classic"
        return path, model
    except (OSError, ValueError, TypeError):
        return None


def prepare_horizon_ely_skin(instance_name: str, account: dict):
    """Return a cached PNG path and model for an Ely account, if it has a skin.

    Horizon's signed client reads HORIZON_OFFLINE_SKIN and
    HORIZON_OFFLINE_SKIN_MODEL for its local/offline player. Keep the PNG in
    the launcher instance directory so a transient skin-service failure does
    not make an already-known Ely skin disappear on the next launch.
    """
    if not isinstance(account, dict) or account.get("type") != "ely":
        return None
    name = str(account.get("name") or "").strip()
    key = _account_key(account)
    if not name or not key:
        return None

    root = instance_dir(instance_name)
    path = root / _SKIN_FILE
    metadata_path = root / _SKIN_META_FILE
    try:
        skin = ely_skin_for(name)
        if skin:
            image = decode_png_uri(skin.get("image"))
            model = "slim" if str(skin.get("variant") or "").lower() == "slim" else "classic"
            metadata = {
                "account_key": key,
                "model": model,
                "sha256": hashlib.sha256(image).hexdigest(),
            }
            _write_atomic(path, image)
            _write_atomic(metadata_path, json.dumps(metadata, sort_keys=True).encode("utf-8"))
            return path.resolve(), model
    except Exception as exc:
        # Skin lookup is a convenience: an outage must never prevent Horizon
        # from launching. Fall back to the last verified image for this account.
        logging.warning("Couldn't refresh the Ely.by skin for Horizon: %s", exc)

    cached = _cached_skin(path, metadata_path, key)
    if cached:
        return cached[0].resolve(), cached[1]
    return None
