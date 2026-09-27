"""Library artwork from SteamGridDB (steamgriddb.com), the community art site.

Its API needs a key: anyone gets one free with a SteamGridDB account (steamgriddb.com → Preferences →
API) and enters it in Deckhand once. Deckhand looks the program up by name and takes the most
popular still image of each kind Steam uses; what SteamGridDB doesn't have keeps Deckhand's own art.
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Callable

from . import core, updater

API = "https://www.steamgriddb.com/api/v2"
KEYS_PAGE = "https://www.steamgriddb.com/profile/preferences/api"

# Steam's kinds of artwork (artwork.STEAM_ART) → where SteamGridDB keeps them.
KINDS = {
    "p": ("grids", {"dimensions": "600x900", "mimes": "image/png,image/jpeg"}),
    "": ("grids", {"dimensions": "920x430,460x215", "mimes": "image/png,image/jpeg"}),
    "_hero": ("heroes", {"mimes": "image/png,image/jpeg"}),
    "_logo": ("logos", {"mimes": "image/png"}),
}
STILL = {"types": "static", "nsfw": "false", "humor": "false"}
PNG, JPEG = b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"


def _api(path: str, key: str, fetch: Callable[[str, str], bytes] | None) -> dict:
    try:
        raw = (fetch or (lambda url, key: updater.fetch(url, timeout=20, limit=4 << 20,
                                                        headers={"Authorization": f"Bearer {key}"})))(API + path, key)
    except updater.UpdateError as e:
        if "HTTP 401" in str(e) or "HTTP 403" in str(e):
            raise core.InstallError("SteamGridDB didn't accept the API key. Check it, or make a new one at "
                                    f"{KEYS_PAGE}.") from None
        if "HTTP 404" in str(e):  # nothing of that kind for this game
            return {}
        raise
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) and data.get("success") else {}


def check_key(key: str, fetch: Callable[[str, str], bytes] | None = None) -> None:
    """Raises InstallError if SteamGridDB doesn't accept the key."""
    _api("/search/autocomplete/portal", key, fetch)


def find_game(name: str, key: str, fetch: Callable[[str, str], bytes] | None = None) -> dict | None:
    """SteamGridDB's best match for the name: only one that shares a word with it (no art beats wrong art)."""
    words = core._words(name) - {"the", "of", "and"}
    games = _api(f"/search/autocomplete/{urllib.parse.quote(name, safe='')}", key, fetch).get("data") or []
    return next((g for g in games if isinstance(g, dict) and words & core._words(str(g.get("name", "")))), None)


def art(name: str, key: str, fetch: Callable[[str, str], bytes] | None = None,
        download: Callable[[str], bytes] | None = None) -> dict[str, bytes]:
    """{kind of Steam artwork: image file} for a program, as much as SteamGridDB has."""
    game = find_game(name, key, fetch)
    if game is None:
        return {}
    out: dict[str, bytes] = {}
    for kind, (where, params) in KINDS.items():
        query = urllib.parse.urlencode({**STILL, **params})
        for image in (_api(f"/{where}/game/{game['id']}?{query}", key, fetch).get("data") or [])[:3]:
            url = image.get("url") if isinstance(image, dict) else None
            if not url:
                continue
            try:  # (the images come from SteamGridDB's CDN: no key is sent there)
                data = (download or (lambda url: updater.fetch(url, timeout=30, limit=24 << 20)))(url)
            except Exception:  # noqa: BLE001 — try the next one
                continue
            if data.startswith((PNG, JPEG)):
                out[kind] = data
                break
    return out
