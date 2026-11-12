"""The PickPage."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from .. import artwork, core
from ..widgets import Sheet, button, label
from .common import PAGE_MARGINS, Page, badge_icon


class PickPage(Page):
    title = "Choose the program"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(14)
        self.heading = label("Which one is the program?", "h1")
        lay.addWidget(self.heading)
        self.hint = label("", "dim")
        lay.addWidget(self.hint)
        self.list = QListWidget()
        self.list.setIconSize(self.list.iconSize() * 2)
        self.list.itemActivated.connect(lambda _i: self.use())
        lay.addWidget(self.list, 10)
        row = QHBoxLayout()
        row.addWidget(label("Name in Steam", wrap=False))
        self.name = QLineEdit()
        row.addWidget(self.name, 1)
        lay.addLayout(row)
        lay.addStretch(1)
        btns = QHBoxLayout()
        self.portable = button("No install needed", slot=lambda: self.win.use_portable())
        self.portable.setToolTip("The file you picked is the program itself: add it as it is")
        btns.addWidget(self.portable)
        btns.addWidget(button("Browse…", slot=lambda: self.win.browse_program_for_pending()))
        btns.addStretch(1)
        btns.addWidget(button("Cancel", "danger", self.back))
        self.use_btn = button("Add to Steam", "primary", self.use)
        btns.addWidget(self.use_btn)
        lay.addLayout(btns)

    def load(self, pending: core.PendingInstall) -> None:
        cands = pending.candidates
        self.list.clear()
        drive_c = pending.pfx / "drive_c"
        for c in cands[:12]:
            try:
                where = str(c.exe.parent.relative_to(drive_c))
            except ValueError:
                where = str(c.exe.parent)
            icon = artwork.load_exe_icon(c.exe)
            it = QListWidgetItem(f"{c.exe.name}\n{where}")
            it.setIcon(QIcon(QPixmap.fromImage(icon)) if icon is not None
                       else badge_icon(c.exe.stem, artwork.accent_color(c.exe.stem).name()))
            it.setData(Qt.ItemDataRole.UserRole, str(c.exe))
            self.list.addItem(it)
        has = bool(cands)
        self.heading.setText("Which one is the program?" if has else "Nothing was installed")
        self.hint.setText("The installer finished. Pick the program to add to Steam." if has else
                          "No new program files were found. If this file is the program itself (no setup "
                          "needed), add it as it is. Otherwise the installer may have been closed early.")
        self.list.setVisible(has)
        self.use_btn.setVisible(has)
        self.portable.setVisible(pending.installer.suffix.lower() == ".exe")
        self.portable.setObjectName("" if has else "primary")
        self.portable.style().polish(self.portable)
        self.name.setText(pending.name)
        if has:
            self.list.setCurrentRow(0)

    def enter(self) -> None:
        (self.list if self.list.isVisible() else self.portable).setFocus()

    def use(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.win.finish_install(Path(item.data(Qt.ItemDataRole.UserRole)), self.name.text())

    def back(self) -> None:
        if Sheet.ask(self, "Throw this install away?", "Everything the installer put on the Deck is deleted. "
                     "To keep it, pick the program instead — or use Browse… to find it.",
                     ("Delete it", "Keep"), primary=1, danger=(0,)) == 0:
            self.win.discard_pending()
