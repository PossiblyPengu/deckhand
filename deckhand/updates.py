"""Updates for Deckhand and the apps it put on the Deck, and telling you about them, in Game Mode too.

`check()` looks at Deckhand, Arcadia, Nexus Launcher, Decky Loader and GE-Proton, each against its latest release:
the AppImages by the checksum their release publishes (a file that differs from the latest one is out of date; no
version number has to be read from it), Decky and GE-Proton by version. An add-on that isn't installed, or a source
that can't be reached, is skipped.

`notify()` tells you about the ones you haven't been told about yet, with an ordinary desktop notification. In
Desktop Mode KDE shows it; in Game Mode SteamOS's steam_notif_daemon takes the same notification and Steam shows it as
a toast. So a check that runs in the background (a systemd user timer, `deckhand --check-updates`; see `install_timer`)
reaches you without Deckhand being open.

What was found is kept (state "updates_available") so Deckhand shows it as soon as it opens.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import __version__, addons, core, proton, updater

DECKY_RELEASE_API = "https://api.github.com/repos/SteamDeckHomebrew/decky-loader/releases/latest"
TIMER = "deckhand-updates"
EVERY_HOURS = 6


@dataclass(frozen=True)
class Found:
    id: str  # "deckhand", or an add-on's id
    name: str
    latest: str  # the version that is out
    current: str = ""  # the one on the Deck, when it's known

    @property
    def key(self) -> str:
        return f"{self.id}:{self.latest}"

    @property
    def text(self) -> str:
        return f"{self.name} {self.latest}" if self.latest else self.name


# ── Checking ─────────────────────────────────────────────────────────────────


def _hash(path: Path, algorithm: str, paths: core.Paths | None) -> str:
    """A file's checksum, remembered while the file is the same one, unchanged (an AppImage is large; it rarely
    changes, and an update replaces the file)."""
    st = path.stat()
    stamp = f"{path}|{st.st_ino}|{st.st_size}|{st.st_mtime_ns}|{algorithm}"
    cache = (paths.state().get("file_hashes") or {}) if paths else {}
    if cache.get(stamp):
        return str(cache[stamp])
    h = hashlib.new(algorithm)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    digest = h.hexdigest()
    if paths:
        kept = {k: v for k, v in cache.items() if not k.startswith(f"{path}|")}
        paths.remember(file_hashes={**kept, stamp: digest})
    return digest


def _strip_v(v: str) -> str:
    return v[1:] if v[:1] in "vV" and v[1:2].isdigit() else v


def check_deckhand(sources=None) -> Found | None:
    if updater.self_path() is None and sources is None:  # (run from source: it doesn't update itself)
        return None
    u = updater.check(__version__, sources)
    return Found("deckhand", "Deckhand", u.version, __version__) if u else None


def check_arcadia(home: Path | None = None, fetch=None, paths: core.Paths | None = None) -> Found | None:
    app = addons.arcadia_app(home)
    if app is None:
        return None
    version, _url, sha = addons.arcadia_release(fetch)
    if sha and _hash(app, "sha256", paths) != sha:
        return Found("arcadia", "Arcadia", version)
    return None


def check_nexus(home: Path | None = None, fetch=None, paths: core.Paths | None = None) -> Found | None:
    app = addons.nexus_app(home)
    if app is None:
        return None
    version, _url, sha = addons.nexus_release(fetch)
    if sha and _hash(app, "sha512", paths) != sha.lower():
        return Found("nexus", "Nexus Launcher", version)
    return None


def check_decky(home: Path | None = None, fetch=None) -> Found | None:
    have = addons.decky_version(home)
    if not have or have == "installed":
        return None
    data = json.loads((fetch or (lambda url: updater.fetch(url, timeout=30)))(DECKY_RELEASE_API))
    latest = _strip_v(str(data.get("tag_name", "")))
    if latest and updater.is_newer(latest, _strip_v(have)):
        return Found("decky", "Decky Loader", latest, _strip_v(have))
    return None


def check_ge_proton(roots=None, fetch=None) -> Found | None:
    have = proton.installed(roots)
    if not have:
        return None
    latest = proton.latest(fetch).version
    if latest and not proton.has(latest, have) and updater.is_newer(proton.base_name(latest),
                                                                     proton.base_name(have[0])):
        return Found("ge-proton", "GE-Proton", proton.base_name(latest), have[0])
    return None


def check(paths: core.Paths | None = None, home: Path | None = None, fetch=None, roots=None,
          deckhand_sources=None, errors: list[Exception] | None = None) -> list[Found]:
    """Everything with an update, Deckhand first. Something that can't be checked (offline, rate-limited) is
    skipped, and its error collected in `errors`."""
    checks: list[Callable[[], Found | None]] = [
        lambda: check_deckhand(deckhand_sources),
        lambda: check_arcadia(home, fetch, paths),
        lambda: check_nexus(home, fetch, paths),
        lambda: check_decky(home, fetch),
        lambda: check_ge_proton(roots, fetch),
    ]
    found = []
    for c in checks:
        try:
            f = c()
        except Exception as e:  # noqa: BLE001 — one source failing doesn't stop the others
            if errors is not None:
                errors.append(e)
            continue
        if f:
            found.append(f)
    return found


# ── Remembering and telling ──────────────────────────────────────────────────


def remember(paths: core.Paths, found: list[Found]) -> None:
    paths.remember(updates_available=[asdict(f) for f in found], updates_checked_at=time.time())


def available(paths: core.Paths) -> list[Found]:
    """What the last check found (shown when Deckhand opens, before it checks again)."""
    out = []
    for item in paths.state().get("updates_available") or []:
        try:
            out.append(Found(**item))
        except TypeError:
            continue
    return out


def forget(paths: core.Paths, app_id: str) -> None:
    """After an update: it's no longer waiting."""
    paths.remember(updates_available=[asdict(f) for f in available(paths) if f.id != app_id])


