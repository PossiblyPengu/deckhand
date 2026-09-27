#!/bin/bash
# Build a self-contained single-file Deckhand executable.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m pip install --user --upgrade -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm --clean --onefile --windowed --name deckhand --paths . \
  --add-data deckhand/logos:deckhand/logos deckhand/__main__.py

echo ""
echo "Built: $ROOT_DIR/dist/deckhand"
