"""Published-ICS calendar client.

D19 says a capability that is one unauthenticated endpoint is a plain HTTP
function inside the runner, not an MCP server — weather is the stated
example, and a published .ics feed is exactly the same shape. It is also the
only shape available here: the source calendar lives in a tenant that will
not grant an API client, so it is shared out as a published feed instead.

Three things this module exists to get right:

1. **Windows timezone names.** Exchange emits `TZID:Eastern Standard Time`,
   which is not an IANA zone and is not "always -5" — the feed carries
   VTIMEZONE blocks with the real DST rules, and icalendar builds the zone
   from those. A class shown an hour off is worse than no calendar at all.
2. **Recurrence.** A timetable is almost entirely RRULE, with individual
   moved lectures as RECURRENCE-ID overrides. Expansion is delegated rather
   than hand-rolled.
3. **Staleness.** The feed is a third party. Parsing is cached, failures keep
   the last good answer, and the caller is told when it was fetched so the
   board can say so out loud.

The feed URL is a bearer credential: anyone holding it can read the calendar
without authenticating. It lives in the environment, never in the repo.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import icalendar
import recurring_ical_events

from atlas.connectors.application.ports import CalendarEvent, CalendarFeed
from atlas.shared.clock import Clock, SystemClock

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_TTL_SECONDS = 60.0
"""Refetch cadence. Kept short so the board picks up a republished feed
quickly.

Worth knowing what this does and does not buy: Exchange regenerates a
published feed on its own schedule and returns no cache headers at all, so an
edit in Outlook still will not appear until Microsoft republishes. This
shortens only the second half of that delay — our copy is at most a minute
behind Microsoft's rather than fifteen.

Because there are no cache headers there is no conditional GET to lean on
either, so every poll transfers the whole file. What the client can avoid is
the expensive half: an unchanged payload is detected by digest and the parsed
expansion is reused, so a minute-by-minute poll costs one download and no
CPU."""

MAX_EVENTS = 200

Fetcher = Callable[[], Awaitable[bytes]]


class IcsCalendarClient:
    def __init__(
        self,
        url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Clock | None = None,
        fetch: Fetcher | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._ttl = ttl_seconds
        self._clock = clock or SystemClock()
        self._fetch: Fetcher = fetch or self._http_fetch
        self._raw: bytes | None = None
        self._digest: str | None = None
        self._fetched_at: dt.datetime | None = None
        self._last_error: str | None = None
        self._expanded: tuple[bytes, dt.datetime, dt.datetime, tuple[CalendarEvent, ...]] | None = (
            None
        )

    async def _http_fetch(self) -> bytes:
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(self._url)
            response.raise_for_status()
            return response.content

    def _is_fresh(self, now: dt.datetime) -> bool:
        if self._raw is None or self._fetched_at is None:
            return False
        return (now - self._fetched_at).total_seconds() < self._ttl

    async def get_events(self, start: dt.datetime, end: dt.datetime) -> CalendarFeed:
        now = self._clock.now()
        if not self._is_fresh(now):
            try:
                fetched = await self._fetch()
                digest = hashlib.sha256(fetched).hexdigest()
                # Keep the SAME bytes object when nothing changed: the
                # expansion cache below is keyed on its identity, so an
                # unchanged feed costs no re-parse at all.
                if digest != self._digest or self._raw is None:
                    self._raw = fetched
                    self._digest = digest
                self._fetched_at = now
                self._last_error = None
            except Exception as exc:
                # Keep the last good copy: a blip at Microsoft must not blank
                # the timetable, it must only date it.
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("calendar feed fetch failed: %s", self._last_error)

        if self._raw is None:
            return CalendarFeed(events=(), fetched_at=None, error=self._last_error)

        raw = self._raw
        cached = self._expanded
        if cached and cached[0] is raw and cached[1] == start and cached[2] == end:
            return CalendarFeed(
                events=cached[3], fetched_at=self._fetched_at, error=self._last_error
            )
        try:
            # Parsing and recurrence expansion are pure CPU and block the loop
            # that is also serving the board; hand them to a thread.
            events = await asyncio.to_thread(_expand, raw, start, end)
        except Exception as exc:
            logger.exception("calendar feed parse failed")
            return CalendarFeed(
                events=(), fetched_at=self._fetched_at, error=f"parse failed: {exc}"
            )

        self._expanded = (raw, start, end, events)
        return CalendarFeed(events=events, fetched_at=self._fetched_at, error=self._last_error)


def _text(component: Any, key: str) -> str | None:
    """Components come back from an untyped library; Any stops here."""
    value = component.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _expand(raw: bytes, start: dt.datetime, end: dt.datetime) -> tuple[CalendarEvent, ...]:
    calendar = icalendar.Calendar.from_ical(raw)
    occurrences = recurring_ical_events.of(calendar).between(start, end)

    events: list[CalendarEvent] = []
    for occurrence in occurrences:
        begin = occurrence.get("DTSTART")
        if begin is None:
            continue
        begins = begin.dt
        all_day = not isinstance(begins, dt.datetime)
        if all_day:
            # A date, not a datetime: pin it to the window's timezone so the
            # board can place it without guessing.
            begins = dt.datetime.combine(begins, dt.time.min, tzinfo=start.tzinfo)

        finish = occurrence.get("DTEND")
        finishes: dt.datetime | None = None
        if finish is not None:
            candidate = finish.dt
            if isinstance(candidate, dt.datetime):
                finishes = candidate
            else:
                finishes = dt.datetime.combine(candidate, dt.time.min, tzinfo=start.tzinfo)

        busy = (_text(occurrence, "X-MICROSOFT-CDO-BUSYSTATUS") or "BUSY").upper() != "FREE"

        events.append(
            CalendarEvent(
                uid=str(occurrence.get("UID", "")) or f"{begins.isoformat()}",
                summary=_text(occurrence, "SUMMARY") or "(no title)",
                start=begins,
                end=finishes,
                location=_text(occurrence, "LOCATION"),
                all_day=all_day,
                busy=busy,
            )
        )

    events.sort(key=lambda event: event.start)
    return tuple(events[:MAX_EVENTS])
