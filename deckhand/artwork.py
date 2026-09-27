"""Program icons and Steam library artwork, drawn with Qt.

Non-Steam shortcuts show up as blank tiles in Game Mode. We render simple artwork from the
program's own icon and name and drop it where Steam looks for custom art
(userdata/<user>/config/grid/<appid>p.png etc.), never overwriting art the user added.
"""
from __future__ import annotations

import functools
import zlib
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QRadialGradient

from . import core

# Steam's file names for a shortcut's custom artwork, and their sizes.
STEAM_ART = {
    "p": (600, 900),       # library capsule (portrait)
    "": (920, 430),        # wide capsule / recent games
    "_hero": (1920, 620),  # banner at the top of the game page
    "_logo": (1280, 400),  # drawn over the hero
}


def load_exe_icon(exe: Path | str) -> QImage | None:
    data = core.exe_icon_data(Path(exe))
    if not data:
        return None
    img = QImage.fromData(data)
    return None if img.isNull() else img.convertToFormat(QImage.Format.Format_ARGB32)


def save_icon(img: QImage, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if img.width() < 256:
        img = img.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    img.save(str(path), "PNG")
    return str(path)


@functools.lru_cache(maxsize=None)
def logo(key: str) -> QImage | None:
    """A streaming service's, game store's or add-on's own logo (core.logo_file)."""
    path = core.logo_file(key)
    return load_icon(str(path)) if path else None


def load_icon(path: str) -> QImage | None:
    if not path:
        return None
    img = QImage(path)
    return None if img.isNull() else img


def accent_color(name: str, icon: QImage | None = None) -> QColor:
    """A pleasant colour for backgrounds: the icon's average hue, or one derived from the name."""
    if icon is not None:
        small = icon.scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        r = g = b = n = 0
        for y in range(small.height()):
            for x in range(small.width()):
                c = small.pixelColor(x, y)
                if c.alpha() > 128:
                    r, g, b, n = r + c.red(), g + c.green(), b + c.blue(), n + 1
        if n:
            c = QColor(r // n, g // n, b // n)
            h, s, _v, _a = c.getHsv()
            if s > 40:
                return QColor.fromHsv(h, min(200, max(120, s)), 150)
    hue = zlib.crc32(name.encode()) % 360
    return QColor.fromHsv(hue, 150, 150)


def initials(name: str) -> str:
    words = [w for w in name.replace("-", " ").split() if w[:1].isalnum()]
    if not words:
        return "?"
    return (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()


def draw_icon(p: QPainter, rect: QRectF, name: str, icon: QImage | None, color: QColor) -> None:
    """The program's icon, or its initials on a rounded coloured square."""
    if icon is not None:
        scaled = icon.scaled(int(rect.width()), int(rect.height()), Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        x = rect.x() + (rect.width() - scaled.width()) / 2
        y = rect.y() + (rect.height() - scaled.height()) / 2
        p.drawImage(QPointF(x, y), scaled)
        return
    path = QPainterPath()
    path.addRoundedRect(rect, rect.width() * 0.22, rect.width() * 0.22)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, color.lighter(135))
    grad.setColorAt(1, color.darker(140))
    p.fillPath(path, grad)
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(rect.height() * 0.42))
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, initials(name))


def _background(p: QPainter, w: int, h: int, color: QColor) -> None:
    grad = QLinearGradient(0, 0, w * 0.4, h)
    grad.setColorAt(0, color.darker(110))
    grad.setColorAt(1, QColor("#0e141b"))
    p.fillRect(0, 0, w, h, grad)
    glow = QRadialGradient(QPointF(w * 0.5, h * 0.35), max(w, h) * 0.6)
    c = QColor(color.lighter(160))
    c.setAlpha(90)
    glow.setColorAt(0, c)
    glow.setColorAt(1, QColor(0, 0, 0, 0))
    p.fillRect(0, 0, w, h, glow)


def _title(p: QPainter, rect: QRectF, name: str, px: int) -> None:
    f = QFont()
    f.setBold(True)
    f.setPixelSize(px)
    p.setFont(f)
    flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
    p.setPen(QColor(0, 0, 0, 150))
    p.drawText(rect.translated(0, px * 0.06), flags, name)
    p.setPen(QColor("white"))
    p.drawText(rect, flags, name)


def render(kind: str, name: str, icon: QImage | None) -> QImage:
    """One piece of Steam artwork: kind is a key of STEAM_ART."""
    w, h = STEAM_ART[kind]
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    color = accent_color(name, icon)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                     | QPainter.RenderHint.TextAntialiasing)
    if kind == "p":
        _background(p, w, h, color)
        draw_icon(p, QRectF(w * 0.25, h * 0.2, w * 0.5, w * 0.5), name, icon, color)
        _title(p, QRectF(40, h * 0.58, w - 80, h * 0.3), name, 64)
    elif kind == "":
        _background(p, w, h, color)
        draw_icon(p, QRectF(60, h * 0.2, h * 0.6, h * 0.6), name, icon, color)
        _title(p, QRectF(60 + h * 0.6 + 40, 30, w - h * 0.6 - 160, h - 60), name, 54)
    elif kind == "_hero":
        _background(p, w, h, color)
        p.setOpacity(0.18)
        draw_icon(p, QRectF(w - h * 1.1, -h * 0.15, h * 1.3, h * 1.3), name, icon, color)
        p.setOpacity(1.0)
    else:  # logo: transparent, just the name
        _title(p, QRectF(0, 0, w, h), name, 110)
    p.end()
    return img


