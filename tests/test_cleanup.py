"""Leftovers of uninstalled Steam games, in a fake Steam with an internal library and an SD card."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import cleanup, core  # noqa: E402
from tests.test_core import Env  # noqa: E402


class TestCleanup(Env):
    def setUp(self):
        super().setUp()
        self.sd = self.tmp / "sdcard"
        self.gone_sd = self.tmp / "removed-sdcard"  # listed by Steam, not mounted right now
        (self.sd / "steamapps").mkdir(parents=True)
        apps = self.steam / "steamapps"
        (apps / "libraryfolders.vdf").write_text(f'''"libraryfolders"
{{
  "0" {{ "path" "{self.steam}" "apps" {{ "100" "1" "1628350" "1" }} }}
  "1" {{ "path" "{self.sd}" "apps" {{ "200" "1" }} }}
  "2" {{ "path" "{self.gone_sd}" "apps" {{ "300" "1" }} }}
}}
''')
        (apps / "appmanifest_100.acf").write_text('"AppState" { "appid" "100" }')
        (self.sd / "steamapps/appmanifest_200.acf").write_text('"AppState" { "appid" "200" }')
        (apps / "appmanifest_400.acf").write_text('"AppState" { "appid" "400" }')  # installed, not in the vdf yet
        shortcut = core.shortcut_appid('"/x/game.sh"', "My Game")
        core.add_steam_shortcut("My Game", "/x/game.sh", "/x", roots=[self.steam])
        for lib, kind, appid, size in ((apps, "shadercache", 100, 10), (apps, "shadercache", 555, 5000),
                                       (apps, "compatdata", 555, 7000), (apps, "compatdata", 300, 10),
                                       (apps, "compatdata", 400, 10), (apps, "compatdata", shortcut, 10),
                                       (apps, "compatdata", 0, 10), (self.sd / "steamapps", "shadercache", 666, 3000),
                                       (self.sd / "steamapps", "compatdata", 200, 10)):
            d = lib / kind / str(appid)
            d.mkdir(parents=True, exist_ok=True)
            (d / "data").write_bytes(b"x" * size)

    def test_only_what_uninstalled_games_left_is_found(self):
        self.paths.downloads.mkdir(parents=True)
        (self.paths.downloads / "Battle.net-Setup.exe").write_bytes(b"MZ" * 100)
        (self.paths.downloads / ".EAappInstaller.exe.download").write_bytes(b"MZ")  # still downloading: not offered
        found = cleanup.find([self.steam], self.paths)
        self.assertEqual([(i.kind, i.appid, i.size) for i in found],
                         [("compatdata", 555, 7000), ("shadercache", 555, 5000), ("shadercache", 666, 3000),
                          ("download", 0, 200)])
        self.assertEqual(cleanup.delete(found), 15200)
        self.assertFalse((self.steam / "steamapps/compatdata/555").exists())
        self.assertTrue((self.steam / "steamapps/compatdata/300").exists())  # its SD card is just out
        self.assertTrue((self.steam / "steamapps/shadercache/100").exists())
        self.assertTrue((self.paths.downloads / ".EAappInstaller.exe.download").exists())

    def test_nothing_is_offered_if_steams_libraries_cant_be_read(self):
        (self.steam / "steamapps/libraryfolders.vdf").unlink()
        for p in self.steam.glob("steamapps/appmanifest_*.acf"):
            p.unlink()
        self.assertEqual(cleanup.find([self.steam]), [])

    def test_names_come_from_the_store_when_it_knows_them(self):
        import json
        items = [cleanup.Leftover("compatdata", Path("/x"), 555), cleanup.Leftover("compatdata", Path("/y"), 777)]
        store = {"555": {"success": True, "data": {"name": "Portal 2"}}}
        cleanup.name_games(items, lambda url: json.dumps({url.split("=")[1].split("&")[0]:
                                                          store.get(url.split("=")[1].split("&")[0], {})}).encode())
        self.assertEqual([i.name for i in items], ["Portal 2", ""])


class TestLeftoverFolders(Env):
    """Folders Steam leaves in steamapps/common after uninstalling (files it didn't download itself)."""

    def setUp(self):
        super().setUp()
        self.apps = self.steam / "steamapps"
        (self.apps / "libraryfolders.vdf").write_text(
            f'"libraryfolders" {{ "0" {{ "path" "{self.steam}" "apps" {{ "1628350" "1" "10" "1" }} }} }}\n')
        (self.apps / "appmanifest_10.acf").write_text('"AppState" { "appid" "10" "installdir" "Some Game" }')
        for d in ("Some Game", "SteamLinuxRuntime_4/var/tmp-X", "Steam Controller Configs/123/config"):
            (self.apps / "common" / d).mkdir(parents=True, exist_ok=True)
            (self.apps / "common" / d / "big.bin").write_bytes(b"x" * (2 << 20))
        (self.apps / "common/Tiny").mkdir()
        (self.apps / "common/Tiny/a.txt").write_text("x")

    def test_an_uninstalled_apps_folder_is_offered_and_installed_ones_never(self):
        from deckhand import cleanup
        found = [i for i in cleanup.find([self.steam]) if i.kind == "common"]
        self.assertEqual([i.name for i in found], ["SteamLinuxRuntime_4"])
        self.assertGreaterEqual(found[0].size, 2 << 20)  # (Steam's controller layouts, and a tiny folder, never)

    def test_a_manifest_that_cant_be_read_means_nothing_is_offered_there(self):
        from deckhand import cleanup
        (self.apps / "appmanifest_11.acf").write_text('"AppState" { "appid" "11" }')  # no installdir
        self.assertEqual([i for i in cleanup.find([self.steam]) if i.kind == "common"], [])
