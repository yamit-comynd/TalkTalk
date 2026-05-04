#!/usr/bin/env bash
# build.sh — build TalkTalk.app for macOS distribution using PyInstaller
#
# Usage:
#   ./build.sh          — full standalone build → dist/TalkTalk.app
#   ./build.sh --dmg    — full build + wrap in a .dmg for sharing
#
# Uses .venv-build (Python 3.13) so your dev .venv is untouched.
# py2app was dropped — it crashes on Python 3.13/3.14 due to an AST bug.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"   # project root

MAKE_DMG=false

for arg in "$@"; do
  case "$arg" in
    --dmg)  MAKE_DMG=true ;;
    --help) echo "Usage: $0 [--dmg]"; exit 0 ;;
  esac
done

# ── 1. Find Python 3.13 ──────────────────────────────────────────────────────
BUILD_PYTHON=""
for candidate in python3.13 python3.12 python3.11; do
  if command -v "$candidate" &>/dev/null; then
    BUILD_PYTHON="$candidate"
    break
  fi
done

if [ -z "$BUILD_PYTHON" ]; then
  echo "ERROR: Need Python 3.11–3.13. Install with: brew install python@3.13"
  exit 1
fi

echo "==> Build Python: $($BUILD_PYTHON --version)"

# ── 2. Create/reuse isolated build venv ──────────────────────────────────────
VENV=".venv-build"
if [ ! -d "$VENV" ]; then
  echo "==> Creating $VENV"
  $BUILD_PYTHON -m venv "$VENV"
fi
source "$VENV/bin/activate"

# ── 3. Install deps ───────────────────────────────────────────────────────────
echo "==> Installing dependencies"
python -m pip install --quiet --upgrade pip setuptools
python -m pip install --quiet pyinstaller
python -m pip install --quiet -r requirements.txt

# ── 4. Clean ──────────────────────────────────────────────────────────────────
echo "==> Cleaning build/ and dist/"
# Kill any running TalkTalk to unlock files
pkill -f "TalkTalk.app" 2>/dev/null || true
sleep 0.5
# Force remove with sudo if needed (handles permission edge cases)
rm -rf build/ dist/ || (echo "Retrying with elevated permissions..." && rm -rf build/ dist/)

# ── 5. Build ──────────────────────────────────────────────────────────────────
echo "==> Running PyInstaller…"
python -m PyInstaller TalkTalk.spec --noconfirm

APP_PATH="dist/TalkTalk.app"
if [ ! -d "$APP_PATH" ]; then
  echo "ERROR: build failed — $APP_PATH not found" >&2
  exit 1
fi

# ── 6. Ad-hoc code sign ───────────────────────────────────────────────────────
# macOS TCC (privacy permissions) requires a consistent code-signing identity
# to show Microphone / Input Monitoring / Accessibility prompts.
# Ad-hoc signing ("-") gives the bundle a stable identity without needing a
# paid Apple Developer certificate.
#
# We sign leaf binaries first (inside-out), then the app bundle last.
# --force    : overwrite any existing signature
# --sign -   : ad-hoc identity (no Developer ID needed)
# --timestamp=none : skip Apple's timestamp server (works offline / in CI)
echo ""
echo "==> Ad-hoc signing $APP_PATH"

# 1. Sign every .dylib and .so inside the bundle
find "$APP_PATH" \( -name "*.dylib" -o -name "*.so" \) -print0 \
  | xargs -0 -I{} codesign --force --sign - --timestamp=none {}

# 2. Sign nested helper executables (Python interpreter, etc.)
find "$APP_PATH/Contents/MacOS" -type f -perm +111 \
  | grep -v "^$APP_PATH/Contents/MacOS/TalkTalk$" \
  | xargs -I{} codesign --force --sign - --timestamp=none {} 2>/dev/null || true

# 3. Sign the main executable with entitlements
codesign --force --sign - --timestamp=none \
  --entitlements packaging/entitlements.plist \
  "$APP_PATH/Contents/MacOS/TalkTalk"

# 4. Sign the app bundle itself (must be last)
codesign --force --sign - --timestamp=none \
  --entitlements packaging/entitlements.plist \
  "$APP_PATH"

echo "==> Signing complete"
codesign --display --verbose=2 "$APP_PATH" 2>&1 | head -10

echo ""
echo "==> Build complete: $APP_PATH"
du -sh "$APP_PATH"

# ── 7. Install to /Applications ───────────────────────────────────────────────
# Each build produces a new binary hash (ad-hoc signing = hash-based identity).
# macOS TCC ties permissions to that hash, so they must be reset after every
# build — otherwise macOS keeps silently denying the new binary.
# We install automatically and reset TCC so the permission ask is predictable.
echo ""
echo "==> Installing to /Applications"
pkill -f "TalkTalk.app" 2>/dev/null || true
sleep 0.5
rm -rf /Applications/TalkTalk.app
cp -R "$APP_PATH" /Applications/TalkTalk.app

echo "==> Resetting TCC permissions (binary hash changed with this build)"
tccutil reset ListenEvent   com.talktalk.app 2>/dev/null || true
tccutil reset Accessibility com.talktalk.app 2>/dev/null || true
tccutil reset Microphone    com.talktalk.app 2>/dev/null || true
tccutil reset AppleEvents   com.talktalk.app 2>/dev/null || true
echo "    TCC reset — app will ask for permissions on next launch."

# ── 8. Optional .dmg ─────────────────────────────────────────────────────────
if [ "$MAKE_DMG" = true ]; then
  DMG_PATH="dist/TalkTalk.dmg"
  echo ""
  echo "==> Creating $DMG_PATH"
  hdiutil create \
    -volname "TalkTalk" \
    -srcfolder "$APP_PATH" \
    -ov -format UDZO \
    "$DMG_PATH"
  echo "==> DMG ready: $DMG_PATH"
  du -sh "$DMG_PATH"
fi

echo ""
echo "==> Ready.  Launch with:"
echo "    open /Applications/TalkTalk.app"
echo ""
echo "    macOS will ask for Microphone, Input Monitoring, and Accessibility"
echo "    — grant all three.  They persist until the next build."
echo ""
echo "  • Whisper models are cached in ~/.cache/huggingface/ after first use"
echo "  • Ollama must be running separately for Transliterate mode"
