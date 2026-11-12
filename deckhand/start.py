"""The Start checklist: what a new Steam Deck needs, and which of it is already done."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import addons, core, stores, streaming


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    detail: str
    done: bool
    glyph: str
    optional: bool = False  # nice to have: shown, but not counted in "steps to go"


def to_go(steps: list[Step]) -> int:
    """How many of the steps that count are left."""
    return sum(not st.done and not st.optional for st in steps)


def self_exe() -> Path | None:
    """The Deckhand binary Steam would run (None when running from source without get.sh)."""
    import sys

    exe = Path(sys.executable) if getattr(sys, "frozen", False) else Path.home() / ".local/bin/deckhand"
    return exe if exe.exists() else None


def steps(paths: core.Paths, roots: Iterable[Path] | None = None, home: Path | None = None,
          exe: Path | None = None, entries: list[tuple[Path, dict]] | None = None) -> list[Step]:
    roots = list(core.steam_roots() if roots is None else roots)
    entries = core.steam_shortcuts(roots) if entries is None else entries
    library = core.Library(paths).load()
    kinds = {a.kind for a in library}
    try:
        protons = [r for r in core.find_runtimes(roots, include_system_wine=False)]
    except OSError:
        protons = []
    store = any(stores.installed(s, library) for s in stores.STORES) or any(
        stores.spots(s, entries, paths.launchers) for s in stores.STORES)
    stream = "stream" in kinds or any(streaming.spots(s, entries, paths.launchers) for s in streaming.SERVICES)
    in_steam = bool(exe and core.find_shortcut(str(exe), entries=entries))
    # In the order a new Deck needs them: open Deckhand from Game Mode, the Proton that Windows games run on, then
    # games (stores first: most people have a library already), then the extras.
    return [
        Step("steam", "Deckhand in Game Mode",
             "In your Steam library: open it from Game Mode" if in_steam
             else "Add Deckhand to your Steam library, so you can open it from Game Mode", in_steam, "stack"),
        Step("proton", "Proton for Windows games",
             f"Ready: {protons[0].name}" if protons else "Windows games and stores run on it: get GE-Proton",
             bool(protons), "plus"),
        Step("stores", "Your game stores", "Epic and GOG (Heroic), Battle.net, EA, Ubisoft and more: your games in Steam",
             store, "bag"),
        Step("program", "A Windows game or program",
             "Pick a setup file; it ends up in Steam" if protons else "Do Proton first, then pick a setup file",
             "program" in kinds, "download"),
        Step("arcadia", "Retro games with Arcadia", "Deckhand's sister app: ROMs, emulators, BIOS and saves",
             addons.arcadia_app(home) is not None, "disc"),
        Step("stream", "Game streaming", "Xbox Cloud Gaming, GeForce NOW, Moonlight, PlayStation Remote Play…",
             stream, "signal"),
        Step("decky", "Decky Loader", "Optional: plugins for the Quick Access menu",
             addons.decky_version(home) is not None, "plus", optional=True),
    ]
