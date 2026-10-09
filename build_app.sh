#!/bin/bash
# Builds "Asphalt 6 (Apple Silicon).app" next to the original app: APFS clone of the original bundle (no extra
# disk for the 1.8 GB of game data) + the native runtime (x86_64 python under Rosetta, 32-bit mode host).
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
ORIG="${1:-$HOME/Downloads/Asphalt 6.app}"
DEST="${2:-$HOME/Downloads/Asphalt 6 (Apple Silicon).app}"
rm -rf "$DEST"
cp -c -R "$ORIG" "$DEST"
# Options movie (Visuals page): adds the "3D Wheels in Race" toggle (the original data stays untouched; the clone gets its own file)
rm -f "$DEST/Contents/Resources/data/file000516.dat"
"$SRC/venv/bin/python" "$SRC/tools/patch_options_swf.py" "$ORIG/Contents/Resources/data/file000516.dat" "$DEST/Contents/Resources/data/file000516.dat"
ENG="$DEST/Contents/Resources/engine"
mkdir -p "$ENG/native" "$ENG/guestlib" "$ENG/x64"
# the original i386 executable is only data for the engine: keep it out of Contents/MacOS (an i386 Mach-O there confuses LaunchServices / codesign)
mv "$DEST/Contents/MacOS/Asphalt 6" "$ENG/game.i386"
rsync -a --exclude __pycache__ "$SRC/n6" "$ENG/"
cp "$SRC/n6main.py" "$SRC/game_meta.json" "$ENG/"
date +%Y%m%d-%H%M > "$ENG/BUILD"
cp "$SRC/native/a6launcher" "$SRC/native/libhost.dylib" "$ENG/native/"
cp "$SRC"/guestlib/*.bin "$ENG/guestlib/"
rsync -a --exclude __pycache__ --exclude /lib/python3.11/test --exclude /lib/python3.11/tkinter --exclude /lib/python3.11/idlelib \
      --exclude /lib/python3.11/turtledemo --exclude /lib/python3.11/lib2to3 --exclude /lib/python3.11/ensurepip \
      --exclude /lib/python3.11/site-packages --exclude /lib/python3.11/config-3.11-darwin \
      --exclude /lib/tcl9 --exclude /lib/tcl9.0 --exclude /lib/tk9.0 --exclude /lib/itcl4.3.8 --exclude /lib/thread3.0.6 \
      --exclude /lib/libtcl9.0.dylib --exclude /lib/libtcl9tk9.0.dylib --exclude /lib/pkgconfig --exclude /share \
      --exclude /include "$SRC/x64/python/" "$ENG/x64/python/"
mkdir -p "$ENG/x64/python/lib/python3.11/site-packages"
rsync -a --exclude __pycache__ --exclude PyObjCTest --exclude PIL --exclude 'pillow*' --exclude bin "$SRC/x64/site" "$ENG/x64/"
(cd "$SRC/native" && clang -arch arm64 -arch x86_64 -O2 -mmacosx-version-min=11.0 -o app_launcher app_launcher.c)
cp "$SRC/native/app_launcher" "$DEST/Contents/MacOS/Asphalt 6"
chmod +x "$DEST/Contents/MacOS/Asphalt 6"
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
plutil -replace CFBundleIdentifier -string com.gameloft.asphalt6mac.arm64 "$DEST/Contents/Info.plist"
plutil -replace CFBundleDisplayName -string "Asphalt 6 (Apple Silicon)" "$DEST/Contents/Info.plist"
plutil -replace LSMinimumSystemVersion -string 11.0 "$DEST/Contents/Info.plist"
find "$DEST" -name "*.x" -delete; find "$DEST" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
xattr -cr "$DEST" 2>/dev/null || true
codesign --force --sign - "$DEST/Contents/MacOS/Asphalt 6"
codesign --force --sign - "$ENG/native/a6launcher" "$ENG/native/libhost.dylib" 2>/dev/null || true
codesign --force --sign - "$DEST"
codesign --verify --strict "$DEST" && echo "signature ok (ad-hoc)"
du -sh "$ENG"
echo "built: $DEST"
