"""Uninstalling Deckhand itself."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core, selfremove  # noqa: E402
from tests.test_core import Env  # noqa: E402


class TestUninstallSelf(Env):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.exe = self.home / ".local/bin/deckhand"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_text("app")
        share = self.home / ".local/share"
        self.desktop = share / "applications/deckhand.desktop"
        self.icon = share / "icons/hicolor/256x256/apps/deckhand.png"
        self.timer = self.home / ".config/systemd/user/deckhand-updates.timer"
        for f in (self.desktop, self.icon, self.timer):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x")

    def systemctl(self, *args):
        self.calls.append(args)
        return True

    def test_removes_the_app_its_menu_entry_icon_timer_and_shortcut(self):
        core.add_steam_shortcut("Deckhand", str(self.exe), str(self.exe.parent), roots=[self.steam])
        r = selfremove.uninstall(self.exe, self.home, [self.steam], running=False, systemctl=self.systemctl)
        for f in (self.exe, self.desktop, self.icon, self.timer):
            self.assertFalse(f.exists(), f)
        self.assertEqual(core.steam_shortcuts([self.steam]), [])
        self.assertFalse(r.left_in_steam)
        self.assertIn(("disable", "--now", "deckhand-updates.timer"), self.calls)

    def test_with_steam_open_the_shortcut_is_left_for_steam(self):
        core.add_steam_shortcut("Deckhand", str(self.exe), str(self.exe.parent), roots=[self.steam])
        r = selfremove.uninstall(self.exe, self.home, [self.steam], running=True, systemctl=self.systemctl)
        self.assertTrue(r.left_in_steam)
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)
        self.assertFalse(self.exe.exists())

    def test_what_it_installed_stays(self):
        launcher = self.paths.launchers / "store-heroic.sh"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("#!/bin/bash\n")
        selfremove.uninstall(self.exe, self.home, [self.steam], running=False, systemctl=self.systemctl)
        self.assertTrue(launcher.exists())

    def test_from_source_nothing_is_touched(self):
        r = selfremove.uninstall(None, self.home, [self.steam], running=False, systemctl=self.systemctl)
        self.assertEqual(r.removed, [])
        self.assertEqual(self.calls, [])
        self.assertTrue(self.desktop.exists() and self.timer.exists())
