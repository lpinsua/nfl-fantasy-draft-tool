#!/usr/bin/env python3
"""Draft-night assistant for Sleeper fantasy football leagues.

    python3 draft.py --preflight --username you   # run this BEFORE draft day
    python3 draft.py --demo                       # rehearse, no network needed
    python3 draft.py --username you               # draft night
    python3 draft.py --rankings my.csv            # layer your own rankings on top

Requires nothing but Python 3.9+. No pip install, no API key.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

from draftkit import config as config_mod
from draftkit import credentials as creds_mod
from draftkit import preflight as preflight_mod
from draftkit import review as review_mod
from draftkit.api import SleeperClient
from draftkit.demo import DemoClient
from draftkit.espn import EspnClient
from draftkit.server import serve
from draftkit.session import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="draft.py",
        description="Live draft board and pick advisor for Sleeper.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "run the preflight check hours before your draft, not minutes:\n"
            "  python3 draft.py --preflight --username <your sleeper name>\n"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8777, help="port (default: 8777)")
    parser.add_argument("--username", default="", help="your Sleeper username")
    parser.add_argument("--league", default="", help="league id, to skip the picker")
    parser.add_argument("--draft", default="", help="draft id, to skip the picker")
    parser.add_argument("--rankings", default="", help="optional CSV of custom rankings")
    parser.add_argument(
        "--team", default="",
        help="your favourite NFL team, highlighted on the board (e.g. MIA)",
    )
    parser.add_argument(
        "--save", action="store_true",
        help=f"remember these settings in {config_mod.CONFIG_NAME} and reuse them next time",
    )
    parser.add_argument(
        "--espn", action="store_true",
        help="use ESPN instead of Sleeper (needs --league and --season)",
    )
    parser.add_argument(
        "--season", default="",
        help="season year, for ESPN (e.g. 2026)",
    )
    parser.add_argument(
        "--espn-login", action="store_true",
        help="store your ESPN cookies for private leagues, then exit",
    )
    parser.add_argument(
        "--review", action="store_true",
        help="grade the completed draft against the rest of the league, then exit",
    )
    parser.add_argument(
        "--preflight", action="store_true",
        help="check Sleeper connectivity and your league, print what was detected, then exit",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="run against synthetic data with no network, to rehearse the interface",
    )
    parser.add_argument(
        "--demo-picks", type=int, default=0,
        help="with --demo, start this many picks into the draft (default: 0)",
    )
    parser.add_argument(
        "--demo-speed", type=float, default=4.0,
        help="with --demo, seconds per simulated pick; 0 freezes the draft (default: 4)",
    )
    parser.add_argument(
        "--demo-type", default="snake", choices=("snake", "linear", "auction"),
        help="with --demo, the draft format to simulate (default: snake)",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Saved defaults fill in anything not given on the command line, so the
    # everyday case is just `python3 draft.py`.
    saved = config_mod.load()
    args.username = args.username or saved.get("username", "")
    args.league = args.league or saved.get("league_id", "")
    args.draft = args.draft or saved.get("draft_id", "")
    args.rankings = args.rankings or saved.get("rankings", "")
    args.team = (args.team or saved.get("favorite_team", "")).upper()
    args.season = args.season or saved.get("season", "")
    if args.espn:
        # ESPN has its own league id, kept separate from the Sleeper one.
        args.league = args.league or saved.get("espn_league_id", "")

    if args.save:
        path = config_mod.save(
            {
                "username": args.username,
                "draft_id": args.draft,
                "favorite_team": args.team,
                "rankings": args.rankings,
                "season": args.season,
                **({"espn_league_id": args.league} if args.espn else {"league_id": args.league}),
            }
        )
        print(f"saved defaults to {path}")

    if args.espn_login:
        return _espn_login()

    csv_path = Path(args.rankings).expanduser() if args.rankings else None
    if csv_path and not csv_path.exists():
        print(f"error: rankings file not found: {csv_path}", file=sys.stderr)
        return 2

    # ---- preflight: check and exit --------------------------------------
    if args.preflight:
        if not (args.username or args.league or args.draft):
            print("error: --preflight needs --username (or --league / --draft)", file=sys.stderr)
            return 2
        if args.espn:
            problem = _espn_ready(args)
            if problem:
                print(problem, file=sys.stderr)
                return 2
            return preflight_mod.run(
                EspnClient(args.league, args.season), username=args.username,
                league_id=args.league, provider="ESPN",
            )
        return preflight_mod.run(
            SleeperClient(), username=args.username, league_id=args.league, draft_id=args.draft
        )

    # ---- review: grade the finished draft and exit -----------------------
    if args.review:
        if not (args.league or args.draft):
            print("error: --review needs --league (or --draft)", file=sys.stderr)
            return 2
        if args.espn:
            problem = _espn_ready(args)
            if problem:
                print(problem, file=sys.stderr)
                return 2
            client = EspnClient(args.league, args.season)
        else:
            client = SleeperClient()
        session = Session(client)
        try:
            session.connect(args.league, args.draft or None, args.username or None)
        except Exception as exc:
            print(f"error: could not load that draft: {exc}", file=sys.stderr)
            return 1
        session.stop()
        print(review_mod.render(session))
        return 0

    # ---- pick a data source ---------------------------------------------
    if args.demo:
        client = DemoClient(
            start_picks=args.demo_picks,
            seconds_per_pick=args.demo_speed,
            draft_type=args.demo_type,
        )
    elif args.espn:
        problem = _espn_ready(args)
        if problem:
            print(problem, file=sys.stderr)
            return 2
        client = EspnClient(args.league, args.season)
    else:
        client = SleeperClient()

    session = Session(client, csv_path=csv_path, favorite_team=args.team)

    if args.demo:
        logging.info("demo mode — synthetic league, no network calls")
        try:
            session.connect("L1", None, args.username or "demo")
        except Exception as exc:
            print(f"error: demo failed to start: {exc}", file=sys.stderr)
            return 1
        # The simulated draft walks down the ranked board, so it needs the
        # board's order once it exists.
        client.board_order = [p.player_id for p in session.board.ordered()]
        session.sync()
    elif args.league or args.draft:
        logging.info("connecting to league/draft from the command line...")
        try:
            session.connect(args.league, args.draft or None, args.username or None)
        except Exception as exc:
            print(f"error: could not connect: {exc}", file=sys.stderr)
            return 1

    httpd = serve(session, client, args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    if args.username and not args.demo:
        url += f"?username={args.username}"

    print()
    print("  Sleeper draft board is running." + ("   [DEMO — synthetic data]" if args.demo else ""))
    print(f"  ->  {url}")
    print("  Ctrl-C to stop.")
    print()

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down.")
    finally:
        session.stop()
        httpd.server_close()
    return 0


def _espn_ready(args) -> str:
    """Empty string when we have what ESPN needs, else what is missing."""
    if not args.league:
        return ("error: ESPN needs a league id.  It is the number in your league URL:\n"
                "  https://fantasy.espn.com/football/league?leagueId=123456\n"
                "  python3 draft.py --espn --league 123456 --season 2026")
    if not args.season:
        return "error: ESPN needs --season (e.g. --season 2026)"
    if not creds_mod.espn_cookies():
        return ("error: no ESPN credentials stored, which a private league needs.\n"
                "  python3 draft.py --espn-login\n"
                "(If your league is public, this is a bug — tell me and I will "
                "make the check conditional.)")
    return ""


def _espn_login() -> int:
    """Prompt for the two ESPN cookies and store them outside the repo."""
    import getpass

    print(creds_mod.HOW_TO_GET_COOKIES)
    print()
    try:
        s2 = getpass.getpass("espn_s2 (input hidden): ").strip()
        swid = getpass.getpass("SWID    (input hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\ncancelled.")
        return 1
    if not (s2 and swid):
        print("error: both values are required.", file=sys.stderr)
        return 2
    path = creds_mod.save({"espn_s2": s2, "espn_swid": creds_mod.normalise_swid(swid)})
    print(f"\nsaved to {path} (readable only by you, and outside this repository).")
    print("check it works:  python3 draft.py --espn --preflight --league <id> --season <year>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
