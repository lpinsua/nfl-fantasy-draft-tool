"""Auction drafts: the tool must degrade honestly, not invent pick timing."""

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

TIMING_WORDS = ("to last", "next pick", "ADP", "you'd get")


def auction_picks(order: list[str], count: int, teams: int = 12) -> list[dict]:
    """Auction picks: a price, an owner, and deliberately no draft_slot."""
    picks = []
    for i in range(count):
        picks.append(
            {
                "player_id": order[i],
                "pick_no": i + 1,
                "roster_id": (i % teams) + 1,
                "picked_by": f"U{(i % teams) + 1}",
                "metadata": {"amount": max(1, 60 - i)},
            }
        )
    return picks


class AuctionTest(unittest.TestCase):
    def setUp(self):
        players = fixtures.make_players()
        projections = fixtures.make_projections(players)
        self.league = parse_league(fixtures.make_league())
        self.board = build_board(players, projections, self.league)
        self.meta = parse_draft(
            fixtures.make_draft(draft_type="auction", budget=200, my_user_id="U7", my_slot=7)
        )
        self.state = DraftState(self.meta, self.league, self.board, my_user_id="U7")
        self.order = [p.player_id for p in self.board.ordered()]

    def test_auction_is_detected(self):
        self.assertTrue(self.meta.is_auction)
        self.assertEqual(self.meta.budget, 200)

    def test_snake_draft_is_not_flagged_as_auction(self):
        snake = parse_draft(fixtures.make_draft())
        self.assertFalse(snake.is_auction)

    def test_pick_timing_is_suppressed(self):
        self.state.apply_picks(auction_picks(self.order, 24))
        self.assertEqual(self.state.next_picks(), [])
        self.assertIsNone(self.state.picks_until_my_turn)
        self.assertFalse(self.state.is_my_turn)

    def test_roster_attributed_without_draft_slot(self):
        # Auction picks carry no draft_slot, so ownership must come from
        # picked_by or the tool would show an empty roster all night.
        self.state.apply_picks(auction_picks(self.order, 24))
        roster = self.state.my_roster()
        self.assertEqual(len(roster), 2, "U7 picked twice in 24 auction picks")
        self.assertEqual(sum(self.state.roster_counts().values()), 2)

    def test_spend_is_tracked(self):
        self.state.apply_picks(auction_picks(self.order, 24))
        # U7 owns picks 7 and 19 -> amounts 54 and 42.
        self.assertEqual(self.state.my_spend(), 54 + 42)

    def test_recommendations_drop_timing_terms(self):
        self.state.apply_picks(auction_picks(self.order, 24))
        recs = self.state.recommendations(limit=8)
        self.assertTrue(recs)
        for rec in recs:
            self.assertEqual(rec.vona, 0.0, "VONA is meaningless without a pick order")
            self.assertEqual(rec.survival, 1.0)
            for word in TIMING_WORDS:
                self.assertNotIn(word, rec.reason, f"timing language leaked: {rec.reason!r}")

    def test_recommendations_still_rank_and_exclude_drafted(self):
        self.state.apply_picks(auction_picks(self.order, 24))
        recs = self.state.recommendations(limit=8)
        scores = [r.score for r in recs]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for rec in recs:
            self.assertNotIn(rec.player.player_id, self.state.drafted)

    def test_value_and_needs_still_work(self):
        self.state.apply_picks(auction_picks(self.order, 24))
        recs = self.state.recommendations(limit=8)
        # Real value signal survives; only the timing half is removed.
        self.assertGreater(max(r.player.vorp for r in recs), 0)
        self.assertTrue(any("starter slot still empty" in r.reason for r in recs))

    def test_demo_client_serves_a_coherent_auction(self):
        client = fixtures.DemoClient(start_picks=24, seconds_per_pick=0, draft_type="auction")
        client.board_order = self.order
        raw = client.draft(" D1")
        self.assertEqual(raw["type"], "auction")
        self.assertEqual(raw["settings"]["budget"], 200)
        picks = client.draft_picks("D1")
        self.assertEqual(len(picks), 24)
        self.assertNotIn("draft_slot", picks[0])
        self.assertGreater(picks[0]["metadata"]["amount"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
