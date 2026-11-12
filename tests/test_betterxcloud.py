"""Better xCloud: installed, kept current, and never replaced by something that isn't it."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import betterxcloud as bx, core, streaming  # noqa: E402
from tests.test_streaming import FlatpakEnv  # noqa: E402


def script(version="1.0.0", name="Better xCloud", matches=("https://www.xbox.com/*/play*",), extra="") -> bytes:
    head = [f"// @name {name}", f"// @version {version}", *[f"// @match {m}" for m in matches], extra]
    return ("// ==UserScript==\n" + "\n".join(h for h in head if h) + "\n// ==/UserScript==\nconsole.log('hi');\n").encode()


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dest = self.tmp / "bxc"
        self.clock = Clock()
        self.fetched = 0

    def served(self, body):
        def fetch(url):
            self.fetched += 1
            if isinstance(body, Exception):
                raise body
            return body
        return fetch

    def run_update(self, body, **kw):
        return bx.update(self.dest, fetch=self.served(body), now=self.clock, **kw)

    # -- the header
    def test_header_is_read(self):
        h = bx.parse_header(script("6.5.1", matches=("https://www.xbox.com/*/play*", "*://*.xbox.com/x")).decode())
        self.assertEqual((h["version"], len(h["matches"])), ("6.5.1", 2))

    def test_what_is_not_better_xcloud_is_refused(self):
        for text, why in ((b"<html>rate limited</html>", "userscript"),
                          (script(name="Some Other Script"), "isn't Better xCloud"),
                          (b"// ==UserScript==\n// @name Better xCloud\n// ==/UserScript==\n", "no version"),
                          (script(matches=("*://*/*",)), "isn't xbox.com"),
                          (script(matches=("https://www.xbox.com/*", "https://example.com/*")), "example.com"),
                          (script(matches=("https://notxbox.com/*",)), "isn't xbox.com"),
                          (script(matches=("https://xbox.com.evil.example/*",)), "isn't xbox.com")):
            with self.assertRaises(ValueError, msg=text) as ctx:
                bx.parse_header(text.decode())
            self.assertIn(why, str(ctx.exception))

    def test_versions_compare_as_numbers(self):
        self.assertGreater(bx.version_key("1.10.0"), bx.version_key("1.9.9"))
        self.assertGreater(bx.version_key("2.0"), bx.version_key("1.99.99"))

    # -- installing and updating
    def test_first_install_writes_the_extension_and_its_state(self):
        r = self.run_update(script("1.0.0"))
        self.assertEqual((r.status, r.version), ("installed", "1.0.0"))
        manifest = json.loads((self.dest / "manifest.json").read_text())
        self.assertEqual((manifest["manifest_version"], manifest["version"]), (3, "1.0.0"))
        cs = manifest["content_scripts"][0]
        self.assertEqual((cs["world"], cs["run_at"], cs["matches"]), ("MAIN", "document_start",
                                                                      ["https://www.xbox.com/*/play*"]))
        self.assertEqual(bx.installed_version(self.dest), "1.0.0")
        self.assertEqual(len(bx.read_state(self.dest)["sha256"]), 64)
        self.assertTrue((self.dest / ".checked").exists())

    def test_a_newer_version_replaces_it_and_the_old_one_is_kept(self):
        self.run_update(script("1.0.0"))
        self.clock.t += bx.CHECK_EVERY + 1
        r = self.run_update(script("1.1.0", matches=("https://www.xbox.com/*/play*", "https://www.xbox.com/new/*")))
        self.assertEqual((r.status, r.version), ("updated", "1.1.0"))
        self.assertIn("from 1.0.0 to 1.1.0", r.message)
        self.assertEqual(bx.installed_version(self.dest), "1.1.0")
        # The permissions follow the new script's own header, not what was true at install time.
        cs = json.loads((self.dest / "manifest.json").read_text())["content_scripts"][0]
        self.assertIn("https://www.xbox.com/new/*", cs["matches"])
        self.assertIn("@version 1.0.0", (self.dest / "previous" / "better-xcloud.user.js").read_text())
        self.assertEqual(json.loads((self.dest / "previous" / "manifest.json").read_text())["version"], "1.0.0")

    def test_the_same_or_an_older_version_changes_nothing(self):
        self.run_update(script("1.2.0"))
        before = (self.dest / "better-xcloud.user.js").read_text()
        for v in ("1.2.0", "1.1.9"):
            self.clock.t += bx.CHECK_EVERY + 1
            r = self.run_update(script(v, extra="// changed"))
            self.assertEqual((r.status, r.version), ("current", "1.2.0"))
            self.assertEqual((self.dest / "better-xcloud.user.js").read_text(), before)

    def test_looks_at_most_every_six_hours_unless_forced(self):
        self.run_update(script("1.0.0"))
        n = self.fetched
        self.clock.t += 3600
        self.assertEqual(self.run_update(script("2.0.0")).status, "skipped")
        self.assertEqual(self.fetched, n)  # didn't even ask
        self.assertEqual(self.run_update(script("2.0.0"), force=True).status, "updated")

    # -- when it can't or mustn't update
    def test_a_script_that_asks_for_other_sites_is_refused_and_the_installed_one_stays(self):
        self.run_update(script("1.0.0"))
        self.clock.t += bx.CHECK_EVERY + 1
        r = self.run_update(script("9.9.9", matches=("*://*/*",)))
        self.assertEqual(r.status, "failed")
        self.assertIn("isn't xbox.com", r.message)
        self.assertIn("Staying on 1.0.0", r.message)
        self.assertEqual(bx.installed_version(self.dest), "1.0.0")
        self.assertIn("isn't xbox.com", bx.read_state(self.dest)["error"])

    def test_offline_keeps_what_is_installed_and_retries_in_half_an_hour(self):
        self.run_update(script("1.0.0"))
        self.clock.t += bx.CHECK_EVERY + 1
        r = self.run_update(OSError("no route to host"))
        self.assertEqual((r.status, r.version), ("failed", "1.0.0"))
        self.assertIn("couldn't reach GitHub", r.message)
        self.assertEqual(bx.installed_version(self.dest), "1.0.0")
        # The launcher's gate is the age of this file: six hours minus the retry delay means "ask again in 30 min".
        age = self.clock.t - (self.dest / ".checked").stat().st_mtime
        self.assertAlmostEqual(age, bx.CHECK_EVERY - bx.RETRY_AFTER, delta=2)

    def test_empty_and_oversized_downloads_are_refused(self):
        for body in (b"", b"x" * (bx.MAX_BYTES + 1)):
            r = self.run_update(body, force=True)
            self.assertEqual(r.status, "failed")
            self.assertIn("unexpected size", r.message)
        self.assertEqual(bx.installed_version(self.dest), "")

    def test_a_good_check_clears_an_old_error(self):
        self.run_update(script("1.0.0"))
        self.run_update(OSError("down"), force=True)
        self.assertIn("error", bx.read_state(self.dest))
        self.run_update(script("1.0.0"), force=True)
        self.assertNotIn("error", bx.read_state(self.dest))

    def test_a_half_installed_folder_is_repaired(self):
        self.run_update(script("1.0.0"))
        (self.dest / "manifest.json").unlink()
        self.assertEqual(bx.installed_version(self.dest), "")  # no longer counts as installed
        r = self.run_update(script("1.0.0"), force=True)
        self.assertEqual(r.status, "installed")
        self.assertTrue((self.dest / "manifest.json").exists())

    # -- what the screen says
    def test_summary(self):
        self.assertEqual(bx.summary(self.dest), "")
        self.run_update(script("1.4.0"))
        self.assertEqual(bx.summary(self.dest, now=self.clock), "v1.4.0, checked just now")
        self.assertEqual(bx.summary(self.dest, now=lambda: self.clock.t + 5 * 3600), "v1.4.0, checked 5 hours ago")
        self.assertEqual(bx.summary(self.dest, now=lambda: self.clock.t + 72 * 3600), "v1.4.0, checked 3 days ago")
        self.clock.t += 100
        self.run_update(OSError("x"), force=True)
        self.assertEqual(bx.summary(self.dest, now=self.clock), "v1.4.0, couldn't check for a newer one")


class TestStreamingWrapper(FlatpakEnv):
    def test_the_old_entry_point_still_installs_and_still_refuses_junk(self):
        ext = self.tmp / "ext"
        self.assertEqual(streaming.install_better_xcloud(ext, fetch=lambda u: script("3.0.0")), "3.0.0")
        with self.assertRaises(core.InstallError) as ctx:
            streaming.install_better_xcloud(self.tmp / "ext2", fetch=lambda u: b"<html>nope</html>")
        self.assertIn("refused", str(ctx.exception))

    def test_cli_prints_what_happened(self):
        from deckhand import app as app_mod
        lines = []
        os.environ["HOME"] = str(self.home)
        rc = app_mod.update_better_xcloud_cli(force=True, out=lines.append, fetch=lambda u: script("5.0.0"))
        self.assertEqual((rc, lines), (0, ["Better xCloud 5.0.0 installed."]))
        lines.clear()
        rc = app_mod.update_better_xcloud_cli(out=lines.append, fetch=lambda u: script("5.0.0"))
        self.assertEqual((rc, lines), (0, []))  # looked recently: says nothing (it runs on every Xbox launch)
        rc = app_mod.update_better_xcloud_cli(force=True, out=lines.append, fetch=lambda u: (_ for _ in ()).throw(OSError("x")))
        self.assertEqual(rc, 1)
        self.assertIn("Couldn't get Better xCloud", lines[-1])


class TestLauncherUpdates(FlatpakEnv):
    """The Xbox launcher gets the newest Better xCloud before it starts the browser."""

    def setUp(self):
        super().setUp()
        self.deckhand_log = self.tmp / "deckhand.log"
        fake = self.home / ".local/bin/deckhand"
        fake.parent.mkdir(parents=True)
        fake.write_text(f'#!/bin/bash\necho "$@" >> {self.deckhand_log}\n')
        fake.chmod(0o755)
        svc = streaming.service("xbox-cloud")
        self.app = streaming.set_up(svc, self.paths, roots=[self.steam], better_xcloud=True,
                                    fetch=lambda u: script("1.0.0"))
        self.stamp = streaming.better_xcloud_dir() / ".checked"

    def launch(self):
        env = dict(os.environ, SteamGamepadUI="1")
        return subprocess.run(["bash", self.app.launcher], env=env, capture_output=True, text=True, timeout=60)

    def calls(self):
        return self.deckhand_log.read_text().split("\n")[:-1] if self.deckhand_log.exists() else []

    def test_the_update_runs_before_the_browser_and_is_not_repeated_while_fresh(self):
        text = Path(self.app.launcher).read_text()
        self.assertLess(text.index("--update-better-xcloud"), text.index('exec flatpak run "$B"'))
        self.stamp.touch()  # (the set-up above would have made it with the real updater)
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(self.calls(), [])  # fresh: Deckhand isn't started at all
        old = time.time() - 7 * 3600
        os.utime(self.stamp, (old, old))
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(self.calls(), ["--update-better-xcloud"])

    def test_with_no_stamp_it_asks_too(self):
        self.stamp.unlink(missing_ok=True)
        self.launch()
        self.assertEqual(self.calls(), ["--update-better-xcloud"])

    def test_a_missing_deckhand_falls_back_to_a_background_download_and_still_starts(self):
        (self.home / ".local/bin/deckhand").unlink()
        text = Path(self.app.launcher).read_text()
        self.assertIn("curl -fsL", text)
        r = self.launch()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), [])  # Deckhand wasn't there to run
        flatpak = self.flatpak_log.read_text().splitlines()
        self.assertTrue([c for c in flatpak if c.startswith("run ")], "the browser still started")

    def test_a_slow_update_cannot_hold_the_launch_for_long(self):
        self.assertIn("timeout 15", Path(self.app.launcher).read_text())


if __name__ == "__main__":
    unittest.main()
