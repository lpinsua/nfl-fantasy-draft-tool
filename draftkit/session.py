"""Holds the live draft session and keeps it in sync with Sleeper."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from .api import SleeperClient, SleeperError
from .csvimport import load_overrides
from .draftstate import DraftMeta, DraftState, parse_draft
from .league import LeagueSettings, parse_league
from .values import Board, build_board

log = logging.getLogger(__name__)

POLL_SECONDS = 3.0


class Session:
    """One connected league + draft, refreshed by a background poller."""

    def __init__(self, client: SleeperClient, csv_path: Path | None = None,
                 favorite_team: str = ""):
        self.client = client
        self.csv_path = csv_path
        self.favorite_team = (favorite_team or "").upper()
        self.lock = threading.RLock()

        self.league: LeagueSettings | None = None
        self.meta: DraftMeta | None = None
        self.state: DraftState | None = None
        self.board: Board | None = None
        self.team_names: dict[int, str] = {}
        self.my_user_id: str | None = None

        self.last_sync: float = 0.0
        self.last_error: str | None = None
        self.manual_picks: set[str] = set()

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- connect --------------------------------------------------------

    def connect(self, league_id: str, draft_id: str | None, username: str | None) -> dict:
        raw_league = self.client.league(league_id)
        if not raw_league:
            raise SleeperError(f"No league found with id {league_id}")
        league = parse_league(raw_league)

        if not draft_id:
            drafts = self.client.league_drafts(league_id)
            if not drafts:
                raise SleeperError("That league has no draft yet.")
            # Most recent draft first.
            drafts.sort(key=lambda d: int(d.get("start_time") or 0), reverse=True)
            draft_id = str(drafts[0].get("draft_id"))

        raw_draft = self.client.draft(draft_id)
        if not raw_draft:
            raise SleeperError(f"No draft found with id {draft_id}")
        meta = parse_draft(raw_draft)

        my_user_id = None
        if username:
            user = self.client.user(username)
            if user:
                my_user_id = str(user.get("user_id"))

        # Map draft slots to human names so the board reads like the real room.
        users = {str(u.get("user_id")): u for u in self.client.league_users(league_id)}
        team_names: dict[int, str] = {}
        for user_id, slot in meta.draft_order.items():
            user = users.get(str(user_id)) or {}
            label = (user.get("metadata") or {}).get("team_name") or user.get("display_name")
            team_names[int(slot)] = str(label or f"Team {slot}")

        players_meta = self.client.players()
        projections = self.client.projections(league.season or raw_draft.get("season") or "")

        overrides = None
        if self.csv_path and self.csv_path.exists():
            try:
                overrides = load_overrides(self.csv_path, players_meta)
            except (OSError, ValueError) as exc:
                log.warning("could not read %s: %s", self.csv_path, exc)

        board = build_board(players_meta, projections, league, overrides)
        state = DraftState(meta, league, board, my_user_id=my_user_id)

        with self.lock:
            self.league = league
            self.meta = meta
            self.board = board
            self.state = state
            self.team_names = team_names
            self.my_user_id = my_user_id
            self.manual_picks = set()
            self.last_error = None

        self.sync(force=True)
        self.start_polling()
        return self.status()

    def set_slot(self, slot: int) -> None:
        with self.lock:
            if self.state:
                self.state.my_slot = slot

    # ---- sync -----------------------------------------------------------

    def sync(self, force: bool = False) -> None:
        with self.lock:
            state, meta = self.state, self.meta
        if not state or not meta:
            return
        try:
            picks = self.client.draft_picks(meta.draft_id)
        except SleeperError as exc:
            with self.lock:
                self.last_error = str(exc)
            log.warning("pick sync failed: %s", exc)
            return

        # Refresh the draft object too: status, the pick clock and the start
        # time all move during a draft, and they are what the countdown reads.
        try:
            raw_draft = self.client.draft(meta.draft_id)
        except SleeperError as exc:
            raw_draft = None
            log.debug("draft refresh failed (non-fatal): %s", exc)
        if raw_draft:
            fresh = parse_draft(raw_draft)
            with self.lock:
                meta.status = fresh.status
                meta.last_picked = fresh.last_picked
                meta.start_time = fresh.start_time
                meta.pick_timer = fresh.pick_timer
                if fresh.draft_order and not meta.draft_order:
                    # The order is published once the commissioner sets it.
                    meta.draft_order = fresh.draft_order
                    if self.state and not self.state.my_slot and self.my_user_id:
                        self.state.my_slot = fresh.draft_order.get(str(self.my_user_id))

        with self.lock:
            merged = list(picks)
            # Once Sleeper reports a pick for real, drop our hand-made stand-in
            # so the player stops offering an "undo" that would do nothing.
            self.manual_picks -= {str(p.get("player_id")) for p in merged}
            if self.manual_picks:
                known = {str(p.get("player_id")) for p in merged}
                offset = len(merged)
                for extra in self.manual_picks:
                    if extra not in known:
                        offset += 1
                        merged.append({"player_id": extra, "pick_no": offset, "draft_slot": 0})
            state.apply_picks(merged)
            self.last_sync = time.time()
            self.last_error = None

    def start_polling(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="sleeper-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _poll_loop(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            try:
                self.sync()
            except Exception:  # a poller crash must not kill the draft board
                log.exception("poll loop error")

    # ---- manual overrides ----------------------------------------------

    def mark_drafted(self, player_id: str, drafted: bool) -> None:
        with self.lock:
            if drafted:
                self.manual_picks.add(player_id)
            else:
                self.manual_picks.discard(player_id)
        self.sync()

    # ---- views ----------------------------------------------------------

    def status(self) -> dict:
        with self.lock:
            if not (self.league and self.meta and self.state):
                return {"connected": False}
            state = self.state
            return {
                "connected": True,
                "league": self.league.summary(),
                "draft": {
                    "draft_id": self.meta.draft_id,
                    "status": self.meta.status,
                    "type": self.meta.draft_type,
                    "is_auction": self.meta.is_auction,
                    "budget": self.meta.budget,
                    "rounds": self.meta.rounds,
                    "teams": self.meta.teams,
                    "my_slot": state.my_slot,
                    "team_names": {str(k): v for k, v in self.team_names.items()},
                },
                "favorite_team": self.favorite_team,
                "board_source": self.board.source if self.board else None,
                "notes": self.board.notes if self.board else [],
            }

    def live(self) -> dict:
        with self.lock:
            if not (self.state and self.league):
                return {"connected": False}
            state = self.state
            recs = state.recommendations(limit=5)
            roster = state.my_roster()
            return {
                "connected": True,
                "picks_made": state.picks_made,
                "on_the_clock": state.on_the_clock,
                "round": state.current_round,
                "is_my_turn": state.is_my_turn,
                "is_auction": self.meta.is_auction if self.meta else False,
                "budget": self.meta.budget if self.meta else 0,
                "my_spend": state.my_spend(),
                "my_slot": state.my_slot,
                "status": self.meta.status if self.meta else "",
                "start_time": self.meta.start_time if self.meta else 0,
                "last_picked": self.meta.last_picked if self.meta else 0,
                "pick_timer": self.meta.pick_timer if self.meta else 0,
                "server_now": int(time.time() * 1000),
                "manual": sorted(self.manual_picks),
                "next_picks": state.next_picks(3),
                "picks_until_turn": state.picks_until_my_turn,
                "drafted": sorted(state.drafted),
                "recommendations": [r.to_dict() for r in recs],
                "fallers": [f.to_dict() for f in state.fallers()],
                "roster": [p.to_dict() for p in roster],
                "roster_counts": state.roster_counts(),
                "runs": state.position_runs(),
                "saturated": sorted(state.saturated_positions()),
                "recent": self._recent_picks(state, 12),
                "last_sync": self.last_sync,
                "error": self.last_error,
            }

    def _recent_picks(self, state: DraftState, count: int) -> list[dict]:
        rows = []
        for pick in state.picks[-count:][::-1]:
            player = state.board.players.get(str(pick.get("player_id") or ""))
            slot = int(pick.get("draft_slot") or 0)
            rows.append(
                {
                    "pick_no": pick.get("pick_no"),
                    "round": pick.get("round"),
                    "slot": slot,
                    "team": self.team_names.get(slot, "Manual" if slot == 0 else f"Team {slot}"),
                    "name": player.name if player else "(unknown)",
                    "pos": player.position if player else "",
                    "nfl_team": player.team if player else "",
                    "adp": player.adp if player else None,
                    "amount": DraftState._amount(pick),
                }
            )
        return rows

    def board_payload(self) -> dict:
        with self.lock:
            if not self.board:
                return {"players": []}
            return {
                "players": [p.to_dict() for p in self.board.ordered()],
                "replacement": {k: round(v, 1) for k, v in self.board.replacement.items()},
                "source": self.board.source,
            }
