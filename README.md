# Fantasy draft board — Sleeper and ESPN

A live draft assistant for Sleeper and ESPN fantasy football. It connects to your league,
detects your settings, tracks picks as they happen, and tells you who to take —
with a reason.

No dependencies. No pip install, no API key, no account linking.
Python 3.9+ and a browser.

---

## Draft night, in three commands

```bash
# 1. Hours before the draft — check everything works against YOUR league
python3 draft.py --preflight --username <your-sleeper-username>

# 2. Anytime — rehearse the interface with no network
python3 draft.py --demo

# 3. Draft night
python3 draft.py --username <your-sleeper-username>
```

Then open <http://127.0.0.1:8777> (it opens by itself) and pick your league.

**Run the preflight check before draft day, not five minutes before.** It exercises
every API call the board depends on and prints what it detected, so if anything is
off you find out with hours to fix it rather than while you are on the clock.

---

## What it actually does

Rankings you can get anywhere. The question this answers is the one you face on
the clock: *given who is gone, what my roster needs, and how long until I pick
again, who do I take right now?*

**Your league's real scoring.** It reads your `scoring_settings` from Sleeper and
scores every player against it. Six-point passing TDs, TE premium, custom return
yardage — whatever you actually play, that is what the projections are converted
with. You configure nothing.

**Value over replacement, not raw points.** A 280-point QB can be worth less than a
150-point TE, because what matters is the gap to the next guy at that position.
Replacement level is computed by simulating every team filling every starting slot
— dedicated spots first, then flex taken greedily by the best eligible player left.
Superflex inflates QB value automatically. A third flex deepens RB/WR/TE
automatically. Nothing is hardcoded.

**Tiers from the actual data.** A tier break is declared where the drop to the next
player is more than one standard deviation above the typical gap — the "cliff after
this guy" you would otherwise eyeball.

**Value over next available (VONA).** The real decision. Using ADP as a
distribution rather than a fixed number, it computes the expected best player at
each position when your *next* turn comes, and ranks by what you lose by waiting.
This is why it will tell you to take the TE now over a slightly better RB when the
RB tier runs ten deep.

Every suggestion says why: *"last TE in tier 1"*, *"31% to last until your next
pick"*, *"4 WRs gone in last 10"*.

---

## The board

| Column | Meaning |
|---|---|
| **Tier** | Tier within position. Take the last player in a tier over the first of the next. |
| **Proj** | Projected season points **under your league's scoring**. |
| **VORP** | Points above replacement. This is the ranking that matters. |
| **ADP** | Average draft position, in your league's format (2QB ADP if superflex). |
| **Val** | Picks he has fallen past his ADP (current pick − ADP). Green = the room let him slide to you. Red = you'd be reaching. |

Filter by position, search by name, and click **×** to mark anyone off the board by
hand.

---

## When something goes wrong mid-draft

The tool is built to degrade rather than fail. In rough order of likelihood:

- **A pick is missing or wrong.** Click **×** next to the player to mark them gone.
  Manual marks survive the background sync — they are not overwritten.
- **The board looks stale.** Hit **↻** in the top right to force a resync.
- **The sync dot goes red.** It keeps retrying on its own. The board stays usable
  from the last good state; nothing is lost.
- **"Projections unavailable".** Sleeper's projections endpoint is undocumented and
  is the one thing that could change without notice. The board falls back to
  ADP-only ordering and says so in a banner. Still usable — but the numbers become
  ordinal, not real point estimates.
- **Username won't resolve.** Use *Connect by ID instead* and paste the draft ID
  from your Sleeper draft URL.

---

## Options

```
--espn                 use ESPN instead of Sleeper (with --league and --season)
--espn-login           store ESPN cookies for a private league, then exit
--season YEAR          season year, required for ESPN
--preflight            check connectivity and your league, print findings, exit
--review               grade the finished draft against the rest of the league
--username NAME        your Sleeper username
--league ID            skip the picker, connect straight to a league
--draft ID             skip the picker, connect straight to a draft
--team ABBR            your team (e.g. MIA) — highlighted in its own colours
--save                 remember these settings, so next time needs no flags
--rankings FILE.csv    layer your own rankings over Sleeper's projections
--demo                 synthetic league, no network
--demo-type TYPE       snake | linear | auction   (with --demo)
--demo-picks N         start N picks in                (with --demo)
--demo-speed SECS      seconds per simulated pick, 0 to freeze  (with --demo)
--port N               default 8777
--no-browser           don't open a browser
--verbose              debug logging
```

### Remembering your league

Run once with `--save` and every later run needs no flags at all:

```bash
python3 draft.py --username you --league 123456789 --team MIA --save
python3 draft.py            # from now on, this is the whole command
```

Settings land in `draft.config.json`. Nothing there is a credential — Sleeper's
API takes no password, token or key, and a username and league id are public
read-only identifiers. Don't add anything genuinely secret to that file.

