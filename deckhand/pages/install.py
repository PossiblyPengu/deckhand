"""The InstallPage."""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QHBoxLayout, QPlainTextEdit, QProgressBar, QVBoxLayout

from .. import core, theme
from ..widgets import ElideLabel, Steps, button, label
from .common import PAGE_MARGINS, Page


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
