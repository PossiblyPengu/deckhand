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
  info) # unscoped (what a launcher runs): installed anywhere
        if [ "$2" != --user ] && [ "$2" != --system ]; then grep -qx "$2" "$db" 2>/dev/null && exit 0; exit 1; fi
        # where it is: as recorded by install; apps put in the db by a test count as --user installs
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
        self.assertIn("B=com.google.Chrome", script)
        self.assertIn('exec flatpak run "$B" --kiosk', script)
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
        self.assertIn("B=com.microsoft.Edge", Path(app.launcher).read_text())

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
        self.assertIn("B=org.chromium.Chromium", launcher)
        self.assertIn('exec flatpak run "$B" --kiosk', launcher)
        self.assertIn(f"--load-extension={ext}", launcher)
        self.assertEqual(launcher.count(f"--load-extension={ext}"), 2)
        self.assertIn("curl -fsL", launcher)  # keeps itself up to date
        self.assertEqual(app.options, ["better-xcloud", "own-profile"])
        # Setting it up again keeps the choice; turning it off goes back to Chrome. One shortcut throughout.
        self.assertEqual(streaming.set_up(svc, self.paths, roots=[self.steam], fetch=self._fake_bx).options,
                         ["better-xcloud", "own-profile"])
        app = streaming.set_up(svc, self.paths, roots=[self.steam], better_xcloud=False)
        self.assertEqual(app.options, ["own-profile"])
        self.assertIn("B=com.google.Chrome", Path(app.launcher).read_text())
        self.assertNotIn("load-extension", Path(app.launcher).read_text())
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)

    def test_turning_better_xcloud_on_before_steam_saved_the_shortcut_sends_nothing_again(self):
        sent = []
        orig = core.request_steam_add, core.STEAM_ADD_WAIT
        core.request_steam_add = lambda f: (sent.append(f), True)[1]  # Steam takes it, saves its list later
        core.STEAM_ADD_WAIT = 0.1
        core.steam_is_running = lambda: True
        try:
            svc = streaming.service("xbox-cloud")
            first = streaming.set_up(svc, self.paths, roots=[self.steam])
            self.assertEqual((first.steam_added, len(sent)), ("requested", 1))
            app = streaming.set_up(svc, self.paths, roots=[self.steam], better_xcloud=True, fetch=self._fake_bx)
            self.assertEqual(len(sent), 1)  # the shortcut Steam has runs the same launcher: a second is a duplicate
            self.assertEqual((app.steam_added, app.steam_requested_at), ("requested", first.steam_requested_at))
            self.assertIn("load-extension", Path(app.launcher).read_text())
        finally:
            core.request_steam_add, core.STEAM_ADD_WAIT = orig

    def test_a_bad_better_xcloud_download_is_refused(self):
        with self.assertRaises(core.InstallError):
            streaming.set_up(streaming.service("xbox-cloud"), self.paths, roots=[self.steam], better_xcloud=True,
                             fetch=lambda url: b"<html>rate limited</html>")
        self.assertEqual(core.Library(self.paths).load(), [])

    def test_better_xcloud_is_only_for_xbox(self):
        app = streaming.set_up(streaming.service("geforce-now"), self.paths, roots=[self.steam], better_xcloud=True,
                               fetch=self._fake_bx)
        self.assertNotIn("better-xcloud", app.options)
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
                                 "boosteroid": [], "moonlight": ["Moonlight"], "chiaki-ng": [], "greenlight": []})

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


