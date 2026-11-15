"""Games (the Stores section): every way to get games onto the Deck, under headings. Game stores other than Steam
(Linux apps from Flathub, Windows apps with Proton), plus the add-ons that are ways to get games: Arcadia for retro
games, Lutris, and Nexus Launcher for Xbox PC games (their actions are the Tools section's, AddonActions)."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QProgressBar, QVBoxLayout

from .. import addons, core, stores, streaming
from ..widgets import Sheet, label
from .addons import AddonActions
from .common import (page_header, PAGE_MARGINS, Page, STEAM_STATE, add_heading, keep_row, logo_icon, on_choose,
                     set_pill)


class StoresPage(AddonActions, Page):
    """Games: game stores, launchers, Arcadia and Xbox PC games, each added to Steam, grouped under headings."""

    title = "Games"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        page_header(lay, "stores", "Games",
                    "Every way to get games onto the Deck, each in your Steam library. Games whose anti-cheat blocks "
                    "Linux won't run.")
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
        # Store id or add-on id → its app as Deckhand set it up, and its core.steam_state (the ids never overlap).
        self.apps: dict[str, core.App] = {}
        self.steam: dict[str, str] = {}
        self.outside: dict[str, list[dict]] = {}  # store id → its Steam shortcuts made outside this app

    def refresh(self) -> None:
        library = self.win.library.load()
        roots, running = core.steam_roots(), core.steam_is_running()
        entries = core.steam_shortcuts(roots)
        self.apps = {s.id: a for s in stores.STORES if (a := stores.installed(s, library)) is not None}
        self.apps.update({i: a for i, a in self._addon_apps().items() if i in addons.GAME_ADDONS})
        self.steam = {i: core.steam_state(a, roots, running, entries) for i, a in self.apps.items()}
        self.outside = {s.id: stores.spots(s, entries, self.win.paths.launchers) for s in stores.STORES}
        row = self.list.currentRow()
        self.list.clear()
        for heading, keys in addons.GAMES_LAYOUT:
            add_heading(self.list, heading)
            for key in keys:
                kind, key_id = key.split(":", 1)
                self.list.addItem(self._addon_item(addons.addon(key_id)) if kind == "addon" else
                                  self._store_item(stores.store(key_id)))
        keep_row(self.list, row)
        if self.installed is None and not self.win.busy_with_quietly("store-flatpaks"):
            self.win.run_worker(lambda _s: stores.detect(), self._got_installed, lambda _m: None,
                                kind="store-flatpaks")

    def _got_installed(self, apps: set[str]) -> None:
        self.installed = apps
        if self.win.stack.currentWidget() is self:
            self.refresh()

    def _store_item(self, s: stores.Store) -> QListWidgetItem:
        it = QListWidgetItem(logo_icon(s.id, s.name, s.color), self._text(s))
        it.setData(Qt.ItemDataRole.UserRole, s.id)
        if s.id in self.apps:
            state = self.steam.get(s.id, "out")
            set_pill(it, STEAM_STATE[state], "done" if state == "in" else "todo")
        elif self.outside.get(s.id):
            set_pill(it, "In Steam", "done")
        else:
            set_pill(it, "Not installed" if s.is_windows else "Not added yet")
        return it

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
        key = item.data(Qt.ItemDataRole.UserRole)
        if key in addons.GAME_ADDONS:
            self._activate_addon(addons.addon(key))
            return
        s = stores.store(key)
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
        removable = s.app in (self.installed or set())  # (from Flathub: Deckhand can uninstall it)
        if removable:
            text = (f"{s.name} is installed, but it isn't in your Steam library.\n\nAdd to Steam: it's in your library "
                    f"with its own artwork.\nUninstall: removes {s.name} from the Deck.") + (f"\n\n{s.note}" if s.note else "")
        buttons = ("Add to Steam", "Uninstall", "Cancel") if removable else ("Add to Steam", "Cancel")
        choice = Sheet.ask(self, s.name if removable else f"Add {s.name} to Steam?", text, buttons, primary=0,
                           danger=(1,) if removable else ())
        if choice == 0:
            self.win.set_up_store(s)
        elif removable and choice == 1:
            self.win.uninstall_flatpak(s.app)

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
