"""Deckhand engine: pick a runtime, run an installer, find the program, add it to Steam.

Nothing in here imports Qt, so it can be tested headless and reused from the CLI.
"""
from __future__ import annotations

import collections
import contextlib
import fcntl
import json
import mmap
import os
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

INSTALLER_SUFFIXES = (".exe", ".msi")


# ── Paths ────────────────────────────────────────────────────────────────────


@dataclass
class Paths:
    root: Path

    @classmethod
    def default(cls) -> "Paths":
        override = os.environ.get("DECKHAND_HOME")
        if override:
            return cls(Path(override).expanduser())
        return cls(data_home() / "deckhand")

    @property
    def prefixes(self) -> Path:
        return self.root / "prefixes"

    @property
    def launchers(self) -> Path:
        return self.root / "launchers"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def icons(self) -> Path:
        return self.root / "icons"

    @property
    def downloads(self) -> Path:
        """What Deckhand downloads for itself: a store's installer, Decky's install script."""
        return self.root / "downloads"

    @property
    def library_file(self) -> Path:
        return self.root / "library.json"

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    def state(self) -> dict:
        """Small things to remember between runs."""
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def remember(self, **values) -> None:
        data = {**self.state(), **values}
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except OSError:
            pass


# ── The data folder: ~/.local/share/deckhand ─────────────────────────────────


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")


def clean_env() -> dict[str, str]:
    """Environment for child processes, minus anything a PyInstaller bundle injected."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        for key in ("LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "SSL_CERT_FILE"):
            orig = env.pop(key + "_ORIG", None)
            if orig is not None:
                env[key] = orig
            else:
                env.pop(key, None)
    return env


# ── Logos of the services, stores and add-ons Deckhand sets up ─────────────

LOGOS = Path(__file__).resolve().parent / "logos"  # bundled with the app (see logos/README.md)


def logo_file(key: str) -> Path | None:
    """The bundled logo of a streaming service, game store or add-on, by its id."""
    p = LOGOS / f"{key}.png"
    return p if p.is_file() else None


def use_logo(app: "App", paths: Paths, key: str) -> bool:
    """Make the logo `key` the app's icon (a copy: the bundled one is gone once a single-file build
    exits). True if it has one now."""
    logo = logo_file(key)
    if logo is None:
        return False
    target = paths.icons / f"{app.id}.png"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(logo, target)
    except OSError:
        return False
    app.icon = str(target)
    return True


# ── Steam + runtime detection ────────────────────────────────────────────────


def steam_roots(home: Path | None = None) -> list[Path]:
    """Steam install roots (native, symlinked and Flatpak), de-duplicated."""
    home = home or Path.home()
    candidates = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    out: list[Path] = []
    for c in candidates:
        try:
            r = c.resolve()
        except OSError:
            continue
        if (r / "steamapps").is_dir() and r not in out:
            out.append(r)
    return out


def steam_library_dirs(root: Path) -> list[Path]:
    """All Steam library folders (internal storage, SD card, …) for a Steam root."""
    dirs = [root]
    vdf_file = root / "steamapps/libraryfolders.vdf"
    try:
        text = vdf_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return dirs
    for m in re.finditer(r'"path"\s+"([^"]+)"', text):
        p = Path(m.group(1).replace("\\\\", "\\"))
        if p.is_dir() and p not in dirs:
            dirs.append(p)
    return dirs


@dataclass
class Runtime:
    name: str
    kind: str  # "proton" or "wine"
    path: str  # proton script, or wine binary

    @property
    def is_proton(self) -> bool:
        return self.kind == "proton"

    def wineserver(self) -> str | None:
        if not self.is_proton:
            return shutil.which("wineserver")
        base = Path(self.path).parent
        for rel in ("files/bin/wineserver", "dist/bin/wineserver"):
            if (base / rel).is_file():
                return str(base / rel)
        return None


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", name)[:4])


def runtime_rank(rt: Runtime) -> tuple:
    """Higher is better: GE-Proton > Proton Experimental > Proton > Wine, newest first."""
    low = rt.name.lower()
    if not rt.is_proton:
        tier = 0
    elif "ge-proton" in low or "proton-ge" in low or low.startswith("ge"):
        tier = 3
    elif "experimental" in low:
        tier = 2
    else:
        tier = 1
    return (tier, _version_key(rt.name))


def find_runtimes(
    roots: Iterable[Path] | None = None,
    extra_tool_dirs: Iterable[Path] | None = None,
    include_system_wine: bool = True,
) -> list[Runtime]:
    """Every usable Proton/Wine, best first."""
    roots = list(steam_roots() if roots is None else roots)
    tool_dirs: list[Path] = [r / "compatibilitytools.d" for r in roots]
    if extra_tool_dirs is None:
        extra_tool_dirs = [
            Path.home() / ".steam/root/compatibilitytools.d",
            Path("/usr/share/steam/compatibilitytools.d"),
        ]
    tool_dirs.extend(extra_tool_dirs)

    found: dict[str, Runtime] = {}

    def add(script: Path, name: str) -> None:
        try:
            key = str(script.resolve())
        except OSError:
            return
        if script.is_file() and key not in found:
            found[key] = Runtime(name=name, kind="proton", path=str(script))

    def children(d: Path) -> list[Path]:
        try:
            return sorted(d.iterdir()) if d.is_dir() else []
        except OSError:  # unreadable or vanished (e.g. an SD card being removed)
            return []

    for d in tool_dirs:
        for sub in children(d):
            add(sub / "proton", sub.name)
    for root in roots:
        for lib in steam_library_dirs(root):
            for sub in children(lib / "steamapps/common"):
                if "proton" in sub.name.lower():
                    add(sub / "proton", sub.name)

    runtimes = list(found.values())
    if include_system_wine:
        wine = shutil.which("wine")
        if wine:
            runtimes.append(Runtime(name="System Wine", kind="wine", path=wine))
    runtimes.sort(key=runtime_rank, reverse=True)
    return runtimes


# Steam app id of "Proton Experimental" — used to offer a one-tap install.
PROTON_EXPERIMENTAL_APPID = 1493710

# Steam never runs Proton directly: it runs it inside the Steam Linux Runtime container named by
# the tool's toolmanifest.vdf. The container supplies libraries (e.g. 32-bit TLS/gnutls) that
# SteamOS itself may lack; without them installers can fail to download ("no connection").
_RUNTIME_DIR_NAMES = {
    1628350: "SteamLinuxRuntime_sniper",
    1391110: "SteamLinuxRuntime_soldier",
}


def required_container_appid(proton_script: Path) -> int | None:
    try:
        text = (Path(proton_script).parent / "toolmanifest.vdf").read_text(errors="replace")
    except OSError:
        return None
    m = re.search(r'"require_tool_appid"\s+"(\d+)"', text)
    return int(m.group(1)) if m else None


def container_entry_point(proton_script: Path, roots: Iterable[Path] | None = None) -> Path | None:
    """The Steam Linux Runtime entry point Steam would run this Proton in, if installed."""
    if os.environ.get("DECKHAND_NO_CONTAINER"):
        return None
    appid = required_container_appid(proton_script)
    if appid is None:
        return None
    for root in steam_roots() if roots is None else roots:
        for lib in steam_library_dirs(root):
            names: list[str] = []
            try:
                acf = (lib / f"steamapps/appmanifest_{appid}.acf").read_text(errors="replace")
                m = re.search(r'"installdir"\s+"([^"]+)"', acf)
                if m:
                    names.append(m.group(1))
            except OSError:
                pass
            if appid in _RUNTIME_DIR_NAMES:
                names.append(_RUNTIME_DIR_NAMES[appid])
            for name in names:
                ep = lib / "steamapps/common" / name / "_v2-entry-point"
                if ep.is_file() and os.access(ep, os.X_OK):
                    return ep
    return None


# ── Naming ───────────────────────────────────────────────────────────────────

_NAME_NOISE = {
    "setup", "install", "installer", "x64", "x86", "x86_64", "win64", "win32", "64bit",
    "32bit", "amd64", "arm64", "windows", "win", "full", "final", "offline", "web",
    "release", "portable", "gog", "en", "us", "multi", "the_setup",
}


_GENERIC_STEMS = {"setup", "install", "installer", "autorun", "start", "launcher", "app"}
_GENERIC_FOLDERS = {"downloads", "download", "desktop", "home", "deck", "tmp", "temp", "documents"}


def guess_name(installer: Path | str) -> str:
    """Turn 'setup_the_witcher_3_goty_1.32_(10709).exe' into 'The Witcher 3 Goty'.

    A plain 'setup.exe' is named after the folder it's in ('Some Game [GOG]/setup.exe').
    """
    p = Path(installer)
    name = _clean_name(p.stem)
    if name.lower() in _GENERIC_STEMS and p.parent.name.lower() not in _GENERIC_FOLDERS:
        folder = _clean_name(p.parent.name)
        if folder and folder.lower() not in _GENERIC_STEMS:
            return folder
    return name


def _clean_name(stem: str) -> str:
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", stem)
    s = re.sub(r"(?i)(?<![a-z0-9])v?\d+(?:\.\d+)+[a-z]?(?![a-z0-9])", " ", s)  # 1.2.3, v2.0
    tokens: list[str] = []
    for t in re.split(r"[\s._\-+]+", s):
        if not t:
            continue
        t = re.sub(r"(?i)(?<=[a-z])(setup|installer|install)$", "", t)  # ChromeSetup
        if t.lower() in _NAME_NOISE or re.fullmatch(r"v?\d{5,}", t, re.I):
            continue
        tokens.append(t[0].upper() + t[1:] if t.islower() else t)
    return " ".join(tokens) or stem


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


# ── Windows shortcuts (.lnk) ─────────────────────────────────────────────────


@dataclass
class LnkInfo:
    target: str | None = None  # absolute Windows path (LinkInfo)
    relative: str | None = None  # path relative to the .lnk file (StringData)
    workdir: str | None = None
    args: str = ""


def lnk_info(data: bytes) -> LnkInfo | None:
    """Where a Windows .lnk points, plus its arguments and working folder. None if not a .lnk."""
    if len(data) < 0x4C or data[:4] != b"L\x00\x00\x00":
        return None
    info = LnkInfo()
    try:
        (flags,) = struct.unpack_from("<I", data, 0x14)
        pos = 0x4C
        if flags & 0x01:  # HasLinkTargetIDList
            (idl_size,) = struct.unpack_from("<H", data, pos)
            pos += 2 + idl_size
        if flags & 0x02:  # HasLinkInfo
            li = pos
            li_size, header_size, li_flags, _vol, base_off = struct.unpack_from("<5I", data, li)
            if li_flags & 0x01:  # VolumeIDAndLocalBasePath
                if header_size >= 0x24 and struct.unpack_from("<I", data, li + 0x1C)[0]:
                    start = li + struct.unpack_from("<I", data, li + 0x1C)[0]
                    end = start
                    while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
                        end += 2
                    info.target = data[start:end].decode("utf-16-le", errors="replace") or None
                else:
                    start = li + base_off
                    end = data.index(b"\x00", start)
                    info.target = data[start:end].decode("cp1252", errors="replace") or None
            pos = li + li_size
        # StringData: name, relative path, working dir, arguments, icon — each only if its flag is set.
        unicode = bool(flags & 0x80)
        strings = {}
        for bit, key in ((0x04, "name"), (0x08, "relative"), (0x10, "workdir"), (0x20, "args")):
            if not flags & bit:
                continue
            (count,) = struct.unpack_from("<H", data, pos)
            pos += 2
            size = count * 2 if unicode else count
            raw = data[pos:pos + size]
            pos += size
            strings[key] = raw.decode("utf-16-le" if unicode else "cp1252", errors="replace")
        info.relative = strings.get("relative") or None
        info.workdir = strings.get("workdir") or None
        info.args = strings.get("args", "").strip()
    except (struct.error, ValueError):
        pass
    return info if (info.target or info.relative) else None


def lnk_target(data: bytes) -> str | None:
    """Windows path a .lnk points at (from its LinkInfo block), or None."""
    info = lnk_info(data)
    return info.target if info else None


def split_windows_args(cmdline: str) -> list[str]:
    """Split a Windows command line the way programs parse it (quotes group, backslashes kept)."""
    out, cur, quoted, have = [], [], False, False
    for ch in cmdline:
        if ch == '"':
            quoted, have = not quoted, True
        elif ch in " \t" and not quoted:
            if cur or have:
                out.append("".join(cur))
            cur, have = [], False
        else:
            cur.append(ch)
    if cur or have:
        out.append("".join(cur))
    return out


def windows_to_unix(pfx: Path, win_path: str) -> Path | None:
    """Map 'C:\\Program Files\\X\\x.exe' into the prefix, matching case-insensitively."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", win_path.strip())
    if not m:
        return None
    drive = m.group(1).lower()
    cur = pfx / "drive_c" if drive == "c" else pfx / "dosdevices" / f"{drive}:"
    for part in re.split(r"[\\/]+", m.group(2)):
        if not part:
            continue
        nxt = cur / part
        if not nxt.exists():
            try:
                match = next((c for c in cur.iterdir() if c.name.lower() == part.lower()), None)
            except OSError:
                return None
            if match is None:
                return None
            nxt = match
        cur = nxt
    return cur


