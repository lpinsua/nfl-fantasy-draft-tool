"""Pre-draft self check.

Run this hours before the draft, not minutes. It exercises every Sleeper call
the live board depends on and prints what was detected, so that any mismatch
between the real API and what this tool expects surfaces while there is still
time to do something about it.
"""

from __future__ import annotations

import time
from typing import Any

from .api import DATA_API, SleeperClient, SleeperError
from .draftstate import parse_draft
from .league import parse_league
from .values import build_board

OK = "  ok  "
WARN = " warn "
FAIL = " FAIL "


class Report:
    def __init__(self) -> None:
        self.failed = 0
        self.warned = 0

    def line(self, tag: str, label: str, detail: str = "") -> None:
        print(f"[{tag}] {label}" + (f"  —  {detail}" if detail else ""))

    def ok(self, label: str, detail: str = "") -> None:
        self.line(OK, label, detail)

    def warn(self, label: str, detail: str = "") -> None:
        self.warned += 1
        self.line(WARN, label, detail)

    def fail(self, label: str, detail: str = "") -> None:
        self.failed += 1
        self.line(FAIL, label, detail)


def run(
    client: SleeperClient,
    username: str = "",
    league_id: str = "",
    draft_id: str = "",
    provider: str = "Sleeper",
) -> int:
    """Returns a process exit code: 0 = good to go, 1 = something is broken."""
    report = Report()
    print(f"\n{provider} draft board — preflight\n" + "=" * 62)

    # 1. Can we reach Sleeper at all?
    season = ""
    try:
        started = time.time()
        state = client.state()
        season = str(state.get("season") or "")
        report.ok(
            f"{provider} API reachable",
            f"season {season} · week {state.get('week')} · {(time.time()-started)*1000:.0f}ms",
        )
    except SleeperError as exc:
        report.fail(f"{provider} API unreachable", str(exc))
        print(
            f"\nNothing else can be checked without network access to {provider}.\n"
            "Check your connection, VPN, or corporate proxy and try again.\n"
        )
        return 1

    # 2. Resolve the league, by whichever identifier we were given.
    raw_league: dict[str, Any] | None = None
    raw_draft: dict[str, Any] | None = None
    my_user_id = ""

    if username:
        try:
            user = client.user(username)
        except SleeperError as exc:
            user = None
            report.fail("Username lookup failed", str(exc))
        if user:
            my_user_id = str(user.get("user_id"))
            report.ok("Username resolved", f"{username} → user_id {my_user_id}")
        else:
            report.fail(f"No such {provider} user", f"'{username}' — check spelling (it is not your email)")

    if draft_id and not league_id:
        raw_draft = client.draft(draft_id)
        if raw_draft:
            league_id = str(raw_draft.get("league_id") or "")
        else:
            report.fail("Draft id not found", draft_id)

    if not league_id and my_user_id:
        leagues = client.user_leagues(my_user_id, season)
        if not leagues:
            report.fail("No leagues found", f"user {my_user_id} has no {season} NFL leagues")
        else:
            report.ok(f"Found {len(leagues)} league(s) for {season}")
            for lg in leagues:
                print(f"         · {lg.get('name')}  ({lg.get('total_rosters')} teams, id {lg.get('league_id')})")
            league_id = str(leagues[0].get("league_id"))
            if len(leagues) > 1:
                report.warn(
                    "Checking the first league only",
                    "re-run with --league <id> to check a different one",
                )

    if not league_id:
        print("\nCould not determine which league to check. Pass --username or --league.\n")
        return 1

    # 3. League settings: the thing the whole value model depends on.
    raw_league = raw_league or client.league(league_id)
    if not raw_league:
        report.fail("League not found", league_id)
        return 1

    league = parse_league(raw_league)
    report.ok("League settings parsed", f"{league.name}")
    print(f"         · scoring    {league.scoring_label}")
    print(f"         · teams      {league.teams}")
    print(f"         · starters   {_fmt_slots(league)}")
    print(f"         · bench      {league.bench}  (roster size {league.roster_size})")

    if not league.roster_positions:
        report.fail("No roster positions", "cannot compute replacement levels")

    # 4. The draft itself.
    if not raw_draft:
        drafts = client.league_drafts(league_id)
        if not drafts:
            report.fail("No draft attached to this league")
        else:
            drafts.sort(key=lambda d: int(d.get("start_time") or 0), reverse=True)
            raw_draft = client.draft(str(drafts[0].get("draft_id")))

    if raw_draft:
        meta = parse_draft(raw_draft)
        report.ok(
            "Draft found",
            f"id {meta.draft_id} · type {meta.draft_type} · {meta.rounds} rounds · status {meta.status}",
        )
        if meta.is_auction:
            report.warn(
                "This is an AUCTION draft",
                "player values/tiers/needs work; pick-timing advice is hidden, "
                "and there is no $-value or max-bid guidance",
            )
        if meta.reversal_round:
            report.ok("Third-round reversal detected", f"reverses from round {meta.reversal_round}")

        slot = meta.draft_order.get(my_user_id) if my_user_id else None
        if meta.is_auction:
            report.ok("Draft slot not applicable", "auctions have no pick order")
        elif slot:
            picks = [
                (r, _pick_no(meta, r, slot)) for r in range(1, min(meta.rounds, 4) + 1)
            ]
            report.ok(
                f"Your draft slot is {slot}",
                "first picks: " + ", ".join(f"R{r} #{n}" for r, n in picks),
            )
        elif meta.draft_order:
            report.warn(
                "Your slot is not assigned yet",
                "normal before the commissioner sets the order; it appears automatically",
            )
        else:
            report.warn("Draft order not published yet", "the board will pick it up once it is")
    else:
        report.fail("Could not load the draft")

    # 5. The two heavy data sources.
    try:
        started = time.time()
        players = client.players()
        report.ok("Player universe loaded", f"{len(players)} players · {(time.time()-started):.1f}s")
    except SleeperError as exc:
        report.fail("Could not load players", str(exc))
        return 1

    projections = client.projections(league.season or season)
    if projections:
        source = f" from {DATA_API}" if provider == "Sleeper" else ""
        report.ok("Projections loaded", f"{len(projections)} rows{source}")
    else:
        report.warn(
            "Projections unavailable",
            "the board will fall back to ADP-only ordering (usable, but values "
            "become ordinal rather than real point estimates)",
        )

    # 6. Prove the whole model actually builds on this league's real data.
    try:
        board = build_board(players, projections, league)
    except Exception as exc:  # noqa: BLE001 - preflight reports, never crashes
        report.fail("Could not build the value board", f"{type(exc).__name__}: {exc}")
        return 1

    report.ok("Value board built", f"{len(board.players)} ranked · source '{board.source}'")
    if board.source == "league-scoring":
        report.ok("Scored on your league's own rules")
    else:
        report.warn(
            "Not using league-exact scoring",
            f"falling back to '{board.source}' — values stay usable but less precise",
        )
    for note in board.notes:
        report.warn("Board note", note)

    print("\n  Top 10 by VORP on YOUR league settings:")
    print(f"    {'#':>3}  {'PLAYER':<24} {'POS':<5} {'PROJ':>7} {'VORP':>7} {'ADP':>6}")
    for i, p in enumerate(board.ordered()[:10], start=1):
        adp = f"{p.adp:.0f}" if p.adp is not None else "—"
        print(f"    {i:>3}  {p.name[:24]:<24} {p.position:<5} {p.points:>7.1f} {p.vorp:>+7.1f} {adp:>6}")

    print("\n  Replacement level (points of the first player who does NOT start):")
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        if pos in board.replacement:
            print(f"    {pos:<5} {board.replacement[pos]:>7.1f}")

    # ---- verdict --------------------------------------------------------
    print("\n" + "=" * 62)
    if report.failed:
        print(f"  {report.failed} check(s) FAILED — the board will not work correctly as-is.\n")
        return 1
    if report.warned:
        print(f"  Ready, with {report.warned} warning(s) noted above.")
        print("  Sanity-check the scoring line and top-10 against the site before tonight.\n")
        return 0
    print("  All checks passed. Sanity-check the scoring line above against what")
    print("  the site shows in your league settings, then you are ready.\n")
    return 0


def _fmt_slots(league) -> str:
    parts = [f"{count}{pos}" for pos, count in league.starters.items()]
    parts += [f"{count}{slot.replace('_', '')}" for slot, count in league.flex_slots.items()]
    return " ".join(parts) or "(none)"


def _pick_no(meta, round_no: int, slot: int) -> int:
    from .draftstate import pick_number

    return pick_number(meta, round_no, slot)
