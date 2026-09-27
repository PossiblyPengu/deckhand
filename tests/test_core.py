"""Headless tests for the install engine. A fake Proton stands in for the real one."""
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core  # noqa: E402


def make_lnk(target: str | None, args: str = "", workdir: str = "", relative: str = "") -> bytes:
    """A Shell Link like Wine writes: LinkInfo local base path, plus optional Unicode StringData
    (relative path, working dir, arguments)."""
    flags = 0x80  # IsUnicode
    info = b""
    if target:
        flags |= 0x02  # HasLinkInfo
        volume = struct.pack("<IIII", 0x11, 3, 0, 0x10) + b"\x00"
        base = target.encode("cp1252") + b"\x00"
        hdr = 0x1C
        size = hdr + len(volume) + len(base) + 1
        info = struct.pack("<7I", size, hdr, 1, hdr, hdr + len(volume), 0, size - 1) + volume + base + b"\x00"
    strings = b""
    for bit, value in ((0x08, relative), (0x10, workdir), (0x20, args)):
        if value:
            flags |= bit
            strings += struct.pack("<H", len(value)) + value.encode("utf-16-le")
    header = bytearray(0x4C)
    header[0:4] = b"L\x00\x00\x00"
    struct.pack_into("<I", header, 0x14, flags)
    return bytes(header) + info + strings


def make_pe(icon: bytes, width: int = 48, bits: int = 32) -> bytes:
    """A minimal PE32+ .exe whose only content is a resource section holding one icon."""
    def rdir(entries):  # IMAGE_RESOURCE_DIRECTORY + entries
        return struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(entries)) + b"".join(
            struct.pack("<II", i, t) for i, t in entries)

    group = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHIH", width, width, 0, 0, 1, bits, len(icon), 1)
    # layout (offsets inside .rsrc): root | icon type dir | icon lang dir | group type dir | group lang dir
    #                                  | 2 data entries | icon data | group data
    ityp_o, ilang_o, gtyp_o, glang_o = 32, 56, 80, 104  # root directory is at 0
    ide_o, gde_o = 128, 144
    icon_o = 160
    group_o = icon_o + len(icon) + (-len(icon)) % 4
    rva = 0x1000
    rsrc = bytearray()
    rsrc += rdir([(3, 0x80000000 | ityp_o), (14, 0x80000000 | gtyp_o)])
    rsrc += rdir([(1, 0x80000000 | ilang_o)])
    rsrc += rdir([(0x409, ide_o)])
    rsrc += rdir([(1, 0x80000000 | glang_o)])
    rsrc += rdir([(0x409, gde_o)])
    rsrc += struct.pack("<IIII", rva + icon_o, len(icon), 0, 0)
    rsrc += struct.pack("<IIII", rva + group_o, len(group), 0, 0)
    assert len(rsrc) == icon_o
    rsrc += icon + b"\0" * ((-len(icon)) % 4) + group
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 64)
    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x22)
    opt = bytearray(240)
    struct.pack_into("<H", opt, 0, 0x20B)
    struct.pack_into("<II", opt, 112 + 16, rva, len(rsrc))
    sec = struct.pack("<8sIIIIIIHHI", b".rsrc", len(rsrc), rva, len(rsrc), 0x400, 0, 0, 0, 0, 0x40000040)
    head = bytes(dos) + b"PE\0\0" + coff + bytes(opt) + sec
    return head + b"\0" * (0x400 - len(head)) + bytes(rsrc)


def bmp_icon(size: int = 16) -> bytes:
    """An icon image the way most .exe files store it: a 32-bit DIB (height doubled for the mask)."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = bytes([40, 80, 220, 255]) * (size * size)  # BGRA: orange-ish blue
    mask = b"\0" * (((size + 31) // 32) * 4 * size)
    return header + pixels + mask


def png_icon() -> bytes:
    """A real 2x2 PNG, built by hand so no image library is needed."""
    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data))
    raw = b"".join(b"\0" + bytes([255, 0, 0, 255]) * 2 for _ in range(2))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


FAKE_PROTON = """#!/bin/bash
[ "$1" = run ] || [ "$1" = waitforexitandrun ] || exit 2
[ -n "$FAKE_BROKEN" ] && { echo "Traceback: proton exploded"; exit 1; }
# Like GE-Proton's protonfixes: without an app id, it needs a number in the compat path.
# (Checks only the folder name: temp dirs have random digits, /home/deck/... paths don't.)
if [ -z "$SteamAppId$SteamGameId$UMU_ID" ] && ! [[ "$(basename "$STEAM_COMPAT_DATA_PATH")" =~ [0-9] ]]; then
  echo "IndexError: list index out of range"; exit 1
fi
pfx="$STEAM_COMPAT_DATA_PATH/pfx"
c="$pfx/drive_c"
# Like real Proton on every launch: create C: and Z: if they're missing (not if they exist).
mkdir -p "$c/windows/system32" "$pfx/dosdevices"
[ -L "$pfx/dosdevices/c:" ] || ln -s ../drive_c "$pfx/dosdevices/c:"
[ -L "$pfx/dosdevices/z:" ] || ln -s / "$pfx/dosdevices/z:"
echo "$1 $2" >> "$STEAM_COMPAT_DATA_PATH/proton-calls.txt"
[ "$2" = wineboot.exe ] && exit 0
if [ "$2" = cmd.exe ]; then  # prefix setup / the no-op used to wait for Wine
  touch "$pfx/system.reg"
  exit 0
fi
echo "$*" >> "$STEAM_COMPAT_DATA_PATH/proton-args.txt"
if [[ "$4" == /T:* ]]; then  # a self-extractor (DirectX's redistributable) unpacking itself
  mkdir -p "$c/deckhand-directx" && touch "$c/deckhand-directx/DXSETUP.exe"; exit 0
fi
[ -n "$FAKE_RC" ] && exit "$FAKE_RC"
ls "$pfx/dosdevices" > "$STEAM_COMPAT_DATA_PATH/drives-during-install.txt"
readlink "$pfx/dosdevices/z:" > "$STEAM_COMPAT_DATA_PATH/z-during-install.txt"
echo "$2" > "$STEAM_COMPAT_DATA_PATH/installer-arg.txt"
[ -n "$FAKE_SLEEP" ] && sleep "$FAKE_SLEEP"
mkdir -p "$c/Program Files/Cool Game/bin" "$c/users/steamuser/Desktop" \\
         "$c/users/steamuser/AppData/Local/Temp"
touch "$c/windows/system32/notepad.exe" "$c/users/steamuser/AppData/Local/Temp/setup-helper.exe"
[ -n "$FAKE_NOTHING" ] && exit 0
if [ -n "$FAKE_TO_D" ]; then  # user picked D:\\Games\\Cool Game in the installer
  mkdir -p "$pfx/dosdevices/d:/Games/Cool Game"
  head -c 3000000 /dev/zero > "$pfx/dosdevices/d:/Games/Cool Game/CoolGame.exe"
  touch "$pfx/dosdevices/d:/Games/Cool Game/unins000.exe"
  exit 0
