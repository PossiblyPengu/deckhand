"""Everything a new Deck needs, as a checklist like Arcadia's Setup: what's done, and the way to the rest."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout

from .. import start, theme
from ..widgets import Ring, label
from .common import page_header, PAGE_MARGINS, Page, glyph_icon, on_choose, set_pill


class StartPage(Page):
    """Everything a new Deck needs, as a checklist like Arcadia's Setup: what's done, and the way to the rest."""

    title = "Start"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        page_header(lay, "start", "Get started",
                    "What a new Steam Deck needs, top to bottom. Pick a step to do it.")
        self.add_banner(lay)
        lay.addSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(28)
        side = QVBoxLayout()
        side.setSpacing(10)
        self.ring = Ring()
        side.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)
        self.summary = label("", "muted")
        self.summary.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.summary.setFixedWidth(self.ring.width())
        side.addWidget(self.summary)
        side.addStretch(1)
        row.addLayout(side)
        self.list = QListWidget()
        self.list.setIconSize(QSize(44, 44))
        on_choose(self.list, self._activate)
        row.addWidget(self.list, 1)
        lay.addLayout(row, 1)
        self.steps: list[start.Step] = []

    def refresh(self) -> None:
        first = not self.steps
        self.steps = start.steps(self.win.paths, exe=start.self_exe())
        row = max(0, self.list.currentRow())
        self.list.clear()
        for st in self.steps:
            icon = glyph_icon("check", theme.GREEN) if st.done else glyph_icon(st.glyph, theme.ACCENT)
            it = QListWidgetItem(icon, f"{st.title}\n{st.detail}")
            it.setData(Qt.ItemDataRole.UserRole, st.id)
            set_pill(it, "Done" if st.done else "Optional" if st.optional else "To do", "done" if st.done else "todo")
            self.list.addItem(it)
        counted = [st for st in self.steps if not st.optional]
        left = start.to_go(self.steps)
        self.ring.set_progress(len(counted) - left, len(counted))
        self.summary.setText("Your Deck is set up. Play everything from your Steam library." if not left else
                             f"{left} step{'s' * (left != 1)} to go. Top to bottom works best.")
        if first:
            row = next((i for i, st in enumerate(self.steps) if not st.done and not st.optional), 0)
        self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _activate(self, item: QListWidgetItem) -> None:
        win = self.win
        {"proton": lambda: win.open_addon("ge-proton"), "steam": win.add_self_to_steam,
         "stores": win.show_stores, "stream": win.show_streaming, "decky": lambda: win.open_addon("decky"),
         "arcadia": lambda: win.open_addon("arcadia"), "program": win.go_home,
         }.get(item.data(Qt.ItemDataRole.UserRole), lambda: None)()

    def enter(self) -> None:
        self.refresh()
        self.list.setFocus()

    def hints(self):
        return self.with_paging([("A", "Select", self.win.nav_activate), ("B", "Back", self.back),
                                 ("☰", "Menu", self.win.open_menu)])

    def back(self) -> None:
        self.win.go_hub()
