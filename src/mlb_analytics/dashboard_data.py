"""Shared, cached data-loading helpers for the Streamlit pages (app.py and
pages/*.py) -- defined once so every page hits the same cache entries
instead of separately loading/recomputing the same S3 data.
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from . import live, model, weather
from .config import SEASON_DATE_RANGES, season_key
from .storage import S3Storage


@st.cache_data(ttl=3600)
def load_history() -> pd.DataFrame:
    storage = S3Storage()
    frames = []
    for year in sorted(SEASON_DATE_RANGES):
        key = season_key(year)
        if storage.exists(key):
            df = storage.load(key)
            df["season"] = year
            frames.append(df)
    return pd.concat(frames, ignore_index=True)


@st.cache_data(ttl=3600)
def build_lift_tables(history: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Expensive part (season ERA + both metric passes over full history) is
    cached; blending by weight_k happens fresh per-rerun since that depends
    on a live UI slider."""
    season_era = model.compute_season_era(history)
    starts_with_opp_era = model.add_opponent_era(history, season_era)
    k_table = model.conditional_win_probabilities(starts_with_opp_era, metric_col="strikeouts")
    outs_table = model.conditional_win_probabilities(starts_with_opp_era, metric_col="outs")
    return season_era, k_table, outs_table


@st.cache_data(ttl=300)
def load_probable_starters() -> pd.DataFrame:
    today = date.today()
    tomorrow = today + timedelta(days=1)
    return live.get_probable_starters(today.isoformat(), tomorrow.isoformat())


@st.cache_data(ttl=1800)
def load_tomorrow_weather() -> pd.DataFrame:
    """Rain/delay-risk forecast for every one of tomorrow's games (not just
    the ones that qualify for the ranking model above). 30-minute TTL --
    forecasts don't move fast enough to need the 5-minute probable-starters
    refresh, and it keeps this free API call infrequent."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    return weather.get_games_weather(tomorrow, tomorrow)
