"""The in-season team report and trade finder, on a hand-built ESPN league."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import espn_fixtures as ef
from draftkit import trades
from draftkit.espn import _season_rates
from draftkit.league import parse_league
from test_espn import client


def snapshot(week: int = 8):
    league_raw, pool = ef.make_season(week=week)
    espn = client(league_raw, pool)
    league = parse_league(espn.league("999111"))
    return trades.build_snapshot(espn.season_snapshot(), league)


class SeasonRatesTest(unittest.TestCase):
    def test_projection_alone_before_any_games(self):
        rates = _season_rates(ef.season_player(1, "A", 2, 15.0), 2026)
        self.assertEqual(rates["rate"], 15.0)
        self.assertEqual(rates["games"], 0)

    def test_actual_average_counts_more_as_games_pile_up(self):
        early = _season_rates(ef.season_player(1, "A", 2, 10.0, actual_avg=20.0, games=2), 2026)
        late = _season_rates(ef.season_player(1, "A", 2, 10.0, actual_avg=20.0, games=12), 2026)
        self.assertEqual(early["games"], 2)
        self.assertLess(10.0, early["rate"])
        self.assertLess(early["rate"], late["rate"])
        self.assertLess(late["rate"], 20.0)

    def test_last_season_is_ignored(self):
        rates = _season_rates(ef.season_player(1, "A", 2, 12.0), 2026)
        self.assertIsNone(rates["actual_avg"], "the 2025 line must not leak in")


class SnapshotTest(unittest.TestCase):
    def test_rosters_names_and_records(self):
        snap = snapshot()
        self.assertEqual([t.name for t in snap.teams],
                         ["Home Team", "Naber Hood", "Tio TTs", "Squad Four"])
        home = snap.team("1")
        self.assertEqual(len(home.players), 10)
        self.assertEqual(home.record, "1-6")
        self.assertEqual(snap.week, 8)
        self.assertEqual(snap.regular_season_weeks, 14)

    def test_free_agents_set_the_waiver_level(self):
        snap = snapshot()
        self.assertEqual(len(snap.free_agents), len(ef.FREE_AGENTS))
        self.assertEqual(snap.waiver["RB"], 6.0, "second-best free agent, not the best")

    def test_team_names_are_found_loosely(self):
        snap = snapshot()
        self.assertEqual([t.team_id for t in snap.find("naber hood")], ["2"])
        self.assertEqual([t.team_id for t in snap.find("tio tts")], ["3"])
        self.assertEqual([t.team_id for t in snap.find("Tio")], ["3"])
        self.assertEqual(snap.find("nobody"), [])

    def test_injured_players_are_marked_down(self):
        league_raw, pool = ef.make_season()
        pool[0]["player"]["injuryStatus"] = "INJURY_RESERVE"
        entry = league_raw["teams"][0]["roster"]["entries"][0]
        entry["playerPoolEntry"]["player"]["injuryStatus"] = "INJURY_RESERVE"
        espn = client(league_raw, pool)
        snap = trades.build_snapshot(espn.season_snapshot(), parse_league(espn.league("1")))
        qb = snap.team("1").players[0]
        self.assertEqual(qb.injury_status, "IR")
        self.assertLess(qb.points, 18.0)

    def test_no_rosters_is_a_clear_error(self):
        league_raw, pool = ef.make_season()
        for team in league_raw["teams"]:
            team["roster"] = {"entries": []}
        with self.assertRaises(Exception) as caught:
            client(league_raw, pool).season_snapshot()
        self.assertIn("no rosters", str(caught.exception))


class TradeFinderTest(unittest.TestCase):
    def test_the_obvious_swap_is_found(self):
        snap = snapshot()
        deals = trades.find_trades(snap, snap.team("1"), snap.team("2"))
        self.assertTrue(deals)
        best = deals[0]
        self.assertIn("RB", {p.position for p in best.give})
        self.assertIn("WR", {p.position for p in best.get})

    def test_every_deal_helps_both_sides(self):
        snap = snapshot()
        me = snap.team("1")
        for partner in snap.teams[1:]:
            for deal in trades.find_trades(snap, me, partner):
                self.assertGreaterEqual(deal.my_gain, trades.MIN_MY_GAIN)
                self.assertGreaterEqual(deal.their_gain, trades.MIN_THEIR_GAIN)

    def test_two_for_one_cuts_the_roster_back(self):
        snap = snapshot()
        me = snap.team("1")
        partner = snap.team("2")
        give = (me.players[3],)
        get = tuple(partner.players[3:5])
        after = trades.after_trade(me.players, give, get, snap.league)
        self.assertEqual(len(after), len(me.players))

    def test_nothing_is_suggested_with_yourself_in_a_balanced_league(self):
        snap = snapshot()
        # The same team on both sides can never improve both lineups.
        self.assertEqual(trades.find_trades(snap, snap.team("4"), snap.team("4")), [])


class RenderTest(unittest.TestCase):
    def test_report_names_you_and_your_targets(self):
        snap = snapshot()
        text = trades.render(snap, "1", [snap.team("2"), snap.team("3")],
                             today=dt.date(2026, 10, 22))
        self.assertIn("<<< YOU", text)
        self.assertIn("TRADING WITH Naber Hood", text)
        self.assertIn("TRADING WITH Tio TTs", text)
        self.assertIn("You give:", text)
        self.assertIn("weak spot", text)
        self.assertIn("Trade deadline:", text)

    def test_league_scan_without_targets(self):
        snap = snapshot()
        text = trades.render(snap, "1", today=dt.date(2026, 10, 22))
        self.assertIn("BEST TRADES ACROSS THE LEAGUE", text)

    def test_unknown_team_id_lists_the_teams(self):
        text = trades.render(snapshot(), "99")
        self.assertIn("Could not find your team", text)
        self.assertIn("Naber Hood", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
