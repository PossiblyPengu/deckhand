#!/bin/bash
# Build the single-file app into bin/ and write bin/deckhand.json, which get.sh and the in-app
# updater read. Usage: scripts/publish_bin.sh ["What's new, one line"]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
NOTES="${1:-}"
VERSION="$(python3 -c 'import deckhand; print(deckhand.__version__)')"

python3 -m PyInstaller --noconfirm --clean --onefile --windowed --strip --name deckhand \
  --paths . --add-data deckhand/logos:deckhand/logos deckhand/__main__.py
mkdir -p bin
cp dist/deckhand bin/deckhand-linux-x86_64
(cd bin && sha256sum deckhand-linux-x86_64 > deckhand-linux-x86_64.sha256)
SHA="$(cut -d' ' -f1 bin/deckhand-linux-x86_64.sha256)"
python3 - "$VERSION" "$SHA" "$NOTES" > bin/deckhand.json <<'PY'
import json, sys
version, sha, notes = sys.argv[1:4]
print(json.dumps({"version": version, "sha256": sha, "notes": notes}, indent=2))
PY
echo "bin/ now holds Deckhand $VERSION ($SHA)"
