"""GE-Proton (github.com/GloriousEggroll/proton-ge-custom): Proton with extra fixes.

Installed the way ProtonUp-Qt does it: the latest release's tarball, checked against the release's
.sha512sum, unpacked into Steam's compatibilitytools.d. Deckhand uses the newest GE-Proton for new
installs right away (core.runtime_rank); Steam lists it for games once it has restarted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import core, updater

RELEASES_API = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases/latest"


@dataclass(frozen=True)
class Release:
    version: str  # e.g. "GE-Proton10-25", also its folder name
    url: str  # the .tar.gz
    size: int
    sha512_url: str = ""


# Builds for other processors, by the names releases give them (GE-Proton 11 publishes x86_64 and aarch64 side by side).
OTHER_ARCHES = {"x86_64": ("aarch64", "arm64", "armhf", "arm"), "aarch64": ("x86_64", "amd64", "i386", "x86")}


def machine() -> str:
    import platform

    m = platform.machine().lower()
    return "aarch64" if m in ("aarch64", "arm64") else "x86_64"


def _foreign(name: str, arch: str | None = None) -> bool:
    """Is this a build for another processor than this one (or `arch`)?"""
    low = name.lower()
    return any(re.search(rf"[-_.]{re.escape(other)}(?:$|[-_.])", low) for other in OTHER_ARCHES[arch or machine()])


def _is_ge(name: str) -> bool:
    low = name.lower()
    return "ge-proton" in low or re.match(r"proton-.*-ge-\d", low) is not None


def installed(roots: Iterable[Path] | None = None) -> list[str]:
    """GE-Proton versions in Steam's compatibility tools folders that run here, newest first. (A build for another
    processor is left out: it can't start a game.)"""
    names = {r.name for r in core.find_runtimes(roots, include_system_wine=False) if _is_ge(r.name)
             and not _foreign(r.name)}
    return sorted(names, key=core._version_key, reverse=True)


def base_name(name: str) -> str:
    """A GE-Proton version without its processor ("GE-Proton11-12-x86_64" and "GE-Proton11-12" are the same one)."""
    return re.sub(r"[-_.](?:x86_64|amd64|aarch64|arm64)$", "", name, flags=re.I)


def has(version: str, have: Iterable[str]) -> bool:
    return base_name(version) in {base_name(h) for h in have}


def remove_foreign(roots: Iterable[Path] | None = None) -> list[str]:
    """Delete GE-Proton folders built for another processor (they can't run a game here). Returns their names."""
    gone = []
    for p in foreign_builds(roots):
        try:
            shutil.rmtree(p)
            gone.append(p.name)
        except OSError:
            pass
    return gone


def remove(name: str, roots: Iterable[Path] | None = None) -> bool:
    """Delete one GE-Proton version from Steam's compatibility tools (any build of it). True if one was there."""
    roots = list(core.steam_roots() if roots is None else roots)
    if not _is_ge(name) or "/" in name or name in (".", ".."):
        return False
    gone = False
    for root in roots:
        folder = root / "compatibilitytools.d"
        try:
            hits = [p for p in folder.iterdir() if p.is_dir() and _is_ge(p.name) and base_name(p.name) == base_name(name)]
        except OSError:
            continue
        for p in hits:
            if p.is_symlink():
                p.unlink()
            else:
                shutil.rmtree(p)
            gone = True
    return gone


def foreign_builds(roots: Iterable[Path] | None = None) -> list[Path]:
    """GE-Proton folders built for another processor (an older Deckhand installed the ARM build of GE-Proton 11)."""
    roots = list(core.steam_roots() if roots is None else roots)
    out = []
    for root in roots:
        folder = root / "compatibilitytools.d"
        try:
            out += [p for p in folder.iterdir() if p.is_dir() and _is_ge(p.name) and _foreign(p.name)]
        except OSError:
            pass
    return out


