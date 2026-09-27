"""Drive the real window headless through installs (with a fake Proton), incl. keyboard navigation."""
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DECKHAND_NO_GAMEPAD"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

from deckhand import core  # noqa: E402
from tests.test_core import Env, bmp_icon, make_lnk, make_pe  # noqa: E402


@unittest.skipIf(QApplication is None, "PyQt6 not installed")
class TestWindow(Env):
    @classmethod
    def setUpClass(cls):
        from deckhand import theme
        cls.qapp = QApplication.instance() or QApplication([])
        cls.qapp.setStyle("Fusion")
        cls.qapp.setStyleSheet(theme.STYLE)

    def setUp(self):
        super().setUp()
        from deckhand import app as app_mod
        from deckhand.widgets import Sheet
        self._orig_roots = core.steam_roots
        core.steam_roots = lambda home=None: [self.steam]
        # Sheets are modal; answer them from a queue instead.
        self.answers: list[int] = []
        self.asked: list[str] = []
        self._orig_ask = Sheet.ask
        Sheet.ask = staticmethod(lambda parent, title, *a, **k: (self.asked.append(title),
                                                                 self.answers.pop(0) if self.answers else -1)[1])
        self.downloads = self.home / "Downloads"
        self.win = app_mod.MainWindow(self.paths)
        self.win.installer_dirs = [self.downloads]
        self.win.show()
        self.win.activateWindow()
        self.shots = os.environ.get("DECKHAND_SCREENSHOTS")

    def tearDown(self):
        from deckhand.widgets import Sheet
        Sheet.ask = self._orig_ask
        core.steam_roots = self._orig_roots
        self.win.nav and self.win.nav.stop()
        self.win.pending = None  # (closing would otherwise ask about an unfinished pick)
        self.win.close()
        self.win.deleteLater()
        self.pump()
        super().tearDown()

    def pump(self, n=5):
        for _ in range(n):
            self.qapp.processEvents()

    def wait_for(self, cond, timeout=20):
        end = time.time() + timeout
        while not cond():
            self.qapp.processEvents()
            if time.time() > end:
                self.fail("timed out")
            time.sleep(0.02)

    def shot(self, name, widget=None):
        if self.shots:
            self.pump()
            Path(self.shots).mkdir(parents=True, exist_ok=True)
            (widget or self.win).grab().save(str(Path(self.shots) / f"{name}.png"))

    def add_installer(self, name="Cool Game Setup.exe", size=4_000_000):
        p = self.downloads / name
        p.write_bytes(b"MZ" + b"\0" * size)
        return p

    def key(self, k):
        QTest.keyClick(QApplication.focusWidget(), k)
        self.pump()

    def test_home_shows_found_installers(self):
        self.shot("1-home-empty")
        self.add_installer("setup_the_witcher_3_goty_1.32_(10709).exe")
        self.add_installer("Firefox Setup 120.0.exe", 1000)
        self.win.go_home()
        titles = [t.title for t in self.win.home.tiles]
        self.assertEqual(titles[0], "Browse files")
        self.assertEqual(sorted(titles[1:]), ["Firefox", "The Witcher 3 Goty"])
        self.shot("2-home-found")

    def test_install_from_tile_ends_in_steam_with_icon_and_art(self):
        lnk = self.tmp / "x.lnk"
        lnk.write_bytes(make_lnk(r"C:\Program Files\Cool Game\bin\CoolGame.exe"))
        pe = self.tmp / "real.exe"
        pe.write_bytes(make_pe(bmp_icon(32), width=32))
        os.environ.update(FAKE_LNK=str(lnk), FAKE_EXE_SRC=str(pe), FAKE_SLEEP="1")
        inst = self.add_installer()
        (self.downloads / "Cool Game Setup-1.bin").write_bytes(b"x" * 1000)
        self.win.go_home()
        self.answers = [0]  # "Install"
        self.win.home.tiles[1].click()
        self.assertEqual(self.asked, ["Install Cool Game?"])
        self.wait_for(lambda: "installer" in self.win.progress.status.text())
        self.shot("3-installing")
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
        self.shot("4-done")
        self.assertIn("Cool Game Deluxe is in your Steam library", self.win.done.heading.text())

        app = core.Library(self.paths).load()[0]
        self.assertTrue(Path(app.icon).is_file())
        grid = self.steam / "userdata/12345/config/grid"
        for suffix in ("p", "", "_hero", "_logo"):
            self.assertTrue((grid / f"{app.steam_appid}{suffix}.png").is_file(), suffix)
        sc = core.vdf_loads((self.steam / "userdata/12345/config/shortcuts.vdf").read_bytes())["shortcuts"]["0"]
        self.assertEqual(sc["icon"], app.icon)

        # Delete the installer (and its .bin part) from the done page.
        self.assertIn("Delete installer", self.win.done.delete_btn.text())
        self.answers = [0]
        self.win.done.delete_btn.click()
        self.assertFalse(inst.exists())
        self.assertFalse((self.downloads / "Cool Game Setup-1.bin").exists())
        self.assertFalse(self.win.done.delete_btn.isVisible())

        # Uninstalling cleans up the generated art and icon too.
        core.uninstall(app, self.paths, roots=[self.steam])
        self.assertFalse(any(grid.iterdir()))
        self.assertFalse(Path(app.icon).exists())

    def test_user_artwork_is_never_overwritten(self):
        from deckhand import artwork
        grid = self.steam / "userdata/12345/config/grid"
        grid.mkdir(parents=True)
        (grid / "123p.jpg").write_bytes(b"mine")
        written = artwork.write_steam_artwork(123, "X", None, [grid])
        self.assertNotIn(str(grid / "123p.png"), written)
        self.assertEqual(len(written), 3)

    def test_keyboard_and_controller_style_navigation(self):
        for n in ("a.exe", "b.exe", "c.exe"):
            self.add_installer(n, 10)
        self.win.go_home()
        tiles = self.win.home.tiles
        self.wait_for(lambda: QApplication.focusWidget() is tiles[1])
        self.key(Qt.Key.Key_Right)
        self.assertIs(QApplication.focusWidget(), tiles[2])
        self.key(Qt.Key.Key_Left)
        self.key(Qt.Key.Key_Left)
        self.assertIs(QApplication.focusWidget(), tiles[0])
        self.key(Qt.Key.Key_Left)  # nothing further left: stays put
        self.assertIs(QApplication.focusWidget(), tiles[0])
        self.key(Qt.Key.Key_Return)  # A on "Browse files"
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.browser)
        self.assertEqual(self.win.browser.cwd, self.downloads)
        self.key(Qt.Key.Key_Escape)  # B at a top-level place goes back home
        self.assertIs(self.win.stack.currentWidget(), self.win.home)

    def test_gamepad_actions(self):
        for n in ("a.exe", "b.exe"):
            self.add_installer(n, 10)
        self.win.go_home()
        tiles = self.win.home.tiles
        self.wait_for(lambda: QApplication.focusWidget() is tiles[1])
        nav = self.win.nav
        nav.on_pad("right", True)
        nav.on_pad("right", False)
        self.pump()
        self.assertIs(QApplication.focusWidget(), tiles[2])
        self.answers = [1]  # A opens the confirm sheet; answer Cancel
        nav.on_pad("a", True)
        self.wait_for(lambda: self.asked == ["Install A?"])
        nav.on_pad("a", False)
        nav.on_pad("x", True)  # X = Browse
        self.pump()
        self.assertIs(self.win.stack.currentWidget(), self.win.browser)
        nav.on_pad("x", False)
        nav.on_pad("b", True)
        self.pump()
        self.assertIs(self.win.stack.currentWidget(), self.win.home)

    def test_browser_folders_and_back(self):
        sub = self.downloads / "Some Game"
        sub.mkdir()
        (sub / "setup.exe").write_bytes(b"MZ")
        (sub / "notes.txt").write_text("x")
        self.win.browse_installers()
        rows = [self.win.browser.list.item(i).text() for i in range(self.win.browser.list.count())]
        self.assertEqual(rows, ["Some Game"])
        self.win.browser._activate(self.win.browser.list.item(0))
        rows = [self.win.browser.list.item(i).text().split()[0] for i in range(self.win.browser.list.count())]
        self.assertEqual(rows, ["Up", "setup.exe"])
        self.assertEqual(self.win.browser.list.currentRow(), 1)
        self.shot("5-browse")
        self.answers = [1]  # Cancel on the confirm sheet
        self.win.browser._activate(self.win.browser.list.item(1))
        self.assertEqual(self.asked, ["Install Some Game?"])
        self.win.browser.back()
        self.assertEqual(self.win.browser.cwd, self.downloads)

    def test_nothing_installed_offers_portable(self):
        os.environ["FAKE_NOTHING"] = "1"
        inst = self.add_installer("PortableTool.exe", 10)
        self.win.start_install(inst)
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.pick)
        self.assertEqual(self.win.pick.heading.text(), "Nothing was installed")
        self.shot("6-pick-nothing")
        self.win.use_portable()
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)

    def test_failure_is_explained(self):
        os.environ["FAKE_BROKEN"] = "1"
        self.win.start_install(self.add_installer())
        self.wait_for(lambda: "Install failed" in self.asked)
        self.assertIs(self.win.stack.currentWidget(), self.win.home)

    def test_update_banner_download_and_restart(self):
        import hashlib
        import json
        from deckhand import app as app_mod
        from deckhand import updater
        from tests.test_update import NEW_APP, Server
        web = self.tmp / "web" / "bin"
        web.mkdir(parents=True)
        (web / updater.ASSET).write_bytes(NEW_APP)
        (web / updater.MANIFEST).write_text(json.dumps(
            {"version": "9.0.0", "sha256": hashlib.sha256(NEW_APP).hexdigest(), "notes": "Shiny new things."}))
        server = Server(self.tmp / "web")
        target = self.tmp / "installed" / "deckhand"
        target.parent.mkdir()
        target.write_bytes(b"old app")
        restarted = []
        orig = (updater.self_path, updater.restart)
        updater.self_path = lambda: target
        updater.restart = lambda t, args=None: restarted.append(t)
        os.environ["DECKHAND_UPDATE_BASE"] = f"{server.url}/bin"
        try:
            win = app_mod.MainWindow(self.paths, check_updates=True)  # checks in the background at start
            win.installer_dirs = [self.downloads]
            win.show()
            self.wait_for(lambda: win.home.banner.isVisible())
            self.assertIn("9.0.0 is available", win.home.banner_text.text())
            self.shot("8-update-banner", win)
            win.home.update_btn.click()
            self.assertIs(win.stack.currentWidget(), win.updating)
            self.wait_for(lambda: win.updating.restart_btn.isVisible())
            self.shot("9-update-done", win)
            self.assertEqual(target.read_bytes(), NEW_APP)
            self.assertFalse(win.home.banner.isVisible())
            win.updating.restart_btn.click()
            self.assertEqual(restarted, [target])
            win.close()
        finally:
            updater.self_path, updater.restart = orig
            del os.environ["DECKHAND_UPDATE_BASE"]
            server.close()

    def test_uninstall_from_installed_programs(self):
        os.environ["FAKE_TO_D"] = "1"
        inst = self.add_installer("Cool Game Setup.exe", 10)
        self.win.start_install(inst)
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
        self.win.go_home()
        self.assertTrue(self.win.home.manage_btn.isVisible())
        self.assertIn("(1)", self.win.home.manage_btn.text())
        self.win.home.manage_btn.click()
        page = self.win.installed
        self.assertIs(self.win.stack.currentWidget(), page)
        self.assertEqual(page.list.count(), 1)
        self.wait_for(lambda: "MB" in page.list.item(0).text())  # size filled in from a background thread
        self.assertIn("In Steam", page.list.item(0).text())
        self.shot("10-installed", self.win)
        app = core.Library(self.paths).load()[0]
        self.answers = [0, 2]  # Uninstall; Keep
        page._activate(page.list.item(0))
        self.assertTrue(Path(app.prefix).exists())
        self.answers = [0, 0]  # Uninstall; Uninstall
        page._activate(page.list.item(0))
        self.assertIn("Uninstall Cool Game?", self.asked)
        self.wait_for(lambda: not Path(app.prefix).exists())  # deleted in the background
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.home)
        self.assertFalse(Path(app.extra_dirs[0]).exists())
        self.assertEqual(core.Library(self.paths).load(), [])
        self.assertIs(self.win.stack.currentWidget(), self.win.home)
        self.assertFalse(self.win.home.manage_btn.isVisible())

    def test_fix_it_installs_a_runtime_into_the_program(self):
        from deckhand import updater
        from tests.test_addons import FakeResponse
        self.win.start_install(self.add_installer("Cool Game Setup.exe", 10))
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
        self.win.show_installed()
        page = self.win.installed
        orig = updater._open
        updater._open = lambda url, timeout, headers=None: FakeResponse(b"MZ" + b"\0" * 64)
        try:
            self.answers = [1, 0]  # Fix it; Visual C++ runtimes
            page._activate(page.list.item(0))
            self.assertEqual(self.asked[-2:], ["Cool Game", "Fix Cool Game"])
            self.wait_for(lambda: not self.win.busy_with_quietly("fix"))
        finally:
            updater._open = orig
        app = core.Library(self.paths).load()[0]
        self.assertIn("fixed:vcrun", app.options)
        self.assertIn("vc_redist.x64.exe /install", (Path(app.prefix) / "proton-args.txt").read_text())

    def test_saves_backed_up_on_uninstall_come_back_after_reinstalling(self):
        from deckhand import saves
        inst = self.add_installer("Cool Game Setup.exe", 10)
        self.win.start_install(inst)
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
        self.assertFalse(self.win.done.restore_btn.isVisible())  # no backups yet
        app = core.Library(self.paths).load()[0]
        save = Path(app.prefix) / "pfx/drive_c/users/steamuser/Saved Games/Cool/slot1.sav"
        save.parent.mkdir(parents=True)
        save.write_text("level 9")
        self.win.show_installed()
        self.answers = [0, 1]  # Uninstall; Back up saves, then uninstall
        self.win.installed._activate(self.win.installed.list.item(0))
        self.wait_for(lambda: core.Library(self.paths).load() == [])
        self.assertEqual(len(saves.backups(self.paths, "Cool Game")), 1)
        # Installed again: the Done page offers them back.
        self.win.start_install(inst)
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
        self.assertTrue(self.win.done.restore_btn.isVisible())
        self.answers = [0]  # the newest backup
        self.win.done.restore_btn.click()
        app = core.Library(self.paths).load()[0]
        restored = Path(app.prefix) / "pfx/drive_c/users/steamuser/Saved Games/Cool/slot1.sav"
        self.wait_for(lambda: restored.is_file())
        self.assertEqual(restored.read_text(), "level 9")
        self.wait_for(lambda: not self.win.done.restore_btn.isVisible())

    def test_add_back_to_steam_from_installed_programs(self):
        inst = self.add_installer("Cool Game Setup.exe", 10)
        self.win.start_install(inst)
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
        app = core.Library(self.paths).load()[0]
        core.remove_steam_shortcut(app.steam_appid, [self.steam])  # e.g. Steam dropped it on restart
        self.win.show_installed()
        page = self.win.installed
        self.assertIn("Not in Steam", page.list.item(0).text())
        self.answers = [0]  # "Add to Steam"
        page._activate(page.list.item(0))
        self.assertEqual(self.asked[-1], "Cool Game")
        self.wait_for(lambda: "In Steam" in page.list.item(0).text() and "Not in" not in page.list.item(0).text())
        self.assertTrue(core.in_steam(core.Library(self.paths).load()[0], [self.steam]))

    def test_with_steam_running_the_program_is_sent_once_and_followed(self):
        sent = []
        orig_request, orig_wait = core.request_steam_add, core.STEAM_ADD_WAIT
        core.request_steam_add = lambda f: (sent.append(f), True)[1]  # Steam takes it, saves its list later
        core.STEAM_ADD_WAIT = 0.3
        core.steam_is_running = lambda: True
        vdf = self.steam / "userdata/12345/config/shortcuts.vdf"
        try:
            self.win.start_install(self.add_installer("Cool Game Setup.exe", 10))
            self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
            self.assertIn("sent to Steam", self.win.done.heading.text())
            self.assertEqual(len(sent), 1)
            self.assertFalse(vdf.exists())  # Steam's own list is never edited while it runs
            self.win.show_installed()
            page = self.win.installed
            self.assertIn("Sent to Steam", page.list.item(0).text())
            self.answers = [-1]  # Back: it's probably there already
            page._activate(page.list.item(0))
            self.assertEqual(self.asked[-1], "Cool Game")
            self.pump(10)
            self.assertEqual(len(sent), 1)
            # Steam saves its list, with the shortcut under an id of its own: Deckhand follows it.
            app = core.Library(self.paths).load()[0]
            vdf.write_bytes(core.vdf_dumps({"shortcuts": {"0": {
                "appid": -1234, "AppName": "Cool Game", "Exe": f'"{app.launcher}"'}}}))
            self.win.sync_steam_ids()
            app = core.Library(self.paths).load()[0]
            self.assertEqual((app.steam_appid, app.steam_added), (-1234 & 0xFFFFFFFF, "live"))
            self.assertTrue(app.artwork)
            self.assertTrue(all(Path(a).name.startswith(str(app.steam_appid)) for a in app.artwork))
            page.refresh()
            self.assertIn("In Steam", page.list.item(0).text())
        finally:
            core.request_steam_add, core.STEAM_ADD_WAIT = orig_request, orig_wait

    def test_leftovers_from_interrupted_installs_are_offered_for_deletion(self):
        keep, gone = self.paths.prefixes / "kept-one", self.paths.prefixes / "cool-game"
        for d in (keep, gone):
            (d / "pfx").mkdir(parents=True)
            (d / "pfx/big").write_bytes(b"x" * 1000)
        self.answers = [1]  # Keep: never asked about these again
        self.win.check_leftovers()
        self.wait_for(lambda: "Unfinished installs" in self.asked)
        self.assertTrue(keep.exists() and gone.exists())
        self.asked.clear()
        self.win.check_leftovers()
        self.pump(20)
        self.assertEqual(self.asked, [])
        self.paths.remember(kept_leftovers=["kept-one"])
        self.answers = [0]  # Delete
        self.win.check_leftovers()
        self.wait_for(lambda: not gone.exists())
        self.assertTrue(keep.exists())

    def test_unfinished_install_is_finished_from_installed_programs(self):
        job = core.Installer(self.add_installer("Cool Game Setup.exe", 10), self.paths,
                             steam_roots_override=[self.steam])
        pending = job.run()
        job.close()  # Deckhand was closed on the pick screen
        self.win.go_home()
        self.assertTrue(self.win.home.manage_btn.isVisible())
        self.assertIn("1 unfinished", self.win.home.manage_btn.text())
        self.win.show_installed()
        page = self.win.installed
        self.assertEqual(page.list.count(), 1)
        self.assertIn("Unfinished install", page.list.item(0).text())
        self.answers = [0]  # Finish setup
        page._activate(page.list.item(0))
        self.wait_for(lambda: self.win.stack.currentWidget() in (self.win.done, self.win.pick))
        if self.win.stack.currentWidget() is self.win.pick:
            self.win.pick.use()
            self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
        app = core.Library(self.paths).load()[0]
        self.assertEqual(app.prefix, str(pending.compat_dir))
        self.assertTrue(core.in_steam(app, [self.steam]))

    def test_streaming_service_added_from_its_page_and_removed_again(self):
        from tests.test_streaming import FAKE_FLATPAK
        bindir = self.tmp / "flatpak-bin"
        bindir.mkdir()
        (bindir / "flatpak").write_text(FAKE_FLATPAK)
        (bindir / "flatpak").chmod(0o755)
        os.environ.update(PATH=f"{bindir}:{os.environ['PATH']}", FAKE_FLATPAK_LOG=str(self.tmp / "fp.log"),
                          FAKE_FLATPAK_DB=str(self.tmp / "fp.db"))
        self.win.go_home()
        for _ in range(2):
            self.win.on_action("rb")  # R1 twice: Install → Stores → Stream
        self.assertIs(self.win.stack.currentWidget(), self.win.streaming)
        self.assertTrue(self.win.stations["stream"].isChecked())
        page = self.win.streaming
        self.wait_for(lambda: page.installed is not None)
        self.assertIn("installs Google Chrome", page.list.item(0).text())
        self.assertIn("Google Chrome ✗", page.on_deck.text())
        self.answers = [0]  # Add to Steam
        page._activate(page.list.item(0))
        self.assertEqual(self.asked[-1], "Add Xbox Cloud Gaming to Steam?")
        self.wait_for(lambda: "In Steam" in page.list.item(0).text())
        self.wait_for(lambda: "Google Chrome ✓" in page.list.item(0).text())  # re-checked after
        self.assertIn("Google Chrome ✓", page.on_deck.text())
        app = core.Library(self.paths).load()[0]
        self.assertTrue(app.artwork)
        self.win.go_home()
        self.assertTrue(self.win.home.manage_btn.isVisible())
        self.win.show_installed()
        self.assertEqual(self.win.installed.list.count(), 1)
        item = self.win.installed.list.item(0).text()
        self.assertIn("Xbox Cloud Gaming", item)
        self.assertIn("Streaming service", item)
        self.assertIn("In Steam", item)
        self.answers = [0, 0]  # Remove; Remove (confirm)
        self.win.installed._activate(self.win.installed.list.item(0))
        self.wait_for(lambda: core.Library(self.paths).load() == [])
        self.wait_for(lambda: self.win.installed.list.count() == 0)
        self.assertTrue(self.win.installed.empty.isVisible())
        self.assertEqual(core.steam_shortcuts([self.steam]), [])

    def test_old_streaming_launchers_are_rewritten_on_start(self):
        from tests.test_streaming import FAKE_FLATPAK
        bindir = self.tmp / "flatpak-bin"
        bindir.mkdir()
        (bindir / "flatpak").write_text(FAKE_FLATPAK)
        (bindir / "flatpak").chmod(0o755)
        db = self.tmp / "fp.db"
        db.write_text("com.google.Chrome\n")
        os.environ.update(PATH=f"{bindir}:{os.environ['PATH']}", FAKE_FLATPAK_LOG=str(self.tmp / "fp.log"),
                          FAKE_FLATPAK_DB=str(db))
        self.paths.launchers.mkdir(parents=True, exist_ok=True)
        launcher = self.paths.launchers / "stream-xbox-cloud.sh"
        old_args = ("exec flatpak run com.google.Chrome --kiosk --start-fullscreen --window-size=1280,800"
                    " --force-device-scale-factor=1.25 https://www.xbox.com/play")
        launcher.write_text(f"#!/bin/bash\n{old_args}\n")
        app = core.App(id="stream-xbox-cloud", name="Xbox Cloud Gaming", kind="stream", exe=str(launcher),
                       launcher=str(launcher), prefix="", runtime_name="", runtime_kind="", runtime_path="")
        core.Library(self.paths).upsert(app)
        self.win.refresh_launchers()
        self.wait_for(lambda: "1024,640" in launcher.read_text())
        self.assertIn("--window-size=1024,640", launcher.read_text())
        self.assertNotIn("1280,800", launcher.read_text())
        # flatpak listing nothing must not downgrade a working launcher to Chrome.
        db.write_text("")
        launcher.write_text(f"#!/bin/bash\n{old_args}\n")
        self.win.refresh_launchers()
        self.wait_for(lambda: not self.win.workers)
        self.assertIn("1280,800", launcher.read_text())

    def test_xbox_dialog_says_which_browser_each_choice_installs(self):
        from tests.test_streaming import FAKE_FLATPAK
        from deckhand import streaming
        from deckhand.widgets import Sheet
        bindir = self.tmp / "flatpak-bin"
        bindir.mkdir()
        (bindir / "flatpak").write_text(FAKE_FLATPAK)
        (bindir / "flatpak").chmod(0o755)
        log = self.tmp / "fp.log"
        os.environ.update(PATH=f"{bindir}:{os.environ['PATH']}", FAKE_FLATPAK_LOG=str(log),
                          FAKE_FLATPAK_DB=str(self.tmp / "fp.db"))
        orig = streaming.install_better_xcloud
        streaming.install_better_xcloud = lambda dest=None, fetch=None: "1.0"
        texts = []
        Sheet.ask = staticmethod(lambda parent, title, text="", *a, **k: (texts.append(text), 1)[1])
        try:
            self.win.show_streaming()
            page = self.win.streaming
            self.wait_for(lambda: page.installed is not None)
            self.assertIn("or Chromium with Better xCloud", page.list.item(0).text())
            page._activate(page.list.item(0))  # answers 1: Add with Better xCloud
            self.assertIn("Add to Steam: opens in Google Chrome (Deckhand installs it", texts[-1])
            self.assertIn("Add with Better xCloud: opens in Chromium (Deckhand installs it", texts[-1])
            self.wait_for(lambda: "Better xCloud on (Chromium)" in page.list.item(0).text())
            installs = [c for c in log.read_text().splitlines() if c.startswith("install")]
            self.assertEqual(installs, ["install --user -y --noninteractive flathub org.chromium.Chromium"])
        finally:
            streaming.install_better_xcloud = orig

    def test_services_stores_and_add_ons_show_their_logos(self):
        from deckhand import app as app_mod, artwork
        for page in (self.win.stores, self.win.streaming, self.win.addons):
            self.win.go(page)
            for i in range(page.list.count()):
                it = page.list.item(i)
                self.assertIsNotNone(artwork.logo(it.data(Qt.ItemDataRole.UserRole)), it.text())
        # (a key without a logo still gets its initials)
        self.assertFalse(app_mod.logo_icon("nope", "No Logo", "#123456").isNull())
        self.shot("streaming")

    def test_a_service_added_before_logos_gets_its_logo(self):
        from deckhand import app as app_mod, streaming
        svc = streaming.service("moonlight")
        installed = {svc.app}
        launcher = streaming.write_launcher(svc, self.paths, installed)
        old = core.App(id="stream-moonlight", name="Moonlight", exe=str(launcher), prefix="", runtime_name="",
                       runtime_kind="", runtime_path="", launcher=str(launcher), kind="stream")
        core.add_to_steam(old, [self.steam])
        core.Library(self.paths).upsert(old)
        win = app_mod.MainWindow(self.paths, use_nav=False, check_updates=False)
        try:
            app = core.Library(self.paths).load()[0]
            self.assertEqual(Path(app.icon), self.paths.icons / "stream-moonlight.png")
            self.assertTrue(app.artwork and all(Path(f).is_file() for f in app.artwork))
            self.assertIn(f"Icon={app.icon}", core.desktop_entry_path(app).read_text())
        finally:
            win.close()
            win.deleteLater()

    def test_a_service_already_in_steam_is_shown_and_not_added_twice(self):
        vdf = self.steam / "userdata/12345/config/shortcuts.vdf"
        vdf.write_bytes(core.vdf_dumps({"shortcuts": {"0": {
            "appid": 7, "AppName": "Xbox Cloud Gaming", "Exe": '"flatpak"',
            "LaunchOptions": "run com.microsoft.Edge --kiosk https://www.xbox.com/play"}}}))
        self.win.show_streaming()
        page = self.win.streaming
        self.assertIn("added outside Deckhand", page.list.item(0).text())
        self.answers = [0]  # Keep what I have
        page._activate(page.list.item(0))
        self.pump(10)
        self.assertEqual(self.asked[-1], "Xbox Cloud Gaming")
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)
        self.assertEqual(core.Library(self.paths).load(), [])

    def test_installing_something_steam_already_has_warns(self):
        vdf = self.steam / "userdata/12345/config/shortcuts.vdf"
        vdf.write_bytes(core.vdf_dumps({"shortcuts": {"0": {"appid": 7, "AppName": "Cool Game", "Exe": '"/x.exe"'}}}))
        texts = []
        from deckhand.widgets import Sheet
        Sheet.ask = staticmethod(lambda parent, title, text="", *a, **k: (texts.append(text), 1)[1])
        self.win.confirm_install(self.add_installer("Cool Game Setup.exe", 10))
        self.assertIn("already has “Cool Game”", texts[-1])

    def test_addons_page_shows_status_and_downloads_emudeck(self):
        from deckhand import addons
        from tests.test_addons import FakeResponse
        self.win.go_home()
        for _ in range(3):
            self.win.on_action("rb")  # R1 three times: Install → Stores → Stream → Add-ons
        page = self.win.addons
        self.assertIs(self.win.stack.currentWidget(), page)
        self.assertTrue(self.win.stations["addons"].isChecked())
        self.assertIn("Decky Loader", page.list.item(0).text())
        self.assertIn("Not installed", page.list.item(1).text())
        # Game Mode: Decky's installer needs Desktop Mode.
        os.environ["XDG_CURRENT_DESKTOP"] = "gamescope"
        self.answers = [1]
        page._activate(page.list.item(0))
        self.assertEqual(self.asked[-1], "Decky Loader")
        # EmuDeck: download (network faked), then it's offered to open — in Desktop Mode only.
        orig_release, orig_open = addons.emudeck_release, addons.updater._open
        addons.emudeck_release = lambda fetch=None: ("2.4.7", "https://example.invalid/EmuDeck.AppImage")
        addons.updater._open = lambda url, timeout, headers=None: FakeResponse(b"\x7fELF" + b"\0" * 100)
        try:
            self.answers = [0, 1]  # Download EmuDeck; (Game Mode) not now
            page._activate(page.list.item(1))
            self.wait_for(lambda: (self.home / "Applications/EmuDeck.AppImage").exists())
            self.wait_for(lambda: not self.win.busy_with_quietly("addon"))  # (then it offers to open it)
            self.assertEqual(self.asked[-1], "EmuDeck")
            self.wait_for(lambda: "Downloaded" in page.list.item(1).text())
        finally:
            addons.emudeck_release, addons.updater._open = orig_release, orig_open

    def test_stores_page_installs_a_windows_store_under_its_own_name(self):
        from deckhand import stores
        from tests.test_addons import FakeResponse
        self.win.go_home()
        self.win.on_action("rb")  # R1: Install → Stores
        page = self.win.stores
        self.assertIs(self.win.stack.currentWidget(), page)
        self.assertTrue(self.win.stations["stores"].isChecked())
        rows = [page.list.item(i).text().split("\n")[0] for i in range(page.list.count())]
        self.assertEqual(rows, [s.name for s in stores.STORES])
        row = rows.index("Battle.net")
        self.assertIn("Not installed", page.list.item(row).text())
        self.shot("stores")
        orig = stores.updater._open
        stores.updater._open = lambda url, timeout, headers=None: FakeResponse(b"MZ" + b"\0" * 100)
        try:
            self.answers = [0]  # Download and install
            page._activate(page.list.item(row))
            self.assertEqual(self.asked[-1], "Install Battle.net?")
            self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
        finally:
            stores.updater._open = orig
        self.assertEqual(self.win.progress.heading.text(), "Installing Battle.net")
        self.assertIn("Battle.net is in your Steam library", self.win.done.heading.text())
        app = core.Library(self.paths).load()[0]
        installer = stores.download_path(stores.store("battlenet"), self.paths)
        self.assertEqual((app.kind, app.name, app.installer), ("program", "Battle.net", str(installer)))
        self.assertTrue(core.in_steam(app, [self.steam]))
        self.assertFalse(installer.exists())  # Deckhand's own download: deleted once installed
        self.assertFalse(self.win.done.delete_btn.isVisible())
        self.win.show_stores()
        self.assertIn("Installed  ·  In Steam", page.list.item(row).text())
        self.answers = [1]  # Close (the other choice: Uninstall)
        page._activate(page.list.item(row))
        self.assertEqual(self.asked[-1], "Battle.net")
        self.assertEqual(len(core.Library(self.paths).load()), 1)

    def test_a_cancelled_store_install_leaves_no_download(self):
        from deckhand import stores
        from tests.test_addons import FakeResponse
        os.environ["FAKE_SLEEP"] = "3"
        orig = stores.updater._open
        stores.updater._open = lambda url, timeout, headers=None: FakeResponse(b"MZ" + b"\0" * 100)
        try:
            self.win.show_stores()
            self.answers = [0]  # Download and install
            self.win.stores._activate(self.win.stores.list.item(2))  # EA app
            self.wait_for(lambda: "installer" in self.win.progress.status.text())
        finally:
            stores.updater._open = orig
        self.assertEqual(self.win.progress.heading.text(), "Installing EA app")
        installer = stores.download_path(stores.store("ea"), self.paths)
        self.assertTrue(installer.exists())
        self.answers = [1]  # Cancel install
        self.win.cancel_install()
        self.wait_for(lambda: self.win.thread is None)
        self.assertFalse(installer.exists())
        self.assertEqual(core.Library(self.paths).load(), [])

    def test_a_failed_store_download_is_explained(self):
        from deckhand import stores

        def unreachable(url, timeout, headers=None):
            raise stores.updater.UpdateError("HTTP 503 for " + url)

        orig = stores.updater._open
        stores.updater._open = unreachable
        try:
            self.win.show_stores()
            page = self.win.stores
            self.answers = [0, 0]  # Download and install; Close
            page._activate(page.list.item(1))
            self.wait_for(lambda: self.asked[-1] == "Couldn't set up Battle.net")
        finally:
            stores.updater._open = orig
        self.assertIs(self.win.stack.currentWidget(), page)
        self.assertEqual(core.Library(self.paths).load(), [])

    def test_stores_page_adds_heroic_and_removes_it_again(self):
        from tests.test_streaming import FAKE_FLATPAK
        bindir = self.tmp / "flatpak-bin"
        bindir.mkdir()
        (bindir / "flatpak").write_text(FAKE_FLATPAK)
        (bindir / "flatpak").chmod(0o755)
        os.environ.update(PATH=f"{bindir}:{os.environ['PATH']}", FAKE_FLATPAK_LOG=str(self.tmp / "fp.log"),
                          FAKE_FLATPAK_DB=str(self.tmp / "fp.db"))
        self.win.show_stores()
        page = self.win.stores
        self.wait_for(lambda: page.installed is not None)
        self.assertIn("Not added yet  ·  installs from Flathub", page.list.item(0).text())
        self.answers = [0]  # Add to Steam
        page._activate(page.list.item(0))
        self.assertEqual(self.asked[-1], "Add Heroic Games Launcher to Steam?")
        self.wait_for(lambda: "In Steam  ·  app installed ✓" in page.list.item(0).text())
        app = core.Library(self.paths).load()[0]
        self.assertEqual(app.kind, "store")
        self.assertTrue(app.artwork)
        self.win.go_home()
        self.assertTrue(self.win.home.manage_btn.isVisible())
        self.win.show_stores()
        self.answers = [0, 0]  # Remove it; Remove (confirm)
        page._activate(page.list.item(0))
        self.assertEqual(self.asked[-1], "Remove Heroic Games Launcher?")
        self.wait_for(lambda: core.Library(self.paths).load() == [])
        self.assertEqual(core.steam_shortcuts([self.steam]), [])
        self.wait_for(lambda: "Not added yet" in page.list.item(0).text())

    def use_fake_flatpak(self):
        from tests.test_streaming import FAKE_FLATPAK
        bindir = self.tmp / "flatpak-bin"
        bindir.mkdir()
        (bindir / "flatpak").write_text(FAKE_FLATPAK)
        (bindir / "flatpak").chmod(0o755)
        os.environ.update(PATH=f"{bindir}:{os.environ['PATH']}", FAKE_FLATPAK_LOG=str(self.tmp / "fp.log"),
                          FAKE_FLATPAK_DB=str(self.tmp / "fp.db"))

    def test_addons_page_installs_flathub_apps_and_ge_proton(self):
        import hashlib
        import json
        from deckhand import addons, proton, updater
        from tests.test_addons import FakeResponse
        from tests.test_proton import ge_tarball
        self.use_fake_flatpak()
        self.win.show_addons()
        page = self.win.addons
        self.wait_for(lambda: page.installed is not None)
        ids = [a.id for a in addons.ADDONS]
        item = page.list.item
        discord, flatseal, ge = ids.index("discord"), ids.index("flatseal"), ids.index("ge-proton")
        self.assertIn("Not installed", item(discord).text())
        # A Game Mode app: installed and added to Steam.
        self.answers = [0]  # Install
        page._activate(item(discord))
        self.assertEqual(self.asked[-1], "Install Discord?")
        self.wait_for(lambda: "Installed  ·  In Steam" in item(discord).text())
        # A Desktop Mode tool: installed only.
        self.answers = [0]
        page._activate(item(flatseal))
        self.wait_for(lambda: not self.win.busy_with_quietly("addon") and page.installed is not None
                      and "Not installed" not in item(flatseal).text())
        self.assertEqual([a.id for a in core.Library(self.paths).load()], ["addon-discord"])
        # Uninstalling Discord removes its shortcut and the app.
        self.answers = [2, 0]  # Uninstall; Uninstall (confirm)
        page._activate(item(discord))
        self.assertEqual(self.asked[-1], "Uninstall Discord?")
        self.wait_for(lambda: page.installed is not None and "Not installed" in item(discord).text())
        self.assertEqual(core.Library(self.paths).load(), [])
        self.assertEqual(core.steam_shortcuts([self.steam]), [])
        # GE-Proton, from (a faked) GitHub.
        tar = ge_tarball()
        files = {proton.RELEASES_API: json.dumps({"assets": [
                     {"name": "GE-Proton10-99.tar.gz", "browser_download_url": "https://x.invalid/t", "size": len(tar)},
                     {"name": "GE-Proton10-99.sha512sum", "browser_download_url": "https://x.invalid/s"}]}).encode(),
                 "https://x.invalid/t": tar, "https://x.invalid/s": hashlib.sha512(tar).hexdigest().encode()}
        orig = updater._open
        updater._open = lambda url, timeout, headers=None: FakeResponse(files[url])
        try:
            self.assertIn("Installed (GE-Proton9-20)", item(ge).text())
            self.answers = [0]  # Download and install
            page._activate(item(ge))
            self.wait_for(lambda: "Installed (GE-Proton10-99)" in item(ge).text())
            self.assertEqual(self.asked[-1], "Install GE-Proton10-99?")
        finally:
            updater._open = orig

    def test_steamgriddb_key_is_asked_once_and_art_replaces_deckhands(self):
        from deckhand import steamgriddb
        from deckhand.widgets import Sheet
        png = b"\x89PNG\r\n\x1a\nfrom-sgdb"
        looked_up, keys = [], []
        orig_art, orig_check, orig_ask_text = steamgriddb.art, steamgriddb.check_key, Sheet.ask_text
        steamgriddb.art = lambda name, key: (looked_up.append((name, key)), {"p": png, "_hero": png})[1]
        steamgriddb.check_key = lambda key: keys.append(key)
        Sheet.ask_text = staticmethod(lambda parent, title, *a, **k: (self.asked.append(title), (0, "  abc123  "))[1])
        try:
            self.win.start_install(self.add_installer("Cool Game Setup.exe", 10))
            self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
            app = core.Library(self.paths).load()[0]
            grid = self.steam / "userdata/12345/config/grid"
            self.assertNotEqual((grid / f"{app.steam_appid}p.png").read_bytes(), png)  # drawn by Deckhand
            self.win.steamgriddb_menu()  # no key yet: asks for one, checks it, then gets art for everything
            self.assertEqual(self.asked[-1], "SteamGridDB art")
            self.wait_for(lambda: keys == ["abc123"])
            self.wait_for(lambda: (grid / f"{app.steam_appid}p.png").read_bytes() == png)
            self.assertEqual(self.paths.state()["steamgriddb_key"], "abc123")
            self.assertEqual(looked_up, [("Cool Game", "abc123")])
            self.assertEqual((grid / f"{app.steam_appid}_hero.png").read_bytes(), png)
            self.assertNotEqual((grid / f"{app.steam_appid}_logo.png").read_bytes(), png)  # none on SGDB: drawn
            self.wait_for(lambda: not self.win.busy_with_quietly("art"))
            app = core.Library(self.paths).load()[0]
            self.assertEqual(len(app.artwork), 4)
            core.uninstall(app, self.paths, roots=[self.steam])
            self.assertEqual(list(grid.iterdir()), [])  # it's ours: removed with the program
        finally:
            steamgriddb.art, steamgriddb.check_key = orig_art, orig_check
            Sheet.ask_text = staticmethod(orig_ask_text)

    def test_free_up_space_deletes_the_safe_leftovers_first(self):
        from deckhand import cleanup
        apps = self.steam / "steamapps"
        (apps / "libraryfolders.vdf").write_text(f'"libraryfolders" {{ "0" {{ "path" "{self.steam}" '
                                                 '"apps" { "100" "1" } } }')
        for kind in ("shadercache", "compatdata"):
            for appid in (100, 555):
                (apps / kind / str(appid)).mkdir(parents=True)
                (apps / kind / str(appid) / "f").write_bytes(b"x" * 2048)
        orig = cleanup.name_games
        cleanup.name_games = lambda items, fetch=None, limit=6: [setattr(i, "name", "Old Game") for i in items]
        texts = []
        from deckhand.widgets import Sheet
        Sheet.ask = staticmethod(lambda parent, title, text="", *a, **k: (texts.append(text), 0)[1])
        try:
            self.win.free_up_space()
            self.wait_for(lambda: not (apps / "shadercache/555").exists())
        finally:
            cleanup.name_games = orig
        self.assertIn("Old Game (2 KB)", texts[0])
        self.assertTrue((apps / "compatdata/555").exists())  # may hold saves: only with "Delete all of it"
        self.assertTrue((apps / "shadercache/100").exists() and (apps / "compatdata/100").exists())

    def test_remove_duplicate_shortcuts_only_with_steam_closed(self):
        vdf = self.steam / "userdata/12345/config/shortcuts.vdf"
        e = {"appid": 1, "AppName": "Emu", "Exe": '"/emu"', "StartDir": '"/"', "LaunchOptions": ""}
        vdf.write_bytes(core.vdf_dumps({"shortcuts": {"0": e, "1": dict(e), "2": dict(e)}}))
        core.steam_is_running = lambda: True
        self.win.remove_duplicates()
        self.assertEqual(self.asked[-1], "Close Steam first")
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 3)
        core.steam_is_running = lambda: False
        self.answers = [0]  # Remove duplicates
        self.win.remove_duplicates()
        self.assertEqual(self.asked[-1], "Remove duplicate shortcuts?")
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)
        self.win.remove_duplicates()
        self.assertEqual(self.asked[-1], "No duplicates")

    def test_install_screen_shows_what_has_been_written(self):
        page = self.win.progress
        inst = self.add_installer("Big Game Setup.exe", 10)
        page.reset(inst)
        page.expected = 4 << 30  # a 4 GB installer
        page.on_status("Running the installer", "installer")
        self.assertIsNotNone(page.meter)
        free = {"v": 100 << 30}
        page.meter = core.WriteMeter([self.home], now=0.0, free=lambda p: free["v"])
        free["v"] -= 1 << 30
        page._show_written(*page.meter.sample(now=10.0))
        self.assertIn("1.0 GB written", page.written.text())
        self.assertIn("about 25%", page.written.text())
        self.assertEqual((page.bar.maximum(), page.bar.value()), (100, 25))
        page.meter.last_growth -= 120  # nothing written for two minutes: probably waiting for the user
        page._show_written(*page.meter.sample(now=11.0))
        self.assertFalse(page.idle.isHidden())
        self.assertIn("waiting for you", page.idle.text())
        page.on_status("Finding the installed program…", "scan")
        self.assertIn("installed", page.written.text())
        self.assertTrue(page.idle.isHidden())
        self.assertEqual(page.bar.maximum(), 0)  # busy again
        page.stop()

    def test_app_icon_is_installed(self):
        from deckhand import app as app_mod
        app_mod.ensure_app_icon()
        for n in (32, 256, 512):
            self.assertTrue((self.home / f".local/share/icons/hicolor/{n}x{n}/apps/deckhand.png").exists(), n)
        self.assertFalse(app_mod.app_icon().isNull())

    def test_long_paths_never_widen_the_window(self):
        deep = self.downloads.joinpath(*[f"A Rather Long Folder Name Number {i}" for i in range(8)])
        deep.mkdir(parents=True)
        inst = deep / "setup.exe"
        inst.write_bytes(b"MZ")
        os.environ["FAKE_SLEEP"] = "1"
        self.win.start_install(inst)
        self.wait_for(lambda: "installer" in self.win.progress.status.text())
        self.pump(10)
        self.assertLessEqual(self.win.width(), 1280)
        cancel = self.win.progress.cancel_btn
        self.assertLessEqual(cancel.mapTo(self.win, cancel.rect().bottomRight()).x(), self.win.width())
        self.assertEqual(self.win.progress.source.text(), str(inst))  # full path kept (and in tooltip)
        self.shot("11-long-path", self.win)
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)

    def test_reinstall_and_low_space_are_mentioned(self):
        inst = self.add_installer("Cool Game Setup.exe", 10)
        self.win.start_install(inst)
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.done)
        texts = []
        from deckhand.widgets import Sheet
        Sheet.ask = staticmethod(lambda parent, title, text="", *a, **k: (texts.append(text), 1)[1])
        self.win.confirm_install(inst)
        self.assertIn("already installed this as Cool Game", texts[-1])
        orig = core.free_space
        core.free_space = lambda p: 5  # bytes
        try:
            self.win.confirm_install(inst)
        finally:
            core.free_space = orig
        self.assertIn("Free space looks tight", texts[-1])

    def test_leaving_the_pick_screen_asks_first(self):
        os.environ["FAKE_NOTHING"] = "1"
        self.win.start_install(self.add_installer("Tool.exe", 10))
        self.wait_for(lambda: self.win.stack.currentWidget() is self.win.pick)
        prefix = self.win.pending.compat_dir
        self.answers = [1]  # Keep
        self.win.pick.back()
        self.assertTrue(prefix.exists())
        self.assertIs(self.win.stack.currentWidget(), self.win.pick)
        self.answers = [0]  # Delete it
        self.win.pick.back()
        self.assertFalse(prefix.exists())
        self.assertIs(self.win.stack.currentWidget(), self.win.home)

    def test_sheet_is_an_overlay_that_confines_focus(self):
        from deckhand.widgets import Sheet
        self.win.go_home()
        results = []

        def answer():
            sheet = Sheet.current
            results.append(sheet is not None and sheet.parent() is self.win)
            results.append(self.win.centralWidget().isEnabled())  # everything behind is disabled
            if sheet is None:
                return
            sheet.buttons[0].setFocus()
            self.pump()
            QTest.keyClick(sheet.buttons[0], Qt.Key.Key_Right)  # D-pad moves between the sheet's buttons
            self.pump()
            results.append(QApplication.focusWidget() is sheet.buttons[1])
            QTest.keyClick(sheet.buttons[1], Qt.Key.Key_Escape)  # B closes it
            if Sheet.current is sheet:  # safety net so a failure can't hang the test
                sheet.reject()

        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, answer)
        choice = self._orig_ask(self.win, "Question", "?", ("One", "Two"))
        self.assertEqual(results, [True, False, True])
        self.assertEqual(choice, -1)
        self.assertIsNone(Sheet.current)
        self.assertTrue(self.win.centralWidget().isEnabled())

    def test_second_launch_hands_installer_to_the_open_window(self):
        from deckhand.app import SingleInstance
        name = f"deckhand-test-{os.getpid()}"
        got = []
        inst = SingleInstance()
        inst.listen(got.append, name=name)
        self.assertTrue(SingleInstance.hand_off(["/home/deck/Downloads/setup.exe"], name=name))
        self.wait_for(lambda: got)
        self.assertEqual(got, [["/home/deck/Downloads/setup.exe"]])
        inst.server.close()
        self.assertFalse(SingleInstance.hand_off(["x"], name=f"{name}-nobody"))

    def test_double_click_picks_once(self):
        from PyQt6.QtTest import QTest
        sub = self.downloads / "Folder"
        sub.mkdir()
        (sub / "inner").mkdir()
        self.win.browse_installers()
        lst = self.win.browser.list
        r = lst.visualItemRect(lst.item(0)).center()
        b, v = Qt.MouseButton.LeftButton, lst.viewport()
        QTest.mousePress(v, b, pos=r)
        QTest.mouseRelease(v, b, pos=r)  # click: opens "Folder"
        QTest.mouseDClick(v, b, pos=r)   # the double-click lands on "Up one folder" now
        QTest.mouseRelease(v, b, pos=r)
        self.pump()
        self.assertEqual(self.win.browser.cwd, sub)  # not bounced back up (or into "inner")

    def test_sheet_screenshot(self):
        if not self.shots:
            self.skipTest("screenshots only")
        from deckhand.widgets import Sheet
        s = Sheet(self.win, "Install Cool Game?", "/home/deck/Downloads/Cool Game Setup.exe\n\nInstaller: 3.8 GB"
                  "   ·   Free space: 200.1 GB", ("Install", "Cancel"))
        s.show()
        self.pump(10)
        self.shot("7-sheet", self.win)  # the overlay composited over the window
        s.close()


if __name__ == "__main__":
    unittest.main()
