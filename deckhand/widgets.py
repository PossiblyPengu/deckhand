"""Deck-sized building blocks: tiles, button hints, full-screen sheets, step indicator, toasts."""
from __future__ import annotations

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
        card.addRoundedRect(r, 14, 14)
        p.fillPath(card, QColor(theme.SURFACE_HI if focused or self.underMouse() else theme.SURFACE))

        art = QRectF(r.x(), r.y(), r.width(), r.height() * 0.62)
        clip = QPainterPath()
        clip.setFillRule(Qt.FillRule.WindingFill)  # union: rounded top corners, square bottom
        clip.addRoundedRect(art, 14, 14)
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
                 value: str = ""):
        top = parent.window()
        super().__init__(top)
        self.top = top
        self.choice = -1
        self.primary = primary
        self._loop: QEventLoop | None = None
        self.setObjectName("sheet")
        self.setGeometry(top.rect())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(f"QFrame#card {{ background: {theme.BG_RAISED}; border: 2px solid {theme.LINE};"
                           " border-radius: 18px; }")
        card.setMinimumWidth(min(760, top.width() - 80))
        card.setMaximumWidth(min(980, top.width() - 40))
        card.setMaximumHeight(max(300, top.height() - 40))
        lay = QVBoxLayout(card)
        lay.setContentsMargins(36, 30, 36, 30)
        lay.setSpacing(16)
        lay.addWidget(label(title, "h2"))
        if text:
            lay.addWidget(label(text, "dim"))
        self.field: QLineEdit | None = None
        self.value = value
        if field is not None:  # a line of text to type: `field` is its placeholder
            self.field = QLineEdit(value)
            self.field.setPlaceholderText(field)
            self.field.returnPressed.connect(lambda: self._pick(primary))
            lay.addWidget(self.field)
        if detail:
            box = QPlainTextEdit(detail)
            box.setReadOnly(True)
            box.setMinimumHeight(min(320, max(120, top.height() - 420)))
            lay.addWidget(box, 1)
        menu = len(buttons) > 3  # a list of choices: stack them, D-pad up/down
        btns = QVBoxLayout() if menu else QHBoxLayout()
        btns.setSpacing(10)
        if not menu:
            btns.addStretch(1)
        self.buttons: list[QPushButton] = []
        for i, text_ in enumerate(buttons):
            obj = "primary" if i == primary else ("danger" if i in danger else "")
            b = button(text_, obj, lambda i=i: self._pick(i))
            b.setMinimumWidth(170)
            btns.addWidget(b)
            self.buttons.append(b)
        lay.addLayout(btns)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        self.hide()

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
        if self.field is not None:
            self.field.setFocus()
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
        p.fillRect(self.rect(), QColor(0, 0, 0, 170))
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


class Toast(QLabel):
    """A short message that fades in at the bottom of the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setStyleSheet(f"background: {theme.SURFACE_HI}; color: {theme.TEXT}; border: 2px solid {theme.LINE};"
                           " border-radius: 14px; padding: 12px 22px; font-size: 18px;")
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, ms: int = 2600) -> None:
        self.setText(text)
        self.adjustSize()
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 76)
        self.raise_()
        self.show()
        self._timer.start(ms)
