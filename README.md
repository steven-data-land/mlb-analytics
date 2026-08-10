# mlb-analytics

Pulls per-game starting pitcher stats (strikeouts, ERA, pitch count) and that
team's win/loss result from the MLB Stats API, using raw `requests` calls
(no wrapper library) so the HTTP/JSON mechanics stay visible.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## The API

Everything comes from `https://statsapi.mlb.com/api/v1` — this is the same
JSON feed MLB.com itself uses internally. It's unofficial (no API key, no
published docs, no enforced rate limit), so field paths below were confirmed
by hitting the live endpoints directly rather than trusting secondhand docs.

Two endpoints are used:

**`GET /schedule?sportId=1&date=...`** (or `&startDate=...&endDate=...` for a
range) — returns `dates[].games[]`. Each game gives you:
- `gamePk` — the ID needed to fetch the boxscore
- `gameType` — `"R"` for regular season (filter out spring training/postseason/all-star)
- `status.abstractGameState` — only trust results once this is `"Final"`
- `teams.home/away.isWinner` — **the** source of truth for win/loss

**`GET /game/{gamePk}/boxscore`** — returns `teams.home/away.players`, a dict
keyed `"ID<personId>"`. Each player has `stats.pitching` with:
- `gamesStarted == 1` — **the** authoritative way to identify the starter
  (not list order, not the schedule's `probablePitcher`, which is a
  pre-game guess that doesn't account for late scratches)
- `strikeOuts`, `earnedRuns` — as you'd expect
- `outs` — exact inning-thirds pitched (18 outs = 6.0 IP). **Use this for
  ERA math**, not the `inningsPitched` string field — that field is
  formatted in baseball's `.1`/`.2` thirds notation (e.g. `"6.1"` means 6⅓
  innings, not 6.1 decimal innings) and will silently produce wrong numbers
  if parsed as a float. ERA here = `earnedRuns * 27 / outs`.
- `numberOfPitches` — pitch count

There's no documented rate limit, but the API sits behind a CDN with only a
20s cache, so this project adds a small delay + retry/backoff between
requests as a courtesy (see `api_client.py`).

## How it's built

```
app.py                       # Streamlit dashboard — see "Dashboard" below
pages/
  1_Edge_Calculator.py     # betting-edge calculator — see "Betting edge" below
scripts/
  fetch_one_day.py        # start here — one date, one game, prints parsed output
  run_collect.py           # local batch CLI — pull a range/season to a CSV/Parquet file
  bulk_upload_history.py    # one-time backfill — every configured season -> S3
  run_weekly_update.py       # incremental refresh — current season's new games -> S3
  reconcile_edge_log.py       # backfills real outcomes into logged bets, scores calibration
src/mlb_analytics/
  api_client.py        # get_schedule(), get_boxscore() — requests + retry + delay
  parsing.py            # JSON -> starter record dicts (pure functions, no I/O)
  storage.py             # save/load records — Local CSV/Parquet and S3 backends
  collect.py              # ties the above together: date range -> DataFrame
  config.py                 # bucket/region, S3 key naming, SEASON_DATE_RANGES
  live.py                    # today/tomorrow's probable starters (pre-game)
  model.py                    # conditional win-probability model — see "Dashboard"
  edge.py                      # odds math for the betting-edge calculator
  dashboard_data.py              # cached data loaders shared by app.py and pages/*.py
infra/
  iam_policy.json          # least-privilege policy for the S3 uploader IAM user
  launchd/                  # macOS scheduling for the weekly refresh
```

The layers are separated on purpose — you can reason about "the HTTP part"
(`api_client.py`), "the parsing part" (`parsing.py`), and "the storage part"
(`storage.py`) independently, and swap any one of them without touching the
others.

Each starter record looks like:

```python
{
    "date": "2024-07-01",
    "game_pk": 744914,
    "team": "Houston Astros",
    "opponent": "Toronto Blue Jays",
    "pitcher_id": 686613,
    "pitcher_name": "Hunter Brown",
    "outs": 18,
    "strikeouts": 5,
    "earned_runs": 0,
    "era": 0.0,
    "pitch_count": 99,
    "team_won": True,
}
```

