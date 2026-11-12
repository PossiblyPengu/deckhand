"""Asking Steam for its keyboard, and keeping Steam's overlay out of what Deckhand starts."""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core  # noqa: E402
from tests.test_core import Env  # noqa: E402


class TestSteamKeyboard(Env):
    def fake_steam(self):
        bindir = self.tmp / "kbin"
        bindir.mkdir()
        log = self.tmp / "steam.log"
        steam = bindir / "steam"
        steam.write_text(f'#!/bin/bash\necho "$@" >> {log}\n')
        steam.chmod(0o755)
        os.environ["PATH"] = str(bindir)
        return log

    def test_the_keyboard_request_goes_to_steam(self):
        log = self.fake_steam()
        self.assertTrue(core.open_steam_keyboard())
        self.assertEqual(log.read_text().split(), ["steam://open/keyboard"])

    def test_without_steam_or_xdg_open_it_says_it_could_not(self):
        os.environ["PATH"] = str(self.tmp)  # (nothing runnable)
        self.assertFalse(core.open_steam_keyboard())

    def test_asking_for_the_keyboard_never_holds_up_the_caller(self):
        from unittest import mock
        done = []

        def slow() -> bool:
            time.sleep(0.4)
            done.append(1)
            return True
        with mock.patch.object(core, "open_steam_keyboard", slow):
            start = time.monotonic()
            core.show_steam_keyboard()
            self.assertLess(time.monotonic() - start, 0.2)  # returns at once ...
            for _ in range(50):
                if done:
                    break
                time.sleep(0.02)
            self.assertEqual(done, [1])  # ... and the request still goes out

    def test_adding_to_steam_still_hands_over_its_url(self):
        log = self.fake_steam()
        desktop = self.tmp / "x.desktop"
        desktop.write_text("[Desktop Entry]\n")
        self.assertTrue(core.request_steam_add(desktop))
        self.assertTrue(log.read_text().startswith("steam://addnonsteamgame/"))


class TestSteamOverlayOutOfChildren(Env):
    def clean(self, preload):
        if preload is None:
            os.environ.pop("LD_PRELOAD", None)
        else:
            os.environ["LD_PRELOAD"] = preload
        return core.clean_env().get("LD_PRELOAD")

    def test_the_steam_overlay_is_removed(self):
        both = ("/home/deck/.local/share/Steam/ubuntu12_32/gameoverlayrenderer.so:"
                "/home/deck/.local/share/Steam/ubuntu12_64/gameoverlayrenderer.so")
        self.assertIsNone(self.clean(both))
        self.assertIsNone(self.clean(both.replace(":", " ")))  # (it may be space separated)

    def test_other_preloads_stay(self):
        self.assertEqual(self.clean("/x/gameoverlayrenderer.so:/lib/libfoo.so"), "/lib/libfoo.so")
        self.assertEqual(self.clean("/lib/libfoo.so"), "/lib/libfoo.so")

    def test_no_preload_stays_none(self):
        self.assertIsNone(self.clean(None))
        self.assertEqual(self.clean(""), "")  # (someone set it empty on purpose: left as they set it)

    def test_programs_deckhand_starts_get_no_overlay_and_print_no_errors(self):
        import subprocess
        os.environ["LD_PRELOAD"] = "/nonexistent/ubuntu12_32/gameoverlayrenderer.so"
        r = subprocess.run(["bash", "-c", "echo hello"], env=core.clean_env(), capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "hello")
        self.assertNotIn("LD_PRELOAD", r.stderr)
        loud = subprocess.run(["bash", "-c", "echo hello"], env=os.environ.copy(), capture_output=True, text=True)
        self.assertIn("LD_PRELOAD", loud.stderr)  # (without the fix: exactly the noise from the photo)

    def test_the_proton_environment_is_clean_too(self):
        os.environ["LD_PRELOAD"] = "/x/gameoverlayrenderer.so"
        rt = core.Runtime("P", "proton", "/p/proton")
        env = core.runtime_env(rt, self.tmp / "compat", None)
        self.assertNotIn("LD_PRELOAD", env)


if __name__ == "__main__":
    unittest.main()
