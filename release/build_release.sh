#!/bin/bash
# Builds the release files in release/dist/:  install.sh  manifest.txt  A6SiliconPatcher-engine-<version>.tar.gz
# The engine package holds only our own code and open-source components (Python, PyObjC, numpy, ...). It contains nothing of the game:
# no game.i386, no game_meta.json (it is derived from the game executable and is created on the user's Mac), no patched data file.
# Usage: release/build_release.sh [original "Asphalt 6.app"] [built "Asphalt 6 (Apple Silicon).app" with a current engine]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"; SRC="$(cd "$HERE/.." && pwd)"
ORIG="${1:-$HOME/Downloads/Asphalt 6.app}"
BUILT="${2:-$HOME/Downloads/Asphalt 6 (Apple Silicon).app}"
DIST="$HERE/dist"; WORK="$HERE/work"
VERSION="$(cat "$BUILT/Contents/Resources/engine/BUILD")"
LIEF_WHEEL="${LIEF_WHEEL:-$(ls "$HERE"/lief-*-macosx_*_x86_64.whl 2>/dev/null | head -n1)}"
[ -f "$LIEF_WHEEL" ] || { echo "put the x86_64 lief wheel (pip download lief --platform macosx_11_0_x86_64 --python-version 3.11 --only-binary=:all: --no-deps) into release/"; exit 1; }
rm -rf "${DIST:?}" "${WORK:?}"; mkdir -p "$DIST" "$WORK/engine/installer/site" "$WORK/launcher"

# the engine as built by build_app.sh, without the parts that come from the game
ditto "$BUILT/Contents/Resources/engine" "$WORK/engine"
rm -f "$WORK/engine/game.i386" "$WORK/engine/game_meta.json" "$WORK/engine/BUILD"
find "$WORK" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$WORK" -name .DS_Store -delete 2>/dev/null || true
cp "$BUILT/Contents/MacOS/Asphalt 6" "$WORK/launcher/Asphalt 6"
# the pieces that run on the user's Mac
mkdir -p "$WORK/engine/tools"
cp "$SRC/tools/patch_options_swf.py" "$SRC/tools/swflib.py" "$SRC/tools/asasm.py" "$WORK/engine/tools/"
cp "$HERE/meta_gen.py" "$WORK/engine/installer/"
cp "$SRC/guestlib/objects.json" "$WORK/engine/installer/objects.json"
(cd "$WORK/engine/installer/site" && unzip -q "$LIEF_WHEEL")
cp "$HERE/LICENSES.txt" "$WORK/engine/" 2>/dev/null || true

ENGINE_FILE="A6SiliconPatcher-engine-$VERSION.tar.gz"
(cd "$WORK" && COPYFILE_DISABLE=1 tar -czf "$DIST/$ENGINE_FILE" engine launcher)
cp "$HERE/install.sh" "$DIST/install.sh"

# checksums of the original files the patch is made for, and of what the patcher must produce from them
PATCHED="$WORK/patched.dat"
"$SRC/venv/bin/python" "$SRC/tools/patch_options_swf.py" "$ORIG/Contents/Resources/data/file000516.dat" "$PATCHED" >/dev/null
{
  echo "VERSION=$VERSION"
  echo "ENGINE_FILE=$ENGINE_FILE"
  echo "ENGINE_SHA256=$(shasum -a 256 "$DIST/$ENGINE_FILE" | cut -d' ' -f1)"
  echo "ENGINE_SIZE=$(stat -f %z "$DIST/$ENGINE_FILE")"
  echo "ORIG_BIN_SHA256=$(shasum -a 256 "$ORIG/Contents/MacOS/Asphalt 6" | cut -d' ' -f1)"
  echo "ORIG_DAT_SHA256=$(shasum -a 256 "$ORIG/Contents/Resources/data/file000516.dat" | cut -d' ' -f1)"
  echo "PATCHED_DAT_SHA256=$(shasum -a 256 "$PATCHED" | cut -d' ' -f1)"
} > "$DIST/manifest.txt"
rm -rf "${WORK:?}"
cat "$DIST/manifest.txt"; ls -la "$DIST"
