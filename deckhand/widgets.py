"""Deck-sized building blocks: tiles, button hints, full-screen sheets, step indicator, toasts."""
from __future__ import annotations

import os
from typing import Callable

from PyQt6.QtCore import QEvent, QEventLoop, QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from . import artwork, theme


def label(text: str = "", obj: str = "", wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    if obj:
        lab.setObjectName(obj)
    lab.setWordWrap(wrap)
    return lab


def button(text: str, obj: str = "", slot: Callable | None = None) -> QPushButton:
    b = QPushButton(text)
    if obj:
        b.setObjectName(obj)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    if slot:
        b.clicked.connect(lambda _checked=False: slot())
    return b


def draw_glyph(p: QPainter, r: QRectF, kind: str, color: QColor) -> None:
    """Simple vector icons (no icon font needed): 'folder', 'download', 'up', 'signal', 'bag', 'stack', 'disc'…"""
    pen = QPen(color, max(3.0, r.width() * 0.07))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    if kind == "folder":
        path = QPainterPath()
        path.moveTo(x, y + h * 0.25)
        path.lineTo(x + w * 0.38, y + h * 0.25)
        path.lineTo(x + w * 0.48, y + h * 0.36)
        path.lineTo(x + w, y + h * 0.36)
        path.lineTo(x + w, y + h * 0.88)
        path.lineTo(x, y + h * 0.88)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "download":
        p.drawLine(int(x + w / 2), int(y + h * 0.08), int(x + w / 2), int(y + h * 0.62))
        p.drawLine(int(x + w * 0.28), int(y + h * 0.42), int(x + w / 2), int(y + h * 0.64))
        p.drawLine(int(x + w * 0.72), int(y + h * 0.42), int(x + w / 2), int(y + h * 0.64))
        tray = QPainterPath()
        tray.moveTo(x + w * 0.08, y + h * 0.66)
        tray.lineTo(x + w * 0.08, y + h * 0.9)
        tray.lineTo(x + w * 0.92, y + h * 0.9)
        tray.lineTo(x + w * 0.92, y + h * 0.66)
        p.drawPath(tray)
    elif kind == "up":
        p.drawLine(int(x + w / 2), int(y + h * 0.15), int(x + w / 2), int(y + h * 0.85))
        p.drawLine(int(x + w * 0.22), int(y + h * 0.42), int(x + w / 2), int(y + h * 0.15))
        p.drawLine(int(x + w * 0.78), int(y + h * 0.42), int(x + w / 2), int(y + h * 0.15))
    elif kind == "signal":  # streaming: a dot with waves going out
        c = QPointF(x + w * 0.2, y + h * 0.8)
        p.setBrush(color)
        p.drawEllipse(c, w * 0.07, h * 0.07)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in (0.38, 0.62, 0.86):
            p.drawArc(QRectF(c.x() - w * k, c.y() - h * k, 2 * w * k, 2 * h * k), 0, 90 * 16)
    elif kind == "stack":  # installed things: three stacked cards
        for i in range(3):
            t = y + h * (0.12 + i * 0.26)
            p.drawRoundedRect(QRectF(x + w * (0.1 + (2 - i) * 0.04), t, w * (0.8 - (2 - i) * 0.08), h * 0.2), 3, 3)
    elif kind == "menu":
        for k in (0.28, 0.5, 0.72):
            p.drawLine(int(x + w * 0.18), int(y + h * k), int(x + w * 0.82), int(y + h * k))
    elif kind == "bag":  # game stores: a shopping bag
        body = QPainterPath()
        body.moveTo(x + w * 0.14, y + h * 0.34)
        body.lineTo(x + w * 0.86, y + h * 0.34)
        body.lineTo(x + w * 0.82, y + h * 0.9)
        body.lineTo(x + w * 0.18, y + h * 0.9)
        body.closeSubpath()
        p.drawPath(body)
        p.drawArc(QRectF(x + w * 0.33, y + h * 0.1, w * 0.34, h * 0.42), 0, 180 * 16)
    elif kind == "plus":  # add-ons: a rounded square with a plus
        p.drawRoundedRect(r.adjusted(w * 0.08, h * 0.08, -w * 0.08, -h * 0.08), w * 0.18, h * 0.18)
        p.drawLine(int(x + w / 2), int(y + h * 0.3), int(x + w / 2), int(y + h * 0.7))
        p.drawLine(int(x + w * 0.3), int(y + h / 2), int(x + w * 0.7), int(y + h / 2))
    elif kind == "check":
        path = QPainterPath()
        path.moveTo(x + w * 0.18, y + h * 0.52)
        path.lineTo(x + w * 0.42, y + h * 0.76)
        path.lineTo(x + w * 0.84, y + h * 0.28)
        p.drawPath(path)
    elif kind == "flag":  # start: a flag on a pole
        p.drawLine(int(x + w * 0.22), int(y + h * 0.1), int(x + w * 0.22), int(y + h * 0.92))
        flag = QPainterPath()
        flag.moveTo(x + w * 0.22, y + h * 0.14)
        flag.lineTo(x + w * 0.82, y + h * 0.14)
        flag.lineTo(x + w * 0.66, y + h * 0.33)
        flag.lineTo(x + w * 0.82, y + h * 0.52)
        flag.lineTo(x + w * 0.22, y + h * 0.52)
        p.drawPath(flag)
    elif kind == "alert":  # something went wrong: an exclamation mark
        p.drawLine(int(x + w / 2), int(y + h * 0.16), int(x + w / 2), int(y + h * 0.6))
        p.setBrush(color)
        p.drawEllipse(QPointF(x + w / 2, y + h * 0.82), w * 0.05, h * 0.05)
    elif kind == "question":
        q = QPainterPath()
        q.moveTo(x + w * 0.3, y + h * 0.34)
        q.cubicTo(x + w * 0.3, y + h * 0.08, x + w * 0.72, y + h * 0.06, x + w * 0.72, y + h * 0.32)
        q.cubicTo(x + w * 0.72, y + h * 0.5, x + w * 0.5, y + h * 0.5, x + w * 0.5, y + h * 0.64)
        p.drawPath(q)
        p.setBrush(color)
        p.drawEllipse(QPointF(x + w / 2, y + h * 0.84), w * 0.05, h * 0.05)
    elif kind == "info":
        p.drawLine(int(x + w / 2), int(y + h * 0.42), int(x + w / 2), int(y + h * 0.84))
        p.setBrush(color)
        p.drawEllipse(QPointF(x + w / 2, y + h * 0.2), w * 0.05, h * 0.05)
    elif kind == "trash":  # removing something: a bin
        p.drawLine(int(x + w * 0.14), int(y + h * 0.24), int(x + w * 0.86), int(y + h * 0.24))
        p.drawLine(int(x + w * 0.38), int(y + h * 0.1), int(x + w * 0.62), int(y + h * 0.1))
        body = QPainterPath()
        body.moveTo(x + w * 0.24, y + h * 0.24)
        body.lineTo(x + w * 0.3, y + h * 0.9)
        body.lineTo(x + w * 0.7, y + h * 0.9)
        body.lineTo(x + w * 0.76, y + h * 0.24)
        p.drawPath(body)
    else:  # disc
        p.drawEllipse(r.adjusted(w * 0.05, h * 0.05, -w * 0.05, -h * 0.05))
        p.drawEllipse(r.adjusted(w * 0.4, h * 0.4, -w * 0.4, -h * 0.4))


class Tile(QPushButton):
    """A big focusable card: artwork (or a glyph) on top, title and subtitle underneath."""

    W, H = 214, 236

    def __init__(self, title: str, subtitle: str = "", icon: QImage | None = None, glyph: str = "",
                 color: QColor | None = None):
        super().__init__()
        self.title, self.subtitle, self.icon, self.glyph = title, subtitle, icon, glyph
        self.color = color or artwork.accent_color(title, icon)
        self.setFixedSize(self.W, self.H)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)  # repaint on mouse hover, not just focus
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.W, self.H)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        focused = self.hasFocus()
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        card = QPainterPath()
        card.addRoundedRect(r, 22, 22)
        p.fillPath(card, QColor(theme.SURFACE_HI if focused or self.underMouse() else theme.SURFACE))

        art = QRectF(r.x(), r.y(), r.width(), r.height() * 0.62)
        clip = QPainterPath()
        clip.setFillRule(Qt.FillRule.WindingFill)  # union: rounded top corners, square bottom
        clip.addRoundedRect(art, 22, 22)
        clip.addRect(QRectF(art.x(), art.y() + 20, art.width(), art.height() - 20))
        p.save()
        p.setClipPath(clip)
        bg = self.color if not self.glyph else QColor(theme.BG_RAISED)
        p.fillRect(art, bg.darker(170))
        p.restore()
        side = art.height() * 0.62
        icon_rect = QRectF(art.center().x() - side / 2, art.center().y() - side / 2, side, side)
        if self.glyph:
            draw_glyph(p, icon_rect.adjusted(side * 0.12, side * 0.12, -side * 0.12, -side * 0.12),
                       self.glyph, QColor(theme.ACCENT if focused else theme.TEXT_DIM))
        else:
            artwork.draw_icon(p, icon_rect, self.title, self.icon, self.color)

        text = QRectF(r.x() + 14, art.bottom() + 8, r.width() - 28, r.bottom() - art.bottom() - 12)
        f = QFont()
        f.setPixelSize(17)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        fm = QFontMetrics(f)
        title_rect = QRectF(text.x(), text.y(), text.width(), fm.height() * 2 + 2)
        title = self.title
        if fm.horizontalAdvance(title) > text.width() * 2 - 20:
            title = fm.elidedText(title, Qt.TextElideMode.ElideRight, int(text.width() * 2 - 30))
        p.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, title)
        if self.subtitle:
            f2 = QFont()
            f2.setPixelSize(14)
            p.setFont(f2)
            p.setPen(QColor(theme.MUTED))
            sub = QFontMetrics(f2).elidedText(self.subtitle, Qt.TextElideMode.ElideRight, int(text.width()))
            p.drawText(QRectF(text.x(), text.bottom() - 20, text.width(), 20), Qt.AlignmentFlag.AlignLeft, sub)

        if focused:
            p.setPen(QPen(QColor(theme.ACCENT), 3))
            p.drawPath(card)
        p.end()