### Theme and your team

- **🌙 / ☀️** in the top right toggles dark and light. It follows your OS on
  first load, then remembers your choice.
- `--team MIA` paints your team's players in their real colours, marks them with
  the team emoji, and adds a **🐬 Dolphins only** filter beside *show drafted* —
  handy for spotting which of your guys are still on the board.
- **?** (button or keypress) opens a glossary of every column and term.

### Take now vs Falling

Two panels, deliberately answering different questions:

- **Take now** is advice. It weights value by what your roster still needs, and
  hides positions you can no longer start — a second quarterback in a one-QB
  league is a bench piece however good he is.
- **Falling** is information. Good players the room has let slide well past
  their ADP, *whatever* your needs, including the positions Take now is hiding.
  It tells you an elite tight end is sitting there at a 26-pick discount, and
  also that you already start one. The call is yours.

### ESPN leagues

Everything above works for ESPN too — same board, same VORP, same review. The
ESPN client just presents ESPN as if it were Sleeper, so nothing downstream
knows the difference.

```bash
python3 draft.py --espn-login                                   # once, private leagues only
python3 draft.py --espn --preflight --league 123456 --season 2026
python3 draft.py --espn --league 123456 --season 2026 --save     # then --espn alone
```

Your league id is the number in the league URL:
`fantasy.espn.com/football/league?leagueId=`**`123456`**

**Private leagues need credentials — real ones.** ESPN authenticates with the
`espn_s2` and `SWID` cookies from a logged-in browser, and those grant access to
your ESPN account, not just one league. `--espn-login` walks you through finding
them and stores them in `~/.config/draftkit/secrets.json`, readable only by you
and **never** in this repository, which is public. Logging out of ESPN
everywhere invalidates them.

Two differences worth knowing:

- **Scoring is exact without any rule mapping.** ESPN returns projections it has
  already scored under your league's own settings, so this does not try to
  reimplement ESPN's scoring from its numeric stat ids.
- **ADP is a draft rank, not an average pick.** ESPN publishes ranks rather than
  average draft position. Same ordering, which is all the model uses it for, but
  the "fell N picks past ADP" figures are looser than on Sleeper.

### Using your own rankings

Any CSV with a name column works, including a FantasyPros export. Recognised value
columns: `projected_points` / `fpts` / `points`, then `adp` / `rank` / `overall`.
Names are matched loosely, so "A.J. Brown" and "AJ Brown" resolve to the same player.

```bash
python3 draft.py --username you --rankings ~/Downloads/fantasypros.csv
```

---

## Limitations, stated plainly

- **It cannot make picks for you.** Sleeper's API is read-only — there is no
  endpoint that drafts a player. The tool advises; you click in Sleeper.
- **Auction drafts are only partly supported.** Auctions are detected, and player
  values, tiers, roster needs and budget tracking all work. But pick-timing advice
  is hidden because it is meaningless without a pick order, and there is **no
  $-value or max-bid guidance**. The board warns you at the top when it detects one.
- **Projections are Sleeper's own.** Decent, not elite. Use `--rankings` for a
  sharper source.
- **Bye weeks and handcuffs are not modelled.**

---

## How it is put together

```
draft.py              entry point / CLI
draftkit/
  api.py              Sleeper client: retries, disk cache, 404-as-None
  scoring.py          projected stat line x league scoring -> points
  league.py           roster/scoring parsing, replacement levels, positional need
  values.py           board build: points -> VORP -> tiers -> ranks
  draftstate.py       snake math, live picks, availability, recommendations
  session.py          connected league + background poller
  server.py           stdlib HTTP server + JSON API
  espn.py             ESPN, presented as if it were Sleeper
  credentials.py      ESPN cookies, stored outside the repo
  preflight.py        the pre-draft self check
  demo.py             synthetic league (shared with the tests)
web/                  index.html / app.js / style.css
tests/                90 offline tests
```

Sleeper endpoints used, all public and unauthenticated:
`/v1/user/<name>`, `/v1/user/<id>/leagues/nfl/<season>`, `/v1/league/<id>`,
`/v1/league/<id>/users`, `/v1/league/<id>/drafts`, `/v1/draft/<id>`,
`/v1/draft/<id>/picks`, `/v1/players/nfl`, and the projections host
`api.sleeper.com/projections/nfl/<season>`.

The ~5MB player file is cached to `~/.cache/draftkit` for 12 hours; Sleeper asks
callers to fetch it no more than once a day. Live picks are polled every 2.5s,
which is well inside their rate limits.

## Tests

```bash
python3 -m unittest discover -s tests
```

90 tests, no network required. They cover snake/linear/third-round-reversal pick
math, flex and superflex replacement levels, league-exact scoring, tier
assignment, availability, the auction degradation path, and a full HTTP
end-to-end run against a stubbed Sleeper.
