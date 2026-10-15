#!/usr/bin/env bash
# Build the macOS app and its disk image (Apple Silicon).
#
#   packaging/macos/build.sh [--out dist/mac] [--version 1.2.0] [--no-dmg]
#
# Produces
#
#   <out>/The Great IHC Leap Forward.app
#       Contents/MacOS/The Great IHC Leap Forward   starts runtime/bin/python3 -m app
#       Contents/Resources/                          the same layout as the Windows install:
#           runtime/     a relocatable CPython 3.13 (python-build-standalone) with every library
#           app/ scripts/ analysis/ atlas/           the repository, unchanged
#           keras/       StarDist's pretrained model, so Measure works offline
#           VERSION.json the version and the fingerprints the in-app updater compares
#   <out>/The-Great-IHC-Leap-Forward-<version>-mac.dmg
#
# Why this shape: see packaging/windows/build.ps1. The stages are loaded by file
# path at run time, so a freezer (PyInstaller, py2app) cannot see what they
# import; a real interpreter with a real site-packages runs every stage exactly
# as it runs from source. python-build-standalone is the macOS counterpart of
# the python.org embeddable zip: a whole Python that works from any folder.
#
# No R: the R figures are off in the app. Unsigned (ad-hoc signature only):
# see packaging/macos/README.md for what that means for the person opening it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OUT="$REPO/dist/mac"
VERSION="0.0.0-dev"
DMG=1
while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="$(mkdir -p "$2" && cd "$2" && pwd)"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --no-dmg) DMG=0; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

NAME="The Great IHC Leap Forward"
PRODUCT="The-Great-IHC-Leap-Forward"
BUNDLE_ID="tech.mbel.the-great-ihc-leap-forward"
# The oldest macOS the app promises to start on. PySide6 6.10+ wheels need 14;
# check_macos_min.py fails the build if any library needs newer, and
# constraints.txt holds back the ones that would.
MIN_MACOS="14.0"
ARCH="aarch64-apple-darwin"

CACHE="$REPO/work/packaging-cache/mac"
mkdir -p "$CACHE"
APP="$OUT/$NAME.app"
RES="$APP/Contents/Resources"
RUNTIME="$RES/runtime"
PY="$RUNTIME/bin/python3"

# bundle-lock.txt: a BUNDLE_VERSIONS.txt from an earlier build, committed, so
# releases share one runtime and the updater can tell code from runtime changes
# (same idea as packaging/windows/bundle-lock.txt).
LOCK="$HERE/bundle-lock.txt"
PY_VERSION=""
PBS_TAG=""
CONSTRAINTS="$CACHE/constraints.txt"
cp "$HERE/constraints.txt" "$CONSTRAINTS"
if [ -f "$LOCK" ]; then
    PY_VERSION="$(sed -n 's/^python==//p' "$LOCK")"
    PBS_TAG="$(sed -n 's/^python-build-standalone==//p' "$LOCK")"
    grep -vE '^(python|python-build-standalone|R)==' "$LOCK" | grep -v '^\s*$' >> "$CONSTRAINTS" || true
    echo "Building against $LOCK"
fi

fetch() {   # url -> cached path
    local file="$CACHE/$(basename "$1")"
    if [ ! -f "$file" ]; then
        echo "  downloading $1" >&2
        curl -fsSL --retry 4 "$1" -o "$file.part"
        mv "$file.part" "$file"
    fi
    echo "$file"
}

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"

# --- Python ---------------------------------------------------------------
if [ -z "$PBS_TAG" ]; then
    PBS_TAG="$(curl -fsSL --retry 4 https://raw.githubusercontent.com/astral-sh/python-build-standalone/latest-release/latest-release.json \
               | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag"])')"
fi
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG"
SUMS="$(fetch "$PBS_URL/SHA256SUMS")"
# Newest 3.13 in that release (or exactly the locked one).
LINE="$(grep -E "  cpython-${PY_VERSION:-3\.13\.[0-9]+}\+$PBS_TAG-$ARCH-install_only_stripped\.tar\.gz$" "$SUMS" \
        | sort -t. -k3,3n | tail -1)"
[ -n "$LINE" ] || { echo "no CPython ${PY_VERSION:-3.13} for $ARCH in python-build-standalone $PBS_TAG" >&2; exit 1; }
TARBALL="${LINE##* }"
PY_VERSION="$(echo "$TARBALL" | sed -E 's/^cpython-([0-9.]+)\+.*/\1/')"
echo "Python $PY_VERSION (python-build-standalone $PBS_TAG)"
TGZ="$(fetch "$PBS_URL/$TARBALL")"
echo "${LINE%% *}  $TGZ" | shasum -a 256 -c - >/dev/null
mkdir -p "$RUNTIME"
tar -xzf "$TGZ" -C "$RUNTIME" --strip-components=1
# Tk and the IDLE/pydoc launchers are never used; their scripts also carry
# the build machine's absolute path.
rm -rf "$RUNTIME/bin/idle3"* "$RUNTIME/bin/pydoc3"* "$RUNTIME/bin/pip"* \
       "$RUNTIME/lib/python3.13/idlelib" "$RUNTIME/lib/python3.13/turtledemo"

SITE="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
cp "$HERE/sitecustomize.py" "$SITE/"

