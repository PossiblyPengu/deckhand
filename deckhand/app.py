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

from PyQt6.QtCore import QSize, QThread, QTimer, QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QImage
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, addons, artwork, cleanup, core, saves, steamgriddb, stores, streaming, theme, updater
from .nav import Nav
from .pages import (AddonsPage, BrowserPage, DonePage, HomePage, InstallPage, InstalledPage,
                    PickPage, StartPage, StoresPage, StreamingPage, UpdatePage)
from .pages.common import (Page, REMOVE_IN_STEAM, ago, app_icon, close_steam_first,
                           count_names, ensure_app_icon, glyph_icon, logo_icon, select_row)
from .pages.threads import InstallThread, UpdateCheckThread, UpdateDownloadThread, Worker
from .widgets import CardDelegate, HintBar, Sheet, Toast, breakable, label

__all__ = ["MainWindow", "SingleInstance", "main", "app_icon", "ensure_app_icon", "logo_icon",
           "select_row"]


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

        self.start = StartPage(self)
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
        for p in (self.start, self.home, self.browser, self.progress, self.pick, self.done, self.updating, self.installed,
                  self.stores, self.streaming, self.addons):
            self.stack.addWidget(p)

        for lst in self.findChildren(QListWidget):
            lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            lst.setWordWrap(True)
        for lst in (self.start.list, self.stores.list, self.streaming.list, self.addons.list, self.installed.list):
            lst.setItemDelegate(CardDelegate(lst))
            lst.setMouseTracking(True)
            lst.setSpacing(2)
        self.nav = Nav(QApplication.instance(), self.on_action, busy=lambda: self.thread is not None) \
            if use_nav else None
        # A bound method (not a lambda): Qt disconnects it automatically when the window goes away.
        QApplication.instance().focusChanged.connect(self._focus_changed)
        self.refresh_launchers()
        ensure_app_icon()
        self.sync_steam_ids()
        self.add_logos()
        self.go(self.start)
        dupes = core.find_duplicate_shortcuts(None, self.paths.launchers)
        if dupes:
            self.flash(f"Steam has {len(dupes)} duplicate shortcut{'s' * (len(dupes) != 1)} — "
                       "☰ Menu → Remove duplicate Steam shortcuts", ms=8000)
        QTimer.singleShot(0, self.check_leftovers)
        QTimer.singleShot(2000, self.refresh_better_xcloud)  # (after the window is up; at most every six hours)
        if check_updates is None:
            check_updates = updater.self_path() is not None and not os.environ.get("DECKHAND_NO_UPDATE_CHECK")
        if check_updates:
            self.check_for_updates(manual=False)

    # chrome
    SECTIONS = ("start", "install", "stores", "stream", "addons", "installed")

    def _rail(self) -> QWidget:
        """Deckhand's sections down the left: tap them, or press left on the D-pad to reach them, up and down to
        choose and A to open (right goes back to the page)."""
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
        self.station_glyphs: dict[str, str] = {}
        for key, text, glyph in (("start", "Start", "flag"), ("install", "Install", "download"), ("stores", "Stores", "bag"),
                                 ("stream", "Stream", "signal"), ("addons", "Add-ons", "plus"),
                                 ("installed", "Installed", "stack")):
            b = QPushButton(text)
            b.setObjectName("station")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.setIcon(glyph_icon(glyph))
            b.setIconSize(QSize(26, 26))
            b.clicked.connect(lambda _c=False, k=key: self.switch_section(k))
            v.addWidget(b)
            self.stations[key] = b
            self.station_glyphs[key] = glyph
        v.addStretch(1)
        menu = QPushButton("Menu")
        menu.setObjectName("station")
        menu.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        menu.setIcon(glyph_icon("menu"))
        menu.setIconSize(QSize(26, 26))
        menu.clicked.connect(lambda _c=False: self.open_menu())
        v.addWidget(menu)
        v.addSpacing(12)
        self.space = label("", "muted", wrap=False)
        self.space.setContentsMargins(24, 0, 0, 2)
        v.addWidget(self.space)
        for text in ("◀ for sections", f"Deckhand {__version__}"):
            foot = label(text, "muted", wrap=False)
            foot.setContentsMargins(24, 0, 0, 0)
            v.addWidget(foot)
        self.crumb = QLabel()  # (the page's own heading says where you are)
        return rail

    def section_of(self, page: QWidget) -> str | None:
        if page is self.start:
            return "start"
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
        if key == current and self.stack.currentWidget() in (self.start, self.home, self.stores, self.streaming, self.addons,
                                                             self.installed):
            self._sync_stations()
            self.stack.currentWidget().enter()  # (picked from the rail: carry on into the page)
            return
        if self.must_stay():
            self._sync_stations()
            return
        {"start": self.show_start, "install": self.go_home, "stores": self.show_stores, "stream": self.show_streaming,
         "addons": self.show_addons, "installed": self.show_installed}[key]()

    def must_stay(self) -> bool:
        """True (and says so) while the page on screen can't be left: an install running, a program waiting
        to be picked, an update. Nothing else leads back to it."""
        if self.thread is not None or self.pending is not None or self.stack.currentWidget() is self.updating:
            self.flash("Finish what's running first")
            return True
        return False

    def _sync_stations(self) -> None:
        current = self.section_of(self.stack.currentWidget())
        for key, b in self.stations.items():
            b.setChecked(key == current)
            # (the chosen section is a pill: its icon must read on the pill, not on the rail)
            b.setIcon(glyph_icon(self.station_glyphs[key], theme.ON_PILL if key == current else theme.TEXT_DIM))

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

    def show_start(self) -> None:
        self.go(self.start)

    def hide_banners(self) -> None:
        for page in (self.start, self.home):
            page.banner.hide()

    def open_addon(self, addon_id: str) -> None:
        """The Add-ons section, with that add-on picked (A then opens it)."""
        self.show_addons()
        if self.stack.currentWidget() is self.addons:
            select_row(self.addons.list, addon_id)

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

    def flash(self, text: str, ms: int = 2600) -> None:
        self.toast.show_message(text, ms)

    # ── install flow ─────────────────────────────────────────────────────

    def browse_installers(self) -> None:
        home = Path.home()
        places = [("Downloads", home / "Downloads"), ("Desktop", home / "Desktop"), ("Home", home)]
        places += [(p.name, p) for p in core.removable_media()]
        self.browser.open("Choose an installer", places, core.INSTALLER_SUFFIXES + core.ARCHIVE_SUFFIXES,
                          self.confirm_install,
                          self.go_home)
        self.go(self.browser)

    def confirm_install(self, installer: Path) -> bool:
        """Ask, then install. True if an install was started."""
        if self.thread is not None or self.pending is not None or self.update_thread is not None \
                or any(getattr(w, "kind", "") != "unpack" for w in self.workers):  # (opening an archive leads here)
            self.flash("Finish what's running first")
            return False
        if installer.suffix.lower() in core.ARCHIVE_SUFFIXES:
            self.unpack_then_confirm(installer)
            return False
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
        if core.quiet_args(installer) is not None:
            text += ("\n\nQuick install lets the installer use its own default choices, so there is nothing to "
                     "click. Choose Install if you want to pick the folder or options yourself.")
            choice = Sheet.ask(self, f"Install {name}?", text, ("Quick install", "Install", "Cancel"))
            if choice in (0, 1):
                self.start_install(installer, quiet=choice == 0)
                return True
            return False
        if Sheet.ask(self, f"Install {name}?", text, ("Install", "Cancel")) == 0:
            self.start_install(installer)
            return True
        return False

    UNPACKED = "unpacked"  # (a folder in Deckhand's downloads: archives are opened here, and deleted afterwards)

    def unpack_then_confirm(self, archive: Path) -> None:
        """A .zip/.7z/.rar/.iso: unpack it, find its setup file, and go on as if that was picked."""
        dest = self.paths.downloads / self.UNPACKED / core.guess_name(archive).replace("/", "_")
        if self.busy_with("unpack"):
            return
        self.flash(f"Opening {archive.name}…")

        def done(setup: Path) -> None:
            if not self.confirm_install(setup):
                self.drop_download(setup)

        def failed(message: str) -> None:
            Sheet.ask(self, "Couldn't open it", message.partition("\n\n")[0], ("Close",),
                      detail=message.partition("\n\n")[2])

        self.run_worker(lambda _s: core.extract_archive(archive, dest), done, failed, kind="unpack")

    def start_install(self, installer: Path, allow_no_container: bool = False, name: str = "",
                      quiet: bool = False) -> None:
        if self.thread is not None:
            return
        installer = Path(installer)
        self.current_installer, self.current_name, self.current_quiet = installer, name, quiet
        self.progress.reset(installer, name)
        self.go(self.progress)
        t = InstallThread(installer, self.paths, allow_no_container, name, quiet)
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
        if installer is None:
            return
        if installer.resolve().parent == self.paths.downloads.resolve():
            installer.unlink(missing_ok=True)
            return
        unpacked = (self.paths.downloads / self.UNPACKED).resolve()
        if installer.resolve().is_relative_to(unpacked) and installer.resolve() != unpacked:
            shutil.rmtree(unpacked / installer.resolve().relative_to(unpacked).parts[0], ignore_errors=True)

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
                self.start_install(self.current_installer, allow_no_container=True, name=self.current_name,
                                   quiet=getattr(self, "current_quiet", False))
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

    def set_up_stream(self, svc: streaming.Service, better_xcloud: bool | None = None,
                      browser: str | None = None) -> None:
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

        self.run_worker(lambda status: streaming.set_up(svc, self.paths, status, better_xcloud=better_xcloud,
                                                        browser=browser),
                        finished, failed,
                        status=page.status.setText, kind="stream", progress=page.show_progress)

    def refresh_better_xcloud(self) -> None:
        """Keep Better xCloud current for someone who has it on, even if they only open Deckhand."""
        if not any(a.kind == "stream" and streaming.BETTER_XCLOUD in a.options for a in self.library.load()):
            return
        if self.busy_with_quietly("better-xcloud"):
            return
        self.run_worker(lambda _s: streaming.update_better_xcloud(),
                        lambda r: self.flash(r.message) if r.status == "updated" else None,
                        lambda _m: None, kind="better-xcloud")

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
        apps = self.library.load()
        for app in apps:
            folder = core.guess_program_dir(app)
            if folder is not None:  # recorded without its folder on D: (see guess_program_dir)
                app.extra_dirs = [str(folder)]
                self.library.upsert(app)
            if app.kind == "program" and app.launcher and app.prefix and Path(app.prefix).is_dir():
                try:
                    core.write_launcher(app, self.paths, roots[0] if roots else None, roots)
                except OSError:
                    pass
        if not any(a.kind in ("stream", "store", "addon") for a in apps):
            return

        def rewrite(installed: set[str]) -> None:
            for app in apps:
                if app.kind == "stream":
                    svc = streaming.service(app.id.split("-", 1)[1])
                elif app.kind == "store":
                    s = stores.store(app.id.split("-", 1)[1])
                    svc = s.service if s and not s.is_windows else None
                elif app.kind == "addon":
                    a = addons.addon(app.id.split("-", 1)[1])
                    svc = a.service if a and a.app else None
                else:
                    continue
                if svc is None:
                    continue
                have = streaming.detect(installed, (svc,))
                bx = streaming.BETTER_XCLOUD in app.options
                # Only rewrite when what it runs in is confirmed present: never downgrade a working
                # Edge/Chromium launcher to Chrome because flatpak list failed or came back empty.
                if streaming.needs(svc, have, bx) is not None:
                    continue
                try:
                    streaming.write_launcher(svc, self.paths, have, bx, app.kind,
                                             own_profile=streaming.OWN_PROFILE in app.options)
                except OSError:
                    pass

        self.run_worker(lambda _s: streaming.installed_apps(), rewrite, lambda _m: None, kind="launchers")

    # ── menu ─────────────────────────────────────────────────────────────

    def open_menu(self) -> None:
        if Sheet.current is not None:
            return
        def look_again() -> None:
            if self.must_stay():
                return
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
            ("Light look" if theme.SCHEME == "dusk" else "Dark look", self.switch_look),
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
                if self.stack.currentWidget() in (self.streaming, self.stores, self.addons, self.installed):
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

    def switch_look(self) -> None:
        """Dusk ⇄ cloud, Arcadia's two schemes. Remembered; everything drawn from now on uses it."""
        look = "cloud" if theme.SCHEME == "dusk" else "dusk"
        theme.use(look)
        self.paths.remember(look=look)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.STYLE)
        self.flash("Reopen Deckhand to see the new look everywhere")

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
                self.start.show_banner(update)
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
        if self.thread is not None or self.pending is not None:
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
        self.hide_banners()
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
            if p.suffix.lower() in core.INSTALLER_SUFFIXES + core.ARCHIVE_SUFFIXES:
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


