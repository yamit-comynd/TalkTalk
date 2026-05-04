#!/usr/bin/env bash
# distribute.sh — build, sign with Developer ID, notarize, and wrap in a DMG
#
# Prerequisites (one-time setup):
#   1. Enroll in the Apple Developer Program (developer.apple.com, $99/yr)
#   2. In Xcode → Settings → Accounts, add your Apple ID and download:
#        • "Developer ID Application: Your Name (TEAMID)"   ← for signing
#   3. Create an app-specific password at appleid.apple.com
#   4. Fill in the four variables below (or export them as env vars)
#
# Usage:
#   ./distribute.sh              — build + sign + notarize + create DMG
#   ./distribute.sh --skip-build — reuse last build (just re-sign/notarize)
#
# Output: dist/TalkTalk-<version>.dmg  (ready to share with testers)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"   # project root

# ── Configuration (fill these in or export as env vars) ───────────────────────

DEVELOPER_ID="${DEVELOPER_ID:-}"          # e.g. "Developer ID Application: Jane Doe (ABC123XYZ)"
APPLE_ID="${APPLE_ID:-}"                  # e.g. "jane@example.com"
NOTARY_PASSWORD="${NOTARY_PASSWORD:-}"    # app-specific password from appleid.apple.com
TEAM_ID="${TEAM_ID:-}"                    # 10-char team ID, e.g. "ABC123XYZ"
APP_VERSION="${APP_VERSION:-0.0.1}"

# ── Validate ──────────────────────────────────────────────────────────────────

if [[ -z "$DEVELOPER_ID" || -z "$APPLE_ID" || -z "$NOTARY_PASSWORD" || -z "$TEAM_ID" ]]; then
  echo ""
  echo "ERROR: Missing signing credentials."
  echo ""
  echo "  Set these four variables (edit distribute.sh or export before running):"
  echo "    DEVELOPER_ID   — e.g. 'Developer ID Application: Jane Doe (ABC123XYZ)'"
  echo "    APPLE_ID       — your Apple ID email"
  echo "    NOTARY_PASSWORD — app-specific password from appleid.apple.com"
  echo "    TEAM_ID        — 10-character team ID (shown in developer.apple.com)"
  echo ""
  echo "  One-time setup:"
  echo "    1. developer.apple.com → Certificates → create 'Developer ID Application'"
  echo "    2. Open the downloaded .cer in Keychain Access to install it"
  echo "    3. appleid.apple.com → Sign-In and Security → App-Specific Passwords"
  echo ""
  exit 1
fi

SKIP_BUILD=false
for arg in "$@"; do
  [[ "$arg" == "--skip-build" ]] && SKIP_BUILD=true
done

APP_PATH="dist/TalkTalk.app"
DMG_NAME="TalkTalk-${APP_VERSION}.dmg"
DMG_PATH="dist/${DMG_NAME}"

# ── 1. Build ──────────────────────────────────────────────────────────────────

if [[ "$SKIP_BUILD" == false ]]; then
  echo "==> Building TalkTalk ${APP_VERSION}…"
  # Update version in spec before building
  sed -i '' "s/version=\"[^\"]*\"/version=\"${APP_VERSION}\"/" TalkTalk.spec
  ./scripts/build.sh   # builds to dist/TalkTalk.app, installs to /Applications, resets TCC
fi

if [[ ! -d "$APP_PATH" ]]; then
  echo "ERROR: $APP_PATH not found. Run without --skip-build first."
  exit 1
fi

# ── 2. Re-sign with Developer ID (replaces ad-hoc signature from build.sh) ───

echo ""
echo "==> Signing with Developer ID: ${DEVELOPER_ID}"

# Sign all dylibs and .so files first (inside-out)
find "$APP_PATH" \( -name "*.dylib" -o -name "*.so" \) -print0 \
  | xargs -0 -I{} codesign --force --sign "$DEVELOPER_ID" --timestamp \
      --options runtime {}

# Sign nested executables
find "$APP_PATH/Contents/MacOS" -type f -perm +111 \
  | grep -v "^${APP_PATH}/Contents/MacOS/TalkTalk$" \
  | xargs -I{} codesign --force --sign "$DEVELOPER_ID" --timestamp \
      --options runtime {} 2>/dev/null || true

# Sign the main binary with entitlements
codesign --force --sign "$DEVELOPER_ID" --timestamp \
  --options runtime \
  --entitlements packaging/entitlements.plist \
  "$APP_PATH/Contents/MacOS/TalkTalk"

# Sign the bundle
codesign --force --sign "$DEVELOPER_ID" --timestamp \
  --options runtime \
  --entitlements packaging/entitlements.plist \
  "$APP_PATH"

echo "==> Verifying signature…"
codesign --verify --deep --strict "$APP_PATH" && echo "    Signature OK"
spctl --assess --type execute --verbose "$APP_PATH" 2>&1 | head -3

# ── 3. Create DMG ─────────────────────────────────────────────────────────────

echo ""
echo "==> Creating ${DMG_PATH}"
rm -f "$DMG_PATH"
hdiutil create \
  -volname "TalkTalk ${APP_VERSION}" \
  -srcfolder "$APP_PATH" \
  -ov -format UDZO \
  "$DMG_PATH"

# ── 4. Notarize ───────────────────────────────────────────────────────────────

echo ""
echo "==> Submitting for notarization (takes 1–5 min)…"
xcrun notarytool submit "$DMG_PATH" \
  --apple-id    "$APPLE_ID" \
  --password    "$NOTARY_PASSWORD" \
  --team-id     "$TEAM_ID" \
  --wait

# ── 5. Staple ─────────────────────────────────────────────────────────────────

echo ""
echo "==> Stapling notarization ticket…"
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH" && echo "    Staple OK"

# ── 6. Done ───────────────────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════════"
echo "  ✓  Ready to distribute: ${DMG_PATH}"
echo "  $(du -sh "$DMG_PATH" | cut -f1)  — share this file with your testers"
echo ""
echo "  Testers:"
echo "    1. Download TalkTalk-${APP_VERSION}.dmg"
echo "    2. Open it and drag TalkTalk to Applications"
echo "    3. Launch — macOS Gatekeeper will accept it silently"
echo "    4. Grant Input Monitoring + Accessibility when prompted"
echo "══════════════════════════════════════════════════════"
