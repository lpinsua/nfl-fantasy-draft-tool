"""Remembered defaults, so draft night is one command with no flags.

Nothing here is a secret. Sleeper's API needs no password, token or API key:
a username and a league id are public, read-only identifiers. Do not put
anything genuinely sensitive in this file -- it sits in the repo.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_NAME = "draft.config.json"
KEYS = ("username", "league_id", "draft_id", "favorite_team", "rankings")


def config_path(root: Path | None = None) -> Path:
    return (root or Path(__file__).resolve().parent.parent) / CONFIG_NAME


def load(root: Path | None = None) -> dict:
    """Read saved defaults. A missing or broken file is simply no defaults."""
    path = config_path(root)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ignoring unreadable %s: %s", path.name, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in KEYS and v}


def save(values: dict, root: Path | None = None) -> Path:
    """Write defaults, merging over whatever is already saved."""
    path = config_path(root)
    merged = load(root)
    merged.update({k: v for k, v in values.items() if k in KEYS and v})
    with path.open("w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path
