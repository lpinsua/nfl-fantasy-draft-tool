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

    # ---- several leagues at once -------------------------------------

    def test_named_leagues_do_not_overwrite_each_other(self):
        config.save({"league_id": "111", "provider": "sleeper"}, self.root, name="one")
        config.save({"league_id": "222", "provider": "espn", "season": "2026"},
                    self.root, name="two")
        self.assertEqual(config.load(self.root, name="one")["league_id"], "111")
        self.assertEqual(config.load(self.root, name="two")["league_id"], "222")
        self.assertEqual(sorted(config.names(self.root)), ["one", "two"])

    def test_the_first_league_saved_becomes_the_default(self):
        config.save({"league_id": "111"}, self.root, name="one")
        config.save({"league_id": "222"}, self.root, name="two")
        self.assertEqual(config.default_name(self.root), "one")
        self.assertEqual(config.load(self.root)["league_id"], "111",
                         "a bare load must stay on the default league")

    def test_set_default_switches_which_league_runs_bare(self):
        config.save({"league_id": "111"}, self.root, name="one")
        config.save({"league_id": "222"}, self.root, name="two")
        self.assertTrue(config.set_default("two", self.root))
        self.assertEqual(config.load(self.root)["league_id"], "222")

    def test_set_default_refuses_a_league_that_does_not_exist(self):
        config.save({"league_id": "111"}, self.root, name="one")
        self.assertFalse(config.set_default("nope", self.root))
        self.assertEqual(config.default_name(self.root), "one", "unchanged")

    def test_names_lists_the_default_first(self):
        config.save({"league_id": "1"}, self.root, name="zulu")
        config.save({"league_id": "2"}, self.root, name="alpha")
        self.assertEqual(config.names(self.root)[0], "zulu")

    def test_an_unknown_name_is_simply_no_defaults(self):
        config.save({"league_id": "111"}, self.root, name="one")
        self.assertEqual(config.load(self.root, name="nope"), {})

    def test_saving_one_league_does_not_disturb_another(self):
        config.save({"league_id": "111", "username": "me"}, self.root, name="one")
        config.save({"league_id": "222"}, self.root, name="two")
        config.save({"favorite_team": "MIA"}, self.root, name="two")
        self.assertEqual(config.load(self.root, name="one"),
                         {"league_id": "111", "username": "me"})

    def test_espn_only_keys_survive_a_save(self):
        # The whole point: these were silently dropped before, so a saved ESPN
        # league could never be reloaded without retyping it.
        config.save({"provider": "espn", "league_id": "884705387",
                     "season": "2026", "team_id": "17"}, self.root, name="espn")
        self.assertEqual(
            config.load(self.root, name="espn"),
            {"provider": "espn", "league_id": "884705387",
             "season": "2026", "team_id": "17"},
        )

    # ---- the old single-league format --------------------------------

    def test_a_legacy_flat_file_still_loads(self):
        config.config_path(self.root).write_text(
            '{"username": "lpinsua", "league_id": "123", "favorite_team": "MIA"}'
        )
        self.assertEqual(config.load(self.root)["league_id"], "123")

    def test_a_legacy_flat_file_is_upgraded_in_place_on_save(self):
        config.config_path(self.root).write_text('{"username": "lpinsua", "league_id": "123"}')
        config.save({"favorite_team": "MIA"}, self.root)
        saved = config.load(self.root)
        self.assertEqual(saved["username"], "lpinsua", "the old settings must survive")
        self.assertEqual(saved["favorite_team"], "MIA")
        self.assertIn("leagues", json.loads(config.config_path(self.root).read_text()))

    def test_a_league_that_is_not_an_object_is_ignored(self):
        config.config_path(self.root).write_text('{"leagues": {"one": "nonsense"}}')
        self.assertEqual(config.load(self.root), {})

    def test_shipped_config_matches_the_documented_league(self):
        # The repo's own config is what makes `python3 draft.py` work bare.
        shipped = config.load()
        self.assertEqual(shipped.get("username"), "lpinsua")
        self.assertEqual(shipped.get("league_id"), "1389723692459638784")
        self.assertEqual(shipped.get("favorite_team"), "MIA")

    def test_shipped_config_holds_the_espn_league_too(self):
        espn = config.load(name="espn")
        self.assertEqual(espn.get("provider"), "espn")
        self.assertEqual(espn.get("league_id"), "884705387")
        self.assertEqual(espn.get("season"), "2026")
        self.assertEqual(espn.get("team_id"), "17")

    def test_shipped_config_holds_no_secret_looking_keys(self):
        # Walks the nested leagues too -- ESPN's cookies must never land here.
        raw = json.loads(config.config_path().read_text())
        keys = list(raw)
        for body in (raw.get("leagues") or {}).values():
            keys.extend(body)
        for key in keys:
            self.assertNotIn("pass", key.lower())
            self.assertNotIn("token", key.lower())
            self.assertNotIn("secret", key.lower())
            self.assertNotIn("cookie", key.lower())
            self.assertNotIn("swid", key.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
