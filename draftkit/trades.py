"""In-season team report and trade finder.

Everything is measured in points per week from here on, and judged the same way
the draft review judges a roster: by the best starting lineup it can field. A
trade is only worth suggesting when it lifts *both* lineups -- an offer that
only helps you is one the other manager declines, and listing it wastes your
week.

Provider-agnostic: it reads the snapshot ``EspnClient.season_snapshot()``
returns, and anything else that hands over the same shape can use it too.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from itertools import combinations

from .league import FLEX_ELIGIBILITY, LeagueSettings, replacement_levels
from .review import best_lineup, waiver_fill
from .values import INJURY_PENALTY, Player

# Only these change hands in practice; nobody trades for a kicker.
TRADE_POSITIONS = ("QB", "RB", "WR", "TE")

# A backup who beats the waiver wire is insurance against byes and injuries.
# Worth something, but nowhere near a starter -- otherwise hoarding depth would
# look like winning a trade.
DEPTH_CREDIT = 0.15

# How deep into each roster to look for trade pieces. Past this they are
# players nobody would take in a deal anyway.
POOL_PER_TEAM = 12

# Smallest gains worth bothering a league-mate with, in points per week. Theirs
# has to be big enough for them to notice, or they have no reason to say yes.
MIN_MY_GAIN = 0.5
MIN_THEIR_GAIN = 1.0

# How lopsided the names on each side may look before the other manager balks,
# whatever it does for their lineup. Market value is points above replacement.
MAX_MARKET_TILT = 1.35


@dataclass
class Team:
    team_id: str
    name: str
    players: list[Player] = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}" + (f"-{self.ties}" if self.ties else "")


@dataclass
class Deal:
    partner: Team
    give: tuple[Player, ...]
    get: tuple[Player, ...]
    my_gain: float
    their_gain: float
    give_market: float
    get_market: float

    @property
    def score(self) -> float:
        """Mostly your gain, some of theirs, and simpler deals first.

        Every extra player is one more thing to argue about, so a one-for-one
        beats a two-for-two that does about the same.
        """
        extra = len(self.give) + len(self.get) - 2
        return self.my_gain + 0.5 * self.their_gain - 0.5 * extra

    @property
    def balance(self) -> str:
        """How it looks to someone reading the names rather than the lineups."""
        give, get = self.give_market, self.get_market
        if give <= 0 and get <= 0:
            return "even on paper"
        if get > give * 1.15:
            return "you get the bigger names"
        if give > get * 1.15:
            return "you give up the bigger names"
        return "even on paper"


@dataclass
class Snapshot:
    league: LeagueSettings
    teams: list[Team]
    free_agents: list[Player]
    week: int
    regular_season_weeks: int
    trade_deadline: int | None
    waiver: dict[str, float]          # what the wire offers at each position
    market: dict[str, float]          # league-wide replacement level, for "names"

    def team(self, team_id: str) -> Team | None:
        return next((t for t in self.teams if t.team_id == str(team_id)), None)

    def find(self, name: str) -> list[Team]:
        """Teams whose name contains this, ignoring case and spacing."""
        wanted = _squash(name)
        exact = [t for t in self.teams if _squash(t.name) == wanted]
        return exact or [t for t in self.teams if wanted and wanted in _squash(t.name)]


def _squash(text: str) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


# ---- building the snapshot --------------------------------------------------

def _player(meta: dict) -> Player:
    player = Player(
        player_id=str(meta.get("player_id")),
        name=str(meta.get("full_name") or meta.get("player_id")),
        position=str(meta.get("position") or ""),
        team=meta.get("team"),
        injury_status=meta.get("injury_status") or None,
        points=float(meta.get("rate") or 0.0),
        source="season",
    )
    factor = INJURY_PENALTY.get((player.injury_status or "").strip())
    if factor:
        player.points *= factor
    return player


def build_snapshot(raw: dict, league: LeagueSettings) -> Snapshot:
    players = {pid: _player(meta) for pid, meta in (raw.get("players") or {}).items()}
    rostered: set[str] = set()
    teams = []
    for entry in raw.get("teams") or []:
        roster = [players[pid] for pid in entry.get("players") or [] if pid in players]
        rostered.update(p.player_id for p in roster)
        teams.append(Team(
            team_id=str(entry.get("team_id")),
            name=str(entry.get("name") or f"Team {entry.get('team_id')}"),
            players=roster,
            wins=int(entry.get("wins") or 0),
            losses=int(entry.get("losses") or 0),
            ties=int(entry.get("ties") or 0),
            points_for=float(entry.get("points_for") or 0.0),
        ))
    free_agents = [p for pid, p in players.items() if pid not in rostered]

    by_pos: dict[str, list[float]] = {}
    for player in players.values():
        by_pos.setdefault(player.position, []).append(player.points)
    for pool in by_pos.values():
        pool.sort(reverse=True)

    return Snapshot(
        league=league,
        teams=teams,
        free_agents=free_agents,
        week=int(raw.get("week") or 1),
        regular_season_weeks=int(raw.get("regular_season_weeks") or 14),
        trade_deadline=raw.get("trade_deadline"),
        waiver=waiver_levels(free_agents),
        market=replacement_levels(by_pos, league),
    )


def waiver_levels(free_agents: list[Player]) -> dict[str, float]:
    """What a waiver claim realistically brings in at each position.

    The second-best free agent, not the best: the best one is the player half
    the league has a claim in for, and you will not get him every time.
    """
    by_pos: dict[str, list[float]] = {}
    for player in free_agents:
        by_pos.setdefault(player.position, []).append(player.points)
    levels = {}
    for pos, pool in by_pos.items():
        pool.sort(reverse=True)
        levels[pos] = pool[1] if len(pool) > 1 else pool[0]
    return levels


# ---- valuing a roster -------------------------------------------------------

def lineup_points(players: list[Player], league: LeagueSettings,
                  waiver: dict[str, float]) -> float:
    """Best starting lineup, with any empty slot filled from the wire."""
    starters, _, unfilled = best_lineup(players, league)
    return sum(p.points for p in starters) + waiver_fill(unfilled, waiver)


def roster_value(players: list[Player], league: LeagueSettings,
                 waiver: dict[str, float]) -> float:
    """The lineup, plus a little for backups who beat the waiver wire."""
    starters, bench, unfilled = best_lineup(players, league)
    total = sum(p.points for p in starters) + waiver_fill(unfilled, waiver)
    for player in bench:
        if player.position in TRADE_POSITIONS:
            total += DEPTH_CREDIT * max(0.0, player.points - waiver.get(player.position, 0.0))
    return total


def after_trade(players: list[Player], out: tuple[Player, ...], into: tuple[Player, ...],
                league: LeagueSettings) -> list[Player]:
    """The roster once the trade goes through, cut back to its old size.

    Taking two for one means somebody has to be released, and it is whoever
    the lineup can best do without -- the weakest player on the bench.
    """
    leaving = {p.player_id for p in out}
    roster = [p for p in players if p.player_id not in leaving] + list(into)
    while len(roster) > len(players):
        _, bench, _ = best_lineup(roster, league)
        pool = bench or roster
        cut = min(pool, key=lambda p: p.points)
        roster = [p for p in roster if p.player_id != cut.player_id]
    return roster


def market_value(players: tuple[Player, ...], market: dict[str, float]) -> float:
    return sum(max(0.0, p.points - market.get(p.position, 0.0)) for p in players)


# ---- where each team stands -------------------------------------------------

@dataclass
class Standing:
    team: Team
    lineup: float
    by_position: dict[str, float]     # starters' points per week, by position
    rank: int = 0


def standings(snap: Snapshot) -> list[Standing]:
    rows = []
    for team in snap.teams:
        starters, _, unfilled = best_lineup(team.players, snap.league)
        by_pos: dict[str, float] = {}
        for player in starters:
            by_pos[player.position] = by_pos.get(player.position, 0.0) + player.points
        for slot in unfilled:
            pos = slot if slot in snap.waiver else max(
                FLEX_ELIGIBILITY.get(slot, ()) or ("RB",),
                key=lambda p: snap.waiver.get(p, 0.0))
            by_pos[pos] = by_pos.get(pos, 0.0) + snap.waiver.get(pos, 0.0)
        rows.append(Standing(team, lineup_points(team.players, snap.league, snap.waiver), by_pos))
    rows.sort(key=lambda s: -s.lineup)
    for i, row in enumerate(rows, start=1):
        row.rank = i
    return rows


def position_ranks(rows: list[Standing], team_id: str) -> dict[str, tuple[float, int, float]]:
    """For one team: each position's starters, their league rank, and vs average."""
    out = {}
    positions = sorted({pos for r in rows for pos in r.by_position})
    for pos in positions:
        values = sorted((r.by_position.get(pos, 0.0) for r in rows), reverse=True)
        mine = next(r for r in rows if r.team.team_id == team_id).by_position.get(pos, 0.0)
        average = sum(values) / len(values)
        out[pos] = (mine, values.index(mine) + 1, mine - average)
    return out