## Usage

See the raw API shapes and one parsed game (good first step, no CLI args):

```bash
python scripts/fetch_one_day.py
```

Batch-pull a date range or a full season into a file:

```bash
python scripts/run_collect.py --start-date 2024-07-01 --end-date 2024-07-07 --out data/week.csv
python scripts/run_collect.py --season 2024 --out data/season_2024.parquet --format parquet
```

`--season` resolves to that year's known regular-season date range (see
`SEASON_DATE_RANGES` in `config.py`); add a year there if it's missing.
Output goes to `data/`, which is gitignored.

## Cloud storage (S3)

Data lives at `s3://mlb-analytics-pitcher-data`, one Parquet object per
season: `starters/season=<year>/data.parquet` (see `config.season_key()`).
`S3Storage` (in `storage.py`) implements the same `save`/`load` shape as the
local backends — `collect.py` never has to know which one it's talking to.

Auth is via the standard AWS shared credentials file (`~/.aws/credentials`,
set up once via `aws configure`) — no keys are stored in this repo. The
credentials belong to a dedicated IAM user (`mlb-analytics-uploader`) scoped
to only this bucket via `infra/iam_policy.json` — not admin/root keys.

**One-time historical backfill** (2021–present, one call per configured
season — slow, ~15-30+ min per season due to the courtesy delay on every
boxscore call):

```bash
python scripts/bulk_upload_history.py                    # all configured seasons
python scripts/bulk_upload_history.py --seasons 2024 2025  # just these
```

**Weekly incremental refresh** (current season only): finds the max date
already stored in S3, re-pulls from a few days before that (to catch
late-finalized box scores) through today, de-dupes on `(game_pk,
pitcher_id)`, and overwrites the season's object. It's idempotent — safe to
run manually any number of times, in addition to its schedule:

```bash
python scripts/run_weekly_update.py                # infers current season from today's date
python scripts/run_weekly_update.py --season 2026
```

This runs automatically every Monday via a macOS `launchd` job (only fires
while this Mac is on). Set up once with:

```bash
cp infra/launchd/com.mlb-analytics.weekly-update.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.mlb-analytics.weekly-update.plist
```

Check it's loaded with `launchctl list | grep mlb-analytics`, trigger it
on-demand with `launchctl start com.mlb-analytics.weekly-update`, and read
its output in `logs/weekly_update.log` (gitignored). Undo any time with:

```bash
launchctl unload ~/Library/LaunchAgents/com.mlb-analytics.weekly-update.plist
rm ~/Library/LaunchAgents/com.mlb-analytics.weekly-update.plist
```

**Cost**: at this data volume (tens of thousands of rows, a few MB total),
S3 storage/request/transfer costs round to a fraction of a cent per month.
A $5/month AWS Budget alert is configured as a safety net, not because
this is expected to get anywhere near it.

## Dashboard

`app.py` is a Streamlit dashboard that ranks today's and tomorrow's probable
starters by two historical signals blended together: **"in starts against a
weaker opponent, how often does this pitcher's team win when he (a) strikes
out at least as many batters as usual, and (b) goes as deep into the game as
usual?"**

```bash
streamlit run app.py
```

### Deploying (Streamlit Community Cloud)

Free, and built for exactly this -- deploys straight from this GitHub repo
and gives back a public `*.streamlit.app` URL reachable from any device,
no need to keep a machine running.

1. Push this repo to GitHub (already set up as the `origin` remote here).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with
   GitHub, and click "New app". Point it at this repo, branch `main`, main
   file path `app.py`.
