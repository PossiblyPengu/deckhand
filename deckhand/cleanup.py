"""Free up space: what uninstalled Steam games leave behind, and Deckhand's own leftover downloads.

When a game is uninstalled Steam keeps its shader cache (steamapps/shadercache/<appid>) and its Windows
setup (steamapps/compatdata/<appid>); on a Deck those add up to gigabytes. Shader caches are safe to
delete (Steam builds them again). A Windows setup can hold the saves of a game without Steam Cloud,
so it's offered separately, with that warning.

A game counts as installed if any Steam library says so, including an SD card that isn't in right now
(Steam's libraryfolders.vdf lists each library's games), and every non-Steam shortcut counts too.
If Steam's list of libraries can't be read, nothing is offered at all.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import core, updater

STORE_API = "https://store.steampowered.com/api/appdetails?appids={}&filters=basic"


@dataclass
class Leftover:
    kind: str  # "shadercache", "compatdata" or "download"
    path: Path
    appid: int = 0
    size: int = 0
    name: str = ""


def installed_appids(roots: Iterable[Path]) -> set[int]:
    """Every app any library has, from each root's libraryfolders.vdf and the manifests that can be seen."""
    ids: set[int] = set()
    for root in roots:
        try:
            text = (root / "steamapps/libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for block in re.findall(r'"apps"\s*\{([^{}]*)\}', text):
            ids |= {int(i) for i in re.findall(r'"(\d+)"\s+"\d+"', block)}
        for lib in core.steam_library_dirs(root):
            try:
                ids |= {int(m.group(1)) for p in (lib / "steamapps").glob("appmanifest_*.acf")
                        if (m := re.fullmatch(r"appmanifest_(\d+)\.acf", p.name))}
            except OSError:
                continue
    return ids


def find(roots: Iterable[Path] | None = None, paths: core.Paths | None = None) -> list[Leftover]:
    """What can go, biggest first (sizes filled in)."""
    roots = list(core.steam_roots() if roots is None else roots)
    installed = installed_appids(roots)
    out: list[Leftover] = []
    if installed:  # (couldn't read Steam's libraries: offer nothing rather than everything)
        keep = installed | {core._entry_appid(e) for _cfg, e in core.steam_shortcuts(roots)} | {0}
        seen: set[Path] = set()
        for root in roots:
            for lib in core.steam_library_dirs(root):
                for kind in ("shadercache", "compatdata"):
                    try:
                        dirs = sorted((lib / "steamapps" / kind).iterdir())
                    except OSError:
                        continue
                    for d in dirs:
                        if d.name.isdigit() and int(d.name) not in keep and d.is_dir() and not d.is_symlink() \
                                and d.resolve() not in seen:
                            seen.add(d.resolve())
                            out.append(Leftover(kind, d, int(d.name), core.dir_size(d)))
    if paths is not None:
        try:
            out += [Leftover("download", f, 0, f.stat().st_size, f.name) for f in paths.downloads.iterdir()
                    if f.is_file() and not f.name.startswith(".")]  # (".x.download": still downloading)
        except OSError:
            pass
    return sorted(out, key=lambda x: x.size, reverse=True)


def name_games(items: list[Leftover], fetch: Callable[[str], bytes] | None = None, limit: int = 6) -> None:
    """Fill in the names of the biggest ones from the Steam store (best effort: offline is fine)."""
    for item in [i for i in items if i.appid and not i.name][:limit]:
        try:
            data = json.loads((fetch or (lambda url: updater.fetch(url, timeout=6)))(STORE_API.format(item.appid)))
            item.name = str(data[str(item.appid)]["data"]["name"])
        except Exception:  # noqa: BLE001 — unknown to the store (a non-Steam game), or offline
            continue


def delete(items: Iterable[Leftover]) -> int:
    """Delete them. Returns the bytes freed."""
    freed = 0
    for item in items:
        if item.path.is_dir():
            shutil.rmtree(item.path, ignore_errors=True)
        else:
            item.path.unlink(missing_ok=True)
        if not item.path.exists():
            freed += item.size
    return freed
