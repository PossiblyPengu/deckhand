"""Self-update: version checks against a local HTTP server standing in for GitHub."""
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import updater  # noqa: E402

NEW_APP = b"#!/bin/sh\necho new\n" + b"x" * 200_000


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Server:
    """Serves a folder over HTTP on localhost."""

    def __init__(self, root: Path):
        handler = partial(_Quiet, directory=str(root))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.handle_error = lambda *a: None  # clients hanging up mid-download (cancel test)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.web = self.tmp / "web"
        self.server = Server(self.web)
        self.sha = hashlib.sha256(NEW_APP).hexdigest()

    def tearDown(self):
        self.server.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def publish_branch(self, name: str, version: str, sha: str | None = None, notes: str = ""):
        d = self.web / name / "bin"
        d.mkdir(parents=True, exist_ok=True)
        (d / updater.ASSET).write_bytes(NEW_APP)
        (d / "deckhand.json").write_text(json.dumps({"version": version, "sha256": sha or self.sha, "notes": notes}))
        return lambda: updater.from_manifest(f"{self.server.url}/{name}/bin")

    def publish_release(self, tag: str):
        d = self.web / "rel"
        d.mkdir(parents=True, exist_ok=True)
        (d / updater.ASSET).write_bytes(NEW_APP)
        (d / f"{updater.ASSET}.sha256").write_text(f"{self.sha}  {updater.ASSET}\n")
        (d / "latest").write_text(json.dumps({
            "tag_name": tag, "body": "Faster installs.",
            "assets": [{"name": updater.ASSET, "browser_download_url": f"{self.server.url}/rel/{updater.ASSET}"},
                       {"name": f"{updater.ASSET}.sha256",
                        "browser_download_url": f"{self.server.url}/rel/{updater.ASSET}.sha256"}]}))
        return lambda: updater.from_release(f"{self.server.url}/rel/latest")

    def test_versions(self):
        self.assertTrue(updater.is_newer("2.10.0", "2.9.9"))
        self.assertTrue(updater.is_newer("v2.1.1", "2.1.0"))
        self.assertFalse(updater.is_newer("2.1.0", "2.1.0"))
        self.assertFalse(updater.is_newer("1.4.1", "2.1.0"))

    def test_picks_newest_source_and_ignores_old_or_broken(self):
        old_release = self.publish_release("v1.4.1")  # like today's only release
        main = self.publish_branch("main", "2.3.0", notes="Controller fixes")
        dev = self.publish_branch("dev", "2.2.0")
        broken = lambda: updater.from_manifest(f"{self.server.url}/missing/bin")  # noqa: E731
        offline = lambda: updater.from_manifest("http://127.0.0.1:9/bin")  # noqa: E731
        u = updater.check("2.1.0", [old_release, broken, offline, dev, main])
        self.assertEqual((u.version, u.notes), ("2.3.0", "Controller fixes"))
        self.assertIn(f"/main/bin/{updater.ASSET}?t=", u.url)  # cache-busted, like deckhand.json
        self.assertIsNone(updater.check("2.3.0", [old_release, main, dev]))
        errors = []
        self.assertIsNone(updater.check("2.1.0", [broken, offline], errors=errors))
        self.assertEqual(len(errors), 2)  # "couldn't check", not "up to date"

    def test_the_latest_release_is_the_one_place_updates_come_from(self):
        os.environ.pop("DECKHAND_UPDATE_BASE", None)
        self.assertEqual(updater.default_sources(), [updater.from_release])

    def test_release_source(self):
        u = updater.check("2.1.0", [self.publish_release("v2.5.0")])
        self.assertEqual((u.version, u.sha256, u.notes), ("2.5.0", self.sha, "Faster installs."))

    def test_download_verify_install(self):
        u = updater.check("2.1.0", [self.publish_branch("main", "2.2.0")])
        target = self.tmp / "bin" / "deckhand"
        target.parent.mkdir()
        target.write_bytes(b"old")
        seen = []
        tmp = updater.download(u, target.parent, lambda done, total: seen.append((done, total)))
        self.assertEqual(seen[-1], (len(NEW_APP), len(NEW_APP)))
        self.assertTrue(os.access(tmp, os.X_OK))
        updater.install(tmp, target)
        self.assertEqual(target.read_bytes(), NEW_APP)
        self.assertEqual(list(target.parent.iterdir()), [target])  # no leftovers

    def test_damaged_download_is_rejected(self):
        u = updater.check("2.1.0", [self.publish_branch("main", "2.2.0", sha="0" * 64)])
        with self.assertRaises(updater.UpdateError):
            updater.download(u, self.tmp / "bin")
        self.assertEqual(list((self.tmp / "bin").iterdir()), [])

    def test_cancel(self):
        u = updater.check("2.1.0", [self.publish_branch("main", "2.2.0")])
        with self.assertRaises(updater.Cancelled):
            updater.download(u, self.tmp / "bin", cancelled=lambda: True)
        self.assertEqual(list((self.tmp / "bin").iterdir()), [])

    def test_restart_env_starts_fresh(self):
        env = dict(os.environ)
        try:
            os.environ.update(_PYI_APPLICATION_HOME_DIR="/tmp/_MEIold", _MEIPASS2="/tmp/_MEIold", KEEP="1")
            e = updater.restart_env()
        finally:
            os.environ.clear()
            os.environ.update(env)
        self.assertEqual(e["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", e)
        self.assertNotIn("_MEIPASS2", e)
        self.assertEqual(e["KEEP"], "1")

    def test_env_override_for_testing(self):
        self.publish_branch("main", "9.0.0")
        os.environ["DECKHAND_UPDATE_BASE"] = f"{self.server.url}/main/bin"
        try:
            self.assertEqual(updater.check("2.1.0").version, "9.0.0")
        finally:
            del os.environ["DECKHAND_UPDATE_BASE"]

    def test_cli_from_source(self):
        out = []
        self.assertEqual(updater.cli_update("2.1.0", out.append), 1)
        self.assertIn("git pull", out[0])


if __name__ == "__main__":
    unittest.main()
