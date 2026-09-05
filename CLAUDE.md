# CLAUDE.md

Context for Claude when working in this repository.

## Whose leagues these are

Saved in `draft.config.json` under short names, which the tool reads as
defaults. Two are set up, and `sleeper` is the one that runs bare:

| name | site | league | notes |
|---|---|---|---|
| `sleeper` * | Sleeper | `1389723692459638784` (https://sleeper.com/leagues/1389723692459638784/) | username `lpinsua` |
| `espn` | ESPN | `884705387`, season 2026, team id `17` | needs cookies if private |

Favourite team is **Miami Dolphins (MIA)** on both — highlighted on the board.

**None of this is a credential.** Neither API needs a password, token or key for
these fields; usernames, league ids, season years and team ids are public
read-only identifiers. ESPN's `espn_s2`/`SWID` cookies *are* credentials and
live in `~/.config/draftkit/secrets.json`, never here. This repository is
**public**, so never add anything to it that actually is a secret.

The everyday commands are therefore:

```bash
python3 draft.py                  # the default league (sleeper)
python3 draft.py --preflight      # same, but check and exit
python3 draft.py --leagues        # what is saved, and which one is default
python3 draft.py --use espn       # the ESPN league
python3 draft.py --set-default espn   # make ESPN the bare-command one
```

Adding another league never disturbs the ones already saved:

```bash
python3 draft.py --espn --league <id> --season 2026 --team-id <n> --save-as <name>
```

`config.load()` still returns a flat dict of one league's settings, so callers
that do not care about multiple leagues never had to change; `load(name=...)`
picks a different one. An old flat config file is read as a single league
called `default` and upgraded in place the next time anything is saved.

## Providers

Sleeper and ESPN. `EspnClient` duck-types `SleeperClient` and returns
Sleeper-shaped dicts, so the board, value model, poller, web UI and review are
provider-agnostic — the same trick `DemoClient` uses. To add another site,
write another adapter; do not touch the engine.

**ESPN specifics.** Private leagues need the `espn_s2`/`SWID` cookies, which are
real credentials and live in `~/.config/draftkit/secrets.json`, never here.
Missing cookies are a warning, not a hard stop — plenty of ESPN leagues read
fine without them, and only ESPN can say whether one does. Identity is a team
id (`--team-id`, the `teamId=` in the league URL) rather than a username: it is
exact, needs no request, and is the only thing that works when not logged in.
`Session(user_id=...)` / `connect(..., user_id=...)` carry it.
ESPN pre-applies league scoring to projections (`appliedTotal`), so the adapter
sets `pts_league` rather than trying to replicate ESPN's stat-id scoring table.
ESPN is blocked from the sandbox exactly like Sleeper, so `tests/espn_fixtures.py`
carries hand-written payloads shaped like the real v3 responses.

## What this is

A zero-dependency draft assistant for Sleeper: stdlib Python plus vanilla JS,
no pip install, no build step. `python3 draft.py` serves a local web board that
polls the live draft and recommends picks.

## Working notes

- **The user is not a command-line user.** Give complete copy-pasteable
  commands, say which directory to run them in, and explain what "no output"
  means. Do not assume familiarity with terminals, git, or servers.
- **Sleeper is blocked from Claude Code's sandbox** (403 on CONNECT to
  `api.sleeper.app`). You cannot test against real data from a session — the
  tests run entirely on fixtures. `--preflight` exists precisely because the
  user has to be the one to validate against the real API.
- **Push requires the Claude GitHub App** to be installed on this repo. It was
  missing at first and caused 403s on both `git push` and the MCP write path.
- Run `python3 -m unittest discover -s tests` before any commit.
- `--demo` runs the whole board on synthetic data with no network, which is the
  fastest way to check a UI change.

## Layout

```
draft.py              CLI entry point
draftkit/
  api.py              Sleeper HTTP client (retries, disk cache)
  config.py           saved defaults in draft.config.json
  scoring.py          projected stats x league scoring -> points
  league.py           roster/scoring parsing, replacement levels, needs
  values.py           board build: points -> VORP -> tiers -> ranks
  draftstate.py       snake math, live picks, availability, recommendations
  session.py          connected league + background poller
  server.py           stdlib HTTP server + JSON API
  preflight.py        pre-draft self check
  demo.py             synthetic league (shared with the tests)
web/                  index.html / app.js / style.css
tests/                offline test suite
```

## Design decisions worth preserving

- **VORP, not raw points**, is the ranking. Replacement level is derived by
  simulating every team filling every starting slot, flex spots taken greedily.
  This is what makes superflex and extra flex slots work without special-casing.
- **Scoring is a dot product** of projected stats against the league's own
  `scoring_settings`, so custom rules need no code.
- **Degrade, never crash**: league-exact scoring -> Sleeper's precomputed totals
  -> ADP-only, with a banner saying which is in use.
- **Auctions are detected and partly supported**: values, tiers, needs and
  budget work; pick-timing advice is hidden because it is meaningless without a
  pick order. There is no $-value or max-bid model. If the user ever drafts in
  an auction, that is the gap to close first.
- The projections endpoint (`api.sleeper.com/projections/...`) is undocumented.
  It works, but it is the most likely thing to break; keep the fallbacks.
