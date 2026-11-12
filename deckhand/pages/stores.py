"""Game stores other than Steam: their apps added to Steam (Linux apps from Flathub, Windows apps with Proton)."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QProgressBar, QVBoxLayout

from .. import core, stores, streaming
from ..widgets import Sheet, label
from .common import PAGE_MARGINS, Page, STEAM_STATE, logo_icon, on_choose, set_pill


class StoresPage(Page):
    """Game stores other than Steam: their apps added to Steam (Linux apps from Flathub, Windows apps with Proton)."""

    title = "Game stores"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        lay.addWidget(label("Game stores", "h1"))
        lay.addWidget(label("Pick a store to add it to Steam, then sign in and play from there. Heroic and itch are made "
                            "for Linux; the rest are Windows apps run with Proton. Games with anti-cheat that blocks "
                            "Linux won't run.",
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
            if s.id in self.apps:
                state = self.steam.get(s.id, "out")
                set_pill(it, STEAM_STATE[state], "done" if state == "in" else "todo")
            elif self.outside.get(s.id):
                set_pill(it, "In Steam", "done")
            else:
                set_pill(it, "Not installed" if s.is_windows else "Not added yet")
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

            xfer = core.Transfer(f"Downloading {s.name}…")

            def progress(done: int, total: int) -> None:
                if total:
                    status(xfer.text(done, total))
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
        return self.with_paging([("A", "Select", self.win.nav_activate), ("B", "Back", self.back), ("☰", "Menu", self.win.open_menu)])