def latest(fetch: Callable[[str], bytes] | None = None, arch: str | None = None) -> Release:
    """The latest GE-Proton built for this processor, and its checksum file."""
    arch = arch or machine()
    data = json.loads((fetch or (lambda url: updater.fetch(url, timeout=30)))(RELEASES_API))
    assets = [a for a in data.get("assets", []) if isinstance(a, dict)]
    tars = [a for a in assets if str(a.get("name", "")).endswith(".tar.gz") and not _foreign(str(a["name"]), arch)]
    # (The one named for this processor first, then one named for none: releases before 11 had a single build.)
    tars.sort(key=lambda a: arch not in str(a["name"]).lower())
    tar = tars[0] if tars else None
    if tar is None:
        raise core.InstallError("Couldn't find GE-Proton's download for this Deck in its latest release. Try again "
                                "later.")
    stem = str(tar["name"])[:-len(".tar.gz")]
    sha = next((str(a.get("browser_download_url", "")) for a in assets
                if str(a.get("name", "")) == f"{stem}.sha512sum"), "")
    if not sha:  # (an older release names one checksum file for its one build)
        sha = next((str(a.get("browser_download_url", "")) for a in assets
                    if str(a.get("name", "")).endswith(".sha512sum") and not _foreign(str(a["name"]), arch)), "")
    return Release(stem, str(tar.get("browser_download_url", "")), int(tar.get("size") or 0), sha)


def install(release: Release, roots: Iterable[Path] | None = None,
            progress: Callable[[int, int], None] = lambda done, total: None,
            status: Callable[[str], None] = lambda s: None,
            fetch: Callable[[str], bytes] | None = None, opener=None) -> Path:
    """Download, check and unpack a release into the first Steam install's compatibilitytools.d."""
    roots = list(core.steam_roots() if roots is None else roots)
    if not roots:
        raise core.InstallError("Steam isn't installed, so there's nowhere to put GE-Proton.")
    tools = roots[0] / "compatibilitytools.d"
    target = tools / release.version
    if (target / "proton").is_file():
        return target
    need = release.size * 4 if release.size else 2 << 30  # the download, plus about three times that unpacked
    if core.free_space(tools) < need:
        raise core.InstallError(f"GE-Proton needs about {core.human_size(need)} of free space while it's installed.")
    if not release.sha512_url:  # (every GE-Proton release has one: without it the download can't be trusted)
        raise core.InstallError("GE-Proton's release has no checksum file, so nothing was downloaded. Try again later.")
    text = (fetch or (lambda url: updater.fetch(url, timeout=30)))(release.sha512_url).decode(errors="replace")
    m = re.match(r"\s*([0-9a-fA-F]{128})\b", text)
    if not m:
        raise core.InstallError("GE-Proton's checksum didn't download correctly. Try again later.")
    expected = m.group(1).lower()
    work = tools / f".deckhand-{release.version}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        digest = hashlib.sha512()
        tarball = updater.download_to(release.url, work / "download.tar.gz", progress, opener, digest=digest)
        if digest.hexdigest() != expected:
            raise core.InstallError("The GE-Proton download was damaged (its checksum doesn't match). Try again.")
        status(f"Unpacking {release.version}…")
        unpacked = work / "unpacked"
        with tarfile.open(tarball) as tf:
            members = tf.getmembers()
            tops = {m.name.split("/", 1)[0] for m in members}
            if len(tops) != 1 or any(m.name.startswith("/") or ".." in Path(m.name).parts for m in members):
                raise core.InstallError("The GE-Proton download isn't laid out like a Proton release.")
            if hasattr(tarfile, "tar_filter"):  # (refuses anything that would land outside the folder)
                tf.extractall(unpacked, filter="tar")
            else:
                tf.extractall(unpacked)
        tarball.unlink()
        top = unpacked / tops.pop()
        if not (top / "proton").is_file():
            raise core.InstallError("The GE-Proton download has no proton script in it.")
        os.replace(top, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target
