"""Reconciles logged edge-calculator bets against actual outcomes once
their games are Final, and reports whether the true-correlated joint
probability has actually been better calibrated than the naive
independent one -- this is what answers "is this viable" empirically.

Safe to run any time (e.g. piggyback on the existing weekly launchd job) --
only touches rows still marked unresolved, and a missing join usually just
means the game hasn't finished yet (or a late scratch changed the starter).

Usage:
    python scripts/reconcile_edge_log.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd

from mlb_analytics.config import SEASON_DATE_RANGES, season_key
from mlb_analytics.storage import S3Storage

EDGE_LOG_KEY = "edge_log/log.parquet"


def load_all_history(storage: S3Storage) -> pd.DataFrame:
    frames = []
    for year in sorted(SEASON_DATE_RANGES):
        key = season_key(year)
        if storage.exists(key):
            frames.append(storage.load(key))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main():
    storage = S3Storage()
    if not storage.exists(EDGE_LOG_KEY):
        print("No edge log found yet -- nothing to reconcile.")
        return

    log = storage.load(EDGE_LOG_KEY)
    unresolved = log[~log["resolved"]]
    if unresolved.empty:
        print(f"All {len(log)} logged bets already resolved. Nothing new to reconcile.")
    else:
        history = load_all_history(storage)
        outcomes = history[["game_pk", "pitcher_id", "team_won", "strikeouts"]].rename(
            columns={"team_won": "actual_team_won", "strikeouts": "actual_strikeouts"}
        )

        base = unresolved.drop(columns=["actual_team_won", "actual_strikeouts"])
        merged = base.merge(outcomes, on=["game_pk", "pitcher_id"], how="left")
        newly_found = merged["actual_team_won"].notna() | merged["actual_strikeouts"].notna()
        merged.loc[newly_found, "resolved"] = True

        log = log.set_index(["game_pk", "pitcher_id", "logged_at"])
        merged_indexed = merged.set_index(["game_pk", "pitcher_id", "logged_at"])
        log.update(merged_indexed)
        log = log.reset_index()
        storage.save(log, EDGE_LOG_KEY)

        print(f"Newly resolved: {int(newly_found.sum())} / {len(unresolved)} previously-unresolved bets "
              f"(the rest haven't played yet, or the logged pitcher was scratched).")

    resolved = log[(log["resolved"] == True) & log["actual_team_won"].notna()].copy()  # noqa: E712
    if resolved.empty:
        print("No bets resolved with a clear win/loss yet -- nothing to score.")
        return

    resolved["k_hit"] = resolved["actual_strikeouts"] >= resolved["k_line"]
    resolved["joint_actual"] = (resolved["actual_team_won"].astype(bool) & resolved["k_hit"]).astype(int)

    brier_naive = ((resolved["naive_joint"] - resolved["joint_actual"]) ** 2).mean()
    brier_true = ((resolved["true_joint"] - resolved["joint_actual"]) ** 2).mean()

    print()
    print(f"Resolved bets scored: {len(resolved)}")
    print(f"Observed joint hit rate (team won AND K-prop hit): {resolved['joint_actual'].mean():.1%}")
    print(f"Naive independent joint:  avg predicted {resolved['naive_joint'].mean():.1%}  |  Brier score {brier_naive:.4f} (lower is better)")
    print(f"True correlated joint:    avg predicted {resolved['true_joint'].mean():.1%}  |  Brier score {brier_true:.4f} (lower is better)")
    if len(resolved) < 20:
        print(f"(Only {len(resolved)} resolved bets -- too small a sample to draw real conclusions yet.)")
    elif brier_true < brier_naive:
        print("-> The true-correlated model has been better calibrated on this sample so far.")
    elif brier_true > brier_naive:
        print("-> Naive independent pricing has actually been more accurate on this sample so far.")
    else:
        print("-> Tied.")


if __name__ == "__main__":
    main()
