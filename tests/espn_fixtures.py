"""Synthetic ESPN v3 payloads, shaped the way the real API returns them.

ESPN is unreachable from the development sandbox, so the adapter is verified
against these rather than against the live service. They encode what the real
responses look like: numeric slot and position ids, projections carrying an
``appliedTotal`` already scored for the league, and draft picks keyed by team
id rather than by draft slot.
"""

from __future__ import annotations

import math

# 1QB 2RB 2WR 1TE 1FLEX 1DEF 1K, 6 bench -- lineupSlotId -> how many.
LINEUP_SLOT_COUNTS = {
    "0": 1,    # QB
    "2": 2,    # RB
    "4": 2,    # WR
    "6": 1,    # TE
    "23": 1,   # RB/WR/TE flex
    "16": 1,   # D/ST
    "17": 1,   # K
    "20": 6,   # bench
}

POS_SHAPE = {1: ("QB", 24, 0.030), 2: ("RB", 60, 0.040),
             3: ("WR", 70, 0.035), 4: ("TE", 24, 0.055),
             5: ("K", 20, 0.010), 16: ("DEF", 20, 0.015)}

TOP_POINTS = {1: 330.0, 2: 300.0, 3: 290.0, 4: 230.0, 5: 140.0, 16: 140.0}
PRO_TEAM_IDS = [15, 2, 12, 25, 6, 21, 8, 4]      # MIA, BUF, KC, SF, DAL, PHI, DET, CIN


def make_players(count_per_pos: dict[int, int] | None = None) -> list[dict]:
    """The kona_player_info rows: {id, player:{...}}."""
    rows: list[dict] = []
    pid = 3000
    rank = 0
    for position_id, (label, count, decay) in POS_SHAPE.items():
        n = (count_per_pos or {}).get(position_id, count)
        for i in range(n):
            pid += 1
            rank += 1
            strength = math.exp(-decay * i)
            rows.append({
                "id": pid,
                "player": {
                    "id": pid,
                    "fullName": f"{label}{i:02d} Espn",
                    "defaultPositionId": position_id,
                    "proTeamId": PRO_TEAM_IDS[i % len(PRO_TEAM_IDS)],
                    "injuryStatus": "OUT" if (position_id == 2 and i == 3) else "ACTIVE",
                    "draftRanksByRankType": {
                        "PPR": {"rank": rank, "auctionValue": 0},
                    },
                    "stats": [
                        # Actual, last season -- must be ignored.
                        {"statSourceId": 0, "statSplitTypeId": 0, "appliedTotal": 999.0},
                        # Projected, whole season -- the one that counts.
                        {"statSourceId": 1, "statSplitTypeId": 0,
                         "appliedTotal": round(TOP_POINTS[position_id] * strength, 2)},
                        # Projected, single week -- must be ignored.
                        {"statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": 12.0},
                    ],
                },
            })
    return rows


def make_teams(count: int = 12) -> list[dict]:
    return [
        {"id": i, "location": "Squad", "nickname": str(i), "name": f"Squad {i}",
         "owners": [f"{{OWNER-{i:04d}}}"]}
        for i in range(1, count + 1)
    ]


def make_league(
    league_id: str = "999111",
    teams: int = 12,
    draft_type: str = "SNAKE",
    in_progress: bool = True,
    drafted: bool = False,
    picks: list[dict] | None = None,
    lineup: dict | None = None,
) -> dict:
    pick_order = list(range(1, teams + 1))
    return {
        "id": int(league_id),
        "seasonId": 2026,
        "settings": {
            "name": "ESPN Test League",
            "size": teams,
            "rosterSettings": {"lineupSlotCounts": lineup or LINEUP_SLOT_COUNTS},
            "scoringSettings": {"scoringType": "PPR"},
            "draftSettings": {
                "type": draft_type,
                "pickOrder": pick_order,
                "timePerSelection": 90,
                "auctionBudget": 200 if draft_type == "AUCTION" else 0,
                "date": 1756400000000,
            },
        },
        "teams": make_teams(teams),
        "draftDetail": {
            "inProgress": in_progress,
            "drafted": drafted,
            "picks": picks if picks is not None else [],
        },
    }


