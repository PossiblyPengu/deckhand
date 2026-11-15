#!/bin/bash
# One-command Deckhand installer for Steam Deck.
# Downloads the latest prebuilt binary — no pip, pacman, or developer mode needed.
#
#   curl -fsSL https://raw.githubusercontent.com/PossiblyPengu/deckhand/main/get.sh | bash
#
# Add --with-arcadia to also install Arcadia, its sister app for retro games (bash -s -- --with-arcadia).
set -euo pipefail

REPO="PossiblyPengu/deckhand"
# The latest release: what GitHub's release workflow built from main (see .github/workflows/release.yml).
# DECKHAND_RELEASE_URL points at another folder with the same two files (for testing).
BASE="${DECKHAND_RELEASE_URL:-https://github.com/$REPO/releases/latest/download}"
ASSET="deckhand-linux-x86_64"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "=== Deckhand Installer ==="

echo "Downloading the latest Deckhand…"
curl -fsSL -o "$TMP/$ASSET.sha256" "$BASE/$ASSET.sha256" || { echo "Couldn't reach GitHub — check your internet connection."; exit 1; }
curl -fL --progress-bar -o "$TMP/$ASSET" "$BASE/$ASSET"
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
  # (Deckhand's bundled font library reads SteamOS's newer font settings and warns about each line it doesn't know:
  # harmless, so those lines are left out. Anything else it says still shows.)
  "$BIN_DIR/deckhand" --add-arcadia 2> >(grep -v '^Fontconfig warning' >&2) \
    || echo "  (Arcadia can be added later from Deckhand: Add-ons → Arcadia)"
fi
