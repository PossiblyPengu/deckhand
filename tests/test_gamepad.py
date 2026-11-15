"""Controller input from Linux evdev: finding the pads, turning raw events into actions, and the read loop (fed real
input_event records through a pipe standing in for /dev/input/eventN)."""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import gamepad  # noqa: E402

DEVICES = '''I: Bus=0003 Vendor=28de Product=1205 Version=0111
N: Name="Steam Deck"
H: Handlers=event5 js0

I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
H: Handlers=kbd event0

I: Bus=0003 Vendor=28de Product=11ff Version=0001
N: Name="Microsoft X-Box 360 pad 0"
H: Handlers=event18 js1
'''


def event(etype, code, value):
    return gamepad._EVENT.pack(0, 0, etype, code, value)


class TestDevices(unittest.TestCase):
    def test_only_joysticks_are_read_with_their_names(self):
        self.assertEqual(gamepad.joystick_devices(DEVICES),
                         [("Steam Deck", ["/dev/input/event5"]), ("Microsoft X-Box 360 pad 0", ["/dev/input/event18"])])
        self.assertEqual(gamepad.joystick_event_nodes(DEVICES), ["/dev/input/event5", "/dev/input/event18"])

    def test_no_devices_file_means_no_pads(self):
        self.assertEqual(gamepad.joystick_devices(""), [])


class TestPadState(unittest.TestCase):
    def setUp(self):
        self.pad = gamepad.PadState()

    def test_a_button_press_and_release(self):
        self.assertEqual(self.pad.feed(gamepad.EV_KEY, 0x130, 1), [("a", True)])
        self.assertEqual(self.pad.feed(gamepad.EV_KEY, 0x130, 1), [])  # (held: no second press)
        self.assertEqual(self.pad.feed(gamepad.EV_KEY, 0x130, 0), [("a", False)])
        self.assertEqual(self.pad.feed(gamepad.EV_KEY, 0x130, 2), [])  # (autorepeat isn't a press)

    def test_the_d_pad_hat_is_directions(self):
        self.assertEqual(self.pad.feed(gamepad.EV_ABS, gamepad.ABS_HAT0X, 1), [("right", True)])
        self.assertEqual(self.pad.feed(gamepad.EV_ABS, gamepad.ABS_HAT0X, -1), [("left", True), ("right", False)])
        self.assertEqual(self.pad.feed(gamepad.EV_ABS, gamepad.ABS_HAT0X, 0), [("left", False)])
        self.assertEqual(self.pad.feed(gamepad.EV_ABS, gamepad.ABS_HAT0Y, -1), [("up", True)])

    def test_the_stick_needs_a_real_push_and_lets_go_late(self):
        self.assertEqual(self.pad.feed(gamepad.EV_ABS, gamepad.ABS_Y, 12000), [])  # (not far enough to count)
        self.assertEqual(self.pad.feed(gamepad.EV_ABS, gamepad.ABS_Y, 20000), [("down", True)])
        self.assertEqual(self.pad.feed(gamepad.EV_ABS, gamepad.ABS_Y, 12000), [])  # (held until well back)
        self.assertEqual(self.pad.feed(gamepad.EV_ABS, gamepad.ABS_Y, 3000), [("down", False)])

    def test_other_events_are_ignored(self):
        self.assertEqual(self.pad.feed(0x00, 0, 0), [])  # (a sync report)
        self.assertEqual(self.pad.feed(gamepad.EV_KEY, 0x2FF, 1), [])  # (a button Deckhand doesn't use)


class TestReadLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fifo = self.tmp / "event5"
        os.mkfifo(self.fifo)
        self._orig = gamepad.joystick_event_nodes
        gamepad.joystick_event_nodes = lambda devices_text=None: [str(self.fifo)]

    def tearDown(self):
        gamepad.joystick_event_nodes = self._orig

    def test_events_from_a_pad_come_out_as_actions(self):
        got, stop = [], threading.Event()

        def emit(action, pressed, device):
            got.append((action, pressed, device))
            if len(got) >= 3:
                stop.set()

        loop = threading.Thread(target=gamepad.read_loop, args=(emit, stop.is_set), daemon=True)
        loop.start()
        time.sleep(0.3)  # (the loop opens the pad first)
        with open(self.fifo, "wb") as pad:
            pad.write(event(gamepad.EV_KEY, 0x130, 1) + event(gamepad.EV_KEY, 0x130, 0)
                      + event(gamepad.EV_ABS, gamepad.ABS_HAT0Y, 1))
        loop.join(5)
        self.assertFalse(loop.is_alive(), "the loop didn't stop when asked")
        self.assertEqual(got, [("a", True, str(self.fifo)), ("a", False, str(self.fifo)),
                               ("down", True, str(self.fifo))])

    def test_with_no_pad_it_waits_and_still_stops(self):
        gamepad.joystick_event_nodes = lambda devices_text=None: []
        stop = threading.Event()
        loop = threading.Thread(target=gamepad.read_loop, args=(lambda *a: None, stop.is_set), daemon=True)
        loop.start()
        stop.set()
        loop.join(3)
        self.assertFalse(loop.is_alive())


if __name__ == "__main__":
    unittest.main()
