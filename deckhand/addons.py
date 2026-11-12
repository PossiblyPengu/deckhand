"""Deck add-ons from their official sources.

Nothing is repackaged. Decky's own installer (what decky.xyz links to) is downloaded and run as
is, with its questions (admin password, release or prerelease, progress) answered from Deckhand's own screens, so
it works in Game Mode too. Arcadia, Deckhand's
sister app for retro games, is its AppImage from its GitHub releases, put in ~/Applications and added to
Steam with a launcher of Deckhand's. GE-Proton
comes from its GitHub releases (proton.py). The rest are apps from Flathub, installed for this user
(no password) with the streaming code's Flatpak helpers; those meant for Game Mode are also added to
Steam, as Apps with kind "addon".
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
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
NEXUS_RELEASES_API = "https://api.github.com/repos/domsvoboda/nexus-launcher/releases?per_page=15"
# Its release files have been named both ways (the project is renaming from Xodus Nexus to Nexus Launcher), newest first.
NEXUS_FAMILIES = ("Nexus-Launcher-", "Xodus-Nexus-")
NEXUS_APPIMAGE = "Nexus-Launcher.AppImage"  # what the project's own installer calls it too


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
    return "Not installed"


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
    """Run Decky's installer as it is, with its own zenity windows (password, release/prerelease or
    update/uninstall, progress). Only for Desktop Mode, and as the fallback if run_decky() can't be used."""
    proc = subprocess.Popen(["bash", str(script)], env=core.clean_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, errors="replace")
    assert proc.stdout is not None
    with proc.stdout:
        for line in proc.stdout:
            if line.strip():
                log(line.rstrip())
    return proc.wait()


# Decky's installer talks to you through `zenity` windows: an optional "no password set" question, a password prompt,
# the branch (or update/uninstall) choice, and progress bars. Everything else it does itself (download, systemd).
# Deckhand runs that same official script, but points its one `zen_nospam()` helper at this stand-in, which answers
# from a small file Deckhand wrote and reports progress back on stderr. So Decky is installed by Decky's own
# installer, in Game Mode as well as on the desktop, and no zenity window is needed.
DECKY_TEMP_PASSWORD = "Decky!"  # what Decky's installer sets (and removes again) if no password exists
ZENITY_STAND_IN = r"""#!/bin/bash
# Deckhand's stand-in for zenity, used only by Decky's installer. $1: the answers file; the rest: zenity's arguments.
answers="$1"; shift
mode=""; text=""; prev=""
for a in "$@"; do
  case "$a" in
    --question|--entry|--info|--warning|--error|--list|--progress) mode="${a#--}" ;;
    --text=*) text="${a#--text=}" ;;
  esac
  [ "$prev" = "--text" ] && text="$a"
  prev="$a"
done
case "$mode" in
  question) [ "$(jq -r '.question' "$answers")" = true ] ;;
  entry) jq -r '.password' "$answers" ;;
  list)
    want="$(jq -r '.option' "$answers")"; found=""
    for a in "$@"; do [ "$a" = "$want" ] && found=1; done
    if [ -n "$found" ]; then printf '%s
' "$want"; else
      prev=""; for a in "$@"; do if [ "$prev" = TRUE ]; then printf '%s
' "$a"; break; fi; prev="$a"; done
    fi ;;
  progress)
    while IFS= read -r line; do
      case "$line" in
        "#"*) echo "DECKHAND-TEXT ${line#\#}" >&2 ;;
        [0-9]*) echo "DECKHAND-PROGRESS ${line%%[!0-9]*}" >&2 ;;
      esac
    done ;;
  info|warning|error) echo "DECKHAND-MESSAGE $text" >&2 ;;
esac
"""
_ZEN_FUNCTION = re.compile(r"zen_nospam\(\)\s*\{[ \t]*\n[ \t]*zenity[^\n]*\n[ \t]*\}")


def patch_decky_installer(text: str, stand_in: Path, answers: Path) -> str:
    """Decky's installer with its zenity helper swapped for the stand-in. Raises InstallError("DECKY_CHANGED") if the
    installer no longer looks the way this expects (then nothing is run that Deckhand can't vouch for)."""
    if not _ZEN_FUNCTION.search(text):
        raise core.InstallError("DECKY_CHANGED")
    call = f'zen_nospam() {{\n  {shlex.quote(str(stand_in))} {shlex.quote(str(answers))} "$@"\n}}'
    return _ZEN_FUNCTION.sub(lambda _m: call, text, count=1)


def admin_password_unset(user: str = "deck") -> bool:
    """True if the account has no password at all (what SteamOS ships with): `passwd -S` says NP."""
    try:
        out = subprocess.run(["passwd", "-S", user], capture_output=True, text=True, timeout=10,
                             env=core.clean_env()).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return len(out) > 1 and out[1] == "NP"


