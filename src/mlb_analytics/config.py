"""Non-secret config: bucket/region and S3 key naming. Credentials are never
stored here -- they come from the local ~/.aws/credentials file via boto3's
default credential chain (set up once via `aws configure`).
"""

S3_BUCKET = "mlb-analytics-pitcher-data"
AWS_REGION = "us-east-2"

# Approximate regular-season windows; good enough to bound a --season pull.
SEASON_DATE_RANGES = {
    2021: ("2021-04-01", "2021-10-03"),
    2022: ("2022-04-07", "2022-10-05"),
    2023: ("2023-03-30", "2023-10-01"),
    2024: ("2024-03-28", "2024-09-29"),
    2025: ("2025-03-27", "2025-09-28"),
    2026: ("2026-03-26", "2026-09-27"),
}


def season_key(year: int) -> str:
    return f"starters/season={year}/data.parquet"
