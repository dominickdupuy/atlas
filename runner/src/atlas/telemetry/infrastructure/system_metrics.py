"""Host metrics for the ops board, read straight from /proc and /sys.

No psutil: everything here is four small text parsers over files the kernel
already exposes, and a dependency that ships compiled wheels per platform is
a poor trade for that (the D14 rule — debuggability outranks elegance — and
the CI matrix includes Windows, where these paths simply do not exist).

Every reader is individually fault-tolerant and returns None rather than
raising. A board that cannot show the CPU temperature must still show the
job failures, which are the reason the screen exists (D11). The parse
functions are pure and take text, so the tests do not need a Raspberry Pi.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# /proc/net/wireless reports link quality against a driver-specific ceiling;
# 70 is the near-universal value for the mac80211 stack this Pi's brcmfmac
# driver sits on, and the number is only ever used to draw a bar.
_WIRELESS_LINK_MAX = 70.0


@dataclass(frozen=True)
class WifiLink:
    interface: str
    link_percent: float | None
    signal_dbm: float | None


@dataclass(frozen=True)
class SystemMetrics:
    cpu_temp_c: float | None = None
    load_1: float | None = None
    load_5: float | None = None
    load_15: float | None = None
    mem_total_bytes: int | None = None
    mem_available_bytes: int | None = None
    disk_total_bytes: int | None = None
    disk_used_bytes: int | None = None
    uptime_seconds: float | None = None
    wifi: WifiLink | None = None

    @property
    def mem_used_percent(self) -> float | None:
        if not self.mem_total_bytes or self.mem_available_bytes is None:
            return None
        used = self.mem_total_bytes - self.mem_available_bytes
        return 100.0 * used / self.mem_total_bytes

    @property
    def disk_used_percent(self) -> float | None:
        if not self.disk_total_bytes or self.disk_used_bytes is None:
            return None
        return 100.0 * self.disk_used_bytes / self.disk_total_bytes


def parse_thermal_millidegrees(text: str) -> float | None:
    """/sys/class/thermal/thermal_zone0/temp holds millidegrees Celsius."""
    try:
        return int(text.strip()) / 1000.0
    except ValueError:
        return None


def parse_uptime(text: str) -> float | None:
    """/proc/uptime: '<uptime seconds> <idle seconds>'."""
    head = text.split(maxsplit=1)
    if not head:
        return None
    try:
        return float(head[0])
    except ValueError:
        return None


def parse_meminfo(text: str) -> tuple[int | None, int | None]:
    """Returns (total, available) in bytes.

    MemAvailable, not MemFree: on a box running Home Assistant most of RAM
    is page cache, and MemFree would read as an alarming 5% free while the
    machine is entirely healthy.
    """
    wanted: dict[str, int | None] = {"MemTotal": None, "MemAvailable": None}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in wanted and wanted[key] is None:
            fields = rest.split()
            if fields:
                try:
                    wanted[key] = int(fields[0]) * 1024
                except ValueError:
                    continue
    return wanted["MemTotal"], wanted["MemAvailable"]


def parse_wireless(text: str, interface: str = "wlan0") -> WifiLink | None:
    """/proc/net/wireless, two header lines then one row per interface:

        wlan0: 0000   63.  -47.  -256  ...

    Columns are status, link quality, signal level (dBm), noise. The values
    carry a trailing '.' which is not a decimal point.
    """
    for line in text.splitlines():
        name, separator, rest = line.partition(":")
        if not separator or name.strip() != interface:
            continue
        fields = rest.split()
        if len(fields) < 3:
            return WifiLink(interface=interface, link_percent=None, signal_dbm=None)
        link = _as_float(fields[1])
        level = _as_float(fields[2])
        return WifiLink(
            interface=interface,
            link_percent=(
                min(100.0, 100.0 * link / _WIRELESS_LINK_MAX) if link is not None else None
            ),
            signal_dbm=level,
        )
    return None


def _as_float(value: str) -> float | None:
    try:
        return float(value.rstrip("."))
    except ValueError:
        return None


class SystemMetricsReader:
    """Roots are injected so the tests can point at a fixture directory."""

    def __init__(
        self,
        *,
        proc: Path = Path("/proc"),
        thermal_zone: Path = Path("/sys/class/thermal/thermal_zone0"),
        disk: Path = Path("/"),
        interface: str = "wlan0",
    ) -> None:
        self._proc = proc
        self._thermal_zone = thermal_zone
        self._disk = disk
        self._interface = interface

    def read(self) -> SystemMetrics:
        total, available = self._meminfo()
        disk_total, disk_used = self._disk_usage()
        load_1, load_5, load_15 = self._loadavg()
        return SystemMetrics(
            cpu_temp_c=self._cpu_temp(),
            load_1=load_1,
            load_5=load_5,
            load_15=load_15,
            mem_total_bytes=total,
            mem_available_bytes=available,
            disk_total_bytes=disk_total,
            disk_used_bytes=disk_used,
            uptime_seconds=self._uptime(),
            wifi=self._wifi(),
        )

    def _read_text(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("metric source %s unavailable: %s", path, exc)
            return None

    def _cpu_temp(self) -> float | None:
        text = self._read_text(self._thermal_zone / "temp")
        return parse_thermal_millidegrees(text) if text else None

    def _uptime(self) -> float | None:
        text = self._read_text(self._proc / "uptime")
        return parse_uptime(text) if text else None

    def _meminfo(self) -> tuple[int | None, int | None]:
        text = self._read_text(self._proc / "meminfo")
        return parse_meminfo(text) if text else (None, None)

    def _wifi(self) -> WifiLink | None:
        text = self._read_text(self._proc / "net" / "wireless")
        return parse_wireless(text, self._interface) if text else None

    def _loadavg(self) -> tuple[float | None, float | None, float | None]:
        # os.getloadavg does not exist on Windows at all, and the CI matrix
        # runs there to keep the spawn-based job executor honest. An
        # AttributeError here would 500 the whole status endpoint.
        getloadavg = getattr(os, "getloadavg", None)
        if getloadavg is None:
            return None, None, None
        try:
            one, five, fifteen = getloadavg()
        except OSError:
            return None, None, None
        return one, five, fifteen

    def _disk_usage(self) -> tuple[int | None, int | None]:
        try:
            usage = shutil.disk_usage(self._disk)
        except OSError as exc:
            logger.debug("disk usage for %s unavailable: %s", self._disk, exc)
            return None, None
        return usage.total, usage.used
