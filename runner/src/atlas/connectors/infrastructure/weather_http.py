"""OpenMeteoWeatherClient: weather as a plain HTTP function (D19 — one
unauthenticated endpoint does not earn a process, a protocol, and a schema).
Open-Meteo requires no API key."""

from __future__ import annotations

import datetime as dt
import logging

import httpx

from atlas.connectors.application.ports import (
    DayForecast,
    Forecast,
    HourlyConditions,
    WeatherReport,
)
from atlas.shared.clock import Clock, SystemClock

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.open-meteo.com/v1/forecast"

_WEATHER_CODES = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "rain showers",
    81: "rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


class OpenMeteoWeatherClient:
    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self._timeout = timeout_seconds

    async def get_forecast(self, latitude: float, longitude: float) -> Forecast:
        params: dict[str, str | int | float] = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 1,
            "timezone": "auto",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()
        code = int(data["current"]["weather_code"])
        return Forecast(
            summary=_WEATHER_CODES.get(code, f"weather code {code}"),
            temperature_c=float(data["current"]["temperature_2m"]),
            high_c=float(data["daily"]["temperature_2m_max"][0]),
            low_c=float(data["daily"]["temperature_2m_min"][0]),
            precipitation_chance_pct=int(data["daily"]["precipitation_probability_max"][0] or 0),
        )


BOARD_TTL_SECONDS = 900.0
"""Fallback refetch cadence, used until the API tells us its own.

Open-Meteo reports `current.interval` — the width of the bucket it is serving,
900s at the time of writing. Polling faster than that returns a byte-identical
payload, so the client adopts whatever the API declares and re-tunes itself if
that ever changes. See `_ttl_from`."""

MIN_TTL_SECONDS = 60.0
MAX_TTL_SECONDS = 3600.0

FORECAST_DAYS = 2


class OpenMeteoForecastClient:
    """Today and tomorrow for the board: temperature, UV and precipitation,
    including the hourly precipitation profile so a wet day can be read as a
    shape rather than a single percentage.

    One keyless endpoint, so D19 keeps it as a plain HTTP call rather than an
    MCP server. The US NWS API is the more literally governmental source but
    publishes no UV index, which is half of what this card is for; Open-Meteo
    blends the national services (NOAA among them) and carries UV directly.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        ttl_seconds: float = BOARD_TTL_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._ttl = ttl_seconds
        self._clock = clock or SystemClock()
        self._cached: WeatherReport | None = None
        self._fetched_at: dt.datetime | None = None
        self._effective_ttl = ttl_seconds

    def _is_fresh(self, now: dt.datetime) -> bool:
        if self._cached is None or self._fetched_at is None:
            return False
        return (now - self._fetched_at).total_seconds() < self._effective_ttl

    def _ttl_from(self, payload: dict[str, object]) -> float:
        """Adopt the API's declared update interval, clamped to something
        sane in case the field is ever absent or absurd."""
        current = payload.get("current")
        interval = current.get("interval") if isinstance(current, dict) else None
        if not isinstance(interval, int | float) or interval <= 0:
            return self._ttl
        return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, float(interval)))

    async def get_report(self, latitude: float, longitude: float) -> WeatherReport:
        now = self._clock.now()
        if self._is_fresh(now) and self._cached is not None:
            return self._cached

        params: dict[str, str | int | float] = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code",
            "hourly": "precipitation_probability,precipitation,uv_index",
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "uv_index_max,precipitation_probability_max,precipitation_sum"
            ),
            "forecast_days": FORECAST_DAYS,
            "timezone": "auto",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(_BASE_URL, params=params)
                response.raise_for_status()
                payload = response.json()
                report = parse_forecast(payload, now)
                self._effective_ttl = self._ttl_from(payload)
        except Exception as exc:
            logger.warning("weather fetch failed: %s", exc)
            if self._cached is not None:
                # Keep yesterday's answer rather than blanking the card, but
                # let the caller see that the refresh failed.
                return self._cached.model_copy(update={"error": f"{type(exc).__name__}: {exc}"})
            return WeatherReport(error=f"{type(exc).__name__}: {exc}")

        self._cached = report
        self._fetched_at = now
        return report


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _hourly_for(data: dict[str, object], day: str) -> tuple[HourlyConditions, ...]:
    hourly = data.get("hourly")
    if not isinstance(hourly, dict):
        return ()
    times = hourly.get("time") or []
    probabilities = hourly.get("precipitation_probability") or []
    amounts = hourly.get("precipitation") or []
    uv_values = hourly.get("uv_index") or []
    if not isinstance(times, list):
        return ()

    rows: list[HourlyConditions] = []
    for index, stamp in enumerate(times):
        if not isinstance(stamp, str) or not stamp.startswith(day):
            continue
        try:
            when = dt.datetime.fromisoformat(stamp)
        except ValueError:
            continue
        probability = probabilities[index] if index < len(probabilities) else 0
        amount = amounts[index] if index < len(amounts) else 0.0
        uv = uv_values[index] if index < len(uv_values) else 0.0
        rows.append(
            HourlyConditions(
                time=when,
                probability_pct=_as_int(probability) or 0,
                millimetres=_as_float(amount) or 0.0,
                uv_index=_as_float(uv) or 0.0,
            )
        )
    return tuple(rows)


def parse_forecast(data: dict[str, object], now: dt.datetime) -> WeatherReport:
    """Pure: the tests drive this with a captured payload, no network."""
    daily = data.get("daily")
    if not isinstance(daily, dict):
        return WeatherReport(error="response carried no daily block", fetched_at=now)

    dates = daily.get("time") or []
    days: list[DayForecast] = []
    for index, day in enumerate(dates):
        if not isinstance(day, str):
            continue

        def pick(key: str, position: int = index) -> object:
            values = daily.get(key) or []
            return values[position] if isinstance(values, list) and position < len(values) else None

        code = _as_int(pick("weather_code"))
        high = _as_float(pick("temperature_2m_max"))
        low = _as_float(pick("temperature_2m_min"))
        if high is None or low is None:
            continue
        days.append(
            DayForecast(
                date=day,
                summary=_WEATHER_CODES.get(code, f"code {code}") if code is not None else "unknown",
                high_c=high,
                low_c=low,
                uv_index_max=_as_float(pick("uv_index_max")),
                precipitation_probability_pct=_as_int(pick("precipitation_probability_max")) or 0,
                precipitation_mm=_as_float(pick("precipitation_sum")) or 0.0,
                hourly=_hourly_for(data, day),
            )
        )

    current = data.get("current")
    current_c: float | None = None
    current_summary: str | None = None
    if isinstance(current, dict):
        current_c = _as_float(current.get("temperature_2m"))
        code_value = _as_int(current.get("weather_code"))
        if code_value is not None:
            current_summary = _WEATHER_CODES.get(code_value, f"code {code_value}")

    return WeatherReport(
        days=tuple(days),
        current_c=current_c,
        current_summary=current_summary,
        fetched_at=now,
    )
