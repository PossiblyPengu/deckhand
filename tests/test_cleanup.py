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