fi
if [ -n "$FAKE_EXE_SRC" ]; then cp "$FAKE_EXE_SRC" "$c/Program Files/Cool Game/bin/CoolGame.exe"
else head -c 3000000 /dev/zero > "$c/Program Files/Cool Game/bin/CoolGame.exe"; fi
touch "$c/Program Files/Cool Game/unins000.exe" "$c/Program Files/Cool Game/bin/CrashReporter.exe"
[ -n "$FAKE_LNK" ] && cp "$FAKE_LNK" "$c/users/steamuser/Desktop/Cool Game Deluxe.lnk"
echo "installed $2"
"""


class Env(unittest.TestCase):
    def setUp(self):
        self.env_backup = dict(os.environ)
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = core.Paths(self.tmp / "data")
        # Fake Steam with one user and one Proton
        self.steam = self.tmp / "Steam"
        (self.steam / "steamapps/common").mkdir(parents=True)
        (self.steam / "userdata/12345/config").mkdir(parents=True)
        proton_dir = self.steam / "compatibilitytools.d/GE-Proton9-20"
        (proton_dir / "files/bin").mkdir(parents=True)
        self.proton = proton_dir / "proton"
        self.proton.write_text(FAKE_PROTON)
        self.proton.chmod(0o755)
        # Steam Linux Runtime (sniper) that this Proton requires, like the real ones
        (proton_dir / "toolmanifest.vdf").write_text(
            '"manifest"\n{\n  "commandline" "/proton %verb%"\n  "require_tool_appid" "1628350"\n}\n')
        sniper = self.steam / "steamapps/common/SteamLinuxRuntime_sniper"
        sniper.mkdir(parents=True)
        (self.steam / "steamapps/appmanifest_1628350.acf").write_text(
            '"AppState"\n{\n  "appid" "1628350"\n  "installdir" "SteamLinuxRuntime_sniper"\n}\n')
        self.entry_log = self.tmp / "entry.log"
        self.entry = sniper / "_v2-entry-point"
        self.entry.write_text('#!/bin/bash\necho "$@" >> "$FAKE_ENTRY_LOG"\n'
                              '[[ "$1" == --verb=* ]] && [ "$2" = -- ] || exit 3\n'
                              'shift 2\nexec "$@"\n')
        self.entry.chmod(0o755)
        os.environ["FAKE_ENTRY_LOG"] = str(self.entry_log)
        ws = proton_dir / "files/bin/wineserver"
        ws.write_text("#!/bin/bash\nexit 0\n")
        ws.chmod(0o755)
        self.installer = self.tmp / "setup_cool_game_v1.2.3_(12345).exe"
        self.installer.write_bytes(b"MZ")
        self.home = self.tmp / "home"
        os.environ.pop("DECKHAND_NO_CONTAINER", None)
        (self.home / "Downloads").mkdir(parents=True)
        os.environ["HOME"] = str(self.home)
        # Steam is closed unless a test says otherwise (a real one on this machine mustn't matter).
        self._real_steam_is_running = core.steam_is_running
        core.steam_is_running = lambda: False

    def tearDown(self):
        core.steam_is_running = self._real_steam_is_running
        os.environ.clear()
        os.environ.update(self.env_backup)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def job(self, **kw):
        return core.Installer(self.installer, self.paths, steam_roots_override=[self.steam], **kw)


class TestNaming(unittest.TestCase):
    def test_guess_name(self):
        cases = {
            "setup_the_witcher_3_wild_hunt_goty_1.32_(a)_(10709).exe": "The Witcher 3 Wild Hunt Goty",
            "ChromeSetup.exe": "Chrome",
            "npp.8.6.Installer.x64.exe": "Npp",
            "Firefox Setup 120.0.exe": "Firefox",
            "7z2301-x64.exe": "7z2301",
            "setup.exe": "setup",
            "Battle.net-Setup.exe": "Battle Net",
        }
        for f, want in cases.items():
            self.assertEqual(core.guess_name(f), want, f)

    def test_plain_setup_named_after_folder(self):
        self.assertEqual(core.guess_name("/x/Some Game [GOG]/setup.exe"), "Some Game")
        self.assertEqual(core.guess_name("/x/Cool_Tool_v2.1/install.exe"), "Cool Tool")
        self.assertEqual(core.guess_name("/home/deck/Downloads/setup.exe"), "setup")

    def test_slugify(self):
        self.assertEqual(core.slugify("The Witcher 3: GOTY"), "the-witcher-3-goty")
        self.assertEqual(core.slugify("!!!"), "app")


class TestLnk(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(core.lnk_target(make_lnk(r"C:\Program Files\X\x.exe")), r"C:\Program Files\X\x.exe")
        self.assertIsNone(core.lnk_target(b"garbage"))

    def test_windows_to_unix_case_insensitive(self):
        with tempfile.TemporaryDirectory() as d:
            pfx = Path(d)
            exe = pfx / "drive_c/Program Files/Game/Game.exe"
            exe.parent.mkdir(parents=True)
            exe.touch()
            self.assertEqual(core.windows_to_unix(pfx, r"c:\PROGRAM FILES\game\game.EXE"), exe)
            self.assertIsNone(core.windows_to_unix(pfx, r"C:\nope.exe"))


class TestIcons(unittest.TestCase):
    def test_png_icon_returned_as_is(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "a.exe"
            exe.write_bytes(make_pe(png_icon(), width=0))
            self.assertEqual(core.exe_icon_data(exe), png_icon())

    def test_bmp_icon_wrapped_as_ico(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "a.exe"
            icon = bmp_icon()
            exe.write_bytes(make_pe(icon, width=16))
            data = core.exe_icon_data(exe)
            self.assertEqual(data[:6], struct.pack("<HHH", 0, 1, 1))
            self.assertEqual(struct.unpack_from("<I", data, 6 + 12)[0], 22)  # image offset
            self.assertEqual(data[22:], icon)

    def test_not_an_exe(self):
        with tempfile.TemporaryDirectory() as d:
            for content in (b"", b"MZ" + b"\0" * 100, b"hello"):
                f = Path(d) / "x.exe"
                f.write_bytes(content)
                self.assertIsNone(core.exe_icon_data(f))
        self.assertIsNone(core.exe_icon_data(Path("/nonexistent.exe")))


class TestFindingInstallers(unittest.TestCase):
    def test_find_sort_and_parts(self):
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d)
            (dl / "Game [GOG]").mkdir()
            gog = dl / "Game [GOG]" / "setup_game_1.0.exe"
            gog.write_bytes(b"x" * 100)
            (dl / "Game [GOG]" / "setup_game_1.0-1.bin").write_bytes(b"x" * 1000)
            (dl / "Game [GOG]" / "unins000.exe").write_bytes(b"x")
            old = dl / "tool.msi"
            old.write_bytes(b"x" * 10)
            os.utime(old, (1, 1))
            (dl / "readme.txt").write_text("x")
            (dl / ".hidden").mkdir()
            (dl / ".hidden" / "secret.exe").write_bytes(b"x")
            deep = dl / "a" / "b" / "c"
            deep.mkdir(parents=True)
            (deep / "too-deep.exe").write_bytes(b"x")
            found = core.find_installers([dl])
            self.assertEqual([f.path.name for f in found], ["setup_game_1.0.exe", "tool.msi"])
            self.assertEqual(found[0].size, 1100)  # exe + its .bin part

    def test_installer_files_and_delete(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "setup_x.exe"
            exe.write_bytes(b"x" * 5)
            for n in ("setup_x-1.bin", "setup_x-2.bin", "SETUP_X.BIN", "other-1.bin"):
                (Path(d) / n).write_bytes(b"y" * 10)
            files = core.installer_files(exe)
            self.assertEqual(sorted(f.name for f in files),
                             ["SETUP_X.BIN", "setup_x-1.bin", "setup_x-2.bin", "setup_x.exe"])
            listed = list(Path(d).iterdir())
            self.assertEqual(sorted(core.installer_files(exe, listed)), sorted(files))  # same, from a listing
            self.assertEqual(core.delete_files(files), 35)
            self.assertEqual([p.name for p in Path(d).iterdir()], ["other-1.bin"])

    def test_removable_media(self):
        mounts = ("/dev/mmcblk0p1 /run/media/deck/SD\\040Card ext4 rw 0 0\n"
                  "/dev/nvme0n1p8 /home ext4 rw 0 0\n"
                  "/dev/sda1 /run/media/deck/USB vfat rw 0 0\n")
        self.assertEqual(core.removable_media(mounts),
                         [Path("/run/media/deck/SD Card"), Path("/run/media/deck/USB")])


class TestGamepad(unittest.TestCase):
    def test_nodes(self):
        from deckhand import gamepad
        text = ("I: Bus=0003\nN: Name=\"Microsoft X-Box 360 pad 0\"\nH: Handlers=event12 js0 \n\n"
                "I: Bus=0011\nN: Name=\"AT keyboard\"\nH: Handlers=sysrq kbd event3\n")
        self.assertEqual(gamepad.joystick_event_nodes(text), ["/dev/input/event12"])

    def test_buttons_hat_and_stick(self):
        from deckhand import gamepad as g
        st = g.PadState()
        self.assertEqual(st.feed(g.EV_KEY, 0x130, 1), [("a", True)])
        self.assertEqual(st.feed(g.EV_KEY, 0x130, 2), [])  # autorepeat ignored
        self.assertEqual(st.feed(g.EV_KEY, 0x130, 0), [("a", False)])
        self.assertEqual(st.feed(g.EV_ABS, g.ABS_HAT0Y, -1), [("up", True)])
        self.assertEqual(st.feed(g.EV_ABS, g.ABS_HAT0Y, 0), [("up", False)])
        self.assertEqual(st.feed(g.EV_ABS, g.ABS_X, 20000), [("right", True)])
        self.assertEqual(st.feed(g.EV_ABS, g.ABS_X, 12000), [])  # hysteresis keeps it held
        self.assertEqual(st.feed(g.EV_ABS, g.ABS_X, 2000), [("right", False)])
        self.assertEqual(st.feed(g.EV_ABS, g.ABS_X, -30000), [("left", True)])


class TestLnkDetails(unittest.TestCase):
    def test_args_workdir_relative(self):
        data = make_lnk(r"C:\Games\X\x.exe", args='-windowed "some profile"', workdir=r"C:\Games\X\data")
        info = core.lnk_info(data)
        self.assertEqual((info.target, info.workdir, info.args),
                         (r"C:\Games\X\x.exe", r"C:\Games\X\data", '-windowed "some profile"'))
        self.assertEqual(core.split_windows_args(info.args), ["-windowed", "some profile"])
        only_rel = core.lnk_info(make_lnk(None, relative=r"..\..\Games\X\x.exe"))
        self.assertEqual((only_rel.target, only_rel.relative), (None, r"..\..\Games\X\x.exe"))
        self.assertIsNone(core.lnk_info(make_lnk(None)))

    def test_split_windows_args(self):
        self.assertEqual(core.split_windows_args('a "b c" d'), ["a", "b c", "d"])
        self.assertEqual(core.split_windows_args('-path="C:\\x y"  -z'), ["-path=C:\\x y", "-z"])
        self.assertEqual(core.split_windows_args('""'), [""])
        self.assertEqual(core.split_windows_args(""), [])


class TestVdf(unittest.TestCase):
    def test_roundtrip(self):
        data = {"shortcuts": {"0": {"appid": -5, "AppName": "Ä", "LastPlayTime": 7,
                                    "big": core.U64(2**40), "tags": {"0": "x"}}}}
        raw = core.vdf_dumps(data)
        self.assertEqual(core.vdf_loads(raw), data)
        self.assertEqual(core.vdf_dumps(core.vdf_loads(raw)), raw)

    def test_rejects_unknown_type(self):
        with self.assertRaises(ValueError):
            core.vdf_loads(b"\x00shortcuts\x00\x09x\x00\x08\x08")


class TestProgress(unittest.TestCase):
    def test_write_meter_counts_free_space_going_down(self):
        free = {"home": 50_000_000_000}
        m = core.WriteMeter([Path("/"), Path("/")], now=0.0, free=lambda p: free["home"])
        self.assertEqual(len(m.drives), 1)  # the same drive is counted once
        free["home"] -= 300 << 20
        written, rate = m.sample(now=2.0)
        self.assertEqual(written, 300 << 20)
        self.assertAlmostEqual(rate, (300 << 20) / 2.0)
        self.assertEqual(m.idle_for(now=2.0), 0.0)
        written, rate = m.sample(now=62.0)  # nothing more written for a minute
        self.assertEqual((written, m.idle_for(now=62.0)), (300 << 20, 60.0))
        free["home"] += 1 << 30  # something else freed space: never negative
        self.assertEqual(m.sample(now=63.0)[0], 0)

    def test_install_estimate(self):
        gb = 1 << 30
        self.assertEqual(core.install_estimate(gb, 4 * gb), 25)
        self.assertIsNone(core.install_estimate(5 * gb, 4 * gb))  # outgrew the estimate: no fake numbers
        self.assertIsNone(core.install_estimate(1 << 20, 50 << 20))  # small installers: nothing to go on
        self.assertEqual(core.install_estimate(4 * gb - 1, 4 * gb), 99)

    def test_flatpak_progress(self):
        from deckhand import streaming
        p = streaming.FlatpakProgress()
        self.assertIsNone(p.feed("Looking for matches…"))
        self.assertEqual(p.feed("Installing 1/2… ████▌ 50%  3.1 MB/s  00:10"), 25)
        self.assertEqual(p.feed("Installing 2/2… ██ 20%  2.0 MB/s"), 60)
        self.assertEqual(p.feed("Installing 2/2… 100%"), 100)


class TestSteamShortcuts(Env):
    def test_add_replace_remove(self):
        cfg = self.steam / "userdata/12345/config"
        existing = {"shortcuts": {"0": {"appid": 1, "AppName": "Other", "Exe": '"/x"'}}}
        (cfg / "shortcuts.vdf").write_bytes(core.vdf_dumps(existing))
        appid, n = core.add_steam_shortcut("Cool", "/l/cool.sh", "/g", roots=[self.steam])
        self.assertEqual(n, 1)
        core.add_steam_shortcut("Cool", "/l/cool.sh", "/g", roots=[self.steam])  # no duplicate
        sc = core.vdf_loads((cfg / "shortcuts.vdf").read_bytes())["shortcuts"]
        self.assertEqual([e["AppName"] for e in sc.values()], ["Other", "Cool"])
        self.assertEqual(sc["1"]["appid"] & 0xFFFFFFFF, appid)
        self.assertTrue(appid & 0x80000000)
        self.assertTrue((cfg / "shortcuts.vdf.deckhand-bak").exists())
        core.remove_steam_shortcut(appid, roots=[self.steam])
        sc = core.vdf_loads((cfg / "shortcuts.vdf").read_bytes())["shortcuts"]
        self.assertEqual([e["AppName"] for e in sc.values()], ["Other"])

    def test_duplicates_are_found_and_removed_keeping_one_of_each(self):
        cfg = self.steam / "userdata/12345/config"
        launchers = self.tmp / "launchers"

        def sc(appid, name, exe, **kw):
            return {"appid": appid, "AppName": name, "Exe": exe, "StartDir": '"/g"', "LaunchOptions": "", **kw}

        entries = [sc(1, "Emu", '"/emu"'), sc(2, "Game", f'"{launchers}/game.sh"'), sc(1, "Emu", '"/emu"'),
                   sc(3, "Emu", '"/emu"', LaunchOptions="-x"),  # different options: a different shortcut
                   sc(4, "My Game", f'"{launchers}/game.sh"'),  # same program, another name: still a copy
                   sc(5, "Emu", '"/emu"')]
        (cfg / "shortcuts.vdf").write_bytes(core.vdf_dumps({"shortcuts": {str(i): e for i, e in enumerate(entries)}}))
        self.assertEqual(sorted(core.find_duplicate_shortcuts([self.steam], launchers)), ["Emu", "Emu", "My Game"])
        n = core.remove_duplicate_shortcuts([self.steam], launchers, keep=[4])  # 4: the id Deckhand uses
        self.assertEqual(n, 3)
        left = [(e["appid"], e["AppName"]) for _c, e in core.steam_shortcuts([self.steam])]
        self.assertEqual(left, [(1, "Emu"), (3, "Emu"), (4, "My Game")])
        self.assertTrue((cfg / "shortcuts.vdf.before-dedupe").exists())
        self.assertEqual(core.find_duplicate_shortcuts([self.steam], launchers), [])
        self.assertEqual(core.remove_duplicate_shortcuts([self.steam], launchers), 0)

    def test_sync_adopts_the_id_steam_gave_the_shortcut(self):
        app = core.App("g", "Game", "/x.exe", "/p", "P", "proton", "/p", launcher="/l/game.sh", steam_appid=5,
                       steam_added="requested", steam_requested_at=1.0)
        entries = [(Path("/c"), {"appid": 9, "Exe": '"/l/game.sh"'})]
        self.assertTrue(core.sync_steam_appid(app, entries))
        self.assertEqual((app.steam_appid, app.steam_added, app.steam_requested_at), (9, "live", 0.0))
        self.assertFalse(core.sync_steam_appid(app, entries))
        self.assertFalse(core.sync_steam_appid(app, []))

    def test_game_mode_always_counts_as_steam_running(self):
        os.environ["XDG_CURRENT_DESKTOP"] = "gamescope"
        self.assertTrue(self._real_steam_is_running())

    def test_steam_is_running_sees_a_process_named_steam(self):
        import subprocess
        code = ("import ctypes, time\n"
                "ctypes.CDLL(None).prctl(15, b'steam', 0, 0, 0)\n"  # PR_SET_NAME
                "print(flush=True)\n"
                "time.sleep(30)\n")
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
        try:
            proc.stdout.readline()
            self.assertTrue(self._real_steam_is_running())
        finally:
            proc.kill()
            proc.wait()
            proc.stdout.close()


class TestRuntimes(Env):
    def test_prefers_ge(self):
        stock = self.steam / "steamapps/common/Proton 9.0"
        stock.mkdir(parents=True)
        (stock / "proton").write_text("")
        exp = self.steam / "steamapps/common/Proton - Experimental"
        exp.mkdir(parents=True)
        (exp / "proton").write_text("")
        old_ge = self.steam / "compatibilitytools.d/GE-Proton8-1"
        old_ge.mkdir(parents=True)
        (old_ge / "proton").write_text("")
        rts = core.find_runtimes([self.steam], extra_tool_dirs=[], include_system_wine=False)
        self.assertEqual([r.name for r in rts],
                         ["GE-Proton9-20", "GE-Proton8-1", "Proton - Experimental", "Proton 9.0"])
        self.assertTrue(rts[0].wineserver().endswith("files/bin/wineserver"))

    def test_none(self):
        empty = self.tmp / "empty"
        (empty / "steamapps").mkdir(parents=True)
        self.assertEqual(core.find_runtimes([empty], extra_tool_dirs=[], include_system_wine=False), [])


class TestInstallFlow(Env):
    def test_end_to_end_with_shortcut(self):
        lnk = self.tmp / "x.lnk"
        lnk.write_bytes(make_lnk(r"C:\Program Files\Cool Game\bin\CoolGame.exe"))
        os.environ["FAKE_LNK"] = str(lnk)
        statuses = []
        job = self.job(status=statuses.append)
        pending = job.run()
        self.assertEqual(pending.name, "Cool Game")
        self.assertTrue(core.is_confident(pending.candidates))
        top = pending.candidates[0]
        self.assertEqual(top.exe.name, "CoolGame.exe")
        names = [c.exe.name for c in pending.candidates]
        self.assertNotIn("notepad.exe", names)
        self.assertNotIn("setup-helper.exe", names)
        self.assertLess(names.index("CoolGame.exe"), names.index("unins000.exe"))

        app = job.finish(pending, top.exe, core.best_name(pending, top))
        self.assertEqual(app.name, "Cool Game Deluxe")
        self.assertTrue(any("installer" in s for s in statuses))
        script = Path(app.launcher).read_text()
        self.assertIn("STEAM_COMPAT_DATA_PATH=", script)
        self.assertIn("CoolGame.exe", script)
        self.assertTrue(os.access(app.launcher, os.X_OK))
        self.assertEqual([a.id for a in core.Library(self.paths).load()], ["cool-game"])
        sc = core.vdf_loads((self.steam / "userdata/12345/config/shortcuts.vdf").read_bytes())
        self.assertEqual(sc["shortcuts"]["0"]["AppName"], "Cool Game Deluxe")

        # Same installer again gets its own prefix
        self.assertEqual(core.Library(self.paths).unique_id("Cool Game"), "cool-game-2")

        core.uninstall(app, self.paths, roots=[self.steam])
        self.assertFalse(Path(app.prefix).exists())
        self.assertEqual(core.Library(self.paths).load(), [])
        sc = core.vdf_loads((self.steam / "userdata/12345/config/shortcuts.vdf").read_bytes())
        self.assertEqual(sc["shortcuts"], {})

    def test_without_shortcut_still_picks_program(self):
        job = self.job()
        pending = job.run()
        job.close()
        self.assertEqual(pending.candidates[0].exe.name, "CoolGame.exe")
        self.assertTrue(core.is_confident(pending.candidates))

    def test_installer_copy_is_never_the_program(self):
        # Installer drops a byte-identical copy of itself plus a setup tool, and nothing else.
        self.installer.write_bytes(b"MZ" + b"\x01" * 5_000_000)
        os.environ["FAKE_NOTHING"] = "1"
        job = self.job()
        pending = job.run()
        job.close()
        c = pending.pfx / "drive_c/Program Files/Cool Game"
        c.mkdir(parents=True, exist_ok=True)
        (c / "cache.exe").write_bytes(self.installer.read_bytes())  # renamed copy
        (c / "GameSetup.exe").write_bytes(b"MZ" + b"\x02" * 5_000_000)
        cands = core.find_program(pending.pfx, pending.name, pending.installer)
        self.assertNotIn("cache.exe", [x.exe.name for x in cands])
        self.assertFalse(core.is_confident(cands, pending.installer))

    def test_program_wins_over_installer_named_shortcut(self):
        lnk = self.tmp / "x.lnk"
        lnk.write_bytes(make_lnk(r"C:\Program Files\Cool Game\bin\CoolGame.exe"))
        os.environ["FAKE_LNK"] = str(lnk)
        job = self.job()
        pending = job.run()
        job.close()
        self.assertTrue(core.is_confident(pending.candidates, pending.installer))
        self.assertEqual(pending.candidates[0].exe.name, "CoolGame.exe")
        self.assertNotEqual(pending.candidates[0].exe.name, self.installer.name)

    def test_portable_program(self):
        os.environ["FAKE_NOTHING"] = "1"
        job = self.job()
        pending = job.run()
        self.assertFalse(core.is_confident(pending.candidates))
        exe = core.adopt_portable(pending)
        self.assertTrue(exe.is_file())
        app = job.finish(pending, exe, "Evil\nrm -rf ~")
        self.assertTrue(app.exe.endswith(self.installer.name))
        self.assertEqual(app.name, "Evil rm -rf ~")
        self.assertNotIn("\nrm", Path(app.launcher).read_text())

    def test_cancel_removes_prefix(self):
        os.environ["FAKE_SLEEP"] = "30"
        job = self.job()
        threading.Timer(0.5, job.cancel).start()
        t0 = time.time()
        with self.assertRaises(core.Cancelled):
            job.run()
        self.assertLess(time.time() - t0, 10)
        self.assertFalse((self.paths.prefixes / "cool-game").exists())

    def test_no_runtime(self):
        empty = self.tmp / "empty"
        (empty / "steamapps").mkdir(parents=True)
        os.environ["PATH"] = str(self.tmp)  # hide any system wine
        job = core.Installer(self.installer, self.paths, steam_roots_override=[empty])
        with self.assertRaises(core.InstallError) as ctx:
            job.run()
        self.assertEqual(str(ctx.exception), "NO_RUNTIME")

    def test_rejects_non_installer(self):
        bad = self.tmp / "notes.txt"
        bad.write_text("x")
        with self.assertRaises(core.InstallError):
            core.Installer(bad, self.paths, steam_roots_override=[self.steam]).run()

    def test_runs_inside_steam_linux_runtime_like_steam(self):
        job = self.job()
        pending = job.run()
        job.close()
        self.assertEqual(job.entry, self.entry)
        calls = self.entry_log.read_text().splitlines()
        # Windows setup, wait, the installer, wait — every step inside the container, and the
        # waits use Proton's own waitforexitandrun (the container's Wine may be invisible outside).
        self.assertEqual(len(calls), 4)
        for c in calls:
            self.assertTrue(c.startswith(f"--verb=waitforexitandrun -- {self.proton} waitforexitandrun "))
        self.assertIn("cmd.exe /c exit", calls[0])
        self.assertIn("cmd.exe /c exit", calls[1])
        self.assertTrue(calls[2].endswith(self.installer.name))
        self.assertIn("cmd.exe /c exit", calls[3])
        app = job.finish(pending, pending.candidates[0].exe)
        script = Path(app.launcher).read_text()
        self.assertIn(f"ENTRY={self.entry}", script)
        self.assertIn('"$ENTRY" --verb=waitforexitandrun -- "$PROTON" waitforexitandrun', script)

    def test_asks_when_container_missing(self):
        self.entry.unlink()
        with self.assertRaises(core.InstallError) as ctx:
            self.job().run()
        self.assertEqual(str(ctx.exception), "NO_CONTAINER:1628350")
        self.assertFalse(self.paths.prefixes.exists() and any(self.paths.prefixes.iterdir()))

    def test_falls_back_to_plain_proton_without_container(self):
        self.entry.unlink()
        job = self.job(allow_no_container=True)
        job.run()
        job.close()
        self.assertIsNone(job.entry)
        self.assertFalse(self.entry_log.exists())
        self.assertIn("Warning: the Steam Linux Runtime", (self.paths.logs / "cool-game.log").read_text())

    def test_proton_gets_an_app_id(self):
        # 'setup' has no digits: GE-Proton's protonfixes would crash without SteamAppId.
        inst = self.home / "Downloads" / "setup.exe"
        inst.write_bytes(b"MZ")
        for k in ("SteamAppId", "SteamGameId", "UMU_ID"):
            os.environ.pop(k, None)
        job = core.Installer(inst, self.paths, steam_roots_override=[self.steam])
        pending = job.run()
        self.assertEqual(pending.id, "setup")
        app = job.finish(pending, pending.candidates[0].exe)
        self.assertIn('SteamAppId="${SteamAppId:-0}"', Path(app.launcher).read_text())

    def test_broken_proton_is_reported_not_nothing_installed(self):
        os.environ["FAKE_BROKEN"] = "1"
        with self.assertRaises(core.InstallError) as ctx:
            self.job().run()
        msg = str(ctx.exception)
        self.assertIn("GE-Proton9-20 failed to start", msg)
        self.assertIn("proton exploded", msg)
        self.assertEqual(list(self.paths.prefixes.iterdir()), [])
        self.assertTrue((self.paths.logs / "cool-game.log").exists())

    def test_msi_command(self):
        rt = core.Runtime("P", "proton", "/p/proton")
        self.assertEqual(core.run_command(rt, "D:\\a.msi"), ["/p/proton", "run", "msiexec", "/i", "D:\\a.msi"])
        self.assertEqual(core.run_command(rt, "x.exe", entry=Path("/e/_v2-entry-point")),
                         ["/e/_v2-entry-point", "--verb=waitforexitandrun", "--", "/p/proton",
                          "waitforexitandrun", "x.exe"])

    def test_installer_sees_home_as_d_and_no_rootfs(self):
        inst = self.home / "Downloads" / self.installer.name
        inst.write_bytes(b"MZ")
        job = core.Installer(inst, self.paths, steam_roots_override=[self.steam])
        pending = job.run()
        job.close()
        drives = (pending.compat_dir / "drives-during-install.txt").read_text().split()
        self.assertIn("c:", drives)
        self.assertIn("d:", drives)
        # Proton recreates a missing Z: on every launch, so Z: must be *repointed* (at the home
        # folder, where the free space is) rather than removed — the fake does what Proton does.
        z_during = (pending.compat_dir / "z-during-install.txt").read_text().strip()
        self.assertEqual(z_during, str(self.home.resolve()))
        arg = (pending.compat_dir / "installer-arg.txt").read_text().strip()
        self.assertEqual(arg, "D:\\Downloads\\" + inst.name)
        dd = pending.pfx / "dosdevices"
        self.assertEqual(os.readlink(dd / "d:"), str(self.home.resolve()))
        self.assertEqual(os.readlink(dd / "z:"), "/")  # restored for running the program

    def test_installer_outside_home_gets_its_own_drive(self):
        job = self.job()
        pending = job.run()
        job.close()
        arg = (pending.compat_dir / "installer-arg.txt").read_text().strip()
        self.assertEqual(arg, "E:\\" + self.installer.name)

    def test_program_installed_to_d_is_found(self):
        os.environ["FAKE_TO_D"] = "1"
        job = self.job()
        pending = job.run()
        self.assertTrue(core.is_confident(pending.candidates, pending.installer))
        top = pending.candidates[0]
        self.assertEqual(top.exe.name, "CoolGame.exe")
        app = job.finish(pending, top.exe)
        self.assertEqual(app.exe, str(self.home.resolve() / "Games/Cool Game/CoolGame.exe"))

    def test_uninstall_program_installed_to_d(self):
        os.environ["FAKE_TO_D"] = "1"
        (self.home / "Games").mkdir()  # a standard folder the installer puts things into
        job = self.job()
        pending = job.run()
        app = job.finish(pending, pending.candidates[0].exe)
        game_dir = (self.home / "Games" / "Cool Game").resolve()
        self.assertEqual(app.extra_dirs, [str(game_dir)])
        self.assertTrue(core.in_steam(app, [self.steam]))
        self.assertGreater(core.app_size(app), 3_000_000)
        core.uninstall(app, self.paths, roots=[self.steam])
        self.assertFalse(game_dir.exists())
        self.assertTrue((self.home / "Games").exists())  # the standard folder itself is kept
        self.assertFalse(Path(app.prefix).exists())
        self.assertFalse(core.in_steam(app, [self.steam]))

    def test_orphan_prefixes_are_the_ones_no_program_uses(self):
        job = self.job()
        pending = job.run()
        app = job.finish(pending, pending.candidates[0].exe)
        left = self.paths.prefixes / "cool-game-2"
        (left / "pfx").mkdir(parents=True)
        self.assertEqual(core.orphan_prefixes(self.paths), [left])
        self.assertTrue(Path(app.prefix).exists())

    def test_an_interrupted_install_can_be_finished_later(self):
        job = self.job()
        pending = job.run()
        job.close()  # Deckhand closed before the program was picked
        z = pending.pfx / "dosdevices/z:"
        z.unlink()
        z.symlink_to(self.home)  # as if the install was cut off while Z: pointed at home
        self.assertEqual(core.orphan_prefixes(self.paths), [pending.compat_dir])
        self.assertFalse(core.prefix_in_use(pending.compat_dir))
        again = core.resume_install(self.paths, pending.compat_dir, [self.steam])
        self.assertEqual((again.id, again.name, again.installer), (pending.id, pending.name, pending.installer))
        self.assertEqual(again.runtime, pending.runtime)
        self.assertEqual(again.candidates[0].exe, pending.candidates[0].exe)
        self.assertEqual(os.readlink(z), "/")
        app = core.Installer(again.installer, self.paths, runtime=again.runtime,
                             steam_roots_override=[self.steam]).finish(again, again.candidates[0].exe)
        self.assertTrue(core.in_steam(app, [self.steam]))
        self.assertEqual(core.orphan_prefixes(self.paths), [])

    def test_resume_finds_nothing_in_an_empty_prefix(self):
        (self.paths.prefixes / "x/pfx/drive_c").mkdir(parents=True)
        self.assertIsNone(core.resume_install(self.paths, self.paths.prefixes / "x", [self.steam]))

    def test_prefix_in_use_sees_a_process_running_in_it(self):
        import subprocess
        compat = self.paths.prefixes / "busy"
        env = dict(os.environ, WINEPREFIX=str(compat / "pfx"))
        proc = subprocess.Popen(["sleep", "30"], env=env)
        try:
            self.assertTrue(core.prefix_in_use(compat))
            self.assertFalse(core.prefix_in_use(self.paths.prefixes / "other"))
        finally:
            proc.kill()
            proc.wait()

    def test_directx_log_in_two_places_is_shown_once(self):
        windows = self.tmp / "pfx/drive_c/windows"
        (windows / "Logs").mkdir(parents=True)
        for d in (windows, windows / "Logs"):
            (d / "DirectX.log").write_text("DXWSetup: WinMain()\n")
        text = core.directx_log(self.tmp / "pfx")
        self.assertEqual(text.count("DXWSetup: WinMain()"), 1)

    def test_directx_log_is_read_even_in_utf16(self):
        windows = self.tmp / "pfx/drive_c/windows"
        windows.mkdir(parents=True)
        self.assertEqual(core.directx_log(self.tmp / "pfx"), "")
        (windows / "DXError.log").write_bytes("line one\r\nDXSetup: internal error 42\r\n".encode("utf-16"))
        text = core.directx_log(self.tmp / "pfx")
        self.assertIn("DXError.log", text)
        self.assertIn("internal error 42", text)

    def test_program_dirs(self):
        h = self.home.resolve()
        exe = h / "Games/Cool Game/bin/x.exe"
        self.assertEqual(core.program_dirs(exe, [h / "Games"], h), [h / "Games/Cool Game"])
        self.assertEqual(core.program_dirs(exe, [h / "Games/Cool Game"], h), [h / "Games/Cool Game"])
        self.assertEqual(core.program_dirs(h / "CoolGame/x.exe", [h / "CoolGame", h / "Other"], h),
                         [h / "CoolGame"])
        self.assertEqual(core.program_dirs(h / "Games/x.exe", [h / "Games"], h), [])

    def test_program_folder_that_existed_before_the_install(self):
        h = self.home.resolve()
        game = h / "Games/Some Game"  # made by an earlier attempt: only its subfolders are new
        exe = game / "Game.exe"
        new = [game / "Engine", game / "Bonus"]
        self.assertEqual(core.program_dirs(exe, new, h), [game])
        # Never a shared folder, even if the installer put folders next to the program there.
        self.assertEqual(core.program_dirs(h / "Games/game.exe", [h / "Games/Engine"], h), [])
        self.assertEqual(core.program_dirs(h / "Downloads/game.exe", [h / "Downloads/Engine"], h), [])
        self.assertEqual(core.program_dirs(h / "game.exe", [h / "Engine"], h), [])
        # Nothing new next to the program: nothing to claim.
        self.assertEqual(core.program_dirs(exe, [h / "Other/New"], h), [])

    def test_program_recorded_without_its_folder_gets_it_back(self):
        h = self.home.resolve()
        game = h / "Games/Some Game"
        (game / "Engine").mkdir(parents=True)
        pfx = self.tmp / "pfx-x"
        app = core.App("x", "X", str(game / "Game.exe"), str(pfx), "P", "proton", "/p")
        self.assertEqual(core.guess_program_dir(app, h), game)
        app.exe = str(h / "Games/game.exe")  # right in a shared folder: nothing of its own
        self.assertIsNone(core.guess_program_dir(app, h))
        app.exe, app.extra_dirs = str(game / "Game.exe"), [str(game)]  # already recorded
        self.assertIsNone(core.guess_program_dir(app, h))
        app.extra_dirs, app.exe = [], str(pfx / "pfx/drive_c/Game/Game.exe")  # on C:: nothing outside
        self.assertIsNone(core.guess_program_dir(app, h))

    def test_uninstall_never_deletes_outside_home_or_standard_folders(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (self.home / "Downloads" / "keep.txt").write_text("x")
        app = core.App("x", "X", "/x.exe", str(self.tmp / "nope"), "P", "proton", "/p",
                       extra_dirs=[str(outside), str(self.home), str(self.home / "Downloads"), "/"])
        self.assertEqual(core.safe_extra_dirs(app), [])
        core.uninstall(app, self.paths, roots=[self.steam])
        self.assertTrue(outside.exists())
        self.assertTrue((self.home / "Downloads" / "keep.txt").exists())

    def test_in_steam_after_removal_in_steam(self):
        job = self.job()
        pending = job.run()
        app = job.finish(pending, pending.candidates[0].exe)
        self.assertTrue(core.in_steam(app, [self.steam]))
        core.remove_steam_shortcut(app.steam_appid, [self.steam])  # the user deleted it in Steam
        self.assertFalse(core.in_steam(app, [self.steam]))
        self.assertTrue(Path(app.prefix).exists())  # files are still there until uninstalled

    def _fake_steam(self, behaves: bool) -> Path:
        """A `steam` command that, like the real client, adds the .desktop file it's handed."""
        bindir = self.tmp / "fakebin"
        bindir.mkdir(exist_ok=True)
        script = bindir / "steam"
        repo = Path(__file__).resolve().parents[1]
        body = ("import sys, urllib.parse, re\n"
                f"sys.path.insert(0, {str(repo)!r})\n"
                "from pathlib import Path\n"
                "from deckhand import core\n"
                "url = sys.argv[1]\n"
                "assert url.startswith('steam://addnonsteamgame/')\n"
                "text = Path(urllib.parse.unquote(url.split('/', 3)[3])).read_text()\n"
                "name = re.search('^Name=(.*)$', text, re.M).group(1)\n"
                "exe = re.search('^Exec=\"(.*)\"$', text, re.M).group(1)\n"
                f"core.add_steam_shortcut(name, exe, '/', roots=[Path({str(self.steam)!r})])\n")
        script.write_text("#!/usr/bin/env python3\n" + (body if behaves else "pass\n"))
        script.chmod(0o755)
        os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
        return bindir

    def _installed_app(self):
        job = self.job()
        pending = job.run()
        app = job.finish(pending, pending.candidates[0].exe)  # Steam not running here: file edit
        core.remove_steam_shortcut(app.steam_appid, [self.steam])
        self.assertFalse(core.in_steam(app, [self.steam]))
        return app

    def test_add_to_running_steam_goes_through_steam(self):
        app = self._installed_app()
        self._fake_steam(behaves=True)
        appid, how = core.add_to_steam(app, [self.steam], running=True, wait=5)
        self.assertEqual(how, "live")
        self.assertEqual(appid, core.find_shortcut(app.launcher, [self.steam]))
        self.assertTrue(core.in_steam(app, [self.steam]))
        entry = core.desktop_entry_path(app).read_text()
        self.assertIn(f'Exec="{app.launcher}"', entry)
        self.assertIn("Categories=Game;", entry)

    def test_running_steam_is_asked_once_and_its_file_never_edited(self):
        app = self._installed_app()
        vdf = self.steam / "userdata/12345/config/shortcuts.vdf"
        os.utime(vdf, (time.time() - 60, time.time() - 60))
        before = vdf.read_bytes()
        self._fake_steam(behaves=False)  # Steam takes it but hasn't saved its list yet
        appid, how = core.add_to_steam(app, [self.steam], running=True, wait=1)
        self.assertEqual(how, "requested")
        self.assertEqual((app.steam_added, app.steam_appid), ("requested", appid))
        self.assertEqual(vdf.read_bytes(), before)  # editing it behind Steam's back makes duplicates
        self.assertEqual(core.steam_state(app, [self.steam], running=True), "sent")
        self.assertEqual(core.steam_state(app, [self.steam], running=False), "out")  # Steam quit without it
        os.utime(vdf, (time.time() + 5, time.time() + 5))  # Steam saved its list, and it isn't there
        self.assertEqual(core.steam_state(app, [self.steam], running=True), "out")

    def test_already_in_steam_is_never_added_again(self):
        job = self.job()
        pending = job.run()
        app = job.finish(pending, pending.candidates[0].exe)
        self._fake_steam(behaves=True)
        for running in (True, False):
            appid, how = core.add_to_steam(app, [self.steam], running=running, wait=2)
            self.assertEqual((how, appid), ("live", app.steam_appid))
        self.assertEqual(len(core.steam_shortcuts([self.steam])), 1)

    def test_running_steam_that_cant_be_reached_changes_nothing(self):
        app = self._installed_app()
        before = (self.steam / "userdata/12345/config/shortcuts.vdf").read_bytes()
        os.environ["PATH"] = str(self.tmp / "empty-bin")  # no steam, no xdg-open
        appid, how = core.add_to_steam(app, [self.steam], running=True, wait=1)
        self.assertEqual(how, "unavailable")
        self.assertEqual((self.steam / "userdata/12345/config/shortcuts.vdf").read_bytes(), before)

    def test_uninstall_while_steam_runs_leaves_steams_list_alone(self):
        job = self.job()
        pending = job.run()
        app = job.finish(pending, pending.candidates[0].exe)
        vdf = self.steam / "userdata/12345/config/shortcuts.vdf"
        before = vdf.read_bytes()
        self.assertTrue(core.uninstall(app, self.paths, roots=[self.steam], running=True))
        self.assertEqual(vdf.read_bytes(), before)
        self.assertFalse(Path(app.prefix).exists())
        self.assertEqual(core.Library(self.paths).load(), [])

    def test_steam_closed_edits_the_file(self):
        app = self._installed_app()
        appid, how = core.add_to_steam(app, [self.steam], running=False)
        self.assertEqual((how, appid), ("file", core.find_shortcut(app.launcher, [self.steam])))

    def test_finish_adds_menu_entry_and_uninstall_removes_it(self):
        job = self.job()
        pending = job.run()
        app = job.finish(pending, pending.candidates[0].exe)
        self.assertEqual(app.steam_added, "file")
        self.assertTrue(core.desktop_entry_path(app).exists())
        log = (self.paths.logs / f"{app.id}.log").read_text()
        self.assertIn("Steam was closed; its saved shortcut list went from 0 to 1 entries", log)
        core.uninstall(app, self.paths, roots=[self.steam])
        self.assertFalse(core.desktop_entry_path(app).exists())

    def test_shortcut_args_and_start_folder_reach_the_launcher(self):
        lnk = self.tmp / "x.lnk"
        lnk.write_bytes(make_lnk(r"C:\Program Files\Cool Game\bin\CoolGame.exe",
                                 args='-skipintro -profile "My Save"', workdir=r"C:\Program Files\Cool Game"))
        os.environ["FAKE_LNK"] = str(lnk)
        job = self.job()
        pending = job.run()
        top = pending.candidates[0]
        self.assertEqual(top.args, ["-skipintro", "-profile", "My Save"])
        app = job.finish(pending, top.exe)
        self.assertEqual(app.args, ["-skipintro", "-profile", "My Save"])
        self.assertTrue(app.workdir.endswith("Cool Game"))
        script = Path(app.launcher).read_text()
        self.assertIn("CoolGame.exe' -skipintro -profile 'My Save' \"$@\"", script)
        import shlex
        self.assertIn(f"cd {shlex.quote(app.workdir)} ", script)

    def test_launcher_restores_hidden_z(self):
        job = self.job()
        pending = job.run()
        app = job.finish(pending, pending.candidates[0].exe)
        z = pending.pfx / "dosdevices/z:"
        z.unlink()
        z.symlink_to(self.home)  # as if an install was interrupted while Z: pointed at home
        import subprocess
        subprocess.run(["bash", app.launcher], env=dict(os.environ), capture_output=True, timeout=30)
        self.assertEqual(os.readlink(z), "/")
        log = (self.paths.logs / f"{app.id}-launch.log").read_text()
        self.assertIn("Deckhand: starting", log)  # each launch leaves a log for troubleshooting
        self.assertIn("Proton: ", log)

    def test_launcher_explains_missing_proton(self):
        job = self.job()
        pending = job.run()
        app = job.finish(pending, pending.candidates[0].exe)
        app.runtime_path = str(self.tmp / "gone/proton")
        core.write_launcher(app, self.paths, self.steam, [self.steam])
        import subprocess
        env = dict(os.environ, HOME=str(self.tmp / "nohome"))
        r = subprocess.run(["bash", app.launcher], env=env, capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("No Proton found", (self.paths.logs / f"{app.id}-launch.log").read_text())

    def test_installs_keep_the_deck_awake_when_allowed(self):
        bindir = self.tmp / "inhibit"
        bindir.mkdir()
        log = self.tmp / "inhibit.log"
        fake = bindir / "systemd-inhibit"
        fake.write_text('#!/bin/bash\necho "$@" >> ' + str(log) + '\n'
                        'while [[ "$1" == --* ]]; do shift; done\nexec "$@"\n')
        fake.chmod(0o755)
        os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
        core._INHIBIT = None
        try:
            job = self.job()
            job.run()
            job.close()
        finally:
            core._INHIBIT = None
        lines = log.read_text().splitlines()
        self.assertTrue(any(self.installer.name in line and "--what=sleep:idle" in line for line in lines))

    def test_sleep_inhibitor_not_allowed_means_plain_run(self):
        bindir = self.tmp / "inhibit"
        bindir.mkdir()
        (bindir / "systemd-inhibit").write_text("#!/bin/bash\necho denied >&2; exit 1\n")
        (bindir / "systemd-inhibit").chmod(0o755)
        os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
        core._INHIBIT = None
        try:
            self.assertEqual(core.sleep_inhibitor(), [])
            job = self.job()
            pending = job.run()  # and the install still works
            job.close()
            self.assertTrue(pending.candidates)
        finally:
            core._INHIBIT = None

    def test_portable_program_folder_is_copied_whole(self):
        folder = self.home / "Downloads" / "Tool 2.0"
        (folder / "data").mkdir(parents=True)
        exe = folder / "Tool.exe"
        exe.write_bytes(b"MZ")
        (folder / "tool.dll").write_bytes(b"x")
        (folder / "data" / "a.pak").write_bytes(b"y")
        os.environ["FAKE_NOTHING"] = "1"
        job = core.Installer(exe, self.paths, steam_roots_override=[self.steam])
        pending = job.run()
        folder, size = core.portable_folder(pending)
        self.assertEqual((folder, size), (folder, 4))
        copied = core.adopt_portable(pending, whole_folder=True)
        self.assertEqual(copied.name, "Tool.exe")
        self.assertTrue((copied.parent / "tool.dll").exists())
        self.assertTrue((copied.parent / "data" / "a.pak").exists())
        job.close()

    def test_portable_from_downloads_takes_only_the_exe(self):
        exe = self.home / "Downloads" / "Tool.exe"
        exe.write_bytes(b"MZ")
        (self.home / "Downloads" / "unrelated.iso").write_bytes(b"x" * 100)
        os.environ["FAKE_NOTHING"] = "1"
        job = core.Installer(exe, self.paths, steam_roots_override=[self.steam])
        pending = job.run()
        job.close()
        self.assertIsNone(core.portable_folder(pending))  # Downloads is shared: never offered
        copied = core.adopt_portable(pending, whole_folder=True)
        self.assertEqual(sorted(p.name for p in copied.parent.iterdir()), ["Tool.exe"])

    def test_never_copies_a_folder_holding_deckhand_itself(self):
        os.environ["FAKE_NOTHING"] = "1"
        job = self.job()  # the installer sits in the folder that also holds Deckhand's data
        pending = job.run()
        self.assertIsNone(core.portable_folder(pending))
        copied = core.adopt_portable(pending, whole_folder=True)
        names = {p.name for p in copied.parent.iterdir()}
        self.assertIn(self.installer.name, names)
        self.assertFalse(names & {"data", "Steam", "home"})  # nothing from around the installer
        job.close()
        job.close()

    def test_library_writes_from_two_windows_dont_lose_entries(self):
        def app(i):
            return core.App(f"a{i}", f"A{i}", "/x", "/p", "P", "proton", "/r")
        threads = [threading.Thread(target=lambda i=i: core.Library(self.paths).upsert(app(i))) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(core.Library(self.paths).load()), 20)

    def test_prefers_proton_whose_runtime_is_installed(self):
        # A newer GE-Proton needing a runtime that isn't installed, next to one that is ready.
        newer = self.steam / "compatibilitytools.d/GE-Proton99-1"
        newer.mkdir(parents=True)
        (newer / "proton").write_text(self.proton.read_text())
        (newer / "proton").chmod(0o755)
        (newer / "toolmanifest.vdf").write_text('"manifest" { "require_tool_appid" "9999999" }')
        job = self.job()
        pending = job.run()
        job.close()
        self.assertEqual(pending.runtime.name, "GE-Proton9-20")

    def test_damaged_library_entry_is_skipped(self):
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.library_file.write_text('[{"id": "broken"}, {"id": "ok", "name": "OK", "exe": "/x", '
                                           '"prefix": "/p", "runtime_name": "P", "runtime_kind": "proton", '
                                           '"runtime_path": "/r"}]')
        self.assertEqual([a.id for a in core.Library(self.paths).load()], ["ok"])

    def test_cancel_restores_nothing_left_behind(self):
        os.environ["FAKE_SLEEP"] = "30"
        inst = self.home / "Downloads" / "x.exe"
        inst.write_bytes(b"MZ")
        job = core.Installer(inst, self.paths, steam_roots_override=[self.steam])
        threading.Timer(1.0, job.cancel).start()
        with self.assertRaises(core.Cancelled):
            job.run()
        self.assertEqual(list(self.paths.prefixes.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
