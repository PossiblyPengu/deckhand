"""Background work: installs, downloads, size counts and update checks."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from .. import __version__, core, updater


class InstallThread(QThread):
    status = pyqtSignal(str)
    log = pyqtSignal(str)
    done = pyqtSignal(object)  # core.PendingInstall
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, installer: Path, paths: core.Paths, allow_no_container: bool = False, name: str = "",
                 quiet: bool = False):
        super().__init__()
        self.job = core.Installer(installer, paths, status=self.status.emit, log=self.log.emit,
                                  allow_no_container=allow_no_container, name=name, quiet=quiet)

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
