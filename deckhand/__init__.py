"""Deckhand — install Windows programs, game stores and game streaming on Steam Deck, straight into Steam."""


def _version() -> str:
    """From the VERSION file at the repository's top (built into the app next to the package). Releasing is: change
    VERSION and RELEASE_NOTES.md, push to main (see .github/workflows/release.yml)."""
    import sys
    from pathlib import Path

    for base in (Path(__file__).resolve().parent.parent, Path(getattr(sys, "_MEIPASS", "") or ".")):
        f = base / "VERSION"
        if f.is_file():
            return f.read_text(encoding="utf-8").strip()
    raise RuntimeError("Deckhand's VERSION file is missing")  # (never guess: a wrong version breaks updating)


__version__ = _version()
