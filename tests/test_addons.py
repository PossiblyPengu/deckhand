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


# Decky's installer, reduced to the shape that matters here: the same zen_nospam() helper, and one call of each kind.
UPSTREAM_SHAPED = '''#!/bin/bash
zen_nospam() {
  zenity 2> >(grep -v 'Gtk' >&2) "$@"
}
if ( zen_nospam --title="Decky Installer" --width=300 --height=200 --question --text="no password set. ok?" ); then
  echo "temporary-password-accepted"
fi
PASS=$(zen_nospam --title="Decky Installer" --width=300 --height=100 --entry --hide-text --text="Enter your sudo/admin password")
[ "$PASS" = "hunter2" ] && echo "password-ok"
OPTION=$(zen_nospam --title="Decky Installer" --width=750 --height=300 --list --radiolist --text "Select branch to install:" --hide-header --column "Buttons" --column "Choice" --column "Info" \\
  TRUE "release" "Recommended for stable steamos" \\
  FALSE "prerelease" "Recommended for beta/preview steamos")
echo "option=$OPTION"
(
echo "15" ; echo "# Creating file structure" ;
echo "60"; echo "# Installing version v3.0.1" ;
echo "100" ; echo "# Install finished, installer can now be closed";
) |
zen_nospam --progress --title="Decky Installer" --width=300 --height=100 --text="Installing..." --percentage=0 --no-cancel
zen_nospam --title="Decky Installer" --width=300 --height=100 --error --text "No Decky Loader release was found."
exit 7
'''


