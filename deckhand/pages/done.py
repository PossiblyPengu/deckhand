"""The DonePage."""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from .. import artwork, core, saves
from ..widgets import Sheet, button, label
from .common import PAGE_MARGINS, Page, close_steam_first, pixmap


class DonePage(Page):
    title = "Installed"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(14)
        lay.addStretch(1)
        self.art = QLabel()
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.art)
        self.heading = label("", "ok")
        self.heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.heading)
        self.text = label("", "dim")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.text)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.delete_btn = button("", slot=self.delete_installer)
        row.addWidget(self.delete_btn)
        self.restore_btn = button("", slot=lambda: self.app and self.win.restore_saves(
            self.app, then=lambda: (self.restore_btn.hide(), self.done_btn.setFocus())))
        row.addWidget(self.restore_btn)
        self.done_btn = button("Done", "primary", self.back)
        self.done_btn.setMinimumWidth(200)
        row.addWidget(self.done_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.app: core.App | None = None

    def load(self, app: core.App) -> None:
        self.app = app
        icon = artwork.load_icon(app.icon)
        self.art.setPixmap(pixmap(artwork.render("", app.name, icon), 460, 215))
        how = app.steam_added
        if how in ("live", "file"):
            self.heading.setText(f"✓  {app.name} is in your Steam library")
            msg = "It's there now — no restart needed." if how == "live" else "It'll be there when Steam starts."
        elif how == "requested":
            self.heading.setText(f"✓  {app.name} is installed and sent to Steam")
            msg = ("Look for it in your library under Non-Steam. If it's not there after restarting Steam, use "
                   "☰ Menu → Installed programs → Add to Steam.")
        elif how == "unavailable":
            self.heading.setText(f"✓  {app.name} is installed")
            msg = "Steam didn't respond, so it wasn't added. " + close_steam_first()
        else:
            self.heading.setText(f"✓  {app.name} is installed")
            msg = "No Steam account was found on this Deck, so it couldn't be added to Steam."
        self.text.setText(f"{msg}\nSteam will launch: {Path(app.exe).name}")
        self.refresh_delete()
        found = saves.backups(self.win.paths, app.name)
        self.restore_btn.setVisible(bool(found))
        if found:  # installed again: it can have its saves back
            when = time.strftime("%b %d", time.localtime(saves.created(found[0])))
            self.restore_btn.setText(f"Restore saves from {when}")

    def installer_files(self) -> list[Path]:
        if not self.app or not self.app.installer:
            return []
        return [f for f in core.installer_files(Path(self.app.installer)) if f.exists()]

    def refresh_delete(self) -> None:
        files = self.installer_files()
        self.delete_btn.setVisible(bool(files))
        if files:
            self.delete_btn.setText(f"Delete installer  ({core.human_size(core.files_size(files))})")

    def delete_installer(self) -> None:
        files = self.installer_files()
        if not files or not self.app:
            return
        names = "\n".join(f"• {f.name}" for f in files[:6]) + ("\n…" if len(files) > 6 else "")
        if Sheet.ask(self, "Delete the installer?", f"{self.app.name} stays installed. This deletes:\n{names}",
                     ("Delete", "Keep"), primary=1, danger=(0,)) != 0:
            return
        freed = core.delete_files(files)
        self.win.flash(f"Freed {core.human_size(freed)}")
        self.win.refresh_space()
        self.refresh_delete()
        self.done_btn.setFocus()

    def enter(self) -> None:
        self.done_btn.setFocus()

    def hints(self):
        return [("A", "Select", self.win.nav_activate), ("B", "Done", self.back)]
