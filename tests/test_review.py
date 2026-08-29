"""The post-draft report."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures
from draftkit import review
from draftkit.league import parse_league
from draftkit.session import Session
from draftkit.values import Player


def make_player(pos: str, points: float) -> Player:
    return Player(player_id=f"{pos}{points}", name=f"{pos} {points}",
                  position=pos, points=points, vorp=points - 100)


class BestLineupTest(unittest.TestCase):
    def setUp(self):
        self.league = parse_league(fixtures.make_league())   # 1QB 2RB 2WR 1TE 1K 1DEF 1FLEX

    def test_fills_every_starting_slot(self):
        roster = (
            [make_player("QB", 300)]
            + [make_player("RB", p) for p in (200, 190, 180)]
            + [make_player("WR", p) for p in (210, 195, 150)]
            + [make_player("TE", 160), make_player("K", 120), make_player("DEF", 110)]
        )
        starters, bench, _ = review.best_lineup(roster, self.league)
        self.assertEqual(len(starters), 9, "1+2+2+1+1+1 dedicated plus 1 flex")
        counts: dict[str, int] = {}
        for p in starters:
            counts[p.position] = counts.get(p.position, 0) + 1
        self.assertEqual(counts["QB"], 1)
        self.assertEqual(counts["RB"], 2 + (1 if counts.get("RB", 0) > 2 else 0))
        self.assertEqual(len(bench), len(roster) - 9)

    def test_flex_takes_the_best_player_left(self):
        roster = (
            [make_player("QB", 300)]
            + [make_player("RB", p) for p in (200, 190)]
            + [make_player("WR", p) for p in (210, 195)]
            + [make_player("TE", 160)]
            + [make_player("RB", 250)]      # clearly the best flex option
        )
        starters, _, _ = review.best_lineup(roster, self.league)
        self.assertIn(250.0, [p.points for p in starters])

    def test_a_short_roster_does_not_crash(self):
        starters, bench, _ = review.best_lineup([make_player("QB", 300)], self.league)
        self.assertEqual(len(starters), 1)
        self.assertEqual(bench, [])

    def test_an_empty_roster_is_an_empty_lineup(self):
        starters, bench, unfilled = review.best_lineup([], self.league)
        self.assertEqual((starters, bench), ([], []))
        self.assertEqual(len(unfilled), 9, 'every slot is empty')


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.client = fixtures.DemoClient(seconds_per_pick=0)
        self.session = Session(self.client)
        self.session.connect("L1", None, "demo")
        self.session.stop()
        self.client.board_order = [p.player_id for p in self.session.board.ordered()]
        self.client.start_picks = 12 * 15          # a completed draft
        self.session.sync()

    def test_every_team_is_ranked_once(self):
        reports = review.build_reports(
            self.session.state.picks, self.session.board.players,
            self.session.league, self.session.team_names, 12,
        )
        self.assertEqual(len(reports), 12)
        self.assertEqual(sorted(r.rank for r in reports), list(range(1, 13)))

    def test_ranking_is_by_starting_lineup_strength(self):
        reports = review.build_reports(
            self.session.state.picks, self.session.board.players,
            self.session.league, self.session.team_names, 12,
        )
        points = [r.starter_points for r in reports]
        self.assertEqual(points, sorted(points, reverse=True))

    def test_value_sign_matches_the_board(self):
        # Taken later than ADP is positive, same convention as the Val column.
        pick = review.Pick(player=Player(player_id="x", name="X", position="RB", adp=30.0),
                           pick_no=50, round_no=5)
        self.assertEqual(pick.value, 20.0)
        early = review.Pick(player=Player(player_id="y", name="Y", position="RB", adp=80.0),
                            pick_no=50, round_no=5)
        self.assertEqual(early.value, -30.0)

    def test_report_renders_the_essentials(self):
        text = review.render(self.session)
        for expected in ("DRAFT REVIEW", "YOUR DRAFT", "Grade:", "Starting lineup",
                         "<<< YOU", "Your starting lineup:"):
            self.assertIn(expected, text)

    def test_gap_to_the_best_roster_is_negative_when_behind(self):
        text = review.render(self.session)
        line = next(l for l in text.splitlines() if "vs the best roster" in l)
        rank_line = next(l for l in text.splitlines() if "Finished" in l)
        rank = int(rank_line.split()[1])
        if rank > 1:
            self.assertIn("-", line, f"ranked {rank} but the gap reads positive: {line}")

    def test_an_empty_draft_says_so_rather_than_crashing(self):
        self.client.start_picks = 0
        self.session.sync()
        self.assertIn("no picks yet", review.render(self.session))

    def test_grades_span_the_field(self):
        self.assertEqual(review._grade(1, 12), "A")
        self.assertEqual(review._grade(12, 12), "D")
        self.assertIn(review._grade(6, 12), ("B", "B+"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class WaiverFillTest(unittest.TestCase):
    """An empty starting slot is a waiver claim, not a zero."""

    def setUp(self):
        self.league = parse_league(fixtures.make_league())
        self.levels = {"QB": 213.0, "RB": 73.0, "WR": 76.0, "TE": 75.0, "K": 99.0, "DEF": 73.0}

    def test_a_missing_kicker_is_scored_at_replacement(self):
        self.assertAlmostEqual(review.waiver_fill(["K"], self.levels), 99.0)

    def test_an_empty_flex_takes_the_best_eligible_replacement(self):
        # FLEX is RB/WR/TE, so it should use the highest of those.
        self.assertAlmostEqual(review.waiver_fill(["FLEX"], self.levels), 76.0)

    def test_nothing_missing_adds_nothing(self):
        self.assertEqual(review.waiver_fill([], self.levels), 0.0)

    def test_missing_levels_do_not_crash(self):
        self.assertEqual(review.waiver_fill(["K"], {}), 0.0)

    def test_a_kicker_barely_moves_the_table(self):
        # The point of the whole mechanism: drafting a kicker should be worth
        # roughly nothing, because a waiver kicker scores roughly the same.
        roster = (
            [make_player("QB", 300)]
            + [make_player("RB", p) for p in (200, 190, 180)]
            + [make_player("WR", p) for p in (210, 195)]
            + [make_player("TE", 160), make_player("DEF", 110)]
        )
        _, _, without_k = review.best_lineup(roster, self.league)
        self.assertIn("K", without_k)
        gap = review.waiver_fill(without_k, self.levels)

        with_k = roster + [make_player("K", 104)]
        _, _, none_missing = review.best_lineup(with_k, self.league)
        self.assertNotIn("K", none_missing)
        # Drafting a kicker is worth only what he beats replacement by.
        self.assertLess(abs(104 - gap), 12, "a drafted kicker is barely an upgrade")


class RankingFairnessTest(unittest.TestCase):
    def setUp(self):
        self.client = fixtures.DemoClient(seconds_per_pick=0)
        self.session = Session(self.client)
        self.session.connect("L1", None, "demo")
        self.session.stop()
        self.client.board_order = [p.player_id for p in self.session.board.ordered()]
        self.client.start_picks = 12 * 15
        self.session.sync()

    def test_hypothetical_rank_stays_inside_the_league(self):
        # A "you would rank N instead" line must never exceed the team count.
        text = review.render(self.session)
        for line in text.splitlines():
            if "you would rank" in line:
                rank = int(line.split("you would rank")[1].split()[0])
                self.assertLessEqual(rank, 12)
                self.assertGreaterEqual(rank, 1)

    def test_waiver_fills_are_disclosed_not_hidden(self):
        text = review.render(self.session)
        self.assertIn("waiver level, not zero", text)
