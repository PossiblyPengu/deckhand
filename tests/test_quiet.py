"""Quick install (installers that take no-questions arguments) and plain-language exit codes."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core  # noqa: E402
from tests.test_core import Env  # noqa: E402


class KindTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def make(self, name: str, body: bytes) -> Path:
        p = self.tmp / name
        p.write_bytes(b"MZ" + b"\0" * 100 + body + b"\0" * 100)
        return p

    def test_kinds_from_signatures(self):
        self.assertEqual(core.installer_kind(self.make("a.exe", b"Inno Setup Setup Data (6.2.2)")), "inno")
        self.assertEqual(core.installer_kind(self.make("b.exe", b"Nullsoft Install System v3.08")), "nsis")
        self.assertEqual(core.installer_kind(self.make("c.exe", b"InstallShield")), "installshield")
        self.assertEqual(core.installer_kind(self.make("d.exe", b"just a program")), "")
        self.assertEqual(core.installer_kind(self.make("e.msi", b"")), "msi")
        self.assertEqual(core.installer_kind(self.tmp / "missing.exe"), "")

    def test_only_known_kinds_get_quiet_arguments(self):
        self.assertIn("/VERYSILENT", core.quiet_args(self.make("a.exe", b"Inno Setup Setup Data")))
        self.assertEqual(core.quiet_args(self.make("b.exe", b"Nullsoft Install System")), ("/S",))
        self.assertIn("/qb-!", core.quiet_args(self.make("c.msi", b"")))
        self.assertIsNone(core.quiet_args(self.make("d.exe", b"InstallShield")))  # not reliably silent
        self.assertIsNone(core.quiet_args(self.make("e.exe", b"")))

    def test_msi_takes_extra_arguments(self):
        rt = core.Runtime("P", "proton", "/p/proton")
        self.assertEqual(core.run_command(rt, "D:\\a.msi", "/qb-!"),
                         ["/p/proton", "run", "msiexec", "/i", "D:\\a.msi", "/qb-!"])

    def test_exit_codes_in_plain_words(self):
        self.assertEqual(core.explain_exit("inno", 0), "")
        self.assertEqual(core.explain_exit("msi", 3010), "")
        self.assertIn("cancelled", core.explain_exit("inno", 2))
        self.assertIn("1618", core.explain_exit("msi", 1618))
        self.assertIn("(code 77)", core.explain_exit("", 77))


class QuickInstallTests(Env):
    def inno(self):
        self.installer.write_bytes(b"MZ" + b"\0" * 50 + b"Inno Setup Setup Data (6.2)" + b"\0" * 50)

    def test_quick_install_passes_the_silent_arguments(self):
        self.inno()
        job = self.job(quiet=True)
        pending = job.run()
        job.close()
        args = (pending.compat_dir / "proton-args.txt").read_text()
        self.assertIn("/VERYSILENT", args)
        self.assertEqual(pending.kind, "inno")
        self.assertEqual(pending.exit_code, 0)
        self.assertTrue(pending.candidates)

    def test_a_normal_install_passes_nothing(self):
        self.inno()
        job = self.job()
        pending = job.run()
        job.close()
        self.assertNotIn("/VERYSILENT", (pending.compat_dir / "proton-args.txt").read_text())

    def test_quick_install_refuses_an_installer_that_cannot_do_it(self):
        with self.assertRaises(core.InstallError) as ctx:
            self.job(quiet=True).run()
        self.assertIn("without questions", str(ctx.exception))
        self.assertEqual(list(self.paths.prefixes.iterdir()), [])  # nothing left behind

    def test_a_quick_install_that_leaves_nothing_says_why_and_cleans_up(self):
        self.inno()
        os.environ["FAKE_RC"] = "2"
        with self.assertRaises(core.InstallError) as ctx:
            self.job(quiet=True).run()
        text = str(ctx.exception)
        self.assertIn("didn't leave a program", text)
        self.assertIn("cancelled", text)
        self.assertIn("Install instead", text)
        self.assertEqual(list(self.paths.prefixes.iterdir()), [])

    def test_the_exit_code_is_kept_for_the_pick_page(self):
        self.inno()
        os.environ["FAKE_RC"] = "5"
        job = self.job()
        pending = job.run()
        job.close()
        self.assertEqual((pending.exit_code, pending.kind), (5, "inno"))
        self.assertIn("closed before it finished", core.explain_exit(pending.kind, pending.exit_code))


if __name__ == "__main__":
    unittest.main()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def zip(self, name, files):
        import zipfile
        p = self.tmp / name
        with zipfile.ZipFile(p, "w") as z:
            for n, data in files.items():
                z.writestr(n, data)
        return p

    def test_the_setup_file_is_found_in_a_zip(self):
        z = self.zip("game.zip", {"readme.txt": "hi", "Game/data/engine.exe": "x" * 5000,
                                  "Game/setup.exe": "MZ", "Game/vc_redist.x64.exe": "x" * 9000})
        setup = core.extract_archive(z, self.tmp / "out")
        self.assertEqual(setup, self.tmp / "out/Game/setup.exe")

    def test_without_a_setup_name_the_top_biggest_exe_wins(self):
        z = self.zip("a.zip", {"deep/er/x.exe": "x" * 9000, "top.exe": "x" * 10, "top2.exe": "x" * 99})
        self.assertEqual(core.extract_archive(z, self.tmp / "o").name, "top2.exe")

    def test_a_zip_with_no_installer_says_so_and_leaves_nothing(self):
        z = self.zip("docs.zip", {"a.txt": "x"})
        with self.assertRaises(core.InstallError) as ctx:
            core.extract_archive(z, self.tmp / "o")
        self.assertIn("no setup file", str(ctx.exception))
        self.assertFalse((self.tmp / "o").exists())

    def test_files_that_would_escape_are_refused(self):
        z = self.zip("evil.zip", {"../escaped.exe": "x", "setup.exe": "x"})
        with self.assertRaises(core.InstallError):
            core.extract_archive(z, self.tmp / "o")
        self.assertFalse((self.tmp / "escaped.exe").exists())
        self.assertFalse((self.tmp / "o").exists())

    def test_a_damaged_zip_is_explained(self):
        bad = self.tmp / "bad.zip"
        bad.write_bytes(b"not a zip")
        with self.assertRaises(core.InstallError) as ctx:
            core.extract_archive(bad, self.tmp / "o")
        self.assertIn("couldn't be opened", str(ctx.exception))

    def test_other_formats_without_a_tool_are_explained(self):
        from unittest import mock
        rar = self.tmp / "g.rar"
        rar.write_bytes(b"Rar!")
        with mock.patch.object(core.shutil, "which", lambda t: None):
            with self.assertRaises(core.InstallError) as ctx:
                core.extract_archive(rar, self.tmp / "o")
        self.assertIn("Ark or Dolphin", str(ctx.exception))

    def test_other_formats_use_the_tool_the_deck_has(self):
        from unittest import mock
        seen = []

        def fake_run(cmd, **kw):
            seen.append(cmd)
            out = Path(cmd[cmd.index("-C") + 1])
            (out / "Setup.exe").write_text("MZ")
            return mock.Mock(returncode=0, stdout="", stderr="")

        iso = self.tmp / "g.iso"
        iso.write_bytes(b"x")
        with mock.patch.object(core.shutil, "which", lambda t: "/usr/bin/bsdtar" if t == "bsdtar" else None), \
                mock.patch.object(core.subprocess, "run", fake_run):
            setup = core.extract_archive(iso, self.tmp / "o")
        self.assertEqual(setup.name, "Setup.exe")
        self.assertEqual(seen[0][:2], ["bsdtar", "-xf"])