# What a dialog or a notification is about, and how that looks: (disc colour, glyph). Pastels that read on both looks,
# with dark ink on them (like the section cards).
TONES = {
    "info": ("#b9c7f2", "info"),
    "question": ("#c9b2f0", "question"),
    "success": ("#a8d5a2", "check"),
    "warning": ("#f6c08f", "alert"),
    "danger": ("#f4a7a0", "trash"),
    "menu": ("#c9b2f0", "menu"),
    "update": ("#b9c7f2", "download"),
}
INK = "#2f2448"

_FAILED = ("couldn't", "can't", "cannot", "failed", "didn't", "isn't working", "not working", "no admin password",
           "is needed", "needed", " first", "still running", "wasn't")
_DONE = ("is installed", "is ready", "uninstalled", "removed", "up to date", "freed", "deleted", "is in your steam",
         "updated", "added", "saved", "backed up", "restored", "installed")


def tone_for(title: str, buttons: tuple[str, ...] = (), danger: tuple[int, ...] = ()) -> str:
    """Guess what a message is about from its words: something failed, something to confirm, something done…"""
    t = title.strip().lower()
    if any(w in t for w in _FAILED):
        return "warning"
    if danger:
        return "danger"
    if t == "menu" or len(buttons) > 3:
        return "menu"
    if t.endswith("?"):
        return "question"
    if "available" in t and ("update" in t or t.startswith("deckhand ")):
        return "update"
    if "isn't" in t or " not " in f" {t} ":
        return "info"
    if any(t.endswith(w) or f"{w} " in t for w in _DONE) or t.startswith("you're up to date"):
        return "success"
    return "info"


