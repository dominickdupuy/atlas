"""Published-ICS parsing, with the network injected.

The trap this covers: Exchange writes Windows timezone names. `Eastern
Standard Time` is not an IANA zone and is NOT a fixed -05:00 — the feed
carries VTIMEZONE rules, and a September lecture must land on EDT (-04:00).
Getting this wrong shifts every class by an hour, which is worse than showing
no calendar at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from atlas.connectors.infrastructure.ics_calendar import IcsCalendarClient
from atlas.shared.clock import Clock

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "calendar" / "timetable.ics"
EASTERN = ZoneInfo("America/New_York")


class FrozenClock(Clock):
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


def _client(fetches: list[int], *, clock: FrozenClock, fail: bool = False) -> IcsCalendarClient:
    async def fetch() -> bytes:
        fetches.append(1)
        if fail:
            raise ConnectionError("outlook unreachable")
        return FIXTURE.read_bytes()

    return IcsCalendarClient("https://example.invalid/cal.ics", clock=clock, fetch=fetch)


def _window() -> tuple[datetime, datetime]:
    start = datetime(2026, 9, 1, 0, 0, tzinfo=EASTERN)
    return start, start + timedelta(days=3)


async def test_windows_timezone_resolves_to_daylight_time() -> None:
    clock = FrozenClock(datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    client = _client([], clock=clock)
    start, end = _window()

    feed = await client.get_events(start, end)
    lecture = next(e for e in feed.events if e.uid == "weekly-lecture")

    assert lecture.start.utcoffset() == timedelta(hours=-4), "September is EDT, not EST"
    assert lecture.start.astimezone(EASTERN).hour == 10
    assert lecture.start.astimezone(EASTERN).minute == 40
    assert lecture.location == "LIT 0101"


async def test_recurrence_expands_across_the_window() -> None:
    clock = FrozenClock(datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    client = _client([], clock=clock)
    start, end = _window()

    feed = await client.get_events(start, end)

    lectures = [e for e in feed.events if e.uid == "weekly-lecture"]
    assert len(lectures) == 2, "TU and WE inside a three-day window"
    assert [e.start.astimezone(EASTERN).day for e in lectures] == [1, 2]


async def test_free_events_are_marked_not_busy() -> None:
    clock = FrozenClock(datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    client = _client([], clock=clock)
    start, end = _window()

    feed = await client.get_events(start, end)
    advising = next(e for e in feed.events if e.uid == "one-off")

    assert advising.busy is False
    assert advising.location is None


async def test_feed_is_cached_and_not_refetched_every_poll() -> None:
    """The board polls every 10s; Exchange regenerates a published feed on the
    order of hours. Refetching per poll is pure waste."""
    clock = FrozenClock(datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    fetches: list[int] = []
    client = _client(fetches, clock=clock)
    start, end = _window()

    for _ in range(5):
        await client.get_events(start, end)
    assert len(fetches) == 1

    clock.advance(1000)
    await client.get_events(start, end)
    assert len(fetches) == 2, "past the TTL it refetches"


async def test_a_failed_refresh_keeps_the_last_good_timetable() -> None:
    """A blip at Microsoft must date the calendar, not blank it."""
    clock = FrozenClock(datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    fetches: list[int] = []

    async def flaky() -> bytes:
        fetches.append(1)
        if len(fetches) > 1:
            raise ConnectionError("outlook unreachable")
        return FIXTURE.read_bytes()

    client = IcsCalendarClient("https://example.invalid/cal.ics", clock=clock, fetch=flaky)
    start, end = _window()

    first = await client.get_events(start, end)
    assert first.error is None
    assert len(first.events) == 3

    clock.advance(1000)
    second = await client.get_events(start, end)

    assert len(second.events) == 3, "events survive the failure"
    assert second.error is not None and "unreachable" in second.error
    assert second.fetched_at == first.fetched_at, "and are correctly dated to the last success"


async def test_a_feed_that_never_loads_reports_empty_not_fake() -> None:
    clock = FrozenClock(datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    client = _client([], clock=clock, fail=True)
    start, end = _window()

    feed = await client.get_events(start, end)

    assert feed.events == ()
    assert feed.fetched_at is None
    assert feed.error is not None


@pytest.mark.parametrize("days", [1, 3])
async def test_window_bounds_are_respected(days: int) -> None:
    clock = FrozenClock(datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    client = _client([], clock=clock)
    start = datetime(2026, 9, 1, 0, 0, tzinfo=EASTERN)

    feed = await client.get_events(start, start + timedelta(days=days))

    assert all(start <= e.start < start + timedelta(days=days) for e in feed.events)
