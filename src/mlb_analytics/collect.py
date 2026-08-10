"""Orchestration: date range -> DataFrame of starting-pitcher records."""

import pandas as pd

from . import api_client, parsing


def collect_starters(start_date: str, end_date: str) -> pd.DataFrame:
    """Pull starting-pitcher/team-result records for every finished regular
    season game between start_date and end_date (inclusive, YYYY-MM-DD)."""
    schedule = api_client.get_schedule(start_date, end_date)

    # Keyed by gamePk to dedupe: a suspended-and-later-resumed game can appear
    # under two different date entries in one schedule response (the original
    # date and the completion date), both "Final" once it's done -- without
    # this, its boxscore gets fetched and counted twice.
    games = {}
    for date_entry in schedule.get("dates", []):
        for game in date_entry["games"]:
            if game["gameType"] != "R":
                continue  # skip spring training / postseason / all-star
            if game["status"]["abstractGameState"] != "Final":
                continue  # skip postponed/in-progress/scheduled games
            games[game["gamePk"]] = (date_entry["date"], game)

    records = []
    for date, game in games.values():
        boxscore = api_client.get_boxscore(game["gamePk"])
        for side in ("away", "home"):
            record = parsing.build_starter_record(date, side, game, boxscore)
            if record is not None:
                records.append(record)

    return pd.DataFrame.from_records(records)