class Badge(QWidget):
    """A pastel disc with a glyph: says at a glance what a dialog or notification is about."""

    def __init__(self, tone: str = "info", size: int = 64, glyph: str = ""):
        super().__init__()
        self.tone = tone if tone in TONES else "info"
        self.glyph = glyph or TONES[self.tone][1]
        self.setFixedSize(size, size)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TONES[self.tone][0]))
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.drawEllipse(r)
        inset = r.width() * 0.26
        draw_glyph(p, r.adjusted(inset, inset, -inset, -inset), self.glyph, QColor(INK))
        p.end()


class Hint(QPushButton):
    """One controller hint in the bottom bar ('Ⓐ Select'). Also tappable."""

    def __init__(self, glyph: str, text: str, slot: Callable):
        super().__init__()
        self.glyph, self.text_ = glyph, text
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda _c=False: slot())
        f = QFont()
        f.setPixelSize(16)
        self.setFixedHeight(40)
        self.setFixedWidth(QFontMetrics(f).horizontalAdvance(text) + 58)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; min-height: 0; }")

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QRectF(4, 7, 26, 26)
        p.setBrush(QColor(theme.TEXT_DIM if not self.isDown() else theme.ACCENT))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(c)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(14 if len(self.glyph) == 1 else 12)
        p.setFont(f)
        p.setPen(QColor(theme.BG))
        p.drawText(c, Qt.AlignmentFlag.AlignCenter, self.glyph)
        f2 = QFont()
        f2.setPixelSize(16)
        p.setFont(f2)
        p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(QRectF(38, 0, self.width() - 38, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text_)
        p.end()


class HintBar(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(52)
        self.setStyleSheet(f"HintBar {{ background: {theme.BG_RAISED}; border-top: 1px solid {theme.LINE}; }}")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(24, 0, 24, 0)
        self.lay.setSpacing(18)
        self.left = label("", "muted", wrap=False)
        self.lay.addWidget(self.left)
        self.lay.addStretch(1)
        self.hints: list[Hint] = []

    def set_hints(self, hints: list[tuple[str, str, Callable]], note: str = "") -> None:
        for h in self.hints:
            self.lay.removeWidget(h)
            h.hide()
            h.deleteLater()
        self.hints = [Hint(g, t, s) for g, t, s in hints]
        for h in self.hints:
            self.lay.addWidget(h)
        self.left.setText(note)


class Sheet(QWidget):
    """A controller-friendly dialog with big buttons, drawn over the window. exec() returns the
    chosen button's index (-1 for Back/Esc).

    It's an overlay inside the main window rather than a separate dialog window: in Game Mode
    gamescope shows one window at a time, so a separate dialog could appear on a black screen.
    """

    current: "Sheet | None" = None  # the sheet on top, if any

    def __init__(self, parent: QWidget, title: str, text: str = "", buttons: tuple[str, ...] = ("OK",),
                 primary: int = 0, danger: tuple[int, ...] = (), detail: str = "", field: str | None = None,
                 value: str = "", secret: bool = False, tone: str = "", glyph: str = ""):
        top = parent.window()
        super().__init__(top)
        self.top = top
        self.choice = -1
        self.primary = primary
        self._loop: QEventLoop | None = None
        self.setObjectName("sheet")
        self.setGeometry(top.rect())
        self.tone = tone or tone_for(title, buttons, danger)
        self._fade = 0.0  # 0 → 1 as it appears

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(f"QFrame#card {{ background: {theme.BG_RAISED}; border: 2px solid {theme.LINE};"
                           " border-radius: 30px; }")
        card.setMinimumWidth(min(760, top.width() - 80))
        card.setMaximumWidth(min(980, top.width() - 40))
        card.setMaximumHeight(max(300, top.height() - 40))
        self.card = card
        lay = QVBoxLayout(card)
        lay.setContentsMargins(34, 30, 36, 22)
        lay.setSpacing(16)
        # The heading: a badge that says what this is about (a question, something done, something wrong), the title
        # and what it means.
        head = QHBoxLayout()
        head.setSpacing(22)
        self.badge = Badge(self.tone, 64, glyph)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(8)
        words.addWidget(label(title, "h2"))
        if text:
            body = label(text, "dim")
            body.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            words.addWidget(body)
        head.addLayout(words, 1)
        menu = len(buttons) > 3  # a list of choices: stack them, D-pad up/down
        keys = QHBoxLayout()
        keys.setSpacing(4)
        keys.addWidget(Hint("A", "Choose", self._activate_focused))
        keys.addWidget(Hint("B", "Back", self.reject))
        if menu:  # (a tall list: the controller's buttons go beside the title, where there is room)
            head.addLayout(keys)
        lay.addLayout(head)
        self.field: QLineEdit | None = None
        self.osk = None  # the on-screen keyboard, when there is one
        self.value = value
        if field is not None:  # a line of text to type: `field` is its placeholder
            self.field = QLineEdit(value)
            self.field.setPlaceholderText(field)
            if secret:  # a password: show dots, never the letters
                self.field.setEchoMode(QLineEdit.EchoMode.Password)
            self.field.returnPressed.connect(lambda: self._pick(primary))
            lay.addWidget(self.field)
            if os.environ.get("DECKHAND_OSK"):  # Deckhand's own keyboard, instead of Steam's (a fallback)
                from .osk import OnScreenKeyboard
                self.osk = OnScreenKeyboard(self.field)
                self.osk.done.connect(lambda: self._pick(primary))
                lay.addWidget(self.osk)
        if detail:
            box = QPlainTextEdit(detail)
            box.setObjectName("detail")
            box.setReadOnly(True)
            box.setStyleSheet(f"QPlainTextEdit#detail {{ background: {theme.CONSOLE}; border: 1px solid {theme.LINE};"
                              " border-radius: 16px; padding: 10px 12px; font-size: 15px; }")
            box.setMinimumHeight(min(320, max(120, top.height() - 420)))
            lay.addWidget(box, 1)
        btns = QVBoxLayout() if menu else QHBoxLayout()
        btns.setSpacing(10)
        self.buttons: list[QPushButton] = []
        for i, text_ in enumerate(buttons):
            obj = "primary" if i == primary else ("danger" if i in danger else "")
            b = button(text_, obj, lambda i=i: self._pick(i))
            b.setMinimumWidth(170)
            btns.addWidget(b)
            self.buttons.append(b)
        # The buttons, with the controller's buttons beside them (the hint bar is under the dimmed window).
        foot = QHBoxLayout()
        foot.setSpacing(12)
        if menu:
            foot.addLayout(btns, 1)
        else:
            foot.addLayout(keys)
            foot.addStretch(1)
            foot.addLayout(btns)
        lay.addSpacing(4)
        lay.addLayout(foot)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        self.hide()

    def _activate_focused(self) -> None:
        from PyQt6.QtWidgets import QApplication

        w = QApplication.focusWidget()
        if isinstance(w, QPushButton) and w in self.buttons:
            w.click()
        elif self.buttons:
            self.buttons[self.primary].click()

    def _animate_in(self) -> None:
        """Fade the dimming in and let the card settle, so a dialog arrives instead of popping up."""
        from PyQt6.QtCore import QVariantAnimation

        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(160)
        anim.valueChanged.connect(self._set_fade)
        anim.start()
        self._anim = anim

    def _set_fade(self, v) -> None:
        self._fade = float(v)
        self.update()

    def exec(self) -> int:
        """Show over the window and wait for a choice; everything underneath is disabled meanwhile."""
        from PyQt6.QtWidgets import QApplication, QMainWindow

        prev_focus = QApplication.focusWidget()
        prev_sheet = Sheet.current
        blocked = [w for w in (self.top.centralWidget() if isinstance(self.top, QMainWindow) else None,
                               prev_sheet) if w is not None and w.isEnabled()]
        for w in blocked:
            w.setEnabled(False)
        Sheet.current = self
        self.top.installEventFilter(self)
        self.setGeometry(self.top.rect())
        self.show()
        self.raise_()
        self._animate_in()
        if self.osk is not None:
            self.osk.keys[0].setFocus()  # (the D-pad starts on the keys)
        elif self.field is not None:
            self.field.setFocus()
            from . import core
            if core.in_game_mode():  # no keyboard in Game Mode: Steam's own one, which types into the box
                QTimer.singleShot(300, core.show_steam_keyboard)
        elif self.buttons:
            self.buttons[self.primary].setFocus()
        self._loop = QEventLoop()
        self._loop.exec()
        self.top.removeEventFilter(self)
        for w in blocked:
            w.setEnabled(True)
        Sheet.current = prev_sheet
        self.hide()
        self.deleteLater()
        try:
            if prev_focus is not None and prev_focus.isVisible() and prev_focus.isEnabled():
                prev_focus.setFocus()
        except RuntimeError:  # the widget that had focus was deleted meanwhile
            pass
        return self.choice

    def _pick(self, i: int) -> None:
        self.choice = i
        if self.field is not None:
            self.value = self.field.text()
        if self._loop is not None:
            self._loop.quit()

    def reject(self) -> None:
        self._pick(-1)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 — keep covering the window when it resizes
        if obj is self.top and ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.top.rect())
        return False

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Dim the window in the look's own ink (not plain black), more as it fades in.
        scrim = QColor(10, 8, 22) if theme.SCHEME == "dusk" else QColor(58, 49, 80)
        scrim.setAlpha(int((185 if theme.SCHEME == "dusk" else 130) * max(self._fade, 0.0)))
        p.fillRect(self.rect(), scrim)
        # A soft shadow under the card.
        c = QRectF(self.card.geometry())
        if c.isValid():
            for i, a in enumerate((26, 18, 10)):
                shadow = QPainterPath()
                shadow.addRoundedRect(c.adjusted(-4 - i * 4, -2 - i * 3, 4 + i * 4, 10 + i * 5), 34 + i * 4, 34 + i * 4)
                p.fillPath(shadow, QColor(0, 0, 0, int(a * self._fade)))
        p.end()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Back):
            self.reject()
            return
        super().keyPressEvent(e)

    # Tests replace this to answer without a modal loop.
    @staticmethod
    def ask(parent: QWidget, title: str, text: str = "", buttons: tuple[str, ...] = ("OK",), **kw) -> int:
        return Sheet(parent, title, text, buttons, **kw).exec()

    @staticmethod
    def ask_text(parent: QWidget, title: str, text: str = "", buttons: tuple[str, ...] = ("OK",),
                 placeholder: str = "", value: str = "", **kw) -> tuple[int, str]:
        """Like ask, with a line of text to type: (chosen button, the text)."""
        sheet = Sheet(parent, title, text, buttons, field=placeholder, value=value, **kw)
        choice = sheet.exec()
        return choice, sheet.value


