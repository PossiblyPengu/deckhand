"""Game streaming services as Steam shortcuts.

Cloud services (Xbox Cloud Gaming, GeForce NOW, …) run full screen in a browser in Game Mode or in a closable
app window in Desktop Mode; home streaming (Moonlight, chiaki-ng) uses its own app. Either way the app comes
from Flathub, installed the way Discover does it (so Discover keeps it updated), and the service ends up in Steam like an
App with kind "stream", a launcher script, artwork, and Steam's own add-a-game hand-off.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import core

FLATHUB = "https://dl.flathub.org/repo/flathub.flatpakrepo"
CHROME, EDGE, CHROMIUM = "com.google.Chrome", "com.microsoft.Edge", "org.chromium.Chromium"
BROWSERS = (CHROME, EDGE, CHROMIUM)  # any one of them will do; Chrome is installed if none is
APP_NAMES = {CHROME: "Google Chrome", EDGE: "Microsoft Edge", CHROMIUM: "Chromium"}

# Better xCloud (github.com/redphx/better-xcloud): an optional userscript for Xbox Cloud Gaming
# (better picture, stream stats, remote play, mouse & keyboard…). It needs nothing from a
# userscript manager (@grant none), so Deckhand wraps it in a tiny browser extension and
# loads it with --load-extension. Google Chrome no longer accepts that switch; Chromium does.
BETTER_XCLOUD = "better-xcloud"
BETTER_XCLOUD_URL = "https://github.com/redphx/better-xcloud/releases/latest/download/better-xcloud.user.js"


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    blurb: str
    url: str = ""  # cloud services: the page to open full screen
    app: str = ""  # home streaming: the Flathub app that does it
    args: tuple[str, ...] = ()
    native: str = ""  # a Flatpak app that does it natively, used instead of the browser if installed
    local: tuple[str, ...] = ()  # names of a non-Flatpak copy: commands on PATH, or AppImage file names
    spot: str = ""  # regex that finds this service in a Steam shortcut someone else made
    color: str = "#555a66"  # badge colour

    @property
    def is_web(self) -> bool:
        return bool(self.url)


SERVICES = (
    Service("xbox-cloud", "Xbox Cloud Gaming", "Game Pass Ultimate games, streamed", url="https://www.xbox.com/play",
            spot=r"xbox\.com/(?:[a-z]{2}-[a-z]{2}/)?play|xbox cloud|xcloud", color="#107c10"),
    Service("geforce-now", "GeForce NOW", "Your Steam, Epic and other PC games, streamed",
            url="https://play.geforcenow.com", native="com.nvidia.geforcenow", spot=r"geforce ?now|geforcenow",
            color="#5f9400"),
    Service("amazon-luna", "Amazon Luna", "Luna+ and Prime Gaming, streamed", url="https://luna.amazon.com",
            spot=r"luna\.amazon|amazon luna", color="#6b3fd6"),
    Service("boosteroid", "Boosteroid", "Your PC games, streamed", url="https://cloud.boosteroid.com",
            spot=r"boosteroid", color="#e0561b"),
    Service("moonlight", "Moonlight", "Stream from your own gaming PC", app="com.moonlight_stream.Moonlight",
            local=("moonlight-qt", "moonlight", "Moonlight*.AppImage"), spot=r"moonlight", color="#3f63d8"),
    Service("chiaki-ng", "chiaki-ng", "Remote Play from your PlayStation", app="io.github.streetpea.Chiaki4deck",
            local=("chiaki-ng", "chiaki", "chiaki*.AppImage"), spot=r"chiaki", color="#1d4fa3"),
)

# Full screen on the Deck's 1280×800 screen, the way Valve's and Microsoft's own Deck guides do it:
# Chrome's window size is in device-independent pixels, so 1024×640 at 1.25 scale fills the screen
# and is sized for 7". Kiosk mode has no address bar; leave with Steam's own "Exit game".
BROWSER_ARGS = ("--kiosk", "--window-size=1024,640", "--force-device-scale-factor=1.25",
                "--device-scale-factor=1.25", "--no-first-run", "--no-default-browser-check")
# Desktop Mode has no "Exit game": open the page as a Chrome app window instead — no address bar, but a normal
# maximized window with a title bar and close button.
DESKTOP_BROWSER_ARGS = ("--start-maximized", "--force-device-scale-factor=1.25", "--device-scale-factor=1.25",
                        "--no-first-run", "--no-default-browser-check")
# core.in_game_mode(), for bash.
GAME_MODE_TEST = '[ "$SteamGamepadUI" = 1 ] || [ "${XDG_CURRENT_DESKTOP,,}" = gamescope ] || [ -n "$GAMESCOPE_WAYLAND_DISPLAY" ]'


def service(service_id: str) -> Service | None:
    return next((s for s in SERVICES if s.id == service_id), None)


# ── Flatpak ──────────────────────────────────────────────────────────────────


def _flatpak() -> str | None:
    import shutil

    return shutil.which("flatpak")


def installed_apps() -> set[str]:
    """Flatpak app ids installed for this user or system-wide."""
    exe = _flatpak()
    if not exe:
        return set()
    try:
        out = subprocess.run([exe, "list", "--app", "--columns=application"], capture_output=True, text=True,
                             timeout=30, env=core.clean_env()).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


APPIMAGE_DIRS = ("Applications", "AppImages", ".local/bin", "Desktop", "Downloads")


def local_copy(svc: Service, home: Path | None = None) -> str | None:
    """A copy of the service's app installed without Flatpak (a command, or an AppImage)."""
    import shutil

    home = home or Path.home()
    for name in svc.local:
        if "*" in name:
            for d in APPIMAGE_DIRS:
                try:
                    hits = sorted((p for p in (home / d).glob(name) if p.is_file() and os.access(p, os.X_OK)),
                                  key=lambda p: p.name.lower(), reverse=True)
                except OSError:
                    hits = []
                if hits:
                    return str(hits[0])
        elif (found := shutil.which(name)) is not None:
            return found
    return None


