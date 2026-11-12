"""The front door: six big cards, one per section. D-pad to a card, A to open it, B to come back here."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout

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
    """A big card that grows with the screen: a coloured glyph at the top, the section's name and one line about it
    at the bottom."""

    MIN_W, MIN_H = 280, 170

    def __init__(self, key: str, title: str, glyph: str, detail: str, tint: QColor):
        super().__init__()
        self.key, self.title, self.glyph, self.detail, self.tint = key, title, glyph, detail, tint
        self.setMinimumSize(self.MIN_W, self.MIN_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
        return QSize(self.MIN_W, self.MIN_H)

    def fonts(self) -> tuple[QFont, QFont]:
        """The title and detail fonts for the card's current size."""
        h = self.height()
        title = QFont()
        title.setPixelSize(int(max(26, min(36, h * 0.14))))
        title.setBold(True)
        detail = QFont()
        detail.setPixelSize(int(max(16, min(20, h * 0.075))))
        return title, detail

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        focused = self.hasFocus()
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        card = QPainterPath()
        card.addRoundedRect(r, 28, 28)
        p.fillPath(card, QColor(theme.SURFACE_HI if focused or self.underMouse() else theme.SURFACE))
        if focused:
            p.setPen(QPen(QColor(theme.ACCENT), 3))
            p.drawPath(card)
        pad = max(20.0, min(30.0, r.width() * 0.07))
        title_font, detail_font = self.fonts()
        tm, dm = QFontMetrics(title_font), QFontMetrics(detail_font)
        text_h = tm.height() + 6 + dm.height() * 2
        side = max(56.0, min(120.0, r.height() - text_h - pad * 2 - 12))
        disc = QRectF(r.x() + pad, r.y() + pad, side, side)
        p.setBrush(self.tint)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(disc)
        inset = side * 0.24
        draw_glyph(p, disc.adjusted(inset, inset, -inset, -inset), self.glyph, QColor(theme.ON_ACCENT))
        width = r.width() - pad * 2
        bottom = r.bottom() - pad
        lines = 1 if dm.horizontalAdvance(self.detail) <= width else 2
        detail_rect = QRectF(r.x() + pad, bottom - dm.height() * lines, width, dm.height() * lines)
        title_rect = QRectF(r.x() + pad, detail_rect.y() - 6 - tm.height(), width, tm.height())
        p.setFont(title_font)
        p.setPen(QColor(theme.TEXT))
        p.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   tm.elidedText(self.title, Qt.TextElideMode.ElideRight, int(width)))
        p.setFont(detail_font)
        p.setPen(QColor(theme.TEXT_DIM))
        text = dm.elidedText(self.detail, Qt.TextElideMode.ElideRight, int(width * 2 - 20))
        p.drawText(detail_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, text)


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
        for col in range(self.COLUMNS):
            grid.setColumnStretch(col, 1)
        for row in range((len(SECTIONS) + self.COLUMNS - 1) // self.COLUMNS):
            grid.setRowStretch(row, 1)
        outer.addLayout(grid, 1)  # the cards take all the room between the heading and the footer
        outer.addSpacing(6)
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
