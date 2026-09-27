"""Deckhand window: a Steam Deck–first installer. Pick a setup file; the program lands in Steam.

It is deliberately not a launcher or library — once installed, programs live in Steam.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QRectF, QSize, QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import (__version__, addons, artwork, cleanup, core, fixes, proton, saves, steamgriddb, stores, streaming,
               theme, updater)
from .nav import Nav
from .widgets import ElideLabel, HintBar, Sheet, Steps, Tile, Toast, breakable, button, draw_glyph, label

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


def glyph_icon(kind: str, color: str = theme.TEXT_DIM) -> QIcon:
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


class InstallThread(QThread):
    status = pyqtSignal(str)
    log = pyqtSignal(str)
    done = pyqtSignal(object)  # core.PendingInstall
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, installer: Path, paths: core.Paths, allow_no_container: bool = False, name: str = ""):
        super().__init__()
        self.job = core.Installer(installer, paths, status=self.status.emit, log=self.log.emit,
                                  allow_no_container=allow_no_container, name=name)

    def run(self) -> None:
        try:
            self.done.emit(self.job.run())
        except core.Cancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 — anything here is shown to the user
            self.failed.emit(str(e) or type(e).__name__)


class Worker(QThread):
    """Runs fn(status) off the UI thread: adding to Steam can wait a few seconds for Steam."""

    status = pyqtSignal(str)
    progress = pyqtSignal(int)  # percent, or -1 for "busy, no percentage"
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[[Callable[[str], None]], object]):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        def status(text: str) -> None:
            self.status.emit(text)

        status.progress = self.progress.emit  # jobs that can count report percentages here
        try:
            self.done.emit(self.fn(status))
        except Exception as e:  # noqa: BLE001 — shown to the user
            self.failed.emit(str(e) or type(e).__name__)


class SizesThread(QThread):
    size = pyqtSignal(str, object)  # key, bytes (object: sizes can exceed 32-bit int)

    def __init__(self, items: list[tuple[str, list[Path]]]):
        super().__init__()
        self.items = items

    def run(self) -> None:
        for key, folders in self.items:
            if self.isInterruptionRequested():
                return
            self.size.emit(key, sum(core.dir_size(f) for f in folders))


class UpdateCheckThread(QThread):
    result = pyqtSignal(object, bool)  # updater.Update | None, reached any source

    def run(self) -> None:
        errors: list[Exception] = []
        update = updater.check(__version__, errors=errors)
        self.result.emit(update, len(errors) < len(updater.default_sources()))


class UpdateDownloadThread(QThread):
    progress = pyqtSignal(int, int)
    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, update: updater.Update, target: Path):
        super().__init__()
        self.update_, self.target = update, target

    def run(self) -> None:
        try:
            tmp = updater.download(self.update_, self.target.parent, self.progress.emit, self.isInterruptionRequested)
            updater.install(tmp, self.target)
            self.done.emit()
        except updater.Cancelled:
            self.failed.emit("")
        except Exception as e:  # noqa: BLE001 — shown to the user
            self.failed.emit(str(e) or type(e).__name__)


# ── Pages ────────────────────────────────────────────────────────────────────


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
        lay.addWidget(label("Install a Windows program", "h1"))
        lay.addWidget(label("Pick a setup file. Deckhand installs it and adds the program to your "
                            "Steam library.", "dim"))
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        self.banner.setStyleSheet(f"QFrame#banner {{ background: {theme.SURFACE}; border: 2px solid {theme.ACCENT};"
                                  " border-radius: 12px; }")
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

        self.section.setText(f"FOUND ON THIS DECK  ·  {len(found)}" if found else "FOUND ON THIS DECK")
        if not found:
            self.note.setText("No setup files found in Downloads, on the Desktop or on an SD card or USB "
                              "drive. Use Browse files to look anywhere else.")
        elif len(found) > len(shown):
            more = len(found) - len(shown)
            self.note.setText(f"{more} more installer{'s' if more != 1 else ''} found — use Browse files.")
        else:
            self.note.setText("")
        self.note.setVisible(bool(self.note.text()))
        count = sum(a.kind == "program" for a in self.win.library.load())
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

    def show_banner(self, update: updater.Update) -> None:
        self.banner_text.setText(f"Deckhand {update.version} is available  ·  you have {__version__}")
        self.banner.show()

    def hide_banner(self) -> None:
        focused = self.banner.isAncestorOf(QApplication.focusWidget())
        self.banner.hide()
        self.win.update_dismissed = True
        if focused:
            (self.tiles[1] if len(self.tiles) > 1 else self.tiles[0]).setFocus()

    def hints(self):
        return [("A", "Select", self.win.nav_activate), ("X", "Browse", self.x), ("☰", "Menu", self.win.open_menu)]

    def back(self) -> None:
        pass

    def x(self) -> None:
        self.win.browse_installers()


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


class InstallPage(Page):
    title = "Installing"
    STEP_OF = {"prepare": 0, "installer": 1, "wait": 1, "scan": 2, "steam": 3}

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(14)
        self.heading = label("", "h1")
        lay.addWidget(self.heading)
        self.source = ElideLabel(obj="muted")
        lay.addWidget(self.source)
        lay.addSpacing(8)
        self.steps = Steps()
        lay.addWidget(self.steps)
        lay.addStretch(1)
        self.status = label("", "status")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.written = label("", "dim")  # "1.2 GB written · 48 MB/s · about 35%"
        self.written.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.written)
        self.elapsed = label("", "muted")
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.elapsed)
        self.idle = label("", "status")  # the installer seems to be waiting for the user
        self.idle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.idle.setStyleSheet(f"color: {theme.ACCENT}; font-size: 17px;")
        self.idle.hide()
        lay.addWidget(self.idle)
        keep_open = label("Keep Deckhand open until this is done. If it does get closed, finish the "
                          "install later from Installed programs.", "muted")
        keep_open.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(keep_open)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.hide()
        lay.addWidget(self.log, 4)
        lay.addStretch(1)
        row = QHBoxLayout()
        self.details_btn = button("Show details", slot=self.toggle_log)
        row.addWidget(self.details_btn)
        row.addStretch(1)
        self.continue_btn = button("Installer is done — continue", "primary", self.win_continue)
        row.addWidget(self.continue_btn)
        self.cancel_btn = button("Cancel install", "danger", self.back)
        row.addWidget(self.cancel_btn)
        lay.addLayout(row)
        self.started = 0.0
        self.stage = ""
        self.installer: Path | None = None
        self.expected = 0  # the installer's own size: a rough floor for what gets installed
        self.meter: core.WriteMeter | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    IDLE_HINT_AFTER = 45  # seconds without anything written while the installer runs

    def reset(self, installer: Path, name: str = "") -> None:
        self.installer, self.stage, self.meter = Path(installer), "", None
        self.expected = core.files_size(core.installer_files(Path(installer)))
        self.written.setText("")
        self.idle.hide()
        self.bar.setRange(0, 0)
        self.heading.setText(f"Installing {name or core.guess_name(installer)}")
        self.source.setText(str(installer))
        self.steps.set_step(0)
        self.status.setText("Getting ready…")
        self.log.clear()
        self.continue_btn.hide()
        self.cancel_btn.setEnabled(True)
        self.started = time.monotonic()
        self._tick()
        self.timer.start(1000)

    def _tick(self) -> None:
        s = int(time.monotonic() - self.started)
        self.elapsed.setText(f"{s // 60}:{s % 60:02d} elapsed")
        if self.meter is not None and self.stage in ("installer", "wait"):
            self._show_written(*self.meter.sample())

    def _show_written(self, written: int, rate: float) -> None:
        pct = core.install_estimate(written, self.expected)
        parts = [f"{core.human_size(written)} written"]
        if rate >= 1 << 20:
            parts.append(f"{core.human_size(int(rate))}/s")
        if pct is not None:
            parts.append(f"about {pct}% (estimated from the installer's size)")
            self.bar.setRange(0, 100)
            self.bar.setValue(pct)
        else:
            self.bar.setRange(0, 0)  # no estimate (small installer, or bigger than expected): just busy
        self.written.setText("  ·  ".join(parts))
        idle = self.meter.idle_for() if self.meter else 0
        waiting = self.stage == "installer" and idle >= self.IDLE_HINT_AFTER
        self.idle.setVisible(waiting)
        if waiting:
            how_long = f"{int(idle)} s" if idle < 120 else f"{int(idle // 60)} min"
            self.idle.setText(f"Nothing new written for {how_long} — the installer may be waiting for you in "
                              "its window.")

    def on_status(self, text: str, stage: str) -> None:
        if stage == "installer" and self.meter is None:
            # Start counting once Windows is set up, so only the installer's own writing counts.
            places = [Path.home()] + ([self.installer.parent] if self.installer else [])
            self.meter = core.WriteMeter(places)
        if stage in ("scan", "steam") and self.stage in ("installer", "wait") and self.meter is not None:
            written, _rate = self.meter.sample()
            self.written.setText(f"{core.human_size(written)} installed")
            self.idle.hide()
            self.bar.setRange(0, 0)
        self.stage = stage
        self.status.setText(text)
        self.steps.set_step(self.STEP_OF.get(stage, self.steps.current))
        self.continue_btn.setVisible(stage == "wait")
        if stage == "wait":
            self.continue_btn.setFocus()

    def stop(self) -> None:
        self.timer.stop()

    def toggle_log(self) -> None:
        show = not self.log.isVisible()
        self.log.setVisible(show)
        self.details_btn.setText("Hide details" if show else "Show details")

    def win_continue(self) -> None:
        self.continue_btn.hide()
        self.win.continue_install()

    def enter(self) -> None:
        self.details_btn.setFocus()

    def hints(self):
        return [("A", "Select", self.win.nav_activate), ("B", "Cancel", self.back)]

    def back(self) -> None:
        self.win.cancel_install()


class PickPage(Page):
    title = "Choose the program"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(14)
        self.heading = label("Which one is the program?", "h1")
        lay.addWidget(self.heading)
        self.hint = label("", "dim")
        lay.addWidget(self.hint)
        self.list = QListWidget()
        self.list.setIconSize(self.list.iconSize() * 2)
        self.list.itemActivated.connect(lambda _i: self.use())
        lay.addWidget(self.list, 10)
        row = QHBoxLayout()
        row.addWidget(label("Name in Steam", wrap=False))
        self.name = QLineEdit()
        row.addWidget(self.name, 1)
        lay.addLayout(row)
        lay.addStretch(1)
        btns = QHBoxLayout()
        self.portable = button("No install needed", slot=lambda: self.win.use_portable())
        self.portable.setToolTip("The file you picked is the program itself: add it as it is")
        btns.addWidget(self.portable)
        btns.addWidget(button("Browse…", slot=lambda: self.win.browse_program_for_pending()))
        btns.addStretch(1)
        btns.addWidget(button("Cancel", "danger", self.back))
        self.use_btn = button("Add to Steam", "primary", self.use)
        btns.addWidget(self.use_btn)
        lay.addLayout(btns)

    def load(self, pending: core.PendingInstall) -> None:
        cands = pending.candidates
        self.list.clear()
        drive_c = pending.pfx / "drive_c"
        for c in cands[:12]:
            try:
                where = str(c.exe.parent.relative_to(drive_c))
            except ValueError:
                where = str(c.exe.parent)
            icon = artwork.load_exe_icon(c.exe)
            it = QListWidgetItem(f"{c.exe.name}\n{where}")
            it.setIcon(QIcon(QPixmap.fromImage(icon)) if icon is not None
                       else badge_icon(c.exe.stem, artwork.accent_color(c.exe.stem).name()))
            it.setData(Qt.ItemDataRole.UserRole, str(c.exe))
            self.list.addItem(it)
        has = bool(cands)
        self.heading.setText("Which one is the program?" if has else "Nothing was installed")
        self.hint.setText("The installer finished. Pick the program to add to Steam." if has else
                          "No new program files were found. If this file is the program itself (no setup "
                          "needed), add it as it is. Otherwise the installer may have been closed early.")
        self.list.setVisible(has)
        self.use_btn.setVisible(has)
        self.portable.setVisible(pending.installer.suffix.lower() == ".exe")
        self.portable.setObjectName("" if has else "primary")
        self.portable.style().polish(self.portable)
        self.name.setText(pending.name)
        if has:
            self.list.setCurrentRow(0)

    def enter(self) -> None:
        (self.list if self.list.isVisible() else self.portable).setFocus()

    def use(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.win.finish_install(Path(item.data(Qt.ItemDataRole.UserRole)), self.name.text())

    def back(self) -> None:
        if Sheet.ask(self, "Throw this install away?", "Everything the installer put on the Deck is deleted. "
                     "To keep it, pick the program instead — or use Browse… to find it.",
                     ("Delete it", "Keep"), primary=1, danger=(0,)) == 0:
            self.win.discard_pending()


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


def close_steam_first(then: str = "☰ Menu → Installed programs → Add to Steam") -> str:
    return ("To add it with Steam closed: in Desktop Mode, exit Steam (Steam menu → Exit), open Deckhand "
            f"from the app menu and use {then}.")


REMOVE_IN_STEAM = "In Steam, open it and choose ⚙ → Manage → Remove non-Steam game from your library."
STEAM_STATE = {"in": "In Steam", "sent": "Sent to Steam", "out": "Not in Steam"}


def count_names(names: list[str], limit: int = 8) -> str:
    """'• Name — 2 extra copies' lines for a list of duplicate shortcut names."""
    import collections

    counts = collections.Counter(names).most_common()
    lines = [f"•  {n} — {c} extra cop{'y' if c == 1 else 'ies'}" for n, c in counts[:limit]]
    if len(counts) > limit:
        lines.append(f"…and {len(counts) - limit} more")
    return "\n".join(lines)


class InstalledPage(Page):
    """What Deckhand installed, to uninstall things. Deliberately not a launcher: no Play here."""

    title = "Installed programs"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        lay.addWidget(label("Installed programs", "h1"))
        lay.addWidget(label("Pick a program to fix it, back up its saves, uninstall it or add it back to Steam. "
                            "You play them from your Steam library.", "dim"))
        self.list = QListWidget()
        self.list.setIconSize(QSize(44, 44))
        on_choose(self.list, self._activate)
        lay.addWidget(self.list, 1)
        self.empty = label("Nothing installed with Deckhand yet.", "muted")
        lay.addWidget(self.empty)
        self.status = label("", "muted")
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        lay.addWidget(self.bar)
        self.apps: dict[str, core.App] = {}
        self.sizes: dict[str, int] = {}
        self.steam: dict[str, str] = {}  # app id → core.steam_state
        self.leftovers: dict[str, Path] = {}  # "leftover:<folder>" → prefix of an unfinished install
        self.thread: SizesThread | None = None

    def refresh(self) -> None:
        apps = sorted((a for a in self.win.library.load() if a.kind == "program"), key=lambda a: a.installed_at,
                      reverse=True)  # (streaming services have their own page)
        self.apps = {a.id: a for a in apps}
        roots, running = core.steam_roots(), core.steam_is_running()
        entries = core.steam_shortcuts(roots)  # read Steam's list once per refresh
        self.steam = {a.id: core.steam_state(a, roots, running, entries) for a in apps}
        self.list.clear()
        for app in apps:
            it = QListWidgetItem(self._text(app))
            icon = artwork.load_icon(app.icon)
            it.setIcon(QIcon(QPixmap.fromImage(icon)) if icon is not None
                       else badge_icon(app.name, artwork.accent_color(app.name).name()))
            it.setData(Qt.ItemDataRole.UserRole, app.id)
            self.list.addItem(it)
        busy = self.win.pending.compat_dir if self.win.pending else None
        self.leftovers = {f"leftover:{d.name}": d for d in core.orphan_prefixes(self.win.paths) if d != busy}
        for key, d in self.leftovers.items():
            it = QListWidgetItem(self._leftover_text(key))
            it.setIcon(glyph_icon("disc"))
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.list.addItem(it)
        shown = bool(apps or self.leftovers)
        self.list.setVisible(shown)
        self.empty.setVisible(not shown)
        if shown:
            self.list.setCurrentRow(0)
        if self.thread is not None:
            self.thread.requestInterruption()  # it clears self.thread when it's done; never touch a dead one
        t = SizesThread([(a.id, core.app_paths(a)) for a in apps if a.id not in self.sizes]
                        + [(k, [d]) for k, d in self.leftovers.items() if k not in self.sizes])
        t.size.connect(self._on_size)
        t.finished.connect(lambda t=t: self._size_thread_done(t))
        self.thread = t
        t.start()

    def _size_thread_done(self, t: SizesThread) -> None:
        if self.thread is t:
            self.thread = None
        t.deleteLater()

    def _text(self, app: core.App) -> str:
        size = core.human_size(self.sizes[app.id]) if app.id in self.sizes else "…"
        where = STEAM_STATE[self.steam.get(app.id, "out")]
        return f"{app.name}\nInstalled {ago(app.installed_at)}  ·  {size}  ·  {where}"

    def _leftover_text(self, key: str) -> str:
        d = self.leftovers[key]
        name = core.load_install_info(d).get("name") or d.name
        size = core.human_size(self.sizes[key]) if key in self.sizes else "…"
        return f"{name}\nUnfinished install  ·  {size}  ·  Not in Steam"

    def _on_size(self, key: str, size: int) -> None:
        self.sizes[key] = size
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) != key:
                continue
            if key in self.apps:
                it.setText(self._text(self.apps[key]))
            elif key in self.leftovers:
                it.setText(self._leftover_text(key))

    def _activate(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if key in self.leftovers:
            self.win.leftover_chosen(self.leftovers[key], self.sizes.get(key))
            return
        app = self.apps.get(key)
        if app is None or self.win.busy_with("uninstall") or self.win.busy_with("fix"):
            return
        state = self.steam.get(app.id, "out")
        actions: list[tuple[str, Callable | None]] = []
        if state == "in":
            text = "It's in your Steam library: play it from there."
        elif state == "sent":
            text = (f"Deckhand sent this to Steam {ago(app.steam_requested_at)}. Steam hasn't saved its list of "
                    "shortcuts since, so Deckhand can't check it yet — look in your library under Non-Steam.\n\n"
                    "Only send it again if it's not there: otherwise you'll get a duplicate.")
            actions.append(("Send to Steam again", lambda: self.win.add_to_steam(app)))
        else:
            text = "This program isn't in your Steam library."
            actions.append(("Add to Steam", lambda: self.win.add_to_steam(app)))
        uninstall = len(actions)
        actions += [("Uninstall", lambda: self.win.uninstall(app, self.sizes.get(app.id))),
                    ("Fix it: install what it's missing", lambda: self._fix(app)),
                    ("Back up saves", lambda: self.win.back_up_saves(app))]
        if saves.backups(self.win.paths, app.name):
            actions.append(("Restore saves", lambda: self.win.restore_saves(app)))
        actions.append(("Close", None))
        choice = Sheet.ask(self, app.name, text, tuple(t for t, _f in actions), primary=len(actions) - 1,
                           danger=(uninstall,))
        if 0 <= choice < len(actions) and actions[choice][1] is not None:
            actions[choice][1]()

    def _fix(self, app: core.App) -> None:
        """Install a runtime the program is missing into its prefix."""
        available = fixes.available(app)
        done = {o.split(":", 1)[1] for o in app.options if o.startswith("fixed:")}
        text = ("If it won't start, or says a file is missing (MSVCP140.dll, d3dx9_43.dll, a .NET version…), install "
                "what it needs into its own Windows setup. Deckhand downloads Microsoft's installers for it.\n\n"
                + "\n".join(f"•  {f.name}: {f.blurb}" + ("  (installed)" if f.id in done else "") for f in available))
        choice = Sheet.ask(self, f"Fix {app.name}", text, (*(f.name for f in available), "Cancel"),
                           primary=len(available))
        if not 0 <= choice < len(available):
            return
        fix = available[choice]
        self.status.setText(f"Installing {fix.name} for {app.name}…")

        def finished(_result) -> None:
            self.status.setText("")
            self.show_progress(None)
            self.win.flash(f"{fix.name} installed for {app.name} — try it again from Steam", ms=6000)

        def failed(message: str) -> None:
            self.status.setText("")
            self.show_progress(None)
            if message == "NO_RUNTIME":
                message = "No Proton is installed any more. Install Proton Experimental from Steam, then try again."
            head, _, rest = message.partition("\n\n")
            Sheet.ask(self, f"Couldn't install {fix.name}", head, ("Close",), detail=rest)

        self.win.run_worker(lambda status: fixes.apply(fix, app, self.win.paths, status), finished, failed,
                            status=self.status.setText, kind="fix", progress=self.show_progress)

    def enter(self) -> None:
        self.refresh()
        (self.list if self.list.isVisible() else self).setFocus()

    def hints(self):
        return [("A", "Select", self.win.nav_activate), ("B", "Back", self.back)]


class StoresPage(Page):
    """Game stores other than Steam: their apps added to Steam (Linux apps from Flathub, Windows apps with Proton)."""

    title = "Game stores"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        lay.addWidget(label("Game stores", "h1"))
        lay.addWidget(label("Pick a store to add its app to Steam, then sign in and play its games from there. Heroic "
                            "and itch are made for Linux; the others are the stores' own Windows apps, installed with "
                            "Proton. Games whose anti-cheat blocks Linux won't run, whichever store they're from.",
                            "dim"))
        self.list = QListWidget()
        self.list.setIconSize(QSize(44, 44))
        on_choose(self.list, self._activate)
        lay.addWidget(self.list, 1)
        self.status = label("", "muted")
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        lay.addWidget(self.bar)
        self.installed: set[str] | None = None  # Flatpak apps; read in the background
        self.apps: dict[str, core.App] = {}  # store id → its app, as Deckhand set it up
        self.steam: dict[str, str] = {}  # store id → core.steam_state of that app
        self.outside: dict[str, list[dict]] = {}  # store id → its Steam shortcuts made outside this app

    def refresh(self) -> None:
        library = self.win.library.load()
        roots, running = core.steam_roots(), core.steam_is_running()
        entries = core.steam_shortcuts(roots)
        self.apps = {s.id: a for s in stores.STORES if (a := stores.installed(s, library)) is not None}
        self.steam = {i: core.steam_state(a, roots, running, entries) for i, a in self.apps.items()}
        self.outside = {s.id: stores.spots(s, entries, self.win.paths.launchers) for s in stores.STORES}
        row = max(0, self.list.currentRow())
        self.list.clear()
        for s in stores.STORES:
            it = QListWidgetItem(logo_icon(s.id, s.name, s.color), self._text(s))
            it.setData(Qt.ItemDataRole.UserRole, s.id)
            self.list.addItem(it)
        self.list.setCurrentRow(min(row, self.list.count() - 1))
        if self.installed is None and not self.win.busy_with_quietly("store-flatpaks"):
            self.win.run_worker(lambda _s: stores.detect(), self._got_installed, lambda _m: None,
                                kind="store-flatpaks")

    def _got_installed(self, apps: set[str]) -> None:
        self.installed = apps
        if self.win.stack.currentWidget() is self:
            self.refresh()

    def _text(self, s: stores.Store) -> str:
        parts = [s.blurb]
        if s.id in self.apps:
            parts += ["Installed"] if s.is_windows else []
            parts.append(STEAM_STATE[self.steam.get(s.id, "out")])
        elif self.outside.get(s.id):
            parts.append("In Steam (added outside Deckhand)")
        else:
            parts.append("Not installed" if s.is_windows else "Not added yet")
        if s.is_windows:
            parts.append("Windows app")
        elif self.installed is not None:
            uses = streaming.uses(s.service, self.installed)
            parts.append("installed (not from Flathub)" if uses.startswith("local:") else
                         "app installed ✓" if uses in self.installed else "installs from Flathub")
        return f"{s.name}\n" + "  ·  ".join(parts)

    def _activate(self, item: QListWidgetItem) -> None:
        s = stores.store(item.data(Qt.ItemDataRole.UserRole))
        if s is None or self.win.busy_with("store"):
            return
        app = self.apps.get(s.id)
        if app is not None:
            self._manage(s, app)
            return
        outside = self.outside.get(s.id)
        if outside:
            names = ", ".join(sorted({str(e.get("AppName", e.get("appname", s.name))) for e in outside}))
            second = "a second copy" if s.is_windows else "a second shortcut"
            if Sheet.ask(self, s.name, f"{s.name} is already in your Steam library ({names}), added outside "
                         f"Deckhand. Nothing to do — open it from there.\n\nAdding it here as well would give you "
                         f"{second}.", ("Keep what I have", "Add Deckhand's too"), primary=0) != 1:
                return
        if s.is_windows:
            self._install(s)
        else:
            self._add(s)

    def _manage(self, s: stores.Store, app: core.App) -> None:
        """Its app is set up: uninstall it, or put it (back) into Steam."""
        remove = "Uninstall" if s.is_windows else "Remove it"
        state = self.steam.get(s.id, "out")
        text = f"{s.name} is in your Steam library." if state == "in" else \
            f"{s.name} was sent to Steam — look in your library under Non-Steam." if state == "sent" else \
            f"{s.name} is set up, but it isn't in your Steam library."
        if s.is_windows:
            text += "\n\nUninstalling it also deletes the games you installed inside it."
        if state in ("in", "sent"):
            if Sheet.ask(self, s.name, text, (remove, "Close"), primary=1, danger=(0,)) == 0:
                self.win.uninstall(app)
            return
        choice = Sheet.ask(self, s.name, text, ("Add to Steam", remove, "Close"), danger=(1,))
        if choice == 0:
            self.win.add_to_steam(app)
        elif choice == 1:
            self.win.uninstall(app)

    def _add(self, s: stores.Store) -> None:
        """A Linux app: from Flathub (unless it's here already), straight into Steam."""
        if streaming.needs(s.service, self.installed or set()):
            text = f"Deckhand installs {s.name} from Flathub first (it isn't on this Deck yet). "
        else:
            text = f"{s.name} is already installed. "
        text += "Then it's in your Steam library with its own artwork." + (f"\n\n{s.note}" if s.note else "")
        if Sheet.ask(self, f"Add {s.name} to Steam?", text, ("Add to Steam", "Cancel")) == 0:
            self.win.set_up_store(s)

    def _install(self, s: stores.Store) -> None:
        """A Windows app: download its official installer, then install it like any setup file."""
        text = (f"Deckhand downloads {s.name}'s official installer (from {s.site}) and installs it like any setup "
                f"file. Click through the installer; if {s.name} opens by itself at the end, close it or pick "
                f"“Installer is done — continue”.\n\nThen {s.name} is in your Steam library. Sign in the first "
                "time you open it; you install and play its games from inside it.")
        text += f"\n\n{s.note}" if s.note else ""
        if Sheet.ask(self, f"Install {s.name}?", text, ("Download and install", "Cancel")) != 0:
            return
        self.status.setText(f"Downloading {s.name}…")

        def run(status):
            report = getattr(status, "progress", lambda pct: None)

            def progress(done: int, total: int) -> None:
                if total:
                    status(f"Downloading {s.name}…  {core.human_size(done)} of {core.human_size(total)}")
                    report(done * 100 // total)
            report(-1)
            return stores.download(s, self.win.paths, progress)

        def finished(installer: Path) -> None:
            self.status.setText("")
            self.show_progress(None)
            self.win.start_install(installer, name=s.name)

        self.win.run_worker(run, finished, self._failed(s.name), status=self.status.setText, kind="store",
                            progress=self.show_progress)

    def _failed(self, name: str):
        def failed(message: str) -> None:
            self.status.setText("")
            self.show_progress(None)
            head, _, rest = message.partition("\n\n")
            Sheet.ask(self, f"Couldn't set up {name}", head, ("Close",), detail=rest)
            self.refresh()
        return failed

    def enter(self) -> None:
        self.refresh()
        self.list.setFocus()

    def hints(self):
        return [("A", "Select", self.win.nav_activate), ("B", "Back", self.back)]


class StreamingPage(Page):
    """Cloud gaming and home streaming, set up as Steam shortcuts."""

    title = "Game streaming"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        lay.addWidget(label("Game streaming", "h1"))
        lay.addWidget(label("Pick a service to add it to Steam. Deckhand installs what it needs and sets up the "
                            "controller. Sign in the first time you open it; leave with STEAM → Exit game.", "dim"))
        self.on_deck = label("Checking what's installed…", "muted")
        lay.addWidget(self.on_deck)
        self.list = QListWidget()
        self.list.setIconSize(QSize(44, 44))
        on_choose(self.list, self._activate)
        lay.addWidget(self.list, 1)
        self.status = label("", "muted")
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        lay.addWidget(self.bar)
        self.installed: set[str] | None = None  # Flatpak apps; read in the background
        self.steam: dict[str, str] = {}
        self.outside: dict[str, list[dict]] = {}  # service id → its Steam shortcuts made outside this app

    def refresh(self) -> None:
        apps = {a.id: a for a in self.win.library.load() if a.kind == "stream"}
        roots, running = core.steam_roots(), core.steam_is_running()
        entries = core.steam_shortcuts(roots)
        self.steam = {i: core.steam_state(a, roots, running, entries) for i, a in apps.items()}
        self.outside = {svc.id: streaming.spots(svc, entries, self.win.paths.launchers) for svc in streaming.SERVICES}
        row = max(0, self.list.currentRow())
        self.list.clear()
        for svc in streaming.SERVICES:
            it = QListWidgetItem(logo_icon(svc.id, svc.name, svc.color), self._text(svc, apps.get(f"stream-{svc.id}")))
            it.setData(Qt.ItemDataRole.UserRole, svc.id)
            self.list.addItem(it)
        self.list.setCurrentRow(min(row, self.list.count() - 1))
        if self.installed is not None:
            have = [f"{streaming.app_name(b)} {'✓' if b in self.installed else '✗'}" for b in streaming.BROWSERS]
            for svc in streaming.SERVICES:
                if svc.native and svc.native in self.installed:
                    have.append(f"{streaming.app_name(svc.native)} ✓")
                if svc.app:
                    ok = svc.app in self.installed or f"local:{svc.id}" in self.installed
                    have.append(f"{svc.name} {'✓' if ok else '✗'}")
            self.on_deck.setText("On this Deck:  " + "   ".join(have))
        if self.installed is None and not self.win.busy_with_quietly("flatpaks"):
            self.win.run_worker(lambda _s: streaming.detect(streaming.installed_apps()), self._got_installed, lambda _m: None,
                                kind="flatpaks")

    def _got_installed(self, apps: set[str]) -> None:
        self.installed = apps
        if self.win.stack.currentWidget() is self:
            self.refresh()

    def _text(self, svc: streaming.Service, app: core.App | None) -> str:
        if app is not None:
            steam = STEAM_STATE[self.steam.get(app.id, "out")]
        elif self.outside.get(svc.id):
            steam = "In Steam (added outside Deckhand)"
        else:
            steam = "Not added yet"
        parts = [svc.blurb, steam]
        bx = bool(app and streaming.BETTER_XCLOUD in app.options)
        if self.installed is not None:
            uses = streaming.uses(svc, self.installed, bx)
            if uses.startswith("local:") or uses == svc.native:
                parts.append(f"uses {streaming.app_name(uses)}")
            elif uses in self.installed:
                parts.append(f"{streaming.app_name(uses)} ✓" if svc.is_web else "app installed ✓")
            else:
                parts.append(f"installs {streaming.app_name(uses)}" if svc.is_web else "installs the app")
        if bx:
            parts.append("Better xCloud on (Chromium)")
        elif svc.id == "xbox-cloud" and app is None:
            parts.append("or Chromium with Better xCloud")
        return f"{svc.name}\n" + "  ·  ".join(parts)

    def _activate(self, item: QListWidgetItem) -> None:
        svc = streaming.service(item.data(Qt.ItemDataRole.UserRole))
        if svc is None or self.win.busy_with("stream"):
            return
        app = streaming.app_for(svc, self.win.paths)
        state = self.steam.get(app.id, "out") if app else "out"
        installed = self.installed or set()
        xbox = svc.id == "xbox-cloud"
        if app is not None and state in ("in", "sent"):
            where = "is in your Steam library" if state == "in" else "was sent to Steam"
            text = f"{svc.name} {where}. Play it from there."
            if not xbox:
                if Sheet.ask(self, svc.name, text, ("Remove it", "Close"), primary=1, danger=(0,)) == 0:
                    self.win.uninstall(app)
                return
            bx = streaming.BETTER_XCLOUD in app.options
            toggle = "Turn Better xCloud off" if bx else "Turn Better xCloud on"
            choice = Sheet.ask(self, svc.name, text + "\n\n" + self._bx_note(svc, installed, not bx),
                               (toggle, "Remove it", "Close"), primary=2, danger=(1,))
            if choice == 0:
                self.win.set_up_stream(svc, better_xcloud=not bx)
            elif choice == 1:
                self.win.uninstall(app)
            return
        outside = self.outside.get(svc.id) if app is None else None
        if outside:
            names = ", ".join(sorted({str(e.get("AppName", e.get("appname", svc.name))) for e in outside}))
            if Sheet.ask(self, svc.name, f"{svc.name} is already in your Steam library ({names}), added outside "
                         "Deckhand. Nothing to do — play it from there.\n\nAdding it here as well would give you a "
                         "second shortcut.", ("Keep what I have", "Add Deckhand's too"), primary=0) != 1:
                return
        need = streaming.needs(svc, installed)
        if need:
            what = f"Deckhand installs {streaming.app_name(need)} from Flathub first (it isn't on this Deck yet). "
        elif svc.is_web:
            what = f"It opens in {streaming.app_name(streaming.uses(svc, installed))}, which is already installed. "
        else:
            what = f"{svc.name} is already installed. "
        text = what + f"Then {svc.name} is in your Steam library with its own artwork" + (
            ", full screen with the controller working." if svc.is_web else ".")
        if xbox:  # two ways, each saying exactly which browser it uses and whether that gets installed
            text = (f"• Add to Steam: opens in {self._browser_state(svc, installed, False)}.\n"
                    f"• Add with Better xCloud: opens in {self._browser_state(svc, installed, True)}, with Better "
                    "xCloud — a free add-on for a sharper picture, stream stats, Xbox remote play and mouse & "
                    "keyboard. It needs Chromium because Google Chrome can't load it.\n\n"
                    f"Either way {svc.name} is in your Steam library, full screen with the controller working.")
            choice = Sheet.ask(self, f"Add {svc.name} to Steam?", text,
                               ("Add to Steam", "Add with Better xCloud", "Cancel"))
            if choice in (0, 1):
                self.win.set_up_stream(svc, better_xcloud=choice == 1)
            return
        if Sheet.ask(self, f"Add {svc.name} to Steam?", text, ("Add to Steam", "Cancel")) == 0:
            self.win.set_up_stream(svc)

    @staticmethod
    def _browser_state(svc: streaming.Service, installed: set[str], better_xcloud: bool) -> str:
        """'Chromium (already installed)' / 'Google Chrome (Deckhand installs it first)'."""
        b = streaming.uses(svc, installed, better_xcloud)
        state = "already installed" if b in installed else "Deckhand installs it from Flathub first"
        return f"{streaming.app_name(b)} ({state})"

    def _bx_note(self, svc: streaming.Service, installed: set[str], turning_on: bool) -> str:
        if turning_on:
            return ("Better xCloud is a free add-on for a sharper picture, stream stats, Xbox remote play and mouse & "
                    f"keyboard. Turning it on switches to {self._browser_state(svc, installed, True)}, because "
                    "Google Chrome can't load it. It keeps itself up to date.")
        return (f"Better xCloud is on (it runs in Chromium). Turning it off switches to "
                f"{self._browser_state(svc, installed, False)}.")

    def enter(self) -> None:
        self.refresh()
        self.list.setFocus()

    def hints(self):
        return [("A", "Select", self.win.nav_activate), ("B", "Back", self.back)]


class AddonsPage(Page):
    """Popular Deck add-ons and tools, installed from their official sources."""

    title = "Add-ons"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        lay.addWidget(label("Add-ons", "h1"))
        lay.addWidget(label("Popular Deck add-ons and tools, from their official sources. Decky Loader and EmuDeck set "
                            "themselves up in Desktop Mode; the apps from Flathub are installed for your user only.",
                            "dim"))
        self.list = QListWidget()
        self.list.setIconSize(QSize(44, 44))
        on_choose(self.list, self._activate)
        lay.addWidget(self.list, 1)
        self.status = label("", "muted")
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        lay.addWidget(self.bar)
        self.installed: set[str] | None = None  # Flatpak apps; read in the background
        self.apps: dict[str, core.App] = {}  # add-on id → its Steam shortcut (Flathub apps added to Steam)
        self.steam: dict[str, str] = {}  # add-on id → core.steam_state of that

    def refresh(self) -> None:
        library = {a.id: a for a in self.win.library.load() if a.kind == "addon"}
        self.apps = {a.id: library[f"addon-{a.id}"] for a in addons.ADDONS if f"addon-{a.id}" in library}
        roots, running = core.steam_roots(), core.steam_is_running()
        entries = core.steam_shortcuts(roots) if self.apps else []
        self.steam = {i: core.steam_state(app, roots, running, entries) for i, app in self.apps.items()}
        row = max(0, self.list.currentRow())
        self.list.clear()
        for a in addons.ADDONS:
            parts = [a.blurb, addons.status(a, installed=self.installed)]
            if a.id in self.apps:
                parts.append(STEAM_STATE[self.steam.get(a.id, "out")])
            parts.append(f"from {a.site}")
            it = QListWidgetItem(logo_icon(a.id, a.name, a.color), f"{a.name}\n" + "  ·  ".join(parts))
            it.setData(Qt.ItemDataRole.UserRole, a.id)
            self.list.addItem(it)
        self.list.setCurrentRow(min(row, self.list.count() - 1))
        if self.installed is None and not self.win.busy_with_quietly("addon-flatpaks"):
            self.win.run_worker(lambda _s: streaming.installed_apps(), self._got_installed, lambda _m: None,
                                kind="addon-flatpaks")

    def _got_installed(self, apps: set[str]) -> None:
        self.installed = apps
        if self.win.stack.currentWidget() is self:
            self.refresh()

    def _desktop_only(self, a: addons.Addon, what: str) -> None:
        """In Game Mode: explain, and offer to switch to Desktop Mode."""
        text = (f"{what} Switch to Desktop Mode, open Deckhand from the app menu (Games) and pick {a.name} "
                "again.")
        if addons.can_switch_to_desktop():
            if Sheet.ask(self, a.name, text, ("Switch to Desktop Mode", "Not now"), primary=1) == 0:
                addons.switch_to_desktop()
        else:
            Sheet.ask(self, a.name, text, ("Close",))

    def _activate(self, item: QListWidgetItem) -> None:
        a = addons.addon(item.data(Qt.ItemDataRole.UserRole))
        if a is None or self.win.busy_with("addon"):
            return
        if a.id == "decky":
            self._decky(a)
        elif a.id == "emudeck":
            self._emudeck(a)
        elif a.id == "ge-proton":
            self._ge_proton()
        else:
            self._flathub(a)

    # ── apps from Flathub ────────────────────────────────────────────

    def _flathub(self, a: addons.Addon) -> None:
        if self.installed is None:
            self.win.flash("Still checking what's installed…")
            return
        if a.app not in self.installed:
            then = ("Then it's in your Steam library too, so you can open it in Game Mode." if a.steam else
                    "Then it's in the Desktop Mode app menu.")
            if Sheet.ask(self, f"Install {a.name}?", f"Deckhand installs {a.name} from Flathub, for your user only "
                         f"(no admin password needed). {then}", ("Install", "Cancel")) == 0:
                self._set_up(a, a.steam)
            return
        app = self.apps.get(a.id)
        in_steam = app is not None and self.steam.get(a.id) in ("in", "sent")
        actions = [(f"Open {a.name}", lambda: (streaming.open_app(a.app), self.win.flash(f"{a.name} is opening…"))),
                   ("Remove from Steam", lambda: self.win.uninstall(app)) if in_steam else
                   ("Add to Steam", lambda: self._set_up(a, True)),
                   ("Uninstall", lambda: self._uninstall(a)),
                   ("Close", None)]
        where = "It's in your Steam library." if in_steam else "It's in the Desktop Mode app menu."
        choice = Sheet.ask(self, a.name, f"{a.name} is installed (from Flathub). {where}",
                           tuple(t for t, _f in actions), primary=len(actions) - 1, danger=(2,))
        if 0 <= choice < len(actions) and actions[choice][1] is not None:
            actions[choice][1]()

    def _set_up(self, a: addons.Addon, steam: bool) -> None:
        """Install from Flathub (if needed), and add to Steam if `steam`."""
        self.status.setText(f"Setting up {a.name}…")

        def run(status):
            if steam:
                return streaming.set_up(a.service, self.win.paths, status, kind="addon")
            report = getattr(status, "progress", lambda pct: None)
            status(f"Installing {a.name} from Flathub…")
            report(-1)
            streaming.install_app(a.app, lambda line: status(f"Installing {a.name} from Flathub…  {line[:60]}"),
                                  lambda pct: (status(f"Installing {a.name} from Flathub…  {pct}%"), report(pct)))
            return None

        def finished(app: core.App | None) -> None:
            self.status.setText("")
            self.show_progress(None)
            self.installed = None  # re-read
            if app is not None:
                self.win._write_art(app, artwork.load_icon(app.icon))
                self.win._say_added(app, f"Add-ons → {a.name}")
            else:
                self.win.flash(f"{a.name} is installed — it's in the Desktop Mode app menu", ms=5000)
            self.refresh()

        self.win.run_worker(run, finished, self._failed(a.name), status=self.status.setText, kind="addon",
                            progress=self.show_progress)

    def _uninstall(self, a: addons.Addon) -> None:
        app = self.apps.get(a.id)
        also = " and its Steam shortcut" if app is not None else ""
        if Sheet.ask(self, f"Uninstall {a.name}?", f"Removes {a.name}{also}. Its settings stay in "
                     f"~/.var/app/{a.app}, in case you install it again.", ("Uninstall", "Keep"), primary=1,
                     danger=(0,)) != 0:
            return
        self.status.setText(f"Uninstalling {a.name}…")
        self.show_progress(-1)

        def run(_status) -> bool:
            left = core.uninstall(app, self.win.paths) if app is not None else False
            streaming.uninstall_app(a.app)
            return left

        def finished(left_in_steam: bool) -> None:
            self.status.setText("")
            self.show_progress(None)
            self.installed = None
            self.win.flash(f"{a.name} uninstalled")
            self.refresh()
            if left_in_steam:
                Sheet.ask(self, f"{a.name} uninstalled", "Its shortcut is still in your Steam library: Deckhand "
                          "doesn't change Steam's list while Steam is open.\n\n" + REMOVE_IN_STEAM, ("Close",))

        self.win.run_worker(run, finished, self._failed(a.name), status=self.status.setText, kind="addon")

    # ── GE-Proton ────────────────────────────────────────────────────

    def _ge_proton(self) -> None:
        self.status.setText("Looking for the newest GE-Proton…")
        self.show_progress(-1)

        def found(release: proton.Release) -> None:
            self.status.setText("")
            self.show_progress(None)
            have = proton.installed()
            use = ("Deckhand uses it for new installs straight away. For a Steam game, restart Steam, then pick it "
                   "under ⚙ → Properties → Compatibility.")
            if release.version in have:
                Sheet.ask(self, "GE-Proton", f"{release.version}, the newest, is installed. {use}", ("Close",))
                return
            size = f" ({core.human_size(release.size)})" if release.size else ""
            now = f" You have {have[0]}." if have else ""
            if Sheet.ask(self, f"Install {release.version}?", "GE-Proton is Proton with extra fixes and video codecs, "
                         f"by GloriousEggroll: many games and installers work better with it.{now}\n\nDeckhand "
                         f"downloads {release.version}{size} from its GitHub releases, checks it, and puts it with "
                         f"Steam's compatibility tools. {use}", ("Download and install", "Cancel")) == 0:
                self._install_ge(release)

        self.win.run_worker(lambda _s: proton.latest(), found, self._failed("GE-Proton"), kind="addon")

    def _install_ge(self, release: proton.Release) -> None:
        self.status.setText(f"Downloading {release.version}…")

        def run(status):
            report = getattr(status, "progress", lambda pct: None)

            def progress(done: int, total: int) -> None:
                if total:
                    status(f"Downloading {release.version}…  {core.human_size(done)} of {core.human_size(total)}")
                    report(done * 100 // total)
            report(-1)
            path = proton.install(release, progress=progress, status=lambda s: (status(s), report(-1)))
            return path

        def finished(_path) -> None:
            self.status.setText("")
            self.show_progress(None)
            self.win.flash(f"{release.version} is installed", ms=5000)
            self.refresh()

        self.win.run_worker(run, finished, self._failed("GE-Proton"), status=self.status.setText, kind="addon",
                            progress=self.show_progress)

    def _decky(self, a: addons.Addon) -> None:
        installed = addons.decky_version() is not None
        if core.in_game_mode():
            self._desktop_only(a, "Decky's installer asks for your admin password in its own window, so it runs in "
                                  "Desktop Mode.")
            return
        what = ("It offers to update, or to uninstall Decky." if installed else
                "It lets you pick the release (stable SteamOS) or prerelease (beta SteamOS) version.")
        if Sheet.ask(self, "Decky Loader", "Deckhand downloads Decky's own installer (github.com/SteamDeckHomebrew — "
                     f"the one decky.xyz links to) and opens it. {what}\n\nIt asks for your admin password. If you "
                     "haven't set one, it offers to use a temporary one and removes it afterwards.",
                     ("Open Decky's installer", "Cancel")) != 0:
            return
        self.status.setText("Downloading Decky's installer…")
        self.show_progress(-1)

        def run(status):
            script = addons.fetch_decky_installer(self.win.paths.downloads)
            status("Decky's installer is open — follow its windows.")
            return addons.run_decky_installer(script)

        def finished(_rc) -> None:
            self.status.setText("")
            self.show_progress(None)
            v = addons.decky_version()
            self.win.flash(f"Decky Loader {v}" if v and v != "installed" else
                           "Decky Loader is installed" if v else "Decky Loader isn't installed", ms=5000)
            self.refresh()

        self.win.run_worker(run, finished, self._failed("Decky Loader"), status=self.status.setText, kind="addon")

    def _emudeck(self, a: addons.Addon) -> None:
        have = addons.emudeck_app() is not None
        if have:
            choice = Sheet.ask(self, "EmuDeck", f"EmuDeck is {addons.status(a).lower()}. Open it to set up or "
                               "change your emulators, or get its newest version.",
                               ("Open EmuDeck", "Update EmuDeck", "Cancel"))
            if choice == 0:
                self._open_emudeck(a)
            elif choice == 1:
                self._download_emudeck(a)
            return
        if Sheet.ask(self, "EmuDeck", "Deckhand downloads EmuDeck the way its own installer does (the latest version "
                     "from EmuDeck's GitHub releases, into your Applications folder). EmuDeck then sets up emulators, "
                     "your ROMs folders and Steam shortcuts.", ("Download EmuDeck", "Cancel")) == 0:
            self._download_emudeck(a)

    def _download_emudeck(self, a: addons.Addon) -> None:
        self.status.setText("Downloading EmuDeck…")

        def run(status):
            report = getattr(status, "progress", lambda pct: None)

            def progress(done: int, total: int) -> None:
                if total:
                    status(f"Downloading EmuDeck…  {core.human_size(done)} of {core.human_size(total)}")
                    report(done * 100 // total)
            report(-1)
            return addons.download_emudeck(progress)

        def finished(version: str) -> None:
            self.status.setText("")
            self.show_progress(None)
            self.refresh()
            self.win.flash(f"EmuDeck {version} downloaded" if version else "EmuDeck downloaded")
            self._open_emudeck(a)

        self.win.run_worker(run, finished, self._failed("EmuDeck"), status=self.status.setText, kind="addon",
                            progress=self.show_progress)

    def _open_emudeck(self, a: addons.Addon) -> None:
        if core.in_game_mode():
            self._desktop_only(a, "EmuDeck sets itself up in Desktop Mode.")
            return
        try:
            addons.open_emudeck()
            self.win.flash("EmuDeck is opening…")
        except (OSError, core.InstallError) as e:
            Sheet.ask(self, "EmuDeck", str(e), ("Close",))

    def _failed(self, name: str):
        def failed(message: str) -> None:
            self.status.setText("")
            self.show_progress(None)
            head, _, rest = message.partition("\n\n")
            Sheet.ask(self, f"Couldn't set up {name}", head, ("Close",), detail=rest)
            self.refresh()
        return failed

    def enter(self) -> None:
        self.refresh()
        self.list.setFocus()

    def hints(self):
        return [("A", "Select", self.win.nav_activate), ("B", "Back", self.back)]


class UpdatePage(Page):
    title = "Update"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(14)
        self.heading = label("", "h1")
        lay.addWidget(self.heading)
        self.notes = label("", "dim")
        lay.addWidget(self.notes)
        lay.addStretch(1)
        self.status = label("", "status")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = button("Cancel", "danger", self.back)
        row.addWidget(self.cancel_btn)
        self.restart_btn = button("Restart Deckhand", "primary", lambda: self.win.restart_after_update())
        self.restart_btn.setMinimumWidth(260)
        row.addWidget(self.restart_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.finished = False

    def reset(self, update: updater.Update) -> None:
        self.finished = False
        self.heading.setText(f"Updating to Deckhand {update.version}")
        self.notes.setText(update.notes)
        self.notes.setVisible(bool(update.notes))
        self.status.setText("Downloading…")
        self.bar.setRange(0, 0)
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)
        self.restart_btn.hide()

    def on_progress(self, done: int, total: int) -> None:
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done * 1000 / total))
            self.status.setText(f"Downloading…  {core.human_size(done)} of {core.human_size(total)}")
        else:
            self.status.setText(f"Downloading…  {core.human_size(done)}")

    def on_done(self) -> None:
        self.finished = True
        self.heading.setText(self.heading.text().replace("Updating to", "Updated to"))
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.status.setText("✓  Update installed. Restart to use the new version.")
        self.cancel_btn.hide()
        self.restart_btn.show()
        self.restart_btn.setFocus()
        self.win.update_hints()

    def enter(self) -> None:
        (self.restart_btn if self.finished else self.cancel_btn).setFocus()

    def hints(self):
        return [("A", "Select", self.win.nav_activate), ("B", "Later" if self.finished else "Cancel", self.back)]

    def back(self) -> None:
        if self.finished:
            self.win.go_home()
        else:
            self.win.cancel_update()


# ── Window ───────────────────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    def __init__(self, paths: core.Paths | None = None, use_nav: bool = True, check_updates: bool | None = None):
        super().__init__()
        self.setObjectName("main")
        self.paths = paths or core.Paths.default()
        self.library = core.Library(self.paths)  # bookkeeping only (ids, launch scripts, cleanup)
        self.installer_dirs: list[Path] | None = None  # None = Downloads, Desktop, SD/USB
        self.thread: InstallThread | None = None
        self.job: core.Installer | None = None
        self.pending: core.PendingInstall | None = None
        self.current_installer: Path | None = None
        self.current_name = ""  # the name it's installed under ("" = guessed from the file name)
        self.update_info: updater.Update | None = None
        self.update_dismissed = False
        self.update_check: UpdateCheckThread | None = None
        self.update_thread: UpdateDownloadThread | None = None
        self.workers: set[QThread] = set()

        self.setWindowTitle("Deckhand")
        self.resize(1280, 800)
        self.setAcceptDrops(True)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._rail())
        main = QWidget()
        outer.addWidget(main, 1)
        v = QVBoxLayout(main)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        self.hint_bar = HintBar()
        v.addWidget(self.hint_bar)
        self.toast = Toast(self)  # on the window itself, so it stays visible over sheets

        self.home = HomePage(self)
        self.browser = BrowserPage(self)
        self.progress = InstallPage(self)
        self.pick = PickPage(self)
        self.done = DonePage(self)
        self.updating = UpdatePage(self)
        self.installed = InstalledPage(self)
        self.stores = StoresPage(self)
        self.streaming = StreamingPage(self)
        self.addons = AddonsPage(self)
        for p in (self.home, self.browser, self.progress, self.pick, self.done, self.updating, self.installed,
                  self.stores, self.streaming, self.addons):
            self.stack.addWidget(p)

        for lst in self.findChildren(QListWidget):
            lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            lst.setWordWrap(True)
        self.nav = Nav(QApplication.instance(), self.on_action, busy=lambda: self.thread is not None) \
            if use_nav else None
        # A bound method (not a lambda): Qt disconnects it automatically when the window goes away.
        QApplication.instance().focusChanged.connect(self._focus_changed)
        self.refresh_launchers()
        ensure_app_icon()
        self.sync_steam_ids()
        self.add_logos()
        self.go(self.home)
        dupes = core.find_duplicate_shortcuts(None, self.paths.launchers)
        if dupes:
            self.flash(f"Steam has {len(dupes)} duplicate shortcut{'s' * (len(dupes) != 1)} — "
                       "☰ Menu → Remove duplicate Steam shortcuts", ms=8000)
        QTimer.singleShot(0, self.check_leftovers)
        if check_updates is None:
            check_updates = updater.self_path() is not None and not os.environ.get("DECKHAND_NO_UPDATE_CHECK")
        if check_updates:
            self.check_for_updates(manual=False)

    # chrome
    SECTIONS = ("install", "stores", "stream", "addons", "installed")

    def _rail(self) -> QWidget:
        """Deckhand's sections down the left: tap them, or switch with L1/R1."""
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(206)
        v = QVBoxLayout(rail)
        v.setContentsMargins(0, 22, 0, 18)
        v.setSpacing(4)
        mark = QLabel(f'deckhand<span style="color:{theme.ACCENT}">.</span>')
        mark.setObjectName("wordmark")
        mark.setContentsMargins(24, 0, 0, 18)
        v.addWidget(mark)
        self.stations: dict[str, QPushButton] = {}
        for key, text, glyph in (("install", "Install", "download"), ("stores", "Stores", "bag"),
                                 ("stream", "Stream", "signal"), ("addons", "Add-ons", "plus"),
                                 ("installed", "Installed", "stack")):
            b = QPushButton(text)
            b.setObjectName("station")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setIcon(glyph_icon(glyph))
            b.setIconSize(QSize(26, 26))
            b.clicked.connect(lambda _c=False, k=key: self.switch_section(k))
            v.addWidget(b)
            self.stations[key] = b
        v.addStretch(1)
        menu = QPushButton("Menu")
        menu.setObjectName("station")
        menu.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        menu.setIcon(glyph_icon("menu"))
        menu.setIconSize(QSize(26, 26))
        menu.clicked.connect(lambda _c=False: self.open_menu())
        v.addWidget(menu)
        v.addSpacing(12)
        self.space = label("", "muted", wrap=False)
        self.space.setContentsMargins(24, 0, 0, 2)
        v.addWidget(self.space)
        for text in ("L1 / R1 to switch", f"Deckhand {__version__}"):
            foot = label(text, "muted", wrap=False)
            foot.setContentsMargins(24, 0, 0, 0)
            v.addWidget(foot)
        self.crumb = QLabel()  # (the page's own heading says where you are)
        return rail

    def section_of(self, page: QWidget) -> str | None:
        if page is self.stores:
            return "stores"
        if page is self.streaming:
            return "stream"
        if page is self.addons:
            return "addons"
        if page is self.installed:
            return "installed"
        if page is self.updating:
            return None
        return "install"

    def switch_section(self, key: str) -> None:
        current = self.section_of(self.stack.currentWidget())
        if key == current and self.stack.currentWidget() in (self.home, self.stores, self.streaming, self.addons,
                                                             self.installed):
            self._sync_stations()
            return
        if self.thread is not None or self.pending is not None or self.stack.currentWidget() is self.updating:
            self.flash("Finish what's running first")
            self._sync_stations()
            return
        {"install": self.go_home, "stores": self.show_stores, "stream": self.show_streaming,
         "addons": self.show_addons, "installed": self.show_installed}[key]()

    def _sync_stations(self) -> None:
        current = self.section_of(self.stack.currentWidget())
        for key, b in self.stations.items():
            b.setChecked(key == current)

    def _focus_changed(self, _old, _new) -> None:
        self.update_hints()

    def update_hints(self) -> None:
        page = self.stack.currentWidget()
        if isinstance(page, Page):
            note = "STEAM + X opens the keyboard" if core.in_game_mode() and \
                isinstance(QApplication.focusWidget(), QLineEdit) else ""
            self.hint_bar.set_hints(page.hints(), note)

    def refresh_space(self) -> None:
        self.space.setText(f"{core.human_size(core.free_space(self.paths.root))} free")

    def go(self, page: Page) -> None:
        self.stack.setCurrentWidget(page)
        self.crumb.setText(page.title or "Install a Windows program")
        self._sync_stations()
        self.refresh_space()
        page.enter()
        self.update_hints()

    def go_home(self) -> None:
        self.go(self.home)

    def nav_activate(self) -> None:
        if self.nav:
            self.nav.activate()

    def on_action(self, action: str) -> None:
        if Sheet.current is not None:
            if action == "b":
                Sheet.current.reject()
            return
        page = self.stack.currentWidget()
        if not isinstance(page, Page):
            return
        if action == "b":
            page.back()
        elif action == "x":
            page.x()
        elif action in ("start", "select"):
            self.open_menu()
        elif action in ("lb", "rb"):  # previous / next section, like tabs
            current = self.section_of(page) or "install"
            i = self.SECTIONS.index(current) + (-1 if action == "lb" else 1)
            self.switch_section(self.SECTIONS[i % len(self.SECTIONS)])

    def flash(self, text: str, ms: int = 2600) -> None:
        self.toast.show_message(text, ms)

    # ── install flow ─────────────────────────────────────────────────────

    def browse_installers(self) -> None:
        home = Path.home()
        places = [("Downloads", home / "Downloads"), ("Desktop", home / "Desktop"), ("Home", home)]
        places += [(p.name, p) for p in core.removable_media()]
        self.browser.open("Choose an installer", places, core.INSTALLER_SUFFIXES, self.confirm_install,
                          self.go_home)
        self.go(self.browser)

    def confirm_install(self, installer: Path) -> None:
        if self.thread is not None or self.busy:
            self.flash("Finish what's running first")
            return
        name = core.guess_name(installer)
        size_b = core.files_size(core.installer_files(installer))
        free_b = core.free_space(self.paths.root)
        notes = []
        before = next((a for a in self.library.load() if a.installer == str(installer)), None)
        if before is not None:
            notes.append(f"You already installed this as {before.name} ({ago(before.installed_at)}). "
                         "Installing again makes a second copy.")
        else:
            words = core._words(name)
            same = next((str(e.get("AppName", "")) for _c, e in core.steam_shortcuts()
                         if words and core._words(str(e.get("AppName", ""))) == words), None)
            if same:
                notes.append(f"Your Steam library already has “{same}” — it may already be installed.")
        if size_b and free_b < size_b * 2:
            notes.append("Free space looks tight — installed games usually take more room than their "
                         "installer.")
        text = (f"{breakable(installer)}\n\nInstaller: {core.human_size(size_b)}   ·   "
                f"Free space: {core.human_size(free_b)}\n\n")
        text += "\n".join(f"⚠  {n}" for n in notes) + ("\n\n" if notes else "")
        text += ("The installer opens next — click through it as usual. Deckhand then finds the program "
                 "and adds it to Steam.")
        if Sheet.ask(self, f"Install {name}?", text, ("Install", "Cancel")) == 0:
            self.start_install(installer)

    def start_install(self, installer: Path, allow_no_container: bool = False, name: str = "") -> None:
        if self.thread is not None:
            return
        installer = Path(installer)
        self.current_installer, self.current_name = installer, name
        self.progress.reset(installer, name)
        self.go(self.progress)
        t = InstallThread(installer, self.paths, allow_no_container, name)
        t.status.connect(lambda text: self.progress.on_status(text, t.job.stage))
        t.log.connect(self.progress.log.appendPlainText)
        t.done.connect(self.on_installed)
        t.failed.connect(self.on_failed)
        t.cancelled.connect(self.on_cancelled)
        t.finished.connect(self._thread_finished)
        self.thread, self.job = t, t.job
        t.start()

    def _thread_finished(self) -> None:
        if self.job:
            # The thread's signals die with it; route later updates straight to the UI.
            job = self.job
            job.status = lambda text: self.progress.on_status(text, job.stage)
            job._log_cb = self.progress.log.appendPlainText
        if self.thread:
            self.thread.deleteLater()
        self.thread = None

    def continue_install(self) -> None:
        if self.thread:
            self.thread.job.continue_now()

    def cancel_install(self) -> None:
        if not self.thread:
            return
        if Sheet.ask(self, "Cancel the install?", "The installer is closed and everything it installed so far "
                     "is removed.", ("Keep installing", "Cancel install"), danger=(1,)) == 1 and self.thread:
            self.progress.cancel_btn.setEnabled(False)
            self.progress.status.setText("Cancelling…")
            self.thread.job.cancel()

    def drop_download(self, installer: Path | None) -> None:
        """A store's installer that Deckhand downloaded itself is deleted once its install is over
        (the user never saw it, and the big ones take a few hundred MB)."""
        if installer is not None and installer.resolve().parent == self.paths.downloads.resolve():
            installer.unlink(missing_ok=True)

    def on_cancelled(self) -> None:
        self.progress.stop()
        self.drop_download(self.current_installer)
        self.go_home()
        self.flash("Install cancelled")

    def on_failed(self, message: str) -> None:
        self.progress.stop()
        self.go_home()
        if not message.startswith("NO_CONTAINER:"):  # (that one can be continued with the same file)
            self.drop_download(self.current_installer)
        if message == "NO_RUNTIME":
            if Sheet.ask(self, "Proton is needed", "Deckhand uses Proton to run Windows installers. Steam "
                         "can download it for you — try again when it's done.", ("Install Proton", "Close")) == 0:
                QDesktopServices.openUrl(QUrl(f"steam://install/{core.PROTON_EXPERIMENTAL_APPID}"))
            return
        if message.startswith("NO_CONTAINER:"):
            appid = message.split(":", 1)[1]
            choice = Sheet.ask(self, "One more Steam download needed",
                               "Proton runs inside the Steam Linux Runtime, which isn't installed yet. Without "
                               "it, installers often can't download anything.\n\nSteam can install it (a few "
                               "hundred MB). Run the install again when it's done.",
                               ("Install it", "Continue without it", "Cancel"))
            if choice == 1 and self.current_installer:
                self.start_install(self.current_installer, allow_no_container=True, name=self.current_name)
                return
            self.drop_download(self.current_installer)
            if choice == 0:
                QDesktopServices.openUrl(QUrl(f"steam://install/{appid}"))
            return
        head, _, rest = message.partition("\n\n")
        Sheet.ask(self, "Install failed", head, ("Close",), detail=rest)

    def on_installed(self, pending: core.PendingInstall) -> None:
        self.pending = pending
        cands = pending.candidates
        if core.is_confident(cands, pending.installer):
            self.finish_install(cands[0].exe, core.best_name(pending, cands[0]))
            return
        self.progress.stop()
        self.pick.load(pending)
        self.go(self.pick)

    def use_portable(self) -> None:
        if not self.pending:
            return
        pending = self.pending
        whole = False
        offer = core.portable_folder(pending)
        if offer is not None:
            folder, size = offer
            choice = Sheet.ask(self, "Copy its folder too?",
                               f"“{folder.name}” ({core.human_size(size)}) has other files next to "
                               f"{pending.installer.name}. Portable programs usually need them.",
                               ("Copy the folder", "Just the file", "Cancel"))
            if choice not in (0, 1):
                return
            whole = choice == 0
        # Copying can take a while: it happens in the background.
        self.finish_install(lambda: core.adopt_portable(pending, whole), self.pick.name.text(),
                            icon_from=pending.installer)

    def browse_program_for_pending(self) -> None:
        if not self.pending:
            return
        drive_c = self.pending.pfx / "drive_c"
        places = [("C: drive", drive_c), ("Home", Path.home())]
        self.browser.open("Choose the program", places, (".exe",),
                          lambda p: self.finish_install(p, self.pick.name.text()), lambda: self.go(self.pick))
        self.go(self.browser)

    def discard_pending(self) -> None:
        if self.pending:
            if self.job:
                self.job.close()
            shutil.rmtree(self.pending.compat_dir, ignore_errors=True)
            self.drop_download(self.pending.installer)
            self.pending = None
        self.go_home()

    def finish_install(self, exe: Path | Callable[[], Path], name: str, icon_from: Path | None = None) -> None:
        """Finish in the background. `exe` may be a function producing it (e.g. copying a portable
        program into place first)."""
        pending, self.pending = self.pending, None
        if pending is None or self.job is None:
            return
        icon_img = artwork.load_exe_icon(icon_from or exe)
        icon_path = artwork.save_icon(icon_img, self.paths.icons / f"{pending.id}.png") if icon_img else ""
        job = self.job
        if self.stack.currentWidget() is not self.progress:
            self.go(self.progress)
        self.progress.on_status("Adding to Steam…", "steam")

        def run(status: Callable[[str], None]) -> core.App:
            job.status, job._log_cb = status, lambda _line: None
            program = exe() if callable(exe) else exe
            return job.finish(pending, program, name, icon=icon_path)

        def finished(app: core.App) -> None:
            self.progress.stop()
            self._write_art(app, icon_img)
            self.drop_download(pending.installer)
            self.done.load(app)
            self.go(self.done)

        def failed(message: str) -> None:
            self.progress.stop()
            self.drop_download(pending.installer)
            Sheet.ask(self, "Couldn't finish", message, ("Close",))
            self.go_home()

        self.run_worker(run, finished, failed, status=lambda text: self.progress.on_status(text, "steam"))

    @property
    def busy(self) -> bool:
        return bool(self.workers)

    def busy_with(self, kind: str) -> bool:
        if any(getattr(w, "kind", "") == kind for w in self.workers):
            self.flash("Still working on the last one…")
            return True
        return False

    def run_worker(self, fn, on_done, on_failed, status=None, kind: str = "", progress=None) -> None:
        w = Worker(fn)
        w.kind = kind
        w.done.connect(on_done)
        w.failed.connect(on_failed)
        if status:
            w.status.connect(status)
        if progress:
            w.progress.connect(progress)
        w.finished.connect(w.deleteLater)
        w.finished.connect(lambda: self.workers.discard(w))
        self.workers.add(w)
        w.start()

    def _write_art(self, app: core.App, icon_img: QImage | None) -> None:
        artwork.remove_files(app.artwork)
        app.artwork = artwork.write_steam_artwork(app.steam_appid, app.name, icon_img, core.steam_grid_dirs())
        self.library.upsert(app)
        if self.sgdb_key():
            self.fetch_art([app], quiet=True)

    # ── SteamGridDB ──────────────────────────────────────────────────

    def sgdb_key(self) -> str:
        return str(self.paths.state().get("steamgriddb_key") or "")

    def fetch_art(self, apps: list[core.App], quiet: bool = False) -> None:
        """Replace Deckhand's drawn art with SteamGridDB's, where it has some, in the background."""
        key = self.sgdb_key()
        apps = [a for a in apps if a.steam_appid]
        if not key or not apps:
            return

        def run(status) -> dict[str, dict[str, bytes]]:
            found = {}
            for i, a in enumerate(apps, 1):
                if not quiet:
                    status(f"Looking up art on SteamGridDB…  {i} of {len(apps)}: {a.name}")
                found[a.id] = steamgriddb.art(a.name, key)
            return found

        def finished(found: dict[str, dict[str, bytes]]) -> None:
            got = 0
            for app in self.library.load():  # (as they are now: one may have been uninstalled meanwhile)
                images = found.get(app.id)
                if not images:
                    continue
                artwork.remove_files(app.artwork)
                app.artwork = artwork.write_steam_artwork(app.steam_appid, app.name, artwork.load_icon(app.icon),
                                                          core.steam_grid_dirs(), images)
                self.library.upsert(app)
                got += 1
            if not quiet:
                self.flash(f"Art from SteamGridDB for {got} of {len(apps)}" if got else
                           "SteamGridDB has no art for these", ms=5000)

        def failed(message: str) -> None:
            if not quiet:
                Sheet.ask(self, "SteamGridDB", message, ("Close",))

        self.run_worker(run, finished, failed, status=None if quiet else (lambda t: self.flash(t, ms=60_000)),
                        kind="art")

    def steamgriddb_menu(self) -> None:
        key = self.sgdb_key()
        if key:
            choice = Sheet.ask(self, "SteamGridDB art", "Deckhand uses library art from SteamGridDB for what it adds to "
                               f"Steam (with the API key ending in …{key[-4:]}). Art you added yourself is never "
                               "replaced.", ("Get art for everything now", "Change the API key",
                                             "Stop using SteamGridDB", "Close"), primary=3)
            if choice == 0:
                if not self.busy_with("art"):
                    self.fetch_art(self.library.load())
            elif choice == 1:
                self.ask_sgdb_key()
            elif choice == 2:
                self.paths.remember(steamgriddb_key="")
                self.flash("Deckhand draws its own art again (the art already in Steam stays)")
            return
        self.ask_sgdb_key()

    def ask_sgdb_key(self) -> None:
        choice, value = Sheet.ask_text(
            self, "SteamGridDB art", "SteamGridDB (steamgriddb.com) has library art made by the community for "
            "most games and apps. Deckhand can use it instead of the art it draws itself.\n\nIt needs a free API "
            "key: sign in at steamgriddb.com (with your Steam account), open Preferences → API and generate one, "
            "then type or paste it here." + ("  (STEAM + X opens the keyboard.)" if core.in_game_mode() else ""),
            ("Save", "Cancel"), placeholder="API key")
        key = value.strip()
        if choice != 0 or not key:
            return

        def saved(_result) -> None:
            self.paths.remember(steamgriddb_key=key)
            self.flash("SteamGridDB key saved — getting art…")
            self.fetch_art(self.library.load())

        self.run_worker(lambda _s: steamgriddb.check_key(key), saved,
                        lambda m: Sheet.ask(self, "SteamGridDB", m, ("Close",)), kind="art")

    def add_to_steam(self, app: core.App) -> None:
        """Put an installed program (back) into Steam — e.g. if a Steam restart dropped it."""
        self.flash(f"Adding {app.name} to Steam…")

        def run(_status) -> core.App:
            core.add_to_steam(app)
            return app

        def finished(a: core.App) -> None:
            how = a.steam_added
            if how in ("live", "file", "requested"):
                self._write_art(a, artwork.load_icon(a.icon))
            else:
                self.library.upsert(a)
            if how == "live":
                self.flash(f"{a.name} is in your Steam library")
            elif how == "file":
                self.flash(f"{a.name} will be in your Steam library when Steam starts")
            elif how == "requested":
                self.flash(f"Sent {a.name} to Steam — look in your library under Non-Steam", ms=5000)
            elif how == "unavailable":
                Sheet.ask(self, "Couldn't reach Steam", "Steam didn't respond, so nothing was added.\n\n"
                          + close_steam_first(), ("Close",))
            else:
                Sheet.ask(self, "Couldn't add to Steam", "No Steam account was found on this Deck.", ("Close",))
            if self.stack.currentWidget() is self.installed:
                self.installed.enter()
            elif self.stack.currentWidget() is self.stores:
                self.stores.refresh()

        if self.busy_with("steam"):
            return
        self.run_worker(run, finished, lambda m: Sheet.ask(self, "Couldn't add to Steam", m, ("Close",)),
                        kind="steam")

    def show_addons(self) -> None:
        if self.thread is not None:
            self.flash("Finish the install first")
            return
        self.go(self.addons)

    def show_streaming(self) -> None:
        if self.thread is not None:
            self.flash("Finish the install first")
            return
        self.go(self.streaming)

    def show_stores(self) -> None:
        if self.thread is not None:
            self.flash("Finish the install first")
            return
        self.go(self.stores)

    def _say_added(self, app: core.App, retry: str) -> None:
        """Where an app that was just set up stands in Steam. `retry`: where to add it again from."""
        msg = {"live": f"{app.name} is in your Steam library",
               "file": f"{app.name} will be in your Steam library when Steam starts",
               "requested": f"Sent {app.name} to Steam — look in your library under Non-Steam"}
        if app.steam_added in msg:
            self.flash(msg[app.steam_added], ms=5000)
        elif app.steam_added == "unavailable":
            Sheet.ask(self, "Couldn't reach Steam", "Steam didn't respond, so nothing was added.\n\n"
                      + close_steam_first(retry), ("Close",))
        else:
            Sheet.ask(self, "Couldn't add to Steam", "No Steam account was found on this Deck.", ("Close",))

    def set_up_store(self, s: stores.Store) -> None:
        """A store's Linux app (Heroic, itch): from Flathub if needed, into Steam."""
        page = self.stores
        page.status.setText(f"Setting up {s.name}…")

        def finished(app: core.App) -> None:
            page.status.setText("")
            page.show_progress(None)
            page.installed = None  # re-read: something may have been installed
            self._write_art(app, artwork.load_icon(app.icon))
            self._say_added(app, f"Stores → {s.name}")
            if self.stack.currentWidget() is page:
                page.refresh()

        self.run_worker(lambda status: stores.set_up(s, self.paths, status), finished, page._failed(s.name),
                        status=page.status.setText, kind="store", progress=page.show_progress)

    def set_up_stream(self, svc: streaming.Service, better_xcloud: bool | None = None) -> None:
        page = self.streaming
        page.status.setText(f"Setting up {svc.name}…")

        def finished(app: core.App) -> None:
            page.status.setText("")
            page.show_progress(None)
            page.installed = None  # re-read: something may have been installed
            self._write_art(app, artwork.load_icon(app.icon))
            self._say_added(app, "☰ Menu → Game streaming")
            if self.stack.currentWidget() is page:
                page.refresh()

        def failed(message: str) -> None:
            page.status.setText("")
            page.show_progress(None)
            head, _, rest = message.partition("\n\n")
            Sheet.ask(self, f"Couldn't set up {svc.name}", head, ("Close",), detail=rest)

        self.run_worker(lambda status: streaming.set_up(svc, self.paths, status, better_xcloud=better_xcloud),
                        finished, failed,
                        status=page.status.setText, kind="stream", progress=page.show_progress)

    def check_leftovers(self) -> None:
        """Offer to delete prefixes left by interrupted installs (sized in the background)."""
        kept = set(self.paths.state().get("kept_leftovers", []))
        dirs = [d for d in core.orphan_prefixes(self.paths) if d.name not in kept and not core.prefix_in_use(d)]
        if not dirs or self.busy_with_quietly("leftovers"):
            return

        def finished(sizes: list[tuple[Path, int]]) -> None:
            # An install may have started meanwhile: never offer its prefix.
            busy = {self.pending.compat_dir.resolve()} if self.pending else set()
            if self.thread is not None or Sheet.current is not None:
                return
            left = [(d, n) for d, n in sizes if d.exists() and d.resolve() not in busy
                    and d in core.orphan_prefixes(self.paths)]
            if not left:
                return
            total = sum(n for _d, n in left)
            names = "\n".join(f"•  {d.name}  ({core.human_size(n)})" for d, n in left[:8])
            if len(left) > 8:
                names += f"\n…and {len(left) - 8} more"
            choice = Sheet.ask(self, "Unfinished installs",
                               f"These are left over from installs that didn't finish (for example, Deckhand "
                               f"was closed during the install). Nothing in Steam uses them.\n\n{names}\n\n"
                               f"Delete them to free {core.human_size(total)}, or look at them one by one — an "
                               "install that got far enough can still be finished.",
                               ("Delete all", "Keep", "Look at them"), primary=2, danger=(0,))
            if choice == 0:
                for d, _n in left:
                    shutil.rmtree(d, ignore_errors=True)
                self.flash(f"Freed {core.human_size(total)}")
                self.refresh_space()
            elif choice == 1:
                self.paths.remember(kept_leftovers=sorted(kept | {d.name for d, _n in left}))
            elif choice == 2:
                self.paths.remember(kept_leftovers=sorted(kept | {d.name for d, _n in left}))  # listed there now
                self.show_installed()

        self.run_worker(lambda _s: [(d, core.dir_size(d)) for d in dirs], finished, lambda _m: None,
                        kind="leftovers")

    def leftover_chosen(self, compat: Path, size: int | None) -> None:
        """An unfinished install: finish it (find what it installed) or delete it."""
        if self.thread is not None or self.busy:
            self.flash("Finish what's running first")
            return
        name = core.load_install_info(compat).get("name") or compat.name
        if core.prefix_in_use(compat):
            Sheet.ask(self, name, "Its installer is still running (Deckhand was closed while it ran). Let it "
                      "finish, then come back here to add the program to Steam.", ("Close",))
            return
        freed = f" and free {core.human_size(size)}" if size else ""
        choice = Sheet.ask(self, name, "This install didn't finish: Deckhand was closed before the program "
                           f"was added to Steam. Finish setting it up, or delete it{freed}.",
                           ("Finish setup", "Delete", "Cancel"), primary=0, danger=(1,))
        if choice == 1:
            shutil.rmtree(compat, ignore_errors=True)
            self.flash(f"Deleted — freed {core.human_size(size)}" if size else "Deleted")
            self.refresh_space()
            self.installed.enter()
        elif choice == 0:
            pending = core.resume_install(self.paths, compat)
            if pending is None:
                if Sheet.ask(self, name, "No program was installed in it. Delete it?", ("Delete", "Keep"),
                             danger=(0,)) == 0:
                    shutil.rmtree(compat, ignore_errors=True)
                    self.refresh_space()
                    self.installed.enter()
                return
            self.job = core.Installer(pending.installer, self.paths, runtime=pending.runtime)
            self.pending = pending
            self.progress.reset(pending.installer)
            self.on_installed(pending)

    def busy_with_quietly(self, kind: str) -> bool:
        return any(getattr(w, "kind", "") == kind for w in self.workers)

    def sync_steam_ids(self) -> None:
        """Steam may give a shortcut an id of its own, or save one we handed it later: follow it, so
        the program's artwork is filed under the id Steam uses."""
        entries = core.steam_shortcuts()
        for app in self.library.load():
            if core.sync_steam_appid(app, entries):
                self._write_art(app, artwork.load_icon(app.icon))

    def add_logos(self) -> None:
        """Streaming services and store apps added before Deckhand had their logos: use the logo for
        their Steam artwork and app menu entry. (The shortcut's own small icon stays until it's added
        to Steam again: Steam's list can't be changed behind a running Steam.)"""
        for app in self.library.load():
            if app.kind in ("stream", "store", "addon") and not app.icon and core.use_logo(app, self.paths,
                                                                                 app.id.split("-", 1)[1]):
                core.write_desktop_entry(app)
                self._write_art(app, artwork.load_icon(app.icon))

    def remove_duplicates(self) -> None:
        roots = core.steam_roots()
        names = core.find_duplicate_shortcuts(roots, self.paths.launchers)
        running = core.steam_is_running()
        if not names:
            note = " (Steam is open: anything added since it last saved its list isn't in it yet.)" if running else ""
            Sheet.ask(self, "No duplicates", "Steam's list of non-Steam shortcuts has no duplicates." + note,
                      ("Close",))
            return
        found = f"Steam's list has {len(names)} extra cop{'y' if len(names) == 1 else 'ies'} of shortcuts:\n" \
                + count_names(names)
        if running:
            Sheet.ask(self, "Close Steam first", found + "\n\nSteam is open, and would put them back the next time "
                      "it saves its list. To remove them all at once: in Desktop Mode, exit Steam (Steam menu → "
                      "Exit), open Deckhand from the app menu and pick ☰ Menu → Remove duplicate Steam "
                      "shortcuts again.\n\nOr one at a time: " + REMOVE_IN_STEAM, ("Close",))
            return
        if Sheet.ask(self, "Remove duplicate shortcuts?", found + "\n\nOne of each stays. A backup of Steam's list "
                     "is kept next to it (shortcuts.vdf.before-dedupe).", ("Remove duplicates", "Cancel")) != 0:
            return
        n = core.remove_duplicate_shortcuts(roots, self.paths.launchers,
                                            [a.steam_appid for a in self.library.load()])
        self.sync_steam_ids()
        self.flash(f"Removed {n} duplicate shortcut{'s' * (n != 1)}")

    def refresh_launchers(self) -> None:
        """Rewrite launch scripts so programs installed by older versions get current fixes."""
        roots = core.steam_roots()
        for app in self.library.load():
            if app.kind == "program" and app.launcher and app.prefix and Path(app.prefix).is_dir():
                try:
                    core.write_launcher(app, self.paths, roots[0] if roots else None, roots)
                except OSError:
                    pass

    # ── menu ─────────────────────────────────────────────────────────────

    def open_menu(self) -> None:
        if Sheet.current is not None:
            return
        def look_again() -> None:
            self.go_home()
            self.flash("Checked Downloads, Desktop and SD cards")

        def about() -> None:
            Sheet.ask(self, f"Deckhand {__version__}",
                      "Installs Windows programs and games on Steam Deck and adds them to your Steam library.\n\n"
                      f"Installed programs: {self.paths.prefixes}\nInstall logs: {self.paths.logs}", ("Close",))

        items = [
            ("Check for updates", lambda: self.check_for_updates(manual=True)),
            ("Add Deckhand to Steam", self.add_self_to_steam),
            ("Free up space", self.free_up_space),
            ("Remove duplicate Steam shortcuts", self.remove_duplicates),
            ("SteamGridDB art", self.steamgriddb_menu),
            ("Look for installers again", look_again),
            ("About", about),
            ("Quit Deckhand", self.close),
            ("Close", None),
        ]
        choice = Sheet.ask(self, "Menu", "", tuple(t for t, _ in items), primary=len(items) - 1)
        if 0 <= choice < len(items) and items[choice][1] is not None:
            items[choice][1]()

    # ── uninstalling ─────────────────────────────────────────────────────

    def show_installed(self) -> None:
        if self.thread is not None:
            self.flash("Finish the install first")
            return
        self.go(self.installed)

    def uninstall(self, app: core.App, size: int | None = None) -> None:
        if app.kind in ("stream", "store", "addon"):
            stays = {"stream": "The browser or app it uses stays installed.",
                     "store": f"{app.name} itself stays installed, and so do the games you installed with it.",
                     "addon": f"{app.name} itself stays installed."}[app.kind]
            if Sheet.ask(self, f"Remove {app.name}?", f"Removes its Steam shortcut and artwork. {stays}",
                         ("Remove", "Keep"), primary=1, danger=(0,)) != 0:
                return

            def removed(left_in_steam: bool) -> None:
                self.flash(f"{app.name} removed")
                if self.stack.currentWidget() in (self.streaming, self.stores, self.addons):
                    self.stack.currentWidget().refresh()
                if left_in_steam:
                    Sheet.ask(self, f"{app.name} removed", "Its shortcut is still in your Steam library: "
                              "Deckhand doesn't change Steam's list while Steam is open.\n\n" + REMOVE_IN_STEAM,
                              ("Close",))

            self.run_worker(lambda _s: core.uninstall(app, self.paths), removed,
                            lambda m: Sheet.ask(self, "Couldn't remove", m, ("Close",)), kind="uninstall")
            return
        extra = core.safe_extra_dirs(app)
        parts = ["the program and anything saved inside its Windows folder (many games keep their saves "
                 "there)"]
        parts += [f"its folder {breakable(d)}" for d in extra]
        running = core.steam_is_running()
        if app.steam_appid and running:
            parts.append("its Steam artwork (Steam is open, so you'll remove the shortcut itself in Steam)")
        else:
            parts.append("its Steam shortcut and artwork" if app.steam_appid else "its shortcut")
        freed = f"This frees {core.human_size(size)}.\n\n" if size else ""
        text = freed + "Deletes " + "; ".join(parts) + "."
        has_saves = saves.has_saves(app)
        buttons = ("Uninstall", "Back up saves, then uninstall", "Keep") if has_saves else ("Uninstall", "Keep")
        choice = Sheet.ask(self, f"Uninstall {app.name}?", text, buttons, primary=len(buttons) - 1, danger=(0,))
        if choice not in range(len(buttons) - 1):
            return
        back_up = has_saves and choice == 1
        # Deleting a big game can take a while: do it in the background (the list shows it's going).
        self.flash(f"Uninstalling {app.name}…", ms=60_000)

        def run(_status) -> bool:
            if back_up:
                saves.backup(app, self.paths)
            return core.uninstall(app, self.paths)

        def finished(left_in_steam: bool) -> None:
            self.installed.sizes.pop(app.id, None)
            msg = f"{app.name} uninstalled"
            if size:
                msg += f" — freed {core.human_size(size)}"
            if back_up:
                msg += " (saves backed up)"
            self.flash(msg)
            self.refresh_space()
            if self.stack.currentWidget() is self.installed:
                if self.library.load() or core.orphan_prefixes(self.paths):
                    self.installed.enter()
                else:
                    self.go_home()
            elif self.stack.currentWidget() is self.stores:
                self.stores.refresh()
            if left_in_steam:
                Sheet.ask(self, msg, "Its shortcut is still in your Steam library: Deckhand doesn't change "
                          "Steam's list while Steam is open (Steam would undo it or duplicate it).\n\n"
                          + REMOVE_IN_STEAM, ("Close",))

        self.run_worker(run, finished, lambda m: Sheet.ask(self, "Couldn't uninstall", m, ("Close",)),
                        kind="uninstall")

    # ── free up space ────────────────────────────────────────────────────

    def free_up_space(self) -> None:
        # (an install, or a download for one, may be using what's in the downloads folder)
        if self.thread is not None or self.pending is not None or \
                any(self.busy_with_quietly(k) for k in ("store", "fix", "addon", "cleanup")):
            self.flash("Finish what's running first")
            return
        self.flash("Looking for leftovers…", ms=60_000)

        def run(_status) -> list[cleanup.Leftover]:
            items = cleanup.find(paths=self.paths)
            cleanup.name_games([i for i in items if i.kind == "compatdata"])
            return items

        self.run_worker(run, self._offer_cleanup, lambda m: Sheet.ask(self, "Free up space", m, ("Close",)),
                        kind="cleanup")

    def _offer_cleanup(self, items: list[cleanup.Leftover]) -> None:
        self.toast.hide()
        safe = [i for i in items if i.kind in ("shadercache", "download")]
        setups = [i for i in items if i.kind == "compatdata"]

        def total(xs) -> str:
            return core.human_size(sum(x.size for x in xs))

        def games(xs) -> str:
            return f"{len(xs)} game that isn't" if len(xs) == 1 else f"{len(xs)} games that aren't"

        lines = []
        caches = [i for i in safe if i.kind == "shadercache"]
        downloads = [i for i in safe if i.kind == "download"]
        if caches:
            lines.append(f"•  Shader caches of {games(caches)} installed: {total(caches)}. Safe to delete: Steam "
                         "builds them again if you reinstall a game.")
        if downloads:
            lines.append(f"•  Deckhand's leftover downloads: {total(downloads)}.")
        if setups:
            biggest = ", ".join(f"{i.name or f'app {i.appid}'} ({core.human_size(i.size)})" for i in setups[:4])
            lines.append(f"•  Windows setups (compatdata) of {games(setups)} installed: {total(setups)} — "
                         f"{biggest}{', …' if len(setups) > 4 else ''}. Games without Steam Cloud keep their saves "
                         "in these.")
        unfinished = core.orphan_prefixes(self.paths)
        if unfinished:
            lines.append(f"•  {len(unfinished)} unfinished install{'s' * (len(unfinished) != 1)}: see Installed "
                         "programs.")
        if not safe and not setups:
            Sheet.ask(self, "Free up space", "Nothing left over by uninstalled games was found." +
                      ("\n\n" + lines[0] if lines else ""), ("Close",))
            return
        actions = []
        if safe:
            actions.append((f"Delete the safe ones ({total(safe)})", safe))
        if setups:
            actions.append((f"Delete all of it ({total(safe + setups)})", safe + setups))
        actions.append(("Close", None))
        choice = Sheet.ask(self, "Free up space", "\n".join(lines), tuple(t for t, _x in actions),
                           primary=len(actions) - 1, danger=(len(actions) - 2,) if setups else ())
        if not 0 <= choice < len(actions) or actions[choice][1] is None:
            return
        chosen = actions[choice][1]

        def done(freed: int) -> None:
            self.flash(f"Freed {core.human_size(freed)}", ms=5000)
            self.refresh_space()

        self.run_worker(lambda _s: cleanup.delete(chosen), done,
                        lambda m: Sheet.ask(self, "Couldn't delete everything", m, ("Close",)), kind="cleanup")

    # ── save backups ─────────────────────────────────────────────────────

    def back_up_saves(self, app: core.App) -> None:
        if self.busy_with("saves"):
            return
        self.flash(f"Backing up {app.name}'s saves…", ms=60_000)

        def finished(archive: Path | None) -> None:
            if archive is None:
                self.flash(f"No saves found in {app.name}'s Windows folder", ms=5000)
            else:
                self.flash(f"Saves backed up ({core.human_size(archive.stat().st_size)})", ms=5000)

        self.run_worker(lambda _s: saves.backup(app, self.paths), finished,
                        lambda m: Sheet.ask(self, "Couldn't back up the saves", m, ("Close",)), kind="saves")

    def restore_saves(self, app: core.App, then: Callable[[], None] = lambda: None) -> None:
        """Pick one of the program's backups and put its saves back."""
        found = saves.backups(self.paths, app.name)[:4]
        if not found or self.busy_with("saves"):
            return
        if core.prefix_in_use(Path(app.prefix)):
            Sheet.ask(self, app.name, f"{app.name} is running. Close it first (STEAM → Exit game).", ("Close",))
            return
        labels = [f"{time.strftime('%b %d, %Y  %H:%M', time.localtime(saves.created(b)))}  ·  "
                  f"{core.human_size(b.stat().st_size)}" for b in found]
        choice = Sheet.ask(self, f"Restore {app.name}'s saves?", "Puts the saves from a backup back into its Windows "
                           "folder, replacing the ones with the same names.", (*labels, "Cancel"),
                           primary=len(labels))
        if not 0 <= choice < len(found):
            return

        def finished(count: int) -> None:
            self.flash(f"Restored {count} file{'s' * (count != 1)} — play it from Steam", ms=5000)
            then()

        self.run_worker(lambda _s: saves.restore(app, found[choice]), finished,
                        lambda m: Sheet.ask(self, "Couldn't restore the saves", m, ("Close",)), kind="saves")

    # ── updates ──────────────────────────────────────────────────────────

    def check_for_updates(self, manual: bool) -> None:
        if manual and updater.self_path() is None:
            Sheet.ask(self, "Updates", updater.why_no_self_update(), ("Close",))
            return
        if self.update_check is not None:
            return
        if manual:
            self.flash("Checking for updates…")
        t = UpdateCheckThread()
        t.result.connect(lambda u, online: self.on_update_checked(u, manual, online))
        t.finished.connect(t.deleteLater)
        t.finished.connect(lambda: setattr(self, "update_check", None))
        self.update_check = t
        t.start()

    def on_update_checked(self, update: updater.Update | None, manual: bool, online: bool = True) -> None:
        if update is not None:
            self.update_info = update
            if manual or not self.update_dismissed:
                self.home.show_banner(update)
            if manual and Sheet.ask(self, f"Deckhand {update.version} is available",
                                    (update.notes + "\n\n" if update.notes else "") + f"You have {__version__}.",
                                    ("Update now", "Later")) == 0:
                self.start_update()
        elif manual and not online:
            Sheet.ask(self, "Couldn't check for updates", "GitHub couldn't be reached. Check that the Deck is "
                      "online and try again.", ("Close",))
        elif manual:
            Sheet.ask(self, "You're up to date", f"Deckhand {__version__} is the latest version.", ("Close",))

    def start_update(self) -> None:
        target = updater.self_path()
        if self.update_info is None or target is None or self.update_thread is not None:
            return
        if self.thread is not None:
            Sheet.ask(self, "Finish the install first", "Deckhand can update once the current install is "
                      "done.", ("Close",))
            return
        self.updating.reset(self.update_info)
        self.go(self.updating)
        t = UpdateDownloadThread(self.update_info, target)
        t.progress.connect(self.updating.on_progress)
        t.done.connect(self.on_update_installed)
        t.failed.connect(self.on_update_failed)
        t.finished.connect(t.deleteLater)
        t.finished.connect(lambda: setattr(self, "update_thread", None))
        self.update_thread = t
        t.start()

    def cancel_update(self) -> None:
        if self.update_thread is not None:
            self.updating.cancel_btn.setEnabled(False)
            self.updating.status.setText("Cancelling…")
            self.update_thread.requestInterruption()
        else:
            self.go_home()

    def on_update_installed(self) -> None:
        self.home.banner.hide()
        self.updating.on_done()

    def on_update_failed(self, message: str) -> None:
        self.go_home()
        if message:
            Sheet.ask(self, "Update failed", f"{message}\n\nDeckhand wasn't changed.", ("Close",))
        else:
            self.flash("Update cancelled")

    def restart_after_update(self) -> None:
        target = updater.self_path()
        if target is None:
            return
        if self.nav:
            self.nav.stop()
        updater.restart(target)

    def add_self_to_steam(self) -> None:
        exe = Path(sys.executable if getattr(sys, "frozen", False) else Path.home() / ".local/bin/deckhand")
        if not exe.exists():
            Sheet.ask(self, "Add to Steam", "Install Deckhand with get.sh first.", ("Close",))
            return
        if core.find_shortcut(str(exe)):
            Sheet.ask(self, "Add to Steam", "Deckhand is already in your Steam library.", ("Close",))
            return
        sent = self.paths.state().get("self_steam_requested_at", 0)
        if sent and core.steam_is_running() and core.shortcuts_saved_at() < sent:
            if Sheet.ask(self, "Add to Steam", f"Deckhand was sent to Steam {ago(sent)}. Steam hasn't saved its "
                         "list of shortcuts since, so Deckhand can't check it yet — look in your library under "
                         "Non-Steam.\n\nOnly send it again if it's not there: otherwise you'll get a duplicate.",
                         ("Close", "Send again")) != 1:
                return
        if self.busy_with("steam"):
            return
        self.flash("Adding Deckhand to Steam…")
        started = time.time()

        def finished(result) -> None:
            appid, how = result
            if how == "requested":
                self.paths.remember(self_steam_requested_at=started)
            if how in ("live", "file", "requested"):
                artwork.write_steam_artwork(appid, "Deckhand", artwork.logo_image(512), core.steam_grid_dirs())
            msg = {"live": "Done — Deckhand is in your Steam library.",
                   "file": "Done — Deckhand will be in your Steam library when Steam starts.",
                   "requested": "Sent to Steam — look in your library under Non-Steam.",
                   "unavailable": "Steam didn't respond, so nothing was added.\n\n"
                                  + close_steam_first("☰ Menu → Add Deckhand to Steam"),
                   }.get(how, "No Steam account found on this device.")
            Sheet.ask(self, "Add to Steam", msg, ("Close",))

        icon = artwork.install_app_icon() or ""
        self.run_worker(lambda _s: core.add_shortcut("Deckhand", str(exe), str(exe.parent), str(icon)), finished,
                        lambda m: Sheet.ask(self, "Couldn't add to Steam", m, ("Close",)), kind="steam")

    def open_files(self, files: list[str]) -> None:
        """Another launch (e.g. Open With in Desktop Mode) handed us an installer."""
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if files and self.thread is None and Sheet.current is None:
            self.confirm_install(Path(files[0]))

    # ── drag & drop, closing ─────────────────────────────────────────────

    def _dropped_installer(self, event) -> Path | None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if len(urls) == 1 and urls[0].isLocalFile():
            p = Path(urls[0].toLocalFile())
            if p.suffix.lower() in core.INSTALLER_SUFFIXES:
                return p
        return None

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self.stack.currentWidget() is self.home and self._dropped_installer(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        p = self._dropped_installer(event)
        if p:
            self.confirm_install(p)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.thread is not None:
            if Sheet.ask(self, "Quit?", "An install is running. Cancel it and quit?", ("Keep installing", "Quit"),
                         danger=(1,)) != 1:
                event.ignore()
                return
            self.thread.job.cancel()
            self.thread.wait(15000)
        elif self.pending is not None:
            if Sheet.ask(self, "Quit?", "The program is installed but not in Steam yet. You can finish later "
                         "from Installed programs.", ("Stay", "Quit"), primary=0) != 1:
                event.ignore()
                return
        # Let background jobs (adding to Steam, uninstalling, updating) finish: a QThread destroyed
        # mid-run takes the whole app down with it.
        if self.update_thread is not None:
            self.update_thread.requestInterruption()
        for t in [*self.workers, self.update_thread, self.update_check, self.installed.thread]:
            if t is not None:
                try:
                    t.wait(20000)
                except RuntimeError:  # already deleted
                    pass
        if self.nav:
            self.nav.stop()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = argv[1:]
    if "--version" in args:
        print(f"Deckhand {__version__}")
        return 0
    if "-h" in args or "--help" in args:
        print("usage: deckhand [INSTALLER.exe|.msi]\n"
              "       deckhand --update\n\n"
              "Opens Deckhand. Given an installer, asks to install it right away.\n"
              "--update downloads and installs the newest Deckhand.")
        return 0
    if "--update" in args:
        return updater.cli_update(__version__)
    if "--install-icon" in args:  # used by get.sh: put the app icon where the desktop finds it
        from PyQt6.QtGui import QGuiApplication

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        gui = QGuiApplication(argv[:1])  # painting needs one
        ok = gui is not None and artwork.install_app_icon() is not None
        return 0 if ok else 1
    app = QApplication(argv[:1])
    app.setApplicationName("Deckhand")
    app.setDesktopFileName("deckhand")
    app.setWindowIcon(app_icon())
    files = [str(Path(a).resolve()) for a in args if not a.startswith("-")]
    if SingleInstance.hand_off(files):
        print("Deckhand is already open — passed it on.")
        return 0
    app.setStyle("Fusion")
    app.setStyleSheet(theme.STYLE)
    win = MainWindow()
    instance = SingleInstance()
    instance.listen(win.open_files)
    if core.in_game_mode() or os.environ.get("DECKHAND_FULLSCREEN"):
        win.showFullScreen()
    else:
        win.show()
    if files:
        QTimer.singleShot(0, lambda: win.confirm_install(Path(files[0])))
    return app.exec()


class SingleInstance:
    """One Deckhand at a time: a second launch hands its installer to the open window.

    Two windows installing at once would race on the same records and prefixes."""

    NAME = f"deckhand-{os.getuid()}"

    def __init__(self) -> None:
        self.server: QLocalServer | None = None
        self.pending: dict[QLocalSocket, bytes] = {}

    @classmethod
    def hand_off(cls, files: list[str], name: str | None = None) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(name or cls.NAME)
        if not sock.waitForConnected(500):
            return False
        sock.write(json.dumps(files).encode())
        sock.flush()
        sock.waitForBytesWritten(1000)
        sock.disconnectFromServer()
        return True

    def listen(self, on_files: Callable[[list[str]], None], name: str | None = None) -> None:
        name = name or self.NAME
        QLocalServer.removeServer(name)  # a stale socket from a crash
        self.server = QLocalServer()
        self.server.listen(name)

        def accept() -> None:
            while self.server.hasPendingConnections():
                sock = self.server.nextPendingConnection()
                self.pending[sock] = b""
                sock.readyRead.connect(lambda s=sock: self.pending.__setitem__(s, self.pending[s] + bytes(s.readAll())))
                sock.disconnected.connect(lambda s=sock: done(s))

        def done(sock: QLocalSocket) -> None:
            data = self.pending.pop(sock, b"") + bytes(sock.readAll())
            sock.deleteLater()
            try:
                files = [str(f) for f in json.loads(data.decode() or "[]")]
            except (ValueError, UnicodeDecodeError, TypeError):
                files = []
            on_files(files)

        self.server.newConnection.connect(accept)