class TestDeckyInGameMode(Env):
    """Decky's own installer, its questions answered by Deckhand (so no zenity window, and Game Mode works)."""

    def setUp(self):
        super().setUp()
        self.script = self.tmp / "decky.sh"
        self.script.write_text(UPSTREAM_SHAPED)
        self.workdir = self.tmp / "t"
        self.workdir.mkdir()
        from unittest import mock
        self.tempdir = mock.patch.object(__import__("tempfile"), "tempdir", str(self.workdir))
        self.tempdir.start()
        self.addCleanup(self.tempdir.stop)

    def run_it(self, option="release", password="hunter2", temporary=False):
        events, lines = [], []
        rc, messages = addons.run_decky(self.script, option, password, temporary,
                                        lambda k, v: events.append((k, v)), lines.append)
        return rc, messages, events, lines

    def test_every_prompt_is_answered_and_progress_is_reported(self):
        rc, messages, events, lines = self.run_it(option="prerelease", temporary=True)
        self.assertEqual(rc, 7)  # (the script's own exit code is passed on)
        self.assertEqual(messages, ["No Decky Loader release was found."])
        self.assertIn("temporary-password-accepted", lines)
        self.assertIn("password-ok", lines)
        self.assertIn("option=prerelease", lines)
        self.assertEqual([v for k, v in events if k == "progress"], ["15", "60", "100"])
        self.assertEqual([v for k, v in events if k == "text"][0], "Creating file structure")

    def test_the_default_choice_is_used_when_the_wanted_one_is_not_offered(self):
        _rc, _m, _e, lines = self.run_it(option="update to latest release")
        self.assertIn("option=release", lines)  # (the list's first TRUE row)

    def test_no_to_the_temporary_password_question_is_passed_on(self):
        _rc, _m, _e, lines = self.run_it(temporary=False)
        self.assertNotIn("temporary-password-accepted", lines)

    def test_the_password_never_reaches_the_log_and_is_wiped(self):
        _rc, messages, events, lines = self.run_it(password="hunter2")
        everything = "\n".join(lines + messages + [f"{k} {v}" for k, v in events])
        self.assertNotIn("hunter2", everything)
        self.assertEqual(list(self.workdir.iterdir()), [])  # the folder with the answers is gone

    def test_no_zenity_is_needed(self):
        import os
        os.environ["PATH"] = "/usr/bin:/bin"  # (nothing called zenity is there)
        self.assertEqual(self.run_it()[0], 7)

    def test_a_changed_installer_is_refused_and_nothing_is_left_behind(self):
        self.script.write_text("#!/bin/bash\n# Decky\nzenity --info --text hi\n")
        with self.assertRaises(core.InstallError) as ctx:
            self.run_it()
        self.assertEqual(str(ctx.exception), "DECKY_CHANGED")
        self.assertEqual(list(self.workdir.iterdir()), [])

    def test_patching_swaps_only_the_zenity_helper(self):
        patched = addons.patch_decky_installer(UPSTREAM_SHAPED, Path("/w/zenity"), Path("/w/answers.json"))
        self.assertIn("/w/zenity /w/answers.json \"$@\"", patched)
        self.assertNotIn("zenity 2>", patched)
        self.assertEqual(patched.count("zen_nospam --"), UPSTREAM_SHAPED.count("zen_nospam --"))  # all calls kept

    def test_paths_with_awkward_characters_are_quoted(self):
        patched = addons.patch_decky_installer(UPSTREAM_SHAPED, Path("/w x/zen'ity"), Path("/w x/a.json"))
        import subprocess
        self.assertEqual(subprocess.run(["bash", "-n", "-c", patched]).returncode, 0)

    def test_the_real_upstream_installer_still_patches(self):
        # Set DECKY_INSTALLER_COPY to a downloaded copy of Decky's current installer to check it still patches.
        import os
        real = Path(os.environ.get("DECKY_INSTALLER_COPY", "/nonexistent"))
        if not real.is_file():
            self.skipTest("DECKY_INSTALLER_COPY isn't set")
        patched = addons.patch_decky_installer(real.read_text(), Path("/w/zenity"), Path("/w/a.json"))
        import subprocess
        self.assertEqual(subprocess.run(["bash", "-n", "-c", patched]).returncode, 0)
        self.assertNotIn("zenity 2>", patched)

    def test_admin_password_unset_reads_passwd_status(self):
        import os
        bindir = self.tmp / "bin"
        bindir.mkdir()
        os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
        passwd = bindir / "passwd"
        for out, expect in (("deck NP 2024-01-01 0 99999 7 -1", True), ("deck P 2024-01-01 0 99999 7 -1", False),
                            ("deck L 2024-01-01 0 99999 7 -1", False), ("", False)):
            passwd.write_text(f'#!/bin/bash\necho "{out}"\n')
            passwd.chmod(0o755)
            self.assertEqual(addons.admin_password_unset(), expect, out)
        passwd.unlink()  # a system without passwd (or a non-Deck): not "unset"
        os.environ["PATH"] = str(bindir)
        self.assertFalse(addons.admin_password_unset())

    def test_sudo_accepts_only_the_right_password(self):
        import os
        bindir = self.tmp / "bin2"
        bindir.mkdir()
        sudo = bindir / "sudo"
        sudo.write_text('#!/bin/bash\nread -r pw\n[ "$pw" = "right" ]\n')
        sudo.chmod(0o755)
        os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
        self.assertTrue(addons.sudo_accepts("right"))
        self.assertFalse(addons.sudo_accepts("wrong"))


