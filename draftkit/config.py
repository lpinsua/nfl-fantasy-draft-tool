"""Remembered defaults, so draft night is one command with no flags.

Several leagues can be saved at once, each under a short name of your choosing,
because most people play in more than one -- and because a Sleeper league and an
ESPN league need different settings (ESPN wants a season and a team id, Sleeper
wants neither). One of them is the default, and that is the one
``python3 draft.py`` uses when you name no league at all::

    {
      "default_league": "sleeper",
      "leagues": {
        "sleeper": {"provider": "sleeper", "league_id": "...", ...},
        "espn":    {"provider": "espn", "league_id": "...", "season": "2026", ...}
      }
    }

An older flat file -- settings at the top level, no ``leagues`` -- is still read
correctly and is treated as a single league named ``default``; it is rewritten
into the format above the first time anything is saved.

Nothing here is a secret. Sleeper's API needs no password, token or API key, and
a league id, a season and a team id are public, read-only identifiers on both
sites. ESPN's cookies *are* real credentials and live in ``draftkit.credentials``
instead, outside this repository. Do not put anything genuinely sensitive in
this file -- it sits in the repo.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_NAME = "draft.config.json"

# The only keys ever written. Anything else handed to save() is dropped, which
# is what keeps a stray password or cookie from being persisted by accident.
KEYS = (
    "provider",         # "sleeper" or "espn"
    "username",         # Sleeper username; ESPN has none
    "league_id",
    "draft_id",
    "season",           # ESPN needs this; Sleeper infers it
    "team_id",          # ESPN team id, so the board knows which roster is yours
    "favorite_team",
    "rankings",
)

FALLBACK_NAME = "default"


def config_path(root: Path | None = None) -> Path:
    return (root or Path(__file__).resolve().parent.parent) / CONFIG_NAME


def _read(root: Path | None = None) -> dict:
    """The raw file. A missing or broken file is simply no defaults."""
    path = config_path(root)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ignoring unreadable %s: %s", path.name, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _clean(values: dict) -> dict:
    """Keep only known keys with a real value, as strings."""
    if not isinstance(values, dict):
        return {}
    return {k: v for k, v in values.items() if k in KEYS and v not in ("", None)}


def _table(data: dict) -> dict[str, dict]:
    """The saved leagues, whichever format the file is in."""
    leagues = data.get("leagues")
    if isinstance(leagues, dict):
        return {str(name): _clean(body) for name, body in leagues.items()
                if isinstance(body, dict)}
    legacy = _clean(data)          # an old flat file is one unnamed league
    return {FALLBACK_NAME: legacy} if legacy else {}


def names(root: Path | None = None) -> list[str]:
    """Every saved league name, default first, then alphabetical."""
    data = _read(root)
    table = _table(data)
    default = default_name(root)
    return sorted(table, key=lambda n: (n != default, n))


def default_name(root: Path | None = None) -> str:
    """Which league ``python3 draft.py`` uses when none is named."""
    data = _read(root)
    table = _table(data)
    saved = str(data.get("default_league") or "")
    if saved in table:
        return saved
    return next(iter(table), FALLBACK_NAME)


def load(root: Path | None = None, name: str | None = None) -> dict:
    """Settings for one saved league: the named one, or the default one.

    Returns a flat dict of settings, exactly as this has always done, so a
    caller that does not care about multiple leagues never has to know.
    An unknown name is not an error -- it is just no defaults.
    """
    table = _table(_read(root))
    return dict(table.get(name or default_name(root)) or {})


def save(values: dict, root: Path | None = None, name: str | None = None,
         make_default: bool = False) -> Path:
    """Write settings for one league, merging over whatever it already has.

    Saving under a new name adds a league rather than replacing one, so the
    league you drafted with last year survives adding this year's.
    """
    data = _read(root)
    table = _table(data)
    key = name or default_name(root)

    merged = dict(table.get(key) or {})
    merged.update(_clean(values))
    table[key] = merged

    default = str(data.get("default_league") or "")
    if make_default or default not in table:
        default = key

    path = config_path(root)
    with path.open("w", encoding="utf-8") as fh:
        json.dump({"default_league": default, "leagues": table}, fh,
                  indent=2, sort_keys=True)
        fh.write("\n")
    return path


def set_default(name: str, root: Path | None = None) -> bool:
    """Choose the league that runs bare. False when there is no such league."""
    data = _read(root)
    table = _table(data)
    if name not in table:
        return False
    path = config_path(root)
    with path.open("w", encoding="utf-8") as fh:
        json.dump({"default_league": name, "leagues": table}, fh,
                  indent=2, sort_keys=True)
        fh.write("\n")
    return True


def describe(name: str, settings: dict) -> str:
    """One readable line for the --leagues listing."""
    provider = (settings.get("provider") or "sleeper").upper()
    bits = [f"league {settings.get('league_id') or '(none)'}"]
    if settings.get("season"):
        bits.append(f"season {settings['season']}")
    if settings.get("username"):
        bits.append(f"as {settings['username']}")
    if settings.get("team_id"):
        bits.append(f"team {settings['team_id']}")
    if settings.get("favorite_team"):
        bits.append(settings["favorite_team"])
    return f"{name:<12} {provider:<8} " + " · ".join(bits)
