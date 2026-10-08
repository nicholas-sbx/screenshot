#!/usr/bin/env bash
# Build a self-contained AppImage: relocatable CPython (python-appimage)
# + PySide6 + zxing-cpp from PyPI + flatshot, packed with appimagetool.
#
# Env overrides: PYTHON_APPIMAGE_URL, APPIMAGETOOL_URL, VERSION.
set -euo pipefail
cd "$(dirname "$0")/.."

ARCH=x86_64
PYVER=3.12
VERSION="${VERSION:-$(sed -nE 's/^__version__ = "(.*)"/\1/p' flatshot/__init__.py)}"
WORK="build/appimage"
APPDIR="$WORK/AppDir"
OUT="dist/Flatshot-$VERSION-$ARCH.AppImage"
export APPIMAGE_EXTRACT_AND_RUN=1  # no FUSE needed on CI runners

rm -rf "$WORK"
mkdir -p "$WORK" dist

if [[ -z "${PYTHON_APPIMAGE_URL:-}" ]]; then
    auth=()
    [[ -n "${GITHUB_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
    PYTHON_APPIMAGE_URL=$(curl -fsSL "${auth[@]}" \
        "https://api.github.com/repos/niess/python-appimage/releases/tags/python$PYVER" |
        grep -o "https://[^\"]*-manylinux_2_28_$ARCH\.AppImage" | head -n1)
fi
APPIMAGETOOL_URL="${APPIMAGETOOL_URL:-https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage}"

echo ">> python: $PYTHON_APPIMAGE_URL"
curl -fsSL -o "$WORK/python.AppImage" "$PYTHON_APPIMAGE_URL"
curl -fsSL -o "$WORK/appimagetool" "$APPIMAGETOOL_URL"
chmod +x "$WORK/python.AppImage" "$WORK/appimagetool"

(cd "$WORK" && ./python.AppImage --appimage-extract >/dev/null)
mv "$WORK/squashfs-root" "$APPDIR"
PY="$APPDIR/opt/python$PYVER/bin/python$PYVER"

"$PY" -m pip install --no-cache-dir --no-warn-script-location "PySide6-Essentials>=6.5" "zxing-cpp>=2.2" "jeepney>=0.7"
"$PY" -m pip install --no-cache-dir --no-warn-script-location --no-deps .

# Trim parts of PySide6 a screenshot tool never loads.
SITE=$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
PYSIDE="$SITE/PySide6"
rm -rf "$PYSIDE"/{include,typesystems,glue,examples,scripts,doc} "$PYSIDE/Qt/translations" "$PYSIDE/Qt/qml"
rm -f "$PYSIDE"/{assistant,designer,linguist,lrelease,lupdate,qmlformat,qmllint,qmlls,qmlcachegen,qmlimportscanner,qmltyperegistrar,balsam,balsamui,svgtoqml}
find "$PYSIDE" -name '*.pyi' -delete
find "$APPDIR" -name __pycache__ -prune -exec rm -rf {} +
"$PY" -m pip uninstall -y pip >/dev/null 2>&1 || true

# Replace python-appimage's launcher, desktop entry and icon with ours.
rm -f "$APPDIR"/*.desktop "$APPDIR"/*.png "$APPDIR"/*.svg "$APPDIR/.DirIcon" "$APPDIR/AppRun"
rm -rf "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo"
cat > "$APPDIR/AppRun" <<APPRUN
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\$0")")"
export FLATSHOT_QT=pyside6
# -I: ignore the host's PYTHON* variables, user site and the current dir.
exec "\$HERE/opt/python$PYVER/bin/python$PYVER" -I -m flatshot "\$@"
APPRUN
chmod +x "$APPDIR/AppRun"
mkdir -p "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp packaging/flatshot.desktop "$APPDIR/flatshot.desktop"
cp packaging/flatshot.desktop "$APPDIR/usr/share/applications/flatshot.desktop"
cp flatshot/assets/flatshot.png "$APPDIR/flatshot.png"
cp flatshot/assets/flatshot.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/flatshot.png"
ln -sf flatshot.png "$APPDIR/.DirIcon"

ARCH=$ARCH "$WORK/appimagetool" --no-appstream "$APPDIR" "$OUT"
chmod +x "$OUT"
ls -lh "$OUT"