def update_better_xcloud_cli(force: bool = False, out=print, fetch=None) -> int:
    """`deckhand --update-better-xcloud [--force]`: look for a newer Better xCloud and install it."""
    result = streaming.update_better_xcloud(force=force, fetch=fetch)
    if result.status != "skipped":
        out(result.message)
    return 0 if result.ok else 1


def add_arcadia_cli(out=print) -> int:
    """`deckhand --add-arcadia`: download Arcadia, the sister app, and set it up (menu entry, Steam shortcut)."""
    from PyQt6.QtGui import QGuiApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    gui = QGuiApplication(sys.argv[:1])  # the artwork is painted
    try:
        out("Downloading Arcadia…")
        version = addons.download_arcadia()
        addons.set_up_arcadia(core.Paths.default())
    except (core.InstallError, OSError) as e:
        out(f"Couldn't set up Arcadia: {e}")
        return 1
    finally:
        del gui
    out(f"Arcadia {version} is installed in ~/Applications and added to Steam.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = argv[1:]
    if "--version" in args:
        print(f"Deckhand {__version__}")
        return 0
    if "-h" in args or "--help" in args:
        print("usage: deckhand [INSTALLER.exe|.msi]\n"
              "       deckhand --update\n"
              "       deckhand --add-arcadia\n"
              "       deckhand --update-better-xcloud [--force]\n\n"
              "Opens Deckhand. Given an installer, asks to install it right away.\n"
              "--update downloads and installs the newest Deckhand.")
        return 0
    if "--update" in args:
        return updater.cli_update(__version__)
    if "--update-better-xcloud" in args:  # run by the Xbox launcher before it starts the browser
        return update_better_xcloud_cli(force="--force" in args)
    if "--add-arcadia" in args:  # used by get.sh --with-arcadia: the same thing as Add-ons → Arcadia
        return add_arcadia_cli()
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
    theme.load_fonts()
    theme.use(core.Paths.default().state().get("look", "dusk"))
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
