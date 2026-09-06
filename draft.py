#!/usr/bin/env python3
"""Draft-night assistant for Sleeper and ESPN fantasy football leagues.

    python3 draft.py --leagues            # which leagues are saved?
    python3 draft.py                      # draft night, on the default league
    python3 draft.py --use espn           # draft night, on another saved league
    python3 draft.py --preflight          # check BEFORE draft day, then exit
    python3 draft.py --demo               # rehearse, no network needed
    python3 draft.py --rankings my.csv    # layer your own rankings on top

Several leagues can be saved at once, each under a short name, so playing in
more than one -- or on more than one site -- needs no second copy of this tool.
Add one with --save-as:

    python3 draft.py --espn --league 884705387 --season 2026 --team-id 17 \
        --save-as espn --preflight

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
        description="Live draft board and pick advisor for Sleeper and ESPN.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "run the preflight check hours before your draft, not minutes:\n"
            "  python3 draft.py --preflight              (the default league)\n"
            "  python3 draft.py --use espn --preflight   (another saved one)\n"
            "\n"
            "see what is saved:  python3 draft.py --leagues\n"
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
        "--use", "--league-name", dest="use", default="", metavar="NAME",
        help="run against a saved league by name (see --leagues)",
    )
    parser.add_argument(
        "--leagues", action="store_true",
        help="list the leagues saved in this folder, then exit",
    )
    parser.add_argument(
        "--save", action="store_true",
        help=f"remember these settings in {config_mod.CONFIG_NAME} and reuse them next time",
    )
    parser.add_argument(
        "--save-as", default="", metavar="NAME",
        help="remember these settings under a new name, keeping the leagues already saved",
    )
    parser.add_argument(
        "--set-default", default="", metavar="NAME",
        help="choose which saved league runs when you type `python3 draft.py` with no flags, then exit",
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
        "--team-id", default="", metavar="ID",
        help="your ESPN team id, so the board knows which roster is yours "
             "(the teamId= number in your league URL)",
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
    parser.add_argument(
        "--refresh", action="store_true",
        help="ignore the cached player/projection data for this run and refetch it",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    # ---- housekeeping that prints and exits ------------------------------
    if args.leagues:
        return _list_leagues()

    if args.set_default:
        if not config_mod.set_default(args.set_default):
            print(f"error: no saved league called '{args.set_default}'.", file=sys.stderr)
            _list_leagues(sys.stderr)
            return 2
        print(f"'{args.set_default}' is now the league that `python3 draft.py` uses.")
        return 0

    if args.espn_login:
        return _espn_login()

    # ---- which saved league are we running? ------------------------------
    # --use names one; --save-as names a new one; otherwise it is the default.
    name = args.use or args.save_as or config_mod.default_name()
    known = config_mod.names()
    if args.use and args.use not in known:
        print(f"error: no saved league called '{args.use}'.", file=sys.stderr)
        _list_leagues(sys.stderr)
        return 2

    # Saved settings fill in anything not given on the command line, so the
    # everyday case is just `python3 draft.py`.
    saved = config_mod.load(name=name)
    args.username = args.username or saved.get("username", "")
    args.league = args.league or saved.get("league_id", "")
    args.draft = args.draft or saved.get("draft_id", "")
    args.rankings = args.rankings or saved.get("rankings", "")
    args.team = (args.team or saved.get("favorite_team", "")).upper()
    args.season = args.season or saved.get("season", "")
    args.team_id = args.team_id or saved.get("team_id", "")

    # The site to talk to is remembered per league, so a saved ESPN league does
    # not need --espn typed at it every time. An explicit --espn still wins.
    provider = "espn" if args.espn else str(saved.get("provider") or "sleeper").lower()
    args.espn = provider == "espn"

    # A team id identifies you on ESPN and nowhere else: Sleeper has a username
    # for that, and the demo league invents its own ids, so handing either one
    # an ESPN team id would point "your roster" at a team that is not yours.
    my_team_id = args.team_id if (args.espn and not args.demo) else ""
    if not args.demo:
        logging.info("league '%s' on %s", name, provider.upper())

    if args.save or args.save_as:
        path = config_mod.save(
            {
                "provider": provider,
                "username": args.username,
                "league_id": args.league,
                "draft_id": args.draft,
                "season": args.season,
                "team_id": args.team_id,
                "favorite_team": args.team,
                "rankings": args.rankings,
            },
            name=name,
        )
        print(f"saved league '{name}' to {path}")
        if len(config_mod.names()) > 1:
            print(f"run it with:  python3 draft.py --use {name}")

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
                EspnClient(args.league, args.season, refresh=args.refresh),
                username=args.username,
                league_id=args.league, provider="ESPN", team_id=args.team_id,
            )
        sleeper = SleeperClient()
        sleeper.refresh = args.refresh
        return preflight_mod.run(
            sleeper, username=args.username, league_id=args.league, draft_id=args.draft
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
            client = EspnClient(args.league, args.season, refresh=args.refresh)
        else:
            client = SleeperClient()
            client.refresh = args.refresh
        session = Session(client, user_id=my_team_id)
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
        client = EspnClient(args.league, args.season, refresh=args.refresh)
    else:
        client = SleeperClient()
        client.refresh = args.refresh

    session = Session(client, csv_path=csv_path, favorite_team=args.team,
                      user_id=my_team_id)

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

    label = "Demo" if args.demo else ("ESPN" if args.espn else "Sleeper")
    print()
    print(f"  {label} draft board is running."
          + ("   [DEMO — synthetic data]" if args.demo else f"   [league '{name}']"))
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


def _list_leagues(stream=sys.stdout) -> int:
    """Print the saved leagues, so nobody has to open the config file."""
    saved = config_mod.names()
    if not saved:
        print(f"\nNo leagues saved yet in {config_mod.CONFIG_NAME}.\n"
              "Save one by adding --save-as <a short name> to a working command, e.g.\n"
              "  python3 draft.py --username you --league 123456 --save-as sleeper\n",
              file=stream)
        return 0
    default = config_mod.default_name()
    print(f"\nLeagues saved in {config_mod.CONFIG_NAME}:\n", file=stream)
    for name in saved:
        mark = " *" if name == default else "  "
        print(mark + " " + config_mod.describe(name, config_mod.load(name=name)), file=stream)
    print(f"\n  * = the one `python3 draft.py` uses with no flags.\n"
          f"  Run another with:            python3 draft.py --use <name>\n"
          f"  Change which one is starred: python3 draft.py --set-default <name>\n",
          file=stream)
    return 0


def _espn_ready(args) -> str:
    """Empty string when we have what ESPN needs, else what is missing."""
    if not args.league:
        return ("error: ESPN needs a league id.  It is the number in your league URL:\n"
                "  https://fantasy.espn.com/football/league?leagueId=123456&teamId=7&seasonId=2026\n"
                "  python3 draft.py --espn --league 123456 --season 2026 --team-id 7")
    if not args.season:
        return "error: ESPN needs --season (e.g. --season 2026)"
    if not creds_mod.espn_cookies():
        # Not fatal: plenty of ESPN leagues are readable without logging in, and
        # only ESPN can say whether yours is one of them. Try, and let the
        # adapter's 401/403 message send you to --espn-login if it is private.
        print("note: no ESPN cookies stored. Public leagues work without them; "
              "a private league will answer with an access error, and then you "
              "run:  python3 draft.py --espn-login", file=sys.stderr)
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