class TestNexusFindsTheRealReleaseFiles(Env):
    """The bug: v0.5.0's files are named Xodus-Nexus-<version>-<arch>.AppImage (the older name), and Deckhand only
    looked for Nexus-Launcher-…, so it said it couldn't find the download."""

    ARCH_SUM = "ab" * 64

    def release(self, names, tag="v0.5.0", prerelease=False, draft=False):
        assets = [{"name": n, "browser_download_url": f"https://example.invalid/{tag}/{n}"} for n in names]
        return {"tag_name": tag, "assets": assets, "prerelease": prerelease, "draft": draft}

    def fetch(self, latest, others=()):
        def get(url):
            if url.endswith(".sha512sum"):
                return f"{self.ARCH_SUM}  {url.rsplit('/', 1)[1][:-len('.sha512sum')]}\n".encode()
            if "releases/latest" in url:
                return json.dumps(latest).encode()
            return json.dumps(list(others)).encode()
        return get

    def test_a_release_with_only_the_older_file_names_is_found(self):
        latest = self.release(["Xodus-Nexus-0.5.0-x86_64.AppImage", "Xodus-Nexus-0.5.0-x86_64.AppImage.sha512sum",
                               "Xodus-Nexus-0.5.0-arm64.AppImage", "Xodus-Nexus-0.5.0-arm64.AppImage.sha512sum"])
        version, url, sha = addons.nexus_release(self.fetch(latest), arch="x86_64")
        self.assertEqual((version, url.rsplit("/", 1)[1], sha),
                         ("0.5.0", "Xodus-Nexus-0.5.0-x86_64.AppImage", self.ARCH_SUM))
        self.assertTrue(addons.nexus_release(self.fetch(latest), arch="arm64")[1].endswith("-arm64.AppImage"))

    def test_the_newer_names_are_preferred_when_both_are_there(self):
        latest = self.release(["Xodus-Nexus-0.6.0-x86_64.AppImage", "Nexus-Launcher-0.6.0-x86_64.AppImage"], "v0.6.0")
        self.assertTrue(addons.nexus_release(self.fetch(latest), arch="x86_64")[1].endswith(
            "Nexus-Launcher-0.6.0-x86_64.AppImage"))

    def test_any_nexus_or_xodus_appimage_for_the_architecture_will_do(self):
        latest = self.release(["Nexus Something-x86_64.AppImage", "notes.txt", "other-app-x86_64.AppImage"])
        self.assertIn("Nexus Something", addons.nexus_release(self.fetch(latest), arch="x86_64")[1])

    def test_a_latest_release_without_an_appimage_falls_back_to_the_recent_releases(self):
        runtime = self.release(["runtime-linux-x86_64.tar.zst"], "runtime-9")  # (another kind of release)
        beta = self.release(["Xodus-Nexus-0.9.0-x86_64.AppImage"], "v0.9.0", prerelease=True)
        draft = self.release(["Xodus-Nexus-0.8.0-x86_64.AppImage"], "v0.8.0", draft=True)
        good = self.release(["Xodus-Nexus-0.5.0-x86_64.AppImage"])
        version, url, _ = addons.nexus_release(self.fetch(runtime, [runtime, beta, draft, good]), arch="x86_64")
        self.assertEqual(version, "0.5.0")  # (not the prerelease, not the draft)
        self.assertTrue(url.endswith("Xodus-Nexus-0.5.0-x86_64.AppImage"))

    def test_when_nothing_matches_the_error_says_what_was_there(self):
        latest = self.release(["something.tar.gz", "other.zip"], "v1")
        with self.assertRaises(core.InstallError) as ctx:
            addons.nexus_release(self.fetch(latest, [latest]), arch="x86_64")
        text = str(ctx.exception)
        self.assertIn("Couldn't find a Nexus Launcher download", text)
        self.assertIn("something.tar.gz", text)  # (so a bad name can be diagnosed from the screen)

    def test_an_unreadable_list_of_releases_still_gives_the_clear_error(self):
        latest = self.release(["something.tar.gz"])

        def get(url):
            if "releases/latest" in url:
                return json.dumps(latest).encode()
            raise OSError("no network")
        with self.assertRaises(core.InstallError):
            addons.nexus_release(get, arch="x86_64")

    def test_downloading_works_end_to_end_with_the_older_names(self):
        import hashlib
        body = b"\x7fELF" + b"\0" * 3000
        name = "Xodus-Nexus-0.5.0-x86_64.AppImage"
        latest = self.release([name, name + ".sha512sum"])

        def get(url):
            if url.endswith(".sha512sum"):
                return f"{hashlib.sha512(body).hexdigest()}  {name}\n".encode()
            return json.dumps(latest).encode()
        self.assertEqual(addons.download_nexus(fetch=get, opener=lambda url: FakeResponse(body)), "0.5.0")
        self.assertEqual((self.home / "Applications" / addons.NEXUS_APPIMAGE).read_bytes(), body)
        with self.assertRaises(core.InstallError):  # a wrong checksum is still refused
            addons.download_nexus(fetch=get, opener=lambda url: FakeResponse(b"\x7fELF" + b"1" * 50))


