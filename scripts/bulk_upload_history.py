"""One-time historical backfill: pulls every season in config.SEASON_DATE_RANGES
and uploads each season's starter records to S3 as its own Parquet object.

This is the slow one-time run -- thousands of boxscore calls with the
courtesy delay baked into api_client.py, likely 15-30+ min per full season.

Usage:
    python scripts/bulk_upload_history.py                # all configured seasons
    python scripts/bulk_upload_history.py --seasons 2023 2024   # just these
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mlb_analytics.collect import collect_starters
from mlb_analytics.config import SEASON_DATE_RANGES, season_key
from mlb_analytics.storage import S3Storage


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seasons", type=int, nargs="+", default=sorted(SEASON_DATE_RANGES),
        help="Season years to backfill (default: all configured seasons)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    storage = S3Storage()
    today = date.today().isoformat()

    for year in args.seasons:
        if year not in SEASON_DATE_RANGES:
            print(f"Skipping {year}: no known date range in config.SEASON_DATE_RANGES")
            continue

        start_date, end_date = SEASON_DATE_RANGES[year]
        if end_date > today:
            end_date = today  # season not finished yet -- pull what's played so far

        print(f"[{year}] collecting {start_date} to {end_date}...")
        df = collect_starters(start_date, end_date)
        key = season_key(year)
        storage.save(df, key)
        print(f"[{year}] saved {len(df)} rows from {df['game_pk'].nunique()} games "
              f"to s3://{storage.bucket}/{key}")


if __name__ == "__main__":
    main()
