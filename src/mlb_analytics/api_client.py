"""Thin wrappers over the MLB Stats API (https://statsapi.mlb.com/api/v1).

There's no official rate limit on this API, but it sits behind a CDN with
only a 20s cache, so a small delay between requests plus basic retries is
the community-norm courtesy (used by wrappers like MLB-StatsAPI/pybaseball).
"""

import time

import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"
# Open-Meteo: free, no API key, no published rate limit for non-commercial
# use (https://open-meteo.com/en/pricing) -- used for the venue weather
# forecast in weather.py.
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.0
DELAY_BETWEEN_REQUESTS_SECONDS = 0.3

_session = requests.Session()


def _get_absolute(url: str, params: dict | None = None) -> dict:
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as error:
            last_error = error
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        finally:
            time.sleep(DELAY_BETWEEN_REQUESTS_SECONDS)

    raise last_error


def _get(path: str, params: dict | None = None) -> dict:
    return _get_absolute(f"{BASE_URL}{path}", params=params)


def get_schedule(
    start_date: str, end_date: str | None = None, sport_id: int = 1, hydrate: str | None = None
) -> dict:
    """Fetch schedule data for a date or date range (YYYY-MM-DD).

    `hydrate` is a comma-separated list of extra data to embed per game, e.g.
    "probablePitcher" -- useful for games that haven't been played yet, where
    boxscore-based starter identification (gamesStarted==1) isn't available.
    """
    params = {"sportId": sport_id}
    if end_date is None:
        params["date"] = start_date
    else:
        params["startDate"] = start_date
        params["endDate"] = end_date
    if hydrate:
        params["hydrate"] = hydrate
    return _get("/schedule", params=params)


def get_boxscore(game_pk: int) -> dict:
    """Fetch the full boxscore (team + player stats) for a single game."""
    return _get(f"/game/{game_pk}/boxscore")


def get_venue(venue_id: int) -> dict:
    """Fetch venue details, hydrated with location (incl. lat/lon) so callers
    can look up a stadium's coordinates for a weather forecast."""
    return _get(f"/venues/{venue_id}", params={"hydrate": "location"})


def get_weather_forecast(latitude: float, longitude: float, forecast_days: int = 3) -> dict:
    """Free, keyless hourly forecast (Open-Meteo) for one point. `timezone:
    auto` makes the response resolve and return the point's own IANA zone
    name, so callers can line up game times (in UTC) against the hourly
    forecast (in local time) without a separate timezone lookup."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "precipitation_probability,precipitation,weathercode,windspeed_10m",
        "timezone": "auto",
        "forecast_days": forecast_days,
    }
    return _get_absolute(OPEN_METEO_URL, params=params)
