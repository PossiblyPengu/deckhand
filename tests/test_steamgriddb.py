"""SteamGridDB lookups against a faked API."""
import json
import sys
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deckhand import core, steamgriddb, updater  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"art"
JPG = b"\xff\xd8\xff\xe0" + b"art"


class FakeSGDB:
    def __init__(self, games, images, key="good"):
        self.games, self.images, self.key, self.calls = games, images, key, []

    def fetch(self, url: str, key: str) -> bytes:
        self.calls.append(url)
        if key != self.key:
            raise updater.UpdateError("HTTP 401 for " + url)
        path = urllib.parse.urlparse(url)
        if "/search/autocomplete/" in path.path:
            return json.dumps({"success": True, "data": self.games}).encode()
        kind = path.path.split("/")[3]  # /api/v2/<kind>/game/<id>
        if kind not in self.images:
            raise updater.UpdateError("HTTP 404 for " + url)
        return json.dumps({"success": True, "data": [{"url": u} for u in self.images[kind]]}).encode()


class TestSteamGridDB(unittest.TestCase):
    def test_art_of_each_kind_is_found_by_name(self):
        api = FakeSGDB([{"id": 42, "name": "The Witcher 3: Wild Hunt"}],
                       {"grids": ["https://cdn/broken.png", "https://cdn/grid.png"], "heroes": ["https://cdn/hero.jpg"]})
        files = {"https://cdn/grid.png": PNG, "https://cdn/hero.jpg": JPG, "https://cdn/broken.png": b"<html>"}
        art = steamgriddb.art("The Witcher 3 Wild Hunt Goty", "good", api.fetch, files.__getitem__)
        self.assertEqual(art, {"p": PNG, "": PNG, "_hero": JPG})  # no logo on SteamGridDB: Deckhand draws one
        grid_query = next(c for c in api.calls if "/grids/" in c and "600x900" in c)
        self.assertIn("types=static", grid_query)
        self.assertIn("nsfw=false", grid_query)

    def test_a_match_sharing_no_word_with_the_name_is_ignored(self):
        api = FakeSGDB([{"id": 1, "name": "Something Else Entirely"}], {"grids": ["https://cdn/x.png"]})
        self.assertEqual(steamgriddb.art("Cool Game", "good", api.fetch, lambda url: PNG), {})
        self.assertEqual(len(api.calls), 1)  # nothing downloaded

    def test_a_bad_key_is_explained(self):
        api = FakeSGDB([], {})
        with self.assertRaises(core.InstallError) as ctx:
            steamgriddb.check_key("wrong", api.fetch)
        self.assertIn("API key", str(ctx.exception))
        steamgriddb.check_key("good", api.fetch)