# ---- the search -------------------------------------------------------------

def _pool(team: Team) -> list[Player]:
    tradeable = [p for p in team.players if p.position in TRADE_POSITIONS and p.points > 0]
    return sorted(tradeable, key=lambda p: -p.points)[:POOL_PER_TEAM]


def _packages(pool: list[Player]) -> list[tuple[Player, ...]]:
    return [(p,) for p in pool] + list(combinations(pool, 2))


def find_trades(snap: Snapshot, me: Team, partner: Team, limit: int = 5) -> list[Deal]:
    """The best trades with one team that leave both lineups better off."""
    league, waiver = snap.league, snap.waiver
    base_me = roster_value(me.players, league, waiver)
    base_them = roster_value(partner.players, league, waiver)

    found: list[Deal] = []
    for give in _packages(_pool(me)):
        for get in _packages(_pool(partner)):
            give_market = market_value(give, snap.market)
            get_market = market_value(get, snap.market)
            if get_market > give_market * MAX_MARKET_TILT + 1.0:
                continue                      # they would not read it as fair
            mine = after_trade(me.players, give, get, league)
            my_gain = roster_value(mine, league, waiver) - base_me
            if my_gain < MIN_MY_GAIN:
                continue
            theirs = after_trade(partner.players, get, give, league)
            their_gain = roster_value(theirs, league, waiver) - base_them
            if their_gain < MIN_THEIR_GAIN:
                continue
            found.append(Deal(partner, give, get, my_gain, their_gain,
                              give_market, get_market))
    return _distinct(found, limit)


