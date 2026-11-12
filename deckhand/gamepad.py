"""Read game controllers straight from Linux evdev (no extra dependencies).

In Game Mode Steam hands a non-Steam app a virtual Xbox-style pad; joystick devices are readable
by the logged-in user (systemd's uaccess rule), so we can use A/B/X/Y, the D-pad and the stick.
This module only turns raw events into named actions; the Qt side decides what they do.
"""
from __future__ import annotations

import os
import re
import select
import struct
from pathlib import Path
from typing import Callable

EV_KEY, EV_ABS = 0x01, 0x03
_EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux

BUTTONS = {
    0x130: "a", 0x131: "b", 0x133: "x", 0x134: "y",  # BTN_SOUTH/EAST/NORTH(X)/WEST(Y) as xpad maps them
    0x136: "lb", 0x137: "rb", 0x13A: "select", 0x13B: "start",
    0x220: "up", 0x221: "down", 0x222: "left", 0x223: "right",  # BTN_DPAD_* on some pads
}
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x10, 0x11
DIRECTIONS = ("up", "down", "left", "right")
# How each action is written on screen, in the order the Controller screen lists them.
ACTION_LABELS = {"a": "A", "b": "B", "x": "X", "y": "Y", "up": "▲", "down": "▼", "left": "◀", "right": "▶",
                 "lb": "L1", "rb": "R1", "select": "⧉", "start": "☰"}


def joystick_devices(devices_text: str | None = None) -> list[tuple[str, list[str]]]:
    """(name, /dev/input/eventN paths) of every joystick, from /proc/bus/input/devices."""
    if devices_text is None:
        try:
            devices_text = Path("/proc/bus/input/devices").read_text(errors="replace")
        except OSError:
            return []
    out = []
    for block in devices_text.split("\n\n"):
        m = re.search(r"^H: Handlers=(.*)$", block, re.M)
        if not m:
            continue
        handlers = m.group(1).split()
        if any(h.startswith("js") for h in handlers):
            name = re.search(r'^N: Name="(.*)"$', block, re.M)
            out.append((name.group(1) if name else "Game controller",
                        [f"/dev/input/{h}" for h in handlers if h.startswith("event")]))
    return out


def joystick_event_nodes(devices_text: str | None = None) -> list[str]:
    """/dev/input/eventN paths of joysticks, from /proc/bus/input/devices."""
    return [path for _name, paths in joystick_devices(devices_text) for path in paths]


class PadState:
    """Turns raw evdev (type, code, value) into ('up', True) / ('a', False) style transitions."""

    STICK_ON, STICK_OFF = 16000, 9000  # hysteresis on a ±32767 axis

    def __init__(self) -> None:
        self.held: set[str] = set()

    def _set(self, name: str, on: bool, out: list[tuple[str, bool]]) -> None:
        if on and name not in self.held:
            self.held.add(name)
            out.append((name, True))
        elif not on and name in self.held:
            self.held.discard(name)
            out.append((name, False))

    def feed(self, etype: int, code: int, value: int) -> list[tuple[str, bool]]:
        out: list[tuple[str, bool]] = []
        if etype == EV_KEY and code in BUTTONS and value in (0, 1):
            self._set(BUTTONS[code], value == 1, out)
        elif etype == EV_ABS and code in (ABS_HAT0X, ABS_HAT0Y):
            neg, pos = ("left", "right") if code == ABS_HAT0X else ("up", "down")
            self._set("hat-" + neg, value < 0, out)
            self._set("hat-" + pos, value > 0, out)
        elif etype == EV_ABS and code in (ABS_X, ABS_Y):
            neg, pos = ("left", "right") if code == ABS_X else ("up", "down")
            for name, active in (("stick-" + neg, -value), ("stick-" + pos, value)):
                on = active > (self.STICK_OFF if name in self.held else self.STICK_ON)
                self._set(name, on, out)
        # Report hat/stick as plain directions.
        return [(n.split("-", 1)[-1], on) for n, on in out]


def read_loop(emit: Callable[[str, bool, str], None], stop: Callable[[], bool], rescan_every: float = 3.0) -> None:
    """Blocking loop: read every joystick, call emit(action, pressed, device). Returns when stop() is true."""
    fds: dict[int, tuple[str, PadState]] = {}
    last_scan = -1e9
    import time

    while not stop():
        now = time.monotonic()
        if now - last_scan > rescan_every:
            last_scan = now
            known = {path for path, _ in fds.values()}
            for path in joystick_event_nodes():
                if path in known:
                    continue
                try:
                    fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = (path, PadState())
                except OSError:
                    continue
        if not fds:
            time.sleep(0.5)
            continue
        try:
            ready, _, _ = select.select(list(fds), [], [], 0.25)
        except (OSError, ValueError):
            ready = []
        for fd in ready:
            try:
                data = os.read(fd, _EVENT.size * 64)
            except BlockingIOError:
                continue
            except OSError:  # unplugged
                os.close(fd)
                fds.pop(fd, None)
                continue
            path, state = fds[fd]
            for i in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
                _s, _us, etype, code, value = _EVENT.unpack_from(data, i)
                for action, pressed in state.feed(etype, code, value):
                    emit(action, pressed, path)
    for fd in fds:
        os.close(fd)
