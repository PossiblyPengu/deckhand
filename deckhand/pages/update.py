"""The UpdatePage."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout

from .. import core, updater
from ..widgets import button, label
from .common import PAGE_MARGINS, Page


class UpdatePage(Page):
    title = "Update"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(14)
        self.heading = label("", "h1")
        lay.addWidget(self.heading)
        self.notes = label("", "dim")
        lay.addWidget(self.notes)
        lay.addStretch(1)
        self.status = label("", "status")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = button("Cancel", "danger", self.back)
        row.addWidget(self.cancel_btn)
        self.restart_btn = button("Restart Deckhand", "primary", lambda: self.win.restart_after_update())
        self.restart_btn.setMinimumWidth(260)
        row.addWidget(self.restart_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.finished = False

    def reset(self, update: updater.Update) -> None:
        self.finished = False
        self.heading.setText(f"Updating to Deckhand {update.version}")
        self.notes.setText(update.notes)
        self.notes.setVisible(bool(update.notes))
        self.status.setText("Downloading…")
        self.bar.setRange(0, 0)
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)
        self.restart_btn.hide()

    def on_progress(self, done: int, total: int) -> None:
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done * 1000 / total))
            self.status.setText(f"Downloading…  {core.human_size(done)} of {core.human_size(total)}")
        else:
            self.status.setText(f"Downloading…  {core.human_size(done)}")

    def on_done(self) -> None:
        self.finished = True
        self.heading.setText(self.heading.text().replace("Updating to", "Updated to"))
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.status.setText("✓  Update installed. Restart to use the new version.")
        self.cancel_btn.hide()
        self.restart_btn.show()
        self.restart_btn.setFocus()
        self.win.update_hints()

    def enter(self) -> None:
        (self.restart_btn if self.finished else self.cancel_btn).setFocus()

    def hints(self):
        return [("A", "Select", self.win.nav_activate), ("B", "Later" if self.finished else "Cancel", self.back)]

    def back(self) -> None:
        if self.finished:
            self.win.go_home()
        else:
            self.win.cancel_update()
