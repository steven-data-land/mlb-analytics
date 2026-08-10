"""Weekly incremental refresh for the CURRENT season only.

Idempotent by design: instead of tracking a separate "last run" state file,
it derives its own starting point from the max date already stored in S3,
re-pulling the last few days as a buffer to catch late-finalized box scores,
then de-duping on (game_pk, pitcher_id) before overwriting. Safe to re-run
any time, including multiple times in the same day.

Usage:
    python scripts/run_weekly_update.py                # infers current season from today
    python scripts/run_weekly_update.py --season 2026
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd

from mlb_analytics.collect import collect_starters
from mlb_analytics.config import SEASON_DATE_RANGES, season_key
from mlb_analytics.storage import S3Storage

LATE_FINALIZATION_BUFFER_DAYS = 3


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=date.today().year)
    return parser.parse_args()


def main():
    args = parse_args()
    year = args.season
    if year not in SEASON_DATE_RANGES:
        raise SystemExit(f"No known date range for season {year} in config.SEASON_DATE_RANGES")

    season_start, season_end = SEASON_DATE_RANGES[year]
    today = date.today().isoformat()
    key = season_key(year)
    storage = S3Storage()

    if storage.exists(key):
        existing = storage.load(key)
        max_date = existing["date"].max()
        start_date = max(
            season_start,
            (datetime.fromisoformat(max_date) - timedelta(days=LATE_FINALIZATION_BUFFER_DAYS)).date().isoformat(),
        )
        print(f"[{year}] existing data through {max_date}; refreshing from {start_date}")
    else:
        existing = None
        start_date = season_start
        print(f"[{year}] no existing S3 data; pulling full season from {start_date}")

    end_date = min(season_end, today)
    if start_date > end_date:
        print(f"[{year}] nothing to do (start {start_date} is after end {end_date})")
        return

    new_rows = collect_starters(start_date, end_date)
    print(f"[{year}] collected {len(new_rows)} rows from {start_date} to {end_date}")

    if existing is not None:
        combined = pd.concat([existing, new_rows], ignore_index=True)
        combined = combined.drop_duplicates(subset=["game_pk", "pitcher_id"], keep="last")
    else:
        combined = new_rows

    added = len(combined) - (len(existing) if existing is not None else 0)
    storage.save(combined, key)
    print(f"[{year}] saved {len(combined)} total rows ({added} net new) to s3://{storage.bucket}/{key}")


if __name__ == "__main__":
    main()
