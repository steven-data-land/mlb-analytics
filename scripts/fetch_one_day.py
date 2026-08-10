"""
Learning milestone, now using the real api_client/parsing modules instead
of inline requests calls. Same date, same output as the first version --
this just proves the extraction didn't change behavior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mlb_analytics import api_client, parsing

# A known past date so the game is guaranteed to be "Final" (not scheduled/live).
DATE = "2024-07-01"


def main():
    schedule = api_client.get_schedule(DATE)
    games = schedule["dates"][0]["games"]
    print(f"Found {len(games)} games on {DATE}\n")

    game = next(g for g in games if g["status"]["abstractGameState"] == "Final")
    game_pk = game["gamePk"]
    print(f"Using gamePk={game_pk}: "
          f"{game['teams']['away']['team']['name']} @ {game['teams']['home']['team']['name']}\n")

    for side in ("away", "home"):
        team = game["teams"][side]["team"]["name"]
        won = game["teams"][side]["isWinner"]
        print(f"  {team}: {'WON' if won else 'lost'}")

    boxscore = api_client.get_boxscore(game_pk)

    print("\nStarting pitchers:")
    for side in ("away", "home"):
        record = parsing.build_starter_record(DATE, side, game, boxscore)
        if record is None:
            print(f"  {side}: no starter found (unexpected)")
            continue

        print(f"  {side} starter: {record['pitcher_name']}")
        print(f"    strikeouts={record['strikeouts']}  earned_runs={record['earned_runs']}  "
              f"outs={record['outs']}  game_era={record['era']:.2f}  pitch_count={record['pitch_count']}")


if __name__ == "__main__":
    main()
