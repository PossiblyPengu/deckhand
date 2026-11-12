"""Deckhand's pages, one module each; MainWindow (in app.py) switches between them."""
from __future__ import annotations

from .addons import AddonsPage
from .browser import BrowserPage
from .done import DonePage
from .home import HomePage
from .install import InstallPage
from .installed import InstalledPage
from .pick import PickPage
from .start import StartPage
from .stores import StoresPage
from .streaming import StreamingPage
from .update import UpdatePage

__all__ = ["AddonsPage", "BrowserPage", "DonePage", "HomePage", "InstallPage", "InstalledPage", "PickPage",
           "StartPage", "StoresPage", "StreamingPage", "UpdatePage"]
