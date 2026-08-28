"""Thin, dependency-free client for Sleeper's public HTTP API.

Sleeper requires no API key and no authentication for everything this tool
reads (leagues, drafts, picks, players, projections). Endpoints are documented
at https://docs.sleeper.com/ with the exception of the projections host, which
is public but undocumented -- so every projections call has fallbacks.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

API = "https://api.sleeper.app"
# The projections/ADP host. Undocumented but public and stable for years.
DATA_API = "https://api.sleeper.com"

USER_AGENT = "draftkit/1.0 (+local draft assistant)"


class SleeperError(RuntimeError):
    """Raised when Sleeper cannot be reached or returns something unusable."""


def _decode(resp) -> Any:
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _get(url: str, timeout: float = 25.0, retries: int = 3) -> Any:
    """GET a JSON document, retrying transient failures with backoff.

    A 404 is returned as None rather than raised: Sleeper uses it to mean
    "no such league/draft/user", which callers handle as a normal outcome.
    """
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _decode(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            # 429/5xx are worth retrying; other 4xx are not.
            if exc.code not in (429, 500, 502, 503, 504):
                raise SleeperError(f"{url} -> HTTP {exc.code}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
        if attempt < retries - 1:
            time.sleep(1.5 * (2**attempt))
    raise SleeperError(f"{url} failed after {retries} attempts: {last}")


class Cache:
    """Tiny on-disk JSON cache so we do not refetch the 5MB player file."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = urllib.parse.quote(key, safe="")
        return self.root / f"{safe}.json"

    def get(self, key: str, max_age: float) -> Any:
        path = self._path(key)
        try:
            if time.time() - path.stat().st_mtime > max_age:
                return None
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, value: Any) -> None:
        path = self._path(key)
        try:
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(value, fh)
            tmp.replace(path)
        except OSError as exc:  # a broken cache must never break the draft
            log.warning("cache write failed for %s: %s", key, exc)


class SleeperClient:
    def __init__(self, cache_dir: Path | None = None):
        root = cache_dir or (Path.home() / ".cache" / "draftkit")
        self.cache = Cache(root)
        self._lock = threading.Lock()

    # ---- reference data -------------------------------------------------

    def state(self) -> dict:
        """Current NFL season/week per Sleeper."""
        return _get(f"{API}/v1/state/nfl") or {}

    def players(self, max_age: float = 12 * 3600) -> dict[str, dict]:
        """The full NFL player universe, keyed by Sleeper player_id.

        This is a ~5MB document. Sleeper explicitly asks callers to fetch it
        at most once per day, so it is cached aggressively on disk.
        """
        cached = self.cache.get("players_nfl", max_age)
        if cached:
            return cached
        with self._lock:
            cached = self.cache.get("players_nfl", max_age)
            if cached:
                return cached
            data = _get(f"{API}/v1/players/nfl", timeout=90.0)
            if not isinstance(data, dict) or not data:
                raise SleeperError("player universe came back empty")
            self.cache.put("players_nfl", data)
            return data

    def projections(self, season: str | int, max_age: float = 6 * 3600) -> list[dict]:
        """Season-long projections including ADP for several scoring formats.

        Undocumented endpoint, so this tries a couple of known shapes and
        returns [] rather than raising -- callers degrade to ADP-only ranking.
        """
        key = f"projections_{season}"
        cached = self.cache.get(key, max_age)
        if cached is not None:
            return cached

        positions = ["QB", "RB", "WR", "TE", "K", "DEF"]
        qs = urllib.parse.urlencode(
            [("season_type", "regular"), ("order_by", "adp_half_ppr")]
            + [("position[]", p) for p in positions]
        )
        candidates = [
            f"{DATA_API}/projections/nfl/{season}?{qs}",
            f"{API}/projections/nfl/{season}?{qs}",
        ]
        for url in candidates:
            try:
                data = _get(url, timeout=45.0, retries=2)
            except SleeperError as exc:
                log.warning("projections fetch failed (%s): %s", url, exc)
                continue
            if isinstance(data, list) and data:
                self.cache.put(key, data)
                return data
        log.warning("no projections available for %s; falling back to ADP rank", season)
        self.cache.put(key, [])
        return []

    # ---- user / league / draft -----------------------------------------

    def user(self, username: str) -> dict | None:
        username = urllib.parse.quote(str(username).strip(), safe="")
        return _get(f"{API}/v1/user/{username}")

    def user_leagues(self, user_id: str, season: str | int) -> list[dict]:
        return _get(f"{API}/v1/user/{user_id}/leagues/nfl/{season}") or []

    def league(self, league_id: str) -> dict | None:
        return _get(f"{API}/v1/league/{league_id}")

    def league_users(self, league_id: str) -> list[dict]:
        return _get(f"{API}/v1/league/{league_id}/users") or []

    def league_drafts(self, league_id: str) -> list[dict]:
        return _get(f"{API}/v1/league/{league_id}/drafts") or []

    def draft(self, draft_id: str) -> dict | None:
        return _get(f"{API}/v1/draft/{draft_id}")

    def draft_picks(self, draft_id: str) -> list[dict]:
        """Every pick made so far. This is the live-sync hot path."""
        return _get(f"{API}/v1/draft/{draft_id}/picks", timeout=15.0, retries=2) or []
