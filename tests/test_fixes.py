"""Runtimes installed into a program's prefix with its Proton (Microsoft's installers faked)."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core, fixes  # noqa: E402
from tests.test_addons import FakeResponse  # noqa: E402
from tests.test_core import Env, bmp_icon, make_pe  # noqa: E402


class TestFixes(Env):
    def installed(self) -> core.App:
        job = self.job()
        pending = job.run()
        return job.finish(pending, pending.candidates[0].exe)

    def args(self, app: core.App) -> list[str]:
        return (Path(app.prefix) / "proton-args.txt").read_text().splitlines()

    def test_visual_cpp_runtimes_are_installed_quietly_in_the_container(self):
        app = self.installed()
        urls = []
        fixes.apply(fixes.VCRUN, app, self.paths, roots=[self.steam],
                    opener=lambda url: (urls.append(url), FakeResponse(b"MZ" + b"\0" * 100))[1])
        self.assertEqual(urls, [u for u, _n, _a in fixes.VCRUN.files])
        runs = [a for a in self.args(app) if "vc_redist" in a]
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(r.startswith("waitforexitandrun ") and r.endswith("/install /quiet /norestart")
                            for r in runs), runs)
        self.assertIn("--verb=waitforexitandrun", self.entry_log.read_text())  # same container as installs
        self.assertEqual(list(self.paths.downloads.iterdir()), [])  # installers deleted afterwards
        self.assertIn("fixed:vcrun", core.Library(self.paths).load()[0].options)
        self.assertIn("Visual C++ runtimes installed", (self.paths.logs / f"{app.id}-fix.log").read_text())

    def test_directx_unpacks_then_runs_its_setup(self):
        app = self.installed()
        fixes.apply(fixes.DIRECTX, app, self.paths, roots=[self.steam], opener=lambda url: FakeResponse(b"MZ"))
        runs = self.args(app)[-2:]
        self.assertTrue(runs[0].endswith("directx_Jun2010_redist.exe /Q /T:C:\\deckhand-directx"), runs)
        self.assertTrue(runs[1].endswith("deckhand-directx/DXSETUP.exe /silent"), runs)
        self.assertFalse((Path(app.prefix) / "pfx/drive_c/deckhand-directx").exists())

    def test_a_failing_installer_is_reported(self):
        app = self.installed()
        os.environ["FAKE_RC"] = "1"
        with self.assertRaises(core.InstallError) as ctx:
            fixes.apply(fixes.VCRUN, app, self.paths, roots=[self.steam], opener=lambda url: FakeResponse(b"MZ"))
        self.assertIn("code 1", str(ctx.exception))
        self.assertEqual(core.Library(self.paths).load()[0].options, [])
        os.environ["FAKE_RC"] = str(3010 % 256)  # "done, restart Windows": fine
        fixes.apply(fixes.VCRUN, app, self.paths, roots=[self.steam], opener=lambda url: FakeResponse(b"MZ"))

    def test_nothing_runs_while_the_program_does(self):
        app = self.installed()
        proc = subprocess.Popen(["sleep", "10"], env={**os.environ, "WINEPREFIX": str(Path(app.prefix) / "pfx")})
        try:
            with self.assertRaises(core.InstallError) as ctx:
                fixes.apply(fixes.VCRUN, app, self.paths, roots=[self.steam], opener=lambda url: FakeResponse(b"MZ"))
            self.assertIn("is running", str(ctx.exception))
        finally:
            proc.kill()
            proc.wait()

    def test_the_net_version_a_program_needs_is_offered(self):
        app = self.installed()
        self.assertEqual([f.id for f in fixes.available(app)], ["vcrun", "d3dx9"])
        exe = Path(app.exe)
        exe.write_bytes(make_pe(bmp_icon()))  # a 64-bit .exe
        (exe.parent / "CoolGame.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
            "tfm": "net8.0", "frameworks": [{"name": "Microsoft.NETCore.App", "version": "8.0.0"},
                                            {"name": "Microsoft.WindowsDesktop.App", "version": "8.0.0"}]}}))
        (exe.parent / "Old.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
            "framework": {"name": "Microsoft.WindowsDesktop.App", "version": "3.1.0"}}}))
        self.assertEqual(fixes.dotnet_needed(exe), [3, 8])
        offered = fixes.available(app)
        self.assertEqual([f.id for f in offered], ["vcrun", "dotnet8", "d3dx9"])  # (3.1 isn't offered any more)
        self.assertIn("/8.0/windowsdesktop-runtime-win-x64.exe", offered[1].files[0][0])
        self.assertEqual(core.pe_machine(exe), "x64")
        # A file that isn't laid out like one is skipped (this runs on the UI thread: never raise).
        (exe.parent / "Odd.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": None}))
        (exe.parent / "Odder.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": ["x"]}))
        self.assertEqual(fixes.dotnet_needed(exe), [3, 8])

    def test_a_download_that_is_not_an_installer_is_refused(self):
        app = self.installed()
        with self.assertRaises(core.InstallError):
            fixes.apply(fixes.VCRUN, app, self.paths, roots=[self.steam],
                        opener=lambda url: FakeResponse(b"<html>404</html>"))
        self.assertEqual(list(self.paths.downloads.iterdir()), [])
