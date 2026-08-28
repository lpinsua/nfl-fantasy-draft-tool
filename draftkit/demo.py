"""Synthetic league data, for rehearsing the board with no network.

This is also what the test suite runs against, so the offline path and the
tested path are the same code rather than two things that drift apart.
"""

from __future__ import annotations

import math
import time

POS_SHAPE = {
    # position: (how many, decay rate down the position)
    "QB": (32, 0.030),
    "RB": (70, 0.040),
    "WR": (90, 0.035),
    "TE": (32, 0.055),
    "K": (32, 0.010),
    "DEF": (32, 0.015),
}

TEAMS = ["BUF", "MIA", "KC", "SF", "DAL", "PHI", "DET", "CIN"]

ADP_BASE = {"QB": 30, "RB": 5, "WR": 8, "TE": 40, "K": 200, "DEF": 190}
ADP_STEP = {"QB": 6, "RB": 3, "WR": 2.6, "TE": 7, "K": 0.4, "DEF": 0.5}

HALF_PPR_SCORING = {
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -1,
    "rush_yd": 0.1, "rush_td": 6,
    "rec": 0.5, "rec_yd": 0.1, "rec_td": 6,
    "fum_lost": -2,
    "fgm": 3, "xpm": 1,
    "def_td": 6, "sack": 1, "int": 2,
}


def _stat_line(pos: str, strength: float) -> dict:
    """A plausible season stat line scaled by how good the player is."""
    if pos == "QB":
        return {
            "pass_yd": 4200 * strength, "pass_td": 30 * strength, "pass_int": 11 * (2 - strength),
            "rush_yd": 260 * strength, "rush_td": 3 * strength, "fum_lost": 2.0,
        }
    if pos == "RB":
        return {
            "rush_yd": 1150 * strength, "rush_td": 9 * strength,
            "rec": 48 * strength, "rec_yd": 380 * strength, "rec_td": 2 * strength,
            "fum_lost": 1.5,
        }
    if pos == "WR":
        return {
            "rec": 95 * strength, "rec_yd": 1250 * strength, "rec_td": 8 * strength,
            "rush_yd": 30 * strength, "fum_lost": 1.0,
        }
    if pos == "TE":
        return {
            "rec": 72 * strength, "rec_yd": 850 * strength, "rec_td": 6 * strength,
            "fum_lost": 0.5,
        }
    if pos == "K":
        return {"fgm": 26 * strength, "xpm": 34 * strength}
    return {"def_td": 3 * strength, "sack": 42 * strength, "int": 14 * strength}


def make_players() -> dict[str, dict]:
    players: dict[str, dict] = {}
    pid = 1000
    for pos, (count, _) in POS_SHAPE.items():
        for i in range(count):
            pid += 1
            players[str(pid)] = {
                "player_id": str(pid),
                "first_name": f"{pos}{i:02d}",
                "last_name": "Player",
                "full_name": f"{pos}{i:02d} Player",
                "position": pos,
                "fantasy_positions": [pos],
                "team": TEAMS[i % len(TEAMS)],
                "age": 22 + (i % 12),
                "years_exp": i % 10,
                "status": "Active",
                "search_rank": pid - 1000,
                "injury_status": "Out" if (pos == "RB" and i == 3) else None,
            }
    return players


def make_projections(players: dict[str, dict]) -> list[dict]:
    """Projections mirroring Sleeper's shape: a stats dict plus ADP columns."""
    rows = []
    by_pos: dict[str, list[str]] = {}
    for pid, meta in players.items():
        by_pos.setdefault(meta["position"], []).append(pid)
    for pos, pids in by_pos.items():
        decay = POS_SHAPE[pos][1]
        for idx, pid in enumerate(sorted(pids, key=int)):
            strength = math.exp(-decay * idx)
            stats = _stat_line(pos, strength)
            stats["gp"] = 17
            adp = ADP_BASE[pos] + idx * ADP_STEP[pos]
            stats["adp_half_ppr"] = adp
            stats["adp_ppr"] = adp
            stats["adp_std"] = adp
            stats["adp_2qb"] = adp * 0.5 if pos == "QB" else adp
            stats["pts_half_ppr"] = 200 * strength
            stats["pts_ppr"] = 220 * strength
            stats["pts_std"] = 180 * strength
            rows.append({"player_id": pid, "season": "2026", "stats": stats})
    return rows


