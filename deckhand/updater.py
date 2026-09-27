"""Self-update for the downloaded (single-file) Deckhand app.

Looks for a newer version in two places, like get.sh: the latest GitHub release, and the
prebuilt binary kept in the repo (bin/deckhand.json on main, then the development branch).
Downloads are verified against their SHA-256, swapped in atomically, and the app restarts
into the new file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = "PossiblyPengu/deckhand"
BRANCHES = ("main", "claude/steam-deck-windows-install-mkeivr")
ASSET = "deckhand-linux-x86_64"
MANIFEST = "deckhand.json"
API = f"https://api.github.com/repos/{REPO}"
RELEASE_API = f"{API}/releases/latest"
RAW = f"https://raw.githubusercontent.com/{REPO}"
USER_AGENT = "Deckhand-updater"


@dataclass
class Update:
    version: str
    url: str
    sha256: str
    notes: str = ""
    source: str = ""


class UpdateError(Exception):
    pass


class Cancelled(Exception):
    pass


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v.split("+")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def _ssl_context() -> ssl.SSLContext:
    """System CA certificates. A bundled OpenSSL may look in the build machine's paths, not SteamOS's."""
    ctx = ssl.create_default_context()
    for f in (os.environ.get("SSL_CERT_FILE", ""), "/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/cert.pem",
              "/etc/pki/tls/certs/ca-bundle.crt"):
        if f and os.path.isfile(f):
            try:
                ctx.load_verify_locations(cafile=f)
                break
            except (ssl.SSLError, OSError):
                continue
    return ctx


def _open(url: str, timeout: float, headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache",
                                               **(headers or {})})
    ctx = _ssl_context() if url.startswith("https:") else None
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.HTTPError as e:
        e.close()  # an HTTP error still holds the open connection
        raise UpdateError(f"HTTP {e.code} for {url.split('?')[0]}") from None


def fetch(url: str, timeout: float = 8.0, limit: int = 1 << 20, headers: dict[str, str] | None = None) -> bytes:
    with _open(url, timeout, headers) as r:
        return r.read(limit)


def _sha_from_text(text: str) -> str:
    m = re.match(r"\s*([0-9a-fA-F]{64})\b", text)
    if not m:
        raise UpdateError("bad checksum file")
    return m.group(1).lower()


def from_release(api_url: str = RELEASE_API) -> Update | None:
    """The latest GitHub release, if it carries the app and its .sha256."""
    data = json.loads(fetch(api_url))
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets", [])}
    if ASSET not in assets or f"{ASSET}.sha256" not in assets:
        return None
    sha = _sha_from_text(fetch(assets[f"{ASSET}.sha256"]).decode(errors="replace"))
    notes = (data.get("body") or "").strip()
    return Update(data.get("tag_name", "0").lstrip("v"), assets[ASSET], sha, notes[:600], "release")


def from_manifest(base_url: str) -> Update | None:
    """bin/deckhand.json next to a prebuilt binary: {"version", "sha256", "notes"?}."""
    # The query defeats raw.githubusercontent's ~5 minute cache (including cached 404s).
    data = json.loads(fetch(f"{base_url}/{MANIFEST}?t={int(time.time())}"))
    # Same for the binary: a cached older copy would fail the checksum right after a release.
    return Update(str(data["version"]), f"{base_url}/{ASSET}?t={int(time.time())}", str(data["sha256"]).lower(),
                  str(data.get("notes", ""))[:600], base_url)


def from_branch(branch: str, api: str = API, raw: str = RAW) -> Update | None:
    """bin/deckhand.json on a branch, read at the branch's current commit.

    raw.githubusercontent caches what a branch name points to for several minutes (query strings
    don't help), so a fresh release could show up stale or with a mismatched binary. Asking the
    API for the branch's commit and reading files by commit id is never stale. If the API is
    unavailable (e.g. rate-limited), the branch name is used as before.
    """
    base = f"{raw}/{branch}/bin"
    try:
        sha = fetch(f"{api}/commits/{branch}", headers={"Accept": "application/vnd.github.sha"}).decode().strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            base = f"{raw}/{sha}/bin"
    except Exception:  # noqa: BLE001 — fall back to the branch name
        pass
    return from_manifest(base)


def default_sources() -> list[Callable[[], Update | None]]:
    override = os.environ.get("DECKHAND_UPDATE_BASE")  # for testing: a folder URL with deckhand.json
    if override:
        return [lambda: from_manifest(override.rstrip("/"))]
    return [from_release] + [lambda b=b: from_branch(b) for b in BRANCHES]


def check(current: str, sources: list[Callable[[], Update | None]] | None = None,
          errors: list[Exception] | None = None) -> Update | None:
    """The newest update above `current`, or None. Unreachable or broken sources are skipped
    (and collected in `errors`, so callers can tell "up to date" from "couldn't check")."""
    best: Update | None = None
    for source in default_sources() if sources is None else sources:
        try:
            u = source()
        except Exception as e:  # noqa: BLE001 — offline, rate-limited, 404, bad JSON: try the next one
            if errors is not None:
                errors.append(e)
            continue
        if u and is_newer(u.version, current) and (best is None or is_newer(u.version, best.version)):
            best = u
    return best


def self_path() -> Path | None:
    """The running single-file app, if it can replace itself (not when running from source)."""
    if not getattr(sys, "frozen", False):
        return None
    p = Path(sys.executable).resolve()
    return p if os.access(p.parent, os.W_OK) else None


def download(update: Update, dest_dir: Path, progress: Callable[[int, int], None] = lambda done, total: None,
             cancelled: Callable[[], bool] = lambda: False, timeout: float = 30.0) -> Path:
    """Download next to the target (same filesystem, for an atomic swap) and verify the SHA-256."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f".{ASSET}.download"
    digest = hashlib.sha256()
    try:
        with _open(update.url, timeout) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancelled():
                    raise Cancelled()
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                progress(done, total)
        if digest.hexdigest() != update.sha256:
            raise UpdateError("The download was damaged (checksum mismatch). Try again.")
        tmp.chmod(0o755)
        return tmp
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def download_to(url: str, dest: Path, progress: Callable[[int, int], None] = lambda done, total: None,
                opener=None, digest=None, check: Callable[[bytes], None] | None = None) -> Path:
    """Download a file for Deckhand to use: via a temporary file next to `dest`, so `dest` only ever
    holds a whole download. `digest`: a hashlib object fed the data as it arrives. `check`: given the
    file's first bytes, raises if it isn't what was expected (then nothing is kept)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.download")
    try:
        with (opener or (lambda u: _open(u, 60)))(url) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 16):
                f.write(chunk)
                if digest is not None:
                    digest.update(chunk)
                done += len(chunk)
                progress(done, total)
        if check is not None:
            with open(tmp, "rb") as f:
                check(f.read(16))
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest


def install(downloaded: Path, target: Path) -> None:
    """Swap the new app in. The running copy keeps working until it restarts."""
    os.replace(downloaded, target)


def restart_env() -> dict[str, str]:
    """Environment for starting the new app as a fresh process, not as the old one's extraction."""
    from .core import clean_env

    env = {k: v for k, v in clean_env().items() if not k.startswith("_PYI_") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def restart(target: Path, args: list[str] | None = None) -> None:
    os.execve(str(target), [str(target), *(args or [])], restart_env())


def why_no_self_update() -> str:
    if not getattr(sys, "frozen", False):
        return "This copy runs from source — update it with git pull, or reinstall with get.sh."
    return (f"Deckhand can't replace its own file in {Path(sys.executable).resolve().parent}. "
            "Reinstall it with get.sh, which puts it in ~/.local/bin.")


def cli_update(current: str, out=print) -> int:
    """`deckhand --update` from a terminal."""
    target = self_path()
    if target is None:
        out(why_no_self_update())
        return 1
    out(f"Deckhand {current}: checking for updates…")
    errors: list[Exception] = []
    u = check(current, errors=errors)
    if u is None:
        if errors and len(errors) == len(default_sources()):
            out(f"Couldn't check for updates: {errors[0]}")
            return 1
        out("You're on the latest version.")
        return 0
    out(f"Downloading {u.version}…")
    try:
        install(download(u, target.parent), target)
    except (UpdateError, OSError) as e:
        out(f"Update failed: {e}")
        return 1
    out(f"Updated to {u.version}.")
    return 0
