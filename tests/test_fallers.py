"""The "Falling" panel.

Suppressing filled positions keeps "Take now" honest, but on its own it throws
away a real signal: an elite player sliding well past his ADP at a position you
have already filled. This panel keeps that visible as information.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures
from draftkit.draftstate import FALL_THRESHOLD, DraftState, parse_draft
from draftkit.league import parse_league
from draftkit.values import build_board


class FallersTest(unittest.TestCase):
    def setUp(self):
        players = fixtures.make_players()
        self.league = parse_league(fixtures.make_league())
        self.board = build_board(players, fixtures.make_projections(players), self.league)
        self.meta = parse_draft(fixtures.make_draft(my_user_id="U7", my_slot=7))
        self.order = [p.player_id for p in self.board.ordered()]

    def _state(self, mine: list[str], picks_made: int = 40) -> DraftState:
        state = DraftState(self.meta, self.league, self.board, my_user_id="U7")
        picks = [
            p for p in fixtures.make_picks(self.order, picks_made)
            if p["draft_slot"] != 7 and p["picked_by"] != "U7"
        ]
        ids = []
        for pos in mine:
            best = next(
                p for p in self.board.ordered()
                if p.position == pos and p.player_id not in ids
            )
            ids.append(best.player_id)
        picks = [p for p in picks if p["player_id"] not in ids]
        for i, pid in enumerate(ids):
            picks.append({"player_id": pid, "pick_no": 900 + i,
                          "draft_slot": 7, "roster_id": 7, "picked_by": "U7"})
        state.apply_picks(picks)
        return state

    def test_a_hidden_position_still_surfaces_when_it_slides(self):
        state = self._state(["QB", "TE"])
        self.assertIn("TE", state.saturated_positions())
        te = next(p for p in state.available() if p.position == "TE")
        te.adp = 12.0                      # a big faller

        self.assertNotIn("TE", [r.player.position for r in state.recommendations(limit=6)])
        falling = [f.player.position for f in state.fallers()]
        self.assertIn("TE", falling, "the whole point of the panel")

    def test_it_says_why_you_might_still_pass(self):
        state = self._state(["QB", "TE"])
        te = next(p for p in state.available() if p.position == "TE")
        te.adp = 12.0
        entry = next(f for f in state.fallers() if f.player.position == "TE")
        self.assertIn("already start a TE", entry.reason)
        self.assertIn("fell", entry.reason)

    def test_only_players_who_actually_slid_appear(self):
        state = self._state(["QB", "TE"])
        for entry in state.fallers():
            fell = state.on_the_clock - (entry.player.adp or 0)
            self.assertGreaterEqual(fell, FALL_THRESHOLD)

    def test_ranked_by_value_not_by_how_far_they_fell(self):
        state = self._state(["QB", "TE"])
        vorps = [f.player.vorp for f in state.fallers()]
        self.assertEqual(vorps, sorted(vorps, reverse=True))

    def test_drafted_players_never_appear(self):
        state = self._state(["QB", "TE"])
        for entry in state.fallers():
            self.assertNotIn(entry.player.player_id, state.drafted)

    def test_worthless_players_are_not_called_a_bargain(self):
        state = self._state(["QB", "TE"])
        for entry in state.fallers():
            self.assertGreater(entry.player.vorp, 0)

    def test_auctions_have_no_fallers(self):
        meta = parse_draft(fixtures.make_draft(draft_type="auction"))
        state = DraftState(meta, self.league, self.board, my_user_id="U7")
        state.apply_picks(fixtures.make_picks(self.order, 40))
        self.assertEqual(state.fallers(), [], "no pick order means no falling past it")

    def test_early_in_the_draft_nothing_has_fallen_yet(self):
        state = self._state([], picks_made=2)
        self.assertEqual(state.fallers(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