def _distinct(deals: list[Deal], limit: int) -> list[Deal]:
    """The strongest deals, without padding the list with near-copies.

    A two-for-two that contains a one-for-one already listed is usually that
    deal plus a throw-in, so it is skipped; and no one target appears in more
    than two suggestions.
    """
    deals.sort(key=lambda d: -d.score)
    kept: list[Deal] = []
    seen_targets: dict[str, int] = {}
    for deal in deals:
        give = {p.player_id for p in deal.give}
        get = {p.player_id for p in deal.get}
        if any({p.player_id for p in k.give} <= give and {p.player_id for p in k.get} <= get
               for k in kept):
            continue
        if any(seen_targets.get(p.player_id, 0) >= 2 for p in deal.get):
            continue
        kept.append(deal)
        for p in deal.get:
            seen_targets[p.player_id] = seen_targets.get(p.player_id, 0) + 1
        if len(kept) >= limit:
            break
    return kept


# ---- the report -------------------------------------------------------------

def _names(players: tuple[Player, ...]) -> str:
    return " + ".join(f"{p.name} ({p.position}, {p.points:.1f})" for p in players)


def _deadline(ms: int | None, today: dt.date | None = None) -> str | None:
    if not ms:
        return None
    try:
        when = dt.datetime.fromtimestamp(int(ms) / 1000).date()
    except (OverflowError, OSError, ValueError):
        return None
    days = (when - (today or dt.date.today())).days
    if days < 0:
        return f"The trade deadline passed on {when:%a %b %d} — trades may be locked."
    return f"Trade deadline: {when:%a %b %d} ({days} days from today)."