class ElideLabel(QLabel):
    """One line of text that shortens itself with "…" instead of widening the window
    (a long file path must never push buttons off the Deck's screen)."""

    def __init__(self, text: str = "", obj: str = "", mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle):
        super().__init__()
        if obj:
            self.setObjectName(obj)
        self.mode = mode
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(10)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, self.mode, max(10, self.width())))


def breakable(path: object) -> str:
    """A path that word-wrap can break after its slashes (zero-width spaces)."""
    return str(path).replace("/", "/\u200b")


class Steps(QWidget):
    """Prepare → Install → Find program → Add to Steam, with the current one highlighted."""

    NAMES = ("Prepare", "Install", "Find program", "Add to Steam")

    def __init__(self):
        super().__init__()
        self.current = 0
        self.setFixedHeight(84)

    def set_step(self, i: int) -> None:
        self.current = i
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.NAMES)
        seg = self.width() / n
        for i, name in enumerate(self.NAMES):
            cx = seg * i + seg / 2
            if i < n - 1:
                p.setPen(QPen(QColor(theme.GREEN if i < self.current else theme.LINE), 3))
                p.drawLine(int(cx + 20), 22, int(cx + seg - 20), 22)
            done, active = i < self.current, i == self.current
            color = QColor(theme.GREEN if done else theme.ACCENT if active else theme.SURFACE)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QRectF(cx - 16, 6, 32, 32))
            f = QFont()
            f.setBold(True)
            f.setPixelSize(15)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT if (done or active) else theme.MUTED))
            p.drawText(QRectF(cx - 16, 6, 32, 32), Qt.AlignmentFlag.AlignCenter, "✓" if done else str(i + 1))
            f.setBold(active)
            f.setPixelSize(16)
            p.setFont(f)
            p.drawText(QRectF(cx - seg / 2, 44, seg, 30), Qt.AlignmentFlag.AlignCenter, name)
        p.end()


