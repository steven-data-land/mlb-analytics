"""Rain/delay-risk forecast for upcoming games, keyed off each game's venue.

Two free, keyless data sources:
- MLB Stats API `/venues/{id}` (same API everything else here uses) for a
  venue's lat/lon.
- Open-Meteo (https://open-meteo.com) for the actual forecast -- free for
  non-commercial use, no API key or account required.

This is a rough heuristic, not an official delay prediction: it looks at
peak precipitation chance and total expected rainfall in the ~4-hour window
around first pitch at the venue's coordinates.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import api_client

# Fixed- or retractable-roof venues: rain essentially can't delay a game here,
# since the roof closes at the first sign of it. Both roof types are folded
# into the same "indoor" treatment rather than trying to model whether a
# retractable roof happens to be open.
DOMED_VENUE_IDS = {
    12: "fixed dome",          # Tropicana Field -- Tampa Bay Rays
    14: "retractable roof",    # Rogers Centre -- Toronto Blue Jays
    15: "retractable roof",    # Chase Field -- Arizona Diamondbacks
    32: "retractable roof",    # American Family Field -- Milwaukee Brewers
    680: "retractable roof",   # T-Mobile Park -- Seattle Mariners
    2392: "retractable roof",  # Daikin Park -- Houston Astros
    4169: "retractable roof",  # loanDepot park -- Miami Marlins
    5325: "retractable roof",  # Globe Life Field -- Texas Rangers
}

GAME_WINDOW_HOURS = 4  # rough first-pitch-to-final-out span to sample

# WMO weather codes (Open-Meteo's `weathercode`), collapsed to the buckets
# worth surfacing in a tooltip.
_WEATHER_CODE_LABELS = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ hail",
}


def weather_code_label(code: int | float | None) -> str:
    if code is None:
        return "Unknown"
    return _WEATHER_CODE_LABELS.get(int(code), "Mixed conditions")


def get_venue_coordinates(venue_id: int) -> tuple[float, float] | None:
    """(latitude, longitude) for a venue, or None if the API has no location
    on file for it."""
    venues = api_client.get_venue(venue_id).get("venues", [])
    if not venues:
        return None
    coords = venues[0].get("location", {}).get("defaultCoordinates")
    if coords is None:
        return None
    return coords["latitude"], coords["longitude"]


def summarize_game_window(forecast: dict, game_time_utc: str) -> dict | None:
    """Reduce an Open-Meteo hourly forecast down to the window around first
    pitch: peak precipitation probability, total precipitation, peak wind,
    and the worst weather code in that window. Returns None if the game
    falls outside the forecast's date range.

    `timezone=auto` (see api_client.get_weather_forecast) makes Open-Meteo
    return both an IANA zone name and hourly timestamps already in that
    zone, so the game's UTC time only needs converting once to line the two
    up -- no separate venue-timezone lookup required.
    """
    tz_name = forecast.get("timezone")
    hourly = forecast.get("hourly", {})
    times = hourly.get("time", [])
    if not tz_name or not times:
        return None

    game_utc = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
    game_local_hour = game_utc.astimezone(ZoneInfo(tz_name)).replace(
        minute=0, second=0, microsecond=0
    )
    game_local_key = game_local_hour.strftime("%Y-%m-%dT%H:%M")

    if game_local_key not in times:
        return None
    start = times.index(game_local_key)
    end = min(start + GAME_WINDOW_HOURS, len(times))

    precip_prob = hourly["precipitation_probability"][start:end]
    precip_mm = hourly["precipitation"][start:end]
    wind_kmh = hourly["windspeed_10m"][start:end]
    codes = hourly["weathercode"][start:end]

    worst_code = max(codes, key=lambda c: (c >= 95, c >= 61, c >= 51, c), default=None)

    return {
        "game_time_local": game_local_hour.strftime("%-I:%M %p %Z"),
        "max_precip_probability_pct": max(precip_prob, default=0),
        "total_precip_mm": round(sum(precip_mm), 1),
        "max_wind_kmh": round(max(wind_kmh, default=0), 0),
        "weather_label": weather_code_label(worst_code),
    }


def classify_risk(max_precip_probability_pct: float, total_precip_mm: float) -> str:
    """Low/Medium/High rain-delay risk from the game-window forecast."""
    if max_precip_probability_pct >= 60 or total_precip_mm >= 4:
        return "High"
    if max_precip_probability_pct >= 30 or total_precip_mm >= 1:
        return "Medium"
    return "Low"


def get_games_weather(start_date: str, end_date: str) -> pd.DataFrame:
    """One row per regular-season game in range, with a rain/delay-risk
    forecast for its venue. Domed/retractable-roof venues are marked
    "Indoor" rather than scored, since weather doesn't apply to them."""
    schedule = api_client.get_schedule(start_date, end_date, hydrate="venue")

    coord_cache: dict[int, tuple[float, float] | None] = {}
    forecast_cache: dict[int, dict] = {}
    rows = []

    for date_entry in schedule.get("dates", []):
        for game in date_entry["games"]:
            if game["gameType"] != "R":
                continue

            venue = game["venue"]
            venue_id = venue["id"]
            row = {
                "date": date_entry["date"],
                "game_pk": game["gamePk"],
                "game_time_utc": game["gameDate"],
                "away": game["teams"]["away"]["team"]["name"],
                "home": game["teams"]["home"]["team"]["name"],
                "venue_name": venue["name"],
            }

            roof = DOMED_VENUE_IDS.get(venue_id)
            if roof is not None:
                rows.append({
                    **row,
                    "game_time_local": None,
                    "precip_probability_pct": 0,
                    "precip_mm": 0.0,
                    "wind_kmh": None,
                    "risk": "Indoor",
                    "risk_note": f"{roof.capitalize()} -- rain doesn't apply",
                })
                continue

            if venue_id not in coord_cache:
                coord_cache[venue_id] = get_venue_coordinates(venue_id)
            coords = coord_cache[venue_id]

            if coords is None:
                rows.append({
                    **row,
                    "game_time_local": None,
                    "precip_probability_pct": None,
                    "precip_mm": None,
                    "wind_kmh": None,
                    "risk": "Unknown",
                    "risk_note": "No venue location on file",
                })
                continue

            if venue_id not in forecast_cache:
                forecast_cache[venue_id] = api_client.get_weather_forecast(*coords)
            summary = summarize_game_window(forecast_cache[venue_id], game["gameDate"])

            if summary is None:
                rows.append({
                    **row,
                    "game_time_local": None,
                    "precip_probability_pct": None,
                    "precip_mm": None,
                    "wind_kmh": None,
                    "risk": "Unknown",
                    "risk_note": "Game is outside the 3-day forecast window",
                })
                continue

            risk = classify_risk(summary["max_precip_probability_pct"], summary["total_precip_mm"])
            rows.append({
                **row,
                "game_time_local": summary["game_time_local"],
                "precip_probability_pct": summary["max_precip_probability_pct"],
                "precip_mm": summary["total_precip_mm"],
                "wind_kmh": summary["max_wind_kmh"],
                "risk": risk,
                "risk_note": summary["weather_label"],
            })

    return pd.DataFrame(rows)
