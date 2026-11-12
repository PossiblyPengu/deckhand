"""download_to against a local HTTP server that misbehaves on purpose: cuts the connection short, fails with 503,
supports (or ignores) Range."""
import hashlib
import http.server
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core, updater  # noqa: E402

BODY = bytes(range(256)) * 2000  # 512,000 bytes


class Handler(http.server.BaseHTTPRequestHandler):
    plan: list = []  # one entry per request: "ok", "cut" (half the file, then close), "503", "404"
    ranges = True
    seen: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        step = self.plan.pop(0) if self.plan else "ok"
        start = 0
        rng = self.headers.get("Range")
        self.seen.append(rng)
        if step in ("503", "404"):
            self.send_response(int(step))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if rng and self.ranges:
            start = int(rng.split("=")[1].rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(BODY) - 1}/{len(BODY)}")
        else:
            self.send_response(200)
        data = BODY[start:]
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data[: len(data) // 2] if step == "cut" else data)
        self.wfile.flush()
        self.close_connection = True


class DownloadTests(unittest.TestCase):
    def setUp(self):
        Handler.plan, Handler.ranges, Handler.seen = [], True, []
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/f"
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "out" / "file.bin"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def get(self, **kw):
        return updater.download_to(self.url, self.dest, waits=(0, 0, 0), **kw)

    def test_a_plain_download(self):
        seen = []
        d = hashlib.sha256()
        self.get(progress=lambda done, total: seen.append((done, total)), digest=d)
        self.assertEqual(self.dest.read_bytes(), BODY)
        self.assertEqual(seen[-1], (len(BODY), len(BODY)))
        self.assertEqual(d.hexdigest(), hashlib.sha256(BODY).hexdigest())
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_a_cut_connection_resumes_where_it_stopped(self):
        Handler.plan = ["cut"]
        d = hashlib.sha256()
        self.get(digest=d)
        self.assertEqual(self.dest.read_bytes(), BODY)
        self.assertEqual(d.hexdigest(), hashlib.sha256(BODY).hexdigest())
        self.assertIsNone(Handler.seen[0])
        self.assertTrue(Handler.seen[1].startswith("bytes=") and Handler.seen[1] != "bytes=0-")

    def test_a_server_that_ignores_range_is_downloaded_again_from_the_start(self):
        Handler.plan, Handler.ranges = ["cut"], False
        self.get()
        self.assertEqual(self.dest.read_bytes(), BODY)

    def test_a_server_error_is_retried(self):
        Handler.plan = ["503", "503"]
        self.get()
        self.assertEqual(self.dest.read_bytes(), BODY)
        self.assertEqual(len(Handler.seen), 3)

    def test_not_found_is_not_retried(self):
        Handler.plan = ["404"]
        with self.assertRaises(updater.UpdateError):
            self.get()
        self.assertEqual(len(Handler.seen), 1)
        self.assertFalse(self.dest.exists())

    def test_giving_up_leaves_nothing_and_says_so(self):
        Handler.plan = ["cut"] * 10
        with self.assertRaises(updater.UpdateError) as ctx:
            self.get()
        self.assertIn("stopped before it finished", str(ctx.exception))
        self.assertEqual(list(self.dest.parent.iterdir()), [])

    def test_a_short_file_is_never_kept_with_a_custom_opener(self):
        class Short:
            headers = {"Content-Length": "1000"}
            status = 200

            def __init__(self):
                self.data = [b"x" * 400, b""]

            def read(self, n):
                return self.data.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with self.assertRaises(updater.UpdateError):
            self.get(opener=lambda url: Short())
        self.assertFalse(self.dest.exists())

    def test_not_enough_space_fails_before_downloading(self):
        with mock.patch.object(core, "free_space", lambda p: 1 << 20):
            with self.assertRaises(core.InstallError) as ctx:
                self.get()
        self.assertIn("enough free space", str(ctx.exception))
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.dest.parent.iterdir()), [])

    def test_cancelling_stops_and_cleans_up(self):
        calls = []

        def cancelled():
            calls.append(1)
            return len(calls) > 2

        with self.assertRaises(updater.Cancelled):
            self.get(cancelled=cancelled)
        self.assertEqual(list(self.dest.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()


class TransferTextTests(unittest.TestCase):
    def test_speed_and_time_left_appear_once_there_is_a_speed(self):
        now = [100.0]
        x = core.Transfer("Downloading X…", clock=lambda: now[0])
        MB = 1 << 20
        self.assertEqual(x.text(0, 100 * MB), "Downloading X…  0 B of 100 MB")
        now[0] = 101.0
        self.assertNotIn("/s", x.text(5 * MB, 100 * MB))  # too early to say
        now[0] = 110.0
        self.assertEqual(x.text(50 * MB, 100 * MB), "Downloading X…  50 MB of 100 MB · 5 MB/s · 10 s left")
        self.assertEqual(x.text(100 * MB, 100 * MB), "Downloading X…  100 MB of 100 MB · 10 MB/s")

    def test_a_resumed_download_is_not_counted_as_fast(self):
        now = [0.0]
        x = core.Transfer("D", clock=lambda: now[0])
        MB = 1 << 20
        x.text(80 * MB, 100 * MB)  # the first update after resuming already has 80 MB
        now[0] = 4.0
        self.assertIn("5 MB/s", x.text(100 * MB, 200 * MB))

    def test_durations(self):
        self.assertEqual([core.human_duration(s) for s in (5, 59.6, 90, 700, 4000)],
                         ["5 s", "1 min", "1 min 30 s", "11 min", "1 h 6 min"])