3. Before (or after) the first deploy, open the app's **Settings -> Secrets**
   and paste in an `[aws]` table with credentials for the
   `mlb-analytics-uploader` IAM user (the same one `~/.aws/credentials` uses
   locally -- see "Cloud storage (S3)" above; it's already scoped to just
   this one bucket, never admin/root keys):

   ```toml
   [aws]
   AWS_ACCESS_KEY_ID = "..."
   AWS_SECRET_ACCESS_KEY = "..."
   ```

   `storage._load_aws_credentials_from_secrets()` picks these up at import
   time and mirrors them into the environment, since there's no
   `~/.aws/credentials` file on Streamlit's servers and boto3's default
   credential chain otherwise has nothing to find. Locally, where a
   `secrets.toml` never exists, this is a no-op and boto3 keeps using
   `~/.aws/credentials` exactly as before -- nothing about local dev changes.
4. Every `git push` to `main` redeploys the app automatically.

How it works (`src/mlb_analytics/model.py`):

1. **Season ERA** is computed per pitcher per season (`earned_runs*27/outs`
   summed across the season, not an average of per-game ERA — the correct
   way to combine starts of different length).
2. **Opponent quality gate**: for a given start, the opposing team's starter
   that same game must have a *worse* (higher) season ERA. Only these
   "qualifying starts" count toward the model.
3. **"Strong" performance is self-referential**, computed independently for
   two metrics — strikeouts and outs recorded (how deep he went) — each
   pitcher's own historical median across all his starts, not a fixed
   league-wide number. `conditional_win_probabilities()` takes a
   `metric_col` parameter so the exact same logic runs for both dimensions
   rather than duplicating it.
4. **Lift** = P(team won | metric at/above median, qualifying subset) ÷
   P(team won | qualifying subset, regardless of that metric) — how much a
   strong performance on that one dimension multiplies the win probability
   above the baseline "faced a weaker opponent" effect alone. Computed
   separately as `lift_ratio_k` and `lift_ratio_outs`. Pitchers with fewer
   than `model.MIN_QUALIFYING_STARTS` (5) qualifying starts on a dimension
   are excluded from it — too little data for a stable estimate.
5. **Recommendation Score** (`compute_recommendation_score()`) blends the
   two: `weight_k * lift_ratio_k + (1 - weight_k) * lift_ratio_outs`. This is
   the dashboard's primary sort order. `weight_k` is a live slider (default
   50/50) — 100% ranks purely by strikeout lift, 0% purely by outs lift.

For today/tomorrow's actual matchups, the opponent-quality gate applies
using each probable starter's **current-season** ERA (`live.py` pulls
probable starters via the schedule's `probablePitcher` hydration, since
boxscore-based starter identification only exists for games that have
already been played — and `live.py` further restricts to `"Preview"`-state
games, so a game that's already started or finished doesn't show up as
"upcoming"). A pitcher needs at least `model.MIN_SEASON_STARTS_FOR_ERA` (3)
starts *this* season before his current ERA is trusted for the gate —
otherwise a single shutout start would look like ace-level performance by
pure small-sample noise.

This is a deliberately simple, interpretable baseline — an empirical rate,
not a regression, with no park factors, no recency weighting, no
opponent-lineup context. Season ERA also uses full-season hindsight, a mild
look-ahead simplification when applied to early-season historical starts.
Across the full 5-season dataset, median K lift is ≈1.09x — a real but
modest effect; individual pitchers with very high/low lift and few
qualifying starts should be treated skeptically (small-sample noise).

The ranked table is a per-column heatmap (white -> `#0ca30c` green, one hue
rather than a red/green pair, since red-green is unreadable for the ~8% of
men with red-green color vision deficiency): darker green is always
"better" for that column — lower for Pitcher ERA, higher for everything
else. See `style_ranked_table()` in `app.py`.

### Rain/delay risk by venue

Below the ranked matchups, a second visualization covers **every** game
scheduled tomorrow (not just the ones the ranking model above qualifies) with
a rough rain/delay-risk read, since a game that gets rained out doesn't care
how good the pitching matchup is.

