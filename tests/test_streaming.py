"""Streaming services: installed from (a fake) Flathub and added to (a fake) Steam."""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core, streaming  # noqa: E402
from tests.test_core import Env  # noqa: E402

FAKE_FLATPAK = r'''#!/bin/bash
echo "$@" >> "$FAKE_FLATPAK_LOG"
db="$FAKE_FLATPAK_DB"
case "$1" in
  list) cat "$db" 2>/dev/null ;;
  remotes) [ -n "$FAKE_FLATPAK_NO_SYSTEM" ] || echo flathub ;;
  install) [ -n "$FAKE_FLATPAK_FAIL" ] && { echo "error: no network"; exit 1; }
           [ "$2" = --system ] && [ -n "$FAKE_FLATPAK_SYSTEM_DENIED" ] && { echo "error: Not authorized"; exit 1; }
           echo "Installing ${@: -1}"; echo "${@: -1}" >> "$db"; echo "$2 ${@: -1}" >> "$db.scope" ;;
  info) # where it is: as recorded by install; apps put in the db by a test count as --user installs
        grep -qxF -- "$2 ${@: -1}" "$db.scope" 2>/dev/null && exit 0
        [ "$2" = --user ] && grep -qx "${@: -1}" "$db" 2>/dev/null && ! grep -qF -- " ${@: -1}" "$db.scope" 2>/dev/null && exit 0
        exit 1 ;;
  uninstall) grep -qx "${@: -1}" "$db" 2>/dev/null || { echo "error: ${@: -1} not installed"; exit 1; }
             grep -vx "${@: -1}" "$db" > "$db.tmp"; mv "$db.tmp" "$db"
             grep -vxF -- "$2 ${@: -1}" "$db.scope" > "$db.tmp" 2>/dev/null; mv "$db.tmp" "$db.scope" 2>/dev/null || true ;;
esac
exit 0
'''


class FlatpakEnv(Env):
    def setUp(self):
        super().setUp()
        bindir = self.tmp / "flatpak-bin"
        bindir.mkdir()
        (bindir / "flatpak").write_text(FAKE_FLATPAK)
        (bindir / "flatpak").chmod(0o755)
        os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
        self.flatpak_log = self.tmp / "flatpak.log"
        self.flatpak_db = self.tmp / "flatpak.db"
        os.environ["FAKE_FLATPAK_LOG"] = str(self.flatpak_log)
        os.environ["FAKE_FLATPAK_DB"] = str(self.flatpak_db)

    def calls(self) -> list[str]:
        return self.flatpak_log.read_text().splitlines() if self.flatpak_log.exists() else []