def sudo_accepts(password: str) -> bool:
    """Does sudo take this password? (Checked before the installer runs, so a typo is caught on the spot.)"""
    try:
        r = subprocess.run(["sudo", "-S", "-k", "true"], input=password + "\n", capture_output=True, text=True,
                           timeout=30, env=core.clean_env())
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def run_decky(script: Path, option: str, password: str, temporary_password: bool,
              on_event: Callable[[str, str], None] = lambda kind, value: None,
              log: Callable[[str], None] = lambda s: None) -> tuple[int, list[str]]:
    """Run Decky's own installer with its questions answered here. `option`: what its choice list should pick
    ("release", "prerelease", "update to latest release", "uninstall decky loader", …). `temporary_password`: the
    person agreed to the temporary password. `on_event("text" | "progress", value)` follows its progress. Returns
    (exit code, the error/warning messages it showed). The answers (with the password) live in a private folder
    for the run and are wiped afterwards."""
    work = Path(tempfile.mkdtemp(prefix="deckhand-decky-"))
    work.chmod(0o700)
    answers = work / "answers.json"
    try:
        answers.write_text(json.dumps({"password": password, "question": temporary_password, "option": option}),
                           encoding="utf-8")
        answers.chmod(0o600)
        stand_in = work / "zenity"
        stand_in.write_text(ZENITY_STAND_IN, encoding="utf-8")
        stand_in.chmod(0o700)
        patched = work / "install.sh"
        patched.write_text(patch_decky_installer(script.read_text(encoding="utf-8"), stand_in, answers),
                           encoding="utf-8")
        patched.chmod(0o700)
        proc = subprocess.Popen(["bash", str(patched)], env=core.clean_env(), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, errors="replace")
        messages: list[str] = []
        assert proc.stdout is not None
        with proc.stdout:
            for raw in proc.stdout:
                line = raw.rstrip()
                if line.startswith("DECKHAND-PROGRESS "):
                    on_event("progress", line.split(" ", 1)[1])
                elif line.startswith("DECKHAND-TEXT "):
                    on_event("text", line.split(" ", 1)[1].strip())
                elif line.startswith("DECKHAND-MESSAGE "):
                    messages.append(line.split(" ", 1)[1].strip())
                elif line.strip():
                    log(line)
        return proc.wait(), messages
    finally:
        try:  # wipe the password before the folder goes
            if answers.exists():
                answers.write_text("0" * 64)
        except OSError:
            pass
        shutil.rmtree(work, ignore_errors=True)


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


def nexus_asset(assets: list[dict], arch: str) -> dict | None:
    """The AppImage for `arch` among a release's files: the newest naming, then the older one, then (like the
    project's own installer) any Nexus/Xodus AppImage for this architecture."""
    ends = f"-{arch}.AppImage"
    named = [(str(a.get("name", "")), a) for a in assets]
    for family in NEXUS_FAMILIES:
        for name, a in named:
            if name.startswith(family) and name.endswith(ends):
                return a
    for name, a in named:
        if name.endswith(ends) and ("nexus" in name.lower() or "xodus" in name.lower()):
            return a
    return None


def nexus_release(fetch: Callable[[str], bytes] | None = None, arch: str | None = None
                  ) -> tuple[str, str, str]:
    """(version, AppImage URL, sha512 or "") of Nexus Launcher's newest release that has an AppImage for this machine.
    Each AppImage has a .sha512sum file beside it. The latest release is tried first; if it has no AppImage (the
    project also publishes other kinds of release), the recent ones are searched."""
    import platform

    get = fetch or (lambda url: updater.fetch(url, timeout=30, limit=8 << 20))
    arch = arch or {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower(), "x86_64")

    def usable(release) -> list[dict]:
        return [a for a in (release.get("assets") or []) if isinstance(a, dict) and a.get("browser_download_url")] \
            if isinstance(release, dict) else []

    data = json.loads(get(NEXUS_RELEASE_API))
    assets = usable(data)
    asset = nexus_asset(assets, arch)
    seen = [str(a.get("name")) for a in assets]
    if asset is None:
        try:
            older = json.loads(get(NEXUS_RELEASES_API))
        except (OSError, ValueError, updater.UpdateError):
            older = []
        for release in older if isinstance(older, list) else []:
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                continue
            candidate = nexus_asset(usable(release), arch)
            if candidate is not None:
                data, assets, asset = release, usable(release), candidate
                break
    if asset is None:
        raise core.InstallError("Couldn't find a Nexus Launcher download for this Deck in its latest releases. Try "
                                "again later, or download it from its GitHub releases page.\n\nThe latest release "
                                f"lists: {', '.join(seen[:8]) or 'no files'}.")
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