def write_steam_artwork(appid: int, name: str, icon: QImage | None, grid_dirs: list[Path],
                        downloaded: dict[str, bytes] | None = None) -> list[str]:
    """Save artwork for a shortcut in every Steam user's grid folder. Returns the files written.
    `downloaded`: image files to use instead of drawing (by kind, e.g. from SteamGridDB)."""
    written: list[str] = []
    if not appid:
        return written
    downloaded = downloaded or {}
    images = {kind: render(kind, name, icon) for kind in STEAM_ART if kind not in downloaded}
    for grid in grid_dirs:
        try:
            grid.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        for kind in STEAM_ART:
            ext = ".jpg" if downloaded.get(kind, b"")[:3] == b"\xff\xd8\xff" else ".png"
            target = grid / f"{appid}{kind}{ext}"
            if any(target.with_suffix(e).exists() for e in (".png", ".jpg", ".jpeg", ".webp")):
                continue  # user's own art (SteamGridDB, Decky…) wins
            try:
                ok = target.write_bytes(downloaded[kind]) > 0 if kind in downloaded else \
                    images[kind].save(str(target), "PNG")
            except OSError:
                ok = False
            if ok:
                written.append(str(target))
    return written


def remove_files(files: list[str]) -> None:
    for f in files:
        try:
            Path(f).unlink()
        except OSError:
            pass


# ── Deckhand's own icon: "d." ────────────────────────────────────────────────
#
# Drawn from shapes rather than a font, so it looks the same on every system: a lowercase d (a ring
# and a stem) in warm white, and the wordmark's coral full stop, on a graphite rounded square.

LOGO_BG_TOP, LOGO_BG_BOTTOM = "#26282e", "#111214"
LOGO_INK, LOGO_DOT = "#f4f1ec", "#ff6b4a"


def draw_logo(p: QPainter, r: QRectF, background: bool = True) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = min(r.width(), r.height())
    x0, y0 = r.x() + (r.width() - s) / 2, r.y() + (r.height() - s) / 2
    if background:
        g = QLinearGradient(x0, y0, x0, y0 + s)
        g.setColorAt(0, QColor(LOGO_BG_TOP))
        g.setColorAt(1, QColor(LOGO_BG_BOTTOM))
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(x0, y0, s, s), s * 0.22, s * 0.22)
        p.fillPath(bg, g)
    u = s / 100  # design grid: 100 × 100
    t = 11 * u  # stroke weight
    # the bowl: a ring, centred low-left
    cx, cy, ro = x0 + 42 * u, y0 + 60 * u, 20 * u
    bowl = QPainterPath()
    bowl.addEllipse(QPointF(cx, cy), ro, ro)
    hole = QPainterPath()
    hole.addEllipse(QPointF(cx, cy), ro - t, ro - t)
    bowl = bowl.subtracted(hole)
    # the stem: from the ascender down to the baseline, on the bowl's right edge
    stem = QPainterPath()
    stem.addRoundedRect(QRectF(cx + ro - t, y0 + 20 * u, t, cy + ro - (y0 + 20 * u)), t / 2, t / 2)
    p.fillPath(bowl.united(stem), QColor(LOGO_INK))
    # the full stop
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(LOGO_DOT))
    d = 7.5 * u
    p.drawEllipse(QPointF(x0 + 76 * u, cy + ro - d), d, d)
    p.restore()


def logo_image(size: int = 256, background: bool = True) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    draw_logo(p, QRectF(0, 0, size, size), background)
    p.end()
    return img


ICON_SIZES = (32, 48, 64, 128, 256, 512)


def install_app_icon(data_home: Path | None = None) -> Path | None:
    """Put the icon where the desktop finds it (~/.local/share/icons/hicolor/…/deckhand.png).
    Returns the 256 px file, which Steam shortcuts and the menu entry point at."""
    base = (data_home or Path.home() / ".local/share") / "icons/hicolor"
    out = None
    for n in ICON_SIZES:
        f = base / f"{n}x{n}/apps/deckhand.png"
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            if logo_image(n).save(str(f), "PNG") and n == 256:
                out = f
        except OSError:
            continue
    return out
