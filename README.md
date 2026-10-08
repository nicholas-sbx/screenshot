# Flatshot

A small ShareX-style screenshot tool for Linux on Wayland, built mainly for KDE Plasma.

Flatshot lives in your system tray. Press your shortcut and the screen freezes. **Drag a region, or click a window, and it's saved and copied straight away.** If you want to mark it up first, use the floating toolbar to draw on the frozen screen, then drag. Any **QR codes and barcodes** on screen are found automatically, each with a small card for copying or opening it.

The app is native Qt, but every pixel is custom-painted. It ignores your Qt style, colour scheme, fonts and icon theme, so it looks the same on every desktop: flat, square with rounded corners, and one fixed palette.

| | |
|---|---|
| ![Capture mode: hovering a window to capture it, with a detected QR code](docs/region-mode.png) | ![Draw mode: annotations on the frozen screen](docs/draw-mode.png) |
| Capture mode: click a window or drag a region | Draw mode: mark up first, then drag |
| ![Settings window](docs/settings.png) | |
| Settings | |

## Features

- **Instant region capture**: drag and release to save to `~/Pictures/Screenshots` and copy to the clipboard.
- **Window capture** (KDE): hover over where a window was when the screen froze and it lights up; click to capture just that window. Clicking empty desktop captures the whole monitor.
- **Tray app with global shortcuts**: Flatshot registers with KDE's shortcut service. You set the keys in Flatshot's settings, or in System Settings → Shortcuts → Flatshot. Defaults are <kbd>Ctrl</kbd>+<kbd>Print</kbd> (region) and <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Print</kbd> (all screens).
- **After-capture actions** you choose in settings: save to a folder, copy the image or its path, show a notification, open the image or folder, or run your own command (for example an upload script).
- **Notifications** show a thumbnail of the capture with **Open**, **Show in folder** and **Annotate** buttons.
- **No window animations on KDE**: like Spectacle, the overlay is a layer-shell surface, so it appears and disappears instantly. This works with the native packages; see the known limitations below.
- **Floating toolbar** that appears on the monitor your mouse is on, and that you can drag anywhere by its grip. Tools: pen, line, arrow, rectangle, ellipse, highlighter, text, pixelate (for hiding secrets) and numbered counters. It has 7 colours, 3 stroke sizes, and undo/redo.
- **QR codes and barcodes** (QR, Data Matrix, Aztec, PDF417, EAN/UPC, Code 128 and more, via zxing-cpp) are outlined in mint. Each gets a slim card with the decoded text and buttons to copy it, open it (for links) or dismiss it. <kbd>Q</kbd> or the toolbar's code button hides or shows them all; codes you dismissed stay dismissed.
- **Adjustable screen shading**, including off.
- A **loupe** shows pixel coordinates and the hex colour under the cursor, and a size label shows the selection's real pixel size.
- **Multi-monitor and HiDPI aware.** Output is saved at full native resolution with annotations drawn in at that resolution.

## Install

