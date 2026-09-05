"""Preflight must report clearly and never traceback — it runs under time pressure."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures
from draftkit import preflight
from draftkit.api import SleeperError
from draftkit.demo import DemoClient


def run_capture(client, **kwargs) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = preflight.run(client, **kwargs)
    return code, buf.getvalue()


class DeadClient(DemoClient):
    def state(self):
        raise SleeperError("Tunnel connection failed: 403 Forbidden")


class NoProjectionsClient(DemoClient):
    def projections(self, season, max_age=0):
        return []


class NoLeagueClient(DemoClient):
    def user_leagues(self, user_id, season):
        return []


class UnreadableLeagueClient(DemoClient):
    """Reachable, but the league itself will not load.

    This is the ESPN shape of failure: its state() answers from settings without
    a request, so the first call that actually goes out is the league fetch --
    and that one used to escape as a traceback.
    """

    def league(self, league_id):
        raise SleeperError("could not reach ESPN: 403 Forbidden")


class NoProjectionsErrorClient(DemoClient):
    def projections(self, season, max_age=0):
        raise SleeperError("projections endpoint moved")


class UnknownUserClient(DemoClient):
    def user(self, username):
        return None


class PreflightTest(unittest.TestCase):
    def test_happy_path_passes(self):
        code, out = run_capture(DemoClient(), username="demo")
        self.assertEqual(code, 0)
        self.assertIn("All checks passed", out)
        self.assertNotIn("[ FAIL ]", out)

    def test_reports_what_it_detected(self):
        _, out = run_capture(DemoClient(), username="demo")
        # The point of preflight is that you can eyeball these against Sleeper.
        for expected in ("Half PPR", "teams      12", "1QB 2RB 2WR 1TE",
                         "Your draft slot is 7", "Top 10 by VORP", "Replacement level"):
            self.assertIn(expected, out)

    def test_unreachable_api_fails_cleanly(self):
        code, out = run_capture(DeadClient(), username="demo")
        self.assertEqual(code, 1)
        self.assertIn("Sleeper API unreachable", out)
        self.assertIn("403", out)
        self.assertNotIn("Traceback", out)

    def test_unknown_user_is_a_failure_not_a_crash(self):
        code, out = run_capture(UnknownUserClient(), username="nope")
        self.assertEqual(code, 1)
        self.assertIn("No such Sleeper user", out)
        self.assertNotIn("Traceback", out)

    def test_no_leagues_is_reported(self):
        code, out = run_capture(NoLeagueClient(), username="demo")
        self.assertEqual(code, 1)
        self.assertIn("No leagues found", out)

    def test_missing_projections_warns_but_still_passes(self):
        # ADP-only is degraded, not broken: preflight should say so and pass.
        code, out = run_capture(NoProjectionsClient(), username="demo")
        self.assertEqual(code, 0)
        self.assertIn("Projections unavailable", out)
        self.assertIn("Ready, with", out)

    def test_auction_is_called_out(self):
        client = DemoClient(draft_type="auction")
        code, out = run_capture(client, username="demo")
        self.assertEqual(code, 0)
        self.assertIn("AUCTION", out)
        self.assertIn("no $-value or max-bid guidance", out)
        self.assertIn("Draft slot not applicable", out)

    def test_reversal_round_is_called_out(self):
        class ReversalClient(DemoClient):
            def draft(self, draft_id):
                return fixtures.make_draft(draft_id=draft_id, reversal_round=3)

            def league_drafts(self, league_id):
                return [fixtures.make_draft(reversal_round=3)]

        code, out = run_capture(ReversalClient(), username="demo")
        self.assertEqual(code, 0)
        self.assertIn("Third-round reversal", out)

    def test_requires_an_identifier(self):
        code, out = run_capture(DemoClient())
        self.assertEqual(code, 1)
        self.assertIn("Could not determine which league", out)

    def test_an_unreadable_league_reports_instead_of_tracebacking(self):
        # If this ever raises, the test fails by erroring -- which is the point.
        code, out = run_capture(UnreadableLeagueClient(), league_id="884705387",
                                provider="ESPN")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertIn("403 Forbidden", out, "the real reason must reach the user")

    def test_failing_projections_warn_rather_than_crash(self):
        code, out = run_capture(NoProjectionsErrorClient(), username="demo")
        self.assertEqual(code, 0)
        self.assertIn("Could not load projections", out)

    def test_a_given_team_id_is_used_as_your_identity(self):
        # ESPN has no username to look up; the team id comes from the league URL.
        code, out = run_capture(DemoClient(), league_id="L1", team_id="U3",
                                provider="ESPN")
        self.assertEqual(code, 0)
        self.assertIn("Your team id", out)
        self.assertIn("Your draft slot is 3", out, "the id must reach the draft order")

    def test_espn_does_not_claim_reachability_it_has_not_tested(self):
        code, out = run_capture(DemoClient(), league_id="L1", provider="ESPN")
        self.assertNotIn("ESPN API reachable", out)
        self.assertIn("ESPN season", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
