"""Colours, fonts and stylesheet: Arcadia's design language (github.com/PossiblyPengu/arcadia, DESIGN.md), so the
sister apps look like one suite. Two schemes: dusk (pastels on deep violet, the default next to Steam's dark UI) and
cloud (light pastels). Sized for the Deck's 1280×800 screen."""

from pathlib import Path

FONTS = Path(__file__).resolve().parent / "fonts"
BODY_FONT = "Nunito"
TITLE_FONT = "Fraunces"

SCHEMES = {
    "dusk": dict(
        BG="#1f1d2e", BG_RAISED="#2b2940", SURFACE="#37344f", SURFACE_HI="#433f5f", LINE="#3a3752",
        TEXT="#f6f1fa", TEXT_DIM="#dcd5e8", MUTED="#bdb6d0", ACCENT="#f7b2c4", ACCENT_HI="#fac6d3",
        ACCENT_DARK="#d98aa1", ACCENT_SOFT="#4a3752", ON_ACCENT="#3a1f2e", GREEN="#a8d5a2", GREEN_HI="#bfe3ba",
        ON_GREEN="#1d3a1a", DANGER="#f2a39a", RAIL="#1a1827", PILL="#f6f1fa", ON_PILL="#1f1d2e", CONSOLE="#16141f",
    ),
    "cloud": dict(
        BG="#f4eff8", BG_RAISED="#ffffff", SURFACE="#ece4f6", SURFACE_HI="#e2d7f0", LINE="#e2d9ec",
        TEXT="#3a3150", TEXT_DIM="#564c6c", MUTED="#7d7394", ACCENT="#f08fae", ACCENT_HI="#f4a8c0",
        ACCENT_DARK="#d9708f", ACCENT_SOFT="#fbe3ec", ON_ACCENT="#3a1f2e", GREEN="#86bf7f", GREEN_HI="#9fd098",
        ON_GREEN="#1d3a1a", DANGER="#c0566a", RAIL="#efe8f6", PILL="#3a3150", ON_PILL="#ffffff", CONSOLE="#faf7fc",
    ),
}
SCHEME = "dusk"


def stylesheet() -> str:
    return f"""
QWidget {{ background: transparent; color: {TEXT}; font-family: "{BODY_FONT}"; font-size: 18px; font-weight: 600; }}
QMainWindow, QDialog#page {{ background: {BG}; }}
QWidget#root {{ background: {BG}; }}
QLabel#h1 {{ font-family: "{TITLE_FONT}"; font-size: 34px; font-weight: 600; }}
QLabel#h2 {{ font-family: "{TITLE_FONT}"; font-size: 24px; font-weight: 600; color: {TEXT}; }}
QLabel#section {{ font-size: 15px; font-weight: 800; color: {MUTED}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#status {{ font-size: 22px; font-weight: 700; }}
QLabel#ok {{ font-family: "{TITLE_FONT}"; font-size: 30px; font-weight: 600; color: {GREEN_HI if SCHEME == "dusk" else ON_GREEN}; }}
QLabel#chip {{ background: {SURFACE}; color: {TEXT_DIM}; border-radius: 14px; padding: 4px 14px; font-size: 15px;
  font-weight: 800; }}
QLabel#wordmark {{ font-family: "{TITLE_FONT}"; font-size: 30px; font-weight: 600; }}
QFrame#rail {{ background: {RAIL}; border-right: 1px solid {LINE}; }}
QPushButton#station {{
  background: transparent; border: none; border-radius: 24px; margin: 0 14px;
  text-align: left; padding: 12px 18px; font-size: 19px; font-weight: 700; color: {MUTED};
}}
QPushButton#station:hover {{ color: {TEXT}; background: {SURFACE}; }}
QPushButton#station:checked {{ color: {ON_PILL}; background: {PILL}; }}
QPushButton#station:focus {{ border: 3px solid {ACCENT}; padding: 9px 15px; }}

QPushButton {{
  background: {SURFACE}; border: 3px solid {SURFACE}; border-radius: 29px;
  padding: 10px 28px; min-height: 36px; font-size: 19px; font-weight: 700;
}}
QPushButton:hover {{ background: {SURFACE_HI}; border-color: {SURFACE_HI}; }}
QPushButton:focus {{ background: {SURFACE_HI}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_DARK}; color: {ON_ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: {ON_ACCENT}; }}
QPushButton#primary:focus {{ border-color: {TEXT}; }}
QPushButton#primary:hover {{ background: {ACCENT_HI}; border-color: {ACCENT_HI}; }}
QPushButton#play {{ background: {GREEN}; border-color: {GREEN}; color: {ON_GREEN}; font-size: 22px; font-weight: 800; }}
QPushButton#play:focus, QPushButton#play:hover {{ background: {GREEN_HI}; border-color: {TEXT}; }}
QPushButton#danger {{ color: {DANGER}; }}
QPushButton#flat {{ background: transparent; border-color: transparent; color: {TEXT_DIM}; }}
QPushButton#flat:focus {{ border-color: {ACCENT}; }}

QListWidget {{
  background: {BG_RAISED}; border: 2px solid {LINE}; border-radius: 22px; padding: 8px; outline: none;
}}
QListWidget:focus {{ border-color: {ACCENT}; }}
QListWidget::item {{ padding: 12px 14px; border-radius: 16px; margin: 1px 0; border: 3px solid transparent; }}
QListWidget::item:selected {{ background: {SURFACE}; color: {TEXT}; }}
QListWidget::item:selected:focus {{ background: {SURFACE_HI}; border: 3px solid {ACCENT}; }}
QLineEdit {{
  background: {BG_RAISED}; border: 2px solid {LINE}; border-radius: 22px; padding: 10px 18px; font-size: 20px;
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QPlainTextEdit {{
  background: {CONSOLE}; border: 2px solid {LINE}; border-radius: 16px; padding: 8px;
  font-family: monospace; font-size: 13px; font-weight: 400; color: {TEXT_DIM};
}}
QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QProgressBar {{ border: none; background: {SURFACE}; border-radius: 5px; max-height: 10px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_HI}; border-radius: 3px; min-height: 40px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {BG_RAISED}; color: {TEXT}; border: 1px solid {LINE}; }}
"""


def use(scheme: str) -> None:
    """Switch every colour (and STYLE) to `scheme` ("dusk" or "cloud"; anything else is dusk)."""
    global SCHEME, STYLE
    SCHEME = scheme if scheme in SCHEMES else "dusk"
    globals().update(SCHEMES[SCHEME])
    STYLE = stylesheet()


def load_fonts() -> None:
    """Register the bundled Nunito and Fraunces (needs a Qt application)."""
    from PyQt6.QtGui import QFontDatabase

    for f in sorted(FONTS.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))


BG = BG_RAISED = SURFACE = SURFACE_HI = LINE = TEXT = TEXT_DIM = MUTED = ACCENT = ACCENT_HI = ACCENT_DARK = ""
ACCENT_SOFT = ON_ACCENT = GREEN = GREEN_HI = ON_GREEN = DANGER = RAIL = PILL = ON_PILL = CONSOLE = STYLE = ""
use("dusk")
