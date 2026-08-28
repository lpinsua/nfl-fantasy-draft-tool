"""Saved defaults, so draft night needs no flags."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from draftkit import config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(config.load(self.root), {})

    def test_round_trip(self):
        config.save({"username": "lpinsua", "league_id": "123", "favorite_team": "MIA"}, self.root)
        self.assertEqual(
            config.load(self.root),
            {"username": "lpinsua", "league_id": "123", "favorite_team": "MIA"},
        )

    def test_save_merges_rather_than_replaces(self):
        config.save({"username": "lpinsua", "league_id": "123"}, self.root)
        config.save({"favorite_team": "MIA"}, self.root)
        saved = config.load(self.root)
        self.assertEqual(saved["username"], "lpinsua", "existing keys must survive")
        self.assertEqual(saved["favorite_team"], "MIA")

    def test_unknown_keys_are_dropped(self):
        # Guards against anything sensitive being persisted by accident.
        config.save({"username": "lpinsua", "password": "hunter2"}, self.root)
        self.assertNotIn("password", config.load(self.root))
        self.assertNotIn("password", config.config_path(self.root).read_text())

    def test_empty_values_are_not_saved(self):
        config.save({"username": "lpinsua", "league_id": ""}, self.root)
        self.assertNotIn("league_id", config.load(self.root))

    def test_corrupt_file_degrades_to_no_defaults(self):
        config.config_path(self.root).write_text("{not json at all")
        self.assertEqual(config.load(self.root), {})

    def test_non_object_json_is_ignored(self):
        config.config_path(self.root).write_text('["a", "list"]')
        self.assertEqual(config.load(self.root), {})

    def test_shipped_config_matches_the_documented_league(self):
        # The repo's own config is what makes `python3 draft.py` work bare.
        shipped = config.load()
        self.assertEqual(shipped.get("username"), "lpinsua")
        self.assertEqual(shipped.get("league_id"), "1389723692459638784")
        self.assertEqual(shipped.get("favorite_team"), "MIA")

    def test_shipped_config_holds_no_secret_looking_keys(self):
        raw = json.loads(config.config_path().read_text())
        for key in raw:
            self.assertNotIn("pass", key.lower())
            self.assertNotIn("token", key.lower())
            self.assertNotIn("secret", key.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
