"""Pre-game data: probable starters for games that haven't been played yet.

Boxscore-based starter identification (gamesStarted==1, used in parsing.py)
only exists once a game is final. For today/tomorrow, the schedule's
probablePitcher hydration is the only source available -- it's a pre-game
projection and can change on late scratches, unlike the authoritative
post-game boxscore data.
"""

import pandas as pd

from . import api_client


def get_probable_starters(start_date: str, end_date: str) -> pd.DataFrame:
    """One row per (team, probable starter) for regular-season games in range."""
    schedule = api_client.get_schedule(start_date, end_date, hydrate="probablePitcher")

    rows = []
    for date_entry in schedule.get("dates", []):
        for game in date_entry["games"]:
            if game["gameType"] != "R":
                continue
            if game["status"]["abstractGameState"] != "Preview":
                continue  # already started (Live) or finished (Final) -- not "upcoming"

            for side in ("away", "home"):
                other_side = "home" if side == "away" else "away"
                team = game["teams"][side]
                probable_pitcher = team.get("probablePitcher")
                if probable_pitcher is None:
                    continue

                rows.append({
                    "date": date_entry["date"],
                    "game_pk": game["gamePk"],
                    "team": team["team"]["name"],
                    "opponent": game["teams"][other_side]["team"]["name"],
                    "pitcher_id": probable_pitcher["id"],
                    "pitcher_name": probable_pitcher["fullName"],
                })

    return pd.DataFrame(rows)
