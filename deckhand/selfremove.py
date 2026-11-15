"""Uninstalling Deckhand itself: what get.sh put on the Deck, and what Deckhand added for itself.

That is the app (~/.local/bin/deckhand, and its unpacked copy in ~/.cache/deckhand), its menu entry and icon, the background update check (a systemd user timer)
and its own Steam shortcut. What Deckhand installed for you is left alone, and keeps working: programs, stores and
streaming services run from Steam through their launchers in Deckhand's data folder (~/.local/share/deckhand), which
stays, so each can still be found and removed by hand, or by installing Deckhand again.

Steam undoes changes to its shortcuts made while it's open, and with Deckhand gone nothing would remove its shortcut
later. So with Steam open, the menu entry and icon go at once, and the rest waits: Deckhand's background timer runs
`deckhand --finish-uninstall` until Steam is closed, which then removes the shortcut, the app, the timer and itself.
Opened meanwhile from that shortcut, Deckhand only says it's uninstalled.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import core, updates

ICON_SIZES_GLOB = "icons/hicolor/*/apps/deckhand.png"


@dataclass
class Result:
    removed: list[str] = field(default_factory=list)
    left_in_steam: bool = False  # Steam is open: its shortcut goes the next time Steam is closed (see finish)


def app_files(exe: Path, home: Path | None = None) -> list[Path]:
    data = (home or Path.home()) / ".local/share"
    return [exe, data / "applications/deckhand.desktop", *sorted(data.glob(ICON_SIZES_GLOB))]


def uninstall(exe: Path | None, home: Path | None = None, roots: Iterable[Path] | None = None,
              running: bool | None = None, systemctl=None) -> Result:
    """Remove Deckhand. `exe`: the installed app (None when running from source: then nothing is touched)."""
    out = Result()
    if exe is None:
        return out  # (running from source: the installed Deckhand, if any, isn't this one)
    if systemctl is not None:
        updates.remove_timer(home, systemctl)
    else:
        updates.remove_timer(home)
    out.removed.append("update check")
    if running is None:
        running = core.steam_is_running()
    if core.find_shortcut(str(exe), roots):
        if running:
            out.left_in_steam = True
        else:
            core.remove_shortcuts_for(str(exe), 0, roots)
            out.removed.append("Steam shortcut")
    if out.left_in_steam:  # (the app stays until Steam is closed, to remove its shortcut then)
        try:
            mark = marker(home)
            mark.parent.mkdir(parents=True, exist_ok=True)
            mark.write_text(str(exe), encoding="utf-8")
            updates.install_timer(exe, home, systemctl or updates._systemctl, updates.FINISH_UNINSTALL)
        except OSError:
            pass
    else:
        _remove_app(exe, home, out)
    for f in app_files(exe, home)[1:]:  # (the menu entry and icons)
        try:
            f.unlink()
            out.removed.append(str(f))
        except OSError:
            pass
    return out


def _remove_app(exe: Path, home: Path | None, out: Result) -> None:
    try:
        exe.unlink()
        out.removed.append(str(exe))
    except OSError:
        pass
    unpacked = cache_dir(home)  # (the launcher's unpacked copies of the app)
    if unpacked.is_dir():
        shutil.rmtree(unpacked, ignore_errors=True)
        out.removed.append(str(unpacked))


def marker(home: Path | None = None) -> Path:
    """There while an uninstall waits for Steam to close; holds the app's path."""
    return cache_dir(home) / "uninstalled"


def uninstalling(home: Path | None = None) -> bool:
    return marker(home).is_file()


def finish(home: Path | None = None, roots: Iterable[Path] | None = None, running: bool | None = None,
           systemctl=None) -> bool:
    """`deckhand --finish-uninstall` (the timer): once Steam is closed, remove the shortcut, the app and the timer.
    True when done (or there was nothing to finish)."""
    try:
        exe = Path(marker(home).read_text(encoding="utf-8").strip())
    except OSError:
        return True
    if running is None:
        running = core.steam_is_running()
    if running:
        return False
    out = Result()
    core.remove_shortcuts_for(str(exe), 0, roots)
    if systemctl is not None:
        updates.remove_timer(home, systemctl)
    else:
        updates.remove_timer(home)
    _remove_app(exe, home, out)  # (the marker goes with the cache folder)
    return True


def cache_dir(home: Path | None = None) -> Path:
    base = os.environ.get("XDG_CACHE_HOME") if home is None else None
    return (Path(base) if base else (home or Path.home()) / ".cache") / "deckhand"
