"""Positions you have already filled must stop being recommended.

Found live: with a quarterback and a tight end already rostered, both kept
appearing in "Take now" while running back slots sat empty. The old weights
penalised a filled position by only 11-18%, which raw VORP walked straight
through.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures
from draftkit.draftstate import DraftState, parse_draft
from draftkit.league import parse_league, positional_need
from draftkit.values import build_board

SUPERFLEX_ROSTER = ["QB", "RB", "RB", "WR", "WR", "TE", "SUPER_FLEX", "BN", "BN"]


class NeedWeightTest(unittest.TestCase):
    def setUp(self):
        self.league = parse_league(fixtures.make_league())

    def test_missing_starter_is_maximum_need(self):
        self.assertEqual(positional_need({}, self.league)["RB"], 1.0)

    def test_backup_qb_is_nearly_worthless_in_a_one_qb_league(self):
        need = positional_need({"QB": 1}, self.league)["QB"]
        self.assertLess(need, 0.2, "you only ever start one quarterback")

    def test_second_te_is_discounted_but_not_zero(self):
        # A tight end can technically flex, so he keeps a little value --
        # but nothing like a genuinely empty starting slot.
        need = positional_need({"TE": 1}, self.league)["TE"]
        self.assertLess(need, 0.55)
        self.assertGreater(need, 0.15)

    def test_a_flex_spot_keeps_rb_and_wr_wanted(self):
        need = positional_need({"RB": 2, "WR": 2}, self.league)
        self.assertGreater(need["RB"], 0.4, "the flex can still take a back")
        self.assertGreater(need["WR"], 0.4)

    def test_depth_decays_as_the_bench_fills(self):
        league = self.league
        prev = 1.0
        for have in (2, 3, 4, 5, 6):
            now = positional_need({"RB": have}, league)["RB"]
            self.assertLessEqual(now, prev)
            prev = now
        self.assertLess(prev, 0.25, "a sixth running back is bench filler")

    def test_backup_kicker_is_worth_almost_nothing(self):
        self.assertLess(positional_need({"K": 1, "DEF": 1}, self.league)["K"], 0.1)

    def test_superflex_still_wants_a_second_quarterback(self):
        sf = parse_league(fixtures.make_league(roster_positions=SUPERFLEX_ROSTER))
        self.assertGreater(
            positional_need({"QB": 1}, sf)["QB"], 0.5,
            "in superflex a second QB is a starter, not a backup",
        )
        self.assertLess(
            positional_need({"QB": 2}, sf)["QB"], 0.2,
            "but a third one is not",
        )


class RecommendationTest(unittest.TestCase):
    def setUp(self):
        players = fixtures.make_players()
        self.league = parse_league(fixtures.make_league())
        self.board = build_board(players, fixtures.make_projections(players), self.league)
        self.meta = parse_draft(fixtures.make_draft(my_user_id="U7", my_slot=7))
        self.order = [p.player_id for p in self.board.ordered()]

    def _state_with(self, my_positions: list[str], picks_made: int = 40) -> DraftState:
        """A draft where your own roster is exactly `my_positions`."""
        state = DraftState(self.meta, self.league, self.board, my_user_id="U7")
        # Drop everything the simulated draft gave slot 7, so the roster under
        # test is exactly `my_positions` and nothing else.
        picks = [
            p for p in fixtures.make_picks(self.order, picks_made)
            if p["draft_slot"] != 7 and p["picked_by"] != "U7"
        ]
        mine = []
        for pos in my_positions:
            best = next(
                p for p in self.board.ordered()
                if p.position == pos and p.player_id not in mine
            )
            mine.append(best.player_id)
        picks = [p for p in picks if p["player_id"] not in mine]
        for i, pid in enumerate(mine):
            picks.append({"player_id": pid, "pick_no": 900 + i,
                          "draft_slot": 7, "roster_id": 7, "picked_by": "U7"})
        state.apply_picks(picks)
        return state

    def test_filled_qb_and_te_stop_crowding_the_list(self):
        state = self._state_with(["QB", "TE"])
        self.assertEqual(state.roster_counts().get("QB"), 1)
        self.assertEqual(state.roster_counts().get("TE"), 1)
        top = [r.player.position for r in state.recommendations(limit=6)]
        self.assertNotIn("QB", top, f"already have a QB, got {top}")
        self.assertNotIn("TE", top, f"already have a TE, got {top}")

    def test_empty_slots_are_what_gets_recommended(self):
        state = self._state_with(["QB", "TE"])
        top = [r.player.position for r in state.recommendations(limit=4)]
        self.assertTrue(set(top) <= {"RB", "WR"}, f"RB/WR slots are empty, got {top}")

    def test_need_outranks_raw_vorp(self):
        # The whole point: a slightly better player at a filled position must
        # lose to a slightly worse one that actually starts for you.
        state = self._state_with(["QB", "TE"])
        recs = state.recommendations(limit=8)
        top = recs[0]
        self.assertIn(top.player.position, ("RB", "WR"))
        better_elsewhere = [
            r for r in recs
            if r.player.vorp > top.player.vorp and r.player.position != top.player.position
        ]
        for rec in better_elsewhere:
            self.assertLess(rec.score, top.score)

    def test_an_empty_roster_wants_everything(self):
        state = self._state_with([])
        need = positional_need(state.roster_counts(), self.league)
        for pos in ("QB", "RB", "WR", "TE"):
            self.assertEqual(need[pos], 1.0)


class RosterAttributionTest(unittest.TestCase):
    """Autopicks can arrive without picked_by; the roster must still be whole."""

    def setUp(self):
        players = fixtures.make_players()
        self.league = parse_league(fixtures.make_league())
        self.board = build_board(players, fixtures.make_projections(players), self.league)
        self.meta = parse_draft(fixtures.make_draft(my_user_id="U7", my_slot=7))
        self.order = [p.player_id for p in self.board.ordered()]

    def test_picks_missing_picked_by_are_still_yours(self):
        state = DraftState(self.meta, self.league, self.board, my_user_id="U7")
        state.apply_picks([
            {"player_id": self.order[0], "pick_no": 7, "draft_slot": 7,
             "roster_id": 7, "picked_by": "U7"},
            # An autopick: Sleeper reports the slot but no user.
            {"player_id": self.order[1], "pick_no": 18, "draft_slot": 7,
             "roster_id": 7, "picked_by": None},
        ])
        roster = state.my_roster()
        self.assertEqual(len(roster), 2, "the autopick must not be dropped")
        self.assertEqual(sum(state.roster_counts().values()), 2)

    def test_no_duplicates_when_both_sources_agree(self):
        state = DraftState(self.meta, self.league, self.board, my_user_id="U7")
        state.apply_picks([
            {"player_id": self.order[0], "pick_no": 7, "draft_slot": 7,
             "roster_id": 7, "picked_by": "U7"},
        ])
        self.assertEqual(len(state.my_roster()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