# ── Finding the installed program ────────────────────────────────────────────

_SKIP_DIR_NAMES = {
    "temp", "tmp", "$pluginsdir", "package cache", "common files",
    "internet explorer", "windows media player", "windows nt", "windows defender",
    "windowspowershell", "microsoft.net", "dotnet", "redist", "_commonredist",
    "directx", "vcredist", "installer", "__installer", "_installer", "support",
}
_BAD_EXE_WORDS = (
    "unins", "uninstall", "setup", "install", "redist", "vcredist", "vc_redist", "dxsetup",
    "dxwebsetup", "dotnet", "crash", "reporter", "bugreport", "update", "patcher", "helper",
    "prereq", "cleanup", "register", "activation", "notification", "service", "elevate",
    "cefprocess", "webhelper", "diagnostic", "benchmark", "7za", "unitycrash", "ue4prereq",
    "easyanticheat", "battleye", "vconsole", "dump", "repair",
)
_BAD_LNK_WORDS = ("uninstall", "readme", "manual", "help", "website", "license", "support", "remove")
_WALK_LIMIT = 20000


@dataclass
class Candidate:
    exe: Path
    score: float
    shortcut_name: str | None = None
    args: list[str] = field(default_factory=list)  # from the program's shortcut
    workdir: Path | None = None  # the shortcut's "Start in" folder


def _start_menu_and_desktop_dirs(drive_c: Path) -> list[tuple[Path, int]]:
    """(directory, bonus) pairs where installers put shortcuts. Desktop beats Start Menu."""
    out: list[tuple[Path, int]] = []
    users = drive_c / "users"
    if users.is_dir():
        for u in users.iterdir():
            out.append((u / "Desktop", 120))
            out.append((u / "Public/Desktop", 120))
            out.append((u / "AppData/Roaming/Microsoft/Windows/Start Menu", 90))
            out.append((u / "Start Menu", 90))
    out.append((drive_c / "ProgramData/Microsoft/Windows/Start Menu", 90))
    return out


def _is_bad_exe(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in _BAD_EXE_WORDS)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1 or w.isdigit()}


def scan_exes(drive_c: Path) -> list[Path]:
    """Executables a user installed (everything except Windows itself, temp and redists)."""
    found: list[Path] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(drive_c):
        seen += 1
        if seen > _WALK_LIMIT:
            break
        top = Path(dirpath) == drive_c
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in _SKIP_DIR_NAMES and not (top and d.lower() == "windows")
        ]
        for f in filenames:
            if f.lower().endswith(".exe"):
                found.append(Path(dirpath) / f)
    return found


