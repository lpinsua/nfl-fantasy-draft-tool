"""End-to-end test of the HTTP layer against a stubbed Sleeper API."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures
from draftkit.server import serve
from draftkit.session import Session


class FakeClient:
    """Stands in for SleeperClient with fixture data and a mutable pick list."""

    def __init__(self):
        self.players_meta = fixtures.make_players()
        self.projections_data = fixtures.make_projections(self.players_meta)
        self.picks: list[dict] = []
        self.calls: dict[str, int] = {}

    def _count(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1

    def state(self):
        return {"season": "2026", "week": 1}

    def players(self, max_age=0):
        self._count("players")
        return self.players_meta

    def projections(self, season, max_age=0):
        self._count("projections")
        return self.projections_data

    def user(self, username):
        return {"user_id": "U7", "display_name": username} if username else None

    def user_leagues(self, user_id, season):
        return [fixtures.make_league()]

    def league(self, league_id):
        return fixtures.make_league(league_id=league_id)

    def league_users(self, league_id):
        return [
            {"user_id": f"U{s}", "display_name": f"manager{s}",
             "metadata": {"team_name": f"Squad {s}"}}
            for s in range(1, 13)
        ]

    def league_drafts(self, league_id):
        return [fixtures.make_draft()]

    def draft(self, draft_id):
        return fixtures.make_draft(draft_id=draft_id)

    def draft_picks(self, draft_id):
        self._count("draft_picks")
        return list(self.picks)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = FakeClient()
        cls.session = Session(cls.client)
        cls.httpd = serve(cls.session, cls.client, "127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.session.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        payload = json.dumps(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        parsed = json.loads(raw) if raw and resp.getheader("Content-Type", "").startswith("application/json") else raw
        return resp.status, parsed

    # ---- ordered flow: each step builds on the previous ------------------

    def test_01_static_assets_are_served(self):
        for path in ("/", "/static/app.js", "/static/style.css"):
            status, body = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body, path)

    def test_02_path_traversal_is_blocked(self):
        status, _ = self.request("GET", "/static/../draft.py")
        self.assertIn(status, (403, 404))

    def test_03_unconnected_status(self):
        status, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertFalse(body["connected"])

    def test_04_league_lookup(self):
        status, body = self.request("GET", "/api/leagues?username=lpinsua")
        self.assertEqual(status, 200)
        self.assertEqual(body["user_id"], "U7")
        self.assertTrue(body["leagues"])
        self.assertEqual(body["leagues"][0]["draft_type"], "snake")

    def test_05_connect_detects_settings(self):
        status, body = self.request(
            "POST", "/api/connect", {"league_id": "L1", "username": "lpinsua"}
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["connected"])
        self.assertEqual(body["league"]["teams"], 12)
        self.assertIn("Half PPR", body["league"]["scoring_label"])
        self.assertEqual(body["draft"]["my_slot"], 7)
        self.assertEqual(body["draft"]["team_names"]["7"], "Squad 7")
        self.assertEqual(body["board_source"], "league-scoring")

    def test_06_board_payload_is_ranked(self):
        status, body = self.request("GET", "/api/board")
        self.assertEqual(status, 200)
        players = body["players"]
        self.assertGreater(len(players), 200)
        self.assertEqual(players[0]["rank"], 1)
        vorps = [p["vorp"] for p in players]
        self.assertEqual(vorps, sorted(vorps, reverse=True))
        for key in ("id", "name", "pos", "pts", "vorp", "adp", "tier", "pos_rank"):
            self.assertIn(key, players[0])

    def test_07_live_before_any_picks(self):
        status, body = self.request("GET", "/api/live")
        self.assertEqual(status, 200)
        self.assertEqual(body["picks_made"], 0)
        self.assertEqual(body["on_the_clock"], 1)
        self.assertEqual(body["round"], 1)
        self.assertFalse(body["is_my_turn"])
        self.assertEqual(body["next_picks"][:2], [7, 18])
        self.assertTrue(body["recommendations"])

    def test_08_picks_flow_through_to_live_state(self):
        _, board = self.request("GET", "/api/board")
        order = [p["id"] for p in board["players"]]
        type(self).drafted_order = order
        self.client.picks = fixtures.make_picks(order, 6)

        status, body = self.request("POST", "/api/sync")
        self.assertEqual(status, 200)
        self.assertEqual(body["picks_made"], 6)
        self.assertTrue(body["is_my_turn"], "slot 7 is on the clock after 6 picks")
        self.assertEqual(len(body["drafted"]), 6)
        self.assertEqual(len(body["recent"]), 6)
        self.assertEqual(body["recent"][0]["team"], "Squad 6")
        # Nothing already drafted may be recommended.
        drafted = set(body["drafted"])
        self.assertFalse({r["id"] for r in body["recommendations"]} & drafted)

    def test_09_roster_and_needs_track_my_picks(self):
        self.client.picks = fixtures.make_picks(self.drafted_order, 30)
        status, body = self.request("POST", "/api/sync")
        self.assertEqual(status, 200)
        self.assertEqual(body["round"], 3)
        # Slot 7 has picked at 7 and 18 by pick 30.
        self.assertEqual(len(body["roster"]), 2)
        self.assertEqual(sum(body["roster_counts"].values()), 2)
        self.assertEqual(sum(body["runs"].values()), 10)

    def test_10_manual_mark_removes_a_player(self):
        _, live = self.request("GET", "/api/live")
        target = live["recommendations"][0]["id"]
        status, _ = self.request("POST", "/api/mark", {"player_id": target, "drafted": True})
        self.assertEqual(status, 200)
        _, after = self.request("GET", "/api/live")
        self.assertIn(target, after["drafted"])
        self.assertNotIn(target, {r["id"] for r in after["recommendations"]})

        # ...and can be undone.
        self.request("POST", "/api/mark", {"player_id": target, "drafted": False})
        _, undone = self.request("GET", "/api/live")
        self.assertNotIn(target, undone["drafted"])

    def test_11_manual_marks_survive_a_resync(self):
        _, live = self.request("GET", "/api/live")
        target = live["recommendations"][0]["id"]
        self.request("POST", "/api/mark", {"player_id": target, "drafted": True})
        self.request("POST", "/api/sync")
        _, after = self.request("GET", "/api/live")
        self.assertIn(target, after["drafted"], "a poll must not wipe manual marks")
        self.request("POST", "/api/mark", {"player_id": target, "drafted": False})

    def test_12_slot_override(self):
        status, body = self.request("POST", "/api/slot", {"slot": 3})
        self.assertEqual(status, 200)
        self.assertEqual(body["draft"]["my_slot"], 3)
        self.request("POST", "/api/slot", {"slot": 7})

    def test_13_bad_routes_and_input(self):
        status, _ = self.request("GET", "/api/nope")
        self.assertEqual(status, 404)
        status, body = self.request("GET", "/api/leagues")
        self.assertEqual(body.get("error"), "username required")

    def test_14_heavy_reference_data_is_fetched_once(self):
        # The 5MB player file must not be refetched on every poll.
        self.assertEqual(self.client.calls.get("players"), 1)
        self.assertEqual(self.client.calls.get("projections"), 1)


def load_tests(loader, tests, pattern):
    # These steps share state, so keep them in declaration order.
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(ServerTest))
    return suite


if __name__ == "__main__":
    unittest.main(verbosity=2)
