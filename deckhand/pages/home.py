"""Pick what to install: installers found on the Deck, or browse for one."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from .. import artwork, core
from ..widgets import Tile, button, label
from .common import page_header, COLUMNS, MAX_FOUND, PAGE_MARGINS, Page, ago


class HomePage(Page):
    """Pick what to install: installers found on the Deck, or browse for one."""

    def __init__(self, win):
        super().__init__(win)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        page_header(lay, "install", "Install a Windows program",
                    "Pick a setup file: Deckhand installs it and adds it to your Steam library.")
        self.add_banner(lay)
        lay.addSpacing(10)
        self.section = label("", "section")
        lay.addWidget(self.section)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(16)
        lay.addLayout(self.grid)
        self.note = label("", "muted")
        lay.addWidget(self.note)
        lay.addSpacing(8)
        manage = QHBoxLayout()
        self.manage_btn = button("", slot=lambda: self.win.show_installed())
        manage.addWidget(self.manage_btn)
        manage.addStretch(1)
        lay.addLayout(manage)
        lay.addStretch(1)
        self.tiles: list[Tile] = []
        self.icons: dict[tuple[str, float], QImage | None] = {}

    def refresh(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:  # hide + delete in place; setParent(None) would make it a stray top-level window
                w.hide()
                w.deleteLater()
        installed = {a.installer for a in self.win.library.load() if a.installer}
        found = core.find_installers(self.win.installer_dirs)
        shown = found[:MAX_FOUND]

        browse = Tile("Browse files", "Any folder or drive", glyph="folder")
        browse.clicked.connect(lambda: self.win.browse_installers())
        self.tiles = [browse]
        for f in shown:
            when = "Installed" if str(f.path) in installed else ago(f.mtime)
            icon = self._icon(f)
            t = Tile(core.guess_name(f.path), f"{core.human_size(f.size)} · {when}", icon=icon,
                     glyph="" if icon is not None else "download")
            t.setToolTip(str(f.path))
            t.clicked.connect(lambda _c=False, f=f: self.win.confirm_install(f.path))
            self.tiles.append(t)
        for i, t in enumerate(self.tiles):
            self.grid.addWidget(t, i // COLUMNS, i % COLUMNS, Qt.AlignmentFlag.AlignLeft)
            t.show()  # layouts show new children lazily; focus needs them visible now
        self.grid.setColumnStretch(COLUMNS, 1)

        self.section.setText(f"Found on this Deck  ·  {len(found)}" if found else "Found on this Deck")
        if not found:
            self.note.setText("No setup files found in Downloads, on the Desktop or on an SD card or USB "
                              "drive. Use Browse files to look anywhere else.")
        elif len(found) > len(shown):
            more = len(found) - len(shown)
            self.note.setText(f"{more} more installer{'s' if more != 1 else ''} found — use Browse files.")
        else:
            self.note.setText("")
        self.note.setVisible(bool(self.note.text()))
        count = len(self.win.library.load())
        unfinished = len(core.orphan_prefixes(self.win.paths))
        text = f"Installed programs ({count})  ·  uninstall"
        if unfinished:
            text += f"  ·  {unfinished} unfinished"
        self.manage_btn.setText(text)
        self.manage_btn.setVisible(count + unfinished > 0)

    def _icon(self, f: core.FoundInstaller) -> QImage | None:
        """The installer's own icon (cached: installers can be big)."""
        key = (str(f.path), f.mtime)
        if key not in self.icons:
            try:
                self.icons[key] = artwork.load_exe_icon(f.path)
            except Exception:  # noqa: BLE001 — a damaged file just gets the plain tile
                self.icons[key] = None
        return self.icons[key]

    def enter(self) -> None:
        self.refresh()
        (self.tiles[1] if len(self.tiles) > 1 else self.tiles[0]).setFocus()

    def hints(self):
        return self.with_paging([("A", "Select", self.win.nav_activate), ("B", "Back", self.back), ("X", "Browse", self.x),
                                 ("☰", "Menu", self.win.open_menu)])

    def back(self) -> None:
        self.win.go_hub()

    def x(self) -> None:
        self.win.browse_installers()
