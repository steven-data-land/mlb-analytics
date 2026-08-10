"""Streamlit dashboard: ranks today's/tomorrow's probable starters by the
conditional win-probability model in src/mlb_analytics/model.py.

Run with:
    streamlit run app.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import altair as alt
import matplotlib.colors as mcolors
import pandas as pd
import streamlit as st

from mlb_analytics import dashboard_data, model

SEQUENTIAL_BLUE = "#256abf"
GOOD_GREEN = "#0ca30c"
RISK_COLORS = {
    "Indoor": "#898781",
    "Unknown": "#898781",
    "Low": "#0ca30c",
    "Medium": "#fab219",
    "High": "#d03b3b",
}
RISK_ORDER = ["Indoor", "Unknown", "Low", "Medium", "High"]
# One hue, light -> dark (never darker than "good" green itself) -- "more
# green is better" without pairing it against red, which ~8% of men can't
# reliably distinguish from green.
_GREEN_RAMP = mcolors.LinearSegmentedColormap.from_list("good_green", ["#ffffff", GOOD_GREEN])
_GREEN_RAMP_REVERSED = _GREEN_RAMP.reversed()


def style_ranked_table(table: pd.DataFrame) -> "pd.io.formats.style.Styler":
    """Per-column heatmap: green gets darker toward the 'good' end of each
    column. ERA is better low, everything else here is better high."""
    green_higher_better = [
        "Usual K", "Usual Outs", "Qualifying Starts", "Baseline Win%",
        "P(Win | Strong K)", "K Lift", "P(Win | Deep Outing)", "Outs Lift",
        "Recommendation Score",
    ]
    styled = table.style.background_gradient(cmap=_GREEN_RAMP_REVERSED, subset=["Pitcher ERA"]) \
        .background_gradient(cmap=_GREEN_RAMP, subset=["Opp. Starter ERA"])
    for col in green_higher_better:
        styled = styled.background_gradient(cmap=_GREEN_RAMP, subset=[col])
    return styled.format({
        "Pitcher ERA": "{:.2f}",
        "Opp. Starter ERA": "{:.2f}",
        "Usual K": "{:.1f}",
        "Usual Outs": "{:.1f}",
        "Baseline Win%": "{:.1%}",
        "P(Win | Strong K)": "{:.1%}",
        "K Lift": "{:.2f}x",
        "P(Win | Deep Outing)": "{:.1%}",
        "Outs Lift": "{:.2f}x",
        "Recommendation Score": "{:.2f}x",
    })

st.set_page_config(page_title="MLB Starter Win-Influence", layout="wide")

st.title("Who's Carrying Their Team Today?")
st.caption(
    "Ranks today's and tomorrow's probable starters by a blend of two historical signals — "
    "hitting their usual strikeouts, and going as deep as usual — restricted to games where "
    "the opposing probable starter has a worse season ERA."
)

history = dashboard_data.load_history()
if history.empty:
    st.warning("No historical data found in S3 yet. Run scripts/bulk_upload_history.py first.")
    st.stop()

season_era, k_table, outs_table = dashboard_data.build_lift_tables(history)
probable = dashboard_data.load_probable_starters()

if probable.empty:
    st.info("No probable starters found for today/tomorrow (off day, or MLB hasn't posted them yet).")
    st.stop()

current_year = date.today().year
current_era = season_era.loc[
    (season_era["season"] == current_year) & (season_era["season_starts"] >= model.MIN_SEASON_STARTS_FOR_ERA),
    ["pitcher_id", "season_era"],
]
probable = probable.merge(current_era, on="pitcher_id", how="left")

opponent_info = probable[["game_pk", "team", "pitcher_id", "season_era"]].rename(
    columns={"team": "opponent", "pitcher_id": "opponent_pitcher_id", "season_era": "opponent_season_era"}
)
probable = probable.merge(opponent_info, on=["game_pk", "opponent"], how="left")

weight_k_pct = st.slider(
    "Weight: strikeouts vs. going deep (outs)", min_value=0, max_value=100, value=50, format="%d%% K",
    help="Recommendation Score = weight_k × K-lift + (1 - weight_k) × outs-lift. "
         "0% = rank purely by how deep they usually go; 100% = rank purely by strikeouts.",
)
weight_k = weight_k_pct / 100
win_probs = model.compute_recommendation_score(k_table, outs_table, weight_k=weight_k)

matchup_qualifies = probable["season_era"] < probable["opponent_season_era"]
# win_probs has its own pitcher_name (from historical data); drop it before
# merging so probable's pitcher_name survives instead of being suffixed.
ranked = probable[matchup_qualifies].merge(
    win_probs.drop(columns=["pitcher_name"]), on="pitcher_id", how="inner"
)
ranked = ranked.sort_values("recommendation_score", ascending=False)

unranked = probable[~probable["pitcher_id"].isin(ranked["pitcher_id"])]

display_cols = {
    "date": "Date",
    "pitcher_name": "Pitcher",
    "team": "Team",
    "opponent": "Opponent",
    "season_era": "Pitcher ERA",
    "opponent_season_era": "Opp. Starter ERA",
    "median_k": "Usual K",
    "median_outs": "Usual Outs",
    "qualifying_starts": "Qualifying Starts",
    "baseline_win_prob": "Baseline Win%",
    "conditional_win_prob_k": "P(Win | Strong K)",
    "lift_ratio_k": "K Lift",
    "conditional_win_prob_outs": "P(Win | Deep Outing)",
    "lift_ratio_outs": "Outs Lift",
    "recommendation_score": "Recommendation Score",
}

if ranked.empty:
    st.info(
        "No probable starters currently qualify — either no matchup has our pitcher "
        "with a better season ERA than the opposing starter, or none have enough "
        "qualifying historical starts on both strikeouts and outs (min 5 each) yet."
    )
else:
    st.subheader("Ranked matchups")
    st.caption(
        "Greener = better on each column: lower Pitcher ERA, higher everything else. "
        "\"Baseline Win%\" is the team's win rate in these qualifying starts regardless of "
        "performance (the pure opponent-quality effect). \"K Lift\"/\"Outs Lift\" are how much "
        "a strong-K start or a deep outing respectively multiply win probability on top of "
        "that baseline. \"Recommendation Score\" blends the two per the slider above — sort "
        "order for both the table and chart."
    )
    table = ranked[list(display_cols)].rename(columns=display_cols)
    st.dataframe(style_ranked_table(table), use_container_width=True, hide_index=True)

    top_n = ranked.head(15).copy()
    top_n["label"] = top_n["pitcher_name"] + " (" + top_n["team"] + ")"
    chart = (
        alt.Chart(top_n)
        .mark_bar(color=SEQUENTIAL_BLUE, cornerRadiusEnd=4, size=14)
        .encode(
            x=alt.X("recommendation_score:Q", title="Recommendation score (blended lift)"),
            y=alt.Y("label:N", sort="-x", title=None),
            tooltip=[
                alt.Tooltip("pitcher_name:N", title="Pitcher"),
                alt.Tooltip("team:N", title="Team"),
                alt.Tooltip("opponent:N", title="Opponent"),
                alt.Tooltip("recommendation_score:Q", title="Recommendation score", format=".2f"),
                alt.Tooltip("lift_ratio_k:Q", title="K lift", format=".2f"),
                alt.Tooltip("lift_ratio_outs:Q", title="Outs lift", format=".2f"),
                alt.Tooltip("qualifying_starts:Q", title="Qualifying starts"),
            ],
        )
        .properties(height=28 * len(top_n))
        .configure_axis(grid=False)
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, use_container_width=True)

with st.expander(f"Probable starters not shown ({len(unranked)}) — why they're excluded"):
    st.caption(
        "Excluded because: the opposing probable starter has an equal/better season ERA, "
        f"one side has fewer than {model.MIN_SEASON_STARTS_FOR_ERA} starts this season to "
        f"compute a trustworthy current ERA from, or fewer than {model.MIN_QUALIFYING_STARTS} "
        "historical qualifying starts exist on strikeouts AND/OR outs for the recommendation score."
    )
    st.dataframe(
        unranked[["date", "pitcher_name", "team", "opponent"]].rename(columns=display_cols),
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Tomorrow's rain/delay risk by venue")
st.caption(
    "Every game scheduled tomorrow (not just the ranked matchups above), with a rough "
    "rain/delay-risk read for the ~4-hour window around first pitch at that venue's "
    "coordinates. Weather via [Open-Meteo](https://open-meteo.com) (free, no API key); "
    "venue coordinates via the same MLB Stats API used everywhere else on this page. "
    "Domed/retractable-roof venues are marked **Indoor** since rain essentially can't "
    "delay a game there. This is a heuristic (peak precipitation chance + total expected "
    "rainfall), not an official forecast or delay prediction."
)

weather_df = dashboard_data.load_tomorrow_weather()

if weather_df.empty:
    st.info("No games scheduled tomorrow.")
else:
    plot_df = weather_df.copy()
    plot_df["matchup"] = plot_df["away"] + " @ " + plot_df["home"]
    plot_df["risk"] = pd.Categorical(plot_df["risk"], categories=RISK_ORDER, ordered=True)
    # Indoor/Unknown rows have no real precipitation number (0 or NaN) --
    # a real 0%-wide bar would render invisible, hiding those games from the
    # "show all the games" view. Give them a small fixed stub so every game
    # still shows a visible, correctly-colored mark; the tooltip carries the
    # real (non-)number.
    plot_df["plot_pct"] = plot_df["precip_probability_pct"].fillna(0)
    plot_df.loc[plot_df["risk"].isin(["Indoor", "Unknown"]), "plot_pct"] = 3
    plot_df = plot_df.sort_values(["risk", "plot_pct"], ascending=[False, False])

    chart = (
        alt.Chart(plot_df)
        .mark_bar(cornerRadiusEnd=4, size=14)
        .encode(
            x=alt.X("plot_pct:Q", title="Peak precipitation chance in game window (%)", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("matchup:N", sort=None, title=None),
            color=alt.Color(
                "risk:N",
                title="Delay risk",
                scale=alt.Scale(domain=RISK_ORDER, range=[RISK_COLORS[r] for r in RISK_ORDER]),
            ),
            tooltip=[
                alt.Tooltip("matchup:N", title="Matchup"),
                alt.Tooltip("venue_name:N", title="Venue"),
                alt.Tooltip("game_time_local:N", title="First pitch (local)"),
                alt.Tooltip("risk:N", title="Delay risk"),
                alt.Tooltip("precip_probability_pct:Q", title="Peak precip. chance", format=".0f"),
                alt.Tooltip("precip_mm:Q", title="Total precip. (mm)", format=".1f"),
                alt.Tooltip("wind_kmh:Q", title="Peak wind (km/h)", format=".0f"),
                alt.Tooltip("risk_note:N", title="Conditions"),
            ],
        )
        .properties(height=28 * len(plot_df))
        .configure_axis(grid=False)
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, use_container_width=True)

    with st.expander("View as table"):
        weather_display_cols = {
            "date": "Date", "matchup": "Matchup", "venue_name": "Venue",
            "game_time_local": "First Pitch (Local)", "risk": "Delay Risk",
            "precip_probability_pct": "Peak Precip. Chance (%)",
            "precip_mm": "Total Precip. (mm)", "wind_kmh": "Peak Wind (km/h)",
            "risk_note": "Conditions",
        }
        table_df = weather_df.copy()
        table_df["matchup"] = table_df["away"] + " @ " + table_df["home"]
        table_df["game_time_local"] = table_df["game_time_local"].fillna("—")
        st.dataframe(
            table_df[list(weather_display_cols)].rename(columns=weather_display_cols),
            use_container_width=True,
            hide_index=True,
        )

st.caption(
    "Model: empirical, not a regression — each lift is the historical fraction of qualifying "
    "starts (opponent had worse season ERA) where the metric (strikeouts, or outs recorded) "
    "was at or above the pitcher's own median, and the team won, divided by the baseline win "
    "rate for that same qualifying subset. Season ERA uses full-season hindsight for past "
    "seasons; ties (rare) are excluded from the win/loss calculation."
)