def message(found: list[Found]) -> tuple[str, str]:
    """(title, body) of the notification."""
    if len(found) == 1:
        f = found[0]
        return f"{f.text} is out", f"Open Deckhand to update {f.name}." if f.id == "deckhand" else \
            f"Open Deckhand and choose Add-ons → {f.name} to update it."
    names = ", ".join(f.text for f in found[:-1]) + f" and {found[-1].text}"
    return f"{len(found)} updates for your Deck", f"{names}. Open Deckhand to update them."


def send(title: str, body: str, icon: str = "") -> bool:
    """A desktop notification: KDE shows it in Desktop Mode, Steam (through steam_notif_daemon) in Game Mode."""
    icon = icon or "deckhand"
    tool = shutil.which("notify-send")
    if tool:
        cmd = [tool, "--app-name=Deckhand", f"--icon={icon}", title, body]
    elif shutil.which("gdbus"):
        cmd = ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
               "--object-path", "/org/freedesktop/Notifications", "--method", "org.freedesktop.Notifications.Notify",
               "Deckhand", "0", icon, title, body, "[]", "{}", "-1"]
    else:
        return False
    try:
        return subprocess.run(cmd, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def notify(paths: core.Paths, found: list[Found], sender: Callable[[str, str], bool] | None = None) -> list[Found]:
    """Tell you about the updates you haven't been told about (each version once). Returns those it told you about."""
    told = set(paths.state().get("updates_notified") or [])
    new = [f for f in found if f.key not in told]
    if not new:
        return []
    title, body = message(new)
    if not (sender or send)(title, body):
        return []  # (no notification service just now: try again next time)
    paths.remember(updates_notified=sorted(told | {f.key for f in new})[-50:])
    return new


def enabled(paths: core.Paths) -> bool:
    return paths.state().get("update_notifications", True) is not False


def run_check(paths: core.Paths | None = None, out=print, sender=None, **kw) -> int:
    """`deckhand --check-updates`: check, remember, and notify (if notifications are on)."""
    paths = paths or core.Paths.default()
    errors: list[Exception] = []
    found = check(paths, errors=errors, **kw)
    remember(paths, found)
    for f in found:
        out(f"Update: {f.text}" + (f" (you have {f.current})" if f.current else ""))
    if not found:
        out("Everything is up to date." if not errors else f"Up to date as far as could be checked ({len(errors)} "
            "couldn't be reached).")
    if enabled(paths):
        notify(paths, found, sender)
    return 0


# ── The background check ─────────────────────────────────────────────────────


def unit_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config/systemd/user"


def unit_files(exe: Path) -> dict[str, str]:
    q = str(exe).replace("\\", "\\\\").replace('"', '\\"')
    return {
        f"{TIMER}.service": (
            "[Unit]\nDescription=Deckhand: check for updates to Deckhand and its add-ons\n"
            "After=network-online.target\n\n"
            f'[Service]\nType=oneshot\nExecStart="{q}" --check-updates\nNice=10\n'
            "Environment=QT_QPA_PLATFORM=offscreen\n"),
        f"{TIMER}.timer": (
            "[Unit]\nDescription=Deckhand: check for updates every few hours\n\n"
            f"[Timer]\nOnStartupSec=5min\nOnUnitActiveSec={EVERY_HOURS}h\nRandomizedDelaySec=10min\n\n"
            "[Install]\nWantedBy=timers.target\n"),
    }


def _systemctl(*args: str) -> bool:
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_timer(exe: Path, home: Path | None = None, systemctl=_systemctl) -> bool:
    """Write and start the timer (again, when Deckhand has moved or the units changed). True if it's running."""
    folder = unit_dir(home)
    wanted = unit_files(exe)
    changed = False
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in wanted.items():
            p = folder / name
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                p.write_text(text, encoding="utf-8")
                changed = True
    except OSError:
        return False
    if changed:
        systemctl("daemon-reload")
    return systemctl("enable", "--now", f"{TIMER}.timer")


def remove_timer(home: Path | None = None, systemctl=_systemctl) -> None:
    systemctl("disable", "--now", f"{TIMER}.timer")
    for name in unit_files(Path("x")):
        try:
            (unit_dir(home) / name).unlink()
        except OSError:
            pass
    systemctl("daemon-reload")


def keep_timer(paths: core.Paths, exe: Path | None, home: Path | None = None, systemctl=_systemctl) -> bool:
    """On every start of the installed Deckhand: the timer runs when notifications are on, and is gone when off."""
    if os.environ.get("DECKHAND_NO_UPDATE_TIMER"):
        return False
    if not enabled(paths):
        if (unit_dir(home) / f"{TIMER}.timer").exists():
            remove_timer(home, systemctl)
        return False
    if exe is None:
        return False
    return install_timer(exe, home, systemctl)
