#!/bin/bash
# Build Deckhand as one file: a launcher with the app attached (scripts/make_app_file.sh), quick to start.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m pip install --user --upgrade -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm --clean --onedir --windowed --name deckhand --paths . \
  --add-data deckhand/logos:deckhand/logos --add-data deckhand/fonts:deckhand/fonts deckhand/__main__.py
scripts/make_app_file.sh dist/deckhand dist/deckhand-linux-x86_64

echo ""
echo "Built: $ROOT_DIR/dist/deckhand-linux-x86_64"
