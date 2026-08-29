"""ESPN fantasy football, presented as if it were Sleeper.

``EspnClient`` implements the same handful of methods ``SleeperClient`` does and
returns the same shapes, so the value model, the board, the live poller, the web
UI and the review all work against ESPN without knowing about it -- the same
trick ``DemoClient`` uses for offline mode.

Two things are genuinely different from Sleeper and worth knowing:

* **Private leagues need credentials.** The ``espn_s2`` and ``SWID`` cookies from
  a logged-in browser. See ``draftkit.credentials``.
* **ESPN scores projections for you.** Each projected stat line carries an
  ``appliedTotal`` already computed under your league's own rules, so this does
  not try to reimplement ESPN's scoring from its numeric stat ids -- which would
  be a large table copied by hand and wrong in ways nobody would notice.

The v3 endpoints are undocumented but long-lived and widely used. Everything
here degrades to a clear error rather than a wrong answer.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .api import Cache, SleeperError
from .credentials import espn_cookies

log = logging.getLogger(__name__)

HOST = "https://lm-api-reads.fantasy.espn.com"
FALLBACK_HOST = "https://fantasy.espn.com"
USER_AGENT = "draftkit/1.0 (+local draft assistant)"

# lineupSlotId -> the slot name this tool uses internally.
SLOT_IDS = {
    0: "QB", 1: "QB", 2: "RB", 3: "WRRB_FLEX", 4: "WR", 5: "REC_FLEX", 6: "TE",
    7: "SUPER_FLEX", 8: "DL", 9: "DL", 10: "LB", 11: "DL", 12: "DB", 13: "DB",
    14: "DB", 15: "IDP_FLEX", 16: "DEF", 17: "K", 18: "P", 19: "HC",
    20: "BN", 21: "IR", 23: "FLEX", 24: "BN",
}

# defaultPositionId -> position.
POSITION_IDS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}

# ESPN's proTeamId -> the abbreviation Sleeper (and this tool) uses.
PRO_TEAMS = {
    0: None, 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI",
    22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WAS",
    29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

# statSourceId 1 = projected (0 is actual); statSplitTypeId 0 = whole season.
PROJECTED, SEASON_SPLIT = 1, 0


def _slot_name(slot_id: Any) -> str | None:
    try:
        return SLOT_IDS.get(int(slot_id))
    except (TypeError, ValueError):
        return None


class EspnClient:
    """A Sleeper-shaped view of one ESPN league."""

    def __init__(self, league_id: str, season: str | int, cache_dir: Path | None = None,
                 cookies: dict[str, str] | None = None):
        self.league_id = str(league_id)
        self.season = str(season)
        self.cookies = cookies if cookies is not None else espn_cookies()
        root = cache_dir or (Path.home() / ".cache" / "draftkit")
        self.cache = Cache(root)
        self._league_cache: dict | None = None

    # ---- plumbing -------------------------------------------------------

    @property
    def authenticated(self) -> bool:
        return bool(self.cookies)

    def _url(self, host: str, views: list[str], extra: str = "") -> str:
        base = (f"{host}/apis/v3/games/ffl/seasons/{self.season}"
                f"/segments/0/leagues/{self.league_id}{extra}")
        if views:
            base += "?" + urllib.parse.urlencode([("view", v) for v in views])
        return base

    def _fetch(self, views: list[str], extra: str = "",
               headers: dict[str, str] | None = None, timeout: float = 30.0) -> Any:
        last: Exception | None = None
        for host in (HOST, FALLBACK_HOST):
            url = self._url(host, views, extra)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                           "Accept": "application/json",
                                                           **(headers or {})})
            if self.cookies:
                request.add_header(
                    "Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items())
                )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise SleeperError(
                        f"ESPN refused access to league {self.league_id} (HTTP {exc.code}). "
                        "For a private league, run:  python3 draft.py --espn-login"
                    ) from exc
                if exc.code == 404:
                    raise SleeperError(
                        f"ESPN has no league {self.league_id} for {self.season}. "
                        "Check the id and the season year."
                    ) from exc
                last = exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
        raise SleeperError(f"could not reach ESPN: {last}")

    def _league_raw(self) -> dict:
        if self._league_cache is None:
            data = self._fetch(["mSettings", "mTeam", "mDraftDetail"])
            if isinstance(data, list):          # some seasons wrap it in a list
                data = data[0] if data else {}
            self._league_cache = data or {}
        return self._league_cache

    # ---- the SleeperClient interface ------------------------------------

    def state(self) -> dict:
        return {"season": self.season, "week": 1}

    def user(self, username: str) -> dict | None:
        """ESPN has no username lookup; identity comes from the cookies.

        The draft order is keyed by team id, so resolve the SWID to the team it
        owns -- otherwise your draft slot never gets found.
        """
        swid = self.cookies.get("SWID")
        if not swid:
            return None
        wanted = swid.strip().upper()
        for team in self._league_raw().get("teams") or []:
            owners = [str(o).strip().upper() for o in (team.get("owners") or [])]
            if wanted in owners:
                return {"user_id": str(team.get("id")), "display_name": username or "me"}
        # Cookies are valid but we could not match a team; the board still works,
        # it just will not know which roster is yours until a slot is given.
        log.warning("SWID %s does not own a team in league %s", swid, self.league_id)
        return {"user_id": "", "display_name": username or "me"}

    def user_leagues(self, user_id: str, season) -> list[dict]:
        return [self.league(self.league_id)]

    def league(self, league_id: str) -> dict | None:
        raw = self._league_raw()
        settings = raw.get("settings") or {}
        roster = ((settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {})

        roster_positions: list[str] = []
        for slot_id, count in sorted(roster.items(), key=lambda kv: int(kv[0])):
            name = _slot_name(slot_id)
            if not name:
                continue
            roster_positions.extend([name] * int(count or 0))
        if not roster_positions:
            raise SleeperError(
                "ESPN returned no roster settings for that league — "
                "check the league id and season."
            )

        return {
            "league_id": self.league_id,
            "name": settings.get("name") or f"ESPN league {self.league_id}",
            "season": self.season,
            "total_rosters": int(settings.get("size") or len(raw.get("teams") or []) or 12),
            "roster_positions": roster_positions,
            # ESPN pre-applies its own scoring to projections, so there is no
            # rule map to replicate. The reception value is carried anyway
            # because the label and the ADP column selection read it.
            "scoring_settings": {"rec": _reception_value(settings)},
            "status": "in_season",
        }

    def league_users(self, league_id: str) -> list[dict]:
        raw = self._league_raw()
        users = []
        for team in raw.get("teams") or []:
            name = (team.get("name")
                    or " ".join(filter(None, [team.get("location"), team.get("nickname")]))
                    or f"Team {team.get('id')}")
            users.append({
                "user_id": str(team.get("id")),
                "display_name": name.strip(),
                "metadata": {"team_name": name.strip()},
            })
        return users

    def league_drafts(self, league_id: str) -> list[dict]:
        return [self.draft(self.league_id)]

    def draft(self, draft_id: str) -> dict | None:
        raw = self._league_raw()
        settings = raw.get("settings") or {}
        draft_settings = settings.get("draftSettings") or {}
        detail = raw.get("draftDetail") or {}
        roster = ((settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {})
        teams = int(settings.get("size") or len(raw.get("teams") or []) or 12)
        rounds = int(sum(int(c or 0) for c in roster.values()) or 15)

        # ESPN draft types: 1 = snake, 2 = auction (offline drafts report 0).
        kind = draft_settings.get("type")
        draft_type = "auction" if str(kind).upper() in ("AUCTION", "2") else "snake"

        # pickOrder maps draft slot -> teamId; invert it for user -> slot.
        order: dict[str, int] = {}
        pick_order = draft_settings.get("pickOrder") or []
        for index, team_id in enumerate(pick_order, start=1):
            order[str(team_id)] = index

        status = "drafting" if detail.get("inProgress") else (
            "complete" if detail.get("drafted") else "pre_draft"
        )
        return {
            "draft_id": self.league_id,
            "league_id": self.league_id,
            "status": status,
            "type": draft_type,
            "season": self.season,
            "settings": {
                "teams": teams,
                "rounds": rounds,
                "reversal_round": 0,
                "budget": int(draft_settings.get("auctionBudget") or 0),
                "pick_timer": int(draft_settings.get("timePerSelection") or 0),
            },
            "start_time": int(draft_settings.get("date") or 0),
            "draft_order": order,
            "slot_to_roster_id": {str(i): t for i, t in enumerate(pick_order, start=1)},
        }

    def draft_picks(self, draft_id: str) -> list[dict]:
        """Live picks. Refetched every poll, so never served from cache."""
        self._league_cache = None
        raw = self._fetch(["mDraftDetail"], timeout=15.0)
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        detail = (raw or {}).get("draftDetail") or {}

        slot_for_team = {}
        draft = self.draft(self.league_id)
        for slot, team_id in (draft or {}).get("slot_to_roster_id", {}).items():
            slot_for_team[str(team_id)] = int(slot)

        picks = []
        for pick in detail.get("picks") or []:
            team_id = str(pick.get("teamId"))
            picks.append({
                "player_id": str(pick.get("playerId")),
                "pick_no": int(pick.get("overallPickNumber") or 0),
                "round": int(pick.get("roundId") or 0),
                "draft_slot": slot_for_team.get(team_id, 0),
                "roster_id": pick.get("teamId"),
                "picked_by": team_id,
                "metadata": {"amount": pick.get("bidAmount") or 0},
            })
        return [p for p in picks if p["player_id"] and p["player_id"] != "None"]

    # ---- players and projections ----------------------------------------

    def _player_rows(self, limit: int = 900) -> list[dict]:
        """The player pool with season projections, in one filtered request."""
        cached = self.cache.get(f"espn_players_{self.league_id}_{self.season}", 6 * 3600)
        if cached is not None:
            return cached

        # ESPN takes its query as a JSON header rather than query parameters.
        filt = {
            "players": {
                "limit": limit,
                "sortDraftRanks": {
                    "sortPriority": 100, "sortAsc": True, "value": "PPR",
                },
            }
        }
        rows = self._fetch(
            ["kona_player_info"],
            headers={"x-fantasy-filter": json.dumps(filt)},
            timeout=60.0,
        )
        if isinstance(rows, list):
            rows = rows[0] if rows else {}
        players = (rows or {}).get("players") or []
        self.cache.put(f"espn_players_{self.league_id}_{self.season}", players)
        return players

    def players(self, max_age: float = 0) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for row in self._player_rows():
            player = row.get("player") or {}
            pid = str(row.get("id") or player.get("id") or "")
            if not pid:
                continue
            position = POSITION_IDS.get(player.get("defaultPositionId"))
            if not position:
                continue
            out[pid] = {
                "player_id": pid,
                "full_name": player.get("fullName") or "",
                "position": position,
                "fantasy_positions": [position],
                "team": PRO_TEAMS.get(player.get("proTeamId")),
                "status": "Inactive" if player.get("injuryStatus") == "OUT" else "Active",
                "injury_status": _injury(player.get("injuryStatus")),
                "search_rank": _draft_rank(player),
            }
        if not out:
            raise SleeperError("ESPN returned no players for that league")
        return out

    def projections(self, season, max_age: float = 0) -> list[dict]:
        """Season projections, already scored under this league's own rules."""
        rows = []
        for row in self._player_rows():
            player = row.get("player") or {}
            pid = str(row.get("id") or player.get("id") or "")
            if not pid:
                continue
            total = _projected_total(player)
            if total is None:
                continue
            rank = _draft_rank(player)
            stats = {"pts_league": total}
            if rank is not None:
                # ESPN publishes a draft rank rather than an average pick; it is
                # the same ordering, which is all the model uses it for.
                for key in ("adp_half_ppr", "adp_ppr", "adp_std", "adp_2qb"):
                    stats[key] = float(rank)
            rows.append({"player_id": pid, "season": self.season, "stats": stats})
        return rows


