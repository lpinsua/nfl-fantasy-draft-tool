"""Live draft state: who is gone, when you pick next, and what to do about it."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from .league import LeagueSettings, positional_need
from .values import Board, Player

# Positions to hold off on until the very end of the draft.
LATE_ROUND_ONLY = frozenset({"K", "DEF"})


@dataclass
class DraftMeta:
    draft_id: str
    status: str
    draft_type: str
    teams: int
    rounds: int
    reversal_round: int = 0
    budget: int = 0
    # Epoch milliseconds, straight from Sleeper.
    start_time: int = 0
    last_picked: int = 0
    pick_timer: int = 0          # seconds allowed per pick, 0 = untimed
    draft_order: dict[str, int] = field(default_factory=dict)   # user_id -> slot
    slot_to_roster: dict[str, int] = field(default_factory=dict)

    @property
    def total_picks(self) -> int:
        return self.teams * self.rounds

    @property
    def is_auction(self) -> bool:
        """Auction drafts have no pick order, so timing-based advice is void."""
        return self.draft_type == "auction"


def parse_draft(raw: dict) -> DraftMeta:
    settings = raw.get("settings") or {}
    return DraftMeta(
        draft_id=str(raw.get("draft_id") or ""),
        status=str(raw.get("status") or "unknown"),
        draft_type=str(raw.get("type") or "snake"),
        teams=int(settings.get("teams") or 12),
        rounds=int(settings.get("rounds") or 15),
        reversal_round=int(settings.get("reversal_round") or 0),
        budget=int(settings.get("budget") or 0),
        start_time=int(raw.get("start_time") or 0),
        last_picked=int(raw.get("last_picked") or 0),
        pick_timer=int(settings.get("pick_timer") or 0),
        draft_order={str(k): int(v) for k, v in (raw.get("draft_order") or {}).items()},
        slot_to_roster={str(k): v for k, v in (raw.get("slot_to_roster_id") or {}).items()},
    )


def pick_number(meta: DraftMeta, round_no: int, slot: int) -> int:
    """Overall pick number for a given round and draft slot.

    Handles snake, linear, and Sleeper's third-round-reversal option.
    """
    teams = meta.teams
    if meta.draft_type == "linear":
        return (round_no - 1) * teams + slot
    reverse = round_no % 2 == 0
    if meta.reversal_round and round_no >= meta.reversal_round:
        reverse = not reverse
    position = (teams - slot + 1) if reverse else slot
    return (round_no - 1) * teams + position


def my_pick_numbers(meta: DraftMeta, slot: int) -> list[int]:
    return [pick_number(meta, r, slot) for r in range(1, meta.rounds + 1)]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def availability(player: Player, at_pick: int, picks_made: int) -> float:
    """Probability the player is still on the board at a future pick.

    ADP is a central tendency, not a guarantee, so this treats a player's true
    draft slot as normally distributed around their ADP with a spread that
    widens deeper into the draft (early picks are far more predictable).
    """
    if player.adp is None:
        return 0.5
    if at_pick <= picks_made:
        return 1.0
    # ADP is an average across many drafts, and real draft slots scatter widely
    # around it -- a player going 20th on average still lands anywhere from the
    # low teens to the mid-30s. Too tight a spread here produces confident "0%"
    # answers that are simply wrong.
    sigma = max(7.0, 0.22 * player.adp)
    # Already survived past their ADP -> shift the distribution to reflect that.
    effective_adp = max(player.adp, picks_made + 0.5)
    return 1.0 - _norm_cdf((at_pick - 0.5 - effective_adp) / sigma)


def expected_best_at(
    candidates: Iterable[Player], at_pick: int, picks_made: int
) -> tuple[float, Player | None]:
    """Expected VORP of the best player at a position when your next turn comes.

    Walks the position in value order and accumulates
    ``value * P(available) * P(everyone better is gone)`` -- the expectation of
    whoever is at the top of that position's board when you are back on the clock.
    """
    ranked = sorted(candidates, key=lambda p: -p.vorp)[:40]
    expected = 0.0
    survives_none = 1.0
    likeliest: Player | None = None
    best_mass = 0.0
    for player in ranked:
        avail = availability(player, at_pick, picks_made)
        mass = survives_none * avail
        expected += player.vorp * mass
        if mass > best_mass:
            best_mass, likeliest = mass, player
        survives_none *= 1.0 - avail
        if survives_none < 1e-4:
            break
    return expected, likeliest


@dataclass
class Recommendation:
    player: Player
    score: float
    vona: float
    need: float
    survival: float
    reason: str

    def to_dict(self) -> dict:
        data = self.player.to_dict()
        data.update(
            {
                "score": round(self.score, 1),
                "vona": round(self.vona, 1),
                "need": round(self.need, 2),
                "survival": round(self.survival, 2),
                "reason": self.reason,
            }
        )
        return data


class DraftState:
    """Everything that changes as the draft unfolds."""

    def __init__(self, meta: DraftMeta, league: LeagueSettings, board: Board,
                 my_user_id: str | None = None, my_slot: int | None = None):
        self.meta = meta
        self.league = league
        self.board = board
        self.my_user_id = my_user_id
        self.my_slot = my_slot or (meta.draft_order.get(str(my_user_id)) if my_user_id else None)
        self.picks: list[dict] = []
        self.drafted: set[str] = set()
        self.rosters: dict[int, list[Player]] = {}
        self.spent: dict[int, int] = {}
        self._my_picks: list[Player] = []

    # ---- ingest ---------------------------------------------------------

    @staticmethod
    def _owner(pick: dict) -> int:
        """Which team made this pick.

        Snake drafts carry draft_slot; auctions do not, so fall back to
        roster_id rather than dropping the pick on the floor.
        """
        return int(pick.get("draft_slot") or pick.get("roster_id") or 0)

    @staticmethod
    def _amount(pick: dict) -> int:
        try:
            return int((pick.get("metadata") or {}).get("amount") or 0)
        except (TypeError, ValueError):
            return 0

    def apply_picks(self, picks: list[dict]) -> None:
        self.picks = sorted(picks, key=lambda p: int(p.get("pick_no") or 0))
        self.drafted = set()
        self.rosters = {}
        self.spent = {}
        self._my_picks = []
        for pick in self.picks:
            pid = str(pick.get("player_id") or "")
            if not pid:
                continue
            self.drafted.add(pid)
            owner = self._owner(pick)
            player = self.board.players.get(pid)
            if owner:
                self.spent[owner] = self.spent.get(owner, 0) + self._amount(pick)
            if player and owner:
                self.rosters.setdefault(owner, []).append(player)
            # Attribute by user id where we can: it survives auctions and any
            # slot/roster mismatch.
            if player and self.my_user_id and str(pick.get("picked_by") or "") == str(self.my_user_id):
                self._my_picks.append(player)

    # ---- derived state --------------------------------------------------

    @property
    def picks_made(self) -> int:
        return len(self.picks)

    @property
    def on_the_clock(self) -> int:
        return self.picks_made + 1

    @property
    def current_round(self) -> int:
        if self.meta.teams <= 0:
            return 1
        return min(self.meta.rounds, self.picks_made // self.meta.teams + 1)

    def next_picks(self, count: int = 3) -> list[int]:
        # An auction has no pick order, so there is no "next pick" to report.
        if self.meta.is_auction or not self.my_slot:
            return []
        upcoming = [n for n in my_pick_numbers(self.meta, self.my_slot) if n >= self.on_the_clock]
        return upcoming[:count]

    @property
    def my_next_pick(self) -> int | None:
        upcoming = self.next_picks(1)
        return upcoming[0] if upcoming else None

    @property
    def picks_until_my_turn(self) -> int | None:
        nxt = self.my_next_pick
        return None if nxt is None else nxt - self.on_the_clock

    @property
    def is_my_turn(self) -> bool:
        return not self.meta.is_auction and self.my_next_pick == self.on_the_clock

    def my_roster(self) -> list[Player]:
        if self._my_picks:
            return self._my_picks
        return self.rosters.get(self.my_slot, []) if self.my_slot else []

    def my_spend(self) -> int:
        if self.meta.is_auction and self.my_user_id:
            return sum(
                self._amount(p) for p in self.picks
                if str(p.get("picked_by") or "") == str(self.my_user_id)
            )
        return self.spent.get(self.my_slot or -1, 0)

    def roster_counts(self, slot: int | None = None) -> dict[str, int]:
        roster = self.rosters.get(slot, []) if slot is not None else self.my_roster()
        counts: dict[str, int] = {}
        for player in roster:
            counts[player.position] = counts.get(player.position, 0) + 1
        return counts

    def available(self) -> list[Player]:
        return [p for p in self.board.players.values() if p.player_id not in self.drafted]

    def position_runs(self, window: int = 10) -> dict[str, int]:
        """How many of each position went in the last N picks."""
        runs: dict[str, int] = {}
        for pick in self.picks[-window:]:
            player = self.board.players.get(str(pick.get("player_id") or ""))
            if player:
                runs[player.position] = runs.get(player.position, 0) + 1
        return runs

    # ---- the actual advice ---------------------------------------------

    def recommendations(self, limit: int = 8) -> list[Recommendation]:
        """Rank available players by what they are worth *to this roster, now*.

        Raw VORP says who is best. It does not say whether you should take them
        now or wait -- so this also computes value-over-next-available (VONA):
        what you give up by passing on a position until your next turn.
        """
        available = self.available()
        if not available:
            return []

        need = positional_need(self.roster_counts(), self.league)
        auction = self.meta.is_auction
        next_pick = self.my_next_pick or self.on_the_clock
        following = self.next_picks(2)

        # "Your next chance at this player." On the clock, that is the turn
        # after this one; otherwise it is the turn you are waiting for. Using a
        # flat round's worth of picks instead is badly wrong at the turn of a
        # snake, where two of your picks can be only a few apart.
        if self.is_my_turn:
            after_next = following[1] if len(following) > 1 else next_pick + self.meta.teams
        else:
            after_next = following[0] if following else self.on_the_clock

        by_pos: dict[str, list[Player]] = {}
        for player in available:
            by_pos.setdefault(player.position, []).append(player)

        # Timing-based value only means something when picks happen in an order.
        expected_next: dict[str, float] = {}
        if not auction:
            for pos, group in by_pos.items():
                expected_next[pos], _ = expected_best_at(group, after_next, self.picks_made)

        rounds_left = self.meta.rounds - self.current_round + 1
        runs = self.position_runs()
        results: list[Recommendation] = []

        for player in sorted(available, key=lambda p: -p.vorp)[:120]:
            pos_need = need.get(player.position, 0.3)
            vona = 0.0 if auction else player.vorp - expected_next.get(player.position, 0.0)
            score = player.vorp * (0.55 + 0.45 * pos_need) + 0.6 * max(0.0, vona)

            if player.position in LATE_ROUND_ONLY and rounds_left > 2:
                score -= 1000.0
            # Same horizon the VONA figure uses, so the two agree.
            survival = 1.0 if auction else availability(player, after_next, self.picks_made)

            results.append(
                Recommendation(
                    player=player,
                    score=score,
                    vona=vona,
                    need=pos_need,
                    survival=survival,
                    reason=self._reason(player, pos_need, vona, survival, by_pos, runs),
                )
            )

        results.sort(key=lambda r: -r.score)
        return results[:limit]

    def _reason(
        self,
        player: Player,
        need: float,
        vona: float,
        survival: float,
        by_pos: dict[str, list[Player]],
        runs: dict[str, int],
    ) -> str:
        bits: list[str] = []
        # How much of this tier is still on the board: the cliff warning is
        # "take him or the tier is gone", so count everyone left in it.
        left_in_tier = sum(
            1 for p in by_pos.get(player.position, []) if p.tier == player.tier
        )
        if left_in_tier <= 2:
            bits.append(
                f"only {left_in_tier} {player.position} left in tier {player.tier}"
                if left_in_tier > 1
                else f"last {player.position} in tier {player.tier}"
            )
        if need >= 1.0:
            bits.append(f"{player.position} starter slot still empty")
        # Timing language is only honest when picks happen in an order.
        if not self.meta.is_auction:
            if survival < 0.35:
                bits.append(f"{int(survival * 100)}% to last until your next pick")
            if vona > 12:
                bits.append(f"+{vona:.0f} pts over the next {player.position} you'd get")
        if runs.get(player.position, 0) >= 4:
            bits.append(f"{runs[player.position]} {player.position}s gone in last 10")
        if player.adp is not None and not self.meta.is_auction:
            # How far he has fallen past his average draft position. Positive
            # means the room let him slide to you; negative means taking him
            # here is earlier than the market would.
            fallen = self.on_the_clock - player.adp
            if fallen >= 12:
                bits.append(f"ADP {player.adp:.0f} — fell {fallen:.0f} picks, value here")
            elif fallen <= -12:
                bits.append(f"ADP {player.adp:.0f} — {abs(fallen):.0f} picks early, a reach")
        return "; ".join(bits[:3])
