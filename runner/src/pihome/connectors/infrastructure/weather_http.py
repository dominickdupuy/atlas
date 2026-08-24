"""OpenMeteoWeatherClient: weather as a plain HTTP function (D19 — one
unauthenticated endpoint does not earn a process, a protocol, and a schema).
Open-Meteo requires no API key."""

from __future__ import annotations

import httpx

from pihome.connectors.application.ports import Forecast

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
