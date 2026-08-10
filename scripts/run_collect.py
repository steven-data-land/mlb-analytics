"""CLI to batch-pull starting pitcher / team result data.

Examples:
    python scripts/run_collect.py --start-date 2024-07-01 --end-date 2024-07-07 --out data/july_2024.csv
    python scripts/run_collect.py --season 2024 --out data/season_2024.parquet --format parquet
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mlb_analytics.collect import collect_starters
from mlb_analytics.config import SEASON_DATE_RANGES
from mlb_analytics.storage import LocalCSVStorage, LocalParquetStorage

STORAGE_BACKENDS = {
    "csv": LocalCSVStorage,
    "parquet": LocalParquetStorage,
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--season", type=int, help="Full season year, e.g. 2024")
    date_group.add_argument("--start-date", type=str, help="YYYY-MM-DD (requires --end-date)")

    parser.add_argument("--end-date", type=str, help="YYYY-MM-DD (required with --start-date)")
    parser.add_argument("--out", type=str, required=True, help="Output file path")
    parser.add_argument("--format", type=str, choices=STORAGE_BACKENDS.keys(), default="csv")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.season is not None:
        if args.season not in SEASON_DATE_RANGES:
            raise SystemExit(f"No known date range for season {args.season}. "
                              f"Add it to SEASON_DATE_RANGES or use --start-date/--end-date.")
        start_date, end_date = SEASON_DATE_RANGES[args.season]
    else:
        if not args.end_date:
            raise SystemExit("--start-date requires --end-date")
        start_date, end_date = args.start_date, args.end_date

    print(f"Collecting starters from {start_date} to {end_date}...")
    df = collect_starters(start_date, end_date)
    print(f"Collected {len(df)} starter records from {df['game_pk'].nunique()} games.")

    storage = STORAGE_BACKENDS[args.format]()
    storage.save(df, args.out)
    print(f"Saved to {args.out}")


if __name__ == "__main__":
    main()