class Notice(QFrame):
    """One notification card: a badge, the message, and (after a dash or a colon) what to do about it."""

    def __init__(self, parent: QWidget, text: str, tone: str):
        super().__init__(parent)
        self.setObjectName("notice")
        self.setStyleSheet(f"QFrame#notice {{ background: {theme.BG_RAISED}; border: 2px solid {theme.LINE};"
                           " border-radius: 26px; }")
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect

        shadow = QGraphicsDropShadowEffect(self)  # (so it stands off whatever is behind it)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 90 if theme.SCHEME == "dusk" else 45))
        self.setGraphicsEffect(shadow)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 24, 12)
        lay.setSpacing(14)
        self.badge = Badge(tone, 40)
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(2)
        head, _, rest = text.partition(" — ")
        self.title = label(head, wrap=True)
        self.title.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {theme.TEXT};")
        words.addWidget(self.title)
        self.more = None
        if rest:
            self.more = label(rest, "dim")
            self.more.setStyleSheet(f"font-size: 15px; color: {theme.TEXT_DIM};")
            words.addWidget(self.more)
        lay.addLayout(words, 1)
        # As wide as the words need (within reason), then as tall as that makes them.
        f = QFont()
        f.setPixelSize(18)
        f.setBold(True)
        f2 = QFont()
        f2.setPixelSize(15)
        need = max(QFontMetrics(f).horizontalAdvance(head) + 12, QFontMetrics(f2).horizontalAdvance(rest))
        width = int(max(320, min(need + 14 + 40 + 14 + 24 + 8, 720, parent.width() - 80)))
        self.setFixedWidth(width)
        self.setFixedHeight(max(64, lay.totalHeightForWidth(width)))


