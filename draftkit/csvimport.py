"""Optional: fold a custom rankings CSV (e.g. a FantasyPros export) into the board.

Any CSV with a name column works. Recognised value columns, in priority order:
``projected_points``/``fpts``/``points``, then ``adp``/``rank``/``overall``.
Names are matched loosely so "A.J. Brown" and "AJ Brown" resolve to the same guy.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

NAME_COLS = ("player", "player name", "name", "full_name", "playername")
POINTS_COLS = ("projected_points", "fpts", "points", "proj", "projection", "fantasy_points")
ADP_COLS = ("adp", "avg_pick", "average_pick")
RANK_COLS = ("rank", "overall", "overall_rank", "rk", "ovr")

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize(name: str) -> str:
    cleaned = re.sub(r"[^a-z ]", "", (name or "").lower())
    parts = [p for p in cleaned.split() if p and p not in SUFFIXES]
    return " ".join(parts)


def _find(header: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {h.strip().lower(): h for h in header}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_overrides(path: Path, players_meta: dict[str, dict]) -> dict[str, dict]:
    """Map CSV rows onto Sleeper player_ids."""
    index: dict[str, list[str]] = {}
    for pid, meta in players_meta.items():
        if not isinstance(meta, dict):
            continue
        name = meta.get("full_name") or " ".join(
            filter(None, [meta.get("first_name"), meta.get("last_name")])
        )
        key = normalize(name)
        if key:
            index.setdefault(key, []).append(pid)

    overrides: dict[str, dict] = {}
    unmatched: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        name_col = _find(header, NAME_COLS)
        if not name_col:
            raise ValueError(
                f"{path.name}: no player-name column found (looked for {', '.join(NAME_COLS)})"
            )
        points_col = _find(header, POINTS_COLS)
        adp_col = _find(header, ADP_COLS) or _find(header, RANK_COLS)

        for row in reader:
            key = normalize(row.get(name_col, ""))
            if not key:
                continue
            matches = index.get(key)
            if not matches:
                unmatched.append(row.get(name_col, ""))
                continue
            patch: dict = {}
            if points_col:
                patch["points"] = _number(row.get(points_col))
            if adp_col:
                patch["adp"] = _number(row.get(adp_col))
            if patch:
                # Ambiguous names (same name, multiple players) go to the first.
                overrides[matches[0]] = patch

    if unmatched:
        log.warning("CSV: %d names did not match a Sleeper player (e.g. %s)",
                    len(unmatched), ", ".join(unmatched[:3]))
    return overrides
