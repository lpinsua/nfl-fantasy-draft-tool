# CLAUDE.md

Context for Claude when working in this repository.

## Whose league this is

Saved in `draft.config.json`, which the tool reads as defaults:

| | |
|---|---|
| Sleeper username | `lpinsua` |
| League ID | `1389723692459638784` (https://sleeper.com/leagues/1389723692459638784/) |
| Favourite team | **Miami Dolphins (MIA)** — highlighted on the board |

**None of this is a credential.** Sleeper's API needs no password, token or key;
a username and league id are public read-only identifiers. This repository is
**public**, so never add anything here that actually is a secret.

The everyday command is therefore just:

```bash
python3 draft.py                  # uses the saved defaults
python3 draft.py --preflight      # same, but check and exit
```

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
