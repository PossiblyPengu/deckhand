"""A keyboard you drive with the controller.

In Game Mode there is no keyboard, and the one Steam offers (STEAM + X) is a chord most people never find. So
every place that asks for text (an admin password, an API key, a program's name) shows this under the text box:
move over the keys with the D-pad and press A, or use the shortcuts on the other buttons:

    A  types the key     B  deletes     X  space     Y  shift     ☰  done

It types into the text box it is given, so the box keeps working with a real keyboard too.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .widgets import button, label

ROWS = ("1234567890", "qwertyuiop", "asdfghjkl'", "zxcvbnm.-_", "@!?#$%&*+/")
HINT = "A types   ·   B deletes   ·   X space   ·   Y shift   ·   ☰ done"


class OnScreenKeyboard(QWidget):
    done = pyqtSignal()  # "Done" was chosen (the owner decides what that means: usually the dialog's main button)

    def __init__(self, field: QLineEdit, parent: QWidget | None = None):
        super().__init__(parent)
        self.field = field
        self.shift = False
        self.keys: list[QPushButton] = []  # in reading order: the first one is where the cursor starts
        self._letters: list[tuple[QPushButton, str]] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        grid = QGridLayout()
        grid.setSpacing(6)
        for r, row in enumerate(ROWS):
            for c, ch in enumerate(row):
                b = self._key(ch, lambda ch=ch: self.type(ch))
                if ch.isalpha():
                    self._letters.append((b, ch))
                grid.addWidget(b, r, c)
        last = len(ROWS)
        self.shift_key = self._key("⇧", self.toggle_shift)
        self.shift_key.setCheckable(True)
        grid.addWidget(self.shift_key, last, 0, 1, 2)
        grid.addWidget(self._key("space", self.space), last, 2, 1, 4)
        grid.addWidget(self._key("⌫", self.backspace), last, 6, 1, 2)
        grid.addWidget(self._key("Done", self.done.emit), last, 8, 1, 2)
        for c in range(10):
            grid.setColumnStretch(c, 1)
        outer.addLayout(grid)
        outer.addWidget(label(HINT, "muted", wrap=False), 0, Qt.AlignmentFlag.AlignHCenter)

    def _key(self, text: str, slot) -> QPushButton:
        b = button(text.replace("&", "&&"), "key", slot)  # (a lone & in a Qt button is a shortcut marker, and vanishes)
        b.setAccessibleName(text)
        self.keys.append(b)
        return b

    # ── typing ───────────────────────────────────────────────────────────

    def type(self, ch: str) -> None:
        self.field.insert(ch.upper() if self.shift and ch.isalpha() else ch)
        if self.shift:  # shift is for one letter, like a phone's
            self.set_shift(False)

    def space(self) -> None:
        self.field.insert(" ")

    def backspace(self) -> None:
        self.field.backspace()

    def toggle_shift(self) -> None:
        self.set_shift(not self.shift)

    def set_shift(self, on: bool) -> None:
        self.shift = on
        self.shift_key.setChecked(on)
        for b, ch in self._letters:
            b.setText(ch.upper() if on else ch)

    def handle(self, action: str) -> bool:
        """The controller's buttons while this keyboard is up. True if it used the button."""
        if action == "b":
            self.backspace()
        elif action == "x":
            self.space()
        elif action == "y":
            self.toggle_shift()
        elif action in ("start", "select"):
            self.done.emit()
        else:
            return False
        return True
