"""Turn projected raw stats into fantasy points under a league's own scoring.

Sleeper stores scoring as a flat map of stat-key -> points-per-unit
(``{"pass_yd": 0.04, "rec": 0.5, "pass_td": 4, ...}``) and its projections use
the same stat keys. That means league-exact scoring is a dot product over the
keys the two have in common -- which handles TE premium, 6-point passing TDs,
first-down bonuses and any other custom rule without special-casing.
"""

from __future__ import annotations

from typing import Any, Iterable

# Keys that appear in a projections payload but are not scorable stats.
NON_STAT_KEYS = frozenset(
    {
        "adp_dynasty", "adp_dynasty_2qb", "adp_dynasty_half_ppr", "adp_dynasty_ppr",
        "adp_dynasty_std", "adp_half_ppr", "adp_ppr", "adp_rookie", "adp_std",
        "adp_2qb", "adp_idp", "pos_adp_dynasty_half_ppr",
        "pts_half_ppr", "pts_ppr", "pts_std", "pts_idp",
        "gp", "gms_active", "gs",
    }
)


def score_stats(stats: dict[str, Any], scoring: dict[str, Any]) -> float:
    """Dot-product a projected stat line against a league's scoring settings."""
    total = 0.0
    for key, per_unit in scoring.items():
        if key in NON_STAT_KEYS:
            continue
        value = stats.get(key)
        if value is None:
            continue
        try:
            total += float(value) * float(per_unit)
        except (TypeError, ValueError):
            continue
    return total


def fallback_points(stats: dict[str, Any], ppr: float) -> float | None:
    """Use Sleeper's own precomputed totals when raw stats are unusable.

    Picks the precomputed column closest to the league's reception value.
    """
    options = (
        (0.0, "pts_std"),
        (0.5, "pts_half_ppr"),
        (1.0, "pts_ppr"),
    )
    best_key = min(options, key=lambda o: abs(o[0] - ppr))[1]
    for key in (best_key, "pts_half_ppr", "pts_ppr", "pts_std"):
        value = stats.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def has_scorable_stats(stats: dict[str, Any], scoring: Iterable[str]) -> bool:
    """True when the stat line shares real scoring categories with the league."""
    meaningful = {
        "pass_yd", "pass_td", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td",
        "fgm", "xpm", "def_td", "sack", "int", "pts_allow",
    }
    keys = set(stats) & set(scoring) & meaningful
    return any(_nonzero(stats.get(k)) for k in keys)


def _nonzero(value: Any) -> bool:
    try:
        return abs(float(value)) > 1e-9
    except (TypeError, ValueError):
        return False
