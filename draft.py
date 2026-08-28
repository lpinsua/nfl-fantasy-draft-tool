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
from draftkit import preflight as preflight_mod
from draftkit.api import SleeperClient
from draftkit.demo import DemoClient
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

    if args.save:
        path = config_mod.save(
            {
                "username": args.username,
                "league_id": args.league,
                "draft_id": args.draft,
                "favorite_team": args.team,
                "rankings": args.rankings,
            }
        )
        print(f"saved defaults to {path}")

    csv_path = Path(args.rankings).expanduser() if args.rankings else None
    if csv_path and not csv_path.exists():
        print(f"error: rankings file not found: {csv_path}", file=sys.stderr)
        return 2

    # ---- preflight: check and exit --------------------------------------
    if args.preflight:
        if not (args.username or args.league or args.draft):
            print("error: --preflight needs --username (or --league / --draft)", file=sys.stderr)
            return 2
        return preflight_mod.run(
            SleeperClient(), username=args.username, league_id=args.league, draft_id=args.draft
        )

    # ---- pick a data source ---------------------------------------------
    if args.demo:
        client = DemoClient(
            start_picks=args.demo_picks,
            seconds_per_pick=args.demo_speed,
            draft_type=args.demo_type,
        )
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


if __name__ == "__main__":
    raise SystemExit(main())
