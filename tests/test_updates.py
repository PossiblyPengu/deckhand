"""Updates for Deckhand and its add-ons, and telling you about them (in Game Mode too) — deckhand/updates.py."""
import os
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deckhand import addons, updater, updates  # noqa: E402
from tests.test_core import Env  # noqa: E402


class TestUpdates(Env):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / "Applications").mkdir(parents=True)
        self.arcadia = self.home / "Applications" / addons.ARCADIA_APPIMAGE
        self.arcadia.write_bytes(b"arcadia 0.1.5")
        self.releases = {}
        self.sent: list[tuple[str, str]] = []

    def fetch(self, url: str) -> bytes:
        for key, body in self.releases.items():
            if key in url:
                return json.dumps(body).encode()
        raise updater.UpdateError(f"offline: {url}")

    def arcadia_release(self, version: str, content: bytes) -> None:
        sha = hashlib.sha256(content).hexdigest()
        self.releases["PossiblyPengu/arcadia"] = {
            "tag_name": f"v{version}",
            "assets": [{"name": addons.ARCADIA_APPIMAGE, "browser_download_url": "https://example/a",
                        "digest": f"sha256:{sha}"}]}

    def sender(self, title: str, body: str) -> bool:
        self.sent.append((title, body))
        return True

    def check(self):
        return updates.check(self.paths, home=self.home, fetch=self.fetch, roots=[self.steam], deckhand_sources=[])

    def test_an_arcadia_that_differs_from_the_latest_release_has_an_update(self):
        self.arcadia_release("0.1.6", b"arcadia 0.1.6")
        found = self.check()
        self.assertEqual([(f.id, f.latest) for f in found], [("arcadia", "0.1.6")])

    def test_the_latest_arcadia_has_none(self):
        self.arcadia_release("0.1.5", b"arcadia 0.1.5")
        self.assertEqual(self.check(), [])

    def test_its_checksum_is_remembered_and_made_again_when_the_file_changes(self):
        self.arcadia_release("0.1.6", b"arcadia 0.1.6")
        self.check()
        self.assertEqual(len(self.paths.state()["file_hashes"]), 1)
        new = self.arcadia.with_name("new.AppImage")  # (updated another way, e.g. Arcadia's own Update Arcadia, which
        new.write_bytes(b"arcadia 0.1.6")             # swaps a new file in, as every updater here does)
        os.replace(new, self.arcadia)
        self.assertEqual(self.check(), [])
        self.assertEqual(len(self.paths.state()["file_hashes"]), 1)  # (the old one is let go)

    def test_decky_by_version(self):
        services = self.home / "homebrew/services"
        services.mkdir(parents=True)
        (services / "PluginLoader").write_text("")
        (services / ".loader.version").write_text("v3.1.9\n")
        self.releases["decky-loader"] = {"tag_name": "v3.1.10"}
        found = self.check()
        self.assertEqual([(f.id, f.latest, f.current) for f in found], [("decky", "3.1.10", "3.1.9")])
        (services / ".loader.version").write_text("v3.1.10\n")
        self.assertEqual(self.check(), [])

    def test_what_isnt_installed_or_cant_be_reached_is_skipped(self):
        self.arcadia.unlink()
        errors: list[Exception] = []
        self.assertEqual(updates.check(self.paths, home=self.home, fetch=self.fetch, roots=[self.steam],
                                       deckhand_sources=[], errors=errors), [])
        self.arcadia.write_bytes(b"x")
        self.assertEqual(updates.check(self.paths, home=self.home, fetch=self.fetch, roots=[self.steam],
                                       deckhand_sources=[], errors=errors), [])  # (offline)
        self.assertTrue(errors)

    def test_each_version_is_told_once(self):
        found = [updates.Found("arcadia", "Arcadia", "0.1.6")]
        self.assertEqual(len(updates.notify(self.paths, found, self.sender)), 1)
        self.assertEqual(self.sent, [("Arcadia 0.1.6 is out", "Open Deckhand and choose Games → Arcadia to update it.")])
        self.assertEqual(updates.notify(self.paths, found, self.sender), [])
        self.assertEqual(len(self.sent), 1)
        newer = [updates.Found("arcadia", "Arcadia", "0.1.7")]
        self.assertEqual(len(updates.notify(self.paths, newer, self.sender)), 1)

    def test_several_updates_in_one_notification(self):
        found = [updates.Found("deckhand", "Deckhand", "4.7.0"), updates.Found("arcadia", "Arcadia", "0.1.6"),
                 updates.Found("ge-proton", "GE-Proton", "GE-Proton10-26")]
        updates.notify(self.paths, found, self.sender)
        self.assertEqual(self.sent, [("3 updates for your Deck", "Deckhand 4.7.0, Arcadia 0.1.6 and "
                                      "GE-Proton GE-Proton10-26. Open Deckhand to update them.")])

    def test_with_no_notification_service_it_tries_again_next_time(self):
        found = [updates.Found("arcadia", "Arcadia", "0.1.6")]
        self.assertEqual(updates.notify(self.paths, found, lambda _t, _b: False), [])
        self.assertEqual(len(updates.notify(self.paths, found, self.sender)), 1)

    def test_the_background_check_remembers_and_notifies_unless_turned_off(self):
        self.arcadia_release("0.1.6", b"arcadia 0.1.6")
        lines: list[str] = []
        updates.run_check(self.paths, out=lines.append, sender=self.sender, home=self.home, fetch=self.fetch,
                          roots=[self.steam], deckhand_sources=[])
        self.assertEqual([f.id for f in updates.available(self.paths)], ["arcadia"])
        self.assertEqual(lines, ["Update: Arcadia 0.1.6"])
        self.assertEqual(len(self.sent), 1)
        self.paths.remember(update_notifications=False, updates_notified=[])
        updates.run_check(self.paths, out=lines.append, sender=self.sender, home=self.home, fetch=self.fetch,
                          roots=[self.steam], deckhand_sources=[])
        self.assertEqual(len(self.sent), 1)  # (found and shown in Deckhand, but no notification)

    def test_after_an_update_it_is_no_longer_waiting(self):
        updates.remember(self.paths, [updates.Found("deckhand", "Deckhand", "4.7.0"),
                                      updates.Found("arcadia", "Arcadia", "0.1.6")])
        updates.forget(self.paths, "arcadia")
        self.assertEqual([f.id for f in updates.available(self.paths)], ["deckhand"])

    def test_the_timer_runs_the_installed_deckhand_and_goes_when_turned_off(self):
        calls: list[tuple] = []

        def systemctl(*args):
            calls.append(args)
            return True

        exe = self.tmp / "bin" / "deck hand"
        self.assertTrue(updates.keep_timer(self.paths, exe, self.home, systemctl))
        units = updates.unit_dir(self.home)
        service = (units / "deckhand-updates.service").read_text()
        self.assertIn(f'ExecStart="{exe}" --check-updates', service)
        self.assertIn("OnUnitActiveSec=6h", (units / "deckhand-updates.timer").read_text())
        self.assertIn(("enable", "--now", "deckhand-updates.timer"), calls)
        calls.clear()
        updates.keep_timer(self.paths, exe, self.home, systemctl)
        self.assertNotIn(("daemon-reload",), calls)  # (unchanged: not reloaded every start)
        self.paths.remember(update_notifications=False)
        updates.keep_timer(self.paths, exe, self.home, systemctl)
        self.assertIn(("disable", "--now", "deckhand-updates.timer"), calls)
        self.assertFalse((units / "deckhand-updates.timer").exists())


if __name__ == "__main__":
    unittest.main()