Grab a package from the [latest release](https://github.com/nicholas-sbx/screenshot/releases/latest):

| Distro | File | Install |
|---|---|---|
| Any (self-contained) | `Flatshot-*-x86_64.AppImage` | `chmod +x Flatshot-*.AppImage && ./Flatshot-*.AppImage` |
| Debian 12+, Ubuntu 24.04+, KDE neon | `flatshot_*_all.deb` | `sudo apt install ./flatshot_*_all.deb` |
| Fedora / openSUSE | `flatshot-*.noarch.rpm` | `sudo dnf install ./flatshot-*.noarch.rpm` |
| Arch / Manjaro / EndeavourOS | `flatshot-*-any.pkg.tar.zst` | `sudo pacman -U flatshot-*-any.pkg.tar.zst` |

The native packages use your distro's **PyQt6**. The AppImage bundles Python, **PySide6** and zxing-cpp. You can also install from source with `pipx install .`.

**Runtime helpers.** On Wayland an app can't read the screen itself, so Flatshot asks a trusted tool:

- **KDE Plasma**: `spectacle` (comes with Plasma)
- **Sway, Hyprland, other wlroots compositors**: `grim`
- **GNOME**: `gnome-screenshot`
- **X11**: built in

`wl-clipboard` is recommended. Without it, a clipboard image copied by a one-off `flatshot` run only lasts while that process runs, unless Klipper grabs it first. The tray app keeps running, so it doesn't have this problem.

### Getting started (KDE)

1. Start **Flatshot** from the app menu. This runs `flatshot --tray`, and the icon appears in the system tray.
2. Right-click the tray icon, choose **Settings…**, click a shortcut field and press your keys. You can also enable **Start at login** there.
3. To use <kbd>Print</kbd>, first free it from Spectacle under **System Settings → Shortcuts → Spectacle**. Flatshot tells you when a key is already taken, and by what.

On other desktops, the tray, settings and notifications work the same. For a key, bind the command `flatshot` in your desktop's keyboard settings; it hands the capture to the running tray app instantly.

## Using it

| | |
|---|---|
| Drag | capture region (saved and copied) |
| Click | capture the highlighted window, or the whole monitor |
| <kbd>Q</kbd> | hide / show detected QR codes and barcodes |
| <kbd>Enter</kbd> / <kbd>Ctrl</kbd>+<kbd>S</kbd> / <kbd>Ctrl</kbd>+<kbd>C</kbd> | capture whole monitor with your drawings |
| <kbd>Esc</kbd> / right-click | cancel the current drag, or quit |
| <kbd>R</kbd> | region tool (back to capture mode) |
| <kbd>P</kbd> <kbd>L</kbd> <kbd>A</kbd> <kbd>B</kbd> <kbd>E</kbd> <kbd>H</kbd> <kbd>T</kbd> <kbd>X</kbd> <kbd>N</kbd> | pen, line, arrow, box, ellipse, highlighter, text, pixelate, counter |
| <kbd>Shift</kbd> while drawing | snap lines to 45° and make boxes/ellipses square/round |
| <kbd>1</kbd>–<kbd>7</kbd> | colour |
| <kbd>[</kbd> <kbd>]</kbd> | stroke size |
| <kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> | undo / redo |

Command line options:

```
flatshot --tray          # run in the system tray (global shortcuts, notifications)
flatshot                 # capture with the overlay (uses the tray app if it is running)
flatshot --full          # capture every screen immediately, no UI
flatshot --settings      # open settings
flatshot --quit          # stop the tray app
flatshot --delay 3       # wait 3 s first
flatshot -o shot.png     # save to a specific file
flatshot -i image.png    # annotate an existing image (and scan it for codes)
flatshot --no-copy / --no-save / --no-scan
```

The saved path (or the decoded code text) is printed to stdout, which makes it easy to use in scripts.

## Configuration

Everything is in the settings window. It's stored in `~/.config/flatshot/config.json`; every key is optional, and these are the defaults:

```json
{
  "save_to_disk": true,
  "save_dir": "~/Pictures/Screenshots",
  "filename": "Screenshot_%Y-%m-%d_%H-%M-%S.png",
  "clipboard": "image",
  "notify": true,
  "open_after": "none",
  "run_command": "",
  "dim_opacity": 60,
  "detect_windows": true,
  "scan_codes": true,
  "show_codes": true,
  "toolbar_follows_mouse": true,
  "backend": "auto",
  "default_tool": "region",
  "default_color": 0,
  "default_size": 1
}
```

- `clipboard`: `image`, `path` or `none`.
- `open_after`: `none`, `image` or `folder`.
- `run_command`: a shell command. `{path}` is replaced with the quoted image path, which is also available as `$FLATSHOT_PATH`.
- `backend`: `auto`, `spectacle`, `grim`, `gnome-screenshot` or `qt`.

`flatshot --print-config` prints the settings in effect. Global shortcuts are stored by KDE itself, not in this file.

## Palette: "Ember"

| | | |
|---|---|---|
| Ink `#121218` | Base `#1A1A22` | Raised `#24242E` |
| Line `#363642` | Text `#F2EEE6` | Muted `#8B8898` |
| **Accent** `#FF6A3D` | **Codes** `#3DDC97` | |

Drawing colours: ember `#FF6A3D`, amber `#FFC53D`, mint `#3DDC97`, sky `#3DB2FF`, violet `#9B7BFF`, bone `#F2EEE6`, ink `#121218`.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/flatshot -i some-image.png        # try the UI without capturing
QT_QPA_PLATFORM=offscreen FLATSHOT_SELFTEST_OUT=shots .venv/bin/flatshot --self-test
```

The self-test builds a fake desktop with a QR code in it. It then exercises, without a display:

- the overlay, scanner, window hit-testing and code show/hide/dismiss
- every drawing tool and the keyboard handling
- the capture pipeline and the settings window

CI runs the self-test on PySide6 and on Ubuntu's PyQt6, offscreen and under a headless Weston (real Wayland), and again inside each built package. It also runs:

- `tests/desktop_services.sh` against KDE's real shortcut daemon (`kglobalaccel`) and a notification server. This covers registering, assigning, conflict detection and key presses, notification buttons, and a full tray run.
- A layer-shell check on a headless Sway.

Building packages locally:

```sh
./packaging/build-packages.sh    # needs nfpm → dist/*.deb *.rpm *.pkg.tar.zst
./packaging/build-appimage.sh    # needs curl → dist/Flatshot-*-x86_64.AppImage
```

## Releasing

Bump `__version__` in `flatshot/__init__.py` and push to the default branch. The `build` workflow notices that there is no `v<version>` tag yet. It builds the `.deb`, `.rpm`, Arch package and AppImage, install-tests them on Ubuntu 24.04, Debian 12 and 13, Fedora and Arch, then publishes the GitHub release with checksums. Pushes that don't change the version still build and test everything. They just don't release.

## Known limitations

- Skipping KWin's animations needs KDE's LayerShellQt library, which is built against your system Qt. The native packages use it. The AppImage bundles its own Qt, so it uses normal fullscreen windows and KWin animates them.
- Window detection uses a KWin script, so it needs KWin (KDE Plasma). Other desktops get region and full-screen capture only.

- On Wayland, capture depends on one of the helper tools above. The xdg-desktop-portal screenshot API is not used yet.
- With mixed-DPI multi-monitor setups, each monitor's slice of the capture is mapped by its logical geometry. The output may be slightly soft on the lower-DPI screen.
