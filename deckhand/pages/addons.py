"""Popular Deck add-ons and tools, installed from their official sources."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QProgressBar, QVBoxLayout

from .. import addons, artwork, core, proton, streaming
from ..widgets import Sheet, label
from .common import page_header, PAGE_MARGINS, Page, REMOVE_IN_STEAM, STEAM_STATE, logo_icon, on_choose, set_pill


class AddonsPage(Page):
    """Popular Deck add-ons and tools, installed from their official sources."""

    title = "Add-ons"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(12)
        page_header(lay, "addons", "Add-ons",
                    "Arcadia for retro games, and popular Deck add-ons and tools, from their official sources.")
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

    def _activate(self, item: QListWidgetItem) -> None:
        a = addons.addon(item.data(Qt.ItemDataRole.UserRole))
        if a is None or self.win.busy_with("addon"):
            return
        if a.id == "arcadia":
            self._arcadia(a)
        elif a.id == "nexus":
            self._nexus(a)
        elif a.id == "decky":
            self._decky(a)
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
            steam = a.steam or core.in_game_mode()  # (in Game Mode a Desktop tool is only usable from Steam)
            then = ("Then it's in your Steam library too, so you can open it in Game Mode." if steam else
                    "Then it's in the Desktop Mode app menu.")
            if Sheet.ask(self, f"Install {a.name}?", f"Deckhand installs {a.name} from Flathub, the way Discover does "
                         f"it, so Discover keeps it up to date. {then}", ("Install", "Cancel")) == 0:
                self._set_up(a, steam)
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

            xfer = core.Transfer(f"Downloading {release.version}…")

            def progress(done: int, total: int) -> None:
                if total:
                    status(xfer.text(done, total))
                    report(done * 100 // total)
            report(-1)
            path = proton.install(release, progress=progress, status=lambda s: (status(s), report(-1)))
            return path

        def finished(_path) -> None:
            self.status.setText("")
            self.show_progress(None)
            core.record_download(self.win.paths, "GE-Proton", release.version,
                                 {"url": release.url, "algo": "sha512", "sha": "(from its .sha512sum file)",
                                  "verified": True})
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
        info: dict = {}  # what was fetched, for the download log

        def run(status):
            report = getattr(status, "progress", lambda pct: None)

            xfer = core.Transfer("Downloading Arcadia…")

            def progress(done: int, total: int) -> None:
                if total:
                    status(xfer.text(done, total))
                    report(done * 100 // total)
            report(-1)
            version = addons.download_arcadia(progress, info=info)
            status("Adding Arcadia to Steam…")
            report(-1)
            return version, addons.set_up_arcadia(self.win.paths)

        def finished(result) -> None:
            version, app = result
            self.status.setText("")
            self.show_progress(None)
            self.win.paths.remember(arcadia_version=version)
            core.record_download(self.win.paths, "Arcadia", version, info)
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

    # ── Nexus Launcher (Xbox PC games, built on Xodus) ───────────────

    def _nexus(self, a: addons.Addon) -> None:
        if addons.nexus_app() is None:
            if Sheet.ask(self, "Xbox PC games", "Nexus Launcher plays your Xbox and Game Pass PC games on the Deck. It "
                         "is built on Xodus, an open-source project, and is unofficial: not made or supported by "
                         "Microsoft, and still experimental, so some games run badly or not at all. You need a "
                         "Microsoft account and a licence for each game.\n\nDeckhand downloads the newest Nexus "
                         "Launcher from its GitHub releases, checks it, and adds it to Steam. Then open it, install "
                         "the Nexus Runtime in its Settings, and sign in (a mouse or the touchscreen helps for "
                         "that; the browser option needs Google Chrome).", ("Download Nexus Launcher", "Cancel")) == 0:
                self._download_nexus(a)
            return
        app = self.apps.get(a.id)
        in_steam = app is not None and self.steam.get(a.id) in ("in", "sent")
        yours = not in_steam and bool(addons.nexus_shortcut_of_yours())
        actions = [("Open Nexus Launcher", lambda: self._open_nexus()),
                   ("Get the newest version", lambda: self._download_nexus(a))]
        if in_steam:
            actions.append(("Remove from Steam", lambda: self.win.uninstall(app)))
        elif not yours:
            actions.append(("Add to Steam", lambda: self._set_up_nexus(a)))
        actions += [("Uninstall", lambda: self._uninstall_nexus(a, yours)), ("Close", None)]
        where = ("It's in your Steam library." if in_steam else
                 "It's in your Steam library already, so Deckhand leaves that shortcut alone." if yours
                 else "It's not in your Steam library yet.")
        choice = Sheet.ask(self, "Nexus Launcher", f"Nexus Launcher is installed. {where}",
                           tuple(t for t, _f in actions), primary=len(actions) - 1, danger=(len(actions) - 2,))
        if 0 <= choice < len(actions) and actions[choice][1] is not None:
            actions[choice][1]()

    def _download_nexus(self, a: addons.Addon) -> None:
        self.status.setText("Downloading Nexus Launcher…")
        info: dict = {}  # what was fetched, for the download log (and whether anything could check it)

        def run(status):
            report = getattr(status, "progress", lambda pct: None)

            xfer = core.Transfer("Downloading Nexus Launcher…")

            def progress(done: int, total: int) -> None:
                if total:
                    status(xfer.text(done, total))
                    report(done * 100 // total)
            report(-1)
            version = addons.download_nexus(progress, info=info, note=lambda text: info.update(note=text))
            status("Adding Nexus Launcher to Steam…")
            report(-1)
            return version, addons.set_up_nexus(self.win.paths)

        def finished(result) -> None:
            version, app = result
            self.status.setText("")
            self.show_progress(None)
            self.win.paths.remember(nexus_version=version)
            core.record_download(self.win.paths, "Nexus Launcher", version, info)
            self.win._write_art(app, artwork.load_icon(app.icon))
            self.win._say_added(app, "Add-ons → Xbox PC games")
            self.refresh()
            if info.get("note"):  # (a release that publishes no checksum: say so rather than staying quiet)
                Sheet.ask(self, "Nexus Launcher is installed", info["note"], ("Close",))

        self.win.run_worker(run, finished, self._failed("Nexus Launcher"), status=self.status.setText, kind="addon",
                            progress=self.show_progress)

    def _set_up_nexus(self, a: addons.Addon) -> None:
        def finished(app: core.App) -> None:
            self.win._write_art(app, artwork.load_icon(app.icon))
            self.win._say_added(app, "Add-ons → Xbox PC games")
            self.refresh()

        self.win.run_worker(lambda _s: addons.set_up_nexus(self.win.paths), finished,
                            self._failed("Nexus Launcher"), kind="addon")

    def _uninstall_nexus(self, a: addons.Addon, yours: bool = False) -> None:
        shortcut = ("" if yours else " and its Steam shortcut")
        if Sheet.ask(self, "Uninstall Nexus Launcher?", f"Removes Nexus Launcher{shortcut} from Deckhand. Your games, "
                     "saves, runtime and sign-in stay, in case you install it again. (A copy made by Nexus's own "
                     "installer is removed with its uninstall script.)", ("Uninstall", "Keep"),
                     primary=1, danger=(0,)) != 0:
            return

        def finished(left_in_steam: bool) -> None:
            self.win.flash("Nexus Launcher uninstalled")
            self.refresh()
            if left_in_steam:
                Sheet.ask(self, "Nexus Launcher uninstalled", "Its shortcut is still in your Steam library: Deckhand "
                          "doesn't change Steam's list while Steam is open.\n\n" + REMOVE_IN_STEAM, ("Close",))

        self.win.run_worker(lambda _s: addons.uninstall_nexus(self.apps.get(a.id), self.win.paths), finished,
                            self._failed("Nexus Launcher"), kind="addon")

    def _open_nexus(self) -> None:
        try:
            addons.open_nexus()
            self.win.flash("Nexus Launcher is opening…")
        except (OSError, core.InstallError) as e:
            Sheet.ask(self, "Nexus Launcher", str(e), ("Close",))

    def _decky(self, a: addons.Addon) -> None:
        """Decky's own installer, with its questions asked here, so it works in Game Mode as well as on the desktop."""
        installed = addons.decky_version() is not None
        if installed:
            labels = ("Update to the latest release", "Update to the latest prerelease", "Uninstall", "Wipe it",
                      "Cancel")
            options = ("update to latest release", "update to latest prerelease", "uninstall decky loader",
                       "wipe decky loader")
            text = ("Decky Loader is installed. Deckhand runs Decky's own installer (github.com/SteamDeckHomebrew, "
                    "the one decky.xyz links to) to update it, or to remove it. \"Wipe it\" also deletes Decky's "
                    "settings and plugins; \"Uninstall\" keeps them. Use the prerelease only on SteamOS beta.")
        else:
            labels = ("Install", "Install the prerelease", "Cancel")
            options = ("release", "prerelease")
            text = ("Deckhand runs Decky's own installer (github.com/SteamDeckHomebrew, the one decky.xyz links to) "
                    "for you. It needs your admin password (or can use a temporary one) and takes a minute. Choose "
                    "the prerelease only on SteamOS beta.")
        choice = Sheet.ask(self, "Decky Loader", text, labels, primary=0, danger=(3,) if installed else ())
        if not 0 <= choice < len(options):
            return
        option = options[choice]
        if option == "wipe decky loader" and Sheet.ask(
                self, "Wipe Decky Loader?", "This deletes Decky Loader together with its settings and every plugin.",
                ("Wipe it", "Keep it"), primary=1, danger=(0,)) != 0:
            return
        password, temporary = self._decky_password()
        if password is None:
            return
        removing = option in ("uninstall decky loader", "wipe decky loader")
        self.status.setText("Downloading Decky's installer…")
        self.show_progress(-1)

        def run(status):
            report = getattr(status, "progress", lambda pct: None)
            script = addons.fetch_decky_installer(self.win.paths.downloads)

            def event(kind: str, value: str) -> None:
                if kind == "progress":
                    report(int(value))
                else:
                    status(f"Decky Loader: {value}")
            status("Running Decky's installer…")
            try:
                rc, messages = addons.run_decky(script, option, password, temporary, event)
            except core.InstallError as e:
                if str(e) != "DECKY_CHANGED":
                    raise
                if core.in_game_mode():
                    raise core.InstallError("Decky's installer has changed since this version of Deckhand, so it "
                                            "can't run it here. Update Deckhand, or open Deckhand in Desktop Mode "
                                            "and pick Decky Loader there.") from None
                status("Decky's installer is open: follow its windows.")  # (Desktop Mode: its own windows still work)
                rc, messages = addons.run_decky_installer(script), []
            return rc, messages

        def finished(result) -> None:
            nonlocal password
            password = ""  # (no longer needed)
            rc, messages = result
            self.status.setText("")
            self.show_progress(None)
            version = addons.decky_version()
            ok = (version is None) if removing else (version is not None)
            self.refresh()
            if ok and removing:
                self.win.flash("Decky Loader is removed", ms=5000)
            elif ok:
                shown = f" {version}" if version and version != "installed" else ""
                Sheet.ask(self, "Decky Loader is ready", f"Decky Loader{shown} is installed. Restart Steam so it "
                          "shows up: STEAM button → Power → Restart Steam. Then open it from the Quick Access menu "
                          "(the ··· button).", ("Close",))
            else:
                why = "\n".join(messages) or f"Decky's installer stopped (code {rc})."
                Sheet.ask(self, "Decky Loader wasn't changed", why, ("Close",))

        self.win.run_worker(run, finished, self._failed("Decky Loader"), status=self.status.setText, kind="addon",
                            progress=self.show_progress)

    def _decky_password(self) -> tuple[str | None, bool]:
        """(the admin password to use, whether it is the temporary one), or (None, False) if they backed out."""
        if addons.admin_password_unset():
            if Sheet.ask(self, "No admin password yet",
                         "Your Deck has no admin password, and Decky needs one to install. Deckhand can set the "
                         f"temporary password \"{addons.DECKY_TEMP_PASSWORD}\" for the install and remove it again "
                         "when it is done, which is what Decky's own installer offers. Continue?",
                         ("Continue", "Cancel"), primary=0) != 0:
                return None, False
            return addons.DECKY_TEMP_PASSWORD, True
        text = "Decky's installer needs your admin password." + (
            "  (Steam's keyboard opens by itself; if it doesn't, press STEAM + X.)" if core.in_game_mode() else "")
        for attempt in range(3):
            choice, password = Sheet.ask_text(self, "Admin password", text, ("Continue", "Cancel"),
                                              placeholder="Password", secret=True)
            if choice != 0 or not password:
                return None, False
            if addons.sudo_accepts(password):
                return password, False
            text = "That password wasn't accepted. Try again."
        Sheet.ask(self, "Admin password", "That password wasn't accepted three times, so nothing was changed.",
                  ("Close",))
        return None, False

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