def detect(installed: set[str], services: Iterable[Service] = SERVICES) -> set[str]:
    """Everything the services could run in that's already here: Flatpak ids, plus "local:<service id>"."""
    return set(installed) | {f"local:{s.id}" for s in services if s.local and local_copy(s)}


def spots(svc: Service, entries: list[tuple[Path, dict]], launchers: Path) -> list[dict]:
    """Steam shortcuts for this service that were made outside this app (by hand, a guide, another tool)."""
    if not svc.spot:
        return []
    out = []
    for _cfg, e in entries:
        exe = str(e.get("Exe", ""))
        if str(launchers) in exe:
            continue  # one of ours
        text = " ".join(str(e.get(k, "")) for k in ("AppName", "appname", "Exe", "LaunchOptions")).lower()
        if re.search(svc.spot, text) and e not in out:
            out.append(e)
    return out


def browser(installed: set[str], better_xcloud: bool = False) -> str | None:
    if better_xcloud:
        return CHROMIUM if CHROMIUM in installed else None
    return next((b for b in BROWSERS if b in installed), None)


def needs(svc: Service, installed: set[str], better_xcloud: bool = False) -> str | None:
    """The Flathub app that must be installed first, or None."""
    if uses(svc, installed, better_xcloud) in installed:
        return None
    return uses(svc, installed, better_xcloud)


def uses(svc: Service, installed: set[str], better_xcloud: bool = False) -> str:
    """What the service runs in: a Flatpak id, or "local:<service id>" for a copy installed another way."""
    if svc.native and svc.native in installed and not better_xcloud:
        return svc.native
    if svc.is_web:
        return browser(installed, better_xcloud) or (CHROMIUM if better_xcloud else CHROME)
    if svc.app not in installed and f"local:{svc.id}" in installed:
        return f"local:{svc.id}"
    return svc.app


