"""Build the ranked player board: points, VORP, tiers and ADP."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from . import scoring as scoring_mod
from .league import SKILL_POSITIONS, LeagueSettings, replacement_levels

log = logging.getLogger(__name__)

# Positions we rank. Anything else (IDP, etc.) is carried but not modelled.
RANKED = frozenset(SKILL_POSITIONS)

INJURY_PENALTY = {
    "IR": 0.55,
    "PUP": 0.75,
    "Out": 0.80,
    "Doubtful": 0.90,
    "Sus": 0.85,
    "NA": 0.85,
}


@dataclass
class Player:
    player_id: str
    name: str
    position: str
    team: str | None = None
    age: int | None = None
    years_exp: int | None = None
    injury_status: str | None = None
    bye_week: int | None = None
    points: float = 0.0
    vorp: float = 0.0
    adp: float | None = None
    tier: int = 99
    pos_rank: int = 0
    overall_rank: int = 0
    source: str = "adp"

    def to_dict(self) -> dict:
        return {
            "id": self.player_id,
            "name": self.name,
            "pos": self.position,
            "team": self.team,
            "age": self.age,
            "exp": self.years_exp,
            "injury": self.injury_status,
            "bye": self.bye_week,
            "pts": round(self.points, 1),
            "vorp": round(self.vorp, 1),
            "adp": round(self.adp, 1) if self.adp is not None else None,
            "tier": self.tier,
            "pos_rank": self.pos_rank,
            "rank": self.overall_rank,
            "source": self.source,
        }


@dataclass
class Board:
    players: dict[str, Player] = field(default_factory=dict)
    replacement: dict[str, float] = field(default_factory=dict)
    source: str = "adp"
    notes: list[str] = field(default_factory=list)

    def ordered(self) -> list[Player]:
        return sorted(self.players.values(), key=lambda p: -p.vorp)


def _adp_key(league: LeagueSettings) -> str:
    if league.is_superflex:
        return "adp_2qb"
    ppr = league.ppr
    if ppr >= 0.95:
        return "adp_ppr"
    if ppr >= 0.4:
        return "adp_half_ppr"
    return "adp_std"


def _clean_name(meta: dict) -> str:
    name = meta.get("full_name")
    if not name:
        first = (meta.get("first_name") or "").strip()
        last = (meta.get("last_name") or "").strip()
        name = f"{first} {last}".strip()
    return name or str(meta.get("player_id") or "Unknown")


def _primary_position(meta: dict) -> str | None:
    pos = meta.get("position")
    if pos in RANKED:
        return pos
    for candidate in meta.get("fantasy_positions") or []:
        if candidate in RANKED:
            return candidate
    return pos if pos else None


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def build_board(
    players_meta: dict[str, dict],
    projections: list[dict],
    league: LeagueSettings,
    overrides: dict[str, dict] | None = None,
) -> Board:
    """Assemble the ranked board from Sleeper's player and projection data.

    Falls back cleanly: league-exact scoring when raw projected stats exist,
    Sleeper's precomputed totals when they do not, and ADP-derived pseudo-points
    when there are no projections at all.
    """
    board = Board()
    adp_key = _adp_key(league)
    proj_by_id: dict[str, dict] = {}

    for row in projections or []:
        pid = str(row.get("player_id") or (row.get("player") or {}).get("player_id") or "")
        if pid:
            proj_by_id[pid] = row

    scored_with_stats = 0
    scored_with_totals = 0

    for pid, meta in players_meta.items():
        if not isinstance(meta, dict):
            continue
        pos = _primary_position(meta)
        if pos not in RANKED:
            continue
        # Skip players who are not on an active roster, except team defenses.
        status = (meta.get("status") or "").lower()
        if pos != "DEF" and status in ("inactive", "retired"):
            continue

        row = proj_by_id.get(pid) or {}
        stats = row.get("stats") or {}
        points: float | None = None
        source = "adp"

        if stats and scoring_mod.has_scorable_stats(stats, league.scoring):
            points = scoring_mod.score_stats(stats, league.scoring)
            source = "league-scoring"
            scored_with_stats += 1
        elif stats:
            points = scoring_mod.fallback_points(stats, league.ppr)
            if points is not None:
                source = "sleeper-totals"
                scored_with_totals += 1

        adp = None
        for key in (adp_key, "adp_half_ppr", "adp_ppr", "adp_std"):
            value = stats.get(key)
            if value is not None:
                try:
                    adp = float(value)
                    break
                except (TypeError, ValueError):
                    continue

        if adp is None:
            rank = _as_int(meta.get("search_rank"))
            # search_rank is a global relevance ordering; treat it as a weak ADP.
            if rank is not None and rank < 10000:
                adp = float(rank)

        player = Player(
            player_id=str(pid),
            name=_clean_name(meta),
            position=pos,
            team=meta.get("team"),
            age=_as_int(meta.get("age")),
            years_exp=_as_int(meta.get("years_exp")),
            injury_status=meta.get("injury_status") or None,
            points=points if points is not None else 0.0,
            adp=adp,
            source=source,
        )
        if points is None:
            player.points = 0.0
        board.players[player.player_id] = player

    if not board.players:
        raise ValueError("no rankable players found")

    _apply_overrides(board, overrides)

    have_projections = any(p.source != "adp" for p in board.players.values())
    if not have_projections:
        board.notes.append(
            "Projections unavailable — ranking from ADP only. Values are ordinal, not point estimates."
        )
        _points_from_adp(board, league)
        board.source = "adp"
    else:
        board.source = "league-scoring" if scored_with_stats else "sleeper-totals"
        # Players with no projection at all should not outrank projected ones.
        _backfill_unprojected(board)

    _apply_injury_discount(board)
    _compute_vorp(board, league)
    _assign_tiers(board)
    _assign_ranks(board)
    return board


def _apply_overrides(board: Board, overrides: dict[str, dict] | None) -> None:
    """Apply user-supplied CSV rankings/projections on top of Sleeper data."""
    if not overrides:
        return
    applied = 0
    for pid, patch in overrides.items():
        player = board.players.get(pid)
        if not player:
            continue
        if patch.get("points") is not None:
            player.points = float(patch["points"])
            player.source = "custom"
        if patch.get("adp") is not None:
            player.adp = float(patch["adp"])
        applied += 1
    if applied:
        board.notes.append(f"Applied {applied} custom rankings from CSV.")


def _points_from_adp(board: Board, league: LeagueSettings) -> None:
    """Synthesise a plausible points curve when only ADP is available.

    Fantasy points decay roughly exponentially with positional rank, so this
    fits a per-position decay. It preserves ADP order while still producing
    usable VORP gaps and tier breaks.
    """
    shape = {"QB": (330.0, 0.020), "RB": (300.0, 0.035), "WR": (290.0, 0.030),
             "TE": (230.0, 0.045), "K": (140.0, 0.010), "DEF": (140.0, 0.015)}
    by_pos: dict[str, list[Player]] = {}
    for player in board.players.values():
        by_pos.setdefault(player.position, []).append(player)
    for pos, group in by_pos.items():
        top, decay = shape.get(pos, (250.0, 0.03))
        ranked = sorted(group, key=lambda p: (p.adp is None, p.adp or 9999.0))
        for idx, player in enumerate(ranked):
            player.points = top * math.exp(-decay * idx)


def _backfill_unprojected(board: Board) -> None:
    """Give unprojected players a floor value below their position's projected pool."""
    by_pos: dict[str, list[float]] = {}
    for player in board.players.values():
        if player.source != "adp" and player.points > 0:
            by_pos.setdefault(player.position, []).append(player.points)
    floors = {
        pos: (min(values) if values else 0.0) for pos, values in by_pos.items()
    }
    for player in board.players.values():
        if player.source == "adp" or player.points <= 0:
            player.points = max(0.0, floors.get(player.position, 0.0) * 0.5)


