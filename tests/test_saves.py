"""Save backups from a program's Windows profile, and putting them back."""
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core, saves  # noqa: E402
from tests.test_core import Env  # noqa: E402


class TestSaves(Env):
    def installed(self) -> core.App:
        job = self.job()
        pending = job.run()
        return job.finish(pending, pending.candidates[0].exe)

    def put_saves(self, app: core.App) -> Path:
        users = Path(app.prefix) / "pfx/drive_c/users"
        files = {"steamuser/Documents/My Games/Cool/slot1.sav": b"level 9",
                 "steamuser/AppData/Roaming/Cool/profile.dat": b"me",
                 "steamuser/AppData/LocalLow/Dev/Cool/prefs": b"hard",
                 "steamuser/Saved Games/Cool/auto.sav": b"auto",
                 "Public/Documents/Cool/shared.sav": b"shared",
                 "steamuser/AppData/Local/Cool/Cache/big.bin": b"x" * 1000,  # caches: not saves
                 "steamuser/AppData/Local/Temp/junk.tmp": b"x",
                 "steamuser/AppData/Local/Microsoft/Windows/thing": b"x"}
        for rel, data in files.items():
            (users / rel).parent.mkdir(parents=True, exist_ok=True)
            (users / rel).write_bytes(data)
        outside = self.tmp / "real-home-documents"
        outside.mkdir()
        (outside / "private.txt").write_text("not a save")
        os.symlink(outside, users / "steamuser/Documents/linked")  # (plain Wine links Documents to the real one)
        return users

    def test_saves_are_backed_up_without_caches_or_linked_folders(self):
        app = self.installed()
        self.put_saves(app)
        archive = saves.backup(app, self.paths)
        self.assertEqual(archive.parent, self.paths.root / "save-backups/cool-game")
        with zipfile.ZipFile(archive) as z:
            names = sorted(n for n in z.namelist() if n != saves.INFO)
        self.assertEqual(names, ["Public/Documents/Cool/shared.sav", "user/AppData/LocalLow/Dev/Cool/prefs",
                                 "user/AppData/Roaming/Cool/profile.dat", "user/Documents/My Games/Cool/slot1.sav",
                                 "user/Saved Games/Cool/auto.sav"])
        self.assertEqual(saves.backups(self.paths, app.name), [archive])
        self.assertGreater(saves.created(archive), 0)

    def test_nothing_to_back_up_makes_no_backup(self):
        app = self.installed()
        self.assertEqual(saves.save_files(app), [])
        self.assertIsNone(saves.backup(app, self.paths))
        self.assertEqual(saves.backups(self.paths, app.name), [])

    def test_a_reinstalled_program_gets_its_saves_back(self):
        app = self.installed()
        self.put_saves(app)
        archive = saves.backup(app, self.paths)
        core.uninstall(app, self.paths, roots=[self.steam])
        self.assertFalse(Path(app.prefix).exists())
        again = self.installed()
        self.assertEqual(saves.restore(again, archive), 5)
        users = Path(again.prefix) / "pfx/drive_c/users"
        self.assertEqual((users / "steamuser/Documents/My Games/Cool/slot1.sav").read_bytes(), b"level 9")
        self.assertEqual((users / "Public/Documents/Cool/shared.sav").read_bytes(), b"shared")

    def test_a_backup_cannot_write_outside_the_profile(self):
        app = self.installed()
        evil = self.tmp / "evil.zip"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("user/../../../../escaped.txt", "x")
            z.writestr("/abs.txt", "x")
            z.writestr("elsewhere/file.txt", "x")
            z.writestr("user/Documents/ok.sav", "ok")
        self.assertEqual(saves.restore(app, evil), 1)
        self.assertFalse(list(self.tmp.rglob("escaped.txt")))

    def test_only_the_newest_backups_are_kept(self):
        app = self.installed()
        self.put_saves(app)
        made = [saves.backup(app, self.paths, now=1_700_000_000 + i * 60) for i in range(saves.KEEP + 2)]
        self.assertEqual(saves.backups(self.paths, app.name), made[::-1][:saves.KEEP])
