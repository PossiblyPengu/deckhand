"""Deck add-ons from their official sources.

Nothing is repackaged. Decky's own installer (what decky.xyz links to) is downloaded and run as
is; it asks for the admin password in its own windows, so it needs Desktop Mode. Arcadia, Deckhand's
sister app for retro games, is its AppImage from its GitHub releases, put in ~/Applications and added to
Steam with a launcher of Deckhand's. EmuDeck is
downloaded the way its own install script (what emudeck.com runs) does it: the latest
EmuDeck.AppImage from its GitHub releases, into ~/Applications, and opened from there. GE-Proton
comes from its GitHub releases (proton.py). The rest are apps from Flathub, installed for this user
(no password) with the streaming code's Flatpak helpers; those meant for Game Mode are also added to
Steam, as Apps with kind "addon".
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import core, streaming, updater

DECKY_INSTALLER_URL = ("https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/"
                       "user_install_script.sh")
ARCADIA_RELEASE_API = "https://api.github.com/repos/PossiblyPengu/arcadia/releases/latest"
ARCADIA_APPIMAGE = "Arcadia_x64_SteamDeck.AppImage"
NEXUS_RELEASE_API = "https://api.github.com/repos/domsvoboda/nexus-launcher/releases/latest"
NEXUS_APPIMAGE = "Nexus-Launcher.AppImage"  # what the project's own installer calls it too
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
    Addon("arcadia", "Arcadia", "Deckhand's sister app: your retro games, with emulators, BIOS check and cloud saves",
          "GitHub", "#cdbdf0"),
    Addon("nexus", "Xbox PC games (Nexus Launcher)",
          "Your Xbox and Game Pass PC games, built on Xodus. New and experimental", "GitHub", "#107c10"),
    Addon("decky", "Decky Loader", "Plugins for the Quick Access menu", "decky.xyz", "#6b35c8"),
    Addon("emudeck", "EmuDeck", "Sets up emulators and adds your retro games to Steam (Arcadia does this in one app)", "emudeck.com", "#d6313f"),
    Addon("retrodeck", "RetroDECK", "Emulators and your retro games in one app (Arcadia is the lighter option)", "Flathub", "#8f5ae8",
          app="net.retrodeck.retrodeck", steam=True),
    Addon("ge-proton", "GE-Proton", "Proton with extra fixes: more games and installers work", "GitHub", "#c0392b"),
    Addon("protonup-qt", "ProtonUp-Qt", "Install and manage Proton versions for Steam and Heroic", "Flathub",
          "#5c6bc0", app="net.davidotek.pupgui2"),
    Addon("protontricks", "Protontricks", "Winetricks for your Steam games' Windows setups", "Flathub", "#8e2a2a",
          app="com.github.Matoking.protontricks"),
    Addon("ludusavi", "Ludusavi", "Back up and restore the saves of all your games (Arcadia syncs emulator saves to the cloud)", "Flathub", "#2e7d32",
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


def arcadia_apps(home: Path | None = None) -> list[Path]:
    """Every Arcadia AppImage in ~/Applications: the name its releases use first, then any other Arcadia*.AppImage."""
    apps = (home or Path.home()) / "Applications"
    try:
        found = sorted(p for p in apps.iterdir() if p.is_file() and p.name.lower().startswith("arcadia")
                       and p.name.lower().endswith(".appimage"))
    except OSError:
        return []
    return sorted(found, key=lambda p: p.name != ARCADIA_APPIMAGE)


def arcadia_app(home: Path | None = None) -> Path | None:
    """The Arcadia AppImage Deckhand opens and updates (see arcadia_apps)."""
    found = arcadia_apps(home)
    return found[0] if found else None


def arcadia_shortcut_of_yours(roots=None, home: Path | None = None) -> int:
    """The appid of a Steam shortcut you made yourself that runs an Arcadia AppImage directly (0 if none).
    Deckhand's own shortcut runs its launcher instead, so it never counts; and Deckhand never removes this one."""
    for appimage in arcadia_apps(home):
        appid = core.find_shortcut(str(appimage), roots)
        if appid:
            return appid
    return 0


