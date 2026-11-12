"""Add-ons: Decky Loader, Arcadia, Nexus and the rest from their official sources (network faked)."""
import io
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import addons, core  # noqa: E402
from tests.test_core import Env  # noqa: E402
from tests.test_streaming import FlatpakEnv  # noqa: E402

DECKY_SCRIPT = b"#!/bin/bash\n# Decky Installer (fake)\nmkdir -p \"$HOME/homebrew/services\"\n" \
               b"touch \"$HOME/homebrew/services/PluginLoader\"\necho v3.1.10 > \"$HOME/homebrew/services/.loader.version\"\n" \
               b"echo installed\n"

class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class TestAddons(Env):
    def test_status_reads_what_is_installed(self):
        decky = addons.addon("decky")
        self.assertEqual(addons.status(decky), "Not installed")
        services = self.home / "homebrew/services"
        services.mkdir(parents=True)
        (services / "PluginLoader").write_text("")
        self.assertEqual(addons.status(decky), "Installed")
        (services / ".loader.version").write_text("v3.1.10\n")
        self.assertEqual(addons.status(decky), "Installed (v3.1.10)")

    def test_decky_uses_its_own_installer(self):
        urls = []
        script = addons.fetch_decky_installer(self.tmp / "dl", lambda url: (urls.append(url), DECKY_SCRIPT)[1])
        self.assertEqual(urls, [addons.DECKY_INSTALLER_URL])
        lines = []
        self.assertEqual(addons.run_decky_installer(script, lines.append), 0)
        self.assertIn("installed", lines)
        self.assertEqual(addons.decky_version(), "v3.1.10")

    def test_a_bad_decky_download_is_refused(self):
        with self.assertRaises(core.InstallError):
            addons.fetch_decky_installer(self.tmp / "dl", lambda url: b"<html>rate limited</html>")


ARCADIA_BODY = b"\x7fELF" + b"\0" * 4000


def arcadia_release(body: bytes = ARCADIA_BODY, digest: str | None = None) -> bytes:
    import hashlib
    return json.dumps({"tag_name": "v0.1.0", "assets": [
        {"name": "arcadia-deck-extras.tar.gz", "browser_download_url": "x"},
        {"name": addons.ARCADIA_APPIMAGE, "browser_download_url": "https://example.invalid/Arcadia.AppImage",
         "digest": digest or "sha256:" + hashlib.sha256(body).hexdigest()}]}).encode()


class TestArcadia(Env):
    def test_the_sister_app_comes_first(self):
        self.assertEqual(addons.ADDONS[0].id, "arcadia")

    def test_arcadia_is_downloaded_checked_and_added_to_steam_once(self):
        a = addons.addon("arcadia")
        self.assertEqual(addons.status(a), "Not installed")
        version = addons.download_arcadia(fetch=lambda url: arcadia_release(), opener=lambda url: FakeResponse(ARCADIA_BODY))
        self.assertEqual(version, "0.1.0")
        appimage = self.home / "Applications" / addons.ARCADIA_APPIMAGE
        self.assertEqual(appimage.read_bytes(), ARCADIA_BODY)
        self.assertTrue(os.access(appimage, os.X_OK))
        self.assertEqual(addons.status(a), "Installed")
        app = addons.set_up_arcadia(self.paths, roots=[self.steam])
        self.assertEqual((app.id, app.kind, app.exe), ("addon-arcadia", "addon", str(appimage)))
        self.assertIn(str(appimage), Path(app.launcher).read_text())
        self.assertTrue(core.in_steam(app, [self.steam]))
        self.assertEqual(Path(app.icon).name, "addon-arcadia.png")
        addons.set_up_arcadia(self.paths, roots=[self.steam])
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)
        # Uninstalling removes the AppImage and the shortcut, never Arcadia's settings.
        (self.home / "Arcadia").mkdir()
        self.assertFalse(addons.uninstall_arcadia(app, self.paths, roots=[self.steam]))
        self.assertFalse(appimage.exists())
        self.assertEqual(core.steam_shortcuts([self.steam]), [])
        self.assertTrue((self.home / "Arcadia").is_dir())

    def test_an_arcadia_already_in_steam_gets_no_second_shortcut(self):
        apps = self.home / "Applications"
        apps.mkdir()
        mine = apps / "Arcadia-0.1.AppImage"
        mine.write_bytes(ARCADIA_BODY)
        self.assertEqual(addons.arcadia_app(), mine)
        appid, _how = core.add_shortcut("Arcadia", str(mine), str(apps), roots=[self.steam], running=False)
        app = addons.set_up_arcadia(self.paths, roots=[self.steam])
        self.assertEqual(addons.arcadia_shortcut_of_yours([self.steam]), appid)
        self.assertEqual(app.steam_appid, 0)  # not adopted: it's yours
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)
        # Uninstalling Arcadia never takes away a shortcut you made, even with Steam closed.
        addons.uninstall_arcadia(app, self.paths, roots=[self.steam])
        self.assertFalse(mine.exists())
        self.assertEqual([core._entry_appid(e) for _c, e in core.steam_shortcuts([self.steam])], [appid])

    def test_uninstall_removes_every_arcadia_appimage(self):
        apps = self.home / "Applications"
        apps.mkdir()
        for name in (addons.ARCADIA_APPIMAGE, addons.ARCADIA_APPIMAGE + ".previous", "Arcadia-0.1.AppImage"):
            (apps / name).write_bytes(ARCADIA_BODY)
        self.assertEqual(addons.arcadia_app(), apps / addons.ARCADIA_APPIMAGE)
        addons.uninstall_arcadia(None, self.paths, roots=[self.steam])
        self.assertEqual(list(apps.iterdir()), [])
        self.assertEqual(addons.status(addons.addon("arcadia")), "Not installed")

    def test_a_download_that_fails_its_checksum_keeps_the_old_arcadia(self):
        apps = self.home / "Applications"
        apps.mkdir()
        (apps / addons.ARCADIA_APPIMAGE).write_bytes(b"OLD")
        with self.assertRaises(core.InstallError):
            addons.download_arcadia(fetch=lambda url: arcadia_release(digest="sha256:" + "0" * 64),
                                    opener=lambda url: FakeResponse(ARCADIA_BODY))
        self.assertEqual([p.name for p in apps.iterdir()], [addons.ARCADIA_APPIMAGE])
        self.assertEqual((apps / addons.ARCADIA_APPIMAGE).read_bytes(), b"OLD")

    def test_no_arcadia_release_yet_is_explained(self):
        with self.assertRaises(core.InstallError):
            addons.arcadia_release(lambda url: json.dumps({"tag_name": "v1", "assets": []}).encode())


