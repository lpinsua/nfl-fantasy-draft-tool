"""Survival odds must be measured against your real next turn.

Both bugs here were found live, mid-draft: every recommendation was claiming
"0% to last", which is never true of a player whose ADP is a couple of picks
away.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures
from draftkit.draftstate import DraftState, availability, my_pick_numbers, parse_draft
from draftkit.league import parse_league
from draftkit.values import Player, build_board


def player(adp: float, pos: str = "RB") -> Player:
    return Player(player_id="x", name="X", position=pos, adp=adp, vorp=50.0)


class SigmaTest(unittest.TestCase):
    def test_adp_spread_is_not_absurdly_tight(self):
        # A player going 20th on average is not a certainty to be gone by 34.
        odds = availability(player(20.0), at_pick=34, picks_made=21)
        self.assertGreater(odds, 0.02, "an ADP-20 player can absolutely last to 34")

    def test_player_near_his_adp_has_real_odds(self):
        # The live bug: this used to come back 0%.
        odds = availability(player(20.2), at_pick=27, picks_made=21)
        self.assertGreater(odds, 0.10)
        self.assertLess(odds, 0.60, "but he is still unlikely to make it")

    def test_odds_still_fall_off_with_distance(self):
        near = availability(player(20.0), at_pick=24, picks_made=21)
        far = availability(player(20.0), at_pick=60, picks_made=21)
        self.assertGreater(near, far)
        self.assertLess(far, 0.05, "60 picks past ADP really is gone")

    def test_a_much_later_adp_is_nearly_certain_to_last(self):
        self.assertGreater(availability(player(45.0), at_pick=27, picks_made=21), 0.9)


class HorizonTest(unittest.TestCase):
    """At the turn of a snake, two of your picks can be only a few apart."""

    def setUp(self):
        players = fixtures.make_players()
        self.league = parse_league(fixtures.make_league())
        self.board = build_board(players, fixtures.make_projections(players), self.league)
        self.meta = parse_draft(fixtures.make_draft(my_user_id="U3", my_slot=3))
        self.order = [p.player_id for p in self.board.ordered()]

    def test_turn_picks_really_are_close_together(self):
        self.assertEqual(my_pick_numbers(self.meta, 3)[:4], [3, 22, 27, 46])

    def _state_on_the_clock_at_22(self) -> DraftState:
        state = DraftState(self.meta, self.league, self.board, my_user_id="U3")
        state.apply_picks(fixtures.make_picks(self.order, 21))
        assert state.on_the_clock == 22 and state.is_my_turn
        return state

    def test_recommendations_are_not_all_hopeless(self):
        state = self._state_on_the_clock_at_22()
        recs = state.recommendations(limit=8)
        self.assertTrue(recs)
        best = max(r.survival for r in recs)
        self.assertGreater(
            best, 0.05,
            "every suggestion reading 0% to last is the bug this test exists for",
        )

    def test_horizon_is_the_next_turn_not_a_flat_round(self):
        # Slot 3 picks at 22 then 27. Measuring against 22+12=34 would make
        # everyone look gone; the real gap is five picks.
        state = self._state_on_the_clock_at_22()
        for rec in state.recommendations(limit=6):
            if rec.player.adp and rec.player.adp > state.on_the_clock:
                self.assertGreater(
                    rec.survival, 0.15,
                    f"{rec.player.name} (ADP {rec.player.adp}) should plausibly last 5 picks",
                )

    def test_mid_round_slot_has_a_wider_gap(self):
        # Slot 8 picks at 8 and 17 -- a longer wait, so lower survival than the
        # turn, on the same player.
        meta8 = parse_draft(fixtures.make_draft(my_user_id="U8", my_slot=8))
        self.assertEqual(my_pick_numbers(meta8, 8)[:3], [8, 17, 32])

    def test_auction_reports_no_pick_timing(self):
        meta = parse_draft(fixtures.make_draft(draft_type="auction"))
        state = DraftState(meta, self.league, self.board, my_user_id="U7")
        state.apply_picks(fixtures.make_picks(self.order, 21))
        for rec in state.recommendations(limit=5):
            self.assertEqual(rec.survival, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