def nexus_apps(home: Path | None = None) -> list[Path]:
    """Every Nexus Launcher AppImage we know of: the project's own installer's (~/.local/opt/xodus-nexus), then
    ~/Applications. (Releases before 0.5 were called Xodus-Nexus.)"""
    home = home or Path.home()
    found: list[Path] = []
    for folder in (home / ".local/opt/xodus-nexus", home / "Applications"):
        try:
            found += sorted((p for p in folder.iterdir() if p.is_file() and p.name.lower().endswith(".appimage")
                             and p.name.lower().startswith(("nexus-launcher", "xodus-nexus"))),
                            key=lambda p: p.name != NEXUS_APPIMAGE)
        except OSError:
            pass
    return found


def nexus_app(home: Path | None = None) -> Path | None:
    found = nexus_apps(home)
    return found[0] if found else None


def nexus_shortcut_of_yours(roots=None, home: Path | None = None) -> int:
    """The appid of a Steam shortcut (made by you, or by Nexus's own "Add to Steam") that runs a Nexus Launcher
    AppImage directly, 0 if none. Deckhand never removes it, and doesn't add a second one."""
    for appimage in nexus_apps(home):
        appid = core.find_shortcut(str(appimage), roots)
        if appid:
            return appid
    return 0


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
    if a.id == "nexus":
        return "Installed" if nexus_app(home) else "Not installed"
    if a.id == "ge-proton":
        from . import proton

        have = proton.installed()
        return f"Installed ({have[0]})" if have else "Not installed"
    if a.id == "arcadia":
        return "Installed" if arcadia_app(home) else "Not installed"
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


# ── Arcadia ──────────────────────────────────────────────────────────────────


def arcadia_release(fetch: Callable[[str], bytes] | None = None) -> tuple[str, str, str]:
    """(version, AppImage URL, sha256 or "") of Arcadia's latest release. GitHub lists each file's sha256."""
    data = json.loads((fetch or (lambda url: updater.fetch(url, timeout=30)))(ARCADIA_RELEASE_API))
    asset = next((a for a in data.get("assets", []) if isinstance(a, dict) and a.get("name") == ARCADIA_APPIMAGE),
                 None)
    if not asset or not asset.get("browser_download_url"):
        raise core.InstallError("Couldn't find Arcadia's Steam Deck download in its latest release. Try again later.")
    digest = str(asset.get("digest") or "")
    sha = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else ""
    return str(data.get("tag_name", "")).lstrip("v"), str(asset["browser_download_url"]), sha


def download_arcadia(progress: Callable[[int, int], None] = lambda done, total: None,
                     fetch: Callable[[str], bytes] | None = None, opener=None, home: Path | None = None) -> str:
    """Download Arcadia's latest AppImage into ~/Applications (over the one there, once it's whole and
    checked). Returns its version."""
    version, url, sha = arcadia_release(fetch)
    dest = arcadia_app(home) or (home or Path.home()) / "Applications" / ARCADIA_APPIMAGE

    def check(head: bytes) -> None:
        if not head.startswith(b"\x7fELF"):
            raise core.InstallError("The Arcadia download was damaged. Try again.")

    digest = hashlib.sha256()
    new = updater.download_to(url, dest.with_name(f".{dest.name}.new"), progress, opener, digest=digest, check=check)
    if sha and digest.hexdigest() != sha:
        new.unlink(missing_ok=True)
        raise core.InstallError("The Arcadia download didn't match its checksum. Try again.")
    new.chmod(0o755)
    os.replace(new, dest)
    return version