class TestDownloadsAreChecked(Env):
    """A checksum that is published is required; one that is missing is said out loud; what was fetched is recorded."""

    BODY = b"\x7fELF" + b"\0" * 3000

    def nexus(self, sidecar="good", has_sidecar=True):
        import hashlib
        name = "Xodus-Nexus-0.5.0-x86_64.AppImage"
        assets = [{"name": name, "browser_download_url": "https://example.invalid/app"}]
        if has_sidecar:
            assets.append({"name": name + ".sha512sum", "browser_download_url": "https://example.invalid/sum"})

        def get(url):
            if url.endswith("/sum"):
                if sidecar == "unreadable":
                    raise OSError("connection reset")
                if sidecar == "other-file":
                    return f"{hashlib.sha512(self.BODY).hexdigest()}  Some-Other-File.AppImage\n".encode()
                return f"{hashlib.sha512(self.BODY).hexdigest()}  {name}\n".encode()
            return json.dumps({"tag_name": "v0.5.0", "assets": assets}).encode()
        return get

    def test_a_published_checksum_that_cannot_be_read_stops_the_download(self):
        opened = []
        with self.assertRaises(core.InstallError) as ctx:
            addons.download_nexus(fetch=self.nexus("unreadable"),
                                  opener=lambda url: (opened.append(url), FakeResponse(self.BODY))[1])
        self.assertIn("nothing was downloaded", str(ctx.exception))
        self.assertEqual(opened, [])  # (the file itself was never even requested)

    def test_a_checksum_file_that_does_not_list_the_download_stops_it(self):
        with self.assertRaises(core.InstallError) as ctx:
            addons.download_nexus(fetch=self.nexus("other-file"), opener=lambda url: FakeResponse(self.BODY))
        self.assertIn("doesn't list this download", str(ctx.exception))

    def test_no_published_checksum_is_said_out_loud_and_recorded_as_unchecked(self):
        notes, info = [], {}
        addons.download_nexus(fetch=self.nexus(has_sidecar=False), opener=lambda url: FakeResponse(self.BODY),
                              info=info, note=notes.append)
        self.assertEqual(len(notes), 1)
        self.assertIn("publishes no checksum", notes[0])
        self.assertFalse(info["verified"])

    def test_a_checked_download_is_recorded_as_checked(self):
        notes, info = [], {}
        addons.download_nexus(fetch=self.nexus(), opener=lambda url: FakeResponse(self.BODY), info=info,
                              note=notes.append)
        self.assertEqual(notes, [])
        self.assertTrue(info["verified"])
        self.assertEqual((info["algo"], len(info["sha"])), ("sha512", 128))
        self.assertTrue(info["url"].endswith("/app"))

    def test_arcadia_without_a_usable_checksum_is_refused(self):
        for digest in ("", "sha1:" + "a" * 40, "sha256:abc", "sha256:" + "z" * 64):
            body = json.dumps({"tag_name": "v1", "assets": [{"name": addons.ARCADIA_APPIMAGE,
                                                              "browser_download_url": "https://example.invalid/a",
                                                              **({"digest": digest} if digest else {})}]}).encode()
            with self.assertRaises(core.InstallError, msg=digest) as ctx:
                addons.download_arcadia(fetch=lambda url: body, opener=lambda url: FakeResponse(ARCADIA_BODY))
            self.assertIn("doesn't list a checksum", str(ctx.exception))
        self.assertFalse((self.home / "Applications").exists() and any((self.home / "Applications").iterdir()))

    def test_arcadia_reports_what_it_fetched(self):
        info = {}
        addons.download_arcadia(fetch=lambda url: arcadia_release(), opener=lambda url: FakeResponse(ARCADIA_BODY),
                                info=info)
        self.assertEqual((info["algo"], info["verified"], len(info["sha"])), ("sha256", True, 64))

    def test_the_download_log_has_one_line_per_download(self):
        core.record_download(self.paths, "Arcadia", "0.1.3", {"url": "https://x/a", "algo": "sha256", "sha": "ab" * 32,
                                                              "verified": True})
        core.record_download(self.paths, "Nexus Launcher", "0.5.0", {"url": "https://x/n", "verified": False})
        lines = (self.paths.logs / "downloads.log").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("Arcadia 0.1.3", lines[0])
        self.assertIn("checked against sha256 " + "ab" * 32, lines[0])
        self.assertIn("NOT checked against a published checksum", lines[1])
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\d \d\d:\d\d  ")
