#!/usr/bin/env bash
# Build Crit Launcher as a macOS .app for the architecture of the selected Python.
# Run on macOS; PyInstaller cannot cross-build macOS apps from Linux or Windows.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${OUT_DIR:-$ROOT/dist/macos}"
WORK="${WORK_DIR:-$ROOT/build/macos}"
PYTHON="${PYTHON:-python3}"
NAME="Crit Launcher"
ICON="$ROOT/packaging/macos/crit.icns"
APP="$OUT/$NAME.app"
VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$ROOT/crit_launcher/version.py" | head -1 | tr -d '\r')"
VERSION="${VERSION:-dev}"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This script must be run on macOS (PyInstaller cannot cross-build a .app)." >&2
    exit 1
fi
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.11+ was not found: $PYTHON" >&2
    exit 1
fi
if [[ ! -f "$ICON" ]]; then
    echo "Missing macOS app icon: $ICON" >&2
    exit 1
fi

mkdir -p "$OUT" "$WORK"
if [[ ! -x "$WORK/venv/bin/python" ]]; then
    "$PYTHON" -m venv "$WORK/venv"
fi
PY="$WORK/venv/bin/python"
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "Crit Launcher requires Python 3.11 or newer to build." >&2
    exit 1
fi
ARCH="$("$PY" -c 'import platform; print(platform.machine())')"
case "$ARCH" in
    arm64|aarch64)
        BUILD_ARCH="arm64"
        DMG="$OUT/Crit-Launcher-Installer.dmg"
        ;;
    x86_64|amd64)
        BUILD_ARCH="x86_64"
        DMG="$OUT/Crit-Launcher-Installer-x86_64.dmg"
        ;;
    *) echo "Unsupported macOS build architecture: $ARCH" >&2; exit 1 ;;
esac
echo "==> Building a $ARCH macOS app (Python $($PY --version 2>&1))"
echo "==> Installing build/runtime dependencies"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet pyinstaller pywebview pillow psutil

echo "==> Building $APP"
"$PY" -m PyInstaller --noconfirm --clean --onefile --windowed \
    --name "$NAME" \
    --icon "$ICON" \
    --osx-bundle-identifier xyz.critlauncher.crit \
    --distpath "$OUT" --workpath "$WORK/pyi-work" --specpath "$WORK" \
    --paths "$ROOT" \
    --add-data "$ROOT/index.html:." \
    --add-data "$ROOT/assets:assets" \
    --hidden-import webview.platforms.cocoa \
    --collect-submodules webview \
    "$ROOT/crit.py"

if [[ ! -d "$APP" ]]; then
    echo "PyInstaller did not create the expected app bundle: $APP" >&2
    exit 1
fi

echo "==> Embedding entitlements and applying an ad-hoc code signature"
ENTITLEMENTS_SOURCE="$ROOT/packaging/macos/entitlements.plist"
ENTITLEMENTS_APP="$APP/Contents/Resources/entitlements.plist"
mkdir -p "$(dirname "$ENTITLEMENTS_APP")"
cp "$ENTITLEMENTS_SOURCE" "$ENTITLEMENTS_APP"
/usr/bin/plutil -lint "$ENTITLEMENTS_APP"
/usr/bin/codesign --force --deep --sign - --entitlements "$ENTITLEMENTS_APP" "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
echo "==> Preparing the drag-and-drop disk image"
STAGING="$WORK/dmg-root"
rm -rf "$STAGING"
mkdir -p "$STAGING"
/usr/bin/ditto "$APP" "$STAGING/Crit Launcher.app"
ln -s /Applications "$STAGING/Applications"
rm -f "$DMG"
/usr/bin/hdiutil create -volname "Crit Launcher" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
echo "==> Done: $APP and $DMG (version $VERSION, $BUILD_ARCH)"
echo "Upload the Apple Silicon disk image to a published GitHub release as Crit-Launcher-Installer.dmg for download.sh."
echo "This app is ad-hoc signed, not Developer ID signed or notarized; see the macOS notes in README.md."
