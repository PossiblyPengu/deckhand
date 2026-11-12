"""What every page shares: the Page base class, list helpers and icons."""
from __future__ import annotations

import time
from typing import Callable, TYPE_CHECKING

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, artwork, core, theme, updater
from ..widgets import ElideLabel, PILL_ROLE, TONE_ROLE, button, draw_glyph

if TYPE_CHECKING:
    from ..app import MainWindow

COLUMNS = 4  # tiles per row next to the rail

PAGE_MARGINS = (40, 30, 40, 22)  # every page lines up with the rail's wordmark

MAX_FOUND = 2 * COLUMNS - 1  # two rows of tiles, the first being "Browse files"


def ago(ts: float) -> str:
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    if d < 2 * 86400:
        return "yesterday"
    if d < 30 * 86400:
        return f"{int(d // 86400)} days ago"
    return time.strftime("%b %d, %Y", time.localtime(ts))


def on_choose(lst: QListWidget, handler: Callable[[QListWidgetItem], None]) -> None:
    """Call handler once per choice: tap/click, Enter, or the controller's A.

    A mouse double-click emits clicked, then doubleClicked + activated (or, if the list changed in
    between, a second clicked); only the first click counts. Keyboard/controller use activated.
    """
    state = {"double": False, "last_click": 0.0}

    def clicked(item: QListWidgetItem) -> None:
        # If the first click changed the list (opened a folder), Qt turns the second click of a
        # double-click into a fresh click on whatever is now under the pointer. Ignore it.
        now = time.monotonic()
        if now - state["last_click"] < QApplication.doubleClickInterval() / 1000:
            return
        state["last_click"] = now
        handler(item)

    def activated(item: QListWidgetItem) -> None:
        if state["double"]:
            state["double"] = False
            return
        handler(item)

    lst.itemClicked.connect(clicked)
    lst.itemDoubleClicked.connect(lambda _i: state.update(double=True))
    lst.itemActivated.connect(activated)


def set_pill(item: QListWidgetItem, text: str, tone: str = "todo") -> None:
    """The status pill on a card row (see CardDelegate): "done" is green."""
    item.setData(PILL_ROLE, text)
    item.setData(TONE_ROLE, tone)


def select_row(lst: QListWidget, key: str) -> None:
    for i in range(lst.count()):
        if lst.item(i).data(Qt.ItemDataRole.UserRole) == key:
            lst.setCurrentRow(i)
            return


def app_icon() -> QIcon:
    icon = QIcon()
    for n in artwork.ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(artwork.logo_image(n)))
    return icon


def ensure_app_icon() -> None:
    """The app menu's icon (get.sh installs it too; this covers copies run another way)."""
    data = core.data_home()
    if not (data / "icons/hicolor/256x256/apps/deckhand.png").exists():
        artwork.install_app_icon(data)


def glyph_icon(kind: str, color: str | None = None) -> QIcon:
    color = color or theme.TEXT_DIM
    pm = QPixmap(48, 48)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_glyph(p, QRectF(8, 8, 32, 32), kind, QColor(color))
    p.end()
    return QIcon(pm)


def badge_icon(name: str, color: str) -> QIcon:
    """A rounded square in a service's colour with its initials."""
    pm = QPixmap(96, 96)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(4, 4, 88, 88), 22, 22)
    f = p.font()
    f.setPixelSize(36)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(4, 4, 88, 88), Qt.AlignmentFlag.AlignCenter, artwork.initials(name))
    p.end()
    return QIcon(pm)


def logo_icon(key: str, name: str, color: str) -> QIcon:
    """A service's, store's or add-on's own logo; a badge with its initials if it has none."""
    img = artwork.logo(key)
    if img is None:
        return badge_icon(name, color)
    return QIcon(pixmap(img, 96, 96))


def pixmap(img: QImage, w: int, h: int) -> QPixmap:
    return QPixmap.fromImage(img.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))


class Page(QWidget):
    title = ""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win

    def enter(self) -> None:
        """Called every time the page is shown."""

    def hints(self) -> list[tuple[str, str, Callable]]:
        return [("A", "Select", self.win.nav_activate), ("B", "Back", self.back)]

    def back(self) -> None:
        self.win.go_home()

    def x(self) -> None:
        pass

    def scroll_area(self) -> QScrollArea | None:
        return self.findChild(QScrollArea)

    def add_banner(self, lay: QVBoxLayout) -> None:
        """The "new Deckhand is out" banner (on Start and Install)."""
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        self.banner.setStyleSheet(f"QFrame#banner {{ background: {theme.SURFACE}; border: 2px solid {theme.ACCENT};"
                                  " border-radius: 22px; }")
        b = QHBoxLayout(self.banner)
        b.setContentsMargins(20, 10, 12, 10)
        self.banner_text = ElideLabel(mode=Qt.TextElideMode.ElideRight)
        b.addWidget(self.banner_text)
        b.addStretch(1)
        self.update_btn = button("Update now", "primary", lambda: self.win.start_update())
        b.addWidget(self.update_btn)
        b.addWidget(button("Later", slot=self.hide_banner))
        self.banner.hide()
        lay.addWidget(self.banner)

    def show_banner(self, update: updater.Update) -> None:
        self.banner_text.setText(f"Deckhand {update.version} is available  ·  you have {__version__}")
        self.banner.show()

    def hide_banner(self) -> None:
        focused = self.banner.isAncestorOf(QApplication.focusWidget())
        self.win.hide_banners()
        self.win.update_dismissed = True
        if focused:
            self.enter()

    def show_progress(self, pct: int) -> None:
        """For pages with a `bar`: a percentage, -1 for busy, or None to hide it."""
        bar = getattr(self, "bar", None)
        if bar is None:
            return
        if pct is None:
            bar.hide()
            return
        bar.setRange(0, 0 if pct < 0 else 100)
        if pct >= 0:
            bar.setValue(pct)
        bar.show()


def close_steam_first(then: str = "☰ Menu → Installed programs → Add to Steam") -> str:
    return ("To add it with Steam closed: in Desktop Mode, exit Steam (Steam menu → Exit), open Deckhand "
            f"from the app menu and use {then}.")

REMOVE_IN_STEAM = "In Steam, open it and choose ⚙ → Manage → Remove non-Steam game from your library."

STEAM_STATE = {"in": "In Steam", "sent": "Sent to Steam", "out": "Not in Steam"}

KIND_LABEL = {"stream": "Streaming service", "store": "Game store", "addon": "Add-on"}


def count_names(names: list[str], limit: int = 8) -> str:
    """'• Name — 2 extra copies' lines for a list of duplicate shortcut names."""
    import collections

    counts = collections.Counter(names).most_common()
    lines = [f"•  {n} — {c} extra cop{'y' if c == 1 else 'ies'}" for n, c in counts[:limit]]
    if len(counts) > limit:
        lines.append(f"…and {len(counts) - limit} more")
    return "\n".join(lines)