def arcadia_launcher(paths: core.Paths, appimage: Path) -> Path:
    paths.launchers.mkdir(parents=True, exist_ok=True)
    script = paths.launchers / "addon-arcadia.sh"
    log = paths.logs / "addon-arcadia-launch.log"
    q = shlex.quote
    script.write_text("\n".join(["#!/bin/bash", "# Deckhand: Arcadia",
                                 f"{{ mkdir -p {q(str(log.parent))} && exec >{q(str(log))} 2>&1; }} || true",
                                 f'exec {q(str(appimage))} "$@"']) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def set_up_arcadia(paths: core.Paths, roots=None, steam: bool = True) -> core.App:
    """Arcadia's launcher, menu entry and (if `steam`) Steam shortcut — once. An Arcadia you added to Steam
    yourself counts: no second shortcut."""
    appimage = arcadia_app()
    if appimage is None:
        raise core.InstallError("Arcadia isn't downloaded yet.")
    library = core.Library(paths)
    app = next((a for a in library.load() if a.id == "addon-arcadia"), None) or core.App(
        id="addon-arcadia", name="Arcadia", exe="", prefix="", runtime_name="", runtime_kind="", runtime_path="",
        kind="addon")
    app.exe = str(appimage)
    app.launcher = str(arcadia_launcher(paths, appimage))
    app.installed_at = time.time()
    core.use_logo(app, paths, "arcadia")
    core.write_desktop_entry(app)
    if steam and not arcadia_shortcut_of_yours(roots) and core.steam_state(app, roots) != "sent":
        core.add_to_steam(app, roots)
    library.upsert(app)
    return app


def uninstall_arcadia(app: core.App | None, paths: core.Paths, roots=None) -> bool:
    """Arcadia's AppImage, launcher and shortcut go; its settings (~/Arcadia) and your games stay.
    True if the shortcut was left for you to remove in Steam."""
    left = core.uninstall(app, paths, roots=roots) if app is not None else False
    for appimage in arcadia_apps():
        for f in (appimage, appimage.with_name(appimage.name + ".previous")):
            f.unlink(missing_ok=True)
    return left


def open_arcadia(home: Path | None = None) -> None:
    app = arcadia_app(home)
    if app is None:
        raise core.InstallError("Arcadia isn't downloaded yet.")
    subprocess.Popen([str(app)], env=core.clean_env(), start_new_session=True, cwd=str(app.parent),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


# ── Nexus Launcher (Xbox PC games, built on Xodus) ───────────────────────────


def parse_sha512sum(text: str, name: str) -> str:
    """The hex digest `name` has in a `sha512sum` file ('<hex>  <name>'); a lone digest line counts too."""
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if not parts or not all(c in "0123456789abcdefABCDEF" for c in parts[0]) or len(parts[0]) != 128:
            continue
        if len(parts) == 1 or parts[1].lstrip("*").strip() == name:
            return parts[0].lower()
    return ""


def nexus_release(fetch: Callable[[str], bytes] | None = None, arch: str | None = None
                  ) -> tuple[str, str, str]:
    """(version, AppImage URL, sha512 or "") of Nexus Launcher's latest release for this machine.
    Assets are named Nexus-Launcher-<version>-<arch>.AppImage, each with a .sha512sum file beside it."""
    import platform

    get = fetch or (lambda url: updater.fetch(url, timeout=30))
    arch = arch or {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower(), "x86_64")
    data = json.loads(get(NEXUS_RELEASE_API))
    assets = [a for a in data.get("assets", []) if isinstance(a, dict) and a.get("browser_download_url")]
    asset = next((a for a in assets if str(a.get("name", "")).startswith("Nexus-Launcher-")
                  and str(a["name"]).endswith(f"-{arch}.AppImage")), None)
    if asset is None:
        raise core.InstallError("Couldn't find Nexus Launcher's download in its latest release. Try again later.")
    sha = ""
    side = next((a for a in assets if a.get("name") == asset["name"] + ".sha512sum"), None)
    if side is not None:
        try:
            sha = parse_sha512sum(get(str(side["browser_download_url"])).decode("utf-8", "replace"), asset["name"])
        except OSError:
            sha = ""  # (the download is still checked to be an AppImage)
    return str(data.get("tag_name", "")).lstrip("v"), str(asset["browser_download_url"]), sha


def download_nexus(progress: Callable[[int, int], None] = lambda done, total: None,
                   fetch: Callable[[str], bytes] | None = None, opener=None, home: Path | None = None) -> str:
    """Download the newest Nexus Launcher AppImage (over the one there, once it's whole and checked). Returns
    its version. A copy from the project's own installer is updated where it is."""
    version, url, sha = nexus_release(fetch)
    dest = nexus_app(home) or (home or Path.home()) / "Applications" / NEXUS_APPIMAGE

    def check(head: bytes) -> None:
        if not head.startswith(b"\x7fELF"):
            raise core.InstallError("The Nexus Launcher download was damaged. Try again.")

    digest = hashlib.sha512()
    new = updater.download_to(url, dest.with_name(f".{dest.name}.new"), progress, opener, digest=digest, check=check)
    if sha and digest.hexdigest() != sha:
        new.unlink(missing_ok=True)
        raise core.InstallError("The Nexus Launcher download didn't match its checksum. Try again.")
    new.chmod(0o755)
    os.replace(new, dest)
    return version


def nexus_launcher(paths: core.Paths, appimage: Path) -> Path:
    paths.launchers.mkdir(parents=True, exist_ok=True)
    script = paths.launchers / "addon-nexus.sh"
    log = paths.logs / "addon-nexus-launch.log"
    q = shlex.quote
    script.write_text("\n".join(["#!/bin/bash", "# Deckhand: Nexus Launcher",
                                 f"{{ mkdir -p {q(str(log.parent))} && exec >{q(str(log))} 2>&1; }} || true",
                                 f'exec {q(str(appimage))} "$@"']) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def set_up_nexus(paths: core.Paths, roots=None, steam: bool = True) -> core.App:
    """Nexus Launcher's launcher, menu entry and (if `steam`) Steam shortcut, once: one you or Nexus made counts."""
    appimage = nexus_app()
    if appimage is None:
        raise core.InstallError("Nexus Launcher isn't downloaded yet.")
    library = core.Library(paths)
    app = next((a for a in library.load() if a.id == "addon-nexus"), None) or core.App(
        id="addon-nexus", name="Nexus Launcher", exe="", prefix="", runtime_name="", runtime_kind="",
        runtime_path="", kind="addon")
    app.exe = str(appimage)
    app.launcher = str(nexus_launcher(paths, appimage))
    app.installed_at = time.time()
    core.use_logo(app, paths, "nexus")
    core.write_desktop_entry(app)
    if steam and not nexus_shortcut_of_yours(roots) and core.steam_state(app, roots) != "sent":
        core.add_to_steam(app, roots)
    library.upsert(app)
    return app


def uninstall_nexus(app: core.App | None, paths: core.Paths, roots=None) -> bool:
    """Deckhand's shortcut, launcher and menu entry go, and the AppImage in ~/Applications. A copy from the
    project's own installer is left to its own uninstall script. Games, saves and the runtime stay. True if the
    shortcut was left for you to remove in Steam."""
    left = core.uninstall(app, paths, roots=roots) if app is not None else False
    apps = Path.home() / "Applications"
    for appimage in nexus_apps():
        if appimage.parent == apps:
            for f in (appimage, appimage.with_name(appimage.name + ".previous")):
                f.unlink(missing_ok=True)
    return left


def open_nexus(home: Path | None = None) -> None:
    app = nexus_app(home)
    if app is None:
        raise core.InstallError("Nexus Launcher isn't downloaded yet.")
    subprocess.Popen([str(app)], env=core.clean_env(), start_new_session=True, cwd=str(app.parent),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


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
