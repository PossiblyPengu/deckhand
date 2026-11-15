#!/bin/bash
# Make Deckhand's download: one file, a small launcher with the app attached.
#
#   scripts/make_app_file.sh DIST_DIR OUT
#
# DIST_DIR is PyInstaller's one-folder build (dist/deckhand: the deckhand program and its _internal folder).
# OUT starts with a short shell script and ends with that folder as a .tar.gz. The first start of each build unpacks
# it once into ~/.cache/deckhand/app-<id>; every start after that runs the unpacked program straight away. (A
# PyInstaller one-file build unpacks itself into /tmp on every start instead, which costs over a second on a Deck.)
# It stays one file, so get.sh, the checksum it's published with and Deckhand's self-update treat it as before.
set -euo pipefail

DIST="${1:?one-folder build}"
OUT="${2:?output file}"
[ -x "$DIST/deckhand" ] || { echo "$DIST/deckhand isn't there: build with PyInstaller --onedir first" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# Same input, same archive: fixed order, owners and times, and no name or time in the gzip header.
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime='2020-01-01 00:00Z' -C "$DIST" -cf - . \
  | gzip -n -6 > "$TMP/app.tar.gz"
ID="$(sha256sum "$TMP/app.tar.gz" | cut -c1-16)"

render() {  # the launcher, with the archive's size in it ($1)
  cat <<EOF
#!/bin/sh
# Deckhand: a launcher with the app attached (see scripts/make_app_file.sh in Deckhand's repository).
# The first start unpacks the app into ~/.cache/deckhand; later starts run it from there.
ID=$ID
SIZE=$1
SELF="\$(readlink -f "\$0")"
CACHE="\${XDG_CACHE_HOME:-\$HOME/.cache}/deckhand"
APP="\$CACHE/app-\$ID"
if [ ! -x "\$APP/deckhand" ]; then
  mkdir -p "\$CACHE" || exit 1
  NEW="\$(mktemp -d "\$CACHE/.unpacking-XXXXXX")" || exit 1
  if ! tail -c "\$SIZE" "\$SELF" | tar -xzf - -C "\$NEW"; then
    rm -rf "\$NEW"
    MSG="Deckhand couldn't unpack itself into \$CACHE (is the disk full?)."
    echo "\$MSG" >&2
    (notify-send Deckhand "\$MSG" || kdialog --error "\$MSG" || zenity --error --text="\$MSG") >/dev/null 2>&1
    exit 1
  fi
  touch "\$NEW"  # (unpacking gave it the archive's fixed date: it's as old as this unpacking, for the clean-up below)
  rm -rf "\$APP"
  mv "\$NEW" "\$APP" || { rm -rf "\$NEW"; [ -x "\$APP/deckhand" ] || exit 1; }  # (another start may have won)
  # Earlier versions' copies go, once they're a day old (one may still be running right after an update).
  find "\$CACHE" -maxdepth 1 -name 'app-*' ! -name "app-\$ID" -mmin +1440 -exec rm -rf {} + 2>/dev/null
fi
export DECKHAND_SELF="\$SELF"
exec "\$APP/deckhand" "\$@"
exit 1
EOF
}
SIZE="$(stat -c %s "$TMP/app.tar.gz")"
render "$SIZE" > "$OUT"
cat "$TMP/app.tar.gz" >> "$OUT"
chmod 755 "$OUT"
echo "$OUT: launcher + app ($(du -h "$OUT" | cut -f1), id $ID)"
