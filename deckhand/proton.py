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


def _is_ge(name: str) -> bool:
    low = name.lower()
    return "ge-proton" in low or re.match(r"proton-.*-ge-\d", low) is not None


def installed(roots: Iterable[Path] | None = None) -> list[str]:
    """GE-Proton versions in Steam's compatibility tools folders, newest first."""
    names = {r.name for r in core.find_runtimes(roots, include_system_wine=False) if _is_ge(r.name)}
    return sorted(names, key=core._version_key, reverse=True)


def latest(fetch: Callable[[str], bytes] | None = None) -> Release:
    data = json.loads((fetch or (lambda url: updater.fetch(url, timeout=30)))(RELEASES_API))
    assets = [a for a in data.get("assets", []) if isinstance(a, dict)]
    tar = next((a for a in assets if str(a.get("name", "")).endswith(".tar.gz")), None)
    if tar is None:
        raise core.InstallError("Couldn't find GE-Proton's download in its latest release. Try again later.")
    sha = next((str(a.get("browser_download_url", "")) for a in assets
                if str(a.get("name", "")).endswith(".sha512sum")), "")
    return Release(str(tar["name"])[:-len(".tar.gz")], str(tar.get("browser_download_url", "")),
                   int(tar.get("size") or 0), sha)


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
    expected = ""
    if release.sha512_url:
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
        if expected and digest.hexdigest() != expected:
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