class TestFlathubAddons(FlatpakEnv):
    def test_a_game_mode_app_is_installed_and_added_to_steam(self):
        from deckhand import streaming
        discord = addons.addon("discord")
        self.assertEqual(addons.status(discord, installed=set()), "Not installed")
        app = streaming.set_up(discord.service, self.paths, roots=[self.steam], kind="addon")
        self.assertIn("install --system -y --noninteractive flathub com.discordapp.Discord", self.calls())
        self.assertEqual((app.kind, app.id), ("addon", "addon-discord"))
        self.assertIn("exec flatpak run com.discordapp.Discord", Path(app.launcher).read_text())
        self.assertEqual(Path(app.icon).name, "addon-discord.png")
        self.assertTrue(core.in_steam(app, [self.steam]))
        self.assertEqual(addons.status(discord, installed=streaming.installed_apps()), "Installed")
        # Uninstalling: the shortcut goes, then the app.
        self.assertFalse(core.uninstall(app, self.paths, roots=[self.steam]))
        streaming.uninstall_app(discord.app)
        self.assertIn("uninstall --system -y --noninteractive com.discordapp.Discord", self.calls())
        self.assertNotIn(discord.app, streaming.installed_apps())
        self.assertEqual(core.steam_shortcuts([self.steam]), [])

    def test_uninstalling_an_app_installed_for_all_users_is_explained(self):
        from deckhand import streaming
        with self.assertRaises(core.InstallError) as ctx:
            streaming.uninstall_app("com.github.tchx84.Flatseal")
        self.assertIn("Discover", str(ctx.exception))

    def test_every_flathub_addon_is_a_flathub_app(self):
        for a in addons.ADDONS:
            if a.app:
                self.assertRegex(a.app, r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2,}$", a.id)
            self.assertFalse(a.steam and not a.app, a.id)  # only Flathub apps are added to Steam



class AddArcadiaCliTests(unittest.TestCase):
    """`deckhand --add-arcadia`, which get.sh --with-arcadia runs."""

    def run_cli(self, download, setup=lambda paths: None):
        from unittest import mock

        from deckhand import app
        lines: list[str] = []
        with mock.patch.object(addons, "download_arcadia", download), \
                mock.patch.object(addons, "set_up_arcadia", setup):
            rc = app.add_arcadia_cli(out=lines.append)
        return rc, "\n".join(lines)

    def test_downloads_then_sets_up(self):
        calls = []
        rc, text = self.run_cli(lambda: calls.append("download") or "1.2.3",
                                lambda paths: calls.append("setup"))
        self.assertEqual((rc, calls), (0, ["download", "setup"]))
        self.assertIn("Arcadia 1.2.3", text)

    def test_a_failed_download_is_reported(self):
        def boom():
            raise core.InstallError("no network")
        rc, text = self.run_cli(boom)
        self.assertEqual(rc, 1)
        self.assertIn("no network", text)