# pyinstaller is in requirements.txt for the old build only.
grep -viE '^\s*pyinstaller' "$REPO/requirements.txt" > "$CACHE/requirements-app.txt"
"$PY" -m pip install --disable-pip-version-check --no-warn-script-location --prefer-binary \
    -r "$CACHE/requirements-app.txt" -r "$REPO/requirements-czi.txt" \
    -r "$REPO/packaging/windows/requirements-bundle.txt" -r "$HERE/requirements-mac.txt" \
    -c "$CONSTRAINTS"
# Never used at run time (see build.ps1): TensorFlow's C++ headers and two
# build tools pip pulls in as dependencies.
rm -rf "$SITE/tensorflow/include"
"$PY" -m pip uninstall -y -q cmake libclang 2>/dev/null || true

"$PY" -m pip freeze --disable-pip-version-check > "$RES/BUNDLE_VERSIONS.txt"
{ echo "python==$PY_VERSION"; echo "python-build-standalone==$PBS_TAG"; } >> "$RES/BUNDLE_VERSIONS.txt"

# --- The repository -------------------------------------------------------
for d in app scripts analysis atlas; do cp -R "$REPO/$d" "$RES/$d"; done
cp "$REPO/config.example.json" "$REPO/README.md" "$RES/"
find "$RES" -name __pycache__ -type d -prune -exec rm -rf {} +
# Windows-only leftovers in app/ would only confuse.
rm -f "$RES/app/lsapp.spec"

# --- StarDist model -------------------------------------------------------
KERAS_HOME="$RES/keras" SSL_CERT_FILE="$("$PY" -c 'import certifi; print(certifi.where())')" \
    "$PY" -c "from stardist.models import StarDist2D; StarDist2D.from_pretrained('2D_versatile_fluo')"
# stardist leaves <key> as a symlink to <key>_extracted; a real folder keeps the
# bundle free of links that a copy or a zip could break.
MODEL="$RES/keras/models/StarDist2D/2D_versatile_fluo"
if [ -L "$MODEL/2D_versatile_fluo" ] && [ -d "$MODEL/2D_versatile_fluo_extracted" ]; then
    rm "$MODEL/2D_versatile_fluo"
    cp -R "$MODEL/2D_versatile_fluo_extracted" "$MODEL/2D_versatile_fluo"
fi

# --- What the updater compares --------------------------------------------
"$PY" "$REPO/packaging/make_update.py" --version "$VERSION" --bundle "$RES" --version-file-only
echo "BUNDLE_VERSIONS.txt (copy to packaging/macos/bundle-lock.txt to pin this runtime):"
cat "$RES/BUNDLE_VERSIONS.txt"

# Precompile, so nothing inside the app is written on first start (that would
# also break its signature). Not checked: some packages ship test fixtures that
# are deliberately not valid Python.
"$PY" -m compileall -q -j 0 "$RES" >/dev/null || true

# --- The .app shell -------------------------------------------------------
cat > "$APP/Contents/MacOS/$NAME" <<'SH'
#!/bin/bash
# Finder starts this; it hands over to the bundled Python.
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
cd "$RES"
exec "$RES/runtime/bin/python3" -m app "$@"
SH
chmod +x "$APP/Contents/MacOS/$NAME"

PLIST_VERSION="$(echo "$VERSION" | sed -E 's/^([0-9]+(\.[0-9]+){0,2}).*/\1/')"
case "$PLIST_VERSION" in [0-9]*) ;; *) PLIST_VERSION="0.0.0" ;; esac
if [ -f "$HERE/AppIcon.icns" ]; then cp "$HERE/AppIcon.icns" "$RES/AppIcon.icns"; fi
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$NAME</string>
    <key>CFBundleDisplayName</key><string>$NAME</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleExecutable</key><string>$NAME</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$PLIST_VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
    <key>LSArchitecturePriority</key><array><string>arm64</string></array>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
    <key>NSHumanReadableCopyright</key><string>mbel-tech</string>
</dict>
</plist>
PLIST

# Every library must load on the oldest macOS promised above.
"$PY" "$HERE/check_macos_min.py" "$APP" "$MIN_MACOS"

# Ad-hoc signature: no Apple Developer account behind it, but Apple Silicon
# Macs refuse to start code with no signature at all, and an ad-hoc signed app
# gets the "Open Anyway" route in System Settings instead of "is damaged".
codesign --force --deep --sign - "$APP"
codesign --verify --deep "$APP"

# Where the size goes, so slimming is done from numbers.
du -sm "$RES"/* "$SITE"/* 2>/dev/null | sort -rn | head -25
echo "Built $APP ($(du -sm "$APP" | cut -f1) MB)"

# --- Disk image -----------------------------------------------------------
if [ "$DMG" = 1 ]; then
    STAGE="$(mktemp -d)"
    ditto "$APP" "$STAGE/$NAME.app"
    ln -s /Applications "$STAGE/Applications"
    cp "$HERE/How to open.txt" "$STAGE/"
    DMG_PATH="$OUT/$PRODUCT-$VERSION-mac.dmg"
    rm -f "$DMG_PATH"
    hdiutil create -quiet -volname "$NAME" -srcfolder "$STAGE" -fs HFS+ -format ULFO "$DMG_PATH"
    rm -rf "$STAGE"
    echo "Built $DMG_PATH ($(du -sm "$DMG_PATH" | cut -f1) MB)"
fi
