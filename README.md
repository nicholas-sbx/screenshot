# Flatshot

A small ShareX-style screenshot tool for Linux on Wayland, built mainly for KDE Plasma.

Press your shortcut and the screen freezes. **Drag a region and it's saved and copied straight away.** If you want to mark it up first, use the floating toolbar to draw on the frozen screen, then drag. Any **QR codes and barcodes** on screen are found automatically and get a **Copy / Open** card on top of them.

The app is native Qt, but every pixel is custom-painted. It ignores your Qt style, colour scheme, fonts and icon theme, so it looks the same on every desktop: flat, square with rounded corners, and one fixed palette.

| | |
|---|---|
| ![Capture mode: dimmed screen, loupe, and a detected QR code with Copy / Open](docs/region-mode.png) | ![Draw mode: annotations on the frozen screen](docs/draw-mode.png) |
| Capture mode: drag to save, QR code detected | Draw mode: mark up first, then drag |

## Features

- **Instant region capture**: drag and release to save to `~/Pictures/Screenshots` and copy to the clipboard. A single click captures the whole monitor.
- **Floating toolbar** you can drag anywhere by its grip. Tools: pen, line, arrow, rectangle, ellipse, highlighter, text, pixelate (for hiding secrets) and numbered counters. It has 7 colours, 3 stroke sizes, and undo/redo.
- **QR codes and barcodes** (QR, Data Matrix, Aztec, PDF417, EAN/UPC, Code 128 and more, via zxing-cpp) are outlined in mint with a card showing the decoded text. **Copy** puts the text on the clipboard. **Open** appears for links and hands them to your browser or mail client.
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

`wl-clipboard` is recommended. Without it, the clipboard only survives as long as Flatshot keeps running, unless Klipper grabs it first. `notify-send` adds a desktop notification after each capture.

### Bind it to a key (KDE)

1. Go to **System Settings → Keyboard → Shortcuts → Add New → Command or Script…**.
2. Enter the command `flatshot`, or the full path to the AppImage.
3. Give it a key, for example <kbd>Print</kbd> or <kbd>Meta</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd>. If you use <kbd>Print</kbd>, first remove Spectacle's binding for it under **Shortcuts → Spectacle**.

## Using it

| | |
|---|---|
| Drag | capture region (saved and copied) |
| Click | capture whole monitor |
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
flatshot                 # overlay with toolbar (default)
flatshot --full          # capture every screen immediately, no UI
flatshot --delay 3       # wait 3 s first
flatshot -o shot.png     # save to a specific file
flatshot -i image.png    # annotate an existing image (and scan it for codes)
flatshot --no-copy / --no-save / --no-scan
```

The saved path (or the decoded code text) is printed to stdout, which makes it easy to use in scripts.

## Configuration

`~/.config/flatshot/config.json`. Every key is optional, and these are the defaults:

```json
{
  "save_dir": "~/Pictures/Screenshots",
  "filename": "Screenshot_%Y-%m-%d_%H-%M-%S.png",
  "save_to_disk": true,
  "copy_to_clipboard": true,
  "notify": true,
  "scan_codes": true,
  "backend": "auto",
  "default_tool": "region",
  "default_color": 0,
  "default_size": 1
}
```

`backend` can be `auto`, `spectacle`, `grim`, `gnome-screenshot` or `qt`. `flatshot --print-config` prints the settings currently in effect.

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

The self-test builds a fake desktop with a QR code in it, then runs the overlay, scanner, every drawing tool, the keyboard handling and the renderer without a display. CI runs it on PySide6 and on Ubuntu's PyQt6, offscreen and under a headless Weston (real Wayland). It also runs it again inside each built package.

Building packages locally:

```sh
./packaging/build-packages.sh    # needs nfpm → dist/*.deb *.rpm *.pkg.tar.zst
./packaging/build-appimage.sh    # needs curl → dist/Flatshot-*-x86_64.AppImage
```

## Releasing

Bump `__version__` in `flatshot/__init__.py` and push to the default branch. The `build` workflow notices that there is no `v<version>` tag yet. It builds the `.deb`, `.rpm`, Arch package and AppImage, install-tests them on Ubuntu 24.04, Debian 12 and 13, Fedora and Arch, then publishes the GitHub release with checksums. Pushes that don't change the version still build and test everything. They just don't release.

## Known limitations

- On Wayland, capture depends on one of the helper tools above. The xdg-desktop-portal screenshot API is not used yet.
- With mixed-DPI multi-monitor setups, each monitor's slice of the capture is mapped by its logical geometry. The output may be slightly soft on the lower-DPI screen.