NEXUS_BODY = b"\x7fELF" + b"\0" * 4000


def nexus_release(body: bytes = NEXUS_BODY) -> dict:
    import hashlib
    name = "Nexus-Launcher-0.5.0-x86_64.AppImage"
    return {"tag_name": "v0.5.0", "assets": [
        {"name": "Nexus-Launcher-0.5.0-arm64.AppImage", "browser_download_url": "https://example.invalid/arm"},
        {"name": name, "browser_download_url": "https://example.invalid/" + name},
        {"name": name + ".sha512sum", "browser_download_url": "https://example.invalid/sum"},
        {"name": "Xodus-Nexus-0.5.0-x86_64.AppImage", "browser_download_url": "https://example.invalid/old"}],
        "sum": f"{hashlib.sha512(body).hexdigest()}  {name}\n"}


def nexus_fetch(body: bytes = NEXUS_BODY):
    rel = nexus_release(body)
    return lambda url: rel["sum"].encode() if url.endswith("/sum") else json.dumps(rel).encode()


class TestNexus(Env):
    def test_parse_sha512sum(self):
        h = "ab" * 64
        self.assertEqual(addons.parse_sha512sum(f"{h}  file.AppImage\n", "file.AppImage"), h)
        self.assertEqual(addons.parse_sha512sum(f"{h} *file.AppImage", "file.AppImage"), h)
        self.assertEqual(addons.parse_sha512sum(f"{h}  other\n", "file.AppImage"), "")
        self.assertEqual(addons.parse_sha512sum("garbage", "file.AppImage"), "")

    def test_release_picks_the_new_name_for_this_arch_with_its_checksum(self):
        version, url, sha = addons.nexus_release(nexus_fetch(), arch="x86_64")
        self.assertEqual((version, url.endswith("Nexus-Launcher-0.5.0-x86_64.AppImage")), ("0.5.0", True))
        self.assertEqual(len(sha), 128)
        self.assertTrue(addons.nexus_release(nexus_fetch(), arch="arm64")[1].endswith("/arm"))
        with self.assertRaises(core.InstallError):
            addons.nexus_release(lambda u: json.dumps({"tag_name": "v1", "assets": []}).encode())

    def test_downloaded_checked_and_added_to_steam_once(self):
        a = addons.addon("nexus")
        self.assertEqual(addons.status(a), "Not installed")
        version = addons.download_nexus(fetch=nexus_fetch(), opener=lambda url: FakeResponse(NEXUS_BODY))
        self.assertEqual(version, "0.5.0")
        appimage = self.home / "Applications" / addons.NEXUS_APPIMAGE
        self.assertEqual(appimage.read_bytes(), NEXUS_BODY)
        self.assertTrue(os.access(appimage, os.X_OK))
        self.assertEqual(addons.status(a), "Installed")
        app = addons.set_up_nexus(self.paths, roots=[self.steam])
        self.assertEqual((app.id, app.kind, app.exe), ("addon-nexus", "addon", str(appimage)))
        self.assertTrue(core.in_steam(app, [self.steam]))
        addons.set_up_nexus(self.paths, roots=[self.steam])
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)
        self.assertFalse(addons.uninstall_nexus(app, self.paths, roots=[self.steam]))
        self.assertFalse(appimage.exists())
        self.assertEqual(core.steam_shortcuts([self.steam]), [])

    def test_a_wrong_checksum_leaves_nothing(self):
        with self.assertRaises(core.InstallError):
            addons.download_nexus(fetch=nexus_fetch(), opener=lambda url: FakeResponse(b"\x7fELF" + b"1" * 50))
        self.assertEqual(list((self.home / "Applications").iterdir()), [])

    def test_the_projects_own_install_is_found_and_updated_in_place(self):
        own = self.home / ".local/opt/xodus-nexus"
        own.mkdir(parents=True)
        mine = own / addons.NEXUS_APPIMAGE
        mine.write_bytes(b"\x7fELFold")
        self.assertEqual(addons.nexus_app(), mine)
        addons.download_nexus(fetch=nexus_fetch(), opener=lambda url: FakeResponse(NEXUS_BODY))
        self.assertEqual(mine.read_bytes(), NEXUS_BODY)
        self.assertFalse((self.home / "Applications").exists() and any((self.home / "Applications").iterdir()))
        # Uninstalling from Deckhand never deletes a copy that the project's own installer made.
        app = addons.set_up_nexus(self.paths, roots=[self.steam])
        addons.uninstall_nexus(app, self.paths, roots=[self.steam])
        self.assertTrue(mine.exists())
