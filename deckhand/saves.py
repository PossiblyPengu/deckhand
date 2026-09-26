"""Save backups for installed Windows programs.

Windows games save in the user's profile: Documents (My Games…), Saved Games and AppData, sometimes the
shared Public Documents. In Deckhand those are inside the program's prefix, so uninstalling deletes
them, unless Steam Cloud or the game's own account keeps a copy. Deckhand zips those folders (without
caches, crash dumps and Windows' own files) into ~/.local/share/deckhand/save-backups/<program>/, and can
put a backup back, e.g. after reinstalling the program.
"""
from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path, PurePosixPath

from . import core

PROFILE_DIRS = ("Documents", "Saved Games", "AppData/Roaming", "AppData/Local", "AppData/LocalLow")
PUBLIC_DIRS = ("Documents",)
SKIP = {"temp", "tmp", "cache", "caches", "gpucache", "code cache", "shadercache", "shader cache", "d3dscache",
        "crashdumps", "crashes", "crashreports", "webcache", "htmlcache", "cefcache", "microsoft", "nvidia"}
KEEP = 5  # backups kept per program; older ones are deleted
INFO = "deckhand-backup.json"


def user_dir(pfx: Path) -> Path | None:
    """The Windows user's profile folder in a prefix ("steamuser" under Proton)."""
    users = pfx / "drive_c/users"
    try:
        found = sorted(d for d in users.iterdir() if d.is_dir() and not d.is_symlink() and d.name != "Public")
    except OSError:
        return None
    return next((d for d in found if d.name == "steamuser"), found[0] if found else None)


def _walk(top: Path, name: str) -> list[tuple[Path, str]]:
    """Files under a folder (never following links out of the prefix), minus caches."""
    if not top.is_dir() or top.is_symlink():
        return []
    out = []
    for dirpath, dirnames, files in os.walk(top):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP and not os.path.islink(os.path.join(dirpath, d))]
        rel = PurePosixPath(name, Path(dirpath).relative_to(top).as_posix())
        for f in files:
            p = Path(dirpath) / f
            if not p.is_symlink() and p.is_file():
                out.append((p, str(rel / f)))
    return out


def save_files(app: core.App) -> list[tuple[Path, str]]:
    """(file, name in the backup) for everything worth backing up. Names start with "user/" (the
    profile) or "Public/" (shared documents)."""
    pfx = Path(app.prefix) / "pfx"
    user = user_dir(pfx) if app.prefix else None
    if user is None:
        return []
    out = [item for d in PROFILE_DIRS for item in _walk(user / d, f"user/{d}")]
    out += [item for d in PUBLIC_DIRS for item in _walk(pfx / "drive_c/users/Public" / d, f"Public/{d}")]
    return out


def save_size(app: core.App) -> int:
    return core.files_size(p for p, _n in save_files(app))


def backup_dir(paths: core.Paths, name: str) -> Path:
    return paths.root / "save-backups" / core.slugify(name)


def backups(paths: core.Paths, name: str) -> list[Path]:
    """A program's backups, newest first."""
    try:
        return sorted(backup_dir(paths, name).glob("*.zip"), key=lambda p: p.name, reverse=True)
    except OSError:
        return []


def backup(app: core.App, paths: core.Paths, now: float | None = None) -> Path | None:
    """Zip the program's saves. None if there's nothing to back up."""
    files = save_files(app)
    if not files:
        return None
    folder = backup_dir(paths, app.name)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H.%M.%S", time.localtime(now))
    dest = folder / f"{stamp}.zip"
    tmp = folder / f".{stamp}.zip.partial"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(INFO, json.dumps({"name": app.name, "created": now or time.time(), "files": len(files)}))
            for path, name in files:
                z.write(path, name)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    for old in backups(paths, app.name)[KEEP:]:
        old.unlink(missing_ok=True)
    return dest


def restore(app: core.App, archive: Path) -> int:
    """Put a backup's saves into the program's prefix (replacing files with the same names). Returns
    how many files were restored."""
    pfx = Path(app.prefix) / "pfx"
    user = user_dir(pfx)
    if user is None:
        raise core.InstallError(f"{app.name}'s Windows folder has no user profile to restore into.")
    roots = {"user": user, "Public": pfx / "drive_c/users/Public"}
    count = 0
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            parts = PurePosixPath(info.filename).parts
            if info.is_dir() or info.filename == INFO or len(parts) < 2 or parts[0] not in roots \
                    or any(p in ("..", "") for p in parts) or info.filename.startswith("/"):
                continue
            target = roots[parts[0]].joinpath(*parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
            count += 1
    return count


def created(archive: Path) -> float:
    """When a backup was made."""
    try:
        with zipfile.ZipFile(archive) as z:
            return float(json.loads(z.read(INFO)).get("created") or 0)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        try:
            return archive.stat().st_mtime
        except OSError:
            return 0.0
