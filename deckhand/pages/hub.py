"""The front door: six big cards, one per section. D-pad to a card, A to open it, B to come back here."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout

from .. import __version__, core, start, theme
from ..widgets import button, draw_glyph, label
from .common import PAGE_MARGINS, Page

# key, title, glyph, what is inside (the first and last lines are replaced by live facts)
SECTIONS = (
    ("start", "Start", "flag", "A checklist for a new Deck"),
    ("install", "Install", "download", "Windows programs and games"),
    ("stores", "Stores", "bag", "Epic, GOG, Ubisoft and more"),
    ("stream", "Stream", "signal", "Xbox Cloud, GeForce NOW and more"),
    ("addons", "Add-ons", "plus", "Arcadia, Decky, Proton, Nexus"),
    ("installed", "Installed", "stack", "Fix, back up or remove"),
)


class SectionCard(QPushButton):
    """A wide card: a coloured glyph, the section's name and one line about it."""

    W, H = 380, 160

    def __init__(self, key: str, title: str, glyph: str, detail: str, tint: QColor):
        super().__init__()
        self.key, self.title, self.glyph, self.detail, self.tint = key, title, glyph, detail, tint
        self.setFixedSize(self.W, self.H)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setObjectName("card")

    def set_detail(self, text: str) -> None:
        if text != self.detail:
            self.detail = text
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.W, self.H)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        focused = self.hasFocus()
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        card = QPainterPath()
        card.addRoundedRect(r, 24, 24)
        p.fillPath(card, QColor(theme.SURFACE_HI if focused or self.underMouse() else theme.SURFACE))
        if focused:
            p.setPen(QColor(theme.ACCENT))
            p.drawPath(card)
            p.setPen(Qt.PenStyle.NoPen)
        disc = QRectF(r.x() + 20, r.center().y() - 38, 76, 76)
        p.setBrush(self.tint)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(disc)
        draw_glyph(p, disc.adjusted(18, 18, -18, -18), self.glyph, QColor(theme.ON_ACCENT))
        left = disc.right() + 18
        width = r.right() - left - 14
        f = QFont()
        f.setPixelSize(26)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(left, r.center().y() - 46, width, 36), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        f2 = QFont()
        f2.setPixelSize(16)
        p.setFont(f2)
        p.setPen(QColor(theme.TEXT_DIM))
        text = QFontMetrics(f2).elidedText(self.detail, Qt.TextElideMode.ElideRight, int(width * 2 - 20))
        p.drawText(QRectF(left, r.center().y() - 6, width, 50), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                   | Qt.TextFlag.TextWordWrap, text)


class HubPage(Page):
    """Where Deckhand opens, and where B from any section lands."""

    title = "Deckhand"
    COLUMNS = 3

    def __init__(self, win):
        super().__init__(win)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*PAGE_MARGINS)
        outer.setSpacing(12)
        head = QHBoxLayout()
        mark = label(f'deckhand<span style="color:{theme.ACCENT}">.</span>', "wordmark", wrap=False)
        head.addWidget(mark)
        head.addStretch(1)
        self.space = label("", "muted", wrap=False)
        head.addWidget(self.space)
        outer.addLayout(head)
        outer.addWidget(label("What would you like to do?", "dim"))
        self.add_banner(outer)
        outer.addSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)
        tints = [QColor(c) for c in theme.HUB_TINTS]
        self.cards: dict[str, SectionCard] = {}
        for i, (key, title, glyph, detail) in enumerate(SECTIONS):
            c = SectionCard(key, title, glyph, detail, tints[i % len(tints)])
            c.clicked.connect(lambda _x=False, k=key: self.win.switch_section(k))
            self.cards[key] = c
            grid.addWidget(c, i // self.COLUMNS, i % self.COLUMNS)
        outer.addLayout(grid)
        outer.addStretch(1)
        foot = QHBoxLayout()
        self.menu_btn = button("Menu", slot=lambda: self.win.open_menu())
        foot.addWidget(self.menu_btn)
        foot.addStretch(1)
        foot.addWidget(label(f"Deckhand {__version__}", "muted", wrap=False))
        outer.addLayout(foot)

    def refresh_space(self) -> None:
        self.space.setText(f"{core.human_size(core.free_space(self.win.paths.root))} free")

    def refresh(self) -> None:
        self.refresh_space()
        left = sum(not s.done for s in start.steps(self.win.paths, exe=start.self_exe()))
        self.cards["start"].set_detail(f"{left} step{'s' * (left != 1)} to go" if left else "All set up")
        count = len(self.win.library.load())
        self.cards["installed"].set_detail(f"{count} program{'s' * (count != 1)}" if count else "Nothing installed yet")

    def enter(self) -> None:
        self.refresh()
        first_run = self.cards["start"].detail != "All set up"
        (self.cards["start"] if first_run else self.cards["install"]).setFocus()

    def hints(self):
        return [("A", "Open", self.win.nav_activate), ("☰", "Menu", self.win.open_menu)]

    def back(self) -> None:
        pass


__all__ = ["HubPage", "SectionCard", "SECTIONS"]
