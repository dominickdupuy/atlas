"""Reconnect policy shared by long-lived adapters (MQTT, Matter, ...). Pure:
no I/O, no clock reads beyond the injectable jitter source."""

from __future__ import annotations

import random
from collections.abc import Callable

INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
BACKOFF_FACTOR = 2.0


class ReconnectBackoff:
    """Exponential backoff with partial jitter.

    The delay is drawn from the top half of the current window (50-100% of
    it), which spreads retries without ever collapsing back to a busy loop
    the way full jitter can.
    """

    def __init__(
        self,
        *,
        initial: float = INITIAL_BACKOFF_SECONDS,
        maximum: float = MAX_BACKOFF_SECONDS,
        factor: float = BACKOFF_FACTOR,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._initial = initial
        self._maximum = maximum
        self._factor = factor
        self._jitter = jitter
        self._attempts = 0

    @property
    def attempts(self) -> int:
        return self._attempts

    def reset(self) -> None:
        self._attempts = 0

    def next_delay(self) -> float:
        window = min(self._maximum, self._initial * self._factor**self._attempts)
        self._attempts += 1
        return window * (0.5 + 0.5 * self._jitter())