def make_picks(player_ids: list[int], count: int, teams: int = 12) -> list[dict]:
    """ESPN picks: keyed by teamId, with an overall pick number."""
    out = []
    for i in range(count):
        rnd = (i // teams) + 1
        idx = i % teams
        slot = (teams - idx) if rnd % 2 == 0 else (idx + 1)
        out.append({
            "playerId": player_ids[i],
            "teamId": slot,              # pickOrder is 1..teams, so slot == teamId
            "roundId": rnd,
            "roundPickNumber": idx + 1,
            "overallPickNumber": i + 1,
            "bidAmount": 0,
            "keeper": False,
        })
    return out


class FakeEspnTransport:
    """Stands in for the network, returning the payloads above."""

    def __init__(self, league: dict | None = None, players: list[dict] | None = None):
        self.league = league or make_league()
        self.players = players if players is not None else make_players()
        self.calls: list[list[str]] = []

    def __call__(self, views, extra="", headers=None, timeout=30.0):
        self.calls.append(list(views))
        if "kona_player_info" in views:
            return {"players": self.players}
        return self.league


# ---- in season: rosters with this season's numbers --------------------------

def season_player(pid: int, name: str, position_id: int, proj_avg: float,
                  actual_avg: float | None = None, games: int = 6,
                  injury: str = "ACTIVE", season: int = 2026) -> dict:
    """A player record as mRoster / kona_player_info return it mid-season."""
    stats = [
        # Last season's actual line -- must be ignored.
        {"seasonId": season - 1, "scoringPeriodId": 0, "statSourceId": 0,
         "statSplitTypeId": 0, "appliedTotal": 400.0, "appliedAverage": 23.5},
        {"seasonId": season, "scoringPeriodId": 0, "statSourceId": 1,
         "statSplitTypeId": 0, "appliedTotal": round(proj_avg * 17, 2),
         "appliedAverage": proj_avg},
    ]
    if actual_avg is not None and games:
        stats.append({"seasonId": season, "scoringPeriodId": 0, "statSourceId": 0,
                      "statSplitTypeId": 0, "appliedTotal": round(actual_avg * games, 2),
                      "appliedAverage": actual_avg})
    return {"id": pid, "fullName": name, "defaultPositionId": position_id,
            "proTeamId": 15, "injuryStatus": injury, "stats": stats}


# Four teams. Team 1 ("Home Team") is deep at RB and thin at WR; team 2
# ("Naber Hood") is the mirror image, so the obvious trade is an RB for a WR.
# Teams 3 and 4 are balanced filler. Rates are points per week.
SEASON_ROSTERS = {
    1: ("Home Team", [("QB", 18), ("RB", 19), ("RB", 17), ("RB", 15.5), ("RB", 13),
                      ("WR", 11), ("WR", 9), ("TE", 9), ("K", 8), ("DEF", 7)]),
    2: ("Naber Hood", [("QB", 17), ("RB", 10), ("RB", 8), ("WR", 19), ("WR", 17),
                       ("WR", 15.5), ("WR", 13), ("TE", 8), ("K", 8), ("DEF", 7)]),
    3: ("Tio TTs", [("QB", 20), ("RB", 14), ("RB", 12), ("WR", 14), ("WR", 12),
                    ("WR", 10), ("TE", 12), ("TE", 6), ("K", 8), ("DEF", 8)]),
    4: ("Squad Four", [("QB", 16), ("RB", 13), ("RB", 11), ("WR", 13), ("WR", 12),
                       ("WR", 9), ("TE", 7), ("K", 7), ("DEF", 6)]),
}
FREE_AGENTS = [("QB", 12), ("QB", 11), ("RB", 7), ("RB", 6), ("WR", 7), ("WR", 6),
               ("TE", 5), ("TE", 4), ("K", 7), ("K", 6), ("DEF", 6), ("DEF", 5)]
POSITION_ID = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "K": 5, "DEF": 16}

# 1QB 2RB 2WR 1TE 1FLEX 1K 1DEF, 6 bench, 1 IR.
SEASON_LINEUP = dict(LINEUP_SLOT_COUNTS, **{"21": 1})


def make_season(week: int = 8, deadline_ms: int | None = 1794636000000):
    """(league payload with rosters, kona player rows) for a mid-season league."""
    pid = 5000
    teams, pool = [], []
    for team_id, (name, roster) in SEASON_ROSTERS.items():
        entries = []
        for pos, rate in roster:
            pid += 1
            player = season_player(pid, f"{pos}{pid}", POSITION_ID[pos], rate,
                                   actual_avg=rate, games=week - 2)
            pool.append({"id": pid, "onTeamId": team_id, "player": player})
            entries.append({"playerId": pid, "lineupSlotId": 20,
                            "playerPoolEntry": {"id": pid, "player": player}})
        teams.append({
            "id": team_id, "name": name, "owners": [f"{{OWNER-{team_id:04d}}}"],
            "record": {"overall": {"wins": team_id, "losses": week - 1 - team_id,
                                   "ties": 0, "pointsFor": 700.0 + team_id}},
            "roster": {"entries": entries},
        })
    for pos, rate in FREE_AGENTS:
        pid += 1
        pool.append({"id": pid, "onTeamId": 0,
                     "player": season_player(pid, f"FA {pos}{pid}", POSITION_ID[pos], rate)})
    league = make_league(teams=4, in_progress=False, drafted=True, lineup=SEASON_LINEUP)
    league["teams"] = teams
    league["scoringPeriodId"] = week
    league["status"] = {"currentMatchupPeriod": week}
    league["settings"]["scheduleSettings"] = {"matchupPeriodCount": 14}
    league["settings"]["tradeSettings"] = {"deadlineDate": deadline_ms}
    return league, pool