class Toast(QWidget):
    """Notifications: a card that slides up above the hint bar, says what happened, and goes. A new one stacks on top
    of the last (three at most); the newest is nearest the bottom."""

    MAX = 3
    BOTTOM = 70  # above the hint bar

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.notices: list[tuple[Notice, QTimer]] = []
        self.hide()

    def text(self) -> str:
        """The newest message (for tests and screen readers)."""
        if not self.notices:
            return ""
        n = self.notices[-1][0]
        return n.title.text() + (" — " + n.more.text() if n.more is not None else "")

    def show_message(self, text: str, ms: int = 2600, tone: str = "") -> None:
        par = self.parentWidget()
        self.setGeometry(par.rect())
        n = Notice(self, text, tone or tone_for(text))
        n.setAccessibleName(text)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda n=n: self._drop(n))
        timer.start(ms)
        self.notices.append((n, timer))
        while len(self.notices) > self.MAX:
            self._drop(self.notices[0][0])
        self.show()
        self.raise_()
        n.show()
        self._place(animate_new=n)

    def _drop(self, n: Notice) -> None:
        for i, (m, t) in enumerate(self.notices):
            if m is n:
                t.stop()
                self.notices.pop(i)
                m.hide()
                m.deleteLater()
                break
        if self.notices:
            self._place()
        else:
            self.hide()

    def _place(self, animate_new: Notice | None = None) -> None:
        from PyQt6.QtCore import QPoint, QPropertyAnimation

        y = self.height() - self.BOTTOM
        for n, _t in reversed(self.notices):
            y -= n.height()
            target = QPoint((self.width() - n.width()) // 2, y)
            old = getattr(n, "_anim", None)
            if old is not None:
                old.stop()  # (a slide still running would end in the old place)
            start = QPoint(target.x(), target.y() + 40) if n is animate_new else n.pos()
            if start == target:
                n.move(target)
            else:  # the new one slides up into place; the others move up to make room
                n.move(start)
                anim = QPropertyAnimation(n, b"pos", n)
                anim.setDuration(180)
                anim.setStartValue(start)
                anim.setEndValue(target)
                anim.start()
                n._anim = anim
            y -= 10


class Ring(QWidget):
    """Arcadia's Setup progress ring: how many of the steps are done."""

    def __init__(self, size: int = 188):
        super().__init__()
        self.done, self.total = 0, 0
        self.setFixedSize(size, size)

    def set_progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total
        self.setAccessibleName(f"{done} of {total} done")
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        pen = QPen(QColor(theme.SURFACE), 16)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawEllipse(r)
        if self.total and self.done:
            pen.setColor(QColor(theme.GREEN if self.done >= self.total else theme.ACCENT))
            p.setPen(pen)
            p.drawArc(r, 90 * 16, -int(360 * 16 * self.done / self.total))
        f = QFont(theme.TITLE_FONT)
        f.setPixelSize(int(self.height() * 0.24))
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        top = QRectF(r.x(), r.y() + r.height() * 0.18, r.width(), r.height() * 0.42)
        p.drawText(top, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, f"{self.done}/{self.total}")
        f2 = QFont(theme.BODY_FONT)
        f2.setPixelSize(15)
        f2.setBold(True)
        p.setFont(f2)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(r.x(), top.bottom() + 4, r.width(), 24), Qt.AlignmentFlag.AlignHCenter,
                   "all set" if self.total and self.done >= self.total else "done")
        p.end()


