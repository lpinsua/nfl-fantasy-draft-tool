"""The ESPN adapter.

ESPN is unreachable from the development sandbox, so everything here runs
against fixtures shaped like the real v3 responses. That makes these tests a
statement about the *translation* being right, not about the live API -- which
is what `--preflight --espn` exists to check.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import espn_fixtures as ef
from draftkit import credentials
from draftkit.draftstate import DraftState, parse_draft
from draftkit.espn import EspnClient
from draftkit.league import parse_league
from draftkit.values import build_board


def client(league: dict | None = None, players: list[dict] | None = None) -> EspnClient:
    espn = EspnClient("999111", 2026, cookies={"espn_s2": "x", "SWID": "{y}"})
    espn._fetch = ef.FakeEspnTransport(league, players)      # noqa: SLF001 - test seam
    return espn


class LeagueTranslationTest(unittest.TestCase):
    def test_roster_slots_become_position_names(self):
        raw = client().league("999111")
        league = parse_league(raw)
        self.assertEqual(league.starters["QB"], 1)
        self.assertEqual(league.starters["RB"], 2)
        self.assertEqual(league.starters["WR"], 2)
        self.assertEqual(league.starters["TE"], 1)
        self.assertEqual(league.starters["K"], 1)
        self.assertEqual(league.starters["DEF"], 1)
        self.assertEqual(league.flex_slots["FLEX"], 1)
        self.assertEqual(league.bench, 6)
        self.assertEqual(league.teams, 12)

    def test_superflex_slot_is_understood(self):
        lineup = dict(ef.LINEUP_SLOT_COUNTS)
        lineup["7"] = 1                       # ESPN's OP / superflex slot
        league = parse_league(client(ef.make_league(lineup=lineup)).league("999111"))
        self.assertTrue(league.is_superflex)

    def test_a_league_with_no_roster_settings_is_an_error(self):
        broken = ef.make_league()
        broken["settings"]["rosterSettings"]["lineupSlotCounts"] = {}
        with self.assertRaises(Exception):
            client(broken).league("999111")

    def test_team_names_come_through(self):
        users = client().league_users("999111")
        self.assertEqual(len(users), 12)
        self.assertEqual(users[0]["metadata"]["team_name"], "Squad 1")


class DraftTranslationTest(unittest.TestCase):
    def test_snake_draft_settings(self):
        meta = parse_draft(client().draft("999111"))
        self.assertEqual(meta.draft_type, "snake")
        self.assertEqual(meta.teams, 12)
        self.assertEqual(meta.rounds, 15, "1+2+2+1+1+1+1 starters plus 6 bench")
        self.assertEqual(meta.pick_timer, 90)
        self.assertEqual(meta.status, "drafting")

    def test_auction_is_detected(self):
        raw = client(ef.make_league(draft_type="AUCTION")).draft("999111")
        meta = parse_draft(raw)
        self.assertTrue(meta.is_auction)
        self.assertEqual(meta.budget, 200)

    def test_draft_status_tracks_espn_flags(self):
        pre = parse_draft(client(ef.make_league(in_progress=False)).draft("999111"))
        self.assertEqual(pre.status, "pre_draft")
        done = parse_draft(
            client(ef.make_league(in_progress=False, drafted=True)).draft("999111")
        )
        self.assertEqual(done.status, "complete")

    def test_pick_order_becomes_a_draft_slot(self):
        meta = parse_draft(client().draft("999111"))
        # pickOrder is [1..12], so team 7 drafts from slot 7.
        self.assertEqual(meta.draft_order["7"], 7)

    def test_picks_are_translated_with_slots(self):
        players = ef.make_players()
        ids = [p["id"] for p in players]
        raw = ef.make_league(picks=ef.make_picks(ids, 24))
        picks = client(raw, players).draft_picks("999111")
        self.assertEqual(len(picks), 24)
        first = picks[0]
        self.assertEqual(first["pick_no"], 1)
        self.assertEqual(first["round"], 1)
        self.assertEqual(first["draft_slot"], 1)
        self.assertEqual(str(first["player_id"]), str(ids[0]))

    def test_an_undrafted_league_has_no_picks(self):
        self.assertEqual(client().draft_picks("999111"), [])


class ProjectionTest(unittest.TestCase):
    def test_uses_the_projected_season_line_only(self):
        rows = {r["player_id"]: r for r in client().projections(2026)}
        players = client().players()
        top_qb = next(p for pid, p in players.items() if p["position"] == "QB")
        pid = top_qb["player_id"]
        # 330 projected, not the 999 "actual" or the 12 weekly line.
        self.assertAlmostEqual(rows[pid]["stats"]["pts_league"], 330.0, places=1)

    def test_positions_and_teams_are_mapped(self):
        players = client().players()
        positions = {p["position"] for p in players.values()}
        self.assertEqual(positions, {"QB", "RB", "WR", "TE", "K", "DEF"})
        self.assertIn("MIA", {p["team"] for p in players.values()})

    def test_injury_status_is_translated(self):
        players = client().players()
        self.assertTrue(any(p["injury_status"] == "Out" for p in players.values()))

    def test_the_whole_board_builds_on_espn_data(self):
        espn = client()
        league = parse_league(espn.league("999111"))
        board = build_board(espn.players(), espn.projections(2026), league)
        self.assertGreater(len(board.players), 100)
        self.assertEqual(board.source, "league-scoring",
                         "ESPN pre-applies league scoring, so this is exact")
        ordered = board.ordered()
        self.assertEqual(ordered[0].overall_rank, 1)
        self.assertFalse(any(p.position in ("K", "DEF") for p in ordered[:20]))

    def test_recommendations_work_end_to_end(self):
        espn = client()
        league = parse_league(espn.league("999111"))
        board = build_board(espn.players(), espn.projections(2026), league)
        meta = parse_draft(espn.draft("999111"))
        state = DraftState(meta, league, board, my_user_id="7")
        self.assertEqual(state.my_slot, 7)

        ids = [p["id"] for p in ef.make_players()]
        picks = client(ef.make_league(picks=ef.make_picks(ids, 20)),
                       ef.make_players()).draft_picks("999111")
        state.apply_picks(picks)
        recs = state.recommendations(limit=5)
        self.assertEqual(len(recs), 5)
        for rec in recs:
            self.assertNotIn(rec.player.player_id, state.drafted)


class CredentialsTest(unittest.TestCase):
    def test_swid_braces_are_repaired(self):
        self.assertEqual(credentials.normalise_swid("ABC-123"), "{ABC-123}")
        self.assertEqual(credentials.normalise_swid("{ABC-123}"), "{ABC-123}")
        self.assertEqual(credentials.normalise_swid('"{ABC-123}"'), "{ABC-123}")

    def test_secrets_live_outside_the_repository(self):
        # The repo is public; credentials must never be written into it.
        path = credentials.secrets_path()
        repo = Path(__file__).resolve().parent.parent
        self.assertNotIn(repo, path.parents, f"{path} is inside the repo")
        self.assertIn(".config", str(path))

    def test_missing_cookies_report_as_unauthenticated(self):
        espn = EspnClient("1", 2026, cookies={})
        self.assertFalse(espn.authenticated)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class IdentityTest(unittest.TestCase):
    """Your draft slot is found by resolving the SWID to the team it owns."""

    def _client_as(self, swid: str) -> EspnClient:
        espn = EspnClient("999111", 2026, cookies={"espn_s2": "x", "SWID": swid})
        espn._fetch = ef.FakeEspnTransport()          # noqa: SLF001
        return espn

    def test_swid_resolves_to_a_team_id(self):
        user = self._client_as("{OWNER-0007}").user("lpinsua")
        self.assertEqual(user["user_id"], "7")

    def test_that_team_id_finds_the_draft_slot(self):
        espn = self._client_as("{OWNER-0007}")
        meta = parse_draft(espn.draft("999111"))
        user_id = espn.user("lpinsua")["user_id"]
        self.assertEqual(meta.draft_order.get(user_id), 7, "slot must resolve, not be blank")

    def test_swid_case_and_spacing_do_not_matter(self):
        self.assertEqual(self._client_as(" {owner-0007} ").user("me")["user_id"], "7")

    def test_a_swid_owning_no_team_is_not_fatal(self):
        user = self._client_as("{NOBODY}").user("me")
        self.assertEqual(user["user_id"], "", "reports unknown rather than crashing")


class ScoringLabelTest(unittest.TestCase):
    def _league(self, scoring: dict):
        raw = ef.make_league()
        raw["settings"]["scoringSettings"] = scoring
        return parse_league(client(raw).league("999111"))

    def test_ppr_is_recognised(self):
        self.assertIn("Full PPR", self._league({"scoringType": "PPR"}).scoring_label)

    def test_standard_is_recognised(self):
        self.assertIn("Standard", self._league({"scoringType": "STANDARD"}).scoring_label)

    def test_half_ppr_is_recognised(self):
        self.assertIn("Half PPR", self._league({"scoringType": "H_PPR"}).scoring_label)

    def test_falls_back_to_the_scoring_items(self):
        league = self._league({"scoringItems": [{"statId": 53, "points": 1.0}]})
        self.assertIn("Full PPR", league.scoring_label)

    def test_unknown_scoring_does_not_crash(self):
        # Only affects the label and which ADP column is read; ESPN has already
        # applied the real rules to the projections.
        league = self._league({"scoringType": "SOMETHING_NEW"})
        self.assertTrue(league.scoring_label)
