"""Missing runtimes, installed into one program's Windows setup (prefix).

The most common reason a Windows program won't start is a runtime it expects Windows to have: the
Visual C++ libraries, a .NET version, or DirectX 9's extra files. Deckhand downloads Microsoft's own
redistributables and runs them silently in the program's prefix with the program's Proton
(core.run_in_prefix). That's what winetricks does for these, without the tools winetricks needs
(cabextract and others) that SteamOS doesn't have.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import core, updater

QUIET = ("/install", "/quiet", "/norestart")


@dataclass(frozen=True)
class Fix:
    id: str
    name: str
    blurb: str
    files: tuple[tuple[str, str, tuple[str, ...]], ...]  # (url, saved as, installer arguments)


VCRUN = Fix("vcrun", "Visual C++ runtimes", "2015–2022, 64- and 32-bit: what most programs need", (
    ("https://aka.ms/vs/17/release/vc_redist.x64.exe", "vc_redist.x64.exe", QUIET),
    ("https://aka.ms/vs/17/release/vc_redist.x86.exe", "vc_redist.x86.exe", QUIET)))
DIRECTX = Fix("d3dx9", "DirectX 9 extras", "d3dx9, XInput and XAudio files older games look for (June 2010)", (
    ("https://download.microsoft.com/download/8/4/A/84A35BF1-DAFE-4AE8-82AF-AD2AE20B6B14/directx_Jun2010_redist.exe",
     "directx_Jun2010_redist.exe", ()),))
DOTNET_OLDEST = 5  # Microsoft no longer offers older .NET (Core) runtimes


def dotnet(major: int, arch: str = "x64") -> Fix:
    return Fix(f"dotnet{major}", f".NET {major} Desktop Runtime", "The .NET version this program was built on", (
        (f"https://aka.ms/dotnet/{major}.0/windowsdesktop-runtime-win-{arch}.exe",
         f"windowsdesktop-runtime-{major}-{arch}.exe", QUIET),))


def dotnet_needed(exe: Path) -> list[int]:
    """The .NET versions (5 and later: "3" for .NET Core 3.x) the program's own *.runtimeconfig.json files
    ask for. Programs built on .NET Framework (4.x) don't have one: Proton's own Wine Mono runs those."""
    majors: set[int] = set()
    try:
        configs = list(exe.parent.glob("*.runtimeconfig.json"))
    except OSError:
        configs = []
    for f in configs:
        try:
            opts = json.loads(f.read_text(encoding="utf-8-sig")).get("runtimeOptions", {})
        except (OSError, ValueError, AttributeError):
            continue
        frameworks = opts.get("frameworks") or ([opts["framework"]] if isinstance(opts.get("framework"), dict) else [])
        for fw in frameworks:
            if isinstance(fw, dict) and fw.get("name") in ("Microsoft.NETCore.App", "Microsoft.WindowsDesktop.App"):
                try:
                    majors.add(int(str(fw.get("version", "")).split(".")[0]))
                except ValueError:
                    pass
    return sorted(majors)


def available(app: core.App) -> list[Fix]:
    """What can be installed for this program, most useful first."""
    exe = Path(app.exe)
    arch = core.pe_machine(exe) or "x64"
    return [VCRUN, *(dotnet(m, arch) for m in dotnet_needed(exe) if m >= DOTNET_OLDEST), DIRECTX]


def apply(fix: Fix, app: core.App, paths: core.Paths, status: Callable[[str], None] = lambda s: None,
          roots=None, opener=None) -> None:
    """Download the fix's installers and run them in the program's prefix. Raises InstallError if one fails."""
    compat = Path(app.prefix)
    if not (compat / "pfx").is_dir():
        raise core.InstallError(f"{app.name}'s Windows folder is missing, so there's nothing to fix.")
    if core.prefix_in_use(compat):
        raise core.InstallError(f"{app.name} is running. Close it first (STEAM → Exit game), then try again.")
    report = getattr(status, "progress", lambda pct: None)
    paths.logs.mkdir(parents=True, exist_ok=True)
    with open(paths.logs / f"{app.id}-fix.log", "a", encoding="utf-8") as log_fh:
        tail: list[str] = []

        def log(line: str) -> None:
            tail[:] = (tail + [line])[-10:]
            log_fh.write(line + "\n")
            log_fh.flush()

        log(f"Deckhand {time.strftime('%Y-%m-%d %H:%M')}: installing {fix.name} for {app.name}")
        for url, name, args in fix.files:
            def progress(done: int, total: int) -> None:
                if total:
                    status(f"Downloading {fix.name}…  {core.human_size(done)} of {core.human_size(total)}")
                    report(done * 100 // total)

            def check(head: bytes) -> None:
                if not head.startswith(b"MZ"):
                    raise core.InstallError(f"The {fix.name} download didn't look right. Try again later.")

            report(-1)
            installer = updater.download_to(url, paths.downloads / name, progress, opener, check=check)
            try:
                status(f"Installing {fix.name}… (this can take a few minutes)")
                report(-1)
                if fix.id == DIRECTX.id:
                    rc = _directx(app, installer, roots, log)
                else:
                    rc = core.run_in_prefix(app, installer, *args, roots=roots, log=log)
            finally:
                installer.unlink(missing_ok=True)
            if rc not in core.RUN_OK:
                raise core.InstallError(f"{fix.name} didn't install (its installer ended with code {rc}).\n\n"
                                        + "\n".join(tail))
        log(f"{fix.name} installed")
    if f"fixed:{fix.id}" not in app.options:
        app.options.append(f"fixed:{fix.id}")
        core.Library(paths).upsert(app)


def _directx(app: core.App, redist: Path, roots, log: Callable[[str], None]) -> int:
    """DirectX's redistributable unpacks itself first; its DXSETUP then installs what's missing."""
    unpacked = Path(app.prefix) / "pfx/drive_c/deckhand-directx"
    shutil.rmtree(unpacked, ignore_errors=True)
    try:
        rc = core.run_in_prefix(app, redist, "/Q", "/T:C:\\deckhand-directx", roots=roots, log=log)
        setup = unpacked / "DXSETUP.exe"
        if rc not in core.RUN_OK or not setup.is_file():
            return rc or 1
        return core.run_in_prefix(app, setup, "/silent", roots=roots, log=log)
    finally:
        shutil.rmtree(unpacked, ignore_errors=True)
