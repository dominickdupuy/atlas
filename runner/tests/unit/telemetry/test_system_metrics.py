"""Host metric parsing, against captured /proc and /sys text.

The samples below are copied verbatim from the Pi this runs on, including
the trailing dots in /proc/net/wireless that are not decimal points and the
kB unit in /proc/meminfo that is not bytes — the two things most likely to
be got quietly wrong.
"""

from __future__ import annotations

from pathlib import Path

from atlas.telemetry.infrastructure.system_metrics import (
    SystemMetrics,
    SystemMetricsReader,
    parse_meminfo,
    parse_thermal_millidegrees,
    parse_uptime,
    parse_wireless,
)

WIRELESS_SAMPLE = (
    "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
    " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
    " wlan0: 0000   63.  -47.  -256        0      0      0    117      0        0\n"
)

MEMINFO_SAMPLE = """MemTotal:        8251776 kB
MemFree:          463760 kB
MemAvailable:    6629696 kB
Buffers:          302480 kB
"""


def test_thermal_is_millidegrees() -> None:
    assert parse_thermal_millidegrees("48500\n") == 48.5


def test_thermal_survives_garbage() -> None:
    assert parse_thermal_millidegrees("") is None
    assert parse_thermal_millidegrees("warm\n") is None


def test_uptime_takes_the_first_field() -> None:
    assert parse_uptime("3331.61 12022.26\n") == 3331.61
    assert parse_uptime("") is None


def test_meminfo_converts_kb_to_bytes() -> None:
    total, available = parse_meminfo(MEMINFO_SAMPLE)
    assert total == 8251776 * 1024
    assert available == 6629696 * 1024


def test_meminfo_prefers_available_over_free() -> None:
    """MemFree on a box running Home Assistant reads as alarmingly low while
    the machine is perfectly healthy; MemAvailable is the honest number."""
    _, available = parse_meminfo(MEMINFO_SAMPLE)
    assert available == 6629696 * 1024, "must not be MemFree (463760 kB)"


def test_meminfo_missing_keys_are_none() -> None:
    assert parse_meminfo("Nothing: 1 kB\n") == (None, None)


def test_wireless_strips_the_trailing_dots() -> None:
    link = parse_wireless(WIRELESS_SAMPLE)
    assert link is not None
    assert link.interface == "wlan0"
    assert link.signal_dbm == -47.0
    assert link.link_percent is not None
    assert 89 < link.link_percent < 91  # 63 of 70


def test_wireless_unknown_interface_is_none() -> None:
    assert parse_wireless(WIRELESS_SAMPLE, interface="wlan9") is None


def test_wireless_header_only_is_none() -> None:
    header = "\n".join(WIRELESS_SAMPLE.splitlines()[:2])
    assert parse_wireless(header) is None


def test_percentages_are_derived_not_read() -> None:
    metrics = SystemMetrics(
        mem_total_bytes=1000,
        mem_available_bytes=250,
        disk_total_bytes=200,
        disk_used_bytes=50,
    )
    assert metrics.mem_used_percent == 75.0
    assert metrics.disk_used_percent == 25.0


def test_percentages_are_none_without_their_inputs() -> None:
    assert SystemMetrics().mem_used_percent is None
    assert SystemMetrics(mem_total_bytes=0, mem_available_bytes=0).mem_used_percent is None


def _fake_roots(tmp_path: Path) -> tuple[Path, Path]:
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "uptime").write_text("3331.61 12022.26\n")
    (proc / "meminfo").write_text(MEMINFO_SAMPLE)
    (proc / "net" / "wireless").write_text(WIRELESS_SAMPLE)
    thermal = tmp_path / "thermal_zone0"
    thermal.mkdir()
    (thermal / "temp").write_text("48500\n")
    return proc, thermal


def test_reader_assembles_a_full_snapshot(tmp_path: Path) -> None:
    proc, thermal = _fake_roots(tmp_path)
    reader = SystemMetricsReader(proc=proc, thermal_zone=thermal, disk=tmp_path)

    metrics = reader.read()

    assert metrics.cpu_temp_c == 48.5
    assert metrics.uptime_seconds == 3331.61
    assert metrics.mem_total_bytes == 8251776 * 1024
    assert metrics.wifi is not None and metrics.wifi.signal_dbm == -47.0
    assert metrics.disk_total_bytes is not None and metrics.disk_total_bytes > 0
    assert metrics.load_1 is not None


def test_reader_degrades_instead_of_raising(tmp_path: Path) -> None:
    """A board that cannot read the CPU temperature must still show the job
    failures, which are the reason the screen exists (D11)."""
    reader = SystemMetricsReader(
        proc=tmp_path / "missing", thermal_zone=tmp_path / "missing", disk=tmp_path
    )

    metrics = reader.read()

    assert metrics.cpu_temp_c is None
    assert metrics.uptime_seconds is None
    assert metrics.wifi is None
    assert metrics.disk_total_bytes is not None, "disk still works"
