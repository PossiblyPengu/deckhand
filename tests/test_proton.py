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

    def test_a_release_with_builds_for_two_processors_gives_this_ones(self):
        # GE-Proton 11 lists the ARM build first: an x86 Deck must get the x86_64 one, with its own checksum.
        release = {"tag_name": "GE-Proton11-12", "assets": [
            {"name": "GE-Proton11-12-aarch64.sha512sum", "browser_download_url": "https://e/arm.sha512sum"},
            {"name": "GE-Proton11-12-aarch64.tar.gz", "browser_download_url": "https://e/arm.tar.gz", "size": 1},
            {"name": "GE-Proton11-12-x86_64.sha512sum", "browser_download_url": "https://e/x86.sha512sum"},
            {"name": "GE-Proton11-12-x86_64.tar.gz", "browser_download_url": "https://e/x86.tar.gz", "size": 2}]}
        fetch = lambda url: json.dumps(release).encode()  # noqa: E731
        got = proton.latest(fetch, arch="x86_64")
        self.assertEqual((got.version, got.url, got.sha512_url),
                         ("GE-Proton11-12-x86_64", "https://e/x86.tar.gz", "https://e/x86.sha512sum"))
        self.assertEqual(proton.latest(fetch, arch="aarch64").url, "https://e/arm.tar.gz")

    def test_a_build_for_another_processor_doesnt_count_and_is_cleaned_up(self):
        tools = self.steam / "compatibilitytools.d"
        arm = tools / "GE-Proton11-12-aarch64"
        (arm / "files/bin").mkdir(parents=True)
        (arm / "proton").write_text("")
        (arm / "toolmanifest.vdf").write_text('"manifest"\n{\n  "commandline" "/proton %verb%"\n}\n')
        self.assertNotIn("GE-Proton11-12-aarch64", proton.installed([self.steam]))
        self.assertEqual(proton.remove_foreign([self.steam]), ["GE-Proton11-12-aarch64"])
        self.assertFalse(arm.exists())
        self.assertTrue((tools / "GE-Proton9-20").exists())  # (the one that runs here stays)

    def test_the_same_version_with_or_without_its_processor_in_the_name(self):
        self.assertTrue(proton.has("GE-Proton11-12-x86_64", ["GE-Proton11-12"]))
        self.assertTrue(proton.has("GE-Proton11-12", ["GE-Proton11-12-x86_64"]))
        self.assertFalse(proton.has("GE-Proton11-13-x86_64", ["GE-Proton11-12"]))

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

    def test_a_release_with_no_checksum_file_is_refused(self):
        tar = ge_tarball()
        release = proton.Release("GE-Proton10-99", "https://example.invalid/ge.tar.gz", len(tar), "")  # (no .sha512sum)
        with self.assertRaises(core.InstallError) as ctx:
            proton.install(release, [self.steam], fetch=lambda url: b"", opener=lambda url: FakeResponse(tar))
        self.assertIn("no checksum file", str(ctx.exception))
        self.assertEqual(proton.installed([self.steam]), ["GE-Proton9-20"])  # nothing was installed