class TestStreamingSignInAndLauncher(FlatpakEnv):
    """Each cloud service keeps its own sign-in; the launcher finds a browser when it runs."""

    def run_launcher(self, launcher: str, **env):
        e = dict(os.environ, SteamGamepadUI="1", **env)
        return subprocess.run(["bash", launcher], env=e, capture_output=True, text=True)

    def test_a_new_cloud_service_gets_its_own_profile_folder(self):
        app = streaming.set_up(streaming.service("amazon-luna"), self.paths, roots=[self.steam])
        self.assertIn("own-profile", app.options)
        text = Path(app.launcher).read_text()
        self.assertEqual(text.count('"--user-data-dir=$HOME/.var/app/$B/data/deckhand/amazon-luna"'), 2)  # both modes
        self.assertEqual(streaming.profile_dir("com.google.Chrome", streaming.service("amazon-luna"), self.home),
                         self.home / ".var/app/com.google.Chrome/data/deckhand/amazon-luna")

    def test_two_services_never_share_a_profile(self):
        a = streaming.set_up(streaming.service("amazon-luna"), self.paths, roots=[self.steam])
        b = streaming.set_up(streaming.service("boosteroid"), self.paths, roots=[self.steam])
        self.assertIn("deckhand/amazon-luna", Path(a.launcher).read_text())
        self.assertNotIn("deckhand/boosteroid", Path(a.launcher).read_text())
        self.assertIn("deckhand/boosteroid", Path(b.launcher).read_text())

    def test_a_service_set_up_before_profiles_keeps_its_shared_sign_in(self):
        svc = streaming.service("boosteroid")
        app = streaming.set_up(svc, self.paths, roots=[self.steam])
        app.options = []  # as an older Deckhand left it
        core.Library(self.paths).upsert(app)
        again = streaming.set_up(svc, self.paths, roots=[self.steam])
        self.assertNotIn("own-profile", again.options)
        self.assertNotIn("user-data-dir", Path(again.launcher).read_text())

    def test_apps_for_home_streaming_have_no_browser_profile(self):
        app = streaming.set_up(streaming.service("moonlight"), self.paths, roots=[self.steam])
        self.assertEqual(app.options, [])
        self.assertNotIn("user-data-dir", Path(app.launcher).read_text())

    def test_the_launcher_uses_another_browser_when_the_chosen_one_is_gone(self):
        app = streaming.set_up(streaming.service("amazon-luna"), self.paths, roots=[self.steam])  # Chrome
        self.flatpak_db.write_text("com.microsoft.Edge\n")  # Chrome was uninstalled; Edge is there now
        self.flatpak_log.write_text("")
        r = self.run_launcher(app.launcher)
        self.assertEqual(r.returncode, 0, r.stderr)
        run = next(c for c in self.calls() if c.startswith("run "))
        self.assertTrue(run.startswith("run com.microsoft.Edge "), run)
        self.assertIn("--user-data-dir=" + str(Path.home()) + "/.var/app/com.microsoft.Edge/data/deckhand/amazon-luna", run)

    def test_the_launcher_says_so_when_no_browser_is_left(self):
        app = streaming.set_up(streaming.service("amazon-luna"), self.paths, roots=[self.steam])
        self.flatpak_db.write_text("")  # nothing installed any more
        self.flatpak_log.write_text("")
        r = self.run_launcher(app.launcher)
        self.assertEqual(r.returncode, 1)
        log = (self.paths.logs / "stream-amazon-luna-launch.log").read_text()  # (a launcher writes its output there)
        self.assertIn("Amazon Luna needs a web browser", log)
        self.assertIn("pick Amazon Luna again", log)
        self.assertFalse([c for c in self.calls() if c.startswith("run ")])

    def test_better_xcloud_never_falls_back_to_a_browser_that_cannot_load_it(self):
        def fake(url):
            return (b"// ==UserScript==\n// @name Better xCloud\n// @version 1.0\n// @match https://www.xbox.com/*/play*\n"
                    b"// ==/UserScript==\nconsole.log(1)\n")
        app = streaming.set_up(streaming.service("xbox-cloud"), self.paths, roots=[self.steam], better_xcloud=True,
                               fetch=fake)
        self.flatpak_db.write_text("com.google.Chrome\n")  # only Chrome: it can't load the extension
        r = self.run_launcher(app.launcher)
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs a web browser", (self.paths.logs / "stream-xbox-cloud-launch.log").read_text())

    def test_greenlight_installs_as_an_app(self):
        svc = streaming.service("greenlight")
        self.assertEqual(svc.app, "io.github.unknownskl.greenlight")
        app = streaming.set_up(svc, self.paths, roots=[self.steam])
        self.assertIn("install --system -y --noninteractive flathub io.github.unknownskl.greenlight", self.calls())
        self.assertIn("exec flatpak run io.github.unknownskl.greenlight", Path(app.launcher).read_text())
        self.assertTrue((Path(core.__file__).parent / "logos/greenlight.png").is_file())

    def test_there_is_no_steam_link_entry(self):  # (the Deck streams Steam games natively)
        self.assertIsNone(streaming.service("steam-link"))
        self.assertFalse([s for s in streaming.SERVICES if "SteamLink" in s.app])

    def test_the_browser_download_is_described_in_plain_words(self):
        self.assertEqual(streaming.download_note("com.google.Chrome"), "about 160 MB")
        self.assertEqual(streaming.download_note("com.microsoft.Edge"), "about 210 MB")
        self.assertEqual(streaming.download_note("com.moonlight_stream.Moonlight"), "")

    def test_the_browser_to_install_can_be_chosen(self):
        svc = streaming.service("amazon-luna")
        self.assertEqual(streaming.needs(svc, set()), "com.google.Chrome")  # (the default)
        self.assertEqual(streaming.needs(svc, set(), prefer="com.microsoft.Edge"), "com.microsoft.Edge")
        self.assertEqual(streaming.needs(svc, set(), prefer="not.a.browser"), "com.google.Chrome")
        # One that is already installed is never replaced by the preference.
        self.assertIsNone(streaming.needs(svc, {"org.chromium.Chromium"}, prefer="com.microsoft.Edge"))
        # Better xCloud only ever runs in Chromium, whatever was preferred.
        self.assertEqual(streaming.needs(streaming.service("xbox-cloud"), set(), True, "com.microsoft.Edge"),
                         "org.chromium.Chromium")

    def test_set_up_installs_only_the_chosen_browser(self):
        app = streaming.set_up(streaming.service("boosteroid"), self.paths, roots=[self.steam],
                               browser="com.microsoft.Edge")
        installs = [c for c in self.calls() if c.startswith("install")]
        self.assertEqual(len(installs), 1)
        self.assertIn("com.microsoft.Edge", installs[0])
        self.assertIn("B=com.microsoft.Edge", Path(app.launcher).read_text())
