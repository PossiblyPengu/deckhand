"""GE-Proton from (a faked) GitHub: checked, unpacked into Steam's compatibility tools, used for installs."""
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core, proton  # noqa: E402
from tests.test_addons import FakeResponse  # noqa: E402
from tests.test_core import Env  # noqa: E402


def ge_tarball(top: str = "GE-Proton10-99", extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        files = {f"{top}/proton": b"#!/usr/bin/env python3\n", f"{top}/files/bin/wineserver": b"#!/bin/sh\n",
                 f"{top}/toolmanifest.vdf": b'"manifest" { "require_tool_appid" "1628350" }\n', **(extra or {})}
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo(f"{top}/files/lib/libfoo.so")
        link.type, link.linkname = tarfile.SYMTYPE, "libfoo.so.1"
        tf.addfile(link)
    return buf.getvalue()


class TestGEProton(Env):
    def serve(self, tar: bytes, sha: str | None = None):
        release = {"tag_name": "GE-Proton10-99", "assets": [
            {"name": "GE-Proton10-99.sha512sum", "browser_download_url": "https://example.invalid/ge.sha512sum"},
            {"name": "GE-Proton10-99.tar.gz", "browser_download_url": "https://example.invalid/ge.tar.gz",
             "size": len(tar)}]}
        sha = sha or hashlib.sha512(tar).hexdigest()
        files = {proton.RELEASES_API: json.dumps(release).encode(),
                 "https://example.invalid/ge.sha512sum": f"{sha}  GE-Proton10-99.tar.gz\n".encode(),
                 "https://example.invalid/ge.tar.gz": tar}
        return (lambda url: files[url]), (lambda url: FakeResponse(files[url]))

    def test_the_newest_release_is_installed_where_steam_and_deckhand_find_it(self):
        fetch, opener = self.serve(ge_tarball())
        release = proton.latest(fetch)
        self.assertEqual((release.version, release.url), ("GE-Proton10-99", "https://example.invalid/ge.tar.gz"))
        self.assertEqual(proton.installed([self.steam]), ["GE-Proton9-20"])
        seen = []
        path = proton.install(release, [self.steam], progress=lambda d, t: seen.append((d, t)), fetch=fetch,
                              opener=opener)
        self.assertEqual(path, self.steam / "compatibilitytools.d/GE-Proton10-99")
        self.assertTrue((path / "proton").is_file() and (path / "files/lib/libfoo.so").is_symlink())
        self.assertEqual(seen[-1][0], seen[-1][1])
        self.assertEqual(proton.installed([self.steam]), ["GE-Proton10-99", "GE-Proton9-20"])
        self.assertEqual(core.find_runtimes([self.steam], extra_tool_dirs=[])[0].name, "GE-Proton10-99")
        self.assertEqual([p.name for p in path.parent.iterdir()], sorted(["GE-Proton9-20", "GE-Proton10-99"],
                                                                         key=[p.name for p in path.parent.iterdir()].index))
        self.assertFalse([p for p in path.parent.iterdir() if p.name.startswith(".")])  # nothing half-done left
        self.assertEqual(proton.install(release, [self.steam], fetch=fetch, opener=opener), path)  # already there

    def test_a_damaged_download_leaves_nothing(self):
        fetch, opener = self.serve(ge_tarball(), sha="0" * 128)
        with self.assertRaises(core.InstallError) as ctx:
            proton.install(proton.latest(fetch), [self.steam], fetch=fetch, opener=opener)
        self.assertIn("checksum", str(ctx.exception))
        self.assertEqual(sorted(p.name for p in (self.steam / "compatibilitytools.d").iterdir()), ["GE-Proton9-20"])

    def test_a_tarball_that_would_write_elsewhere_is_refused(self):
        for tar in (ge_tarball(extra={"GE-Proton10-99/../../evil": b"x"}), ge_tarball(extra={"other/proton": b"x"})):
            fetch, opener = self.serve(tar)
            with self.assertRaises(core.InstallError):
                proton.install(proton.latest(fetch), [self.steam], fetch=fetch, opener=opener)
            self.assertFalse((self.steam / "evil").exists())
            self.assertFalse((self.steam / "compatibilitytools.d/GE-Proton10-99").exists())

    def test_no_release_asset_is_explained(self):
        with self.assertRaises(core.InstallError):
            proton.latest(lambda url: json.dumps({"tag_name": "x", "assets": []}).encode())
