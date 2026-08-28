"""Derive everything the value model needs from a league's own settings.

Nothing here is configured by hand: roster shape, scoring flavour, team count
and flex eligibility all come from ``/v1/league/<id>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Which real positions may fill each flex-style roster slot.
FLEX_ELIGIBILITY: dict[str, tuple[str, ...]] = {
    "FLEX": ("RB", "WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
    "WRRB_WRT": ("RB", "WR", "TE"),
    "REC_FLEX": ("WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "IDP_FLEX": ("DL", "LB", "DB"),
}

# Slots that do not represent a starting lineup spot.
BENCH_SLOTS = frozenset({"BN", "IR", "TAXI"})

SKILL_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass
class LeagueSettings:
    league_id: str
    name: str
    season: str
    teams: int
    roster_positions: list[str]
    scoring: dict[str, float]
    # dedicated starting slots, e.g. {"QB": 1, "RB": 2, "WR": 3, "TE": 1}
    starters: dict[str, int] = field(default_factory=dict)
    # flex slots, e.g. {"FLEX": 1, "SUPER_FLEX": 1}
    flex_slots: dict[str, int] = field(default_factory=dict)
    bench: int = 0

    @property
    def ppr(self) -> float:
        try:
            return float(self.scoring.get("rec", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def te_premium(self) -> float:
        try:
            return float(self.scoring.get("bonus_rec_te", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def is_superflex(self) -> bool:
        return self.flex_slots.get("SUPER_FLEX", 0) > 0 or self.starters.get("QB", 0) > 1

    @property
    def scoring_label(self) -> str:
        ppr = self.ppr
        if ppr >= 0.95:
            base = "Full PPR"
        elif ppr >= 0.4:
            base = "Half PPR"
        elif ppr > 0:
            base = f"{ppr:g} PPR"
        else:
            base = "Standard"
        extras = []
        if self.te_premium:
            extras.append(f"TE+{self.te_premium:g}")
        if self.is_superflex:
            extras.append("Superflex")
        if float(self.scoring.get("pass_td", 4) or 4) >= 6:
            extras.append("6pt pass TD")
        return " · ".join([base, *extras])

    @property
    def roster_size(self) -> int:
        return len(self.roster_positions)

    def starting_slots(self) -> list[str]:
        """Every starting slot as a flat list, dedicated and flex alike."""
        slots: list[str] = []
        for pos, count in self.starters.items():
            slots.extend([pos] * count)
        for slot, count in self.flex_slots.items():
            slots.extend([slot] * count)
        return slots

    def summary(self) -> dict:
        return {
            "league_id": self.league_id,
            "name": self.name,
            "season": self.season,
            "teams": self.teams,
            "scoring_label": self.scoring_label,
            "ppr": self.ppr,
            "is_superflex": self.is_superflex,
            "starters": self.starters,
            "flex_slots": self.flex_slots,
            "bench": self.bench,
            "roster_size": self.roster_size,
            "roster_positions": self.roster_positions,
        }


def parse_league(raw: dict) -> LeagueSettings:
    positions = [str(p) for p in (raw.get("roster_positions") or [])]
    starters: dict[str, int] = {}
    flex_slots: dict[str, int] = {}
    bench = 0

    for slot in positions:
        if slot in BENCH_SLOTS:
            bench += 1
        elif slot in FLEX_ELIGIBILITY:
            flex_slots[slot] = flex_slots.get(slot, 0) + 1
        else:
            starters[slot] = starters.get(slot, 0) + 1

    scoring_raw = raw.get("scoring_settings") or {}
    scoring: dict[str, float] = {}
    for key, value in scoring_raw.items():
        try:
            scoring[str(key)] = float(value)
        except (TypeError, ValueError):
            continue

    teams = raw.get("total_rosters") or (raw.get("settings") or {}).get("num_teams") or 12

    return LeagueSettings(
        league_id=str(raw.get("league_id") or ""),
        name=str(raw.get("name") or "League"),
        season=str(raw.get("season") or ""),
        teams=int(teams),
        roster_positions=positions,
        scoring=scoring,
        starters=starters,
        flex_slots=flex_slots,
        bench=bench,
    )


def replacement_levels(
    players_by_pos: dict[str, list[float]],
    league: LeagueSettings,
) -> dict[str, float]:
    """Points scored by the first player at each position who does *not* start.

    Rather than guessing how flex slots split across RB/WR/TE, this fills every
    starting lineup in the league greedily -- dedicated slots first, then flex
    slots taken by whichever eligible player is worth the most. Whatever is left
    over defines replacement level, which is what VORP is measured against.
    ``players_by_pos`` maps position -> projected points, highest first.
    """
    cursor: dict[str, int] = {pos: 0 for pos in players_by_pos}

    # 1. Dedicated starting slots: every team fills theirs with the best left.
    for pos, per_team in league.starters.items():
        if pos in cursor:
            cursor[pos] += per_team * league.teams

    # 2. Flex slots, filled greedily by best available across eligible spots.
    for slot, per_team in league.flex_slots.items():
        eligible = [p for p in FLEX_ELIGIBILITY.get(slot, ()) if p in players_by_pos]
        for _ in range(per_team * league.teams):
            best_pos, best_pts = None, float("-inf")
            for pos in eligible:
                idx = cursor.get(pos, 0)
                pool = players_by_pos[pos]
                if idx < len(pool) and pool[idx] > best_pts:
                    best_pos, best_pts = pos, pool[idx]
            if best_pos is None:
                break
            cursor[best_pos] += 1

    levels: dict[str, float] = {}
    for pos, pool in players_by_pos.items():
        if not pool:
            levels[pos] = 0.0
            continue
        idx = min(cursor.get(pos, 0), len(pool) - 1)
        levels[pos] = float(pool[idx])
    return levels


def positional_need(roster_counts: dict[str, int], league: LeagueSettings) -> dict[str, float]:
    """How badly a roster still needs each position, as a 0..1-ish weight.

    1.0 means a required starting slot is still empty; the weight decays once
    starters are filled so the model stops forcing needs that are already met.
    """
    need: dict[str, float] = {}
    flex_capacity: dict[str, int] = {}
    for slot, count in league.flex_slots.items():
        for pos in FLEX_ELIGIBILITY.get(slot, ()):
            flex_capacity[pos] = flex_capacity.get(pos, 0) + count

    for pos in SKILL_POSITIONS:
        required = league.starters.get(pos, 0)
        have = roster_counts.get(pos, 0)
        if required and have < required:
            # Empty required starting slot: maximum urgency.
            need[pos] = 1.0
            continue
        surplus = have - required
        flex = flex_capacity.get(pos, 0)
        if flex and surplus < flex:
            need[pos] = 0.75
        elif required == 0 and pos in ("K", "DEF"):
            need[pos] = 0.05
        else:
            # Depth only: decays as the bench fills with this position.
            need[pos] = max(0.15, 0.6 * (0.6 ** max(0, surplus - flex)))
    return need


def flex_eligible(pos: str, league: LeagueSettings) -> bool:
    for slot in league.flex_slots:
        if pos in FLEX_ELIGIBILITY.get(slot, ()):
            return True
    return False
