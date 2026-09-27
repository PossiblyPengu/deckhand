"""Add-ons: Decky Loader and EmuDeck from their official sources (network faked)."""
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import addons, core  # noqa: E402
from tests.test_core import Env  # noqa: E402
from tests.test_streaming import FlatpakEnv  # noqa: E402

DECKY_SCRIPT = b"#!/bin/bash\n# Decky Installer (fake)\nmkdir -p \"$HOME/homebrew/services\"\n" \
               b"touch \"$HOME/homebrew/services/PluginLoader\"\necho v3.1.10 > \"$HOME/homebrew/services/.loader.version\"\n" \
               b"echo installed\n"
RELEASE = {"tag_name": "v2.4.7", "assets": [{"name": "EmuDeck-2.4.7.AppImage",
                                             "browser_download_url": "https://example.invalid/EmuDeck.AppImage"},
                                            {"name": "latest-linux.yml", "browser_download_url": "x"}]}


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class TestAddons(Env):
    def test_status_reads_what_is_installed(self):
        decky, emu = addons.addon("decky"), addons.addon("emudeck")
        self.assertEqual((addons.status(decky), addons.status(emu)), ("Not installed", "Not installed"))
        services = self.home / "homebrew/services"
        services.mkdir(parents=True)
        (services / "PluginLoader").write_text("")
        self.assertEqual(addons.status(decky), "Installed")
        (services / ".loader.version").write_text("v3.1.10\n")
        self.assertEqual(addons.status(decky), "Installed (v3.1.10)")
        (self.home / "Applications").mkdir()
        (self.home / "Applications/EmuDeck.AppImage").write_text("")
        self.assertEqual(addons.status(emu), "Downloaded — not set up yet")
        (self.home / "emudeck").mkdir()
        self.assertEqual(addons.status(emu), "Installed")

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

    def test_emudeck_downloads_the_latest_appimage_into_applications(self):
        body = b"\x7fELF" + b"\0" * 5000
        seen = []
        version = addons.download_emudeck(lambda d, t: seen.append((d, t)),
                                          fetch=lambda url: json.dumps(RELEASE).encode(),
                                          opener=lambda url: FakeResponse(body))
        self.assertEqual(version, "2.4.7")
        app = self.home / "Applications/EmuDeck.AppImage"
        self.assertEqual(app.read_bytes(), body)
        self.assertTrue(os.access(app, os.X_OK))
        self.assertEqual(seen[-1], (len(body), len(body)))

    def test_a_damaged_emudeck_download_leaves_nothing(self):
        with self.assertRaises(core.InstallError):
            addons.download_emudeck(fetch=lambda url: json.dumps(RELEASE).encode(),
                                    opener=lambda url: FakeResponse(b"<html>nope</html>"))
        self.assertEqual(list((self.home / "Applications").iterdir()), [])

    def test_no_appimage_in_the_release_is_explained(self):
        with self.assertRaises(core.InstallError):
            addons.emudeck_release(lambda url: json.dumps({"tag_name": "v1", "assets": []}).encode())


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

