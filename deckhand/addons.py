"""Deck add-ons from their official sources.

Nothing is repackaged. Decky's own installer (what decky.xyz links to) is downloaded and run as
is; it asks for the admin password in its own windows, so it needs Desktop Mode. EmuDeck is
downloaded the way its own install script (what emudeck.com runs) does it: the latest
EmuDeck.AppImage from its GitHub releases, into ~/Applications, and opened from there. GE-Proton
comes from its GitHub releases (proton.py). The rest are apps from Flathub, installed for this user
(no password) with the streaming code's Flatpak helpers; those meant for Game Mode are also added to
Steam, as Apps with kind "addon".
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import core, streaming, updater

DECKY_INSTALLER_URL = ("https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/"
                       "user_install_script.sh")
EMUDECK_RELEASE_API = "https://api.github.com/repos/EmuDeck/emudeck-electron/releases/latest"


@dataclass(frozen=True)
class Addon:
    id: str
    name: str
    blurb: str
    site: str
    color: str
    app: str = ""  # a Flathub app
    steam: bool = False  # (a Flathub app) also added to Steam, to use it in Game Mode

    @property
    def service(self) -> streaming.Service:
        """A Flathub add-on, as the streaming code sets it up."""
        return streaming.Service(self.id, self.name, self.blurb, app=self.app, color=self.color)


ADDONS = (
    Addon("decky", "Decky Loader", "Plugins for the Quick Access menu", "decky.xyz", "#6b35c8"),
    Addon("emudeck", "EmuDeck", "Sets up emulators and adds your retro games to Steam", "emudeck.com", "#d6313f"),
    Addon("retrodeck", "RetroDECK", "Emulators and your retro games in one app", "Flathub", "#8f5ae8",
          app="net.retrodeck.retrodeck", steam=True),
    Addon("ge-proton", "GE-Proton", "Proton with extra fixes: more games and installers work", "GitHub", "#c0392b"),
    Addon("protonup-qt", "ProtonUp-Qt", "Install and manage Proton versions for Steam and Heroic", "Flathub",
          "#5c6bc0", app="net.davidotek.pupgui2"),
    Addon("protontricks", "Protontricks", "Winetricks for your Steam games' Windows setups", "Flathub", "#8e2a2a",
          app="com.github.Matoking.protontricks"),
    Addon("ludusavi", "Ludusavi", "Back up and restore the saves of all your games", "Flathub", "#2e7d32",
          app="com.github.mtkennerly.ludusavi"),
    Addon("flatseal", "Flatseal", "Change what Flatpak apps may access (folders, SD card…)", "Flathub", "#4a86cf",
          app="com.github.tchx84.Flatseal"),
    Addon("lutris", "Lutris", "Game launcher for many stores, runners and emulators", "Flathub", "#ff9900",
          app="net.lutris.Lutris", steam=True),
    Addon("bottles", "Bottles", "Run Windows programs in separate, managed setups", "Flathub", "#3584e4",
          app="com.usebottles.bottles", steam=True),
    Addon("discord", "Discord", "Voice and text chat, in Game Mode too", "Flathub", "#5865f2",
          app="com.discordapp.Discord", steam=True),
)


def addon(addon_id: str) -> Addon | None:
    return next((a for a in ADDONS if a.id == addon_id), None)


# ── What's installed ─────────────────────────────────────────────────────────


def decky_version(home: Path | None = None) -> str | None:
    """Decky Loader's version if it's installed ("installed" if the version is unknown), else None."""
    services = (home or Path.home()) / "homebrew/services"
    if not (services / "PluginLoader").is_file():
        return None
    try:
        return (services / ".loader.version").read_text().strip() or "installed"
    except OSError:
        return "installed"


def emudeck_app(home: Path | None = None) -> Path | None:
    p = (home or Path.home()) / "Applications/EmuDeck.AppImage"
    return p if p.is_file() else None


def emudeck_set_up(home: Path | None = None) -> bool:
    """EmuDeck has been run and has configured the emulators (it keeps its settings in ~/emudeck)."""
    return ((home or Path.home()) / "emudeck").is_dir()


def status(a: Addon, home: Path | None = None, installed: set[str] | None = None) -> str:
    """What's on the Deck. `installed`: Flatpak apps (None while they're still being read)."""
    if a.app:
        return "…" if installed is None else "Installed" if a.app in installed else "Not installed"
    if a.id == "ge-proton":
        from . import proton

        have = proton.installed()
        return f"Installed ({have[0]})" if have else "Not installed"
    if a.id == "decky":
        v = decky_version(home)
        return f"Installed ({v})" if v and v != "installed" else ("Installed" if v else "Not installed")
    if emudeck_app(home) is None:
        return "Set up already" if emudeck_set_up(home) else "Not installed"
    return "Installed" if emudeck_set_up(home) else "Downloaded — not set up yet"


# ── Desktop Mode ─────────────────────────────────────────────────────────────


def can_switch_to_desktop() -> bool:
    return shutil.which("steamos-session-select") is not None


def switch_to_desktop() -> None:
    """What the Power menu's "Switch to Desktop" does."""
    subprocess.Popen(["steamos-session-select", "plasma"], env=core.clean_env(), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


# ── Decky Loader ─────────────────────────────────────────────────────────────


def fetch_decky_installer(dest_dir: Path, fetch: Callable[[str], bytes] | None = None) -> Path:
    """Decky's own installer script, checked to be what it should be."""
    raw = (fetch or (lambda url: updater.fetch(url, timeout=60, limit=4 << 20)))(DECKY_INSTALLER_URL)
    text = raw.decode("utf-8", errors="replace")
    if not text.startswith("#!") or "Decky" not in text:
        raise core.InstallError("Decky's installer didn't download correctly (is the Deck online?).")
    dest_dir.mkdir(parents=True, exist_ok=True)
    script = dest_dir / "decky_user_install_script.sh"
    script.write_text(text, encoding="utf-8")
    script.chmod(0o755)
    return script


def run_decky_installer(script: Path, log: Callable[[str], None] = lambda s: None) -> int:
    """Run it and wait. It shows its own windows: password, release/prerelease (or update/uninstall
    when Decky is already there), and progress."""
    proc = subprocess.Popen(["bash", str(script)], env=core.clean_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, errors="replace")
    assert proc.stdout is not None
    with proc.stdout:
        for line in proc.stdout:
            if line.strip():
                log(line.rstrip())
    return proc.wait()


# ── EmuDeck ──────────────────────────────────────────────────────────────────


def emudeck_release(fetch: Callable[[str], bytes] | None = None) -> tuple[str, str]:
    """(version, AppImage URL) of EmuDeck's latest release."""
    data = json.loads((fetch or (lambda url: updater.fetch(url, timeout=30)))(EMUDECK_RELEASE_API))
    url = next((a.get("browser_download_url") for a in data.get("assets", [])
                if str(a.get("name", "")).endswith(".AppImage")), None)
    if not url:
        raise core.InstallError("Couldn't find EmuDeck's download in its latest release. Try again later.")
    return str(data.get("tag_name", "")).lstrip("v"), url


def download_emudeck(progress: Callable[[int, int], None] = lambda done, total: None,
                     fetch: Callable[[str], bytes] | None = None, opener=None, home: Path | None = None) -> str:
    """Download the latest EmuDeck.AppImage into ~/Applications. Returns its version."""
    version, url = emudeck_release(fetch)

    def check(head: bytes) -> None:
        if not head.startswith(b"\x7fELF"):
            raise core.InstallError("The EmuDeck download was damaged. Try again.")

    dest = updater.download_to(url, (home or Path.home()) / "Applications/EmuDeck.AppImage", progress, opener,
                               check=check)
    dest.chmod(0o755)
    return version


def open_emudeck(home: Path | None = None) -> None:
    app = emudeck_app(home)
    if app is None:
        raise core.InstallError("EmuDeck isn't downloaded yet.")
    subprocess.Popen([str(app)], env=core.clean_env(), start_new_session=True, cwd=str(app.parent),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
