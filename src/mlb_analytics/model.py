"""Conditional win-probability model.

The question this answers per pitcher: "in starts against a weaker
opponent (higher season ERA than his own), how often does his team win
when he strikes out at least as many batters as he usually does?"

This is deliberately simple/empirical (no regression, no park factors, no
recency weighting) -- an interpretable baseline, not a polished forecasting
model. Season ERA is computed with full-season hindsight (earned_runs/outs
summed over the whole season), which is a mild look-ahead simplification
when applied to early-season historical starts; noted as a known limitation
rather than solved here.
"""

import pandas as pd

MIN_QUALIFYING_STARTS = 5
MIN_SEASON_STARTS_FOR_ERA = 3


def compute_season_era(history: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, pitcher_id) with that season's aggregate ERA.

    Aggregate ERA (sum(earned_runs)*27/sum(outs)) rather than averaging the
    per-game era column -- correct way to combine starts of different length.
    """
    grouped = history.groupby(["season", "pitcher_id"], as_index=False).agg(
        pitcher_name=("pitcher_name", "last"),
        season_starts=("game_pk", "count"),
        season_outs=("outs", "sum"),
        season_earned_runs=("earned_runs", "sum"),
    )
    grouped["season_era"] = grouped["season_earned_runs"] * 27 / grouped["season_outs"]
    return grouped


def add_opponent_era(starts: pd.DataFrame, season_era: pd.DataFrame) -> pd.DataFrame:
    """Attach each start's own season_era and the opposing starter's season_era.

    Self-joins `starts` on (game_pk, opponent==team) to find the other row
    for the same game -- every game has exactly one home and one away
    starter row, and each row's `opponent` field names the other row's `team`.
    """
    merged = starts.merge(
        season_era[["season", "pitcher_id", "season_era"]],
        on=["season", "pitcher_id"],
        how="left",
    )

    opponent_info = merged[["game_pk", "team", "pitcher_id", "season_era"]].rename(
        columns={
            "team": "opponent",
            "pitcher_id": "opponent_pitcher_id",
            "season_era": "opponent_season_era",
        }
    )
    merged = merged.merge(opponent_info, on=["game_pk", "opponent"], how="left")
    return merged


def conditional_win_probabilities(
    starts_with_opponent_era: pd.DataFrame,
    metric_col: str = "strikeouts",
    min_qualifying_starts: int = MIN_QUALIFYING_STARTS,
) -> pd.DataFrame:
    """Per pitcher: P(team won | metric_col >= his own median) restricted
    to starts where the opposing starter had a worse (higher) season ERA.

    `metric_col` defaults to "strikeouts"; also used with "outs" (how deep
    a pitcher typically goes) as a second, independent success dimension --
    same self-referential median-threshold pattern either way, so this
    function doesn't need to know which one it's computing.

    Pitchers with fewer than `min_qualifying_starts` such starts are
    excluded -- too little data for a stable probability estimate.
    """
    df = starts_with_opponent_era.copy()
    df["median_metric"] = df.groupby("pitcher_id")[metric_col].transform("median")

    qualifying = df[
        (df["opponent_season_era"] > df["season_era"])
        & df["team_won"].notna()  # exclude the rare tie with no recorded winner
    ]

    results = []
    for pitcher_id, group in qualifying.groupby("pitcher_id"):
        if len(group) < min_qualifying_starts:
            continue

        median_metric = group["median_metric"].iloc[0]
        strong_starts = group[group[metric_col] >= median_metric]
        if strong_starts.empty:
            continue

        baseline_win_prob = group["team_won"].mean()
        conditional_win_prob = strong_starts["team_won"].mean()

        results.append({
            "pitcher_id": pitcher_id,
            "pitcher_name": group["pitcher_name"].iloc[-1],
            "median_metric": median_metric,
            "qualifying_starts": len(group),
            "strong_starts": len(strong_starts),
            # P(meets usual threshold | facing a weaker opponent) -- not
            # necessarily 0.5, since median_metric is computed over ALL
            # starts, not just this opponent-quality-filtered subset.
            "p_strong_metric": len(strong_starts) / len(group),
            # Win rate in the qualifying subset regardless of metric_col
            # performance -- isolates the "facing a weaker opponent" effect
            # alone, so lift below measures the metric's effect on top of
            # that, not conflated with it.
            "baseline_win_prob": baseline_win_prob,
            "conditional_win_prob": conditional_win_prob,
            # How much a strong start (by this metric) multiplies win
            # probability above the baseline for the same pitcher/matchup context.
            "lift_ratio": conditional_win_prob / baseline_win_prob if baseline_win_prob > 0 else float("nan"),
        })

    return pd.DataFrame(results)


def compute_recommendation_score(
    k_table: pd.DataFrame, outs_table: pd.DataFrame, weight_k: float = 0.5
) -> pd.DataFrame:
    """Blend K-based and outs-based lift into one ranking score per pitcher.

    `k_table`/`outs_table` come from conditional_win_probabilities() called
    with metric_col="strikeouts" and metric_col="outs" respectively. Inner
    join -- a pitcher needs enough qualifying starts on BOTH dimensions to
    get a blended score. qualifying_starts/baseline_win_prob are identical
    between the two tables (same underlying qualifying subset, just split
    two different ways), so only kept once, from k_table.
    """
    k = k_table.rename(columns={
        "median_metric": "median_k",
        "p_strong_metric": "p_strong_k",
        "strong_starts": "strong_starts_k",
        "conditional_win_prob": "conditional_win_prob_k",
        "lift_ratio": "lift_ratio_k",
    })
    outs = outs_table.rename(columns={
        "median_metric": "median_outs",
        "p_strong_metric": "p_strong_outs",
        "strong_starts": "strong_starts_outs",
        "conditional_win_prob": "conditional_win_prob_outs",
        "lift_ratio": "lift_ratio_outs",
    })[["pitcher_id", "median_outs", "p_strong_outs", "strong_starts_outs",
        "conditional_win_prob_outs", "lift_ratio_outs"]]

    merged = k.merge(outs, on="pitcher_id", how="inner")
    merged["recommendation_score"] = (
        weight_k * merged["lift_ratio_k"] + (1 - weight_k) * merged["lift_ratio_outs"]
    )
    return merged