def render(snap: Snapshot, my_team_id: str, targets: list[Team] | None = None,
           today: dt.date | None = None) -> str:
    me = snap.team(my_team_id)
    if not me:
        known = ", ".join(f"{t.team_id} = {t.name}" for t in snap.teams)
        return (f"Could not find your team (team id {my_team_id}) in this league.\n"
                f"The teams are: {known}")

    rows = standings(snap)
    lines: list[str] = []
    add = lines.append
    weeks_left = max(0, snap.regular_season_weeks - snap.week + 1)

    add("")
    add("=" * 74)
    add(f"  TRADE REPORT — {snap.league.name} — week {snap.week}")
    add(f"  Points per week from here on: ESPN's projection, blended with what")
    add(f"  each player has actually averaged so far. {weeks_left} regular-season weeks left.")
    note = _deadline(snap.trade_deadline, today)
    if note:
        add(f"  {note}")
    add("=" * 74)

    # ---- the league ----------------------------------------------------
    add("")
    add("  Best starting lineup each team can field, per week:")
    add("")
    best = rows[0].lineup
    for row in rows:
        mark = "  <<< YOU" if row.team.team_id == me.team_id else ""
        if targets and any(t.team_id == row.team.team_id for t in targets):
            mark = "  <-- trade target"
        bar = "█" * max(1, round(20 * row.lineup / best)) if best > 0 else ""
        add(f"  {row.rank:>2}  {row.team.name[:24]:<24} {row.team.record:>6}  "
            f"{row.lineup:6.1f}  {bar}{mark}")

    # ---- your team -----------------------------------------------------
    mine = next(r for r in rows if r.team.team_id == me.team_id)
    ranks = position_ranks(rows, me.team_id)
    teams = len(rows)
    add("")
    add("-" * 74)
    add(f"  YOUR TEAM — {me.name} ({me.record}), lineup rank {mine.rank} of {teams}")
    add("-" * 74)
    add("")
    add("  By position (your starters, per week):")
    for pos in TRADE_POSITIONS:
        if pos not in ranks:
            continue
        pts, rank, vs = ranks[pos]
        verdict = ("strength" if rank <= teams // 3 else
                   "weak spot" if rank > teams - teams // 3 else "middle")
        add(f"    {pos:<3} {pts:6.1f}   rank {rank:>2} of {teams}   {vs:+5.1f} vs average   {verdict}")

    starters, bench, unfilled = best_lineup(me.players, snap.league)
    add("")
    add("  Starting lineup:")
    order = {pos: i for i, pos in enumerate(("QB", "RB", "WR", "TE", "K", "DEF"))}
    for player in sorted(starters, key=lambda p: (order.get(p.position, 9), -p.points)):
        add(f"    {player.position:<4}{player.name[:26]:<26} {player.points:5.1f}"
            + (f"  [{player.injury_status}]" if player.injury_status else ""))
    for slot in unfilled:
        add(f"    {slot:<4}(empty — a waiver pickup)")
    useful = [p for p in bench if p.position in TRADE_POSITIONS]
    if useful:
        add("")
        add("  Bench (the first few are your trade chips):")
        for player in sorted(useful, key=lambda p: -p.points)[:7]:
            add(f"    {player.position:<4}{player.name[:26]:<26} {player.points:5.1f}"
                + (f"  [{player.injury_status}]" if player.injury_status else ""))

    # ---- trades --------------------------------------------------------
    partners = targets or [t for t in snap.teams if t.team_id != me.team_id]
    if targets:
        for partner in partners:
            _render_partner(lines, snap, rows, me, partner, find_trades(snap, me, partner))
    else:
        everything = []
        for partner in partners:
            everything.extend(find_trades(snap, me, partner, limit=2))
        everything.sort(key=lambda d: -d.score)
        add("")
        add("-" * 74)
        add("  BEST TRADES ACROSS THE LEAGUE (two per team at most)")
        add("-" * 74)
        if not everything:
            add("")
            add("  Nothing that helps both sides right now.")
        for deal in everything[:10]:
            _render_deal(lines, deal, show_partner=True)

    add("")
    add("  How to read this: every number is projected points per week. \"You\" and")
    add("  \"them\" are how much each starting lineup improves. Only trades that help")
    add("  both lineups are listed, because those are the ones that get accepted.")
    add("  Injured players are already marked down. Check the news before sending.")
    add("")
    return "\n".join(lines)


def _render_partner(lines, snap, rows, me, partner, deals) -> None:
    add = lines.append
    teams = len(rows)
    row = next(r for r in rows if r.team.team_id == partner.team_id)
    ranks = position_ranks(rows, partner.team_id)
    mine = position_ranks(rows, me.team_id)
    add("")
    add("-" * 74)
    add(f"  TRADING WITH {partner.name} ({partner.record}), lineup rank {row.rank} of {teams}")
    add("-" * 74)
    strong = [p for p in TRADE_POSITIONS if p in ranks and ranks[p][1] <= teams // 3]
    weak = [p for p in TRADE_POSITIONS if p in ranks and ranks[p][1] > teams - teams // 3]
    add("")
    add(f"  They are strong at: {', '.join(strong) or 'nothing in particular'}")
    add(f"  They are short at:  {', '.join(weak) or 'nothing in particular'}")
    fits = [p for p in weak if p in mine and mine[p][1] <= teams // 2]
    wants = [p for p in strong if p in mine and mine[p][1] > teams // 2]
    if fits or wants:
        add(f"  The fit: " + "; ".join(filter(None, [
            f"you can spare {', '.join(fits)}" if fits else "",
            f"you need their {', '.join(wants)}" if wants else "",
        ])))
    if not deals:
        add("")
        add("  No trade with them helps both lineups right now. That can change after")
        add("  an injury or a waiver run, so check again next week.")
        return
    for deal in deals:
        _render_deal(lines, deal, show_partner=False)


def _render_deal(lines, deal: Deal, show_partner: bool) -> None:
    add = lines.append
    add("")
    if show_partner:
        add(f"  With {deal.partner.name}:")
    add(f"    You give:  {_names(deal.give)}")
    add(f"    You get:   {_names(deal.get)}")
    add(f"    Lineup:    you {deal.my_gain:+.1f} / week,  them {deal.their_gain:+.1f} / week"
        f"   ({deal.balance})")
