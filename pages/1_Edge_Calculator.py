"""Edge calculator: compares a naive "priced independently" joint
probability against the model's true-correlated joint probability for a
strikeout-prop + moneyline combo, given odds you enter manually.

See README.md "Betting edge" section for the full methodology.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd
import streamlit as st

from mlb_analytics import dashboard_data, edge
from mlb_analytics.config import S3_BUCKET
from mlb_analytics.storage import S3Storage

EDGE_LOG_KEY = "edge_log/log.parquet"

st.set_page_config(page_title="Edge Calculator", layout="wide")
st.title("Edge Calculator")
st.caption(
    "Compares what a strikeout-prop + moneyline combo would cost if priced as two "
    "independent contracts (a prediction-market RFQ fill, or any book that doesn't "
    "correlate the legs) against the model's true correlated probability. Uses the "
    "pitcher's **K-specific** lift only — not the blended Recommendation Score from "
    "the main dashboard — since the strikeout prop is the leg actually being combined "
    "with the moneyline here."
)

history = dashboard_data.load_history()
if history.empty:
    st.warning("No historical data found in S3 yet. Run scripts/bulk_upload_history.py first.")
    st.stop()

_, k_table, _ = dashboard_data.build_lift_tables(history)
probable = dashboard_data.load_probable_starters()

if probable.empty:
    st.info("No probable starters found for today/tomorrow.")
    st.stop()

probable = probable.merge(
    k_table[["pitcher_id", "median_metric", "qualifying_starts", "lift_ratio"]],
    on="pitcher_id", how="left",
).rename(columns={"median_metric": "median_k", "lift_ratio": "lift_ratio_k"})

probable["label"] = (
    probable["date"] + " — " + probable["pitcher_name"] + " (" + probable["team"]
    + " vs " + probable["opponent"] + ")"
)
selected_label = st.selectbox("Pick a probable starter", probable["label"])
selected = probable[probable["label"] == selected_label].iloc[0]

has_lift_data = pd.notna(selected["lift_ratio_k"])
lift_ratio_k = float(selected["lift_ratio_k"]) if has_lift_data else 1.0

col1, col2 = st.columns(2)
with col1:
    st.metric("Pitcher", selected["pitcher_name"])
    st.metric("Usual K (median)", f"{selected['median_k']:.1f}" if has_lift_data else "N/A")
with col2:
    st.metric("Team / Opponent", f"{selected['team']} vs {selected['opponent']}")
    st.metric(
        "K Lift",
        f"{lift_ratio_k:.2f}x" if has_lift_data else "1.00x (no data — no adjustment applied)",
        help=f"{int(selected['qualifying_starts'])} historical qualifying starts" if has_lift_data else
             "Fewer than the minimum qualifying starts for this pitcher — lift defaults to 1.0x (neutral).",
    )

if not has_lift_data:
    st.warning(
        "Not enough historical qualifying starts for this pitcher to estimate a K lift. "
        "The edge below will show zero (lift=1.0x means no correlation adjustment) — "
        "treat any number here as unreliable for this specific pitcher."
    )

st.divider()
st.subheader("Enter the odds you're seeing (American odds; leave at 0 if you don't have that side)")

odds_col1, odds_col2, odds_col3 = st.columns(3)
with odds_col1:
    st.markdown("**Moneyline**")
    team_ml_odds = st.number_input(f"{selected['team']} moneyline", value=0, step=5, format="%d")
    opp_ml_odds = st.number_input(f"{selected['opponent']} moneyline (optional, for de-vig)", value=0, step=5, format="%d")
with odds_col2:
    st.markdown("**Strikeout prop**")
    k_line = st.number_input("K line (e.g. 5.5)", value=0.0, step=0.5, format="%.1f")
    over_odds = st.number_input("Over odds", value=0, step=5, format="%d")
    under_odds = st.number_input("Under odds (optional, for de-vig)", value=0, step=5, format="%d")
with odds_col3:
    st.markdown("**Combo (optional)**")
    combo_odds = st.number_input("Book's actual combined/SGP price", value=0, step=5, format="%d")

if team_ml_odds == 0 or over_odds == 0:
    st.info("Enter at least the moneyline and Over odds to see the edge calculation.")
    st.stop()

if opp_ml_odds != 0:
    p_win, _ = edge.devig_pair(team_ml_odds, opp_ml_odds)
    win_note = "de-vigged (both sides entered)"
else:
    p_win = edge.american_to_implied_prob(team_ml_odds)
    win_note = "raw implied probability, includes vig (enter the opponent's line to de-vig)"

if under_odds != 0:
    p_strong_k, _ = edge.devig_pair(over_odds, under_odds)
    k_note = "de-vigged (both sides entered)"
else:
    p_strong_k = edge.american_to_implied_prob(over_odds)
    k_note = "raw implied probability, includes vig (enter the Under odds to de-vig)"

naive_joint = edge.naive_independent_joint(p_win, p_strong_k)
true_joint = edge.true_correlated_joint(p_win, p_strong_k, lift_ratio_k)
edge_pts = true_joint - naive_joint

st.divider()
st.subheader("Result")

r1, r2, r3 = st.columns(3)
r1.metric("Market win prob.", f"{p_win:.1%}", help=win_note)
r2.metric("Market K-over prob.", f"{p_strong_k:.1%}", help=k_note)
r3.metric("K Lift applied", f"{lift_ratio_k:.2f}x")

r4, r5, r6 = st.columns(3)
r4.metric("Naive independent joint", f"{naive_joint:.1%}", help="What separate-contract pricing effectively charges you.")
r5.metric("True correlated joint (model)", f"{true_joint:.1%}", help="p_strong_k × min(p_win × lift, 1.0)")
r6.metric("Edge", f"{edge_pts:+.1%} pts", delta=f"{edge_pts:+.1%}")

if combo_odds != 0:
    combo_implied = edge.american_to_implied_prob(combo_odds)
    st.metric(
        "Book's combo price implies",
        f"{combo_implied:.1%}",
        delta=f"{true_joint - combo_implied:+.1%} vs. model's true joint",
        help="If this is meaningfully below the model's true joint probability, the combo is priced generously relative to the correlation the model measures.",
    )

st.divider()
if st.button("Log this bet"):
    storage = S3Storage()
    entry = pd.DataFrame([{
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "date": selected["date"],
        "game_pk": int(selected["game_pk"]),
        "pitcher_id": int(selected["pitcher_id"]),
        "pitcher_name": selected["pitcher_name"],
        "team": selected["team"],
        "opponent": selected["opponent"],
        "team_ml_odds": team_ml_odds,
        "opp_ml_odds": opp_ml_odds or None,
        "k_line": k_line or None,
        "over_odds": over_odds,
        "under_odds": under_odds or None,
        "combo_odds": combo_odds or None,
        "lift_ratio_k": lift_ratio_k,
        "p_win_market": p_win,
        "p_strong_k_market": p_strong_k,
        "naive_joint": naive_joint,
        "true_joint": true_joint,
        "edge_pts": edge_pts,
        "resolved": False,
        "actual_team_won": None,
        "actual_strikeouts": None,
    }])

    if storage.exists(EDGE_LOG_KEY):
        existing = storage.load(EDGE_LOG_KEY)
        combined = pd.concat([existing, entry], ignore_index=True)
    else:
        combined = entry

    storage.save(combined, EDGE_LOG_KEY)
    st.success(f"Logged to s3://{S3_BUCKET}/{EDGE_LOG_KEY} ({len(combined)} bets logged total).")
