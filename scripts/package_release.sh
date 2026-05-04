#!/usr/bin/env bash
# package_release.sh — build DMG + generate tester guide PDF, drop both in release/vVERSION/
#
# Usage:
#   ./package_release.sh             — packages version 0.0.1
#   ./package_release.sh 0.0.2       — packages a specific version
#   ./package_release.sh --skip-build 0.0.1  — reuse existing dist/TalkTalk.app

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"   # project root

# ── Args ──────────────────────────────────────────────────────────────────────

SKIP_BUILD=false
VERSION="0.0.1"
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=true ;;
    *)            VERSION="$arg"  ;;
  esac
done

RELEASE_DIR="release/v${VERSION}"
DMG_SRC="dist/TalkTalk.dmg"
DMG_OUT="${RELEASE_DIR}/TalkTalk-${VERSION}.dmg"
PDF_OUT="${RELEASE_DIR}/TalkTalk-Tester-Guide-${VERSION}.pdf"
HTML_OUT="${RELEASE_DIR}/TalkTalk-Tester-Guide-${VERSION}.html"

echo ""
echo "══════════════════════════════════════════════════════"
echo "  TalkTalk  release packager  v${VERSION}"
echo "══════════════════════════════════════════════════════"

# ── 1. Build app + DMG ────────────────────────────────────────────────────────

if [[ "$SKIP_BUILD" == false ]]; then
  echo ""
  echo "==> Building TalkTalk ${VERSION}…"
  APP_VERSION="$VERSION" ./scripts/build.sh --dmg
else
  echo ""
  echo "==> Skipping build (--skip-build)"
  if [[ ! -f "$DMG_SRC" ]]; then
    echo "ERROR: $DMG_SRC not found. Run without --skip-build first."
    exit 1
  fi
fi

# ── 2. Set up release directory ───────────────────────────────────────────────

echo ""
echo "==> Creating ${RELEASE_DIR}/"
mkdir -p "$RELEASE_DIR"

cp "$DMG_SRC" "$DMG_OUT"
echo "    Copied DMG → ${DMG_OUT}"

# ── 3. Generate tester guide PDF ─────────────────────────────────────────────
#
# Strategy (tried in order):
#   A. pandoc + weasyprint  — pip-installable, no LaTeX needed, clean output
#   B. pandoc alone         — generates HTML; user prints to PDF from browser
#   C. Pure Python          — markdown → styled HTML; user prints to PDF
#
# Install pandoc:   brew install pandoc
# Install weasyprint: pip install weasyprint  (or: pip install "weasyprint<61")

echo ""
echo "==> Generating tester guide…"

PDF_GENERATED=false
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Strategy A: pandoc + weasyprint
if command -v pandoc &>/dev/null && python3 -c "import weasyprint" 2>/dev/null; then
  echo "    Using pandoc + weasyprint"
  pandoc docs/TESTER_GUIDE.md \
    --standalone \
    --metadata title="TalkTalk Tester Guide v${VERSION}" \
    --pdf-engine=weasyprint \
    -o "$PDF_OUT"
  PDF_GENERATED=true

# Strategy B: pandoc → HTML → Chrome headless → PDF
elif command -v pandoc &>/dev/null; then
  echo "    pandoc found — generating HTML then converting via Chrome"
  pandoc docs/TESTER_GUIDE.md \
    --standalone \
    --metadata title="TalkTalk Tester Guide v${VERSION}" \
    -o "$HTML_OUT"
  if [[ -x "$CHROME" ]]; then
    "$CHROME" --headless --disable-gpu \
      --print-to-pdf="$PDF_OUT" \
      --print-to-pdf-no-header \
      "file://$(pwd)/${HTML_OUT}" 2>/dev/null && PDF_GENERATED=true
  fi

# Strategy C: pure Python → styled HTML → Chrome headless → PDF
else
  echo "    pandoc not found — generating styled HTML via Python"
  python3 scripts/generate_guide_html.py "$HTML_OUT" "$VERSION"
  if [[ -x "$CHROME" ]]; then
    echo "    Converting HTML → PDF via Chrome headless"
    "$CHROME" --headless --disable-gpu \
      --print-to-pdf="$PDF_OUT" \
      --print-to-pdf-no-header \
      "file://$(pwd)/${HTML_OUT}" 2>/dev/null && PDF_GENERATED=true
  fi
fi

# ── 4. Summary ────────────────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Release v${VERSION} ready in: ${RELEASE_DIR}/"
echo ""
echo "  $(du -sh "$DMG_OUT" | cut -f1)  ${DMG_OUT}"

if [[ "$PDF_GENERATED" == true ]]; then
  echo "  $(du -sh "$PDF_OUT"  | cut -f1)  ${PDF_OUT}"
else
  echo "  ⚠  PDF not generated automatically."
  echo "     Styled HTML is at: ${HTML_OUT}"
  echo ""
  echo "  To generate the PDF (pick one):"
  echo "    • Open ${HTML_OUT} in Safari → File → Export as PDF"
  echo "    • brew install pandoc && pip install weasyprint, then re-run"
fi

echo ""
echo "  Share both files with your testers."
echo "══════════════════════════════════════════════════════"