def _same_file_contents(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ca, cb = fa.read(1 << 20), fb.read(1 << 20)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


def looks_like_installer(exe: Path, installer: Path | None = None) -> bool:
    """Setup/uninstall/redist tools, or a copy of the installer the user picked."""
    if _is_bad_exe(exe.name):
        return True
    if installer is not None:
        return exe.name.lower() == installer.name.lower() or _same_file_contents(exe, installer)
    return False


def snapshot_dirs(root: Path, depth: int = 3) -> set[Path]:
    """Visible directories under root, a few levels deep — to spot what an installer created."""
    out: set[Path] = set()
    seen = 0
    for dirpath, dirnames, _files in os.walk(root):
        seen += 1
        if seen > _WALK_LIMIT:
            break
        cur = Path(dirpath)
        level = len(cur.relative_to(root).parts)
        dirnames[:] = [d for d in dirnames if not d.startswith(".")] if level < depth else []
        out.update(cur / d for d in dirnames)
    return out


def new_top_dirs(before: set[Path], after: set[Path]) -> list[Path]:
    """Directories in `after` that didn't exist before, without their own subfolders."""
    new = after - before
    return sorted(d for d in new if d.parent not in new)


def find_program(
    pfx: Path,
    name_hint: str = "",
    installer: Path | None = None,
    extra_dirs: Iterable[Path] = (),
) -> list[Candidate]:
    """Rank the installed executables in a prefix by how likely each is 'the program'. Best first.

    `extra_dirs` are folders the installer created outside C: (e.g. on D:, the home folder).
    Copies of the installer itself are never returned: the goal is the finished product.
    """
    drive_c = pfx / "drive_c"
    if not drive_c.is_dir():
        return []
    cands: dict[Path, Candidate] = {}
    hint = _words(name_hint)

    def cand(p: Path) -> Candidate:
        key = p.resolve()
        if key not in cands:
            cands[key] = Candidate(exe=p, score=0.0)
        return cands[key]

    found = [(exe, drive_c) for exe in scan_exes(drive_c)]
    for d in extra_dirs:
        # An installer-created folder is like "Program Files\X": score from the same depth.
        found += [(exe, d.parent.parent) for exe in scan_exes(d)]
    for exe, base in found:
        c = cand(exe)
        rel = exe.relative_to(base)
        c.score -= 3 * (len(rel.parts) - 1)
        try:
            size_mb = exe.stat().st_size / 1_000_000
        except OSError:
            size_mb = 0
        c.score += min(25.0, 5 * max(0.0, size_mb) ** 0.5)
        if rel.parts and rel.parts[0].lower().startswith("program files"):
            c.score += 10
        if "shipping" in exe.name.lower():
            c.score += 10

    for d, bonus in _start_menu_and_desktop_dirs(drive_c):
        if not d.is_dir():
            continue
        for lnk in d.rglob("*"):
            if lnk.suffix.lower() != ".lnk" or any(w in lnk.stem.lower() for w in _BAD_LNK_WORDS):
                continue
            try:
                info = lnk_info(lnk.read_bytes())
            except OSError:
                continue
            if info is None:
                continue
            exe = windows_to_unix(pfx, info.target) if info.target else None
            if exe is None and info.relative:  # no absolute path: resolve next to the .lnk file
                exe = (lnk.parent / info.relative.replace("\\", "/")).resolve()
            if exe is None or exe.suffix.lower() != ".exe" or not exe.is_file():
                continue
            c = cand(exe)
            if c.shortcut_name is None or bonus > 100:
                c.shortcut_name = lnk.stem
                c.args = split_windows_args(info.args) if info.args else []
                wd = windows_to_unix(pfx, info.workdir) if info.workdir else None
                c.workdir = wd if wd is not None and wd.is_dir() else None
            c.score += bonus

    if installer is not None:
        for key in [k for k, c in cands.items() if _same_file_contents(c.exe, installer)]:
            del cands[key]

    for c in cands.values():
        if _is_bad_exe(c.exe.name):
            c.score -= 200
        overlap = hint & (_words(c.exe.stem) | _words(c.exe.parent.name) | _words(c.shortcut_name or ""))
        c.score += 20 * len(overlap)
        if installer is not None and c.exe.name.lower() == installer.name.lower():
            c.score -= 100

    return sorted(cands.values(), key=lambda c: c.score, reverse=True)


# ── Binary VDF (Steam shortcuts.vdf) ─────────────────────────────────────────


class U64(int):
    """Marks a 64-bit VDF value so it round-trips with the right type tag."""


class I64(int):
    pass


def vdf_loads(data: bytes) -> dict:
    pos = 0

    def cstr() -> str:
        nonlocal pos
        end = data.index(b"\x00", pos)
        s = data[pos:end].decode("utf-8", errors="surrogateescape")
        pos = end + 1
        return s

    def parse_map() -> dict:
        nonlocal pos
        out: dict = {}
        while True:
            if pos >= len(data):
                raise ValueError("truncated VDF")
            t = data[pos]
            pos += 1
            if t == 0x08:
                return out
            key = cstr()
            if t == 0x00:
                out[key] = parse_map()
            elif t == 0x01:
                out[key] = cstr()
            elif t == 0x02:
                (out[key],) = struct.unpack_from("<i", data, pos)
                pos += 4
            elif t == 0x03:
                (out[key],) = struct.unpack_from("<f", data, pos)
                pos += 4
            elif t == 0x07:
                out[key] = U64(struct.unpack_from("<Q", data, pos)[0])
                pos += 8
            elif t == 0x0A:
                out[key] = I64(struct.unpack_from("<q", data, pos)[0])
                pos += 8
            else:
                raise ValueError(f"unsupported VDF type 0x{t:02x}")

    result = parse_map()
    return result


def vdf_dumps(obj: dict) -> bytes:
    out = bytearray()

    def key(k: str) -> bytes:
        return str(k).encode("utf-8", errors="surrogateescape") + b"\x00"

    def dump_map(m: dict) -> None:
        for k, v in m.items():
            if isinstance(v, dict):
                out.extend(b"\x00" + key(k))
                dump_map(v)
            elif isinstance(v, str):
                out.extend(b"\x01" + key(k) + v.encode("utf-8", errors="surrogateescape") + b"\x00")
            elif isinstance(v, U64):
                out.extend(b"\x07" + key(k) + struct.pack("<Q", v))
            elif isinstance(v, I64):
                out.extend(b"\x0a" + key(k) + struct.pack("<q", v))
            elif isinstance(v, float):
                out.extend(b"\x03" + key(k) + struct.pack("<f", v))
            elif isinstance(v, int):
                out.extend(b"\x02" + key(k) + struct.pack("<I", v & 0xFFFFFFFF))
            else:
                raise TypeError(f"cannot encode {type(v).__name__} in VDF")
        out.append(0x08)

    dump_map(obj)
    return bytes(out)


def shortcut_appid(exe_field: str, name: str) -> int:
    """The id Steam itself gives a non-Steam shortcut (so artwork tools recognise it)."""
    return (zlib.crc32((exe_field + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def _signed32(v: int) -> int:
    return v - (1 << 32) if v & 0x80000000 else v


def steam_user_config_dirs(roots: Iterable[Path] | None = None) -> list[Path]:
    out: list[Path] = []
    for root in steam_roots() if roots is None else roots:
        ud = root / "userdata"
        if not ud.is_dir():
            continue
        for d in sorted(ud.iterdir()):
            if d.name.isdigit() and d.name != "0" and d.is_dir():
                cfg = (d / "config").resolve()
                if cfg not in out:
                    out.append(cfg)
    return out


def _edit_shortcuts(cfg: Path, edit: Callable[[list[dict]], list[dict]]) -> None:
    f = cfg / "shortcuts.vdf"
    if f.exists():
        raw = f.read_bytes()
        data = vdf_loads(raw) if raw.strip(b"\x00\x08") else {}
        backup = f.with_suffix(".vdf.deckhand-bak")
        if not backup.exists():
            backup.write_bytes(raw)
    else:
        data = {}
    sc = data.get("shortcuts")
    entries = list(sc.values()) if isinstance(sc, dict) else []
    entries = edit(entries)
    data["shortcuts"] = {str(i): e for i, e in enumerate(entries)}
    cfg.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".vdf.tmp")
    tmp.write_bytes(vdf_dumps(data))
    os.replace(tmp, f)


def _entry_appid(e: dict) -> int:
    v = e.get("appid", e.get("appId", 0))
    return int(v) & 0xFFFFFFFF if isinstance(v, int) else 0


def add_steam_shortcut(
    name: str,
    exe: str,
    start_dir: str,
    icon: str = "",
    roots: Iterable[Path] | None = None,
) -> tuple[int, int]:
    """Add (or replace) a non-Steam shortcut for every Steam user. Returns (appid, users updated)."""
    exe_field = f'"{exe}"'
    appid = shortcut_appid(exe_field, name)
    entry = {
        "appid": _signed32(appid),
        "AppName": name,
        "Exe": exe_field,
        "StartDir": f'"{start_dir}"',
        "icon": icon,
        "ShortcutPath": "",
        "LaunchOptions": "",
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {},
    }
    updated = 0
    for cfg in steam_user_config_dirs(roots):
        try:
            _edit_shortcuts(
                cfg,
                lambda es: [e for e in es if _entry_appid(e) != appid and e.get("Exe") != exe_field] + [entry],
            )
            updated += 1
        except (OSError, ValueError, TypeError):
            continue
    return appid, updated


def remove_steam_shortcut(appid: int, roots: Iterable[Path] | None = None) -> None:
    for cfg in steam_user_config_dirs(roots):
        if not (cfg / "shortcuts.vdf").exists():
            continue
        try:
            _edit_shortcuts(cfg, lambda es: [e for e in es if _entry_appid(e) != appid])
        except (OSError, ValueError, TypeError):
            continue


# ── Library ──────────────────────────────────────────────────────────────────


@dataclass
class App:
    id: str
    name: str
    exe: str
    prefix: str
    runtime_name: str
    runtime_kind: str
    runtime_path: str
    launcher: str = ""
    steam_appid: int = 0
    installed_at: float = field(default_factory=time.time)
    installer: str = ""  # the setup file it was installed from
    icon: str = ""  # PNG extracted from the program's .exe
    artwork: list[str] = field(default_factory=list)  # Steam library images we generated
    extra_dirs: list[str] = field(default_factory=list)  # program folder outside C: (installed to D:)
    steam_added: str = ""  # how it got into Steam: see add_shortcut ("live", "requested", "file", …)
    args: list[str] = field(default_factory=list)  # arguments from the program's own shortcut
    workdir: str = ""  # folder to start in ("" = the program's folder)
    steam_requested_at: float = 0.0  # when it was handed to the running Steam (steam_added == "requested")
    kind: str = "program"  # "program" (a Windows program in its own prefix), "stream" (streaming.py), "store", "addon"
    options: list[str] = field(default_factory=list)  # e.g. "better-xcloud" for Xbox Cloud Gaming
    dirs_known: bool = False  # extra_dirs was worked out when it was installed (older records: guess_program_dir)

    @property
    def runtime(self) -> Runtime:
        return Runtime(self.runtime_name, self.runtime_kind, self.runtime_path)


class Library:
    def __init__(self, paths: Paths):
        self.paths = paths

    def load(self) -> list[App]:
        try:
            raw = json.loads(self.paths.library_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        known = set(App.__dataclass_fields__)
        apps = []
        for item in raw if isinstance(raw, list) else []:
            try:
                apps.append(App(**{k: v for k, v in item.items() if k in known}))
            except (TypeError, AttributeError):  # a damaged entry shouldn't stop the app from starting
                continue
        return apps

    def save(self, apps: list[App]) -> None:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        tmp = self.paths.library_file.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps([asdict(a) for a in apps], indent=2), encoding="utf-8")
        os.replace(tmp, self.paths.library_file)

    @contextlib.contextmanager
    def _locked(self):
        """One writer at a time, even across two Deckhand windows."""
        self.paths.root.mkdir(parents=True, exist_ok=True)
        with open(self.paths.root / "library.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def upsert(self, app: App) -> None:
        with self._locked():
            apps = [a for a in self.load() if a.id != app.id]
            apps.append(app)
            self.save(apps)

    def remove(self, app_id: str) -> None:
        with self._locked():
            self.save([a for a in self.load() if a.id != app_id])

    def unique_id(self, name: str) -> str:
        base = slugify(name)
        taken = {a.id for a in self.load()}
        if self.paths.prefixes.is_dir():
            taken |= {p.name for p in self.paths.prefixes.iterdir()}
        slug, n = base, 2
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
        return slug


# ── Running things ───────────────────────────────────────────────────────────


def runtime_env(
    rt: Runtime,
    compat_dir: Path,
    steam_root: Path | None,
    mounts: Iterable[Path] = (),
) -> dict[str, str]:
    env = clean_env()
    env["WINEPREFIX"] = str(compat_dir / "pfx")
    env.setdefault("WINEDEBUG", "-all")
    if rt.is_proton:
        env["STEAM_COMPAT_DATA_PATH"] = str(compat_dir)
        # Steam always sets these. Without them GE-Proton's protonfixes looks for a number in
        # STEAM_COMPAT_DATA_PATH and crashes before running anything if there is none.
        env.setdefault("SteamAppId", "0")
        env.setdefault("SteamGameId", "0")
        env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(steam_root or Path.home() / ".steam/steam")
        # Make Proton itself and anything outside the home folder visible inside the container.
        env["STEAM_COMPAT_TOOL_PATHS"] = str(Path(rt.path).parent)
        extra = [str(m) for m in mounts]
        if extra:
            env["STEAM_COMPAT_MOUNTS"] = ":".join(extra)
    return env


def run_command(rt: Runtime, target: str | Path, *extra: str, entry: Path | None = None,
                verb: str | None = None) -> list[str]:
    """Command line that runs a Windows .exe/.msi (unix or Windows path) under the runtime.

    With `entry` (a Steam Linux Runtime entry point) Proton runs inside the container, exactly
    as Steam launches games. `verb` is Proton's: "run", or "waitforexitandrun" (first wait until
    everything already running in the prefix has exited).
    """
    target = str(target)
    args = ["msiexec", "/i", target] if target.lower().endswith(".msi") else [target, *extra]
    if not rt.is_proton:
        return [rt.path, *args]
    if entry is not None:
        verb = verb or "waitforexitandrun"
        return [str(entry), f"--verb={verb}", "--", rt.path, verb, *args]
    return [rt.path, verb or "run", *args]


_INHIBIT: list[str] | None = None


def sleep_inhibitor() -> list[str]:
    """A systemd-inhibit prefix that keeps the Deck awake during a long install ([] if not allowed).

    Checked once with a harmless command, so an install never fails because inhibiting did."""
    global _INHIBIT
    if _INHIBIT is None:
        exe = shutil.which("systemd-inhibit")
        cmd = [exe, "--what=sleep:idle", "--who=Deckhand", "--why=Installing a Windows program",
               "--mode=block"] if exe else []
        if cmd:
            try:
                ok = subprocess.run([*cmd, "true"], env=clean_env(), timeout=5, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            cmd = cmd if ok else []
        _INHIBIT = cmd
    return list(_INHIBIT)


# ── Drive letters ────────────────────────────────────────────────────────────
#
# Proton maps only C: (the prefix) and Z: (the whole filesystem, "/"). On SteamOS "/" is the
# small read-only system partition ("rootfs"), so installers that look at Z: — or that default
# to the drive they were started from — report "not enough space". While installing we point Z:
# at the home folder (where the free space is; Proton recreates a *missing* Z: every launch, so
# it can't simply be removed), expose the home folder as D:, and start the installer from there.

HOME_DRIVE = "d"
INSTALLER_DRIVE = "e"  # only used when the installer lives outside the home folder


def _dosdevices(pfx: Path) -> Path:
    return pfx / "dosdevices"


def map_drive(pfx: Path, letter: str, target: Path) -> None:
    link = _dosdevices(pfx) / f"{letter}:"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        if link.is_symlink() and os.readlink(link) == str(target):
            return
        link.unlink()
    os.symlink(str(target), link)


def to_windows_path(letter: str, root: Path, path: Path) -> str:
    rel = path.relative_to(root)
    return f"{letter.upper()}:\\" + "\\".join(rel.parts)


def hide_system_drive(pfx: Path, replacement: Path) -> str | None:
    """Point Z: at `replacement` for the duration of an install. Returns its old target."""
    z = _dosdevices(pfx) / "z:"
    if not z.is_symlink():
        return None
    target = os.readlink(z)
    z.unlink()
    os.symlink(str(replacement), z)
    return target


def restore_system_drive(pfx: Path, target: str | None) -> None:
    if target is None:
        return
    z = _dosdevices(pfx) / "z:"
    if z.is_symlink() and os.readlink(z) == target:
        return
    if z.is_symlink() or z.exists():
        z.unlink()
    os.symlink(target, z)


def free_space(path: Path) -> int:
    """Free bytes on the filesystem holding path (or its nearest existing parent)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 0


class WriteMeter:
    """How much an installer has written so far, and how fast: measured as free space going down on
    the drives it writes to (home, where C: and D: live, and the installer's own drive). Cheap enough
    to check every second, however big the install; other activity on the Deck adds a little noise."""

    def __init__(self, paths: Iterable[Path], now: float | None = None,
                 free: Callable[[Path], int] | None = None):
        self.free = free or free_space
        self.drives: dict[object, Path] = {}
        for p in paths:
            try:
                key: object = os.stat(p).st_dev
            except OSError:
                key = str(p)
            self.drives.setdefault(key, Path(p))
        self.base = {k: self.free(p) for k, p in self.drives.items()}
        t = time.monotonic() if now is None else now
        self.samples: collections.deque[tuple[float, int]] = collections.deque([(t, 0)], maxlen=8)
        self.peak = 0
        self.last_growth = t

    def sample(self, now: float | None = None) -> tuple[int, float]:
        """(bytes written so far, bytes per second over the last few seconds)."""
        t = time.monotonic() if now is None else now
        written = max(0, sum(self.base[k] - self.free(p) for k, p in self.drives.items()))
        if written > self.peak + (1 << 20):  # growing (more than 1 MB since the last growth)
            self.peak, self.last_growth = written, t
        t0, w0 = self.samples[0]
        self.samples.append((t, written))
        rate = max(0.0, (written - w0) / (t - t0)) if t > t0 else 0.0
        return written, rate

    def idle_for(self, now: float | None = None) -> float:
        """Seconds since the install last grew."""
        return (time.monotonic() if now is None else now) - self.last_growth


def install_estimate(written: int, expected: int) -> int | None:
    """Percent done, estimated from the installer's own size (installs usually end up at least that big),
    or None when there's nothing to go on or the install has outgrown the estimate."""
    if expected < 100 << 20 or written >= expected:
        return None
    return min(99, int(written * 100 / expected))


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def write_launcher(
    app: App,
    paths: Paths,
    steam_root: Path | None,
    roots: Iterable[Path] | None = None,
) -> Path:
    """A shell script Steam can run directly. It re-finds Proton if the saved one was removed,
    and runs it inside the Steam Linux Runtime like Steam does (plain Proton if that's missing)."""
    paths.launchers.mkdir(parents=True, exist_ok=True)
    script = paths.launchers / f"{app.id}.sh"
    compat = Path(app.prefix)
    q = shlex.quote
    workdir = app.workdir if app.workdir and Path(app.workdir).is_dir() else str(Path(app.exe).parent)
    run = " ".join([q(app.exe), *(q(a) for a in app.args), '"$@"'])
    lines = [
        "#!/bin/bash",
        "# Deckhand launcher for " + " ".join(app.name.split()),
        f"export WINEPREFIX={q(str(compat / 'pfx'))}",
        f"LOG={q(str(paths.logs / (app.id + '-launch.log')))}",
        '{ mkdir -p "$(dirname "$LOG")" && exec >"$LOG" 2>&1; } || true  # last launch only, for troubleshooting',
        'echo "Deckhand: starting $(date)"',
        "# Z: points at the home folder while installing; put it back if an install was interrupted.",
        '[ "$(readlink "$WINEPREFIX/dosdevices/z:")" = / ] || ln -sfn / "$WINEPREFIX/dosdevices/z:"',
        f"cd {q(workdir)} || cd {q(str(Path(app.exe).parent))} || exit 1",
    ]
    if app.runtime_kind == "proton":
        client = str(steam_root or Path.home() / ".steam/steam")
        entry = container_entry_point(Path(app.runtime_path), roots)
        home = Path.home().resolve()
        mounts = sorted({str(p) for p in (compat, Path(app.exe).parent, Path(workdir))
                         if not p.resolve().is_relative_to(home)})
        if mounts:  # the Steam Linux Runtime only sees the home folder unless told otherwise
            lines.append(f"export STEAM_COMPAT_MOUNTS={q(':'.join(mounts))}")
        lines += [
            f"export STEAM_COMPAT_DATA_PATH={q(str(compat))}",
            'export SteamAppId="${SteamAppId:-0}" SteamGameId="${SteamGameId:-0}"',
            f"export STEAM_COMPAT_CLIENT_INSTALL_PATH={q(client)}",
            f"PROTON={q(app.runtime_path)}",
            'if [ ! -x "$PROTON" ]; then',
            "  # Saved Proton is gone (updated/removed): use the newest one still installed.",
            '  PROTON=$(ls -d "$HOME"/.steam/root/compatibilitytools.d/*/proton '
            '"$HOME"/.local/share/Steam/compatibilitytools.d/*/proton '
            '"$HOME"/.local/share/Steam/steamapps/common/Proton*/proton 2>/dev/null | sort -V | tail -n 1)',
            "fi",
            'if [ -z "$PROTON" ]; then echo "No Proton found. Install Proton Experimental from Steam."; exit 1; fi',
            'echo "Proton: $PROTON"',
            'export STEAM_COMPAT_TOOL_PATHS="$(dirname "$PROTON")"',
            f"ENTRY={q(str(entry or ''))}",
            'if [ -n "$ENTRY" ] && [ -x "$ENTRY" ] && [ -z "$DECKHAND_NO_CONTAINER" ]; then',
            f'  exec "$ENTRY" --verb=waitforexitandrun -- "$PROTON" waitforexitandrun {run}',
            "fi",
            f'exec "$PROTON" run {run}',
        ]
    else:
        lines.append(f'exec {q(app.runtime_path)} {run}')
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


class Cancelled(Exception):
    pass


class InstallError(Exception):
    pass


@dataclass
class PendingInstall:
    """Result of running an installer: everything needed to finish, plus ranked guesses."""

    id: str
    name: str
    installer: Path
    compat_dir: Path
    runtime: Runtime
    candidates: list[Candidate]
    log_file: Path
    new_dirs: list[Path] = field(default_factory=list)  # folders the installer created in home (D:)

    @property
    def pfx(self) -> Path:
        return self.compat_dir / "pfx"


class Installer:
    """Runs one installer end-to-end. Call run(), then finish() with the chosen exe."""

    def __init__(
        self,
        installer: Path,
        paths: Paths | None = None,
        runtime: Runtime | None = None,
        steam_root: Path | None = None,
        status: Callable[[str], None] = lambda s: None,
        log: Callable[[str], None] = lambda s: None,
        steam_roots_override: list[Path] | None = None,
        allow_no_container: bool = False,
        name: str = "",
    ):
        self.installer = Path(installer).expanduser().resolve()
        self.name = " ".join(name.split())  # "" = guessed from the installer's file name
        self.paths = paths or Paths.default()
        self.home = Path.home().resolve()
        self.entry: Path | None = None
        self._tail: collections.deque[str] = collections.deque(maxlen=12)
        self.allow_no_container = allow_no_container
        self.library = Library(self.paths)
        self._roots = steam_roots() if steam_roots_override is None else steam_roots_override
        self.steam_root = steam_root or (self._roots[0] if self._roots else None)
        self.runtime = runtime
        self.status = status
        self._log_cb = log
        self._log_fh = None
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._directx_logged = False
        self._skip_wait = False
        self.stage = "idle"
        self._env: dict[str, str] | None = None
        self._lock = threading.Lock()

    def _set(self, stage: str, text: str) -> None:
        self.stage = stage
        self.status(text)

    # logging
    def _log(self, msg: str) -> None:
        self._tail.append(msg.rstrip("\n"))
        if self._log_fh:
            self._log_fh.write(msg.rstrip("\n") + "\n")
            self._log_fh.flush()
        self._log_cb(msg.rstrip("\n"))

    def _log_directx(self, pfx: Path) -> None:
        if not self._directx_logged and (text := directx_log(pfx)):
            self._directx_logged = True
            self._log("The installer ran DirectX setup; its log says:\n" + text)

    def _stream(self, cmd: list[str]) -> int:
        self._log("$ " + " ".join(shlex.quote(c) for c in cmd))
        with self._lock:
            if self._cancelled:
                raise Cancelled()
            self._proc = subprocess.Popen(
                cmd,
                env=self._env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                errors="replace",
                start_new_session=True,
            )
        assert self._proc.stdout is not None
        with self._proc.stdout:
            for line in self._proc.stdout:
                self._log(line)
        rc = self._proc.wait()
        with self._lock:
            self._proc = None
        return rc

    def _kill_wine(self) -> None:
        ws = self.runtime.wineserver() if self.runtime else None
        cmds = [[ws, "-k"]] if ws else []
        if self.runtime is not None and self.runtime.is_proton and self.entry is not None:
            # The container's Wine may not be reachable from outside: end its processes from inside.
            cmds.append(run_command(self.runtime, "wineboot.exe", "-k", entry=self.entry, verb="run"))
        for cmd in cmds if self._env else []:
            try:
                subprocess.run(cmd, env=self._env, timeout=60, check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                pass
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass

    def _wait_for_wine(self, ws: str | None, inhibit: bool = False) -> None:
        """Block until every Windows process in the prefix has exited (installers often hand off
        to a second process and exit early)."""
        pre = sleep_inhibitor() if inhibit else []
        if self.runtime is not None and self.runtime.is_proton:
            # Proton's waitforexitandrun waits for the prefix's Wine where it runs (inside the
            # container, which the outside may not be able to see), then runs a no-op.
            self._stream(pre + run_command(self.runtime, "cmd.exe", "/c", "exit", entry=self.entry,
                                           verb="waitforexitandrun"))
        if ws and not self._skip_wait:
            self._stream([ws, "-w"])  # and the no-op's own short-lived Wine

    def cancel(self) -> None:
        """Abort: close every window of the installer and throw the prefix away."""
        self._cancelled = True
        threading.Thread(target=self._kill_wine, daemon=True).start()

    def continue_now(self) -> None:
        """Stop waiting for leftover windows (e.g. the app the installer auto-started)."""
        self._skip_wait = True
        threading.Thread(target=self._kill_wine, daemon=True).start()

    def run(self) -> PendingInstall:
        if not self.installer.is_file():
            raise InstallError(f"File not found: {self.installer}")
        if self.installer.suffix.lower() not in INSTALLER_SUFFIXES:
            raise InstallError("Pick a Windows installer (.exe or .msi).")

        self._set("prepare", "Getting ready…")
        if self.runtime is None:
            runtimes = find_runtimes(self._roots)
            if not runtimes:
                raise InstallError("NO_RUNTIME")
            # The best Proton that can run the way Steam runs it (its Steam Linux Runtime is
            # installed); only if none can does the user get asked to download a runtime.
            ready = [r for r in runtimes if not r.is_proton or not required_container_appid(Path(r.path))
                     or container_entry_point(Path(r.path), self._roots)]
            self.runtime = (ready or runtimes)[0]

        if self.runtime.is_proton and not self.allow_no_container:
            need = required_container_appid(Path(self.runtime.path))
            if need and container_entry_point(Path(self.runtime.path), self._roots) is None:
                raise InstallError(f"NO_CONTAINER:{need}")

        name = self.name or guess_name(self.installer)
        app_id = self.library.unique_id(name)
        compat = self.paths.prefixes / app_id
        compat.mkdir(parents=True, exist_ok=True)
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        log_file = self.paths.logs / f"{app_id}.log"
        self._log_fh = open(log_file, "w", encoding="utf-8")
        mounts = [p for p in (self.installer.parent, self.paths.root) if not p.is_relative_to(self.home)]
        self._env = runtime_env(self.runtime, compat, self.steam_root, mounts)
        self.entry = container_entry_point(Path(self.runtime.path), self._roots) if self.runtime.is_proton else None
        self._log(f"Deckhand: installing {self.installer.name} as '{name}'")
        save_install_info(compat, name, self.installer, self.runtime)
        self._log(f"Runtime: {self.runtime.name} ({self.runtime.path})")
        if self.entry:
            self._log(f"Container: {self.entry.parent.name} (same as Steam)")
        elif self.runtime.is_proton and required_container_appid(Path(self.runtime.path)):
            self._log("Warning: the Steam Linux Runtime this Proton needs isn't installed; running "
                      "Proton directly. Downloads inside installers may fail.")

        pfx = compat / "pfx"
        hidden_z: str | None = None
        ws = self.runtime.wineserver()
        try:
            # 1. Create the Windows environment first, so its drives can be adjusted.
            self._set("prepare", "Setting up Windows… (takes a minute the first time)")
            self._stream(run_command(self.runtime, "cmd.exe", "/c", "exit", entry=self.entry))
            self._wait_for_wine(ws)
            if self._cancelled:
                raise Cancelled()
            if not (pfx / "system.reg").exists():
                tail = "\n".join(line for line in self._tail if not line.startswith("$ "))[-1500:]
                raise InstallError(f"{self.runtime.name} failed to start, so the installer never ran.\n\n"
                                   f"Last lines of the log:\n{tail}")

            # 2. Show the home folder as D: and hide the full system drive Z: (see HOME_DRIVE).
            home = self.home
            map_drive(pfx, HOME_DRIVE, home)
            try:
                target = to_windows_path(HOME_DRIVE, home, self.installer)
            except ValueError:
                map_drive(pfx, INSTALLER_DRIVE, self.installer.parent)
                target = to_windows_path(INSTALLER_DRIVE, self.installer.parent, self.installer)
            hidden_z = hide_system_drive(pfx, home)
            self._log(f"Drives: C: = {pfx / 'drive_c'} ({human_size(free_space(pfx))} free), "
                      f"D: and Z: = {home} ({human_size(free_space(home))} free) while installing")
            home_before = snapshot_dirs(home)

            # 3. Run the installer.
            self._set("installer", "Running the installer — follow the steps on screen.\n"
                      f"Install to C: or D: (both have {human_size(free_space(home))} free).")
            rc = self._stream(sleep_inhibitor() + run_command(self.runtime, target, entry=self.entry))
            self._log(f"Installer exited with code {rc}")
            if self._cancelled:
                raise Cancelled()

            if not self._skip_wait:
                self._set("wait", "Waiting for the installer's windows to close…")
                self._wait_for_wine(ws, inhibit=True)
            if self._cancelled:
                raise Cancelled()
            self._log_directx(pfx)  # (DirectX setup often runs after the installer's first window closes)
            restore_system_drive(pfx, hidden_z)
            hidden_z = None

            self._set("scan", "Finding the installed program…")
            extra = [d for d in new_top_dirs(home_before, snapshot_dirs(home))
                     if not d.resolve().is_relative_to(self.paths.root.resolve())]
            if extra:
                self._log("New folders outside C: " + ", ".join(map(str, extra)))
            cands = find_program(pfx, name, self.installer, extra)
            self._log("Candidates: " + ", ".join(f"{c.exe.name}={c.score:.0f}" for c in cands[:8]))
            return PendingInstall(app_id, name, self.installer, compat, self.runtime, cands, log_file, extra)
        except Cancelled:
            self._log_directx(pfx)
            self._log("Cancelled.")
            self.close()
            shutil.rmtree(compat, ignore_errors=True)
            raise
        except InstallError:
            self._log_directx(pfx)
            self.close()
            shutil.rmtree(compat, ignore_errors=True)
            raise
        except BaseException:
            self.close()
            raise
        finally:
            if compat.exists():
                restore_system_drive(pfx, hidden_z)

    def close(self) -> None:
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None

    def finish(self, pending: PendingInstall, exe: Path, name: str | None = None, icon: str = "") -> App:
        """Save the app, write its launcher and add it to Steam (with `icon`, a PNG path)."""
        if self._log_fh is None:  # finishing an install from an earlier session: keep adding to its log
            try:
                pending.log_file.parent.mkdir(parents=True, exist_ok=True)
                self._log_fh = open(pending.log_file, "a", encoding="utf-8")
            except OSError:
                pass
        self._set("steam", "Adding to Steam…")
        app = App(
            id=pending.id,
            name=" ".join((name or "").split()) or pending.name,
            exe=str(Path(exe).resolve()),
            prefix=str(pending.compat_dir),
            runtime_name=pending.runtime.name,
            runtime_kind=pending.runtime.kind,
            runtime_path=pending.runtime.path,
            installer=str(pending.installer),
            icon=icon,
        )
        app.extra_dirs = [str(d) for d in program_dirs(Path(app.exe), pending.new_dirs)]
        app.dirs_known = True  # (none found: nothing outside the prefix is the program's to delete)
        chosen = next((c for c in pending.candidates if c.exe.resolve() == Path(app.exe)), None)
        if chosen is not None:
            app.args = list(chosen.args)
            app.workdir = str(chosen.workdir) if chosen.workdir else ""
        launcher = write_launcher(app, self.paths, self.steam_root, self._roots)
        app.launcher = str(launcher)
        write_desktop_entry(app)
        running = steam_is_running()
        before = len(steam_shortcuts(self._roots))
        add_to_steam(app, self._roots, running)
        after = len(steam_shortcuts(self._roots))
        self.library.upsert(app)
        self._log(f"Installed '{app.name}' → {app.exe} (Steam: {app.steam_added or 'not added'})")
        self._log(f"Steam was {'running' if running else 'closed'}; its saved shortcut list went from "
                  f"{before} to {after} entries")
        self.close()
        return app


RUN_OK = {0, 1638 % 256, 1641 % 256, 3010 % 256}  # done; a newer one is there; done, restart wanted (exit
# codes reach Linux as their lowest 8 bits)


def run_in_prefix(app: App, target: Path, *args: str, roots: Iterable[Path] | None = None,
                  log: Callable[[str], None] = lambda s: None) -> int:
    """Run a Windows program (a runtime's installer, say) in an installed program's prefix with its
    Proton, the way installs run (inside the Steam Linux Runtime when it's installed), and wait until
    everything it started has finished. Returns its exit code."""
    roots = list(steam_roots() if roots is None else roots)
    rt = app.runtime
    if not Path(rt.path).exists():  # the Proton it was installed with is gone: the best one still here
        found = find_runtimes(roots)
        if not found:
            raise InstallError("NO_RUNTIME")
        rt = found[0]
    compat = Path(app.prefix)
    home = Path.home().resolve()
    mounts = [p for p in (Path(target).parent, compat) if not p.resolve().is_relative_to(home)]
    env = runtime_env(rt, compat, roots[0] if roots else None, mounts)
    entry = container_entry_point(Path(rt.path), roots) if rt.is_proton else None

    def stream(cmd: list[str]) -> int:
        log("$ " + " ".join(shlex.quote(c) for c in cmd))
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, errors="replace", start_new_session=True)
        assert proc.stdout is not None
        with proc.stdout:
            for line in proc.stdout:
                log(line.rstrip("\n"))
        return proc.wait()

    rc = stream(sleep_inhibitor() + run_command(rt, target, *args, entry=entry, verb="waitforexitandrun"))
    if rt.is_proton:  # installers hand off to other processes: wait for those too
        stream(run_command(rt, "cmd.exe", "/c", "exit", entry=entry, verb="waitforexitandrun"))
    ws = rt.wineserver()
    if ws:
        stream([ws, "-w"])
    log(f"Exit code {rc}")
    return rc


def pe_machine(exe: Path) -> str:
    """"x64" or "x86" for a Windows .exe, "" if it can't be read."""
    try:
        with open(exe, "rb") as f:
            head = f.read(4096)
        (pe,) = struct.unpack_from("<I", head, 0x3C)
        if head[:2] != b"MZ" or head[pe:pe + 4] != b"PE\0\0":
            return ""
        (machine,) = struct.unpack_from("<H", head, pe + 4)
    except (OSError, struct.error):
        return ""
    return {0x8664: "x64", 0x14C: "x86"}.get(machine, "")


def _shared_folder(folder: Path) -> bool:
    """A folder that holds unrelated things (Downloads, Desktop, a drive's root…), not one program."""
    home = Path.home().resolve()
    f = folder.resolve()
    return (f in (home, Path("/")) or f in removable_media() or f.parent == Path("/run/media")
            or (f.parent == home and f.name.lower() in _PROTECTED_HOME_DIRS | _GENERIC_FOLDERS))


def portable_folder(pending: PendingInstall) -> tuple[Path, int] | None:
    """The program's own folder with other files in it, if copying it along is worth offering.

    Portable programs usually need the files next to them. Never offered for shared folders
    (Downloads, Desktop, a drive's root…) or a folder that contains Deckhand's own data."""
    folder = pending.installer.parent
    if _shared_folder(folder) or pending.compat_dir.resolve().is_relative_to(folder.resolve()):
        return None
    try:
        if not any(p != pending.installer for p in folder.iterdir()):
            return None
    except OSError:
        return None
    return folder, dir_size(folder)


def adopt_portable(pending: PendingInstall, whole_folder: bool = False) -> Path:
    """For a program that needs no installing: copy it into the prefix and use it from there —
    just the .exe, or (whole_folder, when portable_folder offers it) its folder with it."""
    folder = re.sub(r'[\\/:*?"<>|]+', " ", pending.name).strip() or "Program"
    dest_dir = pending.pfx / "drive_c/Program Files" / folder
    src = pending.installer
    offer = portable_folder(pending) if whole_folder else None
    if offer is not None:
        _src_dir, need = offer
        if need > free_space(pending.pfx):
            raise InstallError(f"Not enough free space to copy {src.parent.name} ({human_size(need)}).")
        shutil.copytree(src.parent, dest_dir, symlinks=True, dirs_exist_ok=True)
        return dest_dir / src.name
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_dir / src.name)
    return dest_dir / src.name


def best_name(pending: PendingInstall, chosen: Candidate | None) -> str:
    """Prefer a clean Start Menu/Desktop shortcut name over the one guessed from the file name."""
    if chosen and chosen.shortcut_name and len(chosen.shortcut_name) > 2:
        return chosen.shortcut_name
    return pending.name


def is_confident(cands: list[Candidate], installer: Path | None = None) -> bool:
    """True when the top guess is safe to add to Steam without asking.

    Never true for anything that looks like a setup/uninstall tool or the installer itself.
    """
    return bool(cands) and cands[0].score > -100 and not looks_like_installer(cands[0].exe, installer)


def launch(app: App) -> subprocess.Popen:
    return subprocess.Popen([app.launcher], env=clean_env(), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def program_dirs(exe: Path, new_dirs: Iterable[Path], home: Path | None = None) -> list[Path]:
    """The folder a program was installed into outside its prefix (on D:), for uninstalling.

    Only a new folder that holds the program counts. If the installer created a general-purpose
    folder such as ~/Games, the program's own subfolder (~/Games/Cool Game) is used instead.
    """
    home = (home or Path.home()).resolve()
    out = []
    for d in new_dirs:
        top = d.resolve()
        if not exe.is_relative_to(top):
            continue
        if top.parent == home and top.name.lower() in _PROTECTED_HOME_DIRS:
            rel = exe.relative_to(top).parts
            if len(rel) < 2:
                continue  # the program sits directly in ~/Games: no folder of its own to remove
            top = top / rel[0]
        out.append(top)
    if not out:
        # The program's own folder already existed (say, left by an earlier attempt), so only its
        # subfolders are new. If the installer filled the folder the program sits in, that folder
        # is the program's — unless it's a shared one (home, ~/Games, Downloads…).
        folder = exe.parent.resolve()
        filled = any(d.resolve().parent == folder for d in new_dirs)
        shared = (folder == home or not folder.is_relative_to(home)
                  or (folder.parent == home and folder.name.lower() in _PROTECTED_HOME_DIRS | _GENERIC_FOLDERS))
        if filled and not shared:
            out.append(folder)
    return out


def guess_program_dir(app: App, home: Path | None = None) -> Path | None:
    """For a program recorded before 4.1.1 without its folder on D: (its folder already existed when it
    was installed): the folder right under home (or under ~/Games and the like) that holds its .exe.
    Never for a program recorded since: no folder then means none is its own (say, an .exe the user picked
    from a folder they already had)."""
    home = (home or Path.home()).resolve()
    exe = Path(app.exe).resolve()
    if (app.kind != "program" or app.extra_dirs or app.dirs_known or not app.prefix
            or exe.is_relative_to(Path(app.prefix).resolve())):
        return None
    try:
        rel = exe.relative_to(home).parts
    except ValueError:
        return None
    if rel and rel[0].startswith("."):  # ~/.local and the like: never a program's folder
        return None
    if rel and rel[0].lower() in _PROTECTED_HOME_DIRS | _GENERIC_FOLDERS:
        folder = home / rel[0] / rel[1] if len(rel) >= 3 else None
    else:
        folder = home / rel[0] if len(rel) >= 2 else None
    return folder if folder is not None and folder.is_dir() else None


_PROTECTED_HOME_DIRS = {"downloads", "desktop", "documents", "music", "pictures", "videos", "games",
                        ".local", ".steam", ".config", ".var", "steam"}


def safe_extra_dirs(app: App, home: Path | None = None) -> list[Path]:
    """The program folders outside its prefix that uninstall may delete: existing, inside the home
    folder, and never the home folder itself, a standard folder like Downloads, anything in a hidden
    folder (~/.local…: installs never go there) or a folder holding the prefix."""
    home = (home or Path.home()).resolve()
    prefix = Path(app.prefix).resolve() if app.prefix else None
    out = []
    for d in app.extra_dirs:
        p = Path(d)
        try:
            r = p.resolve()
        except OSError:
            continue
        if (r.is_dir() and r.is_relative_to(home) and r != home
                and not (r.parent == home and r.name.lower() in _PROTECTED_HOME_DIRS)
                and not r.relative_to(home).parts[0].startswith(".")
                and not (prefix is not None and prefix.is_relative_to(r))):
            out.append(r)
    return out


def app_paths(app: App) -> list[Path]:
    """Everything on disk that belongs to an installed program."""
    if not app.prefix:  # a streaming service: nothing of its own on disk
        return []
    return [p for p in [Path(app.prefix), *safe_extra_dirs(app)] if p.exists()]


def dir_size(path: Path, limit: int = 500_000) -> int:
    total, n = 0, 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            n += 1
            if n > limit:
                return total
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


INSTALL_INFO = "deckhand-install.json"


def save_install_info(compat: Path, name: str, installer: Path, runtime: Runtime) -> None:
    """What's needed to finish this install later, if Deckhand is closed before it's done."""
    info = {"name": name, "installer": str(installer), "started": time.time(),
            "runtime": [runtime.name, runtime.kind, runtime.path]}
    try:
        (compat / INSTALL_INFO).write_text(json.dumps(info), encoding="utf-8")
    except OSError:
        pass


def load_install_info(compat: Path) -> dict:
    try:
        info = json.loads((compat / INSTALL_INFO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def prefix_in_use(compat: Path) -> bool:
    """Is anything still running in this prefix (e.g. an installer that outlived Deckhand)?"""
    keys = {f"WINEPREFIX={compat / 'pfx'}".encode(), f"STEAM_COMPAT_DATA_PATH={compat}".encode()}
    me = os.getpid()
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        if pid == me:
            continue
        try:
            env = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            continue
        if any(kv in keys for kv in env.split(b"\0")):
            return True
    return False


def resume_install(paths: Paths, compat: Path, roots: Iterable[Path] | None = None) -> PendingInstall | None:
    """Pick up an install that was interrupted after its installer ran: find what it installed.
    None if there's nothing in it to add."""
    pfx = compat / "pfx"
    if not (pfx / "drive_c").is_dir():
        return None
    info = load_install_info(compat)
    name = str(info.get("name") or compat.name.replace("-", " ").title())
    installer = Path(str(info.get("installer") or compat.name))
    rt = info.get("runtime")
    runtime = Runtime(*rt) if isinstance(rt, list) and len(rt) == 3 and Path(rt[2]).exists() else None
    if runtime is None:
        runtimes = find_runtimes(roots)
        if not runtimes:
            return None
        runtime = runtimes[0]
    if (pfx / "dosdevices" / "z:").is_symlink():
        restore_system_drive(pfx, "/")  # it was pointed at the home folder while installing
    cands = find_program(pfx, name, installer if installer.is_file() else None, [])
    if not cands:
        return None
    return PendingInstall(compat.name, name, installer, compat, runtime, cands, paths.logs / f"{compat.name}.log")


def orphan_prefixes(paths: Paths) -> list[Path]:
    """Prefixes no installed program uses: left by installs that were interrupted (Deckhand
    closed or killed mid-install, the Deck turned off…)."""
    used = {Path(a.prefix).resolve() for a in Library(paths).load() if a.prefix}
    try:
        dirs = sorted(d for d in paths.prefixes.iterdir() if d.is_dir() and not d.is_symlink())
    except OSError:
        return []
    return [d for d in dirs if d.resolve() not in used]


def directx_log(pfx: Path, lines: int = 25) -> str:
    """The end of the DirectX setup's own logs (DXError.log, DirectX.log in C:\\Windows), if any."""
    out = []
    names = ("dxerror.log", "directx.log")
    logs: list[Path] = []
    windows = pfx / "drive_c" / "windows"
    for d in (windows, windows / "Logs", windows / "logs", windows / "temp"):
        try:
            logs += [f for f in d.iterdir() if f.name.lower() in names and f not in logs]
        except OSError:
            pass
    users = pfx / "drive_c" / "users"
    for dirpath, dirnames, files in os.walk(users) if users.is_dir() else ():
        if dirpath.count(os.sep) - str(users).count(os.sep) >= 5:
            dirnames.clear()  # temp folders are shallow; don't crawl the whole profile
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        logs += [Path(dirpath) / f for f in files if f.lower() in names]
    seen: set[str] = set()
    for f in sorted(set(logs)):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text[:200]:  # UTF-16
            text = f.read_bytes().decode("utf-16", errors="replace")
        if text in seen:
            continue  # the same log in two places (DirectX setup writes to C:\\windows and its Logs folder)
        seen.add(text)
        tail = [ln.rstrip() for ln in text.splitlines() if ln.strip()][-lines:]
        out.append(f"--- {f.name} (last {len(tail)} lines) ---\n" + "\n".join(tail))
    return "\n".join(out)


def app_size(app: App) -> int:
    return sum(dir_size(p) for p in app_paths(app))


def _user_shortcuts(cfg: Path) -> list[tuple[Path, dict]]:
    try:
        sc = vdf_loads((cfg / "shortcuts.vdf").read_bytes()).get("shortcuts", {})
    except (OSError, ValueError):
        return []
    return [(cfg, e) for e in (sc.values() if isinstance(sc, dict) else []) if isinstance(e, dict)]


def steam_shortcuts(roots: Iterable[Path] | None = None) -> list[tuple[Path, dict]]:
    """Every (user config folder, shortcut) in Steam's saved shortcut lists."""
    return [item for cfg in steam_user_config_dirs(roots) for item in _user_shortcuts(cfg)]


def _unquote(v: object) -> str:
    return str(v).strip().strip('"')


def _runs(e: dict, launcher: str) -> bool:
    return bool(launcher) and launcher in str(e.get("Exe", ""))


def find_shortcut(launcher: str, roots: Iterable[Path] | None = None, appid: int = 0,
                  entries: list[tuple[Path, dict]] | None = None) -> int:
    """The appid of the Steam shortcut that runs `launcher` (or has `appid`), or 0 if there is none."""
    for _cfg, e in steam_shortcuts(roots) if entries is None else entries:
        aid = _entry_appid(e)
        if (appid and aid == appid) or _runs(e, launcher):
            return aid
    return 0


def in_steam(app: App, roots: Iterable[Path] | None = None) -> bool:
    """Is the program's shortcut in any Steam user's saved library?"""
    return bool(find_shortcut(app.launcher, roots, app.steam_appid))


def shortcuts_saved_at(roots: Iterable[Path] | None = None) -> float:
    """When Steam last saved a shortcut list (0 if it never has)."""
    times = [0.0]
    for cfg in steam_user_config_dirs(roots):
        try:
            times.append((cfg / "shortcuts.vdf").stat().st_mtime)
        except OSError:
            pass
    return max(times)


def steam_state(app: App, roots: Iterable[Path] | None = None, running: bool | None = None,
                entries: list[tuple[Path, dict]] | None = None) -> str:
    """"in": Steam's saved list has it. "sent": it was handed to the running Steam, which hasn't
    saved its list since, so we can't see it yet (sending it again would make a duplicate).
    "out": not in Steam."""
    if find_shortcut(app.launcher, roots, app.steam_appid, entries):
        return "in"
    if app.steam_added == "requested" and app.steam_requested_at:
        if running is None:
            running = steam_is_running()
        if running and shortcuts_saved_at(roots) < app.steam_requested_at:
            return "sent"
    return "out"


def sync_steam_appid(app: App, entries: list[tuple[Path, dict]]) -> bool:
    """Steam may give a shortcut its own id: adopt the id of the shortcut that runs our launcher
    (artwork is stored under it). True if the app changed."""
    ids = [_entry_appid(e) for _cfg, e in entries if _runs(e, app.launcher)]
    if not ids:
        return False
    changed = False
    if app.steam_appid not in ids:
        app.steam_appid, changed = ids[0], True
    if app.steam_added == "requested":  # Steam has saved it since: it arrived
        app.steam_added, app.steam_requested_at, changed = "live", 0.0, True
    return changed


# ── Adding to Steam ──────────────────────────────────────────────────────────
#
# Steam keeps its shortcuts in memory and writes shortcuts.vdf itself, so the file must never be
# edited while Steam runs: the edit is either lost when Steam saves, or merged into its own list as
# duplicates. While Steam is running we ask Steam itself to add the program
# (steam://addnonsteamgame/<.desktop file>, what SteamOS's own "Add to Steam" does) — once — and
# remember that we did. The file is only edited while Steam is closed.

def desktop_entry_path(app: App) -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "applications" / f"deckhand-{app.id}.desktop"


def write_desktop_file(path: Path, name: str, exe: str, workdir: str, icon: str,
                       comment: str = "Windows program installed with Deckhand") -> Path:
    def esc(v: str) -> str:
        return v.replace("\\", "\\\\").replace("\n", " ")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={esc(' '.join(name.split()))}\n"
        f"Comment={comment}\n"
        f'Exec="{exe.replace(chr(34), chr(92) + chr(34))}"\n'
        f"Path={esc(workdir)}\n"
        f"Icon={esc(icon or 'applications-games')}\n"
        "Terminal=false\n"
        "Categories=Game;\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def write_desktop_entry(app: App) -> Path:
    """A menu entry (Desktop Mode app menu → Games). Also what Steam's add-a-game handoff reads."""
    return write_desktop_file(desktop_entry_path(app), app.name, app.launcher, str(Path(app.exe).parent), app.icon)


def remove_desktop_entry(app: App) -> None:
    try:
        desktop_entry_path(app).unlink()
    except OSError:
        pass


def request_steam_add(desktop_file: Path) -> bool:
    """Hand a .desktop file to the running Steam client. True if the request was delivered."""
    import urllib.parse

    tmp = Path("/tmp") / desktop_file.name
    try:
        shutil.copy2(desktop_file, tmp)
        Path("/tmp/addnonsteamgamefile").touch()  # SteamOS's add-to-steam sets this flag too
    except OSError:
        tmp = desktop_file
    url = "steam://addnonsteamgame/" + urllib.parse.quote(str(tmp), safe="")
    for cmd in (["steam", url], ["xdg-open", url]):
        exe = shutil.which(cmd[0])
        if not exe:
            continue
        try:
            proc = subprocess.Popen([exe, *cmd[1:]], env=clean_env(), start_new_session=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        except OSError:
            continue
        try:
            proc.wait(timeout=10)  # `steam <url>` just hands the URL to the running client and exits
        except subprocess.TimeoutExpired:
            pass
        return True
    return False


STEAM_ADD_WAIT = 12.0  # seconds to watch for Steam saving a shortcut we handed it


def add_shortcut(name: str, exe: str, start_dir: str, icon: str = "", desktop_file: Path | None = None,
                 roots: Iterable[Path] | None = None, running: bool | None = None,
                 wait: float | None = None) -> tuple[int, str]:
    """Add a non-Steam shortcut, without ever making a duplicate. Returns (appid, how):
    "live"         Steam has it (it was already there, or the running Steam added it and saved),
    "requested"    handed to the running Steam, which hasn't saved its list yet (appid is a guess),
    "file"         Steam is closed, so its shortcut file was edited (Steam loads it on start),
    "unavailable"  Steam is running but couldn't be reached; nothing was changed,
    ""             there's no Steam account."""
    roots = list(steam_roots() if roots is None else roots)
    existing = find_shortcut(exe, roots) if exe else 0
    if existing:
        return existing, "live"  # never a second copy
    if running is None:
        running = steam_is_running()
    if running:
        if not exe:
            return 0, ""
        if desktop_file is None or not desktop_file.exists():
            desktop_file = write_desktop_file(Path("/tmp") / f"deckhand-{slugify(name)}.desktop",
                                              name, exe, start_dir, icon)
        if not request_steam_add(desktop_file):
            return 0, "unavailable"
        deadline = time.monotonic() + (STEAM_ADD_WAIT if wait is None else wait)
        while time.monotonic() < deadline:
            appid = find_shortcut(exe, roots)
            if appid:
                return appid, "live"
            time.sleep(0.3)
        return shortcut_appid(f'"{exe}"', name), "requested"
    appid, users = add_steam_shortcut(name, exe, start_dir, icon=icon, roots=roots)
    return (appid, "file") if users else (0, "")


def add_to_steam(app: App, roots: Iterable[Path] | None = None, running: bool | None = None,
                 wait: float | None = None) -> tuple[int, str]:
    """Add an installed program to Steam (see add_shortcut) and record the result on `app`."""
    entry = desktop_entry_path(app)
    if app.launcher and not entry.exists():
        entry = write_desktop_entry(app)
    sent_at = time.time()
    appid, how = add_shortcut(app.name, app.launcher, str(Path(app.exe).parent), app.icon, entry, roots,
                              running, wait)
    if how != "unavailable":  # (then nothing changed in Steam)
        app.steam_appid = appid
        app.steam_requested_at = sent_at if how == "requested" else 0.0
    app.steam_added = how
    return appid, how


def remove_shortcuts_for(launcher: str, appid: int, roots: Iterable[Path] | None = None) -> None:
    """Remove the shortcut(s) that run `launcher` or have `appid` — only while Steam is closed."""
    for cfg in steam_user_config_dirs(roots):
        if not (cfg / "shortcuts.vdf").exists():
            continue
        try:
            _edit_shortcuts(cfg, lambda es: [e for e in es if not (
                (appid and _entry_appid(e) == appid) or _runs(e, launcher))])
        except (OSError, ValueError, TypeError):
            continue


def uninstall(app: App, paths: Paths, roots: Iterable[Path] | None = None, running: bool | None = None) -> bool:
    """Remove the program's files (prefix, plus its folder on D: if it was installed there),
    its Steam shortcut, icon, artwork, launcher and log. Returns True if its Steam shortcut was
    left for the user to remove in Steam (Steam is running, and would undo an edit)."""
    left_in_steam = False
    if app.steam_appid or app.launcher:
        if running is None:
            running = steam_is_running()
        if running:
            left_in_steam = bool(find_shortcut(app.launcher, roots, app.steam_appid)) or app.steam_added == "requested"
        else:
            remove_shortcuts_for(app.launcher, app.steam_appid, roots)
    remove_desktop_entry(app)
    for d in safe_extra_dirs(app):
        shutil.rmtree(d, ignore_errors=True)
    if app.prefix:
        shutil.rmtree(app.prefix, ignore_errors=True)
    files = [Path(app.launcher), paths.logs / f"{app.id}.log", paths.logs / f"{app.id}-launch.log",
             *map(Path, app.artwork)]
    if app.icon:
        files.append(Path(app.icon))
    for f in files:
        try:
            f.unlink()
        except OSError:
            pass
    Library(paths).remove(app.id)
    return left_in_steam


# ── Duplicate shortcuts ──────────────────────────────────────────────────────


def _duplicate_key(e: dict, launchers: Path | None) -> tuple:
    exe = _unquote(e.get("Exe", ""))
    if launchers is not None and exe and Path(exe).parent == launchers:
        return ("launcher", exe)  # one Deckhand program: one shortcut, whatever it's called
    return ("same", exe, _unquote(e.get("StartDir", "")), str(e.get("LaunchOptions", "")),
            str(e.get("AppName", e.get("appname", ""))))


def _duplicates(entries: list[dict], launchers: Path | None, keep: set[int] = frozenset()) -> set[int]:
    """Indexes of repeated shortcuts. The first of each is kept, unless a later one has an id in `keep`."""
    chosen: dict[tuple, int] = {}
    for i, e in enumerate(entries):
        k = _duplicate_key(e, launchers)
        if k not in chosen or (_entry_appid(e) in keep and _entry_appid(entries[chosen[k]]) not in keep):
            chosen[k] = i
    return set(range(len(entries))) - set(chosen.values())


def find_duplicate_shortcuts(roots: Iterable[Path] | None = None, launchers: Path | None = None) -> list[str]:
    """The names of the extra copies of shortcuts in Steam's saved lists (one name per extra copy)."""
    out = []
    for cfg in steam_user_config_dirs(roots):
        entries = [e for _c, e in _user_shortcuts(cfg)]
        out += [str(entries[i].get("AppName", entries[i].get("appname", "?")))
                for i in sorted(_duplicates(entries, launchers))]
    return out


def remove_duplicate_shortcuts(roots: Iterable[Path] | None = None, launchers: Path | None = None,
                               keep: Iterable[int] = ()) -> int:
    """Remove the extra copies (only while Steam is closed), keeping a backup of each list first.
    `keep`: shortcut ids to prefer when choosing which copy stays. Returns how many were removed."""
    keep = {int(k) & 0xFFFFFFFF for k in keep if k}
    removed = 0

    def dedupe(entries: list[dict]) -> list[dict]:
        drop = _duplicates(entries, launchers, keep)
        return [e for i, e in enumerate(entries) if i not in drop]

    for cfg in steam_user_config_dirs(roots):
        f = cfg / "shortcuts.vdf"
        extra = len(_duplicates([e for _c, e in _user_shortcuts(cfg)], launchers, keep))
        if not extra:
            continue
        try:
            shutil.copy2(f, f.with_suffix(".vdf.before-dedupe"))
            _edit_shortcuts(cfg, dedupe)
        except (OSError, ValueError, TypeError):
            continue
        removed += extra
    return removed


def steam_grid_dirs(roots: Iterable[Path] | None = None) -> list[Path]:
    """Where Steam looks for custom library artwork, one per Steam user."""
    return [cfg / "grid" for cfg in steam_user_config_dirs(roots)]


def in_game_mode() -> bool:
    env = os.environ
    return (env.get("SteamGamepadUI") == "1" or env.get("XDG_CURRENT_DESKTOP", "").lower() == "gamescope"
            or "GAMESCOPE_WAYLAND_DISPLAY" in env)


def removable_media(mounts_text: str | None = None) -> list[Path]:
    """Mounted SD cards and USB drives."""
    if mounts_text is None:
        try:
            mounts_text = Path("/proc/mounts").read_text()
        except OSError:
            return []
    out: list[Path] = []
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        mp = re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1), 8)), parts[1])
        if mp.startswith(("/run/media/", "/media/")) and Path(mp) not in out:
            out.append(Path(mp))
    return out


# ── Finding installers the user downloaded ──────────────────────────────────


@dataclass
class FoundInstaller:
    path: Path
    size: int
    mtime: float


_NOT_INSTALLERS = ("unins", "vc_redist", "vcredist", "dxsetup", "dotnet", "crashhandler", "crashpad")
_SKIP_SEARCH_DIRS = {"steamapps", "compatdata", "shadercache", "windows", "drive_c", "pfx", "node_modules"}


def default_installer_dirs() -> list[Path]:
    home = Path.home()
    return [home / "Downloads", home / "Desktop", *removable_media()]


def installer_files(installer: Path, siblings: Iterable[Path] | None = None) -> list[Path]:
    """The setup file plus its data parts (GOG-style 'setup_x-1.bin', 'setup_x-2.bin', …).
    `siblings`: the folder's files, when the caller already listed them (saves a listing per file)."""
    installer = Path(installer)
    files = [installer] if installer.exists() else []
    pat = re.compile(re.escape(installer.stem) + r"(-\d+)?\.bin", re.I)
    try:
        others = installer.parent.iterdir() if siblings is None else siblings
        files += sorted(p for p in others if p != installer and pat.fullmatch(p.name))
    except OSError:
        pass
    return files


def files_size(files: Iterable[Path]) -> int:
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def delete_files(files: Iterable[Path]) -> int:
    """Delete files; returns bytes freed."""
    freed = 0
    for f in files:
        try:
            size = f.stat().st_size
            f.unlink()
            freed += size
        except OSError:
            pass
    return freed


def find_installers(
    dirs: Iterable[Path] | None = None,
    exclude: Iterable[str] = (),
    depth: int = 2,
    limit: int = 40,
) -> list[FoundInstaller]:
    """Windows installers in Downloads, Desktop and removable drives, newest first."""
    skip = {str(Path(e)) for e in exclude}
    found: dict[Path, FoundInstaller] = {}
    for base in default_installer_dirs() if dirs is None else dirs:
        base = Path(base)
        if not base.is_dir():
            continue
        seen = 0
        for dirpath, dirnames, filenames in os.walk(base):
            seen += 1
            if seen > 3000:
                break
            level = len(Path(dirpath).relative_to(base).parts)
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in _SKIP_SEARCH_DIRS
                           ] if level < depth else []
            for f in filenames:
                low = f.lower()
                if not low.endswith(INSTALLER_SUFFIXES) or any(w in low for w in _NOT_INSTALLERS):
                    continue
                p = Path(dirpath) / f
                if str(p) in skip or p in found:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                parts = [Path(dirpath) / n for n in filenames if n.lower().endswith(".bin")]
                found[p] = FoundInstaller(p, files_size(installer_files(p, parts)), st.st_mtime)
    return sorted(found.values(), key=lambda i: i.mtime, reverse=True)[:limit]


# ── Icons inside Windows .exe files ─────────────────────────────────────────

_RT_ICON, _RT_GROUP_ICON = 3, 14


def exe_icon_data(exe: Path) -> bytes | None:
    """The largest icon embedded in a Windows .exe, as PNG or .ico file bytes (Qt reads both)."""
    try:
        with open(exe, "rb") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    except (OSError, ValueError):
        return None
    try:
        return _pe_icon(mm)
    except (struct.error, IndexError, ValueError, KeyError):
        return None
    finally:
        mm.close()


def _pe_icon(d) -> bytes | None:
    if d[:2] != b"MZ":
        return None
    (pe,) = struct.unpack_from("<I", d, 0x3C)
    if d[pe:pe + 4] != b"PE\0\0":
        return None
    coff = pe + 4
    (nsec,) = struct.unpack_from("<H", d, coff + 2)
    (optsize,) = struct.unpack_from("<H", d, coff + 16)
    opt = coff + 20
    (magic,) = struct.unpack_from("<H", d, opt)
    if magic not in (0x10B, 0x20B):
        return None
    ddir = opt + (96 if magic == 0x10B else 112)
    rsrc_rva, _rsrc_size = struct.unpack_from("<II", d, ddir + 2 * 8)
    if not rsrc_rva:
        return None
    sections = []
    for i in range(nsec):
        vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", d, opt + optsize + i * 40 + 8)
        sections.append((va, max(vsize, rawsize), rawptr))

    def off(rva: int) -> int:
        for va, size, raw in sections:
            if va <= rva < va + size:
                return rva - va + raw
        raise ValueError("RVA outside sections")

    base = off(rsrc_rva)

    def entries(dir_off: int) -> list[tuple[int, int]]:
        named, ids = struct.unpack_from("<HH", d, base + dir_off + 12)
        return [struct.unpack_from("<II", d, base + dir_off + 16 + i * 8) for i in range(min(named + ids, 4096))]

    def leaf(target: int) -> bytes | None:
        for _ in range(8):
            if not target & 0x80000000:
                break
            sub = entries(target & 0x7FFFFFFF)
            if not sub:
                return None
            target = sub[0][1]
        rva, size = struct.unpack_from("<II", d, base + target)
        o = off(rva)
        return bytes(d[o:o + size])

    icons: dict[int, int] = {}
    groups: list[int] = []
    for type_id, target in entries(0):
        if not target & 0x80000000:
            continue
        if type_id == _RT_ICON:
            icons.update((n, t) for n, t in entries(target & 0x7FFFFFFF) if not n & 0x80000000)
        elif type_id == _RT_GROUP_ICON:
            groups = [t for _n, t in entries(target & 0x7FFFFFFF)]
    if not groups or not icons:
        return None
    grp = leaf(groups[0])
    if not grp:
        return None
    (count,) = struct.unpack_from("<H", grp, 4)
    best = None
    for i in range(count):
        w, h, colors, _res, planes, bits, _size, icon_id = struct.unpack_from("<BBBBHHIH", grp, 6 + i * 14)
        key = (w or 256, bits)
        if icon_id in icons and (best is None or key > best[0]):
            best = (key, (w, h, colors, planes, bits, icon_id))
    if best is None:
        return None
    w, h, colors, planes, bits, icon_id = best[1]
    data = leaf(icons[icon_id])
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    return struct.pack("<HHHBBBBHHII", 0, 1, 1, w, h, colors, 0, planes, bits, len(data), 22) + data


def steam_is_running() -> bool:
    """Is the Steam client running? Reads /proc directly (and asks pgrep too): guessing "no" while
    Steam runs would mean editing its shortcut list behind its back."""
    if in_game_mode():
        return True  # Game Mode is Steam
    uid = os.getuid()
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        try:
            if Path(f"/proc/{pid}/comm").read_text().strip() == "steam" and os.stat(f"/proc/{pid}").st_uid == uid:
                return True
        except OSError:
            continue
    try:
        return subprocess.run(["pgrep", "-x", "-u", str(uid), "steam"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False
