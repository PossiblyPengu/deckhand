"""Uninstalling Deckhand itself: what get.sh put on the Deck, and what Deckhand added for itself.

That is the app (~/.local/bin/deckhand), its menu entry and icon, the background update check (a systemd user timer)
and its own Steam shortcut. What Deckhand installed for you is left alone, and keeps working: programs, stores and
streaming services run from Steam through their launchers in Deckhand's data folder (~/.local/share/deckhand), which
stays, so each can still be found and removed by hand, or by installing Deckhand again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import core, updates

ICON_SIZES_GLOB = "icons/hicolor/*/apps/deckhand.png"


@dataclass
class Result:
    removed: list[str] = field(default_factory=list)
    left_in_steam: bool = False  # Steam is open: its shortcut has to be removed in Steam


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
    for f in app_files(exe, home):
        try:
            f.unlink()
            out.removed.append(str(f))
        except OSError:
            pass
    return out
