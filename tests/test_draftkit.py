"""Offline tests for the draft engine. Run: python3 -m unittest discover -s tests"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures
from draftkit.draftstate import (
    DraftState, availability, expected_best_at, my_pick_numbers, parse_draft, pick_number,
)
from draftkit.league import parse_league, positional_need, replacement_levels
from draftkit.scoring import score_stats
from draftkit.values import build_board


class TestScoring(unittest.TestCase):
    def test_dot_product_uses_league_values(self):
        stats = {"pass_yd": 5000, "pass_td": 40, "pass_int": 10}
        four_pt = score_stats(stats, {"pass_yd": 0.04, "pass_td": 4, "pass_int": -1})
        six_pt = score_stats(stats, {"pass_yd": 0.04, "pass_td": 6, "pass_int": -1})
        self.assertAlmostEqual(four_pt, 200 + 160 - 10)
        # 40 TDs at +2 points each is exactly 80 more.
        self.assertAlmostEqual(six_pt - four_pt, 80)

    def test_te_premium_scores_when_the_projection_reports_it(self):
        scoring = {"rec": 0.5, "rec_yd": 0.1, "bonus_rec_te": 0.5}
        base = score_stats({"rec": 80, "rec_yd": 900}, scoring)
        # Sleeper reports the bonus as its own stat count for TEs.
        prem = score_stats({"rec": 80, "rec_yd": 900, "bonus_rec_te": 80}, scoring)
        self.assertAlmostEqual(prem - base, 40.0)

    def test_unknown_scoring_keys_are_simply_skipped(self):
        # A league rule with no matching projected stat must not blow up or
        # silently contribute points.
        stats = {"rec": 10}
        self.assertAlmostEqual(score_stats(stats, {"rec": 1.0, "bonus_rec_te": 0.5}), 10.0)

    def test_ignores_non_stat_columns(self):
        stats = {"rec": 10, "adp_half_ppr": 3.0, "pts_ppr": 250}
        self.assertAlmostEqual(score_stats(stats, {"rec": 1.0}), 10.0)


class TestLeagueParsing(unittest.TestCase):
    def test_standard_roster(self):
        league = parse_league(fixtures.make_league())
        self.assertEqual(league.teams, 12)
        self.assertEqual(league.starters["RB"], 2)
        self.assertEqual(league.starters["WR"], 2)
        self.assertEqual(league.flex_slots["FLEX"], 1)
        self.assertEqual(league.bench, 6)
        self.assertFalse(league.is_superflex)
        self.assertIn("Half PPR", league.scoring_label)

    def test_superflex_detected(self):
        raw = fixtures.make_league(
            roster_positions=["QB", "RB", "RB", "WR", "WR", "TE", "SUPER_FLEX", "BN", "BN"]
        )
        league = parse_league(raw)
        self.assertTrue(league.is_superflex)
        self.assertIn("Superflex", league.scoring_label)

    def test_scoring_labels(self):
        full = parse_league(fixtures.make_league(scoring={**fixtures.HALF_PPR_SCORING, "rec": 1.0}))
        self.assertIn("Full PPR", full.scoring_label)
        std = parse_league(fixtures.make_league(scoring={**fixtures.HALF_PPR_SCORING, "rec": 0.0}))
        self.assertIn("Standard", std.scoring_label)


class TestReplacementLevels(unittest.TestCase):
    def setUp(self):
        # 100 descending values per position.
        self.pools = {
            pos: [300.0 - i * 2 for i in range(100)]
            for pos in ("QB", "RB", "WR", "TE", "K", "DEF")
        }

    def test_dedicated_slots_only(self):
        league = parse_league(
            fixtures.make_league(roster_positions=["QB", "RB", "RB", "WR", "WR", "TE"])
        )
        levels = replacement_levels(self.pools, league)
        # 12 teams x 2 RB = the 25th RB (index 24) is replacement level.
        self.assertAlmostEqual(levels["RB"], self.pools["RB"][24])
        self.assertAlmostEqual(levels["QB"], self.pools["QB"][12])

    def test_flex_pushes_replacement_deeper(self):
        without = parse_league(
            fixtures.make_league(roster_positions=["QB", "RB", "RB", "WR", "WR", "TE"])
        )
        with_flex = parse_league(
            fixtures.make_league(roster_positions=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"])
        )
        a = replacement_levels(self.pools, without)
        b = replacement_levels(self.pools, with_flex)
        flexable = ("RB", "WR", "TE")
        # A flex slot consumes 12 more RB/WR/TE, so the pool as a whole is
        # drawn down further and no baseline may rise.
        self.assertLess(sum(b[p] for p in flexable), sum(a[p] for p in flexable))
        for pos in flexable:
            self.assertLessEqual(b[pos], a[pos])
        # With equal pools the deepest position absorbs the flex: here TE has
        # only 12 starters gone vs 24 for RB/WR, so TE is the one that moves.
        self.assertLess(b["TE"], a["TE"])

    def test_superflex_raises_qb_replacement_bar(self):
        normal = parse_league(fixtures.make_league(roster_positions=["QB", "RB", "WR", "TE"]))
        sf = parse_league(
            fixtures.make_league(roster_positions=["QB", "RB", "WR", "TE", "SUPER_FLEX"])
        )
        a = replacement_levels(self.pools, normal)
        b = replacement_levels(self.pools, sf)
        self.assertLess(b["QB"], a["QB"], "superflex should consume more QBs")

    def test_never_indexes_past_the_pool(self):
        league = parse_league(fixtures.make_league(teams=12))
        tiny = {"QB": [10.0, 9.0], "RB": [8.0], "WR": [7.0], "TE": [6.0], "K": [5.0], "DEF": [4.0]}
        levels = replacement_levels(tiny, league)
        self.assertEqual(levels["RB"], 8.0)


class TestSnakeMath(unittest.TestCase):
    def test_snake_order(self):
        meta = parse_draft(fixtures.make_draft(teams=12, rounds=15))
        self.assertEqual(pick_number(meta, 1, 1), 1)
        self.assertEqual(pick_number(meta, 1, 12), 12)
        # Round 2 reverses: slot 12 picks first.
        self.assertEqual(pick_number(meta, 2, 12), 13)
        self.assertEqual(pick_number(meta, 2, 1), 24)
        self.assertEqual(pick_number(meta, 3, 1), 25)

    def test_linear_order(self):
        meta = parse_draft(fixtures.make_draft(draft_type="linear", teams=10))
        self.assertEqual(pick_number(meta, 2, 1), 11)
        self.assertEqual(pick_number(meta, 3, 1), 21)

    def test_third_round_reversal(self):
        meta = parse_draft(fixtures.make_draft(teams=12, reversal_round=3))
        # With 3RR, slot 1 picks 1st and 24th, then again at 25 -> no,
        # round 3 flips so slot 12 keeps the turn.
        self.assertEqual(pick_number(meta, 2, 1), 24)
        self.assertEqual(pick_number(meta, 3, 1), 36)
        self.assertEqual(pick_number(meta, 3, 12), 25)

    def test_every_pick_number_is_unique(self):
        meta = parse_draft(fixtures.make_draft(teams=12, rounds=15))
        seen = []
        for slot in range(1, 13):
            seen.extend(my_pick_numbers(meta, slot))
        self.assertEqual(sorted(seen), list(range(1, 181)))

    def test_reversal_pick_numbers_also_unique(self):
        meta = parse_draft(fixtures.make_draft(teams=12, rounds=15, reversal_round=3))
        seen = []
        for slot in range(1, 13):
            seen.extend(my_pick_numbers(meta, slot))
        self.assertEqual(sorted(seen), list(range(1, 181)))


class TestAvailability(unittest.TestCase):
    def _player(self, adp):
        from draftkit.values import Player
        return Player(player_id="x", name="X", position="RB", adp=adp, vorp=50.0)

    def test_falls_off_with_distance(self):
        p = self._player(20.0)
        near = availability(p, 22, picks_made=10)
        far = availability(p, 45, picks_made=10)
        self.assertGreater(near, far)
        self.assertTrue(0.0 <= far <= near <= 1.0)

    def test_already_on_the_clock_is_certain(self):
        p = self._player(20.0)
        self.assertEqual(availability(p, 10, picks_made=15), 1.0)

    def test_faller_is_not_treated_as_already_gone(self):
        # ADP 20 but still there at pick 60: the model must not report ~0%.
        p = self._player(20.0)
        self.assertGreater(availability(p, 62, picks_made=60), 0.2)

    def test_expected_best_is_bounded_by_the_best_player(self):
        from draftkit.values import Player
        group = [
            Player(player_id=str(i), name=f"P{i}", position="RB", adp=10.0 + i * 5, vorp=100 - i * 5)
            for i in range(10)
        ]
        expected, who = expected_best_at(group, at_pick=30, picks_made=20)
        self.assertLessEqual(expected, 100.0)
        self.assertGreater(expected, 0.0)
        self.assertIsNotNone(who)


class TestBoard(unittest.TestCase):
    def setUp(self):
        self.players = fixtures.make_players()
        self.projections = fixtures.make_projections(self.players)
        self.league = parse_league(fixtures.make_league())
        self.board = build_board(self.players, self.projections, self.league)

    def test_uses_league_scoring(self):
        self.assertEqual(self.board.source, "league-scoring")

    def test_ordering_and_ranks(self):
        ordered = self.board.ordered()
        self.assertGreater(len(ordered), 100)
        self.assertEqual(ordered[0].overall_rank, 1)
        vorps = [p.vorp for p in ordered]
        self.assertEqual(vorps, sorted(vorps, reverse=True))

    def test_kickers_are_not_top_of_the_board(self):
        top20 = self.board.ordered()[:20]
        self.assertFalse(any(p.position in ("K", "DEF") for p in top20))

    def test_tiers_increase_down_the_board(self):
        rbs = sorted(
            [p for p in self.board.players.values() if p.position == "RB"],
            key=lambda p: -p.points,
        )
        tiers = [p.tier for p in rbs]
        self.assertEqual(tiers, sorted(tiers), "tiers must be monotonic within a position")
        self.assertGreater(max(tiers), 1, "expected more than one RB tier")

    def test_injury_discount_applied(self):
        injured = [p for p in self.board.players.values() if p.injury_status == "Out"]
        self.assertTrue(injured)
        peers = sorted(
            [p for p in self.board.players.values() if p.position == "RB"],
            key=lambda p: p.player_id,
        )
        idx = peers.index(injured[0])
        # The discounted player should now trail the player just above them.
        self.assertLess(injured[0].points, peers[idx - 1].points)

    def test_replacement_level_players_have_near_zero_vorp(self):
        for pos in ("RB", "WR"):
            group = sorted(
                [p for p in self.board.players.values() if p.position == pos],
                key=lambda p: -p.points,
            )
            self.assertTrue(any(abs(p.vorp) < 1e-6 for p in group))

    def test_falls_back_to_adp_without_projections(self):
        board = build_board(self.players, [], self.league)
        self.assertEqual(board.source, "adp")
        self.assertTrue(board.notes)
        ordered = board.ordered()
        self.assertGreater(ordered[0].points, ordered[-1].points)

    def test_superflex_raises_qb_value(self):
        sf_league = parse_league(
            fixtures.make_league(
                roster_positions=["QB", "RB", "RB", "WR", "WR", "TE", "SUPER_FLEX", "BN", "BN"]
            )
        )
        sf_board = build_board(self.players, self.projections, sf_league)
        best_qb_std = max(p.vorp for p in self.board.players.values() if p.position == "QB")
        best_qb_sf = max(p.vorp for p in sf_board.players.values() if p.position == "QB")
        self.assertGreater(best_qb_sf, best_qb_std)


class TestDraftState(unittest.TestCase):
    def setUp(self):
        players = fixtures.make_players()
        projections = fixtures.make_projections(players)
        self.league = parse_league(fixtures.make_league())
        self.board = build_board(players, projections, self.league)
        self.meta = parse_draft(fixtures.make_draft(my_slot=7))
        self.state = DraftState(self.meta, self.league, self.board, my_user_id="U7")
        self.order = [p.player_id for p in self.board.ordered()]

    def test_slot_detected_from_draft_order(self):
        self.assertEqual(self.state.my_slot, 7)

    def test_tracks_picks_and_rosters(self):
        self.state.apply_picks(fixtures.make_picks(self.order, 24))
        self.assertEqual(self.state.picks_made, 24)
        self.assertEqual(self.state.on_the_clock, 25)
        self.assertEqual(self.state.current_round, 3)
        self.assertEqual(len(self.state.my_roster()), 2)
        self.assertTrue(self.state.drafted)

    def test_drafted_players_leave_the_pool(self):
        self.state.apply_picks(fixtures.make_picks(self.order, 10))
        available_ids = {p.player_id for p in self.state.available()}
        self.assertFalse(available_ids & self.state.drafted)

    def test_next_pick_timing(self):
        self.state.apply_picks(fixtures.make_picks(self.order, 6))
        self.assertEqual(self.state.on_the_clock, 7)
        self.assertTrue(self.state.is_my_turn)
        self.assertEqual(self.state.picks_until_my_turn, 0)
        self.state.apply_picks(fixtures.make_picks(self.order, 7))
        # Slot 7 in a 12-team snake picks 7, then 18.
        self.assertEqual(self.state.my_next_pick, 18)
        self.assertEqual(self.state.picks_until_my_turn, 10)

    def test_recommendations_are_available_and_sorted(self):
        self.state.apply_picks(fixtures.make_picks(self.order, 30))
        recs = self.state.recommendations(limit=8)
        self.assertEqual(len(recs), 8)
        scores = [r.score for r in recs]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for rec in recs:
            self.assertNotIn(rec.player.player_id, self.state.drafted)

    def test_kickers_suppressed_until_the_end(self):
        self.state.apply_picks(fixtures.make_picks(self.order, 30))
        early = self.state.recommendations(limit=10)
        self.assertFalse(any(r.player.position in ("K", "DEF") for r in early))

    def test_kickers_surface_in_the_final_rounds(self):
        # 14 of 15 rounds done -> K/DEF should stop being penalised.
        self.state.apply_picks(fixtures.make_picks(self.order, 12 * 14))
        late = self.state.recommendations(limit=12)
        self.assertTrue(any(r.player.position in ("K", "DEF") for r in late))

    def test_empty_starting_slot_drives_need(self):
        self.state.apply_picks(fixtures.make_picks(self.order, 30))
        need = positional_need(self.state.roster_counts(), self.league)
        counts = self.state.roster_counts()
        for pos, required in self.league.starters.items():
            if counts.get(pos, 0) < required:
                self.assertEqual(need[pos], 1.0)

    def test_position_runs_counted(self):
        self.state.apply_picks(fixtures.make_picks(self.order, 20))
        runs = self.state.position_runs(window=10)
        self.assertEqual(sum(runs.values()), 10)

    def test_recommendations_survive_an_empty_board(self):
        self.state.apply_picks(fixtures.make_picks(self.order, len(self.order)))
        self.assertEqual(self.state.recommendations(), [])

    def test_manual_picks_without_a_slot_do_not_crash(self):
        state = DraftState(self.meta, self.league, self.board, my_user_id="nobody")
        state.my_slot = None
        state.apply_picks(fixtures.make_picks(self.order, 5))
        self.assertEqual(state.next_picks(), [])
        self.assertIsNone(state.picks_until_my_turn)
        self.assertTrue(state.recommendations())


class TestCsvImport(unittest.TestCase):
    def test_matches_names_loosely(self):
        import tempfile
        from draftkit.csvimport import load_overrides, normalize

        self.assertEqual(normalize("A.J. Brown"), normalize("AJ Brown"))
        self.assertEqual(normalize("Kenneth Walker III"), "kenneth walker")

        players = {"9": {"full_name": "A.J. Brown", "position": "WR"}}
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write("Player,FPTS,ADP\nAJ Brown,255.5,14\n")
            path = Path(fh.name)
        overrides = load_overrides(path, players)
        path.unlink()
        self.assertEqual(overrides["9"]["points"], 255.5)
        self.assertEqual(overrides["9"]["adp"], 14.0)

    def test_rejects_csv_without_names(self):
        import tempfile
        from draftkit.csvimport import load_overrides

        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write("foo,bar\n1,2\n")
            path = Path(fh.name)
        with self.assertRaises(ValueError):
            load_overrides(path, {})
        path.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
