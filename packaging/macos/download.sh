#!/usr/bin/env bash
# Download the latest published Apple Silicon app archive into ~/Downloads.
set -euo pipefail

REPO="${CRIT_LAUNCHER_REPO:-E-A-I-c-M/crit-launcher}"
ASSET="Crit-Launcher-Installer.dmg"
DOWNLOAD_DIR="${CRIT_LAUNCHER_DOWNLOAD_DIR:-$HOME/Downloads}"
URL="https://github.com/$REPO/releases/latest/download/$ASSET"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This downloader is for macOS Apple Silicon." >&2
    exit 1
fi
if [[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" != "1" ]]; then
    echo "This is an Apple Silicon-only build; this Mac does not report Apple Silicon hardware." >&2
    exit 1
fi
if [[ ! "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    echo "Invalid repository name: $REPO (expected OWNER/REPO)." >&2
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required to download Crit Launcher." >&2
    exit 1
fi

mkdir -p "$DOWNLOAD_DIR"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/crit-launcher-download.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
ARCHIVE="$WORK/$ASSET"

if ! curl --fail --location --retry 3 --show-error --silent --output "$ARCHIVE" "$URL"; then
    echo "No Apple Silicon build was downloaded from $REPO. Publish a non-draft GitHub release containing $ASSET, or set CRIT_LAUNCHER_REPO=OWNER/REPO." >&2
    exit 1
fi

if ! /usr/bin/hdiutil verify "$ARCHIVE" >/dev/null; then
    echo "The downloaded file is not a valid Crit Launcher disk image." >&2
    exit 1
fi

DEST="$DOWNLOAD_DIR/$ASSET"
mv -f "$ARCHIVE" "$DEST"
echo "Downloaded: $DEST"
echo "Double-click the DMG and drag Crit Launcher.app to Applications."
echo "The app is ad-hoc signed and not notarized; macOS may require Control-click > Open on first launch."