# ESPN's scoringType, where it is reported as a plain string.
SCORING_TYPES = {"STANDARD": 0.0, "PPR": 1.0, "H_PPR": 0.5, "HALF_PPR": 0.5}
RECEPTION_STAT_ID = 53


def _reception_value(settings: dict) -> float:
    """Points per reception, for the scoring label and the ADP column.

    Only cosmetic for ESPN -- projections arrive already scored -- so an
    unknown scoring type falls back to half PPR rather than failing.
    """
    scoring = settings.get("scoringSettings") or {}
    named = SCORING_TYPES.get(str(scoring.get("scoringType") or "").upper())
    if named is not None:
        return named
    for item in scoring.get("scoringItems") or []:
        if item.get("statId") == RECEPTION_STAT_ID:
            try:
                return float(item.get("points") or 0.0)
            except (TypeError, ValueError):
                break
    return 0.5


def _injury(status: Any) -> str | None:
    mapping = {"OUT": "Out", "DOUBTFUL": "Doubtful", "QUESTIONABLE": "Questionable",
               "INJURY_RESERVE": "IR", "SUSPENSION": "Sus"}
    return mapping.get(str(status or "").upper())


def _draft_rank(player: dict) -> float | None:
    ranks = player.get("draftRanksByRankType") or {}
    for key in ("PPR", "STANDARD"):
        entry = ranks.get(key) or {}
        value = entry.get("rank")
        if value:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _projected_total(player: dict) -> float | None:
    """The season projection ESPN has already scored for this league."""
    for entry in player.get("stats") or []:
        if (entry.get("statSourceId") == PROJECTED
                and entry.get("statSplitTypeId") == SEASON_SPLIT):
            total = entry.get("appliedTotal")
            if total is not None:
                try:
                    return float(total)
                except (TypeError, ValueError):
                    return None
    return None