def app_name(app_id: str) -> str:
    if app_id.startswith("local:"):
        svc = service(app_id[6:])
        return f"{svc.name if svc else app_id[6:]} (not from Flathub)"
    if app_id == "com.nvidia.geforcenow":
        return "GeForce NOW app"
    return APP_NAMES.get(app_id) or next((s.name for s in SERVICES if s.app == app_id), app_id)


def better_xcloud_dir() -> Path:
    """Inside Chromium's own data folder, which its sandbox can always read."""
    return Path.home() / ".var/app" / CHROMIUM / "data/deckhand-better-xcloud"


def install_better_xcloud(dest: Path | None = None, fetch: Callable[[str], bytes] | None = None) -> str:
    """Download Better xCloud and wrap it as an unpacked extension. Returns its version."""
    import json

    from . import updater

    dest = dest or better_xcloud_dir()
    raw = (fetch or (lambda url: updater.fetch(url, timeout=30, limit=20 << 20)))(BETTER_XCLOUD_URL)
    text = raw.decode("utf-8", errors="replace")
    head = text.split("==/UserScript==", 1)[0]
    if "==UserScript==" not in head or "Better xCloud" not in head:
        raise core.InstallError("The Better xCloud download didn't look right, so it wasn't installed.")
    m = re.search(r"@version\s+([\d.]+)", head)
    version = ".".join((m.group(1) if m else "1").split(".")[:4])
    matches = re.findall(r"@match\s+(\S+)", head) or ["https://www.xbox.com/*/play*"]
    excludes = re.findall(r"@exclude\s+(\S+)", head)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "better-xcloud.user.js").write_text(text, encoding="utf-8")
    manifest = {
        "manifest_version": 3, "name": "Better xCloud (added by Deckhand)", "version": version,
        "description": "github.com/redphx/better-xcloud",
        "content_scripts": [{"matches": matches, "exclude_matches": excludes, "js": ["better-xcloud.user.js"],
                             "run_at": "document_start", "world": "MAIN"}],
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return version


class FlatpakProgress:
    """Overall percent from flatpak's output: "Installing 2/3… ███ 45%" means 1 of 3 done plus 45% of
    the second. flatpak redraws its progress line with \\r, so each redraw is fed separately."""

    def __init__(self) -> None:
        self.step, self.steps = 1, 1

    def feed(self, text: str) -> int | None:
        m = re.search(r"(\d+)/(\d+)", text)
        if m and 0 < int(m.group(1)) <= int(m.group(2)):
            self.step, self.steps = int(m.group(1)), int(m.group(2))
        pct = re.findall(r"(\d{1,3})\s*%", text)
        if not pct:
            return None
        part = min(100, int(pct[-1]))
        return min(100, int(((self.step - 1) + part / 100) * 100 / self.steps))


def _run_flatpak(cmd: list[str], log: Callable[[str], None], progress: Callable[[int], None]) -> tuple[int, list[str]]:
    proc = subprocess.Popen(cmd, env=core.clean_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, errors="replace")
    tail: list[str] = []
    meter = FlatpakProgress()
    assert proc.stdout is not None
    with proc.stdout:
        for raw in proc.stdout:
            for line in raw.split("\r"):
                line = line.strip()
                if not line:
                    continue
                pct = meter.feed(line)
                if pct is not None:
                    progress(pct)
                else:
                    tail = (tail + [line])[-8:]
                    log(line)
    return proc.wait(), tail


def system_flathub() -> bool:
    """Is Flathub set up system-wide (as on SteamOS, where Discover installs from it)?"""
    exe = _flatpak()
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "remotes", "--system", "--columns=name"], capture_output=True, text=True,
                             timeout=30, env=core.clean_env()).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "flathub" in out.split()