def _apply_injury_discount(board: Board) -> None:
    for player in board.players.values():
        factor = INJURY_PENALTY.get((player.injury_status or "").strip())
        if factor:
            player.points *= factor


def _compute_vorp(board: Board, league: LeagueSettings) -> None:
    by_pos: dict[str, list[float]] = {}
    for player in board.players.values():
        by_pos.setdefault(player.position, []).append(player.points)
    for pool in by_pos.values():
        pool.sort(reverse=True)

    board.replacement = replacement_levels(by_pos, league)
    for player in board.players.values():
        baseline = board.replacement.get(player.position, 0.0)
        player.vorp = player.points - baseline


def _assign_tiers(board: Board) -> None:
    """Group each position into tiers by finding meaningful gaps in value.

    A tier break is a drop-off materially larger than the typical gap between
    neighbouring players, which is what "there's a cliff after this guy" means
    in practice. Tiers are also capped so they stay small enough to be useful.
    """
    by_pos: dict[str, list[Player]] = {}
    for player in board.players.values():
        by_pos.setdefault(player.position, []).append(player)

    for group in by_pos.values():
        ranked = sorted(group, key=lambda p: -p.points)
        # Only tier the fantasy-relevant portion; the long tail is all one tier.
        head = ranked[:60]
        tail = ranked[60:]
        if len(head) < 2:
            for player in ranked:
                player.tier = 1
            continue

        gaps = [head[i].points - head[i + 1].points for i in range(len(head) - 1)]
        positive = [g for g in gaps if g > 0]
        if positive:
            mean = sum(positive) / len(positive)
            variance = sum((g - mean) ** 2 for g in positive) / len(positive)
            threshold = mean + math.sqrt(variance)
        else:
            threshold = 1.0

        tier = 1
        size = 0
        head[0].tier = 1
        for idx in range(1, len(head)):
            size += 1
            if (gaps[idx - 1] >= threshold and size >= 2) or size >= 8:
                tier += 1
                size = 0
            head[idx].tier = tier
        for player in tail:
            player.tier = tier + 1


def _assign_ranks(board: Board) -> None:
    overall = sorted(board.players.values(), key=lambda p: -p.vorp)
    for idx, player in enumerate(overall, start=1):
        player.overall_rank = idx
    by_pos: dict[str, list[Player]] = {}
    for player in board.players.values():
        by_pos.setdefault(player.position, []).append(player)
    for group in by_pos.values():
        for idx, player in enumerate(sorted(group, key=lambda p: -p.vorp), start=1):
            player.pos_rank = idx
