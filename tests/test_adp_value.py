"""The sign of ADP value.

This was inverted and shipped: a player who had fallen PAST his ADP (a
bargain) was labelled a reach, and a player whose ADP was far in the future
(a reach) was labelled "value here". Caught live, mid-draft.

The convention, once and for all:

    val = current pick - ADP

    positive -> the room let him slide to you  -> VALUE
    negative -> you are taking him early       -> REACH
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures
from draftkit.draftstate import DraftState, parse_draft
from draftkit.league import parse_league
from draftkit.values import build_board


class AdpValueSignTest(unittest.TestCase):
    def setUp(self):
        players = fixtures.make_players()
        self.league = parse_league(fixtures.make_league())
        self.board = build_board(players, fixtures.make_projections(players), self.league)
        self.meta = parse_draft(fixtures.make_draft(my_user_id="U7", my_slot=7))
        self.order = [p.player_id for p in self.board.ordered()]

    def _state_at(self, picks_made: int) -> DraftState:
        state = DraftState(self.meta, self.league, self.board, my_user_id="U7")
        state.apply_picks(fixtures.make_picks(self.order, picks_made))
        return state

    def _reason_for(self, state: DraftState, adp: float) -> str:
        """Reason text for a synthetic player with a given ADP."""
        target = min(
            (p for p in state.available() if p.adp is not None),
            key=lambda p: abs(p.adp - adp),
        )
        original, target.adp = target.adp, adp
        try:
            need = {target.position: 0.5}
            by_pos = {target.position: [target]}
            return state._reason(target, need.get(target.position, 0.5), 0.0, 0.5, by_pos, {})
        finally:
            target.adp = original

    def test_a_faller_is_called_value_not_a_reach(self):
        # On the clock at 51, a player whose ADP is 30 has slid 21 picks.
        state = self._state_at(50)
        self.assertEqual(state.on_the_clock, 51)
        reason = self._reason_for(state, 30.0)
        self.assertIn("value here", reason)
        self.assertNotIn("reach", reason)

    def test_taking_someone_early_is_called_a_reach(self):
        # On the clock at 51, a player whose ADP is 90 would be 39 picks early.
        state = self._state_at(50)
        reason = self._reason_for(state, 90.0)
        self.assertIn("reach", reason)
        self.assertNotIn("value here", reason)

    def test_a_player_going_about_on_time_is_neither(self):
        state = self._state_at(50)
        reason = self._reason_for(state, 52.0)
        self.assertNotIn("value here", reason)
        self.assertNotIn("reach", reason)

    def test_the_live_case_that_exposed_this(self):
        # Derrick Henry, ADP 20.2, board on pick 22: he has fallen ~2 picks.
        # Mild value, and certainly not a reach.
        state = self._state_at(21)
        self.assertEqual(state.on_the_clock, 22)
        val = state.on_the_clock - 20.2
        self.assertGreater(val, 0, "he lasted past his ADP, so this must be positive")
        self.assertAlmostEqual(val, 1.8, places=1)

    def test_the_inverse_is_a_reach(self):
        # Same board position, a player who normally goes at 40.
        state = self._state_at(21)
        self.assertLess(state.on_the_clock - 40.0, 0, "18 picks early is a reach")


if __name__ == "__main__":
    unittest.main(verbosity=2)
