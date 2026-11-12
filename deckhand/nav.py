"""Controller and keyboard navigation.

D-pad / stick / arrow keys move focus to the nearest control in that direction (like Big
Picture), A / Enter activates, B / Esc goes back, and X / Y / ☰ run page actions. Gamepad input
comes from gamepad.py on a background thread; nothing happens while another window (e.g. a
running installer) has focus.
"""
from __future__ import annotations

import os
import time
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QScrollArea,
    QWidget,
)

from . import gamepad

_KEY_DIRS = {Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right"}
_NAV_TYPES = (QAbstractButton, QLineEdit, QAbstractItemView, QPlainTextEdit)


def _grect(w: QWidget) -> QRect:
    return QRect(w.mapToGlobal(QPoint(0, 0)), w.size())


def focusable(root: QWidget) -> list[QWidget]:
    out = []
    for w in root.findChildren(QWidget):
        if (isinstance(w, _NAV_TYPES) and w.isVisible() and w.isEnabled() and w.window() is root
                and w.focusPolicy() & Qt.FocusPolicy.TabFocus):
            out.append(w)
    return out


def nearest(cur: QRect, cands: list[tuple[QWidget, QRect]], d: str) -> QWidget | None:
    """Best candidate in direction d: close along d, well aligned across it."""
    best, best_score = None, None
    cx, cy = cur.center().x(), cur.center().y()
    for w, r in cands:
        wx, wy = r.center().x(), r.center().y()
        if d == "right":
            ok, primary = wx > cx + 4 and r.left() > cur.left(), max(0, r.left() - cur.right())
            off = max(0, max(r.top(), cur.top()) - min(r.bottom(), cur.bottom()))
        elif d == "left":
            ok, primary = wx < cx - 4 and r.right() < cur.right(), max(0, cur.left() - r.right())
            off = max(0, max(r.top(), cur.top()) - min(r.bottom(), cur.bottom()))
        elif d == "down":
            ok, primary = wy > cy + 4 and r.top() > cur.top(), max(0, r.top() - cur.bottom())
            off = max(0, max(r.left(), cur.left()) - min(r.right(), cur.right()))
        else:
            ok, primary = wy < cy - 4 and r.bottom() < cur.bottom(), max(0, cur.top() - r.bottom())
            off = max(0, max(r.left(), cur.left()) - min(r.right(), cur.right()))
        if not ok:
            continue
        # Prefer things that line up; among those, the closest; tie-break on centre distance.
        centre = abs(wy - cy) if d in ("left", "right") else abs(wx - cx)
        score = (off > 0, primary + off * 2, centre)
        if best_score is None or score < best_score:
            best, best_score = w, score
    return best


class GamepadThread(QThread):
    action = pyqtSignal(str, bool, str)  # action, pressed, device

    def run(self) -> None:
        gamepad.read_loop(lambda a, p, dev="": self.action.emit(a, p, dev), self.isInterruptionRequested)


class Nav(QObject):
    """Installed on the application. `handler(action)` gets 'b', 'x', 'y', 'start', 'select', 'lb', 'rb'."""

    REPEAT_DELAY, REPEAT_EVERY = 380, 110

    def __init__(self, app: QApplication, handler: Callable[[str], None], busy: Callable[[], bool] = lambda: False):
        super().__init__(app)
        self.app, self.handler, self.busy = app, handler, busy
        self._last: dict[str, tuple[float, str]] = {}
        self._held: str | None = None
        self._repeat = QTimer(self)
        self._repeat.timeout.connect(self._on_repeat)
        app.installEventFilter(self)
        app.focusChanged.connect(self._scroll_into_view)
        app.focusChanged.connect(self._remember_page_focus)
        self.page_focus: QWidget | None = None  # what had focus before the section menu took it
        self.pad: GamepadThread | None = None
        # If set, every controller event goes here instead of moving or acting (the Controller screen uses this).
        self.capture: Callable[[str, bool, str], None] | None = None
        # A on a text box: this opens the on-screen keyboard (set by the window); without it, A means Enter.
        self.text_editor: Callable[[QLineEdit], None] | None = None
        if not os.environ.get("DECKHAND_NO_GAMEPAD"):
            self.pad = GamepadThread()
            self.pad.action.connect(self.on_pad)
            self.pad.start()

    def stop(self) -> None:
        if self.pad:
            self.pad.requestInterruption()
            self.pad.wait(1500)

    def _fresh(self, action: str, source: str) -> bool:
        """False for the echo when Steam's keyboard emulation and the gamepad report the same press."""
        now = time.monotonic()
        when, src = self._last.get(action, (0.0, source))
        self._last[action] = (now, source)
        return not (src != source and now - when < 0.06)

    def _ours(self) -> bool:
        """Should the gamepad drive our UI? Not while an installer window has focus.

        Only enforced during installs: some compositors (gamescope) may not report window focus the
        way Qt expects, and the controller must still work on our own screens.
        """
        return self.app.activeWindow() is not None or not self.busy()

    def on_pad(self, action: str, pressed: bool, device: str = "") -> None:
        # Each device is its own source: if the Deck reports both Steam's virtual pad and its built-in
        # controller, one physical press arrives twice and the second copy is dropped.
        source = f"pad:{device}"
        if not self._ours():
            return
        if self.capture is not None:
            if self._fresh(action, source):
                self.capture(action, pressed, device)
            return
        if action in gamepad.DIRECTIONS:
            if pressed:
                fresh = self._fresh(action, source)
                if fresh:
                    self._held = action
                    self.move(action)
                    self._repeat.start(self.REPEAT_DELAY)
            elif self._held == action:
                self._held = None
                self._repeat.stop()
            return
        if not pressed or not self._fresh(action, source):
            return
        if action == "a":
            self.activate()
        else:
            self.handler(action)

    def _on_repeat(self) -> None:
        if self._held and self._ours():
            self.move(self._held)
            self._repeat.start(self.REPEAT_EVERY)
        else:
            self._repeat.stop()

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        if ev.type() != QEvent.Type.KeyPress or not isinstance(obj, QWidget) or obj is not self.app.focusWidget():
            return False
        key = ev.key()
        if key in _KEY_DIRS:
            d = _KEY_DIRS[key]
            if self._widget_wants(obj, d):
                return False
            if ev.isAutoRepeat() or self._fresh(d, "key"):
                self.move(d)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not isinstance(obj, (QLineEdit, QPlainTextEdit)):
            if self._fresh("a", "key"):
                self.activate()
            return True
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Back) and obj.window().objectName() == "main":
            if self._fresh("b", "key"):
                self.handler("b")
            return True
        return False

    def _main(self) -> QWidget | None:
        for w in self.app.topLevelWidgets():
            if w.objectName() == "main" and w.isVisible():
                return w
        return None

    @staticmethod
    def _widget_wants(w: QWidget, d: str) -> bool:
        """Let text fields and lists use arrows themselves until they hit an edge."""
        if isinstance(w, QLineEdit):
            return d in ("left", "right")
        if isinstance(w, QListWidget):
            row, n = w.currentRow(), w.count()
            return (d == "up" and row > 0) or (d == "down" and row < n - 1)
        if isinstance(w, QPlainTextEdit):
            sb = w.verticalScrollBar()
            return (d == "up" and sb.value() > sb.minimum()) or (d == "down" and sb.value() < sb.maximum())
        return False

    def root(self) -> QWidget | None:
        return self.app.activeWindow() or (self.app.focusWidget().window() if self.app.focusWidget() else None) \
            or self._main()

    def move(self, d: str) -> None:
        root = self.root()
        if root is None:
            return
        cur = self.app.focusWidget()
        if isinstance(cur, QListWidget) and self._widget_wants(cur, d):
            cur.setCurrentRow(cur.currentRow() + (1 if d == "down" else -1))
            return
        if d == "right" and cur is not None and cur.objectName() == "station":
            back = self.page_focus  # (the control you left, if it is still there)
            try:
                if back is not None and back.isVisible() and back.isEnabled() and back.window() is root:
                    back.setFocus(Qt.FocusReason.OtherFocusReason)
                    return
            except RuntimeError:  # it was deleted meanwhile
                self.page_focus = None
        cands = [w for w in focusable(root) if w is not cur]
        if not cands:
            return
        if cur is None or not cur.isVisible() or cur.window() is not root:
            cands[0].setFocus(Qt.FocusReason.OtherFocusReason)
            return
        target = nearest(_grect(cur), [(w, _grect(w)) for w in cands], d)
        if target is not None:
            target.setFocus(Qt.FocusReason.TabFocusReason if d in ("down", "right")
                            else Qt.FocusReason.BacktabFocusReason)

    def activate(self) -> None:
        w = self.app.focusWidget()
        if isinstance(w, QAbstractButton):
            w.animateClick()
        elif isinstance(w, QListWidget) and w.currentItem() is not None:
            w.itemActivated.emit(w.currentItem())
        elif isinstance(w, QLineEdit):
            if self.text_editor is not None:
                self.text_editor(w)
            else:
                w.returnPressed.emit()

    def _remember_page_focus(self, old: QWidget | None, new: QWidget | None) -> None:
        """Going left into the section menu remembers where you were, so going right returns you there."""
        if old is not None and new is not None and new.objectName() == "station" and old.objectName() != "station":
            self.page_focus = old

    @staticmethod
    def _scroll_into_view(_old: QWidget | None, new: QWidget | None) -> None:
        w = new
        while w is not None:
            parent = w.parentWidget()
            if isinstance(parent, QWidget) and isinstance(parent.parentWidget(), QScrollArea):
                parent.parentWidget().ensureWidgetVisible(new, 40, 60)
                return
            w = parent
