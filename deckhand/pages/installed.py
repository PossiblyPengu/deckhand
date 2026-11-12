"""What Deckhand installed, to uninstall things. Deliberately not a launcher: no Play here."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QProgressBar, QVBoxLayout

from .. import artwork, core, fixes, saves
from ..widgets import Sheet, label
from .common import KIND_LABEL, PAGE_MARGINS, Page, STEAM_STATE, ago, badge_icon, glyph_icon, on_choose, set_pill
from .threads import SizesThread


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
                            "Streaming services, game stores and add-ons you added are here too. "
                            "You play them from your Steam library.", "dim"))
        self.list = QListWidget()
        self.list.setIconSize(QSize(44, 44))
        on_choose(self.list, self._activate)
        lay.addWidget(self.list, 1)
        self.empty = label("Nothing installed with Deckhand yet. Install a Windows program, or set up a game store, "
                           "a streaming service or an add-on: they're listed here afterwards.", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.empty, 1)  # (takes the list's room while there's no list)
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
        apps = sorted(self.win.library.load(), key=lambda a: a.installed_at,
                      reverse=True)  # streaming services, stores and add-ons are listed too
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
            state = self.steam.get(app.id, "out")
            set_pill(it, STEAM_STATE[state], "done" if state == "in" else "todo")
            self.list.addItem(it)
        busy = self.win.pending.compat_dir if self.win.pending else None
        self.leftovers = {f"leftover:{d.name}": d for d in core.orphan_prefixes(self.win.paths) if d != busy}
        for key, d in self.leftovers.items():
            it = QListWidgetItem(self._leftover_text(key))
            it.setIcon(glyph_icon("disc"))
            it.setData(Qt.ItemDataRole.UserRole, key)
            set_pill(it, "Unfinished", "busy")
            self.list.addItem(it)
        shown = bool(apps or self.leftovers)
        self.list.setVisible(shown)
        self.empty.setVisible(not shown)
        if shown:
            self.list.setCurrentRow(0)
        if self.thread is not None:
            self.thread.requestInterruption()  # it clears self.thread when it's done; never touch a dead one
        t = SizesThread([(a.id, core.app_paths(a)) for a in apps if a.kind == "program" and a.id not in self.sizes]
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
        where = STEAM_STATE[self.steam.get(app.id, "out")]
        if app.kind != "program":
            return f"{app.name}\n{KIND_LABEL[app.kind]}  ·  Added {ago(app.installed_at)}  ·  {where}"
        size = core.human_size(self.sizes[app.id]) if app.id in self.sizes else "…"
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
        if app.kind == "program":
            actions += [("Uninstall", lambda: self.win.uninstall(app, self.sizes.get(app.id))),
                        ("Fix it: install what it's missing", lambda: self._fix(app)),
                        ("Back up saves", lambda: self.win.back_up_saves(app))]
            if saves.backups(self.win.paths, app.name):
                actions.append(("Restore saves", lambda: self.win.restore_saves(app)))
        else:
            actions.append(("Remove", lambda: self.win.uninstall(app)))
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
