"""Game stores: Linux apps from (a fake) Flathub, Windows apps from their (faked) official installers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core, stores  # noqa: E402
from tests.test_addons import FakeResponse  # noqa: E402
from tests.test_streaming import FlatpakEnv  # noqa: E402


class TestStores(FlatpakEnv):
    def test_the_list_is_sound(self):
        self.assertEqual(len({s.id for s in stores.STORES}), len(stores.STORES))
        for s in stores.STORES:
            self.assertNotEqual(bool(s.app), s.is_windows, s.id)  # one way in, never both or neither
            if s.is_windows:
                self.assertTrue(s.url.startswith("https://"), s.id)
                self.assertTrue(s.filename.lower().endswith(core.INSTALLER_SUFFIXES), s.id)
            self.assertTrue(stores._is_named(s, s.name), s.id)  # its own name is recognised

    def test_every_service_store_and_add_on_has_its_logo(self):
        from deckhand import addons, streaming
        ids = [s.id for s in streaming.SERVICES] + [s.id for s in stores.STORES] + [a.id for a in addons.ADDONS]
        self.assertEqual(len(set(ids)), len(ids))  # one logo folder for all of them
        for i in ids:
            logo = core.logo_file(i)
            self.assertIsNotNone(logo, i)
            self.assertEqual(logo.read_bytes()[:8], b"\x89PNG\r\n\x1a\n", i)
        self.assertEqual(sorted(p.stem for p in core.LOGOS.glob("*.png")), sorted(ids))  # and nothing else

    def test_heroic_comes_from_flathub_and_lands_in_steam_once(self):
        heroic = stores.store("heroic")
        app = stores.set_up(heroic, self.paths, roots=[self.steam])
        self.assertIn("install --system -y --noninteractive flathub com.heroicgameslauncher.hgl", self.calls())
        self.assertIn("exec flatpak run com.heroicgameslauncher.hgl", Path(app.launcher).read_text())
        self.assertEqual((app.kind, app.id, app.name), ("store", "store-heroic", "Heroic Games Launcher"))
        self.assertEqual(Path(app.launcher).name, "store-heroic.sh")
        self.assertTrue(core.in_steam(app, [self.steam]))
        # Its own logo is its icon, in Steam and the app menu (a copy that outlives a single-file build).
        self.assertEqual(Path(app.icon), self.paths.icons / "store-heroic.png")
        self.assertEqual(Path(app.icon).read_bytes(), core.logo_file("heroic").read_bytes())
        self.assertEqual(core.steam_shortcuts([self.steam])[0][1]["icon"], app.icon)
        self.assertIn(f"Icon={app.icon}", core.desktop_entry_path(app).read_text())
        self.assertEqual(stores.installed(heroic, core.Library(self.paths).load()).id, app.id)
        stores.set_up(heroic, self.paths, roots=[self.steam])  # again: still one of each
        self.assertEqual(len([c for c in self.calls() if c.startswith("install")]), 1)
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)
        self.assertEqual(len(core.Library(self.paths).load()), 1)
        # Removing it leaves nothing behind (Heroic itself stays installed).
        self.assertFalse(core.uninstall(app, self.paths, roots=[self.steam]))
        self.assertFalse(Path(app.launcher).exists())
        self.assertFalse(Path(app.icon).exists())
        self.assertTrue(core.logo_file("heroic").exists())
        self.assertEqual(core.steam_shortcuts([self.steam]), [])

    def test_a_heroic_appimage_is_used_instead_of_flathub(self):
        apps = self.home / "Applications"
        apps.mkdir()
        img = apps / "Heroic-2.15.2-linux-x86_64.AppImage"
        img.write_text("#!/bin/sh\n")
        img.chmod(0o755)
        self.assertIn("local:heroic", stores.detect())
        app = stores.set_up(stores.store("heroic"), self.paths, roots=[self.steam])
        self.assertFalse([c for c in self.calls() if c.startswith("install")])
        self.assertIn(f"exec {img}", Path(app.launcher).read_text())

    def test_a_store_app_and_a_streaming_service_are_kept_apart(self):
        from deckhand import streaming
        stores.set_up(stores.store("itch"), self.paths, roots=[self.steam])
        streaming.set_up(streaming.service("moonlight"), self.paths, roots=[self.steam])
        kinds = sorted((a.kind, a.id) for a in core.Library(self.paths).load())
        self.assertEqual(kinds, [("store", "store-itch"), ("stream", "stream-moonlight")])
        self.assertIsNone(streaming.app_for(streaming.service("moonlight"), self.paths, kind="store"))

    def test_the_official_installer_is_downloaded(self):
        body = b"MZ" + b"\0" * 5000
        seen, urls = [], []
        battlenet = stores.store("battlenet")
        path = stores.download(battlenet, self.paths, lambda d, t: seen.append((d, t)),
                               opener=lambda url: (urls.append(url), FakeResponse(body))[1])
        self.assertEqual(urls, [battlenet.url])
        self.assertEqual(path, self.paths.root / "downloads/Battle.net-Setup.exe")
        self.assertEqual(path.read_bytes(), body)
        self.assertEqual(seen[-1], (len(body), len(body)))
        # An .msi (Epic's) is an OLE file, not an .exe.
        msi = stores.download(stores.store("epic"), self.paths,
                              opener=lambda url: FakeResponse(stores.MSI_MAGIC + b"\0" * 100))
        self.assertEqual(msi.suffix, ".msi")

    def test_a_download_that_is_not_an_installer_leaves_nothing(self):
        for s, junk in ((stores.store("ea"), b"<html>Access denied</html>"), (stores.store("epic"), b"MZ" * 10)):
            with self.assertRaises(core.InstallError):
                stores.download(s, self.paths, opener=lambda url: FakeResponse(junk))
        self.assertEqual(list((self.paths.root / "downloads").iterdir()), [])

    def test_what_is_installed_is_recognised(self):
        def program(name, installer):
            return core.App(id=core.slugify(name), name=name, exe="/x.exe", prefix="/p", runtime_name="",
                            runtime_kind="", runtime_path="", installer=installer)

        apps = [program("Battle.net", "/home/deck/Downloads/Battle.net-Setup.exe"),  # from its setup file
                program("EA", str(stores.download_path(stores.store("ea"), self.paths))),  # from the Stores page
                program("Epic Games Launcher", "/sd/EpicInstaller-20.3.3.msi"),  # by the name its shortcut gave it
                program("Epic Mickey", "/x/setup_epic_mickey.exe")]
        found = {s.id: getattr(stores.installed(s, apps), "name", None) for s in stores.STORES}
        self.assertEqual(found, {"heroic": None, "battlenet": "Battle.net", "ea": "EA", "ubisoft": None,
                                 "epic": "Epic Games Launcher", "amazon": None, "rockstar": None, "itch": None,
                                 "prism": None})

    def test_store_shortcuts_made_outside_are_recognised_but_not_heroics_games(self):
        entries = [(Path("/c"), e) for e in (
            {"AppName": "Battle.net", "Exe": '"/home/deck/.local/share/Steam/steamapps/compatdata/x/Battle.net.exe"'},
            {"AppName": "EA App", "Exe": '"/x/EADesktop.exe"'},
            {"AppName": "Heroic Games Launcher", "Exe": '"flatpak"', "LaunchOptions": "run com.heroicgameslauncher.hgl"},
            {"AppName": "Hades", "Exe": '"flatpak"',  # a game Heroic added: runs Heroic, but isn't Heroic
             "LaunchOptions": "run com.heroicgameslauncher.hgl --no-gui heroic://launch/legendary/Min"},
            {"AppName": "Minecraft Launcher", "Exe": '"/x/MinecraftLauncher.exe"'},
            {"AppName": "Ubisoft Connect", "Exe": f'"{self.paths.launchers}/ubisoft-connect.sh"'},  # ours
        )]
        found = {s.id: [e["AppName"] for e in stores.spots(s, entries, self.paths.launchers)] for s in stores.STORES}
        self.assertEqual(found, {"heroic": ["Heroic Games Launcher"], "battlenet": ["Battle.net"], "ea": ["EA App"],
                                 "ubisoft": [], "epic": [], "amazon": [], "rockstar": [], "itch": [],
                                 "prism": ["Minecraft Launcher"]})

    def test_a_store_installs_under_its_own_name(self):
        job = self.job(name="Battle.net")
        pending = job.run()
        job.close()
        self.assertEqual((pending.name, pending.id), ("Battle.net", "battle-net"))
        self.assertEqual(core.guess_name(self.installer), "Cool Game")  # what it'd be called otherwise
