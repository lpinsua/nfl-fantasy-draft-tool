"""Secrets storage, kept deliberately away from the repository.

Sleeper needs no credentials at all. ESPN does: a private league is read with
the ``espn_s2`` and ``SWID`` cookies from a logged-in browser session, and those
are **real credentials** -- they grant access to your ESPN account, not just to
one league. So unlike ``draft.config.json``, none of this is ever written into
the project directory, which for this repo is public.

Stored instead in ~/.config/draftkit/secrets.json, readable only by you.
Environment variables win over the file, for anyone who prefers them.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

log = logging.getLogger(__name__)

ENV_PREFIX = "DRAFTKIT_"


def secrets_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root) if root else Path.home() / ".config"
    return base / "draftkit" / "secrets.json"


def _read_file() -> dict:
    path = secrets_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ignoring unreadable %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def get(name: str, default: str = "") -> str:
    """Look up a secret: environment first, then the stored file."""
    from_env = os.environ.get(ENV_PREFIX + name.upper())
    if from_env:
        return from_env.strip()
    value = _read_file().get(name)
    return str(value).strip() if value else default


def save(values: dict[str, str]) -> Path:
    """Write secrets to the user config directory, owner-readable only."""
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = _read_file()
    merged.update({k: v for k, v in values.items() if v})

    # Create with tight permissions rather than widening them afterwards.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
        fh.write("\n")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # best effort; some filesystems do not support it
        pass
    return path


def normalise_swid(swid: str) -> str:
    """ESPN's SWID is expected wrapped in braces; add them if they were lost."""
    swid = (swid or "").strip().strip('"')
    if swid and not swid.startswith("{"):
        swid = "{" + swid
    if swid and not swid.endswith("}"):
        swid = swid + "}"
    return swid


def espn_cookies() -> dict[str, str]:
    """The pair ESPN needs for a private league. Empty when not configured."""
    s2 = get("espn_s2")
    swid = normalise_swid(get("espn_swid"))
    if not (s2 and swid):
        return {}
    return {"espn_s2": s2, "SWID": swid}


HOW_TO_GET_COOKIES = """
How to find your ESPN cookies (Chrome or Edge, on the computer you use for ESPN):

  1. Log in at https://fantasy.espn.com and open your league.
  2. Press F12 (or Cmd-Option-I on a Mac) to open developer tools.
  3. Open the "Application" tab.  In the left sidebar:
        Storage -> Cookies -> https://fantasy.espn.com
  4. Find these two rows and copy their Values:
        espn_s2     (a long string with % signs in it)
        SWID        (looks like {AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE})
  5. Save them:

        python3 draft.py --espn-login

Treat these like a password. They let anyone act as you on ESPN, so they are
stored in your home directory, never in this repository, and they are not
printed back out. If you ever want to revoke them, log out of ESPN everywhere;
that invalidates the cookies.
""".strip()