def install_app(app_id: str, log: Callable[[str], None] = lambda s: None,
                progress: Callable[[int], None] = lambda pct: None) -> str:
    """Install a Flathub app the way Discover does: system-wide, from the system's Flathub, so it shows
    up in Discover and Discover keeps it updated. (SteamOS lets the deck user do that without a
    password: Flatpak's own rule for the wheel group.) Where that isn't allowed, it's installed for
    this user instead, which Discover also lists and updates. Returns "system" or "user"."""
    exe = _flatpak()
    if not exe:
        raise core.InstallError("Flatpak isn't available on this system, so Deckhand can't install "
                                f"{app_id}.")
    tail: list[str] = []
    if system_flathub():
        rc, tail = _run_flatpak([exe, "install", "--system", "-y", "--noninteractive", "flathub", app_id], log,
                                progress)
        if rc == 0:
            return "system"
        log("Installing for the whole Deck wasn't allowed; installing for this user instead.")
    subprocess.run([exe, "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB], env=core.clean_env(),
                   capture_output=True, timeout=120)
    rc, user_tail = _run_flatpak([exe, "install", "--user", "-y", "--noninteractive", "flathub", app_id], log,
                                 progress)
    if rc == 0:
        return "user"
    raise core.InstallError(f"Couldn't install {app_id} from Flathub (is the Deck online?).\n\n"
                            + "\n".join(user_tail or tail))


def uninstall_app(app_id: str) -> None:
    """Uninstall a Flatpak app from wherever install_app put it: system-wide (like Discover), or for
    this user."""
    exe = _flatpak()
    if not exe:
        raise core.InstallError("Flatpak isn't available on this system.")
    env, errors, found = core.clean_env(), [], False
    for scope in ("--system", "--user"):
        info = subprocess.run([exe, "info", scope, app_id], env=env, capture_output=True, timeout=60)
        if info.returncode != 0:
            continue  # not installed there
        found = True
        proc = subprocess.run([exe, "uninstall", scope, "-y", "--noninteractive", app_id], env=env,
                              capture_output=True, text=True, errors="replace", timeout=300)
        if proc.returncode != 0:
            errors.append((proc.stdout + proc.stderr).strip()[-800:])
    if errors or not found:
        raise core.InstallError(f"Couldn't uninstall {app_name(app_id)}. Remove it in Discover (Desktop Mode) "
                                "instead.\n\n" + "\n".join(errors))


def open_app(app_id: str) -> None:
    """Start a Flatpak app, independent of Deckhand."""
    subprocess.Popen(["flatpak", "run", app_id], env=core.clean_env(), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def allow_controllers(app_id: str) -> None:
    """Let a browser see game controllers (the Gamepad API needs udev's device info)."""
    exe = _flatpak()
    if exe:
        subprocess.run([exe, "override", "--user", "--filesystem=/run/udev:ro", app_id], env=core.clean_env(),
                       capture_output=True, timeout=60)


# ── Launchers and the Steam side ─────────────────────────────────────────────


def command(svc: Service, installed: set[str], better_xcloud: bool = False, desktop: bool = False) -> list[str]:
    runner = uses(svc, installed, better_xcloud)
    if runner.startswith("local:"):
        return [local_copy(svc) or svc.local[0], *svc.args]
    if runner == svc.native:
        return ["flatpak", "run", svc.native]
    if svc.is_web:
        b = uses(svc, installed, better_xcloud)
        ext = [f"--load-extension={better_xcloud_dir()}"] if better_xcloud else []
        if desktop:
            return ["flatpak", "run", b, *DESKTOP_BROWSER_ARGS, *ext, *svc.args, f"--app={svc.url}"]
        return ["flatpak", "run", b, *BROWSER_ARGS, *ext, *svc.args, svc.url]
    return ["flatpak", "run", svc.app, *svc.args]


def write_launcher(svc: Service, paths: core.Paths, installed: set[str], better_xcloud: bool = False,
                   kind: str = "stream") -> Path:
    paths.launchers.mkdir(parents=True, exist_ok=True)
    script = paths.launchers / f"{kind}-{svc.id}.sh"
    log = paths.logs / f"{kind}-{svc.id}-launch.log"
    q = shlex.quote
    lines = ["#!/bin/bash", f"# Deckhand: {svc.name}",
             f"{{ mkdir -p {q(str(log.parent))} && exec >{q(str(log))} 2>&1; }} || true"]
    if better_xcloud:
        d = better_xcloud_dir()
        # Keep Better xCloud current: fetch the newest in the background, used from the next launch.
        lines.append(f"( curl -fsL --max-time 60 {q(BETTER_XCLOUD_URL)} -o {q(str(d / '.new.js'))} "
                     f"&& grep -q '==UserScript==' {q(str(d / '.new.js'))} "
                     f"&& mv {q(str(d / '.new.js'))} {q(str(d / 'better-xcloud.user.js'))} ) >/dev/null 2>&1 &")
    if svc.is_web:
        game_mode = " ".join(q(c) for c in command(svc, installed, better_xcloud))
        desktop = " ".join(q(c) for c in command(svc, installed, better_xcloud, desktop=True))
        lines += [f"if {GAME_MODE_TEST}; then", f"  exec {game_mode}", "fi",
                  "# Desktop Mode's only keyboard is Steam's (STEAM + X), which needs Steam running.",
                  "if ! pgrep -x steam >/dev/null 2>&1 && command -v steam >/dev/null 2>&1; then",
                  "  (steam -silent >/dev/null 2>&1 &)", "fi", f"exec {desktop}"]
    else:
        lines.append(f"exec {' '.join(q(c) for c in command(svc, installed, better_xcloud))}")
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def app_for(svc: Service, paths: core.Paths, kind: str = "stream") -> core.App | None:
    return next((a for a in core.Library(paths).load() if a.kind == kind and a.id == f"{kind}-{svc.id}"), None)


def set_up(svc: Service, paths: core.Paths, status: Callable[[str], None] = lambda s: None,
           roots=None, better_xcloud: bool | None = None, fetch: Callable[[str], bytes] | None = None,
           kind: str = "stream") -> core.App:
    """Install what the service needs, write its launcher and add it to Steam (only once).
    `better_xcloud` (Xbox Cloud Gaming only): turn it on/off; None keeps the current choice.
    `kind` is the App's kind: "stream", or "store" for a game store's Linux app (stores.py)."""
    existing = app_for(svc, paths, kind)
    if better_xcloud is None:
        better_xcloud = bool(existing and BETTER_XCLOUD in existing.options)
    better_xcloud = better_xcloud and svc.id == "xbox-cloud"
    installed = detect(installed_apps(), (svc,))
    need = needs(svc, installed, better_xcloud)
    if need:
        report = getattr(status, "progress", lambda pct: None)
        what = svc.name if need == svc.app else app_name(need)
        status(f"Installing {what} from Flathub…")
        report(-1)
        install_app(need, lambda line: status(f"Installing {what} from Flathub…  {line[:60]}"),
                    lambda pct: (status(f"Installing {what} from Flathub…  {pct}%"), report(pct)))
        report(-1)
        installed = detect(installed_apps(), (svc,)) | {need}
    if better_xcloud:
        status("Installing Better xCloud…")
        install_better_xcloud(fetch=fetch)
    if svc.is_web and uses(svc, installed, better_xcloud) in BROWSERS:
        allow_controllers(uses(svc, installed, better_xcloud))
    status("Adding to Steam…")
    launcher = write_launcher(svc, paths, installed, better_xcloud, kind)
    app = existing or core.App(
        id=f"{kind}-{svc.id}", name=svc.name, exe=str(launcher), prefix="", runtime_name="", runtime_kind="",
        runtime_path="", kind=kind)
    app.options = [BETTER_XCLOUD] if better_xcloud else []
    app.launcher = app.exe = str(launcher)
    app.installed_at = time.time()
    core.use_logo(app, paths, svc.id)
    core.write_desktop_entry(app)
    if core.steam_state(app, roots) != "sent":  # (the running Steam has it, unsaved: again would be a duplicate)
        core.add_to_steam(app, roots)
    core.Library(paths).upsert(app)
    return app
