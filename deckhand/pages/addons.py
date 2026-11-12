"""Popular Deck add-ons and tools, installed from their official sources."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QProgressBar, QVBoxLayout

from .. import addons, artwork, core, proton, streaming
from ..widgets import Sheet, label
from .common import PAGE_MARGINS, Page, REMOVE_IN_STEAM, STEAM_STATE, logo_icon, on_choose, set_pill


class AddonsPage(Page):
    """Popular Deck add-ons and tools, installed from their official sources."""

    title = "Add-ons"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        lay.addWidget(label("Add-ons", "h1"))
        lay.addWidget(label("Arcadia, Deckhand's sister app for retro games, and popular Deck add-ons and tools, from "
                            "their official sources. Decky Loader and EmuDeck set "
                            "themselves up in Desktop Mode; apps from Flathub are installed the way Discover does it, so Discover keeps them up to date.",
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
            st = parts[1]
            set_pill(it, "Checking…" if st == "…" else st.split(" (")[0],
                     "done" if st.startswith(("Installed", "Set up")) else "busy" if st.startswith("Downloaded")
                     else "todo")
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
        if a.id == "arcadia":
            self._arcadia(a)
        elif a.id == "decky":
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
            if Sheet.ask(self, f"Install {a.name}?", f"Deckhand installs {a.name} from Flathub, the way Discover does "
                         f"it, so Discover keeps it up to date. {then}", ("Install", "Cancel")) == 0:
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

    # ── Arcadia (the sister app) ─────────────────────────────────────

    def _arcadia(self, a: addons.Addon) -> None:
        if addons.arcadia_app() is None:
            if Sheet.ask(self, "Arcadia", "Arcadia is Deckhand's sister app for retro games: pick a console, then a "
                         "game, and it starts in the right emulator. Its Setup screen sorts your ROMs, installs the "
                         "emulators, checks BIOS files and syncs saves.\n\nDeckhand downloads the newest Arcadia from "
                         "its GitHub releases into your Applications folder, checks it, and adds it to Steam.",
                         ("Download Arcadia", "Cancel")) == 0:
                self._download_arcadia(a)
            return
        app = self.apps.get(a.id)
        in_steam = app is not None and self.steam.get(a.id) in ("in", "sent")
        yours = not in_steam and bool(addons.arcadia_shortcut_of_yours())
        actions = [("Open Arcadia", lambda: self._open_arcadia()),
                   ("Get the newest Arcadia", lambda: self._download_arcadia(a))]
        if in_steam:
            actions.append(("Remove from Steam", lambda: self.win.uninstall(app)))
        elif not yours:
            actions.append(("Add to Steam", lambda: self._set_up_arcadia(a)))
        actions += [("Uninstall", lambda: self._uninstall_arcadia(a, yours)), ("Close", None)]
        where = ("It's in your Steam library." if in_steam else
                 "It's in your Steam library: you added it yourself, so Deckhand leaves that shortcut alone." if yours
                 else "It's not in your Steam library yet.")
        choice = Sheet.ask(self, "Arcadia", f"Arcadia is installed. {where}", tuple(t for t, _f in actions),
                           primary=len(actions) - 1, danger=(len(actions) - 2,))
        if 0 <= choice < len(actions) and actions[choice][1] is not None:
            actions[choice][1]()

    def _download_arcadia(self, a: addons.Addon) -> None:
        self.status.setText("Downloading Arcadia…")

        def run(status):
            report = getattr(status, "progress", lambda pct: None)

            def progress(done: int, total: int) -> None:
                if total:
                    status(f"Downloading Arcadia…  {core.human_size(done)} of {core.human_size(total)}")
                    report(done * 100 // total)
            report(-1)
            version = addons.download_arcadia(progress)
            status("Adding Arcadia to Steam…")
            report(-1)
            return version, addons.set_up_arcadia(self.win.paths)

        def finished(result) -> None:
            version, app = result
            self.status.setText("")
            self.show_progress(None)
            self.win.paths.remember(arcadia_version=version)
            self.win._write_art(app, artwork.load_icon(app.icon))
            self.win._say_added(app, "Add-ons → Arcadia")
            self.refresh()

        self.win.run_worker(run, finished, self._failed("Arcadia"), status=self.status.setText, kind="addon",
                            progress=self.show_progress)

    def _set_up_arcadia(self, a: addons.Addon) -> None:
        def finished(app: core.App) -> None:
            self.win._write_art(app, artwork.load_icon(app.icon))
            self.win._say_added(app, "Add-ons → Arcadia")
            self.refresh()

        self.win.run_worker(lambda _s: addons.set_up_arcadia(self.win.paths), finished, self._failed("Arcadia"),
                            kind="addon")

    def _uninstall_arcadia(self, a: addons.Addon, yours: bool = False) -> None:
        shortcut = (" The Steam shortcut you added yourself stays: remove it in Steam if you like." if yours
                    else " and its Steam shortcut")
        what = f"Removes Arcadia.{shortcut}" if yours else f"Removes Arcadia{shortcut}."
        if Sheet.ask(self, "Uninstall Arcadia?", f"{what} Your games, saves and Arcadia's settings (in ~/Arcadia) "
                     "stay, in case you install it again.", ("Uninstall", "Keep"),
                     primary=1, danger=(0,)) != 0:
            return

        def finished(left_in_steam: bool) -> None:
            self.win.flash("Arcadia uninstalled")
            self.refresh()
            if left_in_steam:
                Sheet.ask(self, "Arcadia uninstalled", "Its shortcut is still in your Steam library: Deckhand "
                          "doesn't change Steam's list while Steam is open.\n\n" + REMOVE_IN_STEAM, ("Close",))

        self.win.run_worker(lambda _s: addons.uninstall_arcadia(self.apps.get(a.id), self.win.paths), finished,
                            self._failed("Arcadia"), kind="addon")

    def _open_arcadia(self) -> None:
        try:
            addons.open_arcadia()
            self.win.flash("Arcadia is opening…")
        except (OSError, core.InstallError) as e:
            Sheet.ask(self, "Arcadia", str(e), ("Close",))

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
