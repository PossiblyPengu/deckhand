"""Game stores other than Steam: Epic Games, GOG, Amazon, Battle.net, EA, Ubisoft, Rockstar, itch.io,
and Minecraft (Prism Launcher).

Two ways in, both ending with the store's app in the Steam library:
- Heroic (Epic Games, GOG and Amazon), itch and Prism Launcher are Linux apps. They come from Flathub and are added
  to Steam the way home streaming apps are (streaming.set_up): an App with kind "store".
- The other stores only have a Windows app. Deckhand downloads the store's own installer from its
  official address and installs it like any setup file (core.Installer, under the store's name):
  its own prefix, its own Steam shortcut, listed and uninstalled under Installed programs. The
  store's games are then installed and played from inside it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import core, streaming, updater

EXE_MAGIC, MSI_MAGIC = b"MZ", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # a Windows .exe; an .msi (OLE file)


@dataclass(frozen=True)
class Store:
    id: str
    name: str
    blurb: str
    site: str  # where it comes from, as shown to the user
    color: str  # badge colour
    app: str = ""  # a Linux app: its Flathub id
    local: tuple[str, ...] = ()  # names of a non-Flatpak copy: commands on PATH, or AppImage file names
    url: str = ""  # a Windows app: its official installer
    filename: str = ""  # what that installer is saved as (and called when downloaded by hand)
    spot: str = ""  # regex matching its name: in Deckhand's list or as a Steam shortcut made outside
    note: str = ""  # one more thing to know, said before it's added

    @property
    def is_windows(self) -> bool:
        return bool(self.url)

    @property
    def service(self) -> streaming.Service:
        """The Linux app, as the streaming code sets it up (Flathub, or a copy installed another way)."""
        return streaming.Service(self.id, self.name, self.blurb, app=self.app, local=self.local, color=self.color)


STORES = (
    Store("heroic", "Heroic Games Launcher", "Epic Games, GOG and Amazon games, made for Linux", "Flathub",
          "#c62d42", app="com.heroicgameslauncher.hgl", local=("heroic", "Heroic*.AppImage"),
          spot=r"heroic( games launcher)?",
          note="Sign in to Epic Games, GOG or Amazon in Heroic. It can add each game you install to Steam too."),
    Store("battlenet", "Battle.net", "Blizzard's games: Diablo, Overwatch…", "battle.net", "#148eff",
          url="https://downloader.battle.net/download/getInstallerForGame?os=win&gameProgram=BATTLENET_APP"
              "&version=Live",
          filename="Battle.net-Setup.exe", spot=r"battle\.?net( launcher)?"),
    Store("ea", "EA app", "EA's games: The Sims, Mass Effect…", "ea.com", "#ff4747",
          url="https://origin-a.akamaihd.net/EA-Desktop-Client-Download/installer-releases/EAappInstaller.exe",
          filename="EAappInstaller.exe", spot=r"ea|ea app|ea desktop|origin"),
    Store("ubisoft", "Ubisoft Connect", "Ubisoft's games: Assassin's Creed, Far Cry…", "ubisoft.com",
          "#0b62d6", url="https://static3.cdn.ubi.com/orbit/launcher_installer/UbisoftConnectInstaller.exe",
          filename="UbisoftConnectInstaller.exe", spot=r"ubisoft connect|uplay"),
    Store("epic", "Epic Games Launcher", "Epic's own app (Heroic is lighter)", "epicgames.com", "#3a3a3a",
          url="https://launcher-public-service-prod06.ol.epicgames.com/launcher/api/installer/download/"
              "EpicGamesLauncherInstaller.msi",
          filename="EpicGamesLauncherInstaller.msi", spot=r"epic games( launcher| store)?",
          note="Heroic Games Launcher plays Epic games too, and is lighter on the Deck."),
    Store("amazon", "Amazon Games", "Amazon's own app (Heroic has these too)", "amazon.com", "#ff9900",
          url="https://download.amazongames.com/AmazonGamesSetup.exe", filename="AmazonGamesSetup.exe",
          spot=r"amazon games( app)?", note="Heroic Games Launcher plays Amazon games too."),
    Store("rockstar", "Rockstar Games Launcher", "Red Dead Redemption 2, GTA V…",
          "rockstargames.com", "#d9a520",
          url="https://gamedownloads.rockstargames.com/public/installer/Rockstar-Games-Launcher.exe",
          filename="Rockstar-Games-Launcher.exe", spot=r"rockstar games( launcher)?"),
    Store("itch", "itch", "Indie games from itch.io", "Flathub", "#fa5c5c", app="io.itch.itch",
          spot=r"itch(\.io)?"),
    Store("prism", "Prism Launcher", "Minecraft: Java Edition, with mods and modpacks", "Flathub", "#6fb03a",
          app="org.prismlauncher.PrismLauncher", spot=r"prism( launcher)?|minecraft( launcher)?",
          note="Sign in with your Microsoft account in Prism Launcher to play the Minecraft you own."),
)


def store(store_id: str) -> Store | None:
    return next((s for s in STORES if s.id == store_id), None)


def _is_named(store_: Store, name: str) -> bool:
    return bool(store_.spot) and re.fullmatch(store_.spot, " ".join(name.split()), re.I) is not None


# ── What's set up already ────────────────────────────────────────────────────


def installed(store_: Store, apps: list[core.App]) -> core.App | None:
    """The store's app as Deckhand set it up: its Linux app, or its Windows app, whether installed from
    the Stores page or from its setup file picked by hand."""
    if not store_.is_windows:
        return next((a for a in apps if a.kind == "store" and a.id == f"store-{store_.id}"), None)
    return next((a for a in apps if a.kind == "program" and (
        Path(a.installer).name.lower() == store_.filename.lower() or _is_named(store_, a.name))), None)


def spots(store_: Store, entries: list[tuple[Path, dict]], launchers: Path) -> list[dict]:
    """Steam shortcuts for the store made outside Deckhand (by hand, or another tool). Only a shortcut
    named after the store counts: those Heroic makes for each game run Heroic, but are games."""
    out = []
    for _cfg, e in entries:
        if str(launchers) in str(e.get("Exe", "")):
            continue  # one of ours
        if _is_named(store_, str(e.get("AppName", e.get("appname", "")))) and e not in out:
            out.append(e)
    return out


# ── Linux apps ───────────────────────────────────────────────────────────────


def detect() -> set[str]:
    """Flatpak apps installed, plus "local:<store id>" for a store app installed another way."""
    return streaming.detect(streaming.installed_apps(), [s.service for s in STORES if not s.is_windows])


def set_up(store_: Store, paths: core.Paths, status: Callable[[str], None] = lambda s: None,
           roots=None) -> core.App:
    """Install the store's Linux app from Flathub if needed, and add it to Steam (only once)."""
    return streaming.set_up(store_.service, paths, status, roots, kind="store")


# ── Windows apps ─────────────────────────────────────────────────────────────


def download_path(store_: Store, paths: core.Paths) -> Path:
    return paths.downloads / store_.filename


def download(store_: Store, paths: core.Paths, progress: Callable[[int, int], None] = lambda done, total: None,
             opener=None) -> Path:
    """Download the store's official installer (fresh each time: they install the latest version)."""
    dest = download_path(store_, paths)

    def check(head: bytes) -> None:
        if not head.startswith(MSI_MAGIC if dest.suffix.lower() == ".msi" else EXE_MAGIC):
            raise core.InstallError(f"The {store_.name} download didn't look like its installer, so it wasn't "
                                    "used. Try again later.")

    return updater.download_to(store_.url, dest, progress, opener, check=check)