def make_league(
    league_id: str = "L1",
    teams: int = 12,
    roster_positions: list[str] | None = None,
    scoring: dict | None = None,
) -> dict:
    if roster_positions is None:
        roster_positions = (
            ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"] + ["BN"] * 6
        )
    return {
        "league_id": league_id,
        "name": "Demo League",
        "season": "2026",
        "total_rosters": teams,
        "roster_positions": roster_positions,
        "scoring_settings": dict(scoring or HALF_PPR_SCORING),
        "status": "in_season",
    }


def make_draft(
    draft_id: str = "D1",
    teams: int = 12,
    rounds: int = 15,
    draft_type: str = "snake",
    reversal_round: int = 0,
    my_user_id: str = "U7",
    my_slot: int = 7,
    budget: int = 0,
) -> dict:
    order = {f"U{s}": s for s in range(1, teams + 1)}
    order[my_user_id] = my_slot
    return {
        "draft_id": draft_id,
        "league_id": "L1",
        "status": "drafting",
        "type": draft_type,
        "season": "2026",
        "settings": {
            "teams": teams, "rounds": rounds,
            "reversal_round": reversal_round, "budget": budget,
        },
        "draft_order": order,
        "slot_to_roster_id": {str(s): s for s in range(1, teams + 1)},
    }


def make_picks(board_order: list[str], count: int, teams: int = 12) -> list[dict]:
    """The first `count` picks, taken straight down the board in snake order."""
    picks = []
    for i in range(count):
        rnd = (i // teams) + 1
        idx = i % teams
        slot = (teams - idx) if rnd % 2 == 0 else (idx + 1)
        picks.append(
            {
                "player_id": board_order[i],
                "pick_no": i + 1,
                "round": rnd,
                "draft_slot": slot,
                "roster_id": slot,
                "picked_by": f"U{slot}",
            }
        )
    return picks


class DemoClient:
    """Drop-in stand-in for SleeperClient backed by synthetic data.

    The draft advances on a timer so the board behaves like a real one:
    picks land, players disappear, and your turn comes around.
    """

    def __init__(self, start_picks: int = 0, seconds_per_pick: float = 4.0,
                 draft_type: str = "snake", teams: int = 12, rounds: int = 15):
        self.players_meta = make_players()
        self.projections_data = make_projections(self.players_meta)
        self.teams = teams
        self.rounds = rounds
        self.draft_type = draft_type
        self.start_picks = start_picks
        self.seconds_per_pick = seconds_per_pick
        self.started = time.time()
        # Filled in once the board has been built and ranked.
        self.board_order: list[str] = []

    def _pick_count(self) -> int:
        if self.seconds_per_pick <= 0:
            return self.start_picks
        elapsed = time.time() - self.started
        total = self.start_picks + int(elapsed / self.seconds_per_pick)
        return max(0, min(total, self.teams * self.rounds, len(self.board_order)))

    # ---- SleeperClient interface ---------------------------------------

    def state(self) -> dict:
        return {"season": "2026", "week": 1}

    def players(self, max_age: float = 0) -> dict[str, dict]:
        return self.players_meta

    def projections(self, season, max_age: float = 0) -> list[dict]:
        return self.projections_data

    def user(self, username: str) -> dict | None:
        return {"user_id": "U7", "display_name": username or "demo"}

    def user_leagues(self, user_id: str, season) -> list[dict]:
        return [make_league()]

    def league(self, league_id: str) -> dict:
        return make_league(league_id=league_id, teams=self.teams)

    def league_users(self, league_id: str) -> list[dict]:
        return [
            {
                "user_id": f"U{s}",
                "display_name": f"manager{s}",
                "metadata": {"team_name": f"Squad {s}"},
            }
            for s in range(1, self.teams + 1)
        ]

    def _budget(self) -> int:
        return 200 if self.draft_type == "auction" else 0

    def league_drafts(self, league_id: str) -> list[dict]:
        return [
            make_draft(
                teams=self.teams, rounds=self.rounds,
                draft_type=self.draft_type, budget=self._budget(),
            )
        ]

    def draft(self, draft_id: str) -> dict:
        return make_draft(
            draft_id=draft_id, teams=self.teams, rounds=self.rounds,
            draft_type=self.draft_type, budget=self._budget(),
        )

    def draft_picks(self, draft_id: str) -> list[dict]:
        if not self.board_order:
            return []
        picks = make_picks(self.board_order, self._pick_count(), teams=self.teams)
        if self.draft_type == "auction":
            # Auction picks carry a price and no meaningful pick order.
            for i, pick in enumerate(picks):
                pick["metadata"] = {"amount": max(1, 60 - i)}
                pick.pop("draft_slot", None)
        return picks