class TestStreaming(FlatpakEnv):
    def test_cloud_service_installs_a_browser_and_lands_in_steam(self):
        svc = streaming.service("xbox-cloud")
        app = streaming.set_up(svc, self.paths, roots=[self.steam])
        calls = self.calls()
        self.assertIn("install --system -y --noninteractive flathub com.google.Chrome", calls)
        self.assertIn("override --user --filesystem=/run/udev:ro com.google.Chrome", calls)
        script = Path(app.launcher).read_text()
        self.assertIn("flatpak run com.google.Chrome --kiosk", script)
        self.assertIn("--window-size=1024,640", script)
        self.assertIn("--force-device-scale-factor=1.25", script)
        self.assertIn("--kiosk", script)
        self.assertIn("--app=https://www.xbox.com/play", script)
        self.assertIn("--start-maximized", script)
        self.assertIn("SteamGamepadUI", script)
        self.assertIn("https://www.xbox.com/play", script)
        self.assertEqual((app.kind, app.id, app.steam_added), ("stream", "stream-xbox-cloud", "file"))
        self.assertTrue(core.in_steam(app, [self.steam]))
        self.assertEqual(core.app_paths(app), [])
        # A second cloud service reuses the browser.
        before = len([c for c in self.calls() if c.startswith("install")])
        streaming.set_up(streaming.service("geforce-now"), self.paths, roots=[self.steam])
        self.assertEqual(len([c for c in self.calls() if c.startswith("install")]), before)
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 2)

    def test_launcher_uses_kiosk_in_game_mode_and_a_window_on_the_desktop(self):
        app = streaming.set_up(streaming.service("xbox-cloud"), self.paths, roots=[self.steam])
        launcher = app.launcher
        self.assertIn("steam -silent", Path(launcher).read_text())
        bindir = self.tmp / "flatpak-bin"
        steam = bindir / "steam"
        steam.write_text('#!/bin/bash\necho "steam $@" >> "$FAKE_FLATPAK_LOG"\n')
        steam.chmod(0o755)
        pgrep = bindir / "pgrep"
        pgrep.write_text("#!/bin/sh\nexit 1\n")
        pgrep.chmod(0o755)
        self.flatpak_log.write_text("")
        game_mode = dict(os.environ, PATH=f"{self.tmp / 'flatpak-bin'}:{os.environ['PATH']}", SteamGamepadUI="1")
        subprocess.run(["bash", launcher], env=game_mode, check=True)
        game_calls = self.calls()
        game_run = next(c for c in game_calls if c.startswith("run "))
        self.assertIn("--kiosk", game_run)

        self.flatpak_log.write_text("")
        desktop = dict(os.environ)
        for key in ("SteamGamepadUI", "XDG_CURRENT_DESKTOP", "GAMESCOPE_WAYLAND_DISPLAY"):
            desktop.pop(key, None)
        desktop["PATH"] = f"{self.tmp / 'flatpak-bin'}:{desktop['PATH']}"
        subprocess.run(["bash", launcher], env=desktop, check=True)
        desktop_calls = self.calls()
        desktop_run = next(c for c in desktop_calls if c.startswith("run "))
        self.assertIn("--app=https://www.xbox.com/play", desktop_run)
        self.assertIn("--start-maximized", desktop_run)
        self.assertNotIn("--kiosk", desktop_run)
        for _ in range(50):
            desktop_calls = self.calls()
            if "steam -silent" in desktop_calls:
                break
            time.sleep(0.02)
        self.assertIn("steam -silent", desktop_calls)

    def test_an_installed_edge_is_used_instead_of_installing_chrome(self):
        self.flatpak_db.write_text("com.microsoft.Edge\n")
        app = streaming.set_up(streaming.service("amazon-luna"), self.paths, roots=[self.steam])
        self.assertFalse([c for c in self.calls() if c.startswith("install")])
        self.assertIn("flatpak run com.microsoft.Edge", Path(app.launcher).read_text())

    def test_home_streaming_app(self):
        app = streaming.set_up(streaming.service("moonlight"), self.paths, roots=[self.steam])
        self.assertIn("install --system -y --noninteractive flathub com.moonlight_stream.Moonlight", self.calls())
        self.assertIn("exec flatpak run com.moonlight_stream.Moonlight", Path(app.launcher).read_text())

    def test_setting_up_twice_keeps_one_shortcut(self):
        svc = streaming.service("boosteroid")
        streaming.set_up(svc, self.paths, roots=[self.steam])
        streaming.set_up(svc, self.paths, roots=[self.steam])
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)
        self.assertEqual(len(core.Library(self.paths).load()), 1)

    def test_installs_system_wide_like_discover_or_falls_back_to_the_user(self):
        self.assertEqual(streaming.install_app("com.google.Chrome"), "system")
        self.assertIn("install --system -y --noninteractive flathub com.google.Chrome", self.calls())
        self.assertFalse([c for c in self.calls() if "--user" in c and c.startswith("install")])
        os.environ["FAKE_FLATPAK_SYSTEM_DENIED"] = "1"
        lines = []
        self.assertEqual(streaming.install_app("com.moonlight_stream.Moonlight", lines.append), "user")
        self.assertIn("remote-add --user --if-not-exists flathub " + streaming.FLATHUB, self.calls())
        self.assertIn("install --user -y --noninteractive flathub com.moonlight_stream.Moonlight", self.calls())
        self.assertTrue(any("for this user instead" in line for line in lines))
        del os.environ["FAKE_FLATPAK_SYSTEM_DENIED"]
        os.environ["FAKE_FLATPAK_NO_SYSTEM"] = "1"  # no system-wide Flathub at all: straight to the user
        self.assertEqual(streaming.install_app("org.chromium.Chromium"), "user")
        self.assertNotIn("install --system -y --noninteractive flathub org.chromium.Chromium", self.calls())

    def test_uninstall_removes_it_from_where_it_was_installed(self):
        streaming.install_app("com.google.Chrome")  # system-wide, like Discover
        streaming.uninstall_app("com.google.Chrome")
        self.assertIn("uninstall --system -y --noninteractive com.google.Chrome", self.calls())
        self.assertNotIn("com.google.Chrome", streaming.installed_apps())
        os.environ["FAKE_FLATPAK_SYSTEM_DENIED"] = "1"
        streaming.install_app("com.moonlight_stream.Moonlight")  # fell back to the user
        streaming.uninstall_app("com.moonlight_stream.Moonlight")
        self.assertIn("uninstall --user -y --noninteractive com.moonlight_stream.Moonlight", self.calls())
        with self.assertRaises(core.InstallError):
            streaming.uninstall_app("org.example.NotThere")

    def test_failed_install_explains_and_adds_nothing(self):
        os.environ["FAKE_FLATPAK_FAIL"] = "1"
        with self.assertRaises(core.InstallError) as ctx:
            streaming.set_up(streaming.service("xbox-cloud"), self.paths, roots=[self.steam])
        self.assertIn("no network", str(ctx.exception))
        self.assertEqual(core.Library(self.paths).load(), [])
        self.assertEqual(core.steam_shortcuts([self.steam]), [])

    def _fake_bx(self, url):
        self.assertEqual(url, streaming.BETTER_XCLOUD_URL)
        return (b"// ==UserScript==\n// @name         Better xCloud\n// @version      6.7.12\n"
                b"// @match        https://www.xbox.com/*/play*\n// @exclude      https://www.xbox.com/*/x\n"
                b"// @grant        none\n// ==/UserScript==\n\"use strict\";\n")

    def test_better_xcloud_runs_in_chromium_as_an_extension(self):
        import json
        self.flatpak_db.write_text("com.google.Chrome\n")
        svc = streaming.service("xbox-cloud")
        app = streaming.set_up(svc, self.paths, roots=[self.steam], better_xcloud=True, fetch=self._fake_bx)
        self.assertIn("install --system -y --noninteractive flathub org.chromium.Chromium", self.calls())
        self.assertIn("override --user --filesystem=/run/udev:ro org.chromium.Chromium", self.calls())
        ext = streaming.better_xcloud_dir()
        manifest = json.loads((ext / "manifest.json").read_text())
        self.assertEqual(manifest["version"], "6.7.12")
        script = manifest["content_scripts"][0]
        self.assertEqual((script["world"], script["run_at"]), ("MAIN", "document_start"))
        self.assertEqual(script["matches"], ["https://www.xbox.com/*/play*"])
        self.assertTrue((ext / "better-xcloud.user.js").read_text().startswith("// ==UserScript=="))
        launcher = Path(app.launcher).read_text()
        self.assertIn("flatpak run org.chromium.Chromium --kiosk", launcher)
        self.assertIn(f"--load-extension={ext}", launcher)
        self.assertEqual(launcher.count(f"--load-extension={ext}"), 2)
        self.assertIn("curl -fsL", launcher)  # keeps itself up to date
        self.assertEqual(app.options, ["better-xcloud"])
        # Setting it up again keeps the choice; turning it off goes back to Chrome. One shortcut throughout.
        self.assertEqual(streaming.set_up(svc, self.paths, roots=[self.steam], fetch=self._fake_bx).options,
                         ["better-xcloud"])
        app = streaming.set_up(svc, self.paths, roots=[self.steam], better_xcloud=False)
        self.assertEqual(app.options, [])
        self.assertIn("flatpak run com.google.Chrome", Path(app.launcher).read_text())
        self.assertNotIn("load-extension", Path(app.launcher).read_text())
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)

    def test_a_bad_better_xcloud_download_is_refused(self):
        with self.assertRaises(core.InstallError):
            streaming.set_up(streaming.service("xbox-cloud"), self.paths, roots=[self.steam], better_xcloud=True,
                             fetch=lambda url: b"<html>rate limited</html>")
        self.assertEqual(core.Library(self.paths).load(), [])

    def test_better_xcloud_is_only_for_xbox(self):
        app = streaming.set_up(streaming.service("geforce-now"), self.paths, roots=[self.steam], better_xcloud=True,
                               fetch=self._fake_bx)
        self.assertEqual(app.options, [])
        self.assertNotIn("load-extension", Path(app.launcher).read_text())

    def test_shortcuts_made_outside_are_recognised(self):
        entries = [(Path("/c"), e) for e in (
            {"AppName": "Google Chrome", "Exe": '"flatpak"',
             "LaunchOptions": "run com.google.Chrome --kiosk https://www.xbox.com/en-US/play"},
            {"AppName": "GFN", "Exe": "/usr/bin/flatpak", "LaunchOptions": "run com.nvidia.geforcenow"},
            {"AppName": "Moonlight", "Exe": '"/home/deck/Applications/Moonlight-6.1.0-x86_64.AppImage"'},
            {"AppName": "Xbox", "Exe": f'"{self.paths.launchers}/stream-xbox-cloud.sh"'},  # ours: not "outside"
            {"AppName": "Halo", "Exe": '"/x/halo.sh"'},
        )]
        found = {svc.id: [e["AppName"] for e in streaming.spots(svc, entries, self.paths.launchers)]
                 for svc in streaming.SERVICES}
        self.assertEqual(found, {"xbox-cloud": ["Google Chrome"], "geforce-now": ["GFN"], "amazon-luna": [],
                                 "boosteroid": [], "moonlight": ["Moonlight"], "chiaki-ng": []})

    def test_an_appimage_or_command_counts_as_installed(self):
        apps = self.home / "Applications"
        apps.mkdir()
        img = apps / "Moonlight-6.1.0-x86_64.AppImage"
        img.write_text("#!/bin/sh\n")
        img.chmod(0o755)
        svc = streaming.service("moonlight")
        self.assertEqual(streaming.local_copy(svc), str(img))
        installed = streaming.detect(set())
        self.assertIn("local:moonlight", installed)
        self.assertIsNone(streaming.needs(svc, installed))
        app = streaming.set_up(svc, self.paths, roots=[self.steam])
        self.assertFalse([c for c in self.calls() if c.startswith("install")])  # nothing downloaded
        self.assertIn(f"exec {img}", Path(app.launcher).read_text())

    def test_the_native_geforce_now_app_is_used_when_installed(self):
        self.flatpak_db.write_text("com.nvidia.geforcenow\n")
        app = streaming.set_up(streaming.service("geforce-now"), self.paths, roots=[self.steam])
        self.assertFalse([c for c in self.calls() if c.startswith("install")])
        self.assertIn("exec flatpak run com.nvidia.geforcenow", Path(app.launcher).read_text())

    def test_removing_a_service_leaves_nothing_behind(self):
        (self.paths.prefixes).mkdir(parents=True)
        app = streaming.set_up(streaming.service("xbox-cloud"), self.paths, roots=[self.steam])
        self.assertEqual(core.orphan_prefixes(self.paths), [])
        self.assertFalse(core.uninstall(app, self.paths, roots=[self.steam]))
        self.assertFalse(Path(app.launcher).exists())
        self.assertEqual(core.steam_shortcuts([self.steam]), [])
        self.assertTrue(self.tmp.exists() and Path.cwd().exists())  # an empty prefix path deletes nothing
