#!/bin/bash
# One-command Deckhand installer for Steam Deck.
# Downloads the latest prebuilt binary — no pip, pacman, or developer mode needed.
#
#   curl -fsSL https://raw.githubusercontent.com/PossiblyPengu/deckhand/main/get.sh | bash
#
# Add --with-arcadia to also install Arcadia, its sister app for retro games (bash -s -- --with-arcadia).
set -euo pipefail

REPO="PossiblyPengu/deckhand"
# main once merged; the development branch until then. DECKHAND_BRANCH picks one explicitly.
BRANCHES="${DECKHAND_BRANCH:-main claude/steam-deck-windows-install-mkeivr}"
ASSET="deckhand-linux-x86_64"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "=== Deckhand Installer ==="

echo "Looking for the newest Deckhand…"
BEST_URL="" BEST_VER=""
for BRANCH in $BRANCHES; do
  # Read files at the branch's current commit: raw.githubusercontent caches branch names for minutes.
  REF=$(curl -fsSL -H "Accept: application/vnd.github.sha" "https://api.github.com/repos/$REPO/commits/$BRANCH" 2>/dev/null || true)
  [[ "$REF" =~ ^[0-9a-f]{40}$ ]] || REF="$BRANCH"
  URL="https://raw.githubusercontent.com/$REPO/$REF/bin"
  VER=$(curl -fsSL "$URL/deckhand.json" 2>/dev/null | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' | head -n 1 || true)
  [ -n "$VER" ] || continue
  if [ -z "$BEST_VER" ] || [ "$(printf '%s\n%s\n' "$BEST_VER" "$VER" | sort -V | tail -n 1)" = "$VER" ] && [ "$VER" != "$BEST_VER" ]; then
    BEST_URL="$URL" BEST_VER="$VER"
  fi
done
[ -n "$BEST_URL" ] || { echo "Couldn't reach GitHub — check your internet connection."; exit 1; }

echo "Downloading Deckhand $BEST_VER…"
curl -fsSL -o "$TMP/$ASSET.sha256" "$BEST_URL/$ASSET.sha256"
curl -fL --progress-bar -o "$TMP/$ASSET" "$BEST_URL/$ASSET"
[ -s "$TMP/$ASSET" ] || { echo "Couldn't download Deckhand — check your internet connection."; exit 1; }

echo "Verifying checksum…"
(cd "$TMP" && sha256sum -c "$ASSET.sha256")

mkdir -p "$BIN_DIR" "$DESKTOP_DIR"
install -m 755 "$TMP/$ASSET" "$BIN_DIR/deckhand"

cat > "$DESKTOP_DIR/deckhand.desktop" << DESKTOP
[Desktop Entry]
Name=Deckhand
Comment=Install Windows programs, game streaming and add-ons on Steam Deck
Exec=$BIN_DIR/deckhand %f
Icon=deckhand
Terminal=false
Type=Application
Categories=Game;Utility;
MimeType=application/x-ms-dos-executable;application/x-msdownload;application/vnd.microsoft.portable-executable;application/x-msi;application/x-ms-installer;
StartupNotify=true
DESKTOP
chmod +x "$DESKTOP_DIR/deckhand.desktop"
QT_QPA_PLATFORM=offscreen "$BIN_DIR/deckhand" --install-icon >/dev/null 2>&1 || true  # the "d." app icon
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo ""
echo "✓ $("$BIN_DIR/deckhand" --version) installed to $BIN_DIR/deckhand"
echo "  Find it in the app menu under Games, or run: deckhand"
echo ""
echo "Tip: in Desktop Mode you can right-click any setup .exe → Open With → Deckhand."
echo "To use it from Game Mode, open Deckhand and pick ☰ Menu → \"Add Deckhand to Steam\"."

if [ "${1:-}" = "--with-arcadia" ]; then
  echo ""
  "$BIN_DIR/deckhand" --add-arcadia || echo "  (Arcadia can be added later from Deckhand: Add-ons → Arcadia)"
fi
