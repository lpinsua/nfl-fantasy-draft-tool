"""Draft clock fields and the manual mark / undo path."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures
from draftkit.draftstate import parse_draft
from draftkit.session import Session

NOW_MS = int(time.time() * 1000)


class ClockFieldsTest(unittest.TestCase):
    def test_clock_fields_are_parsed(self):
        raw = fixtures.make_draft()
        raw["start_time"] = NOW_MS + 3_600_000
        raw["last_picked"] = NOW_MS - 20_000
        raw["settings"]["pick_timer"] = 90
        meta = parse_draft(raw)
        self.assertEqual(meta.start_time, NOW_MS + 3_600_000)
        self.assertEqual(meta.last_picked, NOW_MS - 20_000)
        self.assertEqual(meta.pick_timer, 90)

    def test_clock_fields_default_to_zero_when_absent(self):
        # An untimed or unscheduled draft must not blow up the countdown.
        meta = parse_draft(fixtures.make_draft())
        self.assertEqual(meta.start_time, 0)
        self.assertEqual(meta.last_picked, 0)
        self.assertEqual(meta.pick_timer, 0)


class ClockClient(fixtures.DemoClient):
    """Demo client that reports a scheduled start and a pick timer."""

    def draft(self, draft_id):
        raw = super().draft(draft_id)
        raw["start_time"] = NOW_MS + 1_800_000
        raw["last_picked"] = NOW_MS - 15_000
        raw["settings"]["pick_timer"] = 60
        raw["status"] = "pre_draft"
        return raw

    def league_drafts(self, league_id):
        return [self.draft("D1")]


class LiveClockTest(unittest.TestCase):
    def setUp(self):
        self.client = ClockClient(seconds_per_pick=0)
        self.session = Session(self.client)
        self.session.connect("L1", None, "demo")
        self.session.stop()
        self.client.board_order = [p.player_id for p in self.session.board.ordered()]

    def test_live_exposes_the_clock(self):
        live = self.session.live()
        self.assertEqual(live["pick_timer"], 60)
        self.assertEqual(live["status"], "pre_draft")
        self.assertGreater(live["start_time"], live["server_now"])
        # server_now lets the browser correct for a wrong local clock.
        self.assertAlmostEqual(live["server_now"] / 1000, time.time(), delta=5)


class ManualMarkTest(unittest.TestCase):
    def setUp(self):
        self.client = fixtures.DemoClient(start_picks=12, seconds_per_pick=0)
        self.session = Session(self.client)
        self.session.connect("L1", None, "demo")
        self.session.stop()
        self.client.board_order = [p.player_id for p in self.session.board.ordered()]
        self.session.sync()

    def _first_available(self) -> str:
        live = self.session.live()
        drafted = set(live["drafted"])
        for player in self.session.board.ordered():
            if player.player_id not in drafted:
                return player.player_id
        raise AssertionError("board exhausted")

    def test_mark_then_undo_restores_the_player(self):
        pid = self._first_available()

        self.session.mark_drafted(pid, True)
        live = self.session.live()
        self.assertIn(pid, live["drafted"])
        self.assertIn(pid, live["manual"], "must be flagged as undoable")

        self.session.mark_drafted(pid, False)
        live = self.session.live()
        self.assertNotIn(pid, live["drafted"], "undo must put him back on the board")
        self.assertNotIn(pid, live["manual"])

    def test_undone_player_can_be_recommended_again(self):
        pid = self._first_available()
        self.session.mark_drafted(pid, True)
        self.assertNotIn(pid, {r["id"] for r in self.session.live()["recommendations"]})
        self.session.mark_drafted(pid, False)
        available = {p.player_id for p in self.session.state.available()}
        self.assertIn(pid, available)

    def test_real_picks_are_not_listed_as_undoable(self):
        live = self.session.live()
        self.assertEqual(live["manual"], [], "no manual marks yet")
        self.assertEqual(len(live["drafted"]), 12, "but 12 real picks are drafted")

    def test_manual_mark_is_dropped_once_sleeper_confirms_it(self):
        # Mark the player Sleeper is about to report anyway; the stand-in
        # should be cleaned up so the row stops offering a no-op undo.
        pid = self.client.board_order[12]
        self.session.mark_drafted(pid, True)
        self.assertIn(pid, self.session.live()["manual"])

        self.client.start_picks = 13  # Sleeper now reports that pick for real
        self.session.sync()

        live = self.session.live()
        self.assertIn(pid, live["drafted"], "still drafted")
        self.assertNotIn(pid, live["manual"], "but no longer ours to undo")

    def test_marks_survive_a_poll(self):
        pid = self._first_available()
        self.session.mark_drafted(pid, True)
        self.session.sync()
        self.assertIn(pid, self.session.live()["drafted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
