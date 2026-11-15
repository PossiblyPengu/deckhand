"""The download's launcher (scripts/make_app_file.sh): one file that unpacks the app once, then starts it quickly."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKE = ROOT / "scripts/make_app_file.sh"


class TestAppFile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = self.tmp / "cache"

    def build(self, version: str) -> Path:
        dist = self.tmp / f"dist-{version}"
        (dist / "_internal").mkdir(parents=True, exist_ok=True)
        (dist / "_internal/data.txt").write_text(version)
        app = dist / "deckhand"
        app.write_text('#!/bin/sh\nhere="$(dirname "$0")"\n'
                       'echo "version=$(cat "$here/_internal/data.txt") self=$DECKHAND_SELF args=$*"\n')
        app.chmod(0o755)
        out = self.tmp / f"deckhand-{version}"
        subprocess.run(["bash", str(MAKE), str(dist), str(out)], check=True, capture_output=True)
        return out

    def run_app(self, path: Path, *args: str) -> str:
        env = {**os.environ, "XDG_CACHE_HOME": str(self.cache)}
        return subprocess.run([str(path), *args], env=env, capture_output=True, text=True, check=True).stdout.strip()

    def unpacked(self) -> list[str]:
        return sorted(p.name for p in (self.cache / "deckhand").iterdir())

    def test_unpacks_once_then_runs_the_unpacked_copy(self):
        launcher = self.build("1.0")
        self.assertEqual(self.run_app(launcher, "--version", "a b"),
                         f"version=1.0 self={launcher} args=--version a b")
        [copy] = self.unpacked()
        marker = self.cache / "deckhand" / copy / "_internal/data.txt"
        marker.write_text("touched")  # (unpacked again, this would come back as 1.0)
        self.assertIn("version=touched", self.run_app(launcher))

    def test_says_which_file_was_started_through_a_symlink(self):
        launcher = self.build("1.0")
        link = self.tmp / "bin/deckhand"
        link.parent.mkdir()
        link.symlink_to(launcher)
        self.assertIn(f"self={launcher}", self.run_app(link))

    def test_a_new_version_gets_its_own_copy(self):
        old, new = self.build("1.0"), self.build("2.0")
        self.run_app(old)
        self.assertIn("version=2.0", self.run_app(new))
        self.assertEqual(len(self.unpacked()), 2)  # (the old one goes once it's a day old)

    def test_an_old_versions_copy_goes_once_its_a_day_old(self):
        import time
        old, new = self.build("1.0"), self.build("2.0")
        self.run_app(old)
        [first] = self.unpacked()
        two_days = time.time() - 2 * 86400
        os.utime(self.cache / "deckhand" / first, (two_days, two_days))
        self.run_app(new)
        self.assertNotIn(first, self.unpacked())
        self.assertEqual(len(self.unpacked()), 1)

    def test_the_same_build_makes_the_same_file(self):
        a = self.build("1.0").read_bytes()
        os.utime(self.tmp / "dist-1.0/deckhand", (1, 1))  # (a later build: the files' own dates don't matter)
        b = self.build("1.0").read_bytes()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
