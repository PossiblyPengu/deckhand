"""Better xCloud (github.com/redphx/better-xcloud), kept current.

It is a userscript for Xbox Cloud Gaming. Deckhand wraps it in a tiny browser extension (it needs nothing from a
userscript manager) that Chromium loads with --load-extension. Keeping it current works like this:

* The Xbox launcher asks for an update *before* the browser starts, so the launch that follows already runs the newest
  version. It asks at most every six hours and never waits long, and Deckhand does the same when it opens.
* What is downloaded is checked first: it must be Better xCloud, it must carry a version, it must be newer than what
  is installed (never a downgrade) and it may only ask to run on xbox.com. Anything else is refused and what is
  installed stays.
* The extension's permissions are rebuilt from the new script's own header every time, so they can't go stale.
* The previous version is kept (``previous/``) in case a release turns out to be bad.
* What happened is recorded (``state.json``): the version, when it was checked, and why a check failed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

URL = "https://github.com/redphx/better-xcloud/releases/latest/download/better-xcloud.user.js"
CHECK_EVERY = 6 * 3600  # seconds between looks for a newer version
RETRY_AFTER = 1800  # after a failed look (offline, GitHub down): try again in half an hour
MAX_BYTES = 20 << 20
SCRIPT, MANIFEST, STATE, STAMP, PREVIOUS = "better-xcloud.user.js", "manifest.json", "state.json", ".checked", "previous"

# Match patterns that stay on xbox.com: https://www.xbox.com/*/play*, *://*.xbox.com/*, https://xbox.com/…
_XBOX = re.compile(r"^(?:\*|https?)://(?:\*\.|[a-z0-9-]+\.)*xbox\.com/", re.I)


@dataclass
class Result:
    status: str  # "installed", "updated", "current", "skipped" (looked recently) or "failed"
    version: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "failed"


def parse_header(text: str) -> dict:
    """The userscript header's facts, or ValueError saying why this isn't a Better xCloud we may install."""
    head = text.split("==/UserScript==", 1)[0]
    if "==UserScript==" not in head:
        raise ValueError("it isn't a userscript")
    name = re.search(r"@name\s+(.+)", head)
    if not name or "better xcloud" not in name.group(1).lower():
        raise ValueError("it isn't Better xCloud")
    version = re.search(r"@version\s+([\d.]+)", head)
    if not version:
        raise ValueError("it has no version")
    matches = re.findall(r"@match\s+(\S+)", head) or ["https://www.xbox.com/*/play*"]
    for pattern in matches:
        if not _XBOX.match(pattern):
            raise ValueError(f"it now asks to run on {pattern}, which isn't xbox.com")
    return {"version": version.group(1), "matches": matches, "excludes": re.findall(r"@exclude\s+(\S+)", head)}


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version))


def read_state(dest: Path) -> dict:
    try:
        data = json.loads((dest / STATE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def installed_version(dest: Path) -> str:
    """The installed version ('' if not installed)."""
    if not (dest / SCRIPT).is_file() or not (dest / MANIFEST).is_file():
        return ""
    return str(read_state(dest).get("version") or "")


def _write_state(dest: Path, state: dict) -> None:
    tmp = dest / (STATE + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, dest / STATE)


def _stamp(dest: Path, when: float) -> None:
    """The launcher only asks again once this file is old enough, so it never starts Deckhand needlessly."""
    s = dest / STAMP
    s.touch()
    os.utime(s, (when, when))


def manifest_for(header: dict) -> dict:
    version = ".".join(header["version"].split(".")[:4])
    return {
        "manifest_version": 3, "name": "Better xCloud (added by Deckhand)", "version": version,
        "description": "github.com/redphx/better-xcloud",
        "content_scripts": [{"matches": header["matches"], "exclude_matches": header["excludes"],
                             "js": [SCRIPT], "run_at": "document_start", "world": "MAIN"}],
    }


def update(dest: Path, fetch: Callable[[str], bytes] | None = None, force: bool = False,
           now: Callable[[], float] = time.time) -> Result:
    """Install Better xCloud into `dest`, or update it to the newest release. Never leaves it half-written."""
    state = read_state(dest)
    current = installed_version(dest)
    t = now()
    if current and not force and t - float(state.get("checked_at") or 0) < CHECK_EVERY:
        return Result("skipped", current, f"Better xCloud {current} was checked recently.")
    dest.mkdir(parents=True, exist_ok=True)

    def failed(why: str) -> Result:
        state.update(error=why, error_at=t)
        _write_state(dest, state)
        _stamp(dest, t - CHECK_EVERY + RETRY_AFTER)  # try again soon, not in six hours
        tail = f" Staying on {current}." if current else ""
        return Result("failed", current, f"Couldn't get Better xCloud: {why}.{tail}")

    try:
        if fetch is None:
            from . import updater

            fetch = lambda url: updater.fetch(url, timeout=15, limit=MAX_BYTES + 1)  # noqa: E731
        raw = fetch(URL)
    except Exception as e:  # noqa: BLE001 - offline, DNS, HTTP errors: all just "couldn't check"
        return failed(f"couldn't reach GitHub ({e})")
    if not raw or len(raw) > MAX_BYTES:
        return failed("the download had an unexpected size")
    text = raw.decode("utf-8", errors="replace")
    try:
        header = parse_header(text)
    except ValueError as e:
        return failed(f"the download was refused: {e}")

    new = header["version"]
    if current and version_key(new) <= version_key(current):
        state.update(checked_at=t)
        state.pop("error", None)
        _write_state(dest, state)
        _stamp(dest, t)
        return Result("current", current, f"Better xCloud {current} is the newest.")

    # Keep what was working, then swap the new one in as a pair.
    if (dest / SCRIPT).is_file():
        (dest / PREVIOUS).mkdir(exist_ok=True)
        shutil.copy2(dest / SCRIPT, dest / PREVIOUS / SCRIPT)
        if (dest / MANIFEST).is_file():
            shutil.copy2(dest / MANIFEST, dest / PREVIOUS / MANIFEST)
    (dest / (SCRIPT + ".tmp")).write_text(text, encoding="utf-8")
    (dest / (MANIFEST + ".tmp")).write_text(json.dumps(manifest_for(header), indent=2), encoding="utf-8")
    os.replace(dest / (SCRIPT + ".tmp"), dest / SCRIPT)
    os.replace(dest / (MANIFEST + ".tmp"), dest / MANIFEST)
    state.update(version=new, checked_at=t, updated_at=t, sha256=hashlib.sha256(raw).hexdigest())
    state.pop("error", None)
    _write_state(dest, state)
    _stamp(dest, t)
    if current:
        return Result("updated", new, f"Better xCloud updated from {current} to {new}.")
    return Result("installed", new, f"Better xCloud {new} installed.")


def summary(dest: Path, now: Callable[[], float] = time.time) -> str:
    """One short line for the screen: 'v1.2.3, checked 2 hours ago' (or why it couldn't be)."""
    version = installed_version(dest)
    if not version:
        return ""
    state = read_state(dest)
    checked = float(state.get("checked_at") or 0)
    age = now() - checked if checked else None
    if state.get("error") and (age is None or float(state.get("error_at") or 0) > checked):
        return f"v{version}, couldn't check for a newer one"
    if age is None:
        return f"v{version}"
    hours = int(age // 3600)
    when = "just now" if age < 3600 else f"{hours} hour{'s' * (hours != 1)} ago" if hours < 48 else f"{hours // 24} days ago"
    return f"v{version}, checked {when}"