For each game: look up its venue's coordinates (`GET /venues/{id}?hydrate=location`,
same MLB Stats API as everything else), then pull an hourly forecast for that
point from [Open-Meteo](https://open-meteo.com) -- free, no API key, no
account (`src/mlb_analytics/weather.py`, `dashboard_data.load_tomorrow_weather()`,
cached 30 min). The risk score looks only at the ~4-hour window around first
pitch (`weather.GAME_WINDOW_HOURS`), not the whole day, using peak
precipitation probability and total expected precipitation in that window
(`weather.classify_risk()`): **High** (>=60% chance or >=4mm), **Medium**
(>=30% or >=1mm), else **Low**. Domed and retractable-roof venues
(`weather.DOMED_VENUE_IDS`, all 8 MLB stadiums with a roof) are marked
**Indoor** instead of scored, since rain essentially can't delay a game
there regardless of forecast.

This is a heuristic, not an official delay prediction -- no radar, no
roof-open/closed state, no stadium-specific drainage/turf differences.

## Betting edge

The dashboard's lift signal doubles as a betting thesis: sportsbooks
typically apply a correlation discount when you combine a strikeout prop
with the moneyline into a same-game parlay, but some markets (e.g.
prediction-market RFQ fills) price the two legs as separate, independent
events. If the true correlation the model measures is stronger than that
independent-pricing assumption, buying both legs separately can be
underpriced relative to their real joint probability.

`pages/1_Edge_Calculator.py` (part of the same `streamlit run app.py`
multi-page app) quantifies this for a specific pitcher, using **only the
K-specific lift** (`lift_ratio_k`, not the blended Recommendation Score —
the strikeout prop is the leg actually being combined with the moneyline):

1. Enter American odds for the moneyline and the K-prop Over (and the
   opposing side of each, optionally, to de-vig — `edge.devig_pair()`
   normalizes two implied probabilities to sum to 1; without both sides you
   get the raw vig-included implied probability instead).
2. **Naive independent joint** = `p_win * p_strong_k` (`edge.naive_independent_joint()`)
   — what paying for both legs as separate contracts effectively costs you.
3. **True correlated joint** = `p_strong_k * min(p_win * lift_ratio_k, 1.0)`
   (`edge.true_correlated_joint()`) — the model's estimate of the real joint
   probability. Deliberately applies the lift *ratio* on top of the
   market's own win probability rather than substituting the model's own
   (much noisier, small-sample) historical win rate — the market's price is
   trusted as the better marginal estimate; the model only contributes the
   *correlation* structure on top of it.
4. **Edge** = true joint − naive joint, in probability points. If you also
   enter the book's actual combined/SGP price, its own implied probability
   is shown alongside for direct comparison.

"Log this bet" appends the entry (odds entered, computed probabilities, a
timestamp) to `s3://mlb-analytics-pitcher-data/edge_log/log.parquet` via the
same `S3Storage` class used everywhere else. Run
`python scripts/reconcile_edge_log.py` periodically (safe to re-run, only
touches still-unresolved rows) to backfill actual outcomes once those games
finish and score which estimate — naive or true-correlated — was actually
better calibrated (Brier score) on the resolved sample. This is what
answers "is this viable" empirically, once enough bets have been logged.

## Gotchas worth knowing

- **Doubleheaders** put two games (two `gamePk`s) under one `officialDate`.
- **Postponed/in-progress games** are filtered out via `abstractGameState != "Final"` — don't point a batch pull at the live, ongoing season expecting complete data for today's games.
- **Suspended-and-resumed games** can appear under two different date entries in one schedule response (the original date and the completion date), both `"Final"` once done — `collect.py` dedupes on `gamePk` before fetching boxscores to avoid double-counting.
- **Openers/bullpen games** occasionally have no traditional starter; `parsing.build_starter_record` returns `None` for that side rather than guessing, and those games are simply skipped.
- **Ties**: rare, but a handful of Final games (e.g. old rain-shortened games that weren't replayed) have no `isWinner` key on either team. `team_won` is `None` (not `False`) for those — distinct from an actual loss.
- **`live.py` only includes `"Preview"`-state games** in the probable-starters list — the schedule API keeps `probablePitcher` populated even for games that are already `"Live"` (in progress) or `"Final"`, which would otherwise make an already-started or finished game look "upcoming."
