"""Cloud gaming and home streaming, set up as Steam shortcuts."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QProgressBar, QVBoxLayout

from .. import core, streaming
from ..widgets import Sheet, label
from .common import PAGE_MARGINS, Page, STEAM_STATE, logo_icon, on_choose, set_pill


class StreamingPage(Page):
    """Cloud gaming and home streaming, set up as Steam shortcuts."""

    title = "Game streaming"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        lay.addWidget(label("Game streaming", "h1"))
        lay.addWidget(label("Pick a service to add it to Steam. Apps it needs come from Flathub, just like in "
                            "Discover, so Discover keeps them up to date. Sign in the first time you open it; leave "
                            "with STEAM → Exit game.", "dim"))
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
            app = apps.get(f"stream-{svc.id}")
            it = QListWidgetItem(logo_icon(svc.id, svc.name, svc.color), self._text(svc, app))
            it.setData(Qt.ItemDataRole.UserRole, svc.id)
            if app is not None:
                state = self.steam.get(app.id, "out")
                set_pill(it, STEAM_STATE[state], "done" if state == "in" else "todo")
            else:
                set_pill(it, "In Steam", "done") if self.outside.get(svc.id) else set_pill(it, "Not added yet")
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
            what = (f"Deckhand installs {streaming.app_name(need)} from Flathub first, the same as Discover would, "
                    "so Discover keeps it up to date. ")
        elif svc.is_web:
            what = f"It opens in {streaming.app_name(streaming.uses(svc, installed))}, which is already installed. "
        else:
            what = f"{svc.name} is already installed. "
        desktop_note = ("\n\nIn Desktop Mode it opens as a window you can close; STEAM + X opens the keyboard."
                        if svc.is_web else "")
        text = what + f"Then {svc.name} is in your Steam library with its own artwork" + (
            ", full screen with the controller working." if svc.is_web else ".") + desktop_note
        if xbox:  # two ways, each saying exactly which browser it uses and whether that gets installed
            text = (f"• Add to Steam: opens in {self._browser_state(svc, installed, False)}.\n"
                    f"• Add with Better xCloud: opens in {self._browser_state(svc, installed, True)}, with Better "
                    "xCloud — a free add-on for a sharper picture, stream stats, Xbox remote play and mouse & "
                    "keyboard. It needs Chromium because Google Chrome can't load it.\n\n"
                    f"Either way {svc.name} is in your Steam library, full screen with the controller working."
                    + desktop_note)
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
