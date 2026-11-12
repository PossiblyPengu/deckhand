"""A controller-friendly file browser (the desktop file dialog is awkward with a gamepad)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout

from .. import core
from ..widgets import ElideLabel, button, label
from .common import PAGE_MARGINS, Page, glyph_icon, on_choose


class BrowserPage(Page):
    """A controller-friendly file browser (the desktop file dialog is awkward with a gamepad)."""

    title = "Browse"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        self.heading = label("Choose an installer", "h1")
        lay.addWidget(self.heading)
        self.where = ElideLabel(obj="muted")
        lay.addWidget(self.where)
        self.places = QHBoxLayout()
        self.places.setSpacing(10)
        lay.addLayout(self.places)
        self.list = QListWidget()
        self.list.setIconSize(self.list.iconSize() * 1.6)
        on_choose(self.list, self._activate)
        lay.addWidget(self.list, 1)
        self.cwd = Path.home()
        self.roots: list[Path] = []
        self.suffixes: tuple[str, ...] = core.INSTALLER_SUFFIXES
        self.on_pick: Callable[[Path], None] = lambda p: None
        self.on_back: Callable[[], None] = win.go_home
        self.icon_dir, self.icon_file, self.icon_up = glyph_icon("folder"), glyph_icon("download"), glyph_icon("up")

    def open(self, heading: str, places: list[tuple[str, Path]], suffixes: tuple[str, ...],
             on_pick: Callable[[Path], None], on_back: Callable[[], None], start: Path | None = None) -> None:
        self.heading.setText(heading)
        self.suffixes, self.on_pick, self.on_back = suffixes, on_pick, on_back
        places = [(n, p) for n, p in places if p.is_dir()]
        self.roots = [p for _n, p in places]
        while self.places.count():
            it = self.places.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for name, path in places:
            self.places.addWidget(button(name, slot=lambda p=path: self.show_dir(p)))
        self.places.addStretch(1)
        self.show_dir(start if start and start.is_dir() else (self.roots[0] if self.roots else Path.home()))

    def show_dir(self, path: Path) -> None:
        self.cwd = path
        self.where.setText(str(path))
        self.list.clear()
        if path not in self.roots and path.parent != path:
            it = QListWidgetItem(self.icon_up, "Up one folder")
            it.setData(Qt.ItemDataRole.UserRole, str(path.parent))
            self.list.addItem(it)
        try:
            entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            entries = []
        dirs = [e for e in entries if e.is_dir() and not e.name.startswith(".")]
        files = [e for e in entries if e.is_file() and e.name.lower().endswith(self.suffixes)]
        for d in dirs:
            it = QListWidgetItem(self.icon_dir, d.name)
            it.setData(Qt.ItemDataRole.UserRole, str(d))
            self.list.addItem(it)
        bins = [e for e in entries if e.name.lower().endswith(".bin")]
        for f in files:
            size = core.human_size(core.files_size(core.installer_files(f, bins)))
            it = QListWidgetItem(self.icon_file, f"{f.name}     {size}")
            it.setData(Qt.ItemDataRole.UserRole, str(f))
            self.list.addItem(it)
        if not dirs and not files:
            it = QListWidgetItem("Nothing to pick in this folder")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(it)
        # Start on the first real entry rather than "Up one folder".
        has_up = path not in self.roots and path.parent != path
        self.list.setCurrentRow(1 if has_up and (dirs or files) else 0)
        self.list.setFocus()

    def _activate(self, item: QListWidgetItem) -> None:
        target = item.data(Qt.ItemDataRole.UserRole)
        if not target:
            return
        p = Path(target)
        if p.is_dir():
            self.show_dir(p)
        else:
            self.on_pick(p)

    def enter(self) -> None:
        self.list.setFocus()

    def hints(self):
        return [("A", "Open", self.win.nav_activate), ("B", "Back", self.back)]

    def back(self) -> None:
        if self.cwd not in self.roots and self.cwd.parent != self.cwd:
            self.show_dir(self.cwd.parent)
        else:
            self.on_back()