PILL_ROLE = Qt.ItemDataRole.UserRole + 1  # the status shown as a pill on the right of a card row
TONE_ROLE = Qt.ItemDataRole.UserRole + 2  # "done" (green), "todo" (plain) or "busy" (accent)


class CardDelegate(QStyledItemDelegate):
    """List rows drawn as Arcadia's cards: logo, name, what it is, and its status as a pill.

    The item's text stays "Name\\nwhat  ·  status  ·  …" (screen readers and tests read it); the part that's
    the pill isn't repeated underneath."""

    H = 88

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), self.H)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        p.save()
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(option.rect).adjusted(2, 3, -2, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        focused = selected and option.widget is not None and option.widget.hasFocus()
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        card = QPainterPath()
        card.addRoundedRect(r, 18, 18)
        p.fillPath(card, QColor(theme.SURFACE_HI if focused else theme.SURFACE if selected or hover
                                else theme.BG_RAISED))
        if focused:
            p.setPen(QPen(QColor(theme.ACCENT), 3))
            p.drawPath(card)

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        side = 52
        x = r.x() + 16
        if icon is not None and not icon.isNull():
            icon.paint(p, int(x), int(r.center().y() - side / 2), side, side)
        x += side + 16

        pill = str(index.data(PILL_ROLE) or "")
        tone = str(index.data(TONE_ROLE) or "todo")
        right = r.right() - 16
        if pill:
            f = QFont(theme.BODY_FONT)
            f.setPixelSize(14)
            f.setBold(True)
            p.setFont(f)
            w = QFontMetrics(f).horizontalAdvance(pill) + 28
            box = QRectF(right - w, r.center().y() - 16, w, 32)
            bg, fg = {"done": (theme.GREEN, theme.ON_GREEN), "busy": (theme.ACCENT, theme.ON_ACCENT)}.get(
                tone, (theme.BG if selected or focused else theme.SURFACE, theme.TEXT_DIM))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(bg))
            p.drawRoundedRect(box, 16, 16)
            p.setPen(QColor(fg))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, pill)
            right = box.left() - 16

        lines = str(index.data(Qt.ItemDataRole.DisplayRole) or "").split("\n")
        title = lines[0]
        rest = [part for line in lines[1:] for part in line.split("  ·  ") if part and part != pill]
        tf = QFont(theme.BODY_FONT)
        tf.setPixelSize(19)
        tf.setBold(True)
        p.setFont(tf)
        p.setPen(QColor(theme.TEXT))
        width = max(10.0, right - x)
        tm = QFontMetrics(tf)
        top = r.y() + (r.height() - (tm.height() + (44 if rest else 0))) / 2
        p.drawText(QRectF(x, top, width, tm.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   tm.elidedText(title, Qt.TextElideMode.ElideRight, int(width)))
        if rest:
            sf = QFont(theme.BODY_FONT)
            sf.setPixelSize(15)
            p.setFont(sf)
            p.setPen(QColor(theme.TEXT_DIM if selected else theme.MUTED))
            p.drawText(QRectF(x, top + tm.height() + 2, width, 42),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                       "  ·  ".join(rest))
        p.restore()
