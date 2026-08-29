"""Post-draft report: how every roster in the league came out, and where you rank.

Judged on the only thing that scores points -- the best starting lineup each
roster can actually field -- rather than the sum of everyone drafted, so a
bench full of good backups does not flatter a thin starting eleven.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .league import FLEX_ELIGIBILITY, LeagueSettings
from .values import Player


@dataclass
class Pick:
    player: Player
    pick_no: int
    round_no: int

    @property
    def value(self) -> float | None:
        """Picks he lasted past his ADP. Positive means you got a bargain."""
        if self.player.adp is None:
            return None
        return self.pick_no - self.player.adp


@dataclass
class TeamReport:
    slot: int
    name: str
    picks: list[Pick] = field(default_factory=list)
    starters: list[Player] = field(default_factory=list)
    bench: list[Player] = field(default_factory=list)
    unfilled: list[str] = field(default_factory=list)
    drafted_points: float = 0.0     # what the drafted starters alone score
    waiver_points: float = 0.0      # replacement level for any empty slot
    starter_points: float = 0.0     # the two together -- what teams are ranked on
    total_vorp: float = 0.0
    rank: int = 0

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for pick in self.picks:
            out[pick.player.position] = out.get(pick.player.position, 0) + 1
        return out

    @property
    def steals(self) -> list[Pick]:
        scored = [p for p in self.picks if p.value is not None]
        return sorted(scored, key=lambda p: -(p.value or 0))[:3]

    @property
    def reaches(self) -> list[Pick]:
        scored = [p for p in self.picks if p.value is not None]
        return sorted(scored, key=lambda p: (p.value or 0))[:3]


def best_lineup(
    players: list[Player], league: LeagueSettings
) -> tuple[list[Player], list[Player], list[str]]:
    """Fill the league's starting slots with the best the roster can field.

    Dedicated slots first, then flex spots from whoever is left -- the same
    greedy fill the replacement-level model uses, so the two agree. Also
    reports any slot nobody could fill.
    """
    remaining = sorted(players, key=lambda p: -p.points)
    starters: list[Player] = []
    unfilled: list[str] = []

    def take(eligible: tuple[str, ...]) -> Player | None:
        for i, candidate in enumerate(remaining):
            if candidate.position in eligible:
                return remaining.pop(i)
        return None

    for pos, count in league.starters.items():
        for _ in range(count):
            picked = take((pos,))
            starters.append(picked) if picked else unfilled.append(pos)
    for slot, count in league.flex_slots.items():
        for _ in range(count):
            picked = take(FLEX_ELIGIBILITY.get(slot, ()))
            starters.append(picked) if picked else unfilled.append(slot)
    return starters, remaining, unfilled


def waiver_fill(unfilled: list[str], replacement: dict[str, float]) -> float:
    """Points an empty starting slot is really worth.

    A roster with no kicker is not a roster that scores zero at kicker -- it is
    one waiver claim away from a replacement-level kicker. Scoring the gap at
    zero would punish a team for something fixable in thirty seconds, and would
    make the league table mostly a ranking of who spent a late pick on a K.
    """
    total = 0.0
    for slot in unfilled:
        if slot in replacement:
            total += replacement[slot]
        else:
            eligible = FLEX_ELIGIBILITY.get(slot, ())
            total += max((replacement.get(pos, 0.0) for pos in eligible), default=0.0)
    return total


def build_reports(
    picks: list[dict],
    board_players: dict[str, Player],
    league: LeagueSettings,
    team_names: dict[int, str],
    teams: int,
    replacement: dict[str, float] | None = None,
) -> list[TeamReport]:
    reports: dict[int, TeamReport] = {}
    for raw in sorted(picks, key=lambda p: int(p.get("pick_no") or 0)):
        pid = str(raw.get("player_id") or "")
        player = board_players.get(pid)
        if not player:
            continue
        slot = int(raw.get("draft_slot") or raw.get("roster_id") or 0)
        if not slot:
            continue
        report = reports.setdefault(
            slot, TeamReport(slot=slot, name=team_names.get(slot, f"Team {slot}"))
        )
        report.picks.append(
            Pick(player=player, pick_no=int(raw.get("pick_no") or 0),
                 round_no=int(raw.get("round") or 0))
        )

    for slot in range(1, teams + 1):
        reports.setdefault(slot, TeamReport(slot=slot, name=team_names.get(slot, f"Team {slot}")))

    levels = replacement or {}
    for report in reports.values():
        roster = [p.player for p in report.picks]
        report.starters, report.bench, report.unfilled = best_lineup(roster, league)
        report.drafted_points = sum(p.points for p in report.starters)
        report.waiver_points = waiver_fill(report.unfilled, levels)
        report.starter_points = report.drafted_points + report.waiver_points
        report.total_vorp = sum(p.vorp for p in roster)

    ordered = sorted(reports.values(), key=lambda r: -r.starter_points)
    for i, report in enumerate(ordered, start=1):
        report.rank = i
    return ordered


def _grade(rank: int, teams: int) -> str:
    pct = (rank - 1) / max(1, teams - 1)
    if pct <= 0.10:
        return "A"
    if pct <= 0.25:
        return "A-"
    if pct <= 0.40:
        return "B+"
    if pct <= 0.60:
        return "B"
    if pct <= 0.75:
        return "C+"
    if pct <= 0.90:
        return "C"
    return "D"


def _bar(value: float, best: float, width: int = 22) -> str:
    if best <= 0:
        return ""
    filled = max(1, round(width * value / best))
    return "█" * filled + "·" * (width - filled)


def render(session) -> str:
    """The whole report as text."""
    league = session.league
    meta = session.meta
    state = session.state
    if not (league and meta and state):
        return "Not connected to a draft."

    reports = build_reports(
        state.picks, session.board.players, league, session.team_names, meta.teams,
        replacement=session.board.replacement,
    )
    if not any(r.picks for r in reports):
        return "That draft has no picks yet — nothing to review."

    mine = next((r for r in reports if r.slot == state.my_slot), None)
    best = reports[0].starter_points
    lines: list[str] = []
    add = lines.append

    add("")
    add("=" * 74)
    add(f"  DRAFT REVIEW — {league.name}")
    add(f"  {league.scoring_label} · {meta.teams} teams · {meta.rounds} rounds "
        f"· {len(state.picks)} picks")
    add("=" * 74)

    # ---- the table ------------------------------------------------------
    add("")
    add("  Projected starting lineup, best each roster can field.")
    add("  Any empty starting slot is scored at waiver level, not zero — a missing")
    add("  kicker is one claim away, and should not decide the table.")
    add("")
    add(f"  {'#':>2}  {'TEAM':<22}{'STARTERS':>10}  {'':22} {'ROSTER'}")
    for report in reports:
        marker = " <<< YOU" if mine and report.slot == mine.slot else ""
        shape = "/".join(
            f"{n}{pos}" for pos, n in sorted(report.counts.items(), key=lambda kv: -kv[1])
        )
        gap = f" +{', '.join(report.unfilled)} from waivers" if report.unfilled else ""
        add(f"  {report.rank:>2}  {report.name[:22]:<22}{report.starter_points:>10.1f}  "
            f"{_bar(report.starter_points, best)} {shape}{gap}{marker}")

    # ---- your team ------------------------------------------------------
    if mine and mine.picks:
        # Signed from your point of view: negative means you are behind.
        gap_to_first = mine.starter_points - reports[0].starter_points
        median = reports[len(reports) // 2].starter_points
        add("")
        add("-" * 74)
        add(f"  YOUR DRAFT — {mine.name}")
        add("-" * 74)
        add("")
        add(f"  Finished {mine.rank} of {meta.teams}          Grade: {_grade(mine.rank, meta.teams)}")
        add(f"  Starting lineup      {mine.starter_points:.1f} projected points")
        if mine.unfilled:
            filled = ", ".join(mine.unfilled)
            add(f"    of which           {mine.waiver_points:.1f} is a waiver-level "
                f"{filled} you still need to add")
            # Compare like with like: everyone's drafted starters only.
            ahead = sum(1 for r in reports if r.drafted_points > mine.drafted_points)
            add(f"    (scored at zero    you would rank {ahead + 1} instead of {mine.rank} — "
                f"which is why it is not scored that way)")
        add(f"  vs the best roster   {gap_to_first:+.1f}")
        add(f"  vs the league median {mine.starter_points - median:+.1f}")
        add(f"  Total VORP drafted   {mine.total_vorp:+.1f}")

        add("")
        add("  Your starting lineup:")
        for player in sorted(mine.starters, key=lambda p: -p.points):
            add(f"    {player.position:<4} {player.name[:26]:<26} {player.points:>7.1f}"
                f"  ({player.vorp:+.1f} VORP)")
        if mine.bench:
            add("")
            add("  Bench:")
            for player in sorted(mine.bench, key=lambda p: -p.points)[:8]:
                add(f"    {player.position:<4} {player.name[:26]:<26} {player.points:>7.1f}")

        steals = [p for p in mine.steals if (p.value or 0) > 5]
        if steals:
            add("")
            add("  Best value — lasted past their ADP:")
            for pick in steals:
                add(f"    pick {pick.pick_no:>3}  {pick.player.name[:24]:<24} "
                    f"ADP {pick.player.adp:>5.1f}  fell {pick.value:+.0f}")
        reaches = [p for p in mine.reaches if (p.value or 0) < -5]
        if reaches:
            add("")
            add("  Earliest relative to ADP:")
            for pick in reaches:
                add(f"    pick {pick.pick_no:>3}  {pick.player.name[:24]:<24} "
                    f"ADP {pick.player.adp:>5.1f}  {pick.value:+.0f}")

        # ---- where the roster is thin -----------------------------------
        gaps = []
        for pos, required in league.starters.items():
            have = mine.counts.get(pos, 0)
            if have < required:
                how = " — grab one off waivers before week 1" if pos in ("K", "DEF") else ""
                gaps.append(f"{pos} ({have}/{required} — a starting slot is empty){how}")
            elif pos in ("RB", "WR") and have < required + 2:
                gaps.append(f"{pos} depth ({have} rostered)")
        if gaps:
            add("")
            add("  Watch:")
            for gap in gaps:
                add(f"    · {gap}")

    add("")
    add("  Projections are Sleeper's own and every ranking here is a preseason")
    add("  estimate. Drafts are won in September, not tonight.")
    add("")
    return "\n".join(lines)
