"""Forecast parsing, against a payload captured from Open-Meteo.

Pure function, no network: the board's weather must not depend on the suite
being able to reach the internet, and the parsing is where the bugs live.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.connectors.infrastructure.weather_http import (
    BOARD_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    OpenMeteoForecastClient,
    parse_forecast,
)

NOW = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)

PAYLOAD: dict[str, object] = {
    "current": {"time": "2026-09-01T16:15", "temperature_2m": 29.0, "weather_code": 3},
    "hourly": {
        "time": [f"2026-09-01T{hour:02d}:00" for hour in range(24)]
        + [f"2026-09-02T{hour:02d}:00" for hour in range(24)],
        "precipitation_probability": [10] * 24 + [70] * 24,
        "precipitation": [0.0] * 12 + [1.5] * 12 + [0.0] * 24,
        "uv_index": [0.0] * 48,
    },
    "daily": {
        "time": ["2026-09-01", "2026-09-02"],
        "weather_code": [63, 0],
        "temperature_2m_max": [31.0, 28.8],
        "temperature_2m_min": [23.1, 23.8],
        "uv_index_max": [6.9, 6.05],
        "precipitation_probability_max": [40, 5],
        "precipitation_sum": [4.3, 0.0],
    },
}


def test_parses_two_days_with_uv_and_precipitation() -> None:
    report = parse_forecast(PAYLOAD, NOW)

    assert len(report.days) == 2
    today, tomorrow = report.days
    assert today.summary == "rain"
    assert today.high_c == 31.0
    assert today.uv_index_max == pytest.approx(6.9)
    assert today.precipitation_probability_pct == 40
    assert today.precipitation_mm == pytest.approx(4.3)
    assert tomorrow.summary == "clear"


def test_current_conditions_are_read() -> None:
    report = parse_forecast(PAYLOAD, NOW)

    assert report.current_c == 29.0
    assert report.current_summary == "overcast"
    assert report.fetched_at == NOW
    assert report.error is None


def test_hourly_rows_are_bucketed_to_their_own_day() -> None:
    report = parse_forecast(PAYLOAD, NOW)

    today, tomorrow = report.days
    assert len(today.hourly) == 24
    assert len(tomorrow.hourly) == 24
    assert {row.time.day for row in today.hourly} == {1}
    assert {row.time.day for row in tomorrow.hourly} == {2}
    assert today.hourly[13].millimetres == pytest.approx(1.5)


def test_wet_day_detection_drives_the_chart() -> None:
    """A dry day must not spend card space on a flat chart."""
    report = parse_forecast(PAYLOAD, NOW)

    today, tomorrow = report.days
    assert today.is_wet is True, "40% and 4.3mm"
    assert tomorrow.is_wet is False, "5% and nothing forecast"


def test_a_response_without_daily_data_is_an_error_not_a_crash() -> None:
    report = parse_forecast({"current": {}}, NOW)

    assert report.days == ()
    assert report.error is not None


def test_missing_fields_are_skipped_rather_than_faked() -> None:
    payload: dict[str, object] = {
        "daily": {
            "time": ["2026-09-01"],
            "weather_code": [63],
            "temperature_2m_max": [None],
            "temperature_2m_min": [23.1],
        }
    }

    report = parse_forecast(payload, NOW)

    assert report.days == (), "a day without a high is dropped, not defaulted to zero"


# --- self-tuning refetch cadence -------------------------------------------


def test_client_adopts_the_interval_the_api_declares() -> None:
    """Open-Meteo reports the width of the bucket it is serving. Polling
    faster returns a byte-identical payload, so the client takes its cadence
    from the API rather than from a number someone guessed."""
    client = OpenMeteoForecastClient()

    assert client._ttl_from({"current": {"interval": 900}}) == 900.0
    assert client._ttl_from({"current": {"interval": 300}}) == 300.0


def test_an_absurd_or_missing_interval_falls_back() -> None:
    client = OpenMeteoForecastClient()

    assert client._ttl_from({}) == BOARD_TTL_SECONDS
    assert client._ttl_from({"current": {}}) == BOARD_TTL_SECONDS
    assert client._ttl_from({"current": {"interval": 0}}) == BOARD_TTL_SECONDS
    assert client._ttl_from({"current": {"interval": 5}}) == MIN_TTL_SECONDS
    assert client._ttl_from({"current": {"interval": 999999}}) == MAX_TTL_SECONDS
