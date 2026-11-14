# Crit Launcher

Crit Launcher is an open-source Minecraft launcher written in Python and HTML.

<details>
<summary>Notes</summary>

We are not affiliated with Microsoft or Mojang, Modrinth or FastClient, Fabric, Forge or NeoForge, McDonald's, or Burger King.

The themes that aim to replicate other launchers are custom-built; no original or decompiled code was used. They are unrelated to those launchers. FastClient or Modrinth can contact us through the Discord support server with any requests.
</details>

## Project layout

The backend is the `crit_launcher/` package, with one folder per feature. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for where things live and how to debug them.

## Run from source

Python 3.11 or newer is required. Install `pywebview`, Pillow, and `psutil`, then run:

```sh
python -m pip install pywebview pillow psutil
python crit.py
```

On macOS, pywebview uses Cocoa/WKWebView and installs its PyObjC dependencies. On Linux, use the Qt dependencies described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Run the tests with:

```sh
python -m unittest discover -s tests
```

## Build

### Windows

Build with PyInstaller, then package with [Inno Setup](https://jrsoftware.org/isinfo.php):

```powershell
pyinstaller --onefile --windowed --name crit --icon=icon.ico --add-data "index.html;." crit.py
```

Then run `packaging/windows/critsetup.iss` in Inno Setup.

### Linux

Run `packaging/linux/build-appimage.sh` (see [packaging/linux/README.md](packaging/linux/README.md)).

### macOS

Build on a Mac with Python 3.11 or newer:

```sh
./packaging/macos/build.sh
```

The script creates an isolated build environment, writes `dist/macos/Crit Launcher.app`, and packages a drag-and-drop disk image: `Crit-Launcher-Installer.dmg` on Apple Silicon or `Crit-Launcher-Installer-x86_64.dmg` on Intel. It bundles the UI and artwork, selects Cocoa/WKWebView, and applies an ad-hoc code signature. PyInstaller builds for the architecture of the selected Python interpreter; build separately on Intel and Apple Silicon Macs if you need both architectures. The `macos-arm64-release.yml` workflow builds and publishes the Apple Silicon DMG on a GitHub-hosted ARM64 Mac runner when tag `v1.8-macos` is pushed. The existing `v1.8` source tag predates this port, so the platform-specific tag avoids moving it.

On an Apple Silicon Mac, download the latest published ARM64 release with:

```sh
./packaging/macos/download.sh
```

The script downloads and validates `Crit-Launcher-Installer.dmg` into `~/Downloads`. It defaults to this fork (`E-A-I-c-M/crit-launcher`); set `CRIT_LAUNCHER_REPO=OWNER/REPO` to use another fork, or `CRIT_LAUNCHER_DOWNLOAD_DIR=/path` to change the destination. A published release containing that exact asset is required.

The app is **not Developer ID signed or notarized**. macOS may require you to Control-click the app and choose **Open**, then approve it in Privacy & Security. For public distribution, sign with a Developer ID certificate and notarize the app. The launcher stores its data in `~/Library/Application Support/crit-launcher` (or the directory set by `CRIT_LAUNCHER_DATA`), not inside the `.app` bundle.

Minecraft instances can download their required Mojang Java runtime automatically. Horizon has builds for Apple Silicon macOS only; it is not available on Intel Macs. The in-app updater does not install macOS builds, so update the `.app` manually. The macOS desktop-shortcut action creates a `.command` file, which Finder opens in Terminal.

Before distributing a Mac build, test both `python crit.py` and the packaged `.app`: launch the window and resize it; sign in; create and play a vanilla instance (including automatic Java download); restart and confirm settings and instances persist in Application Support; open the instance folder; test Discord Rich Presence if Discord is running; and create/open a desktop shortcut. On Intel Macs, confirm Horizon reports that it is unsupported; on Apple Silicon, test its setup separately.

<sub>README updated for Crit Launcher 1.8.</sub>
