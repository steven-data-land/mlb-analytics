"""Pure functions that turn schedule/boxscore JSON into starter records.

No network calls in here on purpose -- these are easy to unit test against
saved JSON fixtures.
"""


def find_starting_pitcher(team_boxscore: dict) -> dict | None:
    """Return the player entry for the starting pitcher on one team's boxscore.

    `gamesStarted == 1` is the authoritative signal -- not list order in
    teams.<side>.pitchers, and not the schedule's probablePitcher (that's
    just a pre-game guess and doesn't account for late scratches).
    """
    for player in team_boxscore["players"].values():
        pitching = player.get("stats", {}).get("pitching")
        if pitching and pitching.get("gamesStarted") == 1:
            return player
    return None


def build_starter_record(date: str, side: str, schedule_game: dict, boxscore: dict) -> dict | None:
    """Build one record for the starting pitcher on `side` ("home" or "away").

    Returns None if no starter could be identified for that side (e.g. an
    opener/bullpen-game with no traditional starter).
    """
    other_side = "home" if side == "away" else "away"

    team_boxscore = boxscore["teams"][side]
    starter = find_starting_pitcher(team_boxscore)
    if starter is None:
        return None

    pitching = starter["stats"]["pitching"]
    outs = pitching["outs"]
    earned_runs = pitching["earnedRuns"]
    era = (earned_runs * 27 / outs) if outs else 0.0

    schedule_team = schedule_game["teams"][side]
    schedule_opponent = schedule_game["teams"][other_side]

    return {
        "date": date,
        "game_pk": schedule_game["gamePk"],
        "team": schedule_team["team"]["name"],
        "opponent": schedule_opponent["team"]["name"],
        "pitcher_id": starter["person"]["id"],
        "pitcher_name": starter["person"]["fullName"],
        "outs": outs,
        "strikeouts": pitching["strikeOuts"],
        "earned_runs": earned_runs,
        "era": round(era, 2),
        "pitch_count": pitching["numberOfPitches"],
        # isWinner is absent (not just False) for the rare Final game with no
        # decision -- e.g. a rain-shortened tie that wasn't replayed. None
        # here means "no winner recorded", distinct from an actual loss.
        "team_won": schedule_team.get("isWinner"),
    }
