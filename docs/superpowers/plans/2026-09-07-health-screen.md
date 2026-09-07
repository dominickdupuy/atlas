# Health Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A nightly job on atlas that turns FreeReps' Postgres data into one `board.json` health document with a deviation/alert system, and a second screen on the wall board, opened by ColoPlay key 2 and scrolled by the dial, that renders it.

**Architecture:** Two repositories with one file between them. `dominickdupuy/health` (a new uv project, `atlas-health`) reads FreeReps tables through the read-only `analysis` role, computes nights, heart, exercise, weight and the deviation system from spec §4, writes `/var/lib/atlas-health/board.json`, keeps its own history in SQLite beside it, pushes ntfy on ALERT, and prints an `atlas-summary` line so it shows in the RUNS panel as a hosted repo. `pi-home` (this repo) gains a fault-tolerant `HealthBoardReader` that passes that document through `/api/status` as `health`, and `board.js` gains a second `main` region, screen switching on the dial keys, dial scrolling, and hand-drawn SVG blocks, following the existing weather-chart pattern. No chart library, nothing interactive.

**Tech Stack:** Python 3.12+ (uv), psycopg 3 (read), stdlib `sqlite3` (write), pandas, numpy, httpx; FastAPI/pydantic runner (existing, mypy strict, ruff, pytest with sockets disabled); vanilla JS + SVG on a 1440x1080 canvas (4:3 panel); hosted-repo cron via `scripts/repos.py`; ntfy.

**Spec:** `docs/superpowers/specs/2026-09-07-health-screen-spec.md` (v3), copied into the health repo as `docs/spec.md` by Task 1 so that repo is self-contained. Section numbers below refer to it.

## Global Constraints

- **Two repositories.** Tasks 1 to 14 are done in a clone of `git@github.com:dominickdupuy/health.git` (empty and private as of 2026-09-07); every path in them is relative to that clone's root. Tasks 15 to 20 are done in `pi-home`; their paths are relative to its root. No task edits both. The only thing they share is the shape of `/var/lib/atlas-health/board.json`, defined once in Task 12.
- **Postgres is read-only; SQLite is the only thing this project writes.** The `analysis` role reads FreeReps' `public` schema and nothing else — no writes to Postgres at all. (The empty `analysis` schema created on atlas in the v2 work is simply left unused; the role keeps its read grants.) Computed nights, runs and status history go to `/var/lib/atlas-health/health.db` (stdlib `sqlite3`), beside the board document. This keeps the whole write path testable in a `tmp_path` and means a Postgres outage costs the history nothing but a night.
- Runner tests are hermetic: `--disable-socket --allow-hosts=127.0.0.1,::1`. Nothing in the runner may open a network or database connection to render health. The runner only reads a JSON file.
- Runner code passes `uv run mypy src tests` (strict) and `uv run ruff check .` (rules E,F,I,UP,B,SIM,ASYNC,DTZ,RUF; line length 100). Every datetime is tz-aware (DTZ). The health repo uses the same rule set and line length.
- The board is output only (D11): no buttons, links, inputs. The dial is the only input.
- The deployed trees are `/opt/atlas` (pi-home, `git reset --hard` to `origin/release` every 5 minutes) and `/opt/health` (the health repo, kept by `scripts/repos.py`). Secrets and host state never live in either checkout: health config lives in `/home/domdd/atlas-health-analysis/.env`, output and history in `/var/lib/atlas-health/`.
- FreeReps facts (spec §2), verified against the live database on 2026-09-07 (574,268 `health_metrics`, 6,661 `sleep_stages`, 122 `workouts` for this user): `user_id` is looked up by login `domdd305@gmail.com`, never hard-coded. `heart_rate` rows carry `avg_val`/`min_val`, NULL `qty`; read `COALESCE(qty, avg_val)`. Watch stage source is `Dominick’s Apple Watch` (U+2019). Local zone `America/New_York`.
- Config defaults (spec §4): `DOB=2005-08-31`, `HEIGHT_M=1.854`, `HR_MAX=200`, `SLEEP_GOAL_H=7.5`, `RUNS_PER_WEEK_TARGET=3`, `STRENGTH_PER_WEEK_TARGET=2`, `EASY_SHARE_TARGET=0.70`, `FIXED_HR_BAND=150-160`, `FREE_DAYS=Sat,Sun`. Miles are the primary running unit.
- Windows dev machine: run health tests with `uv run pytest` from the health clone's root; runner tests from `pi-home/runner/`. Each has its own `uv.lock`.
- Excluded from the screen, always: steps, stand hours, move ring, daily calories, gait metrics, audio exposure, body fat, lean mass, scale BMI (spec §7).

---

## File structure

In the **health repo** (`dominickdupuy/health`), everything at the clone root:

```
pyproject.toml, uv.lock, README.md
docs/spec.md         copy of the v3 spec, so this repo stands alone
src/atlas_health/
  __init__.py
  config.py        HealthConfig.from_env(); zone edges; typed settings
  source.py        DataSource protocol, PostgresSource, CsvSource, DATASETS
  sleep.py         stages -> nights (§4.1), timing/quantity/score (§4.2)
  heart.py         sleep HR, HR-min timing, HRV/resp nightly, RHR (§4.3)
  exercise.py      zones, TRIMP, easy share, load, consistency, capacity (§4.6, §4.7)
  weight.py        §4.5
  deviations.py    Signal engine, S/H/F/W signals, composite, status, drivers, action (§4.4, §4.8, §4.9)
  board.py         assemble board.json (schema v1) from the pieces
  notify.py        ntfy push + dedupe state (§4.10)
  store.py         SQLite history: nights, runs, status_history
  cli.py           `atlas-health nightly|weekly|dump-fixture|render`
tests/
  conftest.py      fixture CsvSource, as_of, config
  fixtures/        CSVs dumped from the live DB (Task 2)
  test_*.py
```

In **pi-home**:

```
runner/src/atlas/
  config.py                                  + health_board_path
  telemetry/infrastructure/health_board.py   NEW HealthBoardReader
  bootstrap/container.py                     + health_board reader wiring
  presentation/http/status.py                + HealthInfo, snapshot().health
  presentation/http/runs.py                  + ("nights", "{n} nights") figure label
  presentation/templates/board.html          + #main-health region
  presentation/static/board.js               screens, dial scroll, renderHealth*
  presentation/static/board.css              health styles
runner/tests/unit/telemetry/test_health_board.py   NEW
runner/tests/integration/test_api_status.py         + health cases
runner/tests/fixtures/health/board.json             NEW sample document
infra/repos.toml                             + health-nightly, health-weekly entries
docs/repos.md                                + health paragraph
```

The `board.json` document (schema v1) is the contract between the two repos. It is defined once, in Task 12, and the JS in Tasks 17 to 19 reads exactly those keys.

---

## Part A: the analysis package

### Task 1: Clone the health repo and scaffold it with config and a CLI skeleton

**Files:**
- Create: `pyproject.toml`, `README.md`, `src/atlas_health/__init__.py`, `src/atlas_health/config.py`, `src/atlas_health/cli.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `HealthConfig` dataclass (fields below), `HealthConfig.from_env(path: Path | None) -> HealthConfig`, `HealthConfig.zone_edges(rhr_60d: float) -> list[float]`, `main(argv) -> int`.

- [ ] **Step 1: Clone the empty repo and bring the spec with it**

`dominickdupuy/health` exists and is private, with no commits and no default branch. Clone it
beside `pi-home` and copy the spec in, so an executor working here never needs the other repo:

```bash
cd ~/Documents/GitHub
git clone git@github.com:dominickdupuy/health.git
cd health
git switch -c main
mkdir -p docs
cp ../pi-home/docs/superpowers/specs/2026-09-07-health-screen-spec.md docs/spec.md
```

Everything from here to Task 14 happens in this directory. Every path below is relative to it.

- [ ] **Step 2: Create the project files**

`pyproject.toml`:

```toml
[project]
name = "atlas-health"
version = "0.1.0"
description = "Nightly health analysis for the atlas board: FreeReps Postgres -> board.json + ntfy"
requires-python = ">=3.12"
dependencies = [
    "psycopg[binary]>=3.2",
    "pandas>=2.2",
    "numpy>=2.0",
    "httpx>=0.28",
    "tzdata>=2024.1",
]

[project.scripts]
atlas-health = "atlas_health.cli:main"

[dependency-groups]
dev = ["pytest>=8.3", "ruff>=0.16", "mypy>=1.14", "pandas-stubs>=2.2"]

[build-system]
requires = ["uv_build>=0.12.5,<0.13.0"]
build-backend = "uv_build"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "DTZ", "RUF"]

[tool.mypy]
strict = true
mypy_path = "src"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`src/atlas_health/__init__.py`: empty.

`src/atlas_health/config.py`:

```python
"""Configuration for the nightly job.

Read from a dotenv-style file (default /home/domdd/atlas-health-analysis/.env,
override with ATLAS_HEALTH_ENV_FILE) and then the process environment, which
wins. Nothing here is secret except PGPASSWORD and NTFY_TOKEN; keep the file
outside the /opt/atlas checkout because deploys `git reset --hard` there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_ENV_FILE = Path("/home/domdd/atlas-health-analysis/.env")
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class HealthConfig:
    pg_host: str
    pg_port: int
    pg_database: str
    pg_user: str
    pg_password: str
    login: str
    tz: str
    watch_source: str
    dob: date
    height_m: float
    sex: str
    hr_max: int
    sleep_goal_h: float
    runs_per_week_target: int
    strength_per_week_target: int
    easy_share_target: float
    fixed_hr_low: int
    fixed_hr_high: int
    free_days: frozenset[int]
    strength_names: frozenset[str]
    ntfy_url: str
    ntfy_topic: str
    ntfy_token: str
    state_dir: Path
    weekly_summary: bool

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def dsn(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_database} "
            f"user={self.pg_user} password={self.pg_password}"
        )

    def age_on(self, day: date) -> int:
        years = day.year - self.dob.year
        if (day.month, day.day) < (self.dob.month, self.dob.day):
            years -= 1
        return years

    def zone_edges(self, rhr_60d: float) -> list[float]:
        """Karvonen edges [Z2 low, Z3 low, Z4 low, Z5 low] in bpm (spec §4.7)."""
        hrr = self.hr_max - rhr_60d
        return [round(rhr_60d + hrr * frac, 1) for frac in (0.60, 0.70, 0.80, 0.90)]

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> HealthConfig:
        path = env_file or Path(os.environ.get("ATLAS_HEALTH_ENV_FILE", DEFAULT_ENV_FILE))
        values = read_env_file(path)
        values.update({k: v for k, v in os.environ.items() if k in _KEYS})

        def get(key: str, default: str) -> str:
            return values.get(key, default)

        band = get("FIXED_HR_BAND", "150-160").split("-")
        free = get("FREE_DAYS", "Sat,Sun")
        return cls(
            pg_host=get("PGHOST", "127.0.0.1"),
            pg_port=int(get("PGPORT", "5432")),
            pg_database=get("PGDATABASE", "freereps"),
            pg_user=get("PGUSER", "analysis"),
            pg_password=get("PGPASSWORD", ""),
            login=get("FREEREPS_LOGIN", "domdd305@gmail.com"),
            tz=get("LOCAL_TZ", "America/New_York"),
            watch_source=get("WATCH_SOURCE", "Dominick’s Apple Watch"),
            dob=date.fromisoformat(get("DOB", "2005-08-31")),
            height_m=float(get("HEIGHT_M", "1.854")),
            sex=get("SEX", "male"),
            hr_max=int(get("HR_MAX", "200")),
            sleep_goal_h=float(get("SLEEP_GOAL_H", "7.5")),
            runs_per_week_target=int(get("RUNS_PER_WEEK_TARGET", "3")),
            strength_per_week_target=int(get("STRENGTH_PER_WEEK_TARGET", "2")),
            easy_share_target=float(get("EASY_SHARE_TARGET", "0.70")),
            fixed_hr_low=int(band[0]),
            fixed_hr_high=int(band[1]),
            free_days=frozenset(WEEKDAYS[d.strip().lower()[:3]] for d in free.split(",") if d),
            strength_names=frozenset(
                n.strip()
                for n in get("STRENGTH_NAMES", "Traditional Strength Training").split(",")
            ),
            ntfy_url=get("NTFY_URL", "http://127.0.0.1:8090"),
            ntfy_topic=get("NTFY_TOPIC", ""),
            ntfy_token=get("NTFY_TOKEN", ""),
            state_dir=Path(get("STATE_DIR", "/var/lib/atlas-health")),
            weekly_summary=get("WEEKLY_SUMMARY", "false").lower() == "true",
        )


_KEYS = {
    "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "FREEREPS_LOGIN", "LOCAL_TZ",
    "WATCH_SOURCE", "DOB", "HEIGHT_M", "SEX", "HR_MAX", "SLEEP_GOAL_H", "RUNS_PER_WEEK_TARGET",
    "STRENGTH_PER_WEEK_TARGET", "EASY_SHARE_TARGET", "FIXED_HR_BAND", "FREE_DAYS",
    "STRENGTH_NAMES", "NTFY_URL", "NTFY_TOPIC", "NTFY_TOKEN", "STATE_DIR", "WEEKLY_SUMMARY",
}
```

`src/atlas_health/cli.py` (skeleton; subcommands are filled in by Tasks 2 and 14):

```python
"""atlas-health: nightly | weekly | dump-fixture | render."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="atlas-health")
    ap.add_argument("--env-file", type=Path, default=None)
    ap.add_argument("--as-of", default=None, help="ISO datetime; default now")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("nightly", help="compute board.json, store history, push alerts")
    sub.add_parser("weekly", help="push the Sunday summary")
    df = sub.add_parser("dump-fixture", help="write the datasets as CSV for tests")
    df.add_argument("out_dir", type=Path)
    rd = sub.add_parser("render", help="compute board.json from a CSV fixture dir, no DB")
    rd.add_argument("fixture_dir", type=Path)
    rd.add_argument("out_file", type=Path)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"atlas-health: {args.cmd} not implemented yet", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

`README.md`:

```markdown
# atlas-health

Nightly analysis of the FreeReps database on atlas. Reads Postgres through the
read-only `analysis` role, writes `/var/lib/atlas-health/board.json` for the
wall board's health screen (rendered by the `pi-home` repo), keeps its history
in `/var/lib/atlas-health/health.db`, and pushes ntfy on ALERT.

Spec: `docs/spec.md`. Deployed to `/opt/health` on atlas and scheduled by
`pi-home`'s `scripts/repos.py`; configuration lives outside the checkout in
`/home/domdd/atlas-health-analysis/.env`.

    uv run atlas-health nightly                          # on the Pi, via repos.py
    uv run atlas-health render tests/fixtures out.json   # anywhere, no DB
    uv run pytest
```

- [ ] **Step 3: Write the failing config test**

`tests/test_config.py`:

```python
from datetime import date
from pathlib import Path

from atlas_health.config import HealthConfig


def test_from_env_file_and_defaults(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("PGPASSWORD=pw\nHR_MAX=198\nFIXED_HR_BAND=145-155\nFREE_DAYS=Fri,Sat\n")
    cfg = HealthConfig.from_env(env)
    assert cfg.pg_password == "pw"
    assert cfg.hr_max == 198
    assert (cfg.fixed_hr_low, cfg.fixed_hr_high) == (145, 155)
    assert cfg.free_days == frozenset({4, 5})
    assert cfg.dob == date(2005, 8, 31)
    assert cfg.sleep_goal_h == 7.5
    assert cfg.watch_source == "Dominick’s Apple Watch"


def test_age_and_zone_edges(tmp_path: Path) -> None:
    cfg = HealthConfig.from_env(tmp_path / "missing.env")
    assert cfg.age_on(date(2026, 9, 7)) == 21
    assert cfg.age_on(date(2026, 8, 30)) == 20
    # HR_MAX 200, rhr 53: HRR 147 -> Z2 141.2, Z3 155.9, Z4 170.6, Z5 185.3 (spec §4.7)
    assert cfg.zone_edges(53.0) == [141.2, 155.9, 170.6, 185.3]
```

- [ ] **Step 4: Run it, expect failure**

Run: `uv sync && uv run pytest tests/test_config.py -v`
Expected: FAIL (module not found) until the Step 2 files exist; after they exist, PASS.

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: clean.

- [ ] **Step 6: Commit and push**

```bash
git add -A
git commit -m "scaffold atlas-health: config, CLI skeleton, spec"
git push -u origin main
```

---

### Task 2: Data sources and the fixture dump

**Files:**
- Create: `src/atlas_health/source.py`, `tests/conftest.py`, `tests/fixtures/*.csv` (generated), `tests/test_source.py`
- Modify: `src/atlas_health/cli.py` (dump-fixture)

**Interfaces:**
- Produces: `DATASETS: dict[str, Dataset]`; `class DataSource(Protocol): def load(self, name: str) -> pd.DataFrame`; `PostgresSource(config)`, `CsvSource(dir)`; `dump_fixture(source, out_dir)`.
- Every loaded frame has tz-aware UTC datetime columns as listed in `Dataset.datetime_cols`.

Dataset names and columns (the whole contract downstream code relies on):

| name | columns | window |
|---|---|---|
| `sleep_stages` | start_time, end_time, stage, duration_hr, source | 400 days |
| `heart_rate` | time, bpm (=COALESCE(qty, avg_val)), bpm_min (=COALESCE(qty, min_val, avg_val)) | 75 days |
| `vitals` | time, metric_name, value (=COALESCE(qty, avg_val)), units | 400 days; metrics hrv, resp, rhr, hrr, weight, walking hr |
| `workouts` | id, name, start_time, end_time, duration_sec, distance_m, avg_hr, max_hr, kcal, is_indoor | 400 days |
| `workout_hr` | time, workout_id, avg_bpm | 100 days |
| `running_speed` | time, mps | 100 days |
| `import_logs` | created_at, status | 10 days |

- [ ] **Step 1: Write the source module**

`src/atlas_health/source.py`:

```python
"""Where the numbers come from: Postgres on atlas, or CSVs dumped from it.

Every dataset is a pandas frame with the columns listed in DATASETS and
tz-aware UTC timestamps. The CSV source exists so every calculation can be
tested on the real shape of the data without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd
import psycopg

from atlas_health.config import HealthConfig


@dataclass(frozen=True)
class Dataset:
    sql: str
    columns: tuple[str, ...]
    datetime_cols: tuple[str, ...]


_UID = "(SELECT id FROM users WHERE login = %(login)s)"

DATASETS: dict[str, Dataset] = {
    "sleep_stages": Dataset(
        f"""SELECT start_time, end_time, stage, duration_hr, source
            FROM sleep_stages WHERE user_id = {_UID}
              AND start_time >= %(as_of)s - interval '400 days' AND start_time < %(as_of)s
            ORDER BY start_time""",
        ("start_time", "end_time", "stage", "duration_hr", "source"),
        ("start_time", "end_time"),
    ),
    "heart_rate": Dataset(
        f"""SELECT time, COALESCE(qty, avg_val) AS bpm,
                   COALESCE(qty, min_val, avg_val) AS bpm_min
            FROM health_metrics WHERE user_id = {_UID} AND metric_name = 'heart_rate'
              AND time >= %(as_of)s - interval '75 days' AND time < %(as_of)s
            ORDER BY time""",
        ("time", "bpm", "bpm_min"),
        ("time",),
    ),
    "vitals": Dataset(
        f"""SELECT time, metric_name, COALESCE(qty, avg_val) AS value, units
            FROM health_metrics WHERE user_id = {_UID}
              AND metric_name IN ('heart_rate_variability', 'respiratory_rate',
                                  'resting_heart_rate', 'heart_rate_recovery_one_minute',
                                  'weight_body_mass', 'walking_heart_rate_average')
              AND time >= %(as_of)s - interval '400 days' AND time < %(as_of)s
            ORDER BY time""",
        ("time", "metric_name", "value", "units"),
        ("time",),
    ),
    "workouts": Dataset(
        f"""SELECT id::text AS id, name, start_time, end_time, duration_sec,
                   distance AS distance_m, avg_heart_rate AS avg_hr,
                   max_heart_rate AS max_hr, active_energy_burned AS kcal, is_indoor
            FROM workouts WHERE user_id = {_UID}
              AND start_time >= %(as_of)s - interval '400 days' AND start_time < %(as_of)s
            ORDER BY start_time""",
        ("id", "name", "start_time", "end_time", "duration_sec", "distance_m", "avg_hr",
         "max_hr", "kcal", "is_indoor"),
        ("start_time", "end_time"),
    ),
    "workout_hr": Dataset(
        f"""SELECT time, workout_id::text AS workout_id, avg_bpm
            FROM workout_heart_rate WHERE user_id = {_UID}
              AND time >= %(as_of)s - interval '100 days' AND time < %(as_of)s
            ORDER BY time""",
        ("time", "workout_id", "avg_bpm"),
        ("time",),
    ),
    "running_speed": Dataset(
        f"""SELECT time, COALESCE(qty, avg_val) AS mps
            FROM health_metrics WHERE user_id = {_UID} AND metric_name = 'running_speed'
              AND time >= %(as_of)s - interval '100 days' AND time < %(as_of)s
            ORDER BY time""",
        ("time", "mps"),
        ("time",),
    ),
    "import_logs": Dataset(
        f"""SELECT created_at, status FROM import_logs WHERE user_id = {_UID}
              AND created_at >= %(as_of)s - interval '10 days' ORDER BY created_at""",
        ("created_at", "status"),
        ("created_at",),
    ),
}


class DataSource(Protocol):
    def load(self, name: str) -> pd.DataFrame: ...


def _finish(frame: pd.DataFrame, dataset: Dataset) -> pd.DataFrame:
    for col in dataset.datetime_cols:
        frame[col] = pd.to_datetime(frame[col], utc=True)
    return frame


class PostgresSource:
    def __init__(self, config: HealthConfig, as_of: pd.Timestamp) -> None:
        self._config = config
        self._as_of = as_of.to_pydatetime()

    def load(self, name: str) -> pd.DataFrame:
        dataset = DATASETS[name]
        with psycopg.connect(self._config.dsn) as conn, conn.cursor() as cur:
            cur.execute(dataset.sql, {"login": self._config.login, "as_of": self._as_of})
            rows = cur.fetchall()
        return _finish(pd.DataFrame(rows, columns=list(dataset.columns)), dataset)


class CsvSource:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def load(self, name: str) -> pd.DataFrame:
        dataset = DATASETS[name]
        path = self._dir / f"{name}.csv"
        if not path.exists():
            return _finish(pd.DataFrame(columns=list(dataset.columns)), dataset)
        return _finish(pd.read_csv(path), dataset)


def dump_fixture(source: DataSource, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in DATASETS:
        source.load(name).to_csv(out_dir / f"{name}.csv", index=False)
```

- [ ] **Step 2: Wire `dump-fixture` in the CLI**

In `cli.py` replace the `main` body:

```python
def _as_of(raw: str | None, tz: str) -> pd.Timestamp:
    if raw is None:
        return pd.Timestamp.now(tz=tz)
    stamp = pd.Timestamp(raw)
    return stamp.tz_localize(tz) if stamp.tzinfo is None else stamp.tz_convert(tz)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = HealthConfig.from_env(args.env_file)
    as_of = _as_of(args.as_of, config.tz)
    if args.cmd == "dump-fixture":
        dump_fixture(PostgresSource(config, as_of), args.out_dir)
        print(f"fixture written to {args.out_dir}")
        return 0
    print(f"atlas-health: {args.cmd} not implemented yet", file=sys.stderr)
    return 2
```

with imports `import pandas as pd`, `from atlas_health.config import HealthConfig`, `from atlas_health.source import PostgresSource, dump_fixture`.

- [ ] **Step 3: Dump the fixture from the live database**

The fixture is real personal data committed to a private repo; that is accepted by the owner.
The database listens on atlas's loopback only, so the dump runs there. Push what exists so far,
clone it on the Pi, and pull the CSVs back:

```bash
git push
ssh domdd@atlas 'git clone git@github-health:dominickdupuy/health.git /tmp/health-dump 2>/dev/null || git -C /tmp/health-dump pull'
ssh domdd@atlas 'cd /tmp/health-dump && ~/.local/bin/uv run atlas-health --env-file /home/domdd/atlas-health-analysis/.env --as-of 2026-09-07T10:00 dump-fixture /tmp/fixture && wc -l /tmp/fixture/*.csv'
scp domdd@atlas:/tmp/fixture/'*.csv' tests/fixtures/
```

The `github-health` ssh alias is created in Task 20, Step 1; if it does not exist yet, `scp -r`
the working tree to `/tmp/health-dump` instead and dump from there.

Expected: seven CSVs; `heart_rate.csv` about 9k rows, `sleep_stages.csv` about 5k rows,
`workouts.csv` 122 rows, `vitals.csv` a few thousand.

- [ ] **Step 4: Write conftest and a source test**

`tests/conftest.py`:

```python
from pathlib import Path

import pandas as pd
import pytest

from atlas_health.config import HealthConfig
from atlas_health.source import CsvSource

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = pd.Timestamp("2026-09-07T10:00", tz="America/New_York")


@pytest.fixture
def source() -> CsvSource:
    return CsvSource(FIXTURES)


@pytest.fixture
def config(tmp_path: Path) -> HealthConfig:
    return HealthConfig.from_env(tmp_path / "none.env")


@pytest.fixture
def as_of() -> pd.Timestamp:
    return AS_OF
```

`tests/test_source.py`:

```python
from pathlib import Path

from atlas_health.source import DATASETS, CsvSource


def test_fixture_datasets_have_contract_columns(source: CsvSource) -> None:
    for name, dataset in DATASETS.items():
        frame = source.load(name)
        assert list(frame.columns) == list(dataset.columns), name
        for col in dataset.datetime_cols:
            assert str(frame[col].dtype) == "datetime64[ns, UTC]", (name, col)


def test_missing_csv_is_an_empty_frame(tmp_path: Path) -> None:
    frame = CsvSource(tmp_path).load("running_speed")
    assert frame.empty and list(frame.columns) == ["time", "mps"]


def test_fixture_has_the_known_shape(source: CsvSource) -> None:
    stages = source.load("sleep_stages")
    assert (stages["source"] == "Dominick’s Apple Watch").sum() > 3000
    hr = source.load("heart_rate")
    assert hr["bpm"].notna().all()
    workouts = source.load("workouts")
    assert (workouts["name"] == "Running").sum() >= 40
```

- [ ] **Step 5: Run tests, lint, type-check**

Run: `uv run pytest -v && uv run ruff check . && uv run mypy src`
Expected: PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "health: data sources (Postgres, CSV) and a fixture dumped from the live database"
```

---
### Task 3: Sleep sessions from stages (§4.1)

**Files:**
- Create: `src/atlas_health/sleep.py`, `tests/test_sleep.py`

**Interfaces:**
- Produces: `cluster_nights(stages: pd.DataFrame, *, watch_source: str, tz: str, gap_hours: float = 2.0) -> pd.DataFrame` with one row per `night_date` and columns: `night_date` (date), `sleep_start`, `sleep_end` (UTC Timestamps), `asleep_min`, `deep_min`, `rem_min`, `core_min`, `awake_min`, `interruptions` (int), `naps_min`, `deep_pct`, `rem_pct`. Sorted by `night_date`. Includes every clustered session, so callers filter with `asleep_min >= 180` for "nights with data" (spec §4 conventions). Also `ASLEEP_STAGES = ("Core", "Deep", "REM", "Asleep")`.

- [ ] **Step 1: Write the failing tests**

`tests/test_sleep.py`:

```python
import pandas as pd

from atlas_health.sleep import cluster_nights
from atlas_health.source import CsvSource

TZ = "America/New_York"
WATCH = "Dominick’s Apple Watch"


def _stage(start: str, minutes: int, stage: str, source: str = WATCH) -> dict[str, object]:
    s = pd.Timestamp(start, tz="UTC")
    return {
        "start_time": s,
        "end_time": s + pd.Timedelta(minutes=minutes),
        "stage": stage,
        "duration_hr": minutes / 60,
        "source": source,
    }


def test_one_night_is_clustered_and_measured() -> None:
    # 03:00Z = 23:00 local; the night ends 07:30 local on Sep 5.
    rows = [
        _stage("2026-09-05T03:00Z", 30, "Core"),
        _stage("2026-09-05T03:30Z", 60, "Deep"),
        _stage("2026-09-05T04:30Z", 5, "Awake"),
        _stage("2026-09-05T04:35Z", 90, "Core"),
        _stage("2026-09-05T06:05Z", 60, "REM"),
        _stage("2026-09-05T07:05Z", 10, "Awake"),   # trailing awake, not an interruption
        _stage("2026-09-05T03:00Z", 300, "In Bed"),  # ignored
        _stage("2026-09-05T03:00Z", 300, "In Bed", source="Dominick Dupuy"),  # ignored
    ]
    nights = cluster_nights(pd.DataFrame(rows), watch_source=WATCH, tz=TZ)
    assert len(nights) == 1
    n = nights.iloc[0]
    assert str(n["night_date"]) == "2026-09-05"
    assert n["asleep_min"] == 240 and n["deep_min"] == 60 and n["rem_min"] == 60
    assert n["core_min"] == 120 and n["awake_min"] == 15
    assert n["interruptions"] == 1
    assert n["sleep_start"] == pd.Timestamp("2026-09-05T03:00Z")
    assert n["sleep_end"] == pd.Timestamp("2026-09-05T07:05Z")
    assert round(n["deep_pct"], 3) == 0.25


def test_gap_over_two_hours_splits_and_shorter_becomes_nap() -> None:
    rows = [
        _stage("2026-09-05T03:00Z", 360, "Core"),   # night, 23:00-05:00 local
        _stage("2026-09-05T18:00Z", 40, "Core"),    # 14:00 local nap, same night_date
    ]
    nights = cluster_nights(pd.DataFrame(rows), watch_source=WATCH, tz=TZ)
    assert len(nights) == 1
    assert nights.iloc[0]["asleep_min"] == 360
    assert nights.iloc[0]["naps_min"] == 40


def test_gap_under_two_hours_stays_one_session() -> None:
    rows = [
        _stage("2026-09-05T03:00Z", 120, "Core"),
        _stage("2026-09-05T06:30Z", 120, "Core"),   # 90 min gap
    ]
    nights = cluster_nights(pd.DataFrame(rows), watch_source=WATCH, tz=TZ)
    assert len(nights) == 1 and nights.iloc[0]["asleep_min"] == 240


def test_real_fixture_last_60_days_has_about_twenty_real_nights() -> None:
    stages = CsvSource(__import__("tests.conftest", fromlist=["FIXTURES"]).FIXTURES).load(
        "sleep_stages"
    )
    nights = cluster_nights(stages, watch_source=WATCH, tz=TZ)
    recent = nights[nights["night_date"] >= pd.Timestamp("2026-07-09").date()]
    real = recent[recent["asleep_min"] >= 180]
    # Spec §3: 20 sessions >= 3 h in the last 60 days before re-clustering; clustering
    # can only merge fragments, so the count stays between 18 and 24.
    assert 18 <= len(real) <= 24
    assert (real["asleep_min"] > 120).all() and (real["asleep_min"] < 720).all()
```

- [ ] **Step 2: Run, expect ImportError**

Run: `uv run pytest tests/test_sleep.py -v`

- [ ] **Step 3: Implement**

`src/atlas_health/sleep.py`:

```python
"""Nights from stages (spec §4.1) and sleep timing/quantity (spec §4.2)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

ASLEEP_STAGES = ("Core", "Deep", "REM", "Asleep")
NIGHT_COLUMNS = [
    "night_date", "sleep_start", "sleep_end", "asleep_min", "deep_min", "rem_min",
    "core_min", "awake_min", "interruptions", "naps_min", "deep_pct", "rem_pct",
]


def _minutes(frame: pd.DataFrame, stage: str) -> float:
    sub = frame[frame["stage"] == stage]
    return float(((sub["end_time"] - sub["start_time"]).dt.total_seconds() / 60).sum())


def _session_row(session: pd.DataFrame, tz: str) -> dict[str, object]:
    asleep = session[session["stage"].isin(ASLEEP_STAGES)]
    if asleep.empty:
        return {}
    first, last = asleep["start_time"].min(), asleep["end_time"].max()
    inner_awake = session[
        (session["stage"] == "Awake") & (session["start_time"] > first) & (session["end_time"] < last)
    ]
    deep, rem, core = _minutes(session, "Deep"), _minutes(session, "REM"), _minutes(session, "Core")
    asleep_min = deep + rem + core + _minutes(session, "Asleep")
    return {
        "night_date": last.tz_convert(tz).date(),
        "sleep_start": first,
        "sleep_end": last,
        "asleep_min": asleep_min,
        "deep_min": deep,
        "rem_min": rem,
        "core_min": core,
        "awake_min": _minutes(session, "Awake"),
        "interruptions": int(len(inner_awake)),
        "naps_min": 0.0,
        "deep_pct": deep / asleep_min if asleep_min else 0.0,
        "rem_pct": rem / asleep_min if asleep_min else 0.0,
    }


def cluster_nights(
    stages: pd.DataFrame, *, watch_source: str, tz: str, gap_hours: float = 2.0
) -> pd.DataFrame:
    rows = stages[(stages["source"] == watch_source) & (stages["stage"] != "In Bed")]
    rows = rows.sort_values("start_time").reset_index(drop=True)
    if rows.empty:
        return pd.DataFrame(columns=NIGHT_COLUMNS)
    prev_end = rows["end_time"].cummax().shift(1)
    new_session = (rows["start_time"] - prev_end) > pd.Timedelta(hours=gap_hours)
    session_id = new_session.fillna(True).cumsum()
    sessions = [_session_row(group, tz) for _, group in rows.groupby(session_id)]
    frame = pd.DataFrame([s for s in sessions if s])
    if frame.empty:
        return pd.DataFrame(columns=NIGHT_COLUMNS)
    # Longest session per night_date is the night; the others are naps.
    out: list[dict[str, object]] = []
    for _, group in frame.groupby("night_date", sort=True):
        ordered = group.sort_values("asleep_min", ascending=False)
        night = ordered.iloc[0].to_dict()
        night["naps_min"] = float(ordered.iloc[1:]["asleep_min"].sum())
        out.append(night)
    result = pd.DataFrame(out, columns=NIGHT_COLUMNS)
    result["interruptions"] = result["interruptions"].astype(int)
    return result.reset_index(drop=True)


def nights_with_data(nights: pd.DataFrame, as_of: pd.Timestamp, days: int) -> pd.DataFrame:
    """Nights >= 180 min asleep whose night_date is within `days` of as_of (spec §4)."""
    start: date = (as_of - pd.Timedelta(days=days)).date()
    mask = (nights["asleep_min"] >= 180) & (nights["night_date"] >= start)
    return nights[mask].sort_values("night_date").reset_index(drop=True)


def _unused() -> None:  # keeps numpy imported for Task 4 without a lint error
    np.zeros(0)
```

Remove `_unused` and the numpy import if Task 4 does not end up using numpy in this module.

- [ ] **Step 4: Run, expect PASS; lint; type-check**

Run: `uv run pytest tests/test_sleep.py -v && uv run ruff check . && uv run mypy src`

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/sleep.py tests/test_sleep.py
git commit -m "health: cluster watch sleep stages into nights with a 2 h gap rule and naps"
```

---

### Task 4: Sleep timing, quantity, debt, social jet lag, score (§4.2)

**Files:**
- Modify: `src/atlas_health/sleep.py`
- Test: `tests/test_sleep_timing.py`

**Interfaces:**
- Produces, all in `sleep.py`:
  - `add_timing(nights: pd.DataFrame, tz: str) -> pd.DataFrame` adds `bedtime`, `waketime`, `midsleep` (hours on the shifted 24 to 36 scale, floats).
  - `bedtime_dev_min(nights) -> float | None` (latest vs median of previous 13), `bedtime_sd_min(nights, n=14) -> float | None`.
  - `sleep_7n(nights) -> float | None` (minutes), `debt_14n_h(nights, goal_h) -> float`.
  - `social_jet_lag_h(nights, free_days: frozenset[int]) -> float | None`.
  - `target_bedtime(nights, goal_h) -> str | None` ("23:50").
  - `sleep_score(asleep_h, deep_h, rem_h, bedtime_dev_min, interruptions, goal_h) -> dict[str, float]` with keys `duration`, `consistency`, `interruptions`, `total`, `lever`.
- All "last N" helpers take nights already filtered by `nights_with_data` and sorted ascending.

- [ ] **Step 1: Write the failing tests**

`tests/test_sleep_timing.py`:

```python
import pandas as pd
import pytest

from atlas_health import sleep

TZ = "America/New_York"


def _night(night: str, start_local: str, end_local: str, asleep: float = 400) -> dict[str, object]:
    return {
        "night_date": pd.Timestamp(night).date(),
        "sleep_start": pd.Timestamp(start_local, tz=TZ).tz_convert("UTC"),
        "sleep_end": pd.Timestamp(end_local, tz=TZ).tz_convert("UTC"),
        "asleep_min": asleep, "deep_min": 60, "rem_min": 80, "core_min": asleep - 140,
        "awake_min": 10, "interruptions": 2, "naps_min": 0, "deep_pct": 0.15, "rem_pct": 0.2,
    }


@pytest.fixture
def nights() -> pd.DataFrame:
    rows = [
        _night("2026-09-01", "2026-08-31T23:30", "2026-09-01T07:00"),  # Tue
        _night("2026-09-02", "2026-09-02T00:30", "2026-09-02T07:30"),
        _night("2026-09-03", "2026-09-02T23:00", "2026-09-03T06:30"),
        _night("2026-09-05", "2026-09-05T01:00", "2026-09-05T09:00"),  # Sat
        _night("2026-09-06", "2026-09-06T01:30", "2026-09-06T09:30"),  # Sun
        _night("2026-09-07", "2026-09-07T00:00", "2026-09-07T07:00", asleep=300),
    ]
    return sleep.add_timing(pd.DataFrame(rows), TZ)


def test_bedtime_is_on_the_shifted_scale(nights: pd.DataFrame) -> None:
    assert nights.iloc[0]["bedtime"] == pytest.approx(23.5)
    assert nights.iloc[1]["bedtime"] == pytest.approx(24.5)
    assert nights.iloc[1]["waketime"] == pytest.approx(7.5)
    assert nights.iloc[1]["midsleep"] == pytest.approx((24.5 + 31.5) / 2)


def test_bedtime_deviation_and_sd(nights: pd.DataFrame) -> None:
    # latest bedtime 24.0; previous five: 23.5, 24.5, 23.0, 25.0, 25.5 -> median 24.5
    assert sleep.bedtime_dev_min(nights) == pytest.approx(30.0)
    assert sleep.bedtime_sd_min(nights) == pytest.approx(pd.Series([23.5, 24.5, 23, 25, 25.5, 24]).std() * 60)


def test_seven_night_mean_and_debt(nights: pd.DataFrame) -> None:
    assert sleep.sleep_7n(nights) == pytest.approx((400 * 5 + 300) / 6)
    # goal 7.5 h = 450 min: each 400-min night owes 50 min, the 300 owes 150 -> 400 min
    assert sleep.debt_14n_h(nights, 7.5) == pytest.approx(400 / 60)


def test_social_jet_lag_needs_three_of_each(nights: pd.DataFrame) -> None:
    assert sleep.social_jet_lag_h(nights, frozenset({5, 6})) is None
    more = pd.concat([nights, pd.DataFrame([_night("2026-08-30", "2026-08-30T01:00", "2026-08-30T09:00")])])
    more = sleep.add_timing(more.sort_values("night_date"), TZ)
    # free midsleep 29.0 (1:00-9:00), work midsleep ~27.4
    assert sleep.social_jet_lag_h(more, frozenset({5, 6})) == pytest.approx(29.0 - 27.25, abs=0.3)


def test_target_bedtime(nights: pd.DataFrame) -> None:
    # median waketime 7.25 -> 7:15 - 7.5 h - 20 min = 23:25
    assert sleep.target_bedtime(nights, 7.5) == "23:25"


def test_sleep_score_parts() -> None:
    score = sleep.sleep_score(6.0, 1.0, 1.2, 30.0, 2, 7.5)
    assert score["duration"] == pytest.approx(40 * 0.8 + 5 + 4)
    assert score["consistency"] == pytest.approx(30 * (1 - 30 / 90))
    assert score["interruptions"] == pytest.approx(20 * 0.75)
    assert score["total"] == pytest.approx(round(32 + 5 + 4 + 20 + 15, 1))
    assert score["lever"] == "interruptions"
```

- [ ] **Step 2: Run, expect AttributeError**

Run: `uv run pytest tests/test_sleep_timing.py -v`

- [ ] **Step 3: Implement**

Append to `sleep.py`:

```python
def _shifted_clock(stamp: pd.Timestamp, tz: str) -> float:
    local = stamp.tz_convert(tz)
    hours = local.hour + local.minute / 60 + local.second / 3600
    return hours + 24 if hours < 12 else hours


def add_timing(nights: pd.DataFrame, tz: str) -> pd.DataFrame:
    out = nights.copy().sort_values("night_date").reset_index(drop=True)
    out["bedtime"] = [_shifted_clock(s, tz) for s in out["sleep_start"]]
    wake = [_shifted_clock(e, tz) for e in out["sleep_end"]]
    out["waketime"] = [w - 24 if w >= 24 else w for w in wake]
    out["midsleep"] = [(b + (w if w >= b else w + 24)) / 2 for b, w in zip(out["bedtime"], wake, strict=True)]
    return out


def bedtime_dev_min(nights: pd.DataFrame) -> float | None:
    if len(nights) < 2:
        return None
    previous = nights["bedtime"].iloc[-14:-1]
    return float(abs(nights["bedtime"].iloc[-1] - previous.median()) * 60)


def bedtime_sd_min(nights: pd.DataFrame, n: int = 14) -> float | None:
    recent = nights["bedtime"].iloc[-n:]
    return float(recent.std() * 60) if len(recent) >= 2 else None


def sleep_7n(nights: pd.DataFrame) -> float | None:
    recent = nights["asleep_min"].iloc[-7:]
    return float(recent.mean()) if len(recent) else None


def debt_14n_h(nights: pd.DataFrame, goal_h: float) -> float:
    recent = nights["asleep_min"].iloc[-14:]
    return float((goal_h * 60 - recent).clip(lower=0).sum() / 60)


def social_jet_lag_h(nights: pd.DataFrame, free_days: frozenset[int]) -> float | None:
    recent = nights.iloc[-28:]
    is_free = recent["night_date"].map(lambda d: d.weekday() in free_days)
    free, work = recent[is_free]["midsleep"], recent[~is_free]["midsleep"]
    if len(free) < 3 or len(work) < 3:
        return None
    return float(abs(free.median() - work.median()))


def target_bedtime(nights: pd.DataFrame, goal_h: float) -> str | None:
    if nights.empty:
        return None
    wake = float(nights["waketime"].median())
    target = (wake + 24 - goal_h - 20 / 60) % 24
    minutes = int(round(target * 60 / 5) * 5) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def sleep_score(
    asleep_h: float, deep_h: float, rem_h: float, bedtime_dev_min: float | None,
    interruptions: int, goal_h: float,
) -> dict[str, float | str]:
    dur = 40 * min(asleep_h / goal_h, 1) + 5 * min(deep_h / 1.0, 1) + 5 * min(rem_h / 1.5, 1)
    dev = 0.0 if bedtime_dev_min is None else bedtime_dev_min
    cons = 30 * max(0.0, 1 - dev / 90)
    intr = 20 * max(0.0, 1 - interruptions / 8)
    parts = {"duration": dur / 50, "consistency": cons / 30, "interruptions": intr / 20}
    return {
        "duration": dur, "consistency": cons, "interruptions": intr,
        "total": round(dur + cons + intr, 1), "lever": min(parts, key=parts.__getitem__),
    }
```

`lever` is the part with the lowest fraction of its maximum, which is what "lowest part" means when the maxima differ (50/30/20).

- [ ] **Step 4: Run, expect PASS; lint; type-check**

Run: `uv run pytest tests/test_sleep_timing.py tests/test_sleep.py -v && uv run ruff check . && uv run mypy src`

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/sleep.py tests/test_sleep_timing.py
git commit -m "health: sleep timing, debt, social jet lag, target bedtime and score"
```

---

### Task 5: Heart during sleep (§4.3)

**Files:**
- Create: `src/atlas_health/heart.py`, `tests/test_heart.py`

**Interfaces:**
- Produces: `add_sleep_heart(nights, heart_rate, vitals, tz) -> pd.DataFrame` adding per night: `sleep_avg_hr`, `sleep_min_hr`, `hr_min_frac`, `hrv`, `resp`, `rhr` (NaN when absent). `rhr_60d(vitals, as_of) -> float | None`. `hrr_readings(vitals) -> pd.DataFrame` (time, value) of 1-minute recovery samples.

- [ ] **Step 1: Write the failing tests**

`tests/test_heart.py`:

```python
import numpy as np
import pandas as pd
import pytest

from atlas_health import heart

TZ = "America/New_York"


def _hr(start: str, minutes: int, bpm_series: list[float]) -> pd.DataFrame:
    times = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=len(bpm_series), freq=f"{minutes}min")
    return pd.DataFrame({"time": times, "bpm": bpm_series, "bpm_min": bpm_series})


def test_sleep_hr_and_minimum_timing() -> None:
    night = pd.DataFrame([{
        "night_date": pd.Timestamp("2026-09-05").date(),
        "sleep_start": pd.Timestamp("2026-09-05T03:00Z"),
        "sleep_end": pd.Timestamp("2026-09-05T11:00Z"),  # 8 h
    }])
    # 96 samples every 5 min: 60 bpm falling to 40 at the 80% mark, then 55
    bpm = [60 - (i / 76) * 20 for i in range(77)] + [55] * 19
    hr = _hr("2026-09-05T03:00Z", 5, bpm)
    vitals = pd.DataFrame({
        "time": [pd.Timestamp("2026-09-05T05:00Z"), pd.Timestamp("2026-09-05T08:00Z"),
                 pd.Timestamp("2026-09-05T06:00Z"), pd.Timestamp("2026-09-05T15:00Z")],
        "metric_name": ["heart_rate_variability", "heart_rate_variability",
                        "respiratory_rate", "resting_heart_rate"],
        "value": [70.0, 90.0, 16.0, 52.0],
        "units": ["ms", "ms", "breaths/min", "bpm"],
    })
    out = heart.add_sleep_heart(night, hr, vitals, TZ)
    row = out.iloc[0]
    assert row["sleep_avg_hr"] == pytest.approx(np.mean(bpm), abs=0.01)
    assert row["sleep_min_hr"] == pytest.approx(40.0)
    assert 0.7 < row["hr_min_frac"] < 0.9
    assert row["hrv"] == pytest.approx(80.0)
    assert row["resp"] == pytest.approx(16.0)
    assert row["rhr"] == pytest.approx(52.0)


def test_missing_samples_leave_nan() -> None:
    night = pd.DataFrame([{
        "night_date": pd.Timestamp("2026-09-05").date(),
        "sleep_start": pd.Timestamp("2026-09-05T03:00Z"),
        "sleep_end": pd.Timestamp("2026-09-05T11:00Z"),
    }])
    empty_hr = pd.DataFrame({"time": pd.to_datetime([], utc=True), "bpm": [], "bpm_min": []})
    empty_v = pd.DataFrame({"time": pd.to_datetime([], utc=True), "metric_name": [], "value": [], "units": []})
    row = heart.add_sleep_heart(night, empty_hr, empty_v, TZ).iloc[0]
    assert all(np.isnan(row[c]) for c in ("sleep_avg_hr", "sleep_min_hr", "hr_min_frac", "hrv", "resp", "rhr"))


def test_rhr_60d_and_hrr() -> None:
    vitals = pd.DataFrame({
        "time": pd.to_datetime(["2026-09-01T12:00Z", "2026-06-01T12:00Z", "2026-09-05T20:00Z"], utc=True),
        "metric_name": ["resting_heart_rate", "resting_heart_rate", "heart_rate_recovery_one_minute"],
        "value": [50.0, 70.0, 28.0],
        "units": ["bpm", "bpm", "bpm"],
    })
    as_of = pd.Timestamp("2026-09-07T10:00", tz=TZ)
    assert heart.rhr_60d(vitals, as_of) == pytest.approx(50.0)
    hrr = heart.hrr_readings(vitals)
    assert list(hrr["value"]) == [28.0]
```

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Implement**

`src/atlas_health/heart.py`:

```python
"""Heart during sleep (spec §4.3)."""

from __future__ import annotations

import math

import pandas as pd

HRV, RESP, RHR, HRR = (
    "heart_rate_variability", "respiratory_rate", "resting_heart_rate",
    "heart_rate_recovery_one_minute",
)


def _night_window(night_date: object, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """18:00 local the evening before to 12:00 local on night_date."""
    day = pd.Timestamp(str(night_date), tz=tz)
    return (day - pd.Timedelta(hours=6)).tz_convert("UTC"), (day + pd.Timedelta(hours=12)).tz_convert("UTC")


def _hr_min_frac(samples: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    if len(samples) < 12:
        return math.nan
    series = samples.set_index("time")["bpm"].sort_index()
    rolling = series.rolling("5min", min_periods=1).median()
    t_min = rolling.idxmin()
    span = (end - start).total_seconds()
    return float((t_min - start).total_seconds() / span) if span > 0 else math.nan


def add_sleep_heart(
    nights: pd.DataFrame, heart_rate: pd.DataFrame, vitals: pd.DataFrame, tz: str
) -> pd.DataFrame:
    out = nights.copy()
    avg, low, frac, hrv, resp, rhr = [], [], [], [], [], []
    for _, night in out.iterrows():
        start, end = night["sleep_start"], night["sleep_end"]
        hr = heart_rate[(heart_rate["time"] >= start) & (heart_rate["time"] <= end)]
        avg.append(float(hr["bpm"].mean()) if len(hr) else math.nan)
        low.append(float(hr["bpm_min"].min()) if len(hr) else math.nan)
        frac.append(_hr_min_frac(hr, start, end))
        w0, w1 = _night_window(night["night_date"], tz)
        window = vitals[(vitals["time"] >= w0) & (vitals["time"] < w1)]
        hrv_v = window[window["metric_name"] == HRV]["value"]
        resp_v = window[window["metric_name"] == RESP]["value"]
        hrv.append(float(hrv_v.mean()) if len(hrv_v) else math.nan)
        resp.append(float(resp_v.mean()) if len(resp_v) else math.nan)
        day_start = pd.Timestamp(str(night["night_date"]), tz=tz).tz_convert("UTC")
        day = vitals[
            (vitals["metric_name"] == RHR) & (vitals["time"] >= day_start)
            & (vitals["time"] < day_start + pd.Timedelta(days=1))
        ]
        rhr.append(float(day["value"].iloc[-1]) if len(day) else math.nan)
    out["sleep_avg_hr"], out["sleep_min_hr"], out["hr_min_frac"] = avg, low, frac
    out["hrv"], out["resp"], out["rhr"] = hrv, resp, rhr
    return out


def rhr_60d(vitals: pd.DataFrame, as_of: pd.Timestamp) -> float | None:
    cutoff = (as_of - pd.Timedelta(days=60)).tz_convert("UTC")
    rows = vitals[(vitals["metric_name"] == RHR) & (vitals["time"] >= cutoff)]
    return float(rows["value"].mean()) if len(rows) else None


def hrr_readings(vitals: pd.DataFrame) -> pd.DataFrame:
    rows = vitals[vitals["metric_name"] == HRR][["time", "value"]]
    return rows.sort_values("time").reset_index(drop=True)
```

- [ ] **Step 4: Run, expect PASS; lint; type-check**

Run: `uv run pytest tests/test_heart.py -v && uv run ruff check . && uv run mypy src`

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/heart.py tests/test_heart.py
git commit -m "health: sleeping HR, HR-minimum timing, nightly HRV and respiratory rate"
```

---
### Task 6: Exercise: zones, load, consistency, capacity (§4.6, §4.7)

**Files:**
- Create: `src/atlas_health/exercise.py`, `tests/test_exercise.py`

**Interfaces:**
- Produces:
  - `zone_minutes(workout_hr: pd.DataFrame, workout_id: str, edges: list[float]) -> list[float]` five values Z1..Z5.
  - `edwards_trimp(zones: list[float]) -> float`.
  - `enrich_workouts(workouts, workout_hr, running_speed, vitals, config, edges, tz) -> pd.DataFrame`: workouts sorted by `start_time` with added `is_run`, `is_strength`, `valid_run`, `zones` (list), `trimp`, `easy_share`, `pace_min_mi`, `pace_min_km`, `miles`, `ef`, `speed_at_hr`, `hrr_1min`.
  - `weekly_load(workouts, as_of, tz, weeks=8) -> list[dict]` `[{week_start: "YYYY-MM-DD", trimp: float, runs: int, strength: int, miles: float}]` oldest first, current ISO week last.
  - `consistency(workouts, as_of, tz, config) -> dict` keys `days_since_run`, `days_since_strength`, `runs_per_week_4w`, `strength_per_week_4w`, `runs_this_week`, `strength_this_week`, `longest_run_gap_60d`.
  - `easy_share_60d(workouts, as_of) -> float | None`.
  - `load_summary(workouts, as_of, tz) -> dict` keys `load_week`, `load_4w_avg`, `load_7d`, `load_28d`, `acwr` (None unless >= 8 workouts in 28 d), `workouts_28d`.
  - `capacity(workouts, hrr: pd.DataFrame) -> dict` keys `speed_at_hr_latest`, `speed_at_hr_vs_avg_pct`, `speed_at_hr_trend` ("up"/"flat"/"down"/None), `speed_at_hr_n_of_10`, `metric` ("speed_at_hr" or "ef"), `ef_latest`, `ef_vs_avg_pct`, `hrr_latest`, `hrr_mean`, `hrr_sd`, `hrr_z`, `hrr_last4` (list), `capacity_z`, `f1_z`, `durability_28d_min`, `durability_60d_min`.
  - `vdot_estimate(workouts, as_of) -> float | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_exercise.py`:

```python
import pandas as pd
import pytest

from atlas_health import exercise
from atlas_health.config import HealthConfig

TZ = "America/New_York"
EDGES = [141.2, 155.9, 170.6, 185.3]


def _workout(wid: str, start: str, minutes: int, name: str = "Running", distance_m: float = 3200,
             avg_hr: float = 150, max_hr: float = 185, indoor: bool = False) -> dict[str, object]:
    s = pd.Timestamp(start, tz=TZ).tz_convert("UTC")
    return {"id": wid, "name": name, "start_time": s, "end_time": s + pd.Timedelta(minutes=minutes),
            "duration_sec": minutes * 60, "distance_m": distance_m, "avg_hr": avg_hr,
            "max_hr": max_hr, "kcal": 300.0, "is_indoor": indoor}


def _hr_minutes(wid: str, start: str, bpms: list[float]) -> pd.DataFrame:
    times = pd.date_range(pd.Timestamp(start, tz=TZ).tz_convert("UTC"), periods=len(bpms), freq="1min")
    return pd.DataFrame({"time": times, "workout_id": wid, "avg_bpm": bpms})


def test_zone_minutes_and_trimp() -> None:
    hr = _hr_minutes("w1", "2026-09-05T18:00", [120, 150, 160, 175, 190, 190])
    zones = exercise.zone_minutes(hr, "w1", EDGES)
    assert zones == [1, 1, 1, 1, 2]
    assert exercise.edwards_trimp(zones) == 1 + 2 + 3 + 4 + 10


def test_enrich_marks_valid_runs_and_computes_pace_ef_and_speed_at_hr(config: HealthConfig) -> None:
    workouts = pd.DataFrame([
        _workout("w1", "2026-09-05T18:00", 20, distance_m=3218.7, avg_hr=155),   # valid, 2 mi
        _workout("w2", "2026-09-03T18:00", 10),                                    # too short
        _workout("w3", "2026-09-01T18:00", 30, indoor=True),                       # indoor
        _workout("w4", "2026-09-02T18:00", 45, name="Traditional Strength Training", distance_m=0),
    ])
    hr = _hr_minutes("w1", "2026-09-05T18:00", [140] * 5 + [155] * 10 + [165] * 5)
    speed_times = pd.date_range(pd.Timestamp("2026-09-05T18:00", tz=TZ).tz_convert("UTC"), periods=20, freq="1min")
    speed = pd.DataFrame({"time": speed_times, "mps": [2.5] * 5 + [2.8] * 10 + [3.0] * 5})
    vitals = pd.DataFrame({
        "time": [pd.Timestamp("2026-09-05T18:22", tz=TZ).tz_convert("UTC")],
        "metric_name": ["heart_rate_recovery_one_minute"], "value": [28.0], "units": ["bpm"],
    })
    out = exercise.enrich_workouts(workouts, hr, speed, vitals, config, EDGES, TZ)
    w1 = out[out["id"] == "w1"].iloc[0]
    assert bool(w1["valid_run"]) and not bool(out[out["id"] == "w2"].iloc[0]["valid_run"])
    assert not bool(out[out["id"] == "w3"].iloc[0]["valid_run"])
    assert bool(out[out["id"] == "w4"].iloc[0]["is_strength"])
    assert w1["miles"] == pytest.approx(2.0, abs=0.01)
    assert w1["pace_min_mi"] == pytest.approx(10.0, abs=0.05)
    assert w1["ef"] == pytest.approx((3218.7 / 20) / 155)
    assert w1["speed_at_hr"] == pytest.approx(2.8)          # minutes at 155 bpm inside 150-160
    assert w1["easy_share"] == pytest.approx(15 / 20)        # Z1+Z2 = 140 and 155 minutes
    assert w1["hrr_1min"] == pytest.approx(28.0)


def test_consistency_and_weekly_load(config: HealthConfig) -> None:
    as_of = pd.Timestamp("2026-09-07T10:00", tz=TZ)  # Monday
    workouts = pd.DataFrame([
        _workout("r1", "2026-09-05T18:00", 28), _workout("r2", "2026-09-01T18:00", 17),
        _workout("r3", "2026-08-24T18:00", 29),
        _workout("s1", "2026-08-24T10:00", 40, name="Traditional Strength Training", distance_m=0),
    ])
    workouts["trimp"] = [60.0, 50.0, 55.0, 30.0]
    workouts["is_run"] = [True, True, True, False]
    workouts["is_strength"] = [False, False, False, True]
    workouts["valid_run"] = [True, True, True, False]
    workouts["miles"] = [2.4, 2.0, 2.0, 0.0]
    c = exercise.consistency(workouts, as_of, TZ, config)
    assert c["days_since_run"] == 1 and c["days_since_strength"] == 14
    assert c["runs_per_week_4w"] == pytest.approx(3 / 4) and c["runs_this_week"] == 0
    assert c["longest_run_gap_60d"] >= 8
    weeks = exercise.weekly_load(workouts, as_of, TZ, weeks=3)
    assert [w["week_start"] for w in weeks] == ["2026-08-24", "2026-08-31", "2026-09-07"]
    assert weeks[0]["trimp"] == 85.0 and weeks[1]["runs"] == 2
    load = exercise.load_summary(workouts, as_of, TZ)
    assert load["acwr"] is None and load["workouts_28d"] == 4


def test_capacity_trend_and_hrr_z() -> None:
    runs = pd.DataFrame({
        "id": [f"r{i}" for i in range(6)],
        "start_time": pd.date_range("2026-07-01", periods=6, freq="7D", tz="UTC"),
        "valid_run": [True] * 6,
        "duration_sec": [1800] * 6,
        "speed_at_hr": [2.6, 2.65, 2.7, 2.7, 2.75, 2.5],
        "ef": [0.017] * 6,
        "hrr_1min": [30, 32, 33, 31, 35, 20],
    })
    hrr = pd.DataFrame({"time": runs["start_time"], "value": runs["hrr_1min"].astype(float)})
    cap = exercise.capacity(runs, hrr)
    assert cap["metric"] == "speed_at_hr"
    assert cap["speed_at_hr_vs_avg_pct"] == pytest.approx((2.5 / 2.68 - 1) * 100, abs=0.5)
    assert cap["hrr_latest"] == 20 and cap["hrr_last4"] == [33, 31, 35, 20]
    assert cap["hrr_z"] < -1


def test_vdot_from_fastest_3k_plus_run() -> None:
    runs = pd.DataFrame([_workout("a", "2026-09-01T18:00", 17, distance_m=3240)])
    runs["valid_run"] = [True]
    v = exercise.vdot_estimate(runs, pd.Timestamp("2026-09-07T10:00", tz=TZ))
    assert v is not None and 35 < v < 50
```

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Implement**

`src/atlas_health/exercise.py`:

```python
"""Exercise consistency (spec §4.6), intensity, load and running capacity (spec §4.7)."""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd

from atlas_health.config import HealthConfig

METERS_PER_MILE = 1609.344
VALID_RUN_MIN_SEC = 900


def zone_minutes(workout_hr: pd.DataFrame, workout_id: str, edges: list[float]) -> list[float]:
    bpm = workout_hr[workout_hr["workout_id"] == workout_id]["avg_bpm"].dropna()
    zones = [0.0] * 5
    for value in bpm:
        idx = sum(value >= edge for edge in edges)
        zones[idx] += 1
    return zones


def edwards_trimp(zones: list[float]) -> float:
    return float(sum(weight * minutes for weight, minutes in zip((1, 2, 3, 4, 5), zones, strict=True)))


def _speed_at_hr(
    workout: pd.Series, workout_hr: pd.DataFrame, running_speed: pd.DataFrame, low: int, high: int
) -> float:
    hr = workout_hr[workout_hr["workout_id"] == workout["id"]]
    speed = running_speed[
        (running_speed["time"] >= workout["start_time"]) & (running_speed["time"] <= workout["end_time"])
    ]
    if hr.empty or speed.empty:
        return math.nan
    per_minute = speed.set_index("time")["mps"].resample("1min").mean()
    hr_minutes = hr.set_index("time")["avg_bpm"].resample("1min").mean()
    joined = pd.concat([per_minute.rename("mps"), hr_minutes.rename("bpm")], axis=1).dropna()
    keep = joined[(joined["bpm"] >= low) & (joined["bpm"] <= high) & (joined["mps"] > 1.5)]
    return float(keep["mps"].median()) if len(keep) >= 5 else math.nan


def enrich_workouts(
    workouts: pd.DataFrame, workout_hr: pd.DataFrame, running_speed: pd.DataFrame,
    vitals: pd.DataFrame, config: HealthConfig, edges: list[float], tz: str,
) -> pd.DataFrame:
    out = workouts.sort_values("start_time").reset_index(drop=True).copy()
    out["is_run"] = out["name"] == "Running"
    out["is_strength"] = out["name"].isin(config.strength_names)
    out["valid_run"] = out["is_run"] & (~out["is_indoor"].fillna(False).astype(bool)) & (
        out["duration_sec"] >= VALID_RUN_MIN_SEC
    )
    zones = [zone_minutes(workout_hr, wid, edges) for wid in out["id"]]
    out["zones"] = zones
    out["trimp"] = [edwards_trimp(z) for z in zones]
    out["easy_share"] = [(z[0] + z[1]) / sum(z) if sum(z) else math.nan for z in zones]
    minutes = out["duration_sec"] / 60
    out["miles"] = out["distance_m"].fillna(0) / METERS_PER_MILE
    out["pace_min_mi"] = np.where(out["miles"] > 0, minutes / out["miles"].replace(0, np.nan), np.nan)
    out["pace_min_km"] = np.where(
        out["distance_m"] > 0, minutes / (out["distance_m"].replace(0, np.nan) / 1000), np.nan
    )
    out["ef"] = np.where(
        out["valid_run"] & (out["avg_hr"] > 0), (out["distance_m"] / minutes) / out["avg_hr"], np.nan
    )
    out["speed_at_hr"] = [
        _speed_at_hr(w, workout_hr, running_speed, config.fixed_hr_low, config.fixed_hr_high)
        if w["valid_run"] else math.nan
        for _, w in out.iterrows()
    ]
    hrr = vitals[vitals["metric_name"] == "heart_rate_recovery_one_minute"]
    out["hrr_1min"] = [
        float(hrr[(hrr["time"] >= w["end_time"]) & (hrr["time"] <= w["end_time"] + pd.Timedelta(minutes=10))]["value"].iloc[0])
        if len(hrr[(hrr["time"] >= w["end_time"]) & (hrr["time"] <= w["end_time"] + pd.Timedelta(minutes=10))]) else math.nan
        for _, w in out.iterrows()
    ]
    return out


def _week_start(stamp: pd.Timestamp, tz: str) -> pd.Timestamp:
    local = stamp.tz_convert(tz)
    return (local - pd.Timedelta(days=local.weekday())).normalize()


def weekly_load(workouts: pd.DataFrame, as_of: pd.Timestamp, tz: str, weeks: int = 8) -> list[dict[str, object]]:
    this_week = _week_start(as_of, tz)
    starts = [this_week - pd.Timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]
    rows: list[dict[str, object]] = []
    for start in starts:
        end = start + pd.Timedelta(days=7)
        local = workouts["start_time"].dt.tz_convert(tz)
        inside = workouts[(local >= start) & (local < end)]
        rows.append({
            "week_start": start.strftime("%Y-%m-%d"),
            "trimp": float(inside["trimp"].sum()),
            "runs": int(inside["is_run"].sum()),
            "strength": int(inside["is_strength"].sum()),
            "miles": float(inside[inside["is_run"]]["miles"].sum()),
        })
    return rows


def _days_since(stamps: pd.Series, as_of: pd.Timestamp) -> int | None:
    if stamps.empty:
        return None
    return int((as_of - stamps.max()).total_seconds() // 86400)


def consistency(workouts: pd.DataFrame, as_of: pd.Timestamp, tz: str, config: HealthConfig) -> dict[str, object]:
    runs = workouts[workouts["is_run"]]
    strength = workouts[workouts["is_strength"]]
    d28 = as_of - pd.Timedelta(days=28)
    week_start = _week_start(as_of, tz).tz_convert("UTC")
    run_days = runs[runs["start_time"] >= as_of - pd.Timedelta(days=60)]["start_time"].sort_values()
    gaps = run_days.diff().dt.total_seconds().div(86400).dropna()
    longest = int(gaps.max()) if len(gaps) else None
    return {
        "days_since_run": _days_since(runs["start_time"], as_of),
        "days_since_strength": _days_since(strength["start_time"], as_of),
        "runs_per_week_4w": float((runs["start_time"] >= d28).sum() / 4),
        "strength_per_week_4w": float((strength["start_time"] >= d28).sum() / 4),
        "runs_this_week": int((runs["start_time"] >= week_start).sum()),
        "strength_this_week": int((strength["start_time"] >= week_start).sum()),
        "longest_run_gap_60d": longest,
        "runs_per_week_target": config.runs_per_week_target,
        "strength_per_week_target": config.strength_per_week_target,
    }


def easy_share_60d(workouts: pd.DataFrame, as_of: pd.Timestamp) -> float | None:
    runs = workouts[workouts["is_run"] & (workouts["start_time"] >= as_of - pd.Timedelta(days=60))]
    zones = np.array([z for z in runs["zones"] if sum(z)])
    if len(zones) < 3:
        return None
    total = zones.sum()
    return float(zones[:, :2].sum() / total) if total else None


def load_summary(workouts: pd.DataFrame, as_of: pd.Timestamp, tz: str) -> dict[str, object]:
    week_start = _week_start(as_of, tz).tz_convert("UTC")
    d7, d28 = as_of - pd.Timedelta(days=7), as_of - pd.Timedelta(days=28)
    load_week = float(workouts[workouts["start_time"] >= week_start]["trimp"].sum())
    load_7d = float(workouts[workouts["start_time"] >= d7]["trimp"].sum())
    in_28 = workouts[workouts["start_time"] >= d28]
    load_28d = float(in_28["trimp"].sum())
    acwr = load_7d / (load_28d / 4) if len(in_28) >= 8 and load_28d > 0 else None
    return {
        "load_week": load_week, "load_4w_avg": load_28d / 4, "load_7d": load_7d,
        "load_28d": load_28d, "acwr": acwr, "workouts_28d": int(len(in_28)),
    }


def _pct_vs_previous(values: pd.Series) -> float | None:
    values = values.dropna()
    if len(values) < 2:
        return None
    previous = values.iloc[-6:-1]
    return float((values.iloc[-1] / previous.mean() - 1) * 100)


def _trend(values: pd.Series) -> str | None:
    values = values.dropna().iloc[-10:]
    if len(values) < 3:
        return None
    slope = float(np.polyfit(np.arange(len(values)), values.to_numpy(dtype=float), 1)[0])
    per_run_pct = slope / values.mean() * 100
    return "up" if per_run_pct > 1 else "down" if per_run_pct < -1 else "flat"


def capacity(workouts: pd.DataFrame, hrr: pd.DataFrame) -> dict[str, object]:
    runs = workouts[workouts["valid_run"]].sort_values("start_time")
    last10 = runs.iloc[-10:]
    n_speed = int(last10["speed_at_hr"].notna().sum())
    metric = "speed_at_hr" if n_speed >= 4 else "ef"
    series = runs[metric]
    vs_avg = _pct_vs_previous(series)
    pct_history = [
        _pct_vs_previous(series.iloc[: i + 1]) for i in range(len(series))
    ]
    pct_sd = float(np.nanstd([p for p in pct_history[-10:] if p is not None])) if vs_avg is not None else None
    f1_z = (vs_avg / pct_sd) if vs_avg is not None and pct_sd else None
    values = hrr["value"].astype(float)
    hrr_mean = float(values.mean()) if len(values) else None
    hrr_sd = float(values.std()) if len(values) >= 2 else None
    hrr_latest = float(values.iloc[-1]) if len(values) else None
    hrr_z = (hrr_latest - hrr_mean) / hrr_sd if hrr_latest is not None and hrr_mean is not None and hrr_sd else None
    zs = [z for z in (f1_z, hrr_z) if z is not None]
    d28 = runs[runs["start_time"] >= runs["start_time"].max() - timedelta(days=28)] if len(runs) else runs
    d60 = runs[runs["start_time"] >= runs["start_time"].max() - timedelta(days=60)] if len(runs) else runs
    return {
        "metric": metric,
        "speed_at_hr_latest": float(series.dropna().iloc[-1]) if metric == "speed_at_hr" and series.notna().any() else None,
        "speed_at_hr_vs_avg_pct": vs_avg if metric == "speed_at_hr" else None,
        "speed_at_hr_trend": _trend(runs["speed_at_hr"]),
        "speed_at_hr_n_of_10": n_speed,
        "ef_latest": float(runs["ef"].dropna().iloc[-1]) if runs["ef"].notna().any() else None,
        "ef_vs_avg_pct": _pct_vs_previous(runs["ef"]),
        "hrr_latest": hrr_latest, "hrr_mean": hrr_mean, "hrr_sd": hrr_sd, "hrr_z": hrr_z,
        "hrr_last4": [float(v) for v in values.iloc[-4:]],
        "hrr_n": int(len(values)),
        "f1_z": f1_z,
        "capacity_z": float(np.mean(zs)) if len(zs) == 2 else None,
        "durability_28d_min": float(d28["duration_sec"].max() / 60) if len(d28) else None,
        "durability_60d_min": float(d60["duration_sec"].max() / 60) if len(d60) else None,
    }


def vdot_estimate(workouts: pd.DataFrame, as_of: pd.Timestamp) -> float | None:
    runs = workouts[
        workouts["valid_run"] & (workouts["distance_m"] >= 3000)
        & (workouts["start_time"] >= as_of - pd.Timedelta(days=90))
    ]
    if runs.empty:
        return None
    speed = runs["distance_m"] / (runs["duration_sec"] / 60)  # m/min
    best = speed.idxmax()
    v, t = float(speed[best]), float(runs.loc[best, "duration_sec"] / 60)
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v
    pct = 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)
    return round(vo2 / pct, 1)
```

Note for the implementer: the `hrr_1min` list comprehension evaluates the window twice; factor it into a small helper `_hrr_after(hrr, end)` returning `float | nan` if ruff complains about line length.

- [ ] **Step 4: Run, expect PASS; lint; type-check**

Run: `uv run pytest tests/test_exercise.py -v && uv run ruff check . && uv run mypy src`

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/exercise.py tests/test_exercise.py
git commit -m "health: zones, Edwards TRIMP, weekly load, consistency, running capacity, VDOT"
```

---

### Task 7: Weight (§4.5)

**Files:**
- Create: `src/atlas_health/weight.py`, `tests/test_weight.py`

**Interfaces:**
- Produces: `weight_block(vitals, as_of, tz, height_m) -> dict` with keys `readings` (list of `{date, kg}` for 60 days), `last_kg`, `last_date`, `n_7`, `weight_7d`, `weight_28d`, `week_change`, `bmi`, `weigh_ins_7d`, `n_28`, `drift_kg` (= weight_7d - weight_28d or None).

- [ ] **Step 1: Write the failing tests**

`tests/test_weight.py`:

```python
import pandas as pd
import pytest

from atlas_health.weight import weight_block

TZ = "America/New_York"


def _vitals(days: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame({
        "time": pd.to_datetime([f"{d}T11:00Z" for d, _ in days], utc=True),
        "metric_name": "weight_body_mass",
        "value": [v for _, v in days],
        "units": "kg",
    })


def test_no_readings_gives_blank_block() -> None:
    empty = pd.DataFrame({"time": pd.to_datetime([], utc=True), "metric_name": [], "value": [], "units": []})
    block = weight_block(empty, pd.Timestamp("2026-09-07T10:00", tz=TZ), TZ, 1.854)
    assert block["weight_7d"] is None and block["weigh_ins_7d"] == 0 and block["readings"] == []


def test_means_medians_and_bmi() -> None:
    days = [(f"2026-08-{d:02d}", 82.0) for d in range(10, 32)] + [
        ("2026-09-01", 81.0), ("2026-09-02", 81.2), ("2026-09-02", 83.0),  # double weigh-in -> median 82.1
        ("2026-09-04", 81.4), ("2026-09-06", 81.0),
    ]
    block = weight_block(_vitals(days), pd.Timestamp("2026-09-07T10:00", tz=TZ), TZ, 1.854)
    assert block["weigh_ins_7d"] == 4
    assert block["weight_7d"] == pytest.approx((81.0 + 82.1 + 81.4 + 81.0) / 4)
    assert block["weight_28d"] is not None and block["n_28"] >= 8
    assert block["bmi"] == pytest.approx(block["weight_7d"] / 1.854**2, abs=0.01)
    assert block["drift_kg"] == pytest.approx(block["weight_7d"] - block["weight_28d"])


def test_fewer_than_three_readings_shows_last_only() -> None:
    block = weight_block(_vitals([("2026-09-05", 81.5), ("2026-09-06", 81.3)]),
                         pd.Timestamp("2026-09-07T10:00", tz=TZ), TZ, 1.854)
    assert block["weight_7d"] is None and block["last_kg"] == 81.3 and block["n_7"] == 2
```

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Implement**

`src/atlas_health/weight.py`:

```python
"""Weight (spec §4.5): daily medians, 7 and 28 day means, week change, BMI."""

from __future__ import annotations

import pandas as pd


def _daily(vitals: pd.DataFrame, tz: str) -> pd.Series:
    rows = vitals[vitals["metric_name"] == "weight_body_mass"]
    if rows.empty:
        return pd.Series(dtype=float)
    days = rows["time"].dt.tz_convert(tz).dt.date
    return rows.groupby(days)["value"].median().sort_index()


def _mean_over(daily: pd.Series, end: pd.Timestamp, days: int, minimum: int) -> tuple[float | None, int]:
    start = (end - pd.Timedelta(days=days)).date()
    window = daily[(daily.index > start) & (daily.index <= end.date())]
    return (float(window.mean()) if len(window) >= minimum else None), int(len(window))


def weight_block(vitals: pd.DataFrame, as_of: pd.Timestamp, tz: str, height_m: float) -> dict[str, object]:
    daily = _daily(vitals, tz)
    local_now = as_of.tz_convert(tz)
    w7, n7 = _mean_over(daily, local_now, 7, 3)
    w28, n28 = _mean_over(daily, local_now, 28, 8)
    w7_prev, _ = _mean_over(daily, local_now - pd.Timedelta(days=7), 7, 3)
    cutoff = (local_now - pd.Timedelta(days=60)).date()
    readings = [{"date": str(d), "kg": round(float(v), 2)} for d, v in daily.items() if d > cutoff]
    return {
        "readings": readings,
        "last_kg": float(daily.iloc[-1]) if len(daily) else None,
        "last_date": str(daily.index[-1]) if len(daily) else None,
        "n_7": n7,
        "n_28": n28,
        "weight_7d": w7,
        "weight_28d": w28,
        "week_change": (w7 - w7_prev) if w7 is not None and w7_prev is not None else None,
        "bmi": (w7 / height_m**2) if w7 is not None else None,
        "weigh_ins_7d": n7,
        "drift_kg": (w7 - w28) if w7 is not None and w28 is not None else None,
    }
```

- [ ] **Step 4: Run, expect PASS; lint; type-check**

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/weight.py tests/test_weight.py
git commit -m "health: weight block with daily medians and rolling means"
```

---
### Task 8: Deviation engine and the sleep signals (§4.4, sleep table)

**Files:**
- Create: `src/atlas_health/deviations.py`, `tests/test_deviations_sleep.py`

**Interfaces:**
- Produces:
  - `SEVERITY: dict[str, int]` = `{"BLANK": -1, "GOOD": 0, "OK": 1, "WATCH": 2, "ALERT": 3}`.
  - `@dataclass(frozen=True) Signal` with `key, domain, label, state, display, vs_avg, z, arrow` and a `severity` property.
  - `Baseline(mean, sd, n)`, `baseline(values) -> Baseline`, `rolling7(values) -> pd.Series`,
    `zscore(value, base) -> float | None`, `state_from_z(z, favorable, watch, alert) -> str`,
    `worse(a, b) -> str`, `arrow_for(z) -> str`, `blank(key, domain, label) -> Signal`,
    `hm(minutes) -> str`.
  - `sleep_signals(nights: pd.DataFrame, config: HealthConfig) -> list[Signal]` returning S1 to S6 in order.
- Consumes: `sleep.debt_14n_h`, `sleep.bedtime_sd_min` from Task 4. `nights` is the 60-day
  nights-with-data frame, ascending, already through `sleep.add_timing` (Task 4) and
  `heart.add_sleep_heart` (Task 5).

- [ ] **Step 1: Write the failing tests**

`tests/test_deviations_sleep.py`:

```python
import math

import pandas as pd

from atlas_health import deviations as dv
from atlas_health.config import HealthConfig


def _nights(asleep: list[float], bedtimes: list[float], interruptions: int = 2,
            hr_min_frac: float = 0.4) -> pd.DataFrame:
    n = len(asleep)
    days = pd.date_range("2026-07-10", periods=n, freq="D")
    return pd.DataFrame({
        "night_date": [d.date() for d in days],
        "asleep_min": asleep,
        "deep_min": [a * 0.16 for a in asleep],
        "rem_min": [a * 0.22 for a in asleep],
        "core_min": [a * 0.62 for a in asleep],
        "awake_min": [10.0] * n,
        "interruptions": [interruptions] * n,
        "bedtime": bedtimes,
        "waketime": [7.0] * n,
        "midsleep": [(b + 31.0) / 2 for b in bedtimes],
        "hr_min_frac": [hr_min_frac] * n,
        "deep_pct": [0.16] * n,
        "rem_pct": [0.22] * n,
    })


def _by_key(signals: list[dv.Signal]) -> dict[str, dv.Signal]:
    return {s.key: s for s in signals}


def test_state_from_z_respects_the_favorable_direction() -> None:
    assert dv.state_from_z(-1.4, "above", 1.0, 2.0) == "WATCH"
    assert dv.state_from_z(-2.4, "above", 1.0, 2.0) == "ALERT"
    assert dv.state_from_z(1.4, "above", 1.0, 2.0) == "GOOD"
    assert dv.state_from_z(1.4, "below", 1.0, 2.0) == "WATCH"
    assert dv.state_from_z(0.2, "below", 1.0, 2.0) == "OK"
    assert dv.state_from_z(None, "below", 1.0, 2.0) == "BLANK"


def test_too_few_nights_is_blank_not_ok(config: HealthConfig) -> None:
    signals = _by_key(dv.sleep_signals(_nights([400.0] * 4, [24.0] * 4), config))
    assert signals["S1"].state == "BLANK" and signals["S2"].state == "BLANK"
    assert signals["S4"].state == "BLANK"


def test_s1_absolute_rule_beats_the_z_rule(config: HealthConfig) -> None:
    # 20 nights at 400 min, latest 350 (5h50): the z rule alone would not fire,
    # but the absolute "< 6.0 h" rule does (spec §4.4, S1).
    signals = _by_key(dv.sleep_signals(_nights([400.0] * 19 + [350.0], [24.0] * 20), config))
    assert signals["S1"].state == "WATCH"
    assert signals["S1"].display == "5h50"


def test_s1_alerts_on_three_short_nights_in_seven(config: HealthConfig) -> None:
    asleep = [400.0] * 13 + [350.0, 400.0, 340.0, 400.0, 400.0, 400.0, 330.0]
    signals = _by_key(dv.sleep_signals(_nights(asleep, [24.0] * 20), config))
    assert signals["S1"].state == "ALERT"


def test_s2_uses_the_sd_of_seven_night_means_and_the_debt_rule(config: HealthConfig) -> None:
    asleep = [400.0] * 13 + [330.0] * 7          # 7n mean 330, 60d mean about 376
    signals = _by_key(dv.sleep_signals(_nights(asleep, [24.0] * 20), config))
    # debt over the last 14 nights exceeds 7 h, so the rule alerts on its own
    assert signals["S2"].state == "ALERT"
    assert signals["S2"].display == "5h30"
    assert signals["S2"].vs_avg.endswith("h vs avg")


def test_s2_is_good_when_well_above_average(config: HealthConfig) -> None:
    asleep = [360.0] * 13 + [450.0] * 7
    signals = _by_key(dv.sleep_signals(_nights(asleep, [24.0] * 20), config))
    assert signals["S2"].state == "GOOD"


def test_s3_bedtime_regularity_is_absolute(config: HealthConfig) -> None:
    steady = _by_key(dv.sleep_signals(_nights([400.0] * 20, [24.0, 24.2] * 10), config))
    assert steady["S3"].state == "OK"
    wild = _by_key(dv.sleep_signals(_nights([400.0] * 20, [22.5, 26.0] * 10), config))
    assert wild["S3"].state == "ALERT"
    assert wild["S3"].display.endswith(" min")


def test_s4_interruptions_and_s5_architecture(config: HealthConfig) -> None:
    nights = _nights([400.0] * 20, [24.0] * 20)
    nights.loc[nights.index[-1], "interruptions"] = 9
    signals = _by_key(dv.sleep_signals(nights, config))
    assert signals["S4"].state == "ALERT"
    thin = _nights([400.0] * 20, [24.0] * 20)
    thin["deep_pct"] = 0.08
    assert _by_key(dv.sleep_signals(thin, config))["S5"].state == "WATCH"


def test_s6_late_hr_minimum_watches_but_never_alerts(config: HealthConfig) -> None:
    nights = _nights([400.0] * 20, [24.0] * 20, hr_min_frac=0.85)
    signal = _by_key(dv.sleep_signals(nights, config))["S6"]
    assert signal.state == "WATCH"
    nights["hr_min_frac"] = math.nan
    assert _by_key(dv.sleep_signals(nights, config))["S6"].state == "BLANK"


def test_hm_formats_minutes() -> None:
    assert dv.hm(395.0) == "6h35"
    assert dv.hm(None) == "—"
```

- [ ] **Step 2: Run, expect ImportError**

Run: `uv run pytest tests/test_deviations_sleep.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atlas_health.deviations'`.

- [ ] **Step 3: Implement the engine and the sleep signals**

`src/atlas_health/deviations.py`:

```python
"""The deviation system (spec §4.4): every signal compared to the subject's
own average, with a favorable direction and two thresholds.

A signal is never OK for lack of data. BLANK means "not enough nights or runs
to say anything", and §4.8 excludes BLANK signals from the domain state rather
than letting silence read as health.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from atlas_health import sleep as sleep_mod
from atlas_health.config import HealthConfig

SEVERITY = {"BLANK": -1, "GOOD": 0, "OK": 1, "WATCH": 2, "ALERT": 3}


@dataclass(frozen=True)
class Signal:
    key: str
    domain: str
    label: str
    state: str
    display: str = "—"
    vs_avg: str = ""
    z: float | None = None
    arrow: str = "flat"

    @property
    def severity(self) -> int:
        return SEVERITY[self.state]


@dataclass(frozen=True)
class Baseline:
    mean: float | None
    sd: float | None
    n: int


def baseline(values: pd.Series) -> Baseline:
    clean = pd.Series(values).dropna().astype(float)
    if clean.empty:
        return Baseline(None, None, 0)
    sd = float(clean.std()) if len(clean) >= 2 else None
    return Baseline(float(clean.mean()), sd if sd else None, int(len(clean)))


def rolling7(values: pd.Series) -> pd.Series:
    """The rolling 7-night means themselves. Whenever a 7-night mean is
    compared to a baseline the spread is the SD of these, not of single
    nights, which is roughly 2.5x larger (spec §4 conventions)."""
    return pd.Series(values).dropna().astype(float).rolling(7).mean().dropna()


def zscore(value: float | None, base: Baseline) -> float | None:
    if value is None or base.mean is None or not base.sd:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return float((value - base.mean) / base.sd)


def state_from_z(z: float | None, favorable: str, watch: float, alert: float) -> str:
    """`favorable` is "above" or "below": which direction is good. Pass a huge
    `alert` for a signal the spec says never alerts."""
    if z is None or math.isnan(z):
        return "BLANK"
    bad = -z if favorable == "above" else z
    if bad >= alert:
        return "ALERT"
    if bad >= watch:
        return "WATCH"
    if bad <= -watch:
        return "GOOD"
    return "OK"


def worse(first: str, second: str) -> str:
    return first if SEVERITY[first] >= SEVERITY[second] else second


def arrow_for(z: float | None) -> str:
    if z is None or math.isnan(z) or abs(z) < 0.05:
        return "flat"
    return "up" if z > 0 else "down"


def blank(key: str, domain: str, label: str) -> Signal:
    return Signal(key=key, domain=domain, label=label, state="BLANK")


def hm(minutes: float | None) -> str:
    """Minutes as the board writes them: 395 -> "6h35"."""
    if minutes is None or (isinstance(minutes, float) and math.isnan(minutes)):
        return "—"
    total = int(round(minutes))
    return f"{total // 60}h{total % 60:02d}"


def _latest(nights: pd.DataFrame, column: str) -> float | None:
    if nights.empty or column not in nights:
        return None
    value = nights[column].iloc[-1]
    return None if pd.isna(value) else float(value)


def _vs_hours(value: float | None, mean: float | None) -> str:
    if value is None or mean is None:
        return ""
    return f"{(value - mean) / 60:+.1f} h vs avg"


def sleep_signals(nights: pd.DataFrame, config: HealthConfig) -> list[Signal]:
    """S1 to S6 of spec §4.4. `nights` is the 60-day nights-with-data frame."""
    n = len(nights)
    signals: list[Signal] = []

    # S1 latest night, favorable above. Where a relative and an absolute
    # threshold are both given, whichever fires first counts (spec §4.4).
    if n < 7:
        signals.append(blank("S1", "sleep", "sleep last night"))
    else:
        base = baseline(nights["asleep_min"])
        latest = _latest(nights, "asleep_min")
        z = zscore(latest, base)
        state = state_from_z(z, "above", 1.0, 99.0)
        if latest is not None and latest < 6.0 * 60:
            state = worse(state, "WATCH")
        short = int((nights["asleep_min"].iloc[-7:] < 6.0 * 60).sum())
        if short >= 3:
            state = "ALERT"
        signals.append(Signal("S1", "sleep", "sleep last night", state, hm(latest),
                              _vs_hours(latest, base.mean), z, arrow_for(z)))

    # S2 seven-night mean against the SD of seven-night means, plus the debt rule.
    means = rolling7(nights["asleep_min"]) if n else pd.Series(dtype=float)
    if n < 7 or means.empty:
        signals.append(blank("S2", "sleep", "sleep 7n"))
    else:
        base = Baseline(float(nights["asleep_min"].mean()),
                        float(means.std()) if len(means) >= 2 else None, len(means))
        latest = float(means.iloc[-1])
        vs_avg_h = (latest - (base.mean or latest)) / 60
        z = zscore(latest, base)
        state = "OK"
        if vs_avg_h < -0.5:
            state = "WATCH"
        if vs_avg_h > 0.5:
            state = "GOOD"
        debt = sleep_mod.debt_14n_h(nights, config.sleep_goal_h)
        if vs_avg_h < -1.0 or debt > 7.0:
            state = "ALERT"
        signals.append(Signal("S2", "sleep", "sleep 7n", state, hm(latest),
                              f"{vs_avg_h:+.1f} h vs avg", z, arrow_for(vs_avg_h)))

    # S3 bedtime regularity, absolute thresholds, no baseline.
    sd_min = sleep_mod.bedtime_sd_min(nights) if n >= 7 else None
    if sd_min is None:
        signals.append(blank("S3", "sleep", "bedtime regularity"))
    else:
        state = "ALERT" if sd_min > 60 else "WATCH" if sd_min > 45 else "OK"
        signals.append(Signal("S3", "sleep", "bedtime regularity", state,
                              f"{sd_min:.0f} min", "vs 30 target", None,
                              "up" if sd_min > 45 else "flat"))

    # S4 interruptions on the latest night, favorable below.
    if n < 14:
        signals.append(blank("S4", "sleep", "interruptions"))
    else:
        base = baseline(nights["interruptions"].astype(float))
        latest = _latest(nights, "interruptions")
        z = zscore(latest, base)
        mean_text = f"{base.mean:.1f}" if base.mean is not None else "—"
        signals.append(Signal("S4", "sleep", "interruptions",
                              state_from_z(z, "below", 1.0, 2.0),
                              f"{latest:.0f}" if latest is not None else "—",
                              f"avg {mean_text}", z, arrow_for(z)))

    # S5 architecture: absolute bands over the last seven nights, never ALERT.
    if n < 7:
        signals.append(blank("S5", "sleep", "architecture"))
    else:
        deep = float(nights["deep_pct"].iloc[-7:].mean())
        rem = float(nights["rem_pct"].iloc[-7:].mean())
        state = "WATCH" if (deep < 0.10 or rem < 0.15) else "OK"
        signals.append(Signal("S5", "sleep", "architecture", state,
                              f"deep {deep * 100:.0f}% · REM {rem * 100:.0f}%",
                              "bands 13-23% / 20-25%"))

    # S6 HR-minimum timing: a consumer-wearable heuristic, so WATCH only.
    fracs = nights["hr_min_frac"].iloc[-7:].dropna() if "hr_min_frac" in nights else pd.Series(
        dtype=float
    )
    if n < 7 or fracs.empty:
        signals.append(blank("S6", "sleep", "HR-min timing"))
    else:
        late = int((fracs > 0.7).sum())
        state = "WATCH" if late >= 4 else "OK"
        signals.append(Signal("S6", "sleep", "HR-min timing", state,
                              f"{float(fracs.mean()):.2f}", f"{late}/7 late"))
    return signals
```

- [ ] **Step 4: Run, expect PASS; lint; type-check**

Run: `uv run pytest tests/test_deviations_sleep.py -v && uv run ruff check . && uv run mypy src`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/deviations.py tests/test_deviations_sleep.py
git commit -m "health: deviation engine and the six sleep signals"
```

---

### Task 9: Heart signals and the illness composite (§4.4, heart table)

**Files:**
- Modify: `src/atlas_health/deviations.py` (append)
- Test: `tests/test_deviations_heart.py`

**Interfaces:**
- Produces: `heart_signals(nights: pd.DataFrame, config: HealthConfig) -> list[Signal]` returning
  H1 to H5 followed by the composite, key `"HX"`, label `"illness / overreaching"`: `ALERT` when
  two or more of H1, H2, H3 are at WATCH or worse, `OK` otherwise, `BLANK` when fewer than two of
  the three have a state at all.
- Consumes: `Signal`, `Baseline`, `baseline`, `rolling7`, `zscore`, `state_from_z`, `blank`,
  `arrow_for`, `_latest`, `SEVERITY` from Task 8; the `sleep_avg_hr`, `sleep_min_hr`, `hrv`,
  `resp`, `rhr` columns added by `heart.add_sleep_heart` (Task 5).

- [ ] **Step 1: Write the failing tests**

`tests/test_deviations_heart.py`:

```python
import math

import pandas as pd

from atlas_health import deviations as dv
from atlas_health.config import HealthConfig


def _nights(hr: list[float], hrv: list[float], resp: list[float],
            rhr: float | None = 52.0, hr_min: list[float] | None = None) -> pd.DataFrame:
    n = len(hr)
    days = pd.date_range("2026-07-10", periods=n, freq="D")
    return pd.DataFrame({
        "night_date": [d.date() for d in days],
        "sleep_avg_hr": hr,
        "sleep_min_hr": hr_min if hr_min is not None else [v - 6 for v in hr],
        "hrv": hrv,
        "resp": resp,
        "rhr": [math.nan] * (n - 1) + [rhr if rhr is not None else math.nan],
    })


def _by_key(signals: list[dv.Signal]) -> dict[str, dv.Signal]:
    return {s.key: s for s in signals}


def test_fewer_than_fourteen_nights_is_blank(config: HealthConfig) -> None:
    signals = _by_key(dv.heart_signals(_nights([50.0] * 10, [78.0] * 10, [17.0] * 10), config))
    assert signals["H1"].state == "BLANK" and signals["H2"].state == "BLANK"
    assert signals["HX"].state == "BLANK"


def test_h1_rises_with_the_z_score(config: HealthConfig) -> None:
    hr = [48.0, 52.0] * 9 + [57.0]
    signals = _by_key(dv.heart_signals(_nights(hr, [78.0] * 19, [17.0] * 19), config))
    assert signals["H1"].state in {"WATCH", "ALERT"}
    assert signals["H1"].arrow == "up"
    assert signals["H1"].display == "57"


def test_h1_alerts_on_three_consecutive_watch_nights(config: HealthConfig) -> None:
    hr = [48.0, 52.0] * 8 + [54.5, 54.5, 54.5]
    signals = _by_key(dv.heart_signals(_nights(hr, [78.0] * 19, [17.0] * 19), config))
    assert signals["H1"].state == "ALERT"


def test_h2_uses_the_sd_of_seven_night_means(config: HealthConfig) -> None:
    hrv = [80.0, 76.0] * 9 + [40.0] * 8          # the 7n mean collapses at the end
    nights = _nights([50.0] * 26, hrv, [17.0] * 26)
    signals = _by_key(dv.heart_signals(nights, config))
    assert signals["H2"].state == "ALERT"
    assert signals["H2"].arrow == "down"


def test_h3_absolute_twenty_alerts_and_h4_h5_are_present(config: HealthConfig) -> None:
    resp = [16.9] * 19 + [21.0]
    signals = _by_key(dv.heart_signals(_nights([50.0] * 20, [78.0] * 20, resp, rhr=71.0), config))
    assert signals["H3"].state == "ALERT"
    assert signals["H4"].state == "ALERT"
    assert signals["H5"].state in {"OK", "WATCH", "GOOD"}


def test_h4_is_blank_when_apple_recorded_no_resting_hr(config: HealthConfig) -> None:
    signals = _by_key(dv.heart_signals(
        _nights([50.0] * 20, [78.0] * 20, [17.0] * 20, rhr=None), config))
    assert signals["H4"].state == "BLANK"


def test_composite_fires_when_two_of_three_are_watching(config: HealthConfig) -> None:
    hr = [48.0, 52.0] * 9 + [56.0]
    hrv = [80.0, 76.0] * 9 + [50.0]
    signals = _by_key(dv.heart_signals(_nights(hr, hrv, [17.0] * 19), config))
    watching = sum(signals[k].severity >= dv.SEVERITY["WATCH"] for k in ("H1", "H2", "H3"))
    assert (signals["HX"].state == "ALERT") == (watching >= 2)
```

- [ ] **Step 2: Run, expect AttributeError**

Run: `uv run pytest tests/test_deviations_heart.py -v`
Expected: FAIL with `AttributeError: module 'atlas_health.deviations' has no attribute 'heart_signals'`.

- [ ] **Step 3: Implement**

Append to `deviations.py`:

```python
def heart_signals(nights: pd.DataFrame, config: HealthConfig) -> list[Signal]:
    """H1 to H5 and the illness / overreaching composite (spec §4.4)."""
    n = len(nights)
    signals: list[Signal] = []

    # H1 sleeping HR, favorable below, with the three-consecutive-nights rule.
    if n < 14:
        signals.append(blank("H1", "heart", "sleeping HR"))
    else:
        base = baseline(nights["sleep_avg_hr"])
        latest = _latest(nights, "sleep_avg_hr")
        z = zscore(latest, base)
        state = state_from_z(z, "below", 1.0, 2.0)
        recent = [zscore(value, base) for value in nights["sleep_avg_hr"].iloc[-3:]]
        if len(recent) == 3 and all(item is not None and item > 1 for item in recent):
            state = "ALERT"
        delta = "" if latest is None or base.mean is None else f"{latest - base.mean:+.1f} bpm"
        z_text = "" if z is None else f" (z {z:+.1f})"
        signals.append(Signal("H1", "heart", "sleeping HR", state,
                              f"{latest:.0f}" if latest is not None else "—",
                              f"{delta}{z_text}", z, arrow_for(z)))

    # H2 HRV seven-night mean against the SD of seven-night means.
    means = rolling7(nights["hrv"]) if "hrv" in nights else pd.Series(dtype=float)
    if n < 14 or means.empty:
        signals.append(blank("H2", "heart", "HRV 7n"))
    else:
        base = Baseline(float(pd.Series(nights["hrv"]).dropna().mean()),
                        float(means.std()) if len(means) >= 2 else None, len(means))
        latest = float(means.iloc[-1])
        z = zscore(latest, base)
        delta = "" if base.mean is None else f"{latest - base.mean:+.0f} ms"
        z_text = "" if z is None else f" (z7 {z:+.1f})"
        signals.append(Signal("H2", "heart", "HRV 7n", state_from_z(z, "above", 1.0, 2.0),
                              f"{latest:.0f} ms", f"{delta}{z_text}", z, arrow_for(z)))

    # H3 respiratory rate, favorable below; above 20 alerts on the value alone.
    if n < 14:
        signals.append(blank("H3", "heart", "respiratory rate"))
    else:
        base = baseline(nights["resp"])
        latest = _latest(nights, "resp")
        z = zscore(latest, base)
        state = state_from_z(z, "below", 1.0, 2.0)
        if latest is not None and latest > 20:
            state = "ALERT"
        delta = "" if latest is None or base.mean is None else f"{latest - base.mean:+.1f} /min"
        signals.append(Signal("H3", "heart", "respiratory rate", state,
                              f"{latest:.1f}" if latest is not None else "—",
                              delta, z, arrow_for(z)))

    # H4 Apple's daily resting HR: absolute, and blank on the days it is absent.
    rhr = _latest(nights, "rhr")
    if rhr is None:
        signals.append(blank("H4", "heart", "resting HR"))
    else:
        state = "ALERT" if rhr >= 70 else "WATCH" if rhr >= 62 else "OK"
        signals.append(Signal("H4", "heart", "resting HR", state, f"{rhr:.0f}",
                              "watch days only", None, "flat"))

    # H5 nightly HR minimum, favorable below, WATCH at z > 2 and never ALERT.
    if n < 14:
        signals.append(blank("H5", "heart", "HR minimum"))
    else:
        base = baseline(nights["sleep_min_hr"])
        latest = _latest(nights, "sleep_min_hr")
        z = zscore(latest, base)
        mean_text = f"avg {base.mean:.0f}" if base.mean is not None else ""
        signals.append(Signal("H5", "heart", "HR minimum", state_from_z(z, "below", 2.0, 99.0),
                              f"{latest:.0f}" if latest is not None else "—",
                              mean_text, z, arrow_for(z)))

    # The composite outranks everything (spec §4.4, §4.8.2).
    known = [s for s in signals if s.key in ("H1", "H2", "H3") and s.state != "BLANK"]
    watching = sum(1 for s in known if s.severity >= SEVERITY["WATCH"])
    if len(known) < 2:
        signals.append(blank("HX", "heart", "illness / overreaching"))
    else:
        signals.append(Signal(
            "HX", "heart", "illness / overreaching",
            "ALERT" if watching >= 2 else "OK",
            f"{watching}/3 elevated",
            "sleeping HR, HRV and respiratory rate together",
        ))
    return signals
```

- [ ] **Step 4: Run, expect PASS; lint; type-check**

Run: `uv run pytest tests/test_deviations_heart.py -v && uv run ruff check . && uv run mypy src`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/deviations.py tests/test_deviations_heart.py
git commit -m "health: heart signals and the illness/overreaching composite"
```

---

### Task 10: Fitness and weight signals (§4.4, fitness and weight tables)

**Files:**
- Modify: `src/atlas_health/deviations.py` (append)
- Test: `tests/test_deviations_fitness.py`

**Interfaces:**
- Produces:
  - `fitness_signals(capacity: dict[str, object], consistency: dict[str, object], load: dict[str, object], easy_share: float | None, config: HealthConfig) -> list[Signal]` returning F1 to F7.
  - `weight_signals(block: dict[str, object]) -> list[Signal]` returning W1, W2.
- Consumes: the dicts returned by `exercise.capacity`, `exercise.consistency`,
  `exercise.load_summary`, `exercise.easy_share_60d` (Task 6) and `weight.weight_block` (Task 7),
  by the exact keys those tasks define.

- [ ] **Step 1: Write the failing tests**

`tests/test_deviations_fitness.py`:

```python
from atlas_health import deviations as dv
from atlas_health.config import HealthConfig


def _capacity(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "metric": "speed_at_hr", "speed_at_hr_latest": 2.9, "speed_at_hr_vs_avg_pct": 1.0,
        "speed_at_hr_trend": "flat", "speed_at_hr_n_of_10": 6, "ef_latest": 0.017,
        "ef_vs_avg_pct": 0.5, "hrr_latest": 28.0, "hrr_mean": 32.7, "hrr_sd": 7.0,
        "hrr_z": -0.67, "hrr_last4": [16.0, 36.0, 30.0, 28.0], "hrr_n": 24, "f1_z": 0.3,
        "capacity_z": -0.2, "durability_28d_min": 28.0, "durability_60d_min": 36.0,
    }
    base.update(over)
    return base


def _consistency(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "days_since_run": 2, "days_since_strength": 3, "runs_per_week_4w": 0.75,
        "strength_per_week_4w": 0.5, "runs_this_week": 1, "strength_this_week": 0,
        "longest_run_gap_60d": 21, "runs_per_week_target": 3, "strength_per_week_target": 2,
    }
    base.update(over)
    return base


def _load(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "load_week": 180.0, "load_4w_avg": 95.0, "load_7d": 180.0, "load_28d": 380.0,
        "acwr": None, "workouts_28d": 4,
    }
    base.update(over)
    return base


def _by_key(signals: list[dv.Signal]) -> dict[str, dv.Signal]:
    return {s.key: s for s in signals}


def test_all_green_case(config: HealthConfig) -> None:
    signals = _by_key(dv.fitness_signals(_capacity(), _consistency(), _load(), 0.72, config))
    assert signals["F1"].state == "OK"
    assert signals["F4"].state == "OK" and signals["F5"].state == "OK"
    assert signals["F6"].state == "OK"


def test_f1_watch_and_alert_on_percent_decline(config: HealthConfig) -> None:
    watch = _by_key(dv.fitness_signals(
        _capacity(speed_at_hr_vs_avg_pct=-4.0), _consistency(), _load(), 0.72, config))
    assert watch["F1"].state == "WATCH" and watch["F1"].display == "2.9 m/s"
    alert = _by_key(dv.fitness_signals(
        _capacity(speed_at_hr_vs_avg_pct=-7.0), _consistency(), _load(), 0.72, config))
    assert alert["F1"].state == "ALERT"


def test_f1_falls_back_to_the_efficiency_factor_and_says_so(config: HealthConfig) -> None:
    signals = _by_key(dv.fitness_signals(
        _capacity(metric="ef", speed_at_hr_n_of_10=1, ef_vs_avg_pct=-8.0),
        _consistency(), _load(), 0.72, config))
    assert signals["F1"].state == "ALERT"
    assert "EF" in signals["F1"].label


def test_f1_is_blank_with_too_few_runs(config: HealthConfig) -> None:
    signals = _by_key(dv.fitness_signals(
        _capacity(speed_at_hr_vs_avg_pct=None, speed_at_hr_n_of_10=1, ef_vs_avg_pct=None),
        _consistency(), _load(), 0.72, config))
    assert signals["F1"].state == "BLANK"


def test_f2_hrr_thresholds(config: HealthConfig) -> None:
    low = _by_key(dv.fitness_signals(
        _capacity(hrr_latest=18.0, hrr_z=-2.1), _consistency(), _load(), 0.72, config))
    assert low["F2"].state == "WATCH"
    worst = _by_key(dv.fitness_signals(
        _capacity(hrr_latest=14.0, hrr_z=-2.7), _consistency(), _load(), 0.72, config))
    assert worst["F2"].state == "ALERT"
    thin = _by_key(dv.fitness_signals(_capacity(hrr_n=3), _consistency(), _load(), 0.72, config))
    assert thin["F2"].state == "BLANK"


def test_f3_load_spike_is_blank_below_eight_workouts(config: HealthConfig) -> None:
    thin = _by_key(dv.fitness_signals(_capacity(), _consistency(), _load(), 0.72, config))
    assert thin["F3"].state == "BLANK"
    spike = _by_key(dv.fitness_signals(
        _capacity(), _consistency(),
        _load(workouts_28d=10, load_week=300.0, load_4w_avg=100.0), 0.72, config))
    assert spike["F3"].state == "ALERT"


def test_f4_and_f5_consistency_thresholds(config: HealthConfig) -> None:
    # target 3 runs/week -> WATCH above 7/3 + 1 = 3.33 days, ALERT above 7
    watch = _by_key(dv.fitness_signals(
        _capacity(), _consistency(days_since_run=4), _load(), 0.72, config))
    assert watch["F4"].state == "WATCH"
    alert = _by_key(dv.fitness_signals(
        _capacity(), _consistency(days_since_run=9, days_since_strength=12), _load(), 0.72, config))
    assert alert["F4"].state == "ALERT" and alert["F5"].state == "ALERT"


def test_f6_easy_share_never_alerts_and_f7_capacity_index(config: HealthConfig) -> None:
    signals = _by_key(dv.fitness_signals(
        _capacity(capacity_z=-2.4), _consistency(), _load(), 0.10, config))
    assert signals["F6"].state == "WATCH" and signals["F6"].display == "10 %"
    assert signals["F7"].state == "ALERT"
    missing = _by_key(dv.fitness_signals(
        _capacity(capacity_z=None), _consistency(), _load(), None, config))
    assert missing["F7"].state == "BLANK" and missing["F6"].state == "BLANK"


def test_weight_signals() -> None:
    none_yet = _by_key(dv.weight_signals({
        "weight_7d": None, "weight_28d": None, "drift_kg": None, "week_change": None,
        "weigh_ins_7d": 0, "n_7": 0, "n_28": 0, "bmi": None, "last_kg": None,
    }))
    assert none_yet["W1"].state == "BLANK" and none_yet["W2"].state == "WATCH"
    drifting = _by_key(dv.weight_signals({
        "weight_7d": 84.0, "weight_28d": 81.5, "drift_kg": 2.5, "week_change": 0.6,
        "weigh_ins_7d": 6, "n_7": 6, "n_28": 20, "bmi": 24.4, "last_kg": 84.1,
    }))
    assert drifting["W1"].state == "ALERT" and drifting["W2"].state == "OK"
    assert drifting["W1"].display == "84.0 kg"
```

- [ ] **Step 2: Run, expect AttributeError**

Run: `uv run pytest tests/test_deviations_fitness.py -v`

- [ ] **Step 3: Implement**

Append to `deviations.py`:

```python
def _num(value: object) -> float | None:
    """Values arrive from dicts built by pandas code, so None, NaN and numpy
    scalars all turn up; everything downstream wants a plain float or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return None if math.isnan(float(value)) else float(value)
    return None


def fitness_signals(
    capacity: dict[str, object], consistency: dict[str, object], load: dict[str, object],
    easy_share: float | None, config: HealthConfig,
) -> list[Signal]:
    """F1 to F7 of spec §4.4, from the dicts Task 6 produces."""
    signals: list[Signal] = []

    # F1 capacity: speed at the fixed HR band, or the efficiency factor when
    # fewer than 4 of the last 10 valid runs carry a speed value (spec §4.7).
    uses_speed = str(capacity.get("metric")) == "speed_at_hr"
    pct = _num(capacity.get("speed_at_hr_vs_avg_pct" if uses_speed else "ef_vs_avg_pct"))
    label = (
        f"speed @{config.fixed_hr_low}-{config.fixed_hr_high}"
        if uses_speed
        else "EF (no speed samples)"
    )
    if pct is None:
        signals.append(blank("F1", "fitness", label))
    else:
        state = "ALERT" if pct < -6 else "WATCH" if pct < -3 else "GOOD" if pct > 3 else "OK"
        if uses_speed:
            latest = _num(capacity.get("speed_at_hr_latest"))
            display = f"{latest:.1f} m/s" if latest is not None else "—"
        else:
            latest = _num(capacity.get("ef_latest"))
            display = f"{latest:.4f}" if latest is not None else "—"
        signals.append(Signal("F1", "fitness", label, state, display,
                              f"{pct:+.0f} % vs prev 5", _num(capacity.get("f1_z")),
                              "up" if pct > 0 else "down"))

    # F2 one-minute heart rate recovery against the personal baseline.
    hrr = _num(capacity.get("hrr_latest"))
    hrr_n = _num(capacity.get("hrr_n")) or 0
    hrr_z = _num(capacity.get("hrr_z"))
    if hrr is None or hrr_n < 5:
        signals.append(blank("F2", "fitness", "HR recovery"))
    else:
        state = "OK"
        if hrr < 20 or (hrr_z is not None and hrr_z < -1):
            state = "WATCH"
        if hrr < 15:
            state = "ALERT"
        if state == "OK" and hrr_z is not None and hrr_z > 1:
            state = "GOOD"
        mean = _num(capacity.get("hrr_mean"))
        mean_text = f"avg {mean:.1f}" if mean is not None else ""
        signals.append(Signal("F2", "fitness", "HR recovery", state, f"{hrr:.0f}",
                              mean_text, hrr_z, arrow_for(hrr_z)))

    # F3 load spike: only meaningful with 8 workouts in 28 days (spec §4.7 guard).
    week = _num(load.get("load_week"))
    avg4 = _num(load.get("load_4w_avg"))
    workouts_28d = _num(load.get("workouts_28d")) or 0
    if week is None or not avg4 or workouts_28d < 8:
        signals.append(blank("F3", "fitness", "load spike"))
    else:
        ratio = week / avg4
        state = "ALERT" if ratio > 2.0 else "WATCH" if ratio > 1.5 else "OK"
        signals.append(Signal("F3", "fitness", "load spike", state, f"{ratio:.1f}x",
                              f"week {week:.0f} vs 4-wk {avg4:.0f}", None,
                              "up" if ratio > 1 else "down"))

    # F4 / F5 consistency against the weekly targets.
    signals.append(_consistency_signal(
        "F4", "run consistency", _num(consistency.get("days_since_run")),
        config.runs_per_week_target, alert_days=7.0))
    signals.append(_consistency_signal(
        "F5", "strength consistency", _num(consistency.get("days_since_strength")),
        config.strength_per_week_target, alert_days=10.0))

    # F6 easy share: shape, not safety, so it never alerts.
    if easy_share is None:
        signals.append(blank("F6", "fitness", "easy share"))
    else:
        state = "WATCH" if easy_share < 0.50 else "OK"
        target = config.easy_share_target
        signals.append(Signal("F6", "fitness", "easy share", state,
                              f"{easy_share * 100:.0f} %", f"vs {target * 100:.0f} % target",
                              None, "down" if easy_share < target else "up"))

    # F7 capacity index: the one number behind the Fitness chip.
    cap_z = _num(capacity.get("capacity_z"))
    if cap_z is None:
        signals.append(blank("F7", "fitness", "capacity"))
    else:
        signals.append(Signal("F7", "fitness", "capacity",
                              state_from_z(cap_z, "above", 1.0, 2.0), f"z {cap_z:+.1f}",
                              "speed@HR and HR recovery", cap_z, arrow_for(cap_z)))
    return signals


def _consistency_signal(
    key: str, label: str, days_since: float | None, per_week_target: int, alert_days: float
) -> Signal:
    if days_since is None:
        return blank(key, "fitness", label)
    watch_days = 7 / per_week_target + 1
    state = "ALERT" if days_since > alert_days else "WATCH" if days_since > watch_days else "OK"
    return Signal(key, "fitness", label, state, f"{days_since:.0f} d",
                  f"target {per_week_target}/wk", None, "up" if state != "OK" else "flat")


def weight_signals(block: dict[str, object]) -> list[Signal]:
    """W1 drift and W2 weigh-in coverage (spec §4.4, §4.5)."""
    drift = _num(block.get("drift_kg"))
    seven = _num(block.get("weight_7d"))
    weigh_ins = int(_num(block.get("weigh_ins_7d")) or 0)
    if drift is None or seven is None:
        w1 = blank("W1", "weight", "weight drift")
    else:
        size = abs(drift)
        state = "ALERT" if size > 2.0 else "WATCH" if size > 1.0 else "OK"
        w1 = Signal("W1", "weight", "weight drift", state, f"{seven:.1f} kg",
                    f"{drift:+.1f} kg vs 28-day", None, "up" if drift > 0 else "down")
    w2 = Signal("W2", "weight", "weigh-ins", "WATCH" if weigh_ins < 3 else "OK",
                f"{weigh_ins}/7", "3 needed for a 7-day mean", None, "flat")
    return [w1, w2]
```

- [ ] **Step 4: Run, expect PASS; lint; type-check**

Run: `uv run pytest tests/test_deviations_fitness.py -v && uv run ruff check . && uv run mypy src`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/deviations.py tests/test_deviations_fitness.py
git commit -m "health: fitness and weight signals"
```

---

### Task 11: STATUS compilation, deviation list and the action line (§4.8, §4.9)

**Files:**
- Modify: `src/atlas_health/deviations.py` (append)
- Test: `tests/test_status.py`

**Interfaces:**
- Produces:
  - `DOMAINS = ("sleep", "heart", "fitness", "weight")`, `NO_DATA = "NO DATA"`.
  - `domain_state(signals, domain) -> str` — worst non-BLANK state; `NO_DATA` when all blank.
  - `overall_state(signals) -> str` — worst domain state; `NO_DATA` only when every domain is.
  - `drivers(signals, limit=2) -> list[Signal]` — highest severity first, ties by `|z|` then key;
    GOOD signals only when nothing is at WATCH or worse.
  - `deviation_rows(signals, limit=4) -> list[dict[str, str]]` with keys
    `label, value, vs_avg, arrow, state`; at most one GOOD row, placed last.
  - `@dataclass(frozen=True) ActionContext` with fields `target_bedtime: str | None`,
    `debt_14n_h: float`, `bedtime_sd_min: float | None`, `z2_top: float`,
    `easy_share_60d: float | None`, `days_since_run: int | None`, `days_since_strength: int | None`,
    `weight_7d: float | None`, `weight_drift_kg: float | None`, `fixed_hr_band: str`,
    `speed_decline_pct: float | None`, `sleep_hr_delta: float | None`, `hrv_pct: float | None`,
    `next_due: str`.
  - `action_line(signals, context) -> str` — the first matching rule of the §4.9 table.
  - `status_block(signals) -> dict[str, object]` with keys `overall`, `domains`, `drivers`.

- [ ] **Step 1: Write the failing tests**

`tests/test_status.py`:

```python
from atlas_health import deviations as dv


def _s(key: str, domain: str, state: str, z: float | None = None, label: str = "x") -> dv.Signal:
    return dv.Signal(key, domain, label, state, "v", "vs avg", z, "up")


def test_domain_state_ignores_blanks_and_reports_no_data() -> None:
    signals = [_s("S1", "sleep", "BLANK"), _s("S2", "sleep", "WATCH"), _s("W1", "weight", "BLANK")]
    assert dv.domain_state(signals, "sleep") == "WATCH"
    assert dv.domain_state(signals, "weight") == "NO DATA"
    assert dv.domain_state(signals, "heart") == "NO DATA"


def test_overall_is_the_worst_domain() -> None:
    signals = [_s("S1", "sleep", "WATCH"), _s("H1", "heart", "OK"), _s("F1", "fitness", "ALERT")]
    assert dv.overall_state(signals) == "ALERT"


def test_drivers_prefer_severity_then_absolute_z() -> None:
    signals = [
        _s("S1", "sleep", "WATCH", z=-1.1, label="sleep 7n"),
        _s("H1", "heart", "ALERT", z=2.4, label="sleeping HR"),
        _s("F6", "fitness", "WATCH", z=None, label="easy share"),
        _s("H2", "heart", "GOOD", z=1.6, label="HRV 7n"),
    ]
    names = [s.label for s in dv.drivers(signals)]
    assert names[0] == "sleeping HR"
    assert "HRV 7n" not in names


def test_drivers_fall_back_to_good_when_nothing_is_wrong() -> None:
    signals = [_s("S1", "sleep", "OK"), _s("H2", "heart", "GOOD", z=1.6, label="HRV 7n")]
    assert [s.label for s in dv.drivers(signals)] == ["HRV 7n"]


def test_deviation_rows_are_capped_and_carry_one_good_last() -> None:
    signals = [
        _s("S3", "sleep", "ALERT", z=None, label="bedtime regularity"),
        _s("F6", "fitness", "WATCH", z=None, label="easy share"),
        _s("S2", "sleep", "OK", z=-0.2, label="sleep 7n"),
        _s("H2", "heart", "GOOD", z=1.2, label="HRV 7n"),
        _s("H3", "heart", "GOOD", z=1.1, label="respiratory rate"),
        _s("H5", "heart", "OK", z=0.1, label="HR minimum"),
    ]
    rows = dv.deviation_rows(signals, limit=4)
    assert len(rows) == 4
    assert rows[0]["label"] == "bedtime regularity" and rows[0]["state"] == "ALERT"
    assert rows[-1]["state"] == "GOOD"
    assert sum(row["state"] == "GOOD" for row in rows) == 1


def _context(**over: object) -> dv.ActionContext:
    base: dict[str, object] = {
        "target_bedtime": "23:50", "debt_14n_h": 4.2, "bedtime_sd_min": 77.0, "z2_top": 156.0,
        "easy_share_60d": 0.10, "days_since_run": 2, "days_since_strength": 3,
        "weight_7d": None, "weight_drift_kg": None, "fixed_hr_band": "150-160",
        "speed_decline_pct": -7.0, "sleep_hr_delta": 4.0, "hrv_pct": -18.0, "next_due": "run",
    }
    base.update(over)
    return dv.ActionContext(**base)  # type: ignore[arg-type]


def test_action_line_priority_one_is_the_composite() -> None:
    assert dv.action_line([_s("HX", "heart", "ALERT")], _context()).startswith("Rest today.")


def test_action_line_priority_three_is_bedtime_regularity() -> None:
    line = dv.action_line([_s("S3", "sleep", "ALERT")], _context())
    assert line == "Bed by 23:50 tonight. Bedtime varies +/-77 min; aim within 30."


def test_action_line_priority_five_asks_for_a_run() -> None:
    line = dv.action_line([_s("F4", "fitness", "WATCH")], _context(days_since_run=5))
    assert line == "Run today, easy (HR under 156). 5 days since last run."


def test_action_line_falls_through_to_on_track() -> None:
    assert dv.action_line([_s("S1", "sleep", "OK")], _context()) == "On track. Next: run."


def test_status_block_shape() -> None:
    signals = [
        _s("S3", "sleep", "ALERT", label="bedtime regularity"),
        _s("H1", "heart", "OK"), _s("F6", "fitness", "WATCH", label="easy share"),
        _s("W1", "weight", "BLANK"),
    ]
    block = dv.status_block(signals)
    assert block["overall"] == "ALERT"
    assert block["domains"] == {
        "sleep": "ALERT", "heart": "OK", "fitness": "WATCH", "weight": "NO DATA"
    }
    assert str(block["drivers"][0]).startswith("bedtime regularity")
```

- [ ] **Step 2: Run, expect AttributeError**

Run: `uv run pytest tests/test_status.py -v`

- [ ] **Step 3: Implement**

Append to `deviations.py`:

```python
DOMAINS = ("sleep", "heart", "fitness", "weight")
NO_DATA = "NO DATA"


def domain_state(signals: list[Signal], domain: str) -> str:
    """Worst state in the domain. A blank signal does not count; a domain in
    which every signal is blank says NO DATA, never OK (spec §4.8.1)."""
    known = [s for s in signals if s.domain == domain and s.state != "BLANK"]
    if not known:
        return NO_DATA
    return max(known, key=lambda s: s.severity).state


def overall_state(signals: list[Signal]) -> str:
    states = [domain_state(signals, domain) for domain in DOMAINS]
    real = [state for state in states if state != NO_DATA]
    if not real:
        return NO_DATA
    return max(real, key=lambda state: SEVERITY[state])


def _rank(signal: Signal) -> tuple[int, float, str]:
    return (-signal.severity, -abs(signal.z or 0.0), signal.key)


def drivers(signals: list[Signal], limit: int = 2) -> list[Signal]:
    bad = sorted((s for s in signals if s.severity >= SEVERITY["WATCH"]), key=_rank)
    if bad:
        return bad[:limit]
    return sorted((s for s in signals if s.state == "GOOD"), key=_rank)[:limit]


def deviation_rows(signals: list[Signal], limit: int = 4) -> list[dict[str, str]]:
    ranked = sorted((s for s in signals if s.state != "BLANK"), key=_rank)
    kept: list[Signal] = []
    good_used = False
    for signal in ranked:
        if signal.state == "GOOD":
            if good_used:
                continue
            good_used = True
        kept.append(signal)
        if len(kept) == limit:
            break
    # At most one GOOD line, and it sits last (spec §4.8.5).
    kept.sort(key=lambda s: (s.state == "GOOD", _rank(s)))
    return [
        {"label": s.label, "value": s.display, "vs_avg": s.vs_avg, "arrow": s.arrow,
         "state": s.state}
        for s in kept
    ]


@dataclass(frozen=True)
class ActionContext:
    target_bedtime: str | None
    debt_14n_h: float
    bedtime_sd_min: float | None
    z2_top: float
    easy_share_60d: float | None
    days_since_run: int | None
    days_since_strength: int | None
    weight_7d: float | None
    weight_drift_kg: float | None
    fixed_hr_band: str
    speed_decline_pct: float | None
    sleep_hr_delta: float | None
    hrv_pct: float | None
    next_due: str


def _state_of(signals: list[Signal], key: str) -> str:
    for signal in signals:
        if signal.key == key:
            return signal.state
    return "BLANK"


def action_line(signals: list[Signal], context: ActionContext) -> str:
    """Exactly one line, by the first matching rule of spec §4.9. Later
    matches are not lost: they are already in the deviation list."""
    state = {
        key: _state_of(signals, key)
        for key in ("HX", "S1", "S2", "S3", "F1", "F4", "F5", "F6", "F7", "W1")
    }
    bedtime = context.target_bedtime or "23:30"
    z2 = f"{context.z2_top:.0f}"

    if state["HX"] == "ALERT":
        hr = f"+{context.sleep_hr_delta:.1f}" if context.sleep_hr_delta is not None else "+0.0"
        hrv = f"{abs(context.hrv_pct):.0f}" if context.hrv_pct is not None else "0"
        return f"Rest today. Sleeping HR {hr} bpm, HRV -{hrv} % vs your average."
    if "ALERT" in (state["S1"], state["S2"]):
        return f"Bed by {bedtime} tonight. Debt {context.debt_14n_h:.1f} h over 14 nights."
    if state["S3"] == "ALERT":
        sd = f"{context.bedtime_sd_min:.0f}" if context.bedtime_sd_min is not None else "0"
        return f"Bed by {bedtime} tonight. Bedtime varies +/-{sd} min; aim within 30."
    if "ALERT" in (state["F1"], state["F7"]):
        drop = f"{abs(context.speed_decline_pct):.0f}" if context.speed_decline_pct else "0"
        return (f"Back off: speed at {context.fixed_hr_band} down {drop} % over 5 runs. "
                f"Next run easy, under {z2}.")
    if state["F4"] in ("WATCH", "ALERT"):
        days = context.days_since_run if context.days_since_run is not None else 0
        return f"Run today, easy (HR under {z2}). {days} days since last run."
    if state["F5"] in ("WATCH", "ALERT"):
        days = context.days_since_strength if context.days_since_strength is not None else 0
        return f"Lift today. {days} days since last session."
    if state["F6"] == "WATCH" and context.easy_share_60d is not None:
        return (f"Next run easy: keep HR under {z2}. "
                f"Easy share {context.easy_share_60d * 100:.0f} % vs 70 %.")
    if state["W1"] == "ALERT" and context.weight_7d is not None:
        drift = context.weight_drift_kg or 0.0
        return (f"Weight {context.weight_7d:.1f} kg, {drift:+.1f} kg vs 28-day mean. "
                "Check intake and weigh-in timing.")
    return f"On track. Next: {context.next_due}."


def status_block(signals: list[Signal]) -> dict[str, object]:
    return {
        "overall": overall_state(signals),
        "domains": {domain: domain_state(signals, domain) for domain in DOMAINS},
        "drivers": [
            " ".join(
                part
                for part in (s.label, s.display, f"({s.vs_avg})" if s.vs_avg else "")
                if part
            )
            for s in drivers(signals)
        ],
    }
```

- [ ] **Step 4: Run the whole suite, expect PASS; lint; type-check**

Run: `uv run pytest -v && uv run ruff check . && uv run mypy src`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/deviations.py tests/test_status.py
git commit -m "health: STATUS compilation, deviation list and the action line"
```

---
## Part B: the document

### Task 12: `board.json` — the contract between the analysis and the screen

**Files:**
- Create: `src/atlas_health/board.py`, `tests/test_board.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION = 1` and
  `build_board(source: DataSource, config: HealthConfig, as_of: pd.Timestamp) -> dict[str, object]`.
- Consumes: everything from Tasks 2 to 11.

This is the one place the document's shape is defined. Tasks 18 to 21 read exactly these keys
and no others.

```
{
  "schema": 1,
  "generated_at": "2026-09-07T10:00:00-04:00",   ISO 8601, local zone
  "as_of_night": "2026-09-06" | null,            night_date of the most recent night with data
  "status":   {"overall": "ALERT",
               "domains": {"sleep": "ALERT", "heart": "OK",
                           "fitness": "WATCH", "weight": "NO DATA"},
               "drivers": ["bedtime regularity 77 min (vs 30 target)", ...]},
  "action":   "Bed by 23:50 tonight. ...",
  "deviations": [{"label","value","vs_avg","arrow","state"}],     max 4, GOOD last
  "signals":  [{"key","domain","label","state","display","vs_avg","z","arrow"}],  all of them
  "tiles":    [{"key","title","value","arrow","state","lines":[str, str]}],  5, in screen order
  "sleep": {
    "goal_h": 7.5, "target_bedtime": "23:50", "median_bedtime": 24.67,
    "mean_7n_min": 395.0, "mean_60d_min": 401.0, "debt_14n_h": 4.2,
    "bedtime_sd_min": 77.0, "social_jet_lag_h": 1.67,
    "score": {"duration": 37.0, "consistency": 5.0, "interruptions": 20.0,
              "total": 62.0, "lever": "consistency"},
    "nights": [{"date","asleep_min","deep_min","rem_min","core_min","awake_min",
                "interruptions","naps_min","bedtime","waketime","midsleep",
                "hr_min_frac","state"}]          60 days, ascending, gaps omitted
  },
  "heart": {
    "sleep_hr_mean_60d","sleep_hr_sd_60d","hrv_mean_60d","hrv_7n_sd",
    "rhr_latest","rhr_60d","resp_latest","hrr_last4":[float],
    "nights": [{"date","sleep_avg_hr","sleep_min_hr","hrv","resp","rhr"}]
  },
  "exercise": {
    "zone_edges": [141.2, 155.9, 170.6, 185.3], "hr_max": 200, "rhr_anchor": 53.0,
    "easy_share_60d": 0.10, "easy_share_target": 0.70, "vdot": 39.1,
    "capacity": {...},        exactly exercise.capacity()'s dict
    "consistency": {...},     exactly exercise.consistency()'s dict
    "load": {...},            exactly exercise.load_summary()'s dict
    "weekly": [{"week_start","trimp","runs","strength","miles"}],   8 weeks, oldest first
    "runs": [{"date","miles","pace_min_mi","avg_hr","max_hr","hrr","speed_at_hr","ef",
              "zones":[5 floats],"state"}]        last 8 runs, newest first
  },
  "weight": {...},            weight.weight_block()'s dict, plus "bmi_stale": bool
  "coverage": {"nights_with_data_7","nights_with_data_60","weigh_ins_7d",
               "last_import_at","last_hr_sample_at","last_weight_at"}
}
```

Every number is a JSON number or `null` — never NaN, which `json.dumps` writes as bare `NaN`
and `JSON.parse` rejects.

- [ ] **Step 1: Write the failing tests**

`tests/test_board.py`:

```python
import json
from pathlib import Path

import pandas as pd

from atlas_health.board import SCHEMA_VERSION, build_board
from atlas_health.config import HealthConfig
from atlas_health.source import CsvSource


def test_board_from_the_real_fixture_has_every_section(
    source: CsvSource, config: HealthConfig, as_of: pd.Timestamp
) -> None:
    board = build_board(source, config, as_of)
    assert board["schema"] == SCHEMA_VERSION
    for key in ("generated_at", "status", "action", "deviations", "signals", "tiles",
                "sleep", "heart", "exercise", "weight", "coverage"):
        assert key in board, key
    status = board["status"]
    assert set(status["domains"]) == {"sleep", "heart", "fitness", "weight"}
    assert status["overall"] in {"OK", "GOOD", "WATCH", "ALERT", "NO DATA"}
    assert len(board["tiles"]) == 5
    assert [t["key"] for t in board["tiles"]] == [
        "sleep", "sleep_hr", "hrv", "weight", "last_run"
    ]
    assert len(board["deviations"]) <= 4


def test_board_is_json_serialisable_with_no_nan(
    source: CsvSource, config: HealthConfig, as_of: pd.Timestamp, tmp_path: Path
) -> None:
    board = build_board(source, config, as_of)
    text = json.dumps(board, allow_nan=False)
    assert "NaN" not in text
    (tmp_path / "board.json").write_text(text, encoding="utf-8")
    assert json.loads(text)["schema"] == SCHEMA_VERSION


def test_board_reproduces_the_specs_reading_of_the_query_date(
    source: CsvSource, config: HealthConfig, as_of: pd.Timestamp
) -> None:
    """Spec §5.1: on 2026-09-07 bedtime SD is 77 min, so Sleep is ALERT, easy
    share is about 10 % so Fitness is WATCH, and there is no weight data."""
    board = build_board(source, config, as_of)
    assert board["status"]["domains"]["sleep"] == "ALERT"
    assert board["status"]["domains"]["fitness"] in {"WATCH", "ALERT"}
    assert board["status"]["domains"]["weight"] == "NO DATA"
    assert board["status"]["overall"] == "ALERT"
    assert board["action"].startswith("Bed by ")
    assert board["exercise"]["zone_edges"][0] > 130


def test_empty_source_produces_a_no_data_board_not_a_crash(
    config: HealthConfig, as_of: pd.Timestamp, tmp_path: Path
) -> None:
    board = build_board(CsvSource(tmp_path), config, as_of)
    assert board["status"]["overall"] == "NO DATA"
    assert board["sleep"]["nights"] == []
    assert board["coverage"]["nights_with_data_60"] == 0
    json.dumps(board, allow_nan=False)
```

- [ ] **Step 2: Run, expect ImportError**

Run: `uv run pytest tests/test_board.py -v`

- [ ] **Step 3: Implement**

`src/atlas_health/board.py`:

```python
"""Assemble board.json (schema 1), the only contract between this package and
the wall board. Everything the screen draws is in here; the runner passes the
document through untouched and the JS reads these keys.

NaN never leaves this module: the board is JSON, and `JSON.parse` rejects the
bare `NaN` that `json.dumps` would otherwise emit.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from atlas_health import deviations as dv
from atlas_health import exercise, heart, sleep, weight
from atlas_health.config import HealthConfig
from atlas_health.source import DataSource

SCHEMA_VERSION = 1
RUNS_ON_SCREEN = 8
WEEKS_ON_SCREEN = 8


def _clean(value: object) -> Any:
    """JSON-safe: NaN and pandas NA become None, numpy scalars become floats."""
    if value is None:
        return None
    if isinstance(value, bool | str):
        return value
    if isinstance(value, int | float):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if isinstance(value, list | tuple):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if pd.isna(value):  # type: ignore[arg-type]
        return None
    return str(value)


def _night_state(asleep_min: float, goal_h: float) -> str:
    if asleep_min < 5 * 60:
        return "ALERT"
    if asleep_min < 6 * 60:
        return "WATCH"
    return "GOOD" if asleep_min >= goal_h * 60 else "OK"


def _signal(signals: list[dv.Signal], key: str) -> dv.Signal:
    for item in signals:
        if item.key == key:
            return item
    return dv.blank(key, "sleep", key)


def _tiles(
    signals: list[dv.Signal], nights: pd.DataFrame, heart_block: dict[str, Any],
    weight_block: dict[str, Any], consistency: dict[str, Any], config: HealthConfig,
) -> list[dict[str, Any]]:
    s1, s2 = _signal(signals, "S1"), _signal(signals, "S2")
    h1, h2 = _signal(signals, "H1"), _signal(signals, "H2")
    w1, f4 = _signal(signals, "W1"), _signal(signals, "F4")
    mean_60d = heart_block["sleep_hr_mean_60d"]
    hrv_mean = heart_block["hrv_mean_60d"]
    hrv_sd = heart_block["hrv_7n_sd"]
    minimum = dv._latest(nights, "sleep_min_hr") if len(nights) else None
    seven = weight_block["weight_7d"]
    twenty_eight = weight_block["weight_28d"]
    days_since_run = consistency["days_since_run"]
    return [
        {"key": "sleep", "title": "SLEEP", "value": s1.display, "arrow": s1.arrow,
         "state": s1.state,
         "lines": [f"7n {s2.display}", s2.vs_avg or "—"]},
        {"key": "sleep_hr", "title": "SLEEP HR", "value": h1.display, "arrow": h1.arrow,
         "state": h1.state,
         "lines": [f"60d {mean_60d:.0f}" if mean_60d is not None else "60d —",
                   f"min {minimum:.0f}" if minimum is not None else "min —"]},
        {"key": "hrv", "title": "HRV 7n", "value": h2.display, "arrow": h2.arrow,
         "state": h2.state,
         "lines": [f"60d {hrv_mean:.0f}" if hrv_mean is not None else "60d —",
                   f"+/-{hrv_sd:.0f}" if hrv_sd is not None else "+/-—"]},
        {"key": "weight", "title": "WEIGHT 7d", "value": f"{seven:.1f}" if seven else "--",
         "arrow": w1.arrow, "state": w1.state,
         "lines": [f"28d {twenty_eight:.1f}" if twenty_eight else "no data",
                   f"n {weight_block['weigh_ins_7d']}/7"]},
        {"key": "last_run", "title": "LAST RUN",
         "value": f"{days_since_run}d" if days_since_run is not None else "—",
         "arrow": f4.arrow, "state": f4.state,
         "lines": [f"{consistency['runs_per_week_4w']:.1f}/wk",
                   f"tgt {config.runs_per_week_target}/wk"]},
    ]


def build_board(
    source: DataSource, config: HealthConfig, as_of: pd.Timestamp
) -> dict[str, Any]:
    tz = config.tz
    stages = source.load("sleep_stages")
    heart_rate = source.load("heart_rate")
    vitals = source.load("vitals")
    workouts_raw = source.load("workouts")
    workout_hr = source.load("workout_hr")
    running_speed = source.load("running_speed")
    imports = source.load("import_logs")

    all_nights = sleep.cluster_nights(stages, watch_source=config.watch_source, tz=tz)
    nights = sleep.nights_with_data(all_nights, as_of, 60)
    if not nights.empty:
        nights = sleep.add_timing(nights, tz)
        nights = heart.add_sleep_heart(nights, heart_rate, vitals, tz)

    rhr_anchor = heart.rhr_60d(vitals, as_of) or 53.0
    edges = config.zone_edges(rhr_anchor)
    workouts = exercise.enrich_workouts(
        workouts_raw, workout_hr, running_speed, vitals, config, edges, tz
    )
    hrr = heart.hrr_readings(vitals)
    capacity = exercise.capacity(workouts, hrr)
    consistency = exercise.consistency(workouts, as_of, tz, config)
    load = exercise.load_summary(workouts, as_of, tz)
    easy_share = exercise.easy_share_60d(workouts, as_of)
    weekly = exercise.weekly_load(workouts, as_of, tz, weeks=WEEKS_ON_SCREEN)
    weight_block = weight.weight_block(vitals, as_of, tz, config.height_m)

    signals = (
        dv.sleep_signals(nights, config)
        + dv.heart_signals(nights, config)
        + dv.fitness_signals(capacity, consistency, load, easy_share, config)
        + dv.weight_signals(weight_block)
    )

    hrv_means = dv.rolling7(nights["hrv"]) if "hrv" in nights else pd.Series(dtype=float)
    heart_block: dict[str, Any] = {
        "sleep_hr_mean_60d": _clean(nights["sleep_avg_hr"].mean()) if len(nights) else None,
        "sleep_hr_sd_60d": _clean(nights["sleep_avg_hr"].std()) if len(nights) > 1 else None,
        "hrv_mean_60d": _clean(nights["hrv"].mean()) if len(nights) else None,
        "hrv_7n_sd": _clean(hrv_means.std()) if len(hrv_means) > 1 else None,
        "rhr_latest": _clean(dv._latest(nights, "rhr")) if len(nights) else None,
        "rhr_60d": _clean(rhr_anchor),
        "resp_latest": _clean(dv._latest(nights, "resp")) if len(nights) else None,
        "hrr_last4": _clean(capacity["hrr_last4"]),
        "nights": [
            {"date": str(row["night_date"]),
             "sleep_avg_hr": _clean(row["sleep_avg_hr"]),
             "sleep_min_hr": _clean(row["sleep_min_hr"]),
             "hrv": _clean(row["hrv"]), "resp": _clean(row["resp"]),
             "rhr": _clean(row["rhr"])}
            for _, row in nights.iterrows()
        ],
    }

    sleep_block: dict[str, Any] = {
        "goal_h": config.sleep_goal_h,
        "target_bedtime": sleep.target_bedtime(nights, config.sleep_goal_h)
        if len(nights) else None,
        "median_bedtime": _clean(nights["bedtime"].median()) if len(nights) else None,
        "mean_7n_min": _clean(sleep.sleep_7n(nights)) if len(nights) else None,
        "mean_60d_min": _clean(nights["asleep_min"].mean()) if len(nights) else None,
        "debt_14n_h": _clean(sleep.debt_14n_h(nights, config.sleep_goal_h)) if len(nights) else 0.0,
        "bedtime_sd_min": _clean(sleep.bedtime_sd_min(nights)) if len(nights) else None,
        "social_jet_lag_h": _clean(sleep.social_jet_lag_h(nights, config.free_days))
        if len(nights) else None,
        "score": None,
        "nights": [
            {"date": str(row["night_date"]),
             "asleep_min": _clean(row["asleep_min"]), "deep_min": _clean(row["deep_min"]),
             "rem_min": _clean(row["rem_min"]), "core_min": _clean(row["core_min"]),
             "awake_min": _clean(row["awake_min"]),
             "interruptions": int(row["interruptions"]),
             "naps_min": _clean(row["naps_min"]),
             "bedtime": _clean(row["bedtime"]), "waketime": _clean(row["waketime"]),
             "midsleep": _clean(row["midsleep"]), "hr_min_frac": _clean(row["hr_min_frac"]),
             "state": _night_state(float(row["asleep_min"]), config.sleep_goal_h)}
            for _, row in nights.iterrows()
        ],
    }
    if len(nights):
        latest = nights.iloc[-1]
        sleep_block["score"] = _clean(sleep.sleep_score(
            float(latest["asleep_min"]) / 60, float(latest["deep_min"]) / 60,
            float(latest["rem_min"]) / 60, sleep.bedtime_dev_min(nights),
            int(latest["interruptions"]), config.sleep_goal_h,
        ))

    runs = workouts[workouts["is_run"]].sort_values("start_time", ascending=False)
    exercise_block: dict[str, Any] = {
        "zone_edges": _clean(edges),
        "hr_max": config.hr_max,
        "rhr_anchor": _clean(rhr_anchor),
        "easy_share_60d": _clean(easy_share),
        "easy_share_target": config.easy_share_target,
        "vdot": _clean(exercise.vdot_estimate(workouts, as_of)),
        "capacity": _clean(capacity),
        "consistency": _clean(consistency),
        "load": _clean(load),
        "weekly": _clean(weekly),
        "runs": [
            {"date": str(row["start_time"].tz_convert(tz).date()),
             "miles": _clean(row["miles"]), "pace_min_mi": _clean(row["pace_min_mi"]),
             "avg_hr": _clean(row["avg_hr"]), "max_hr": _clean(row["max_hr"]),
             "hrr": _clean(row["hrr_1min"]), "speed_at_hr": _clean(row["speed_at_hr"]),
             "ef": _clean(row["ef"]), "zones": _clean(list(row["zones"])),
             "state": "WATCH" if (row["easy_share"] == row["easy_share"]
                                  and row["easy_share"] < 0.5) else "OK"}
            for _, row in runs.head(RUNS_ON_SCREEN).iterrows()
        ],
    }

    weight_out = dict(weight_block)
    weight_out["bmi_stale"] = weight_block["weight_7d"] is None
    successes = imports[imports["status"] == "success"] if len(imports) else imports
    coverage = {
        "nights_with_data_7": int(len(sleep.nights_with_data(all_nights, as_of, 7))),
        "nights_with_data_60": int(len(nights)),
        "weigh_ins_7d": int(weight_block["weigh_ins_7d"]),
        "last_import_at": str(successes["created_at"].max().tz_convert(tz))
        if len(successes) else None,
        "last_hr_sample_at": str(heart_rate["time"].max().tz_convert(tz))
        if len(heart_rate) else None,
        "last_weight_at": weight_block["last_date"],
    }

    z2_top = edges[1]
    next_due = (
        "run"
        if (consistency["days_since_run"] or 99) * config.runs_per_week_target
        >= (consistency["days_since_strength"] or 99) * config.strength_per_week_target
        else "lift"
    )
    hrv_signal = _signal(signals, "H2")
    hrv_mean = heart_block["hrv_mean_60d"]
    hrv_pct = None
    if hrv_mean and hrv_signal.z is not None and heart_block["hrv_7n_sd"]:
        hrv_pct = 100 * (hrv_signal.z * heart_block["hrv_7n_sd"]) / hrv_mean
    context = dv.ActionContext(
        target_bedtime=sleep_block["target_bedtime"],
        debt_14n_h=float(sleep_block["debt_14n_h"] or 0.0),
        bedtime_sd_min=sleep_block["bedtime_sd_min"],
        z2_top=float(z2_top),
        easy_share_60d=easy_share,
        days_since_run=consistency["days_since_run"],
        days_since_strength=consistency["days_since_strength"],
        weight_7d=weight_block["weight_7d"],
        weight_drift_kg=weight_block["drift_kg"],
        fixed_hr_band=f"{config.fixed_hr_low}-{config.fixed_hr_high}",
        speed_decline_pct=dv._num(capacity.get("speed_at_hr_vs_avg_pct")),
        sleep_hr_delta=(
            None if heart_block["sleep_hr_mean_60d"] is None
            else (dv._latest(nights, "sleep_avg_hr") or 0.0) - heart_block["sleep_hr_mean_60d"]
        ),
        hrv_pct=hrv_pct,
        next_due=next_due,
    )

    return {
        "schema": SCHEMA_VERSION,
        "generated_at": as_of.isoformat(),
        "as_of_night": str(nights.iloc[-1]["night_date"]) if len(nights) else None,
        "status": dv.status_block(signals),
        "action": dv.action_line(signals, context),
        "deviations": dv.deviation_rows(signals),
        "signals": [
            {"key": s.key, "domain": s.domain, "label": s.label, "state": s.state,
             "display": s.display, "vs_avg": s.vs_avg, "z": _clean(s.z), "arrow": s.arrow}
            for s in signals
        ],
        "tiles": _tiles(signals, nights, heart_block, weight_block, consistency, config),
        "sleep": sleep_block,
        "heart": heart_block,
        "exercise": exercise_block,
        "weight": _clean(weight_out),
        "coverage": coverage,
    }
```

`dv._latest` and `dv._num` are private helpers reused here deliberately: they are the same
"pandas value to float or None" rule, and duplicating them would let the two drift.

- [ ] **Step 4: Run, expect PASS; lint; type-check**

Run: `uv run pytest tests/test_board.py -v && uv run ruff check . && uv run mypy src`
Expected: PASS, clean. If ruff flags `dv._latest` / `dv._num` as private access, rename both
to `latest_value` and `as_float` in `deviations.py` and update every call site.

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/board.py tests/test_board.py
git commit -m "health: assemble board.json, the contract between the analysis and the screen"
```

---

### Task 13: ntfy delivery with the repeat rules (§4.10)

**Files:**
- Create: `src/atlas_health/notify.py`, `tests/test_notify.py`

**Interfaces:**
- Produces:
  - `alert_body(board: dict) -> str` — the action line plus up to 5 deviation lines.
  - `weekly_body(board: dict) -> str`.
  - `should_push(board: dict, state: dict, today: date) -> tuple[bool, dict]` — the decision and
    the state to persist. State keys: `last_push_date` (ISO date or None),
    `alert_keys` (list of signal keys at ALERT at the last push), `alerting_since` (ISO date).
  - `load_state(path: Path) -> dict`, `save_state(path: Path, state: dict) -> None`.
  - `push(config: HealthConfig, title: str, body: str, *, tags: str) -> bool` — POSTs to ntfy,
    returns False on any failure instead of raising.
  - `nightly_push(config, board, today, *, sender=push) -> bool` — ties the above together.
- Rules (spec §4.10): ALERT pushes; WATCH and GOOD never do. Push again only when a new signal
  reaches ALERT, or the state has been ALERT for 3 consecutive days and 3 days have passed since
  the last push.

- [ ] **Step 1: Write the failing tests**

`tests/test_notify.py`:

```python
from datetime import date
from pathlib import Path

from atlas_health import notify
from atlas_health.config import HealthConfig


def _board(overall: str, alert_keys: list[str]) -> dict[str, object]:
    return {
        "status": {"overall": overall, "domains": {}, "drivers": []},
        "action": "Bed by 23:50 tonight. Bedtime varies +/-77 min; aim within 30.",
        "deviations": [
            {"label": "bedtime regularity", "value": "77 min", "vs_avg": "vs 30 target",
             "arrow": "up", "state": "ALERT"},
            {"label": "easy share", "value": "10 %", "vs_avg": "vs 70 % target",
             "arrow": "down", "state": "WATCH"},
        ],
        "signals": [
            {"key": key, "domain": "sleep", "label": key, "state": "ALERT",
             "display": "x", "vs_avg": "", "z": None, "arrow": "up"}
            for key in alert_keys
        ],
        "sleep": {"mean_7n_min": 395.0, "mean_60d_min": 401.0},
        "exercise": {"capacity": {"capacity_z": -0.2},
                     "consistency": {"runs_this_week": 1, "strength_this_week": 0,
                                     "runs_per_week_target": 3, "strength_per_week_target": 2}},
        "weight": {"weight_7d": None, "weight_28d": None},
    }


def test_no_push_below_alert() -> None:
    push, state = notify.should_push(_board("WATCH", []), {}, date(2026, 9, 7))
    assert push is False and state["alerting_since"] is None


def test_first_alert_pushes() -> None:
    push, state = notify.should_push(_board("ALERT", ["S3"]), {}, date(2026, 9, 7))
    assert push is True
    assert state["last_push_date"] == "2026-09-07" and state["alert_keys"] == ["S3"]


def test_same_alert_next_day_is_silent() -> None:
    _, state = notify.should_push(_board("ALERT", ["S3"]), {}, date(2026, 9, 7))
    push, state2 = notify.should_push(_board("ALERT", ["S3"]), state, date(2026, 9, 8))
    assert push is False
    assert state2["alerting_since"] == "2026-09-07"


def test_a_new_alerting_signal_pushes_again() -> None:
    _, state = notify.should_push(_board("ALERT", ["S3"]), {}, date(2026, 9, 7))
    push, _ = notify.should_push(_board("ALERT", ["S3", "H1"]), state, date(2026, 9, 8))
    assert push is True


def test_persistent_alert_pushes_every_third_day() -> None:
    _, state = notify.should_push(_board("ALERT", ["S3"]), {}, date(2026, 9, 7))
    for day in (8, 9):
        push, state = notify.should_push(_board("ALERT", ["S3"]), state, date(2026, 9, day))
        assert push is False
    push, state = notify.should_push(_board("ALERT", ["S3"]), state, date(2026, 9, 10))
    assert push is True and state["last_push_date"] == "2026-09-10"


def test_recovery_clears_the_streak() -> None:
    _, state = notify.should_push(_board("ALERT", ["S3"]), {}, date(2026, 9, 7))
    _, state = notify.should_push(_board("OK", []), state, date(2026, 9, 8))
    assert state["alerting_since"] is None and state["alert_keys"] == []
    push, _ = notify.should_push(_board("ALERT", ["S3"]), state, date(2026, 9, 9))
    assert push is True


def test_alert_body_is_the_action_line_then_the_deviations() -> None:
    body = notify.alert_body(_board("ALERT", ["S3"]))
    lines = body.splitlines()
    assert lines[0].startswith("Bed by 23:50")
    assert "bedtime regularity" in lines[1]
    assert len(lines) <= 6


def test_state_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "notify.json"
    assert notify.load_state(path) == {}
    notify.save_state(path, {"last_push_date": "2026-09-07"})
    assert notify.load_state(path)["last_push_date"] == "2026-09-07"


def test_nightly_push_uses_the_sender_and_persists(config: HealthConfig, tmp_path: Path) -> None:
    sent: list[tuple[str, str]] = []

    def sender(cfg: HealthConfig, title: str, body: str, *, tags: str) -> bool:
        sent.append((title, body))
        return True

    board = _board("ALERT", ["S3"])
    pushed = notify.nightly_push(config, board, date(2026, 9, 7), state_path=tmp_path / "n.json",
                                 sender=sender)
    assert pushed is True and len(sent) == 1
    again = notify.nightly_push(config, board, date(2026, 9, 8), state_path=tmp_path / "n.json",
                                sender=sender)
    assert again is False and len(sent) == 1
```

- [ ] **Step 2: Run, expect ImportError**

Run: `uv run pytest tests/test_notify.py -v`

- [ ] **Step 3: Implement**

`src/atlas_health/notify.py`:

```python
"""ntfy delivery (spec §4.10).

ALERT pushes; WATCH and GOOD live on the screen and nowhere else. The repeat
rules exist so a habit that stays out of range — bedtime regularity will, for
weeks — does not become a daily notification the subject learns to ignore.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from atlas_health.config import HealthConfig

logger = logging.getLogger(__name__)

MAX_DEVIATION_LINES = 5
REPEAT_AFTER_DAYS = 3

Sender = Callable[..., bool]


def load_state(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def _alerting_keys(board: dict[str, Any]) -> list[str]:
    signals = board.get("signals", [])
    return sorted(
        str(signal["key"]) for signal in signals if signal.get("state") == "ALERT"
    )


def alert_body(board: dict[str, Any]) -> str:
    lines = [str(board.get("action", ""))]
    for row in board.get("deviations", [])[:MAX_DEVIATION_LINES]:
        lines.append(f"{row['label']}  {row['value']}  {row['vs_avg']}  {row['state']}")
    return "\n".join(line for line in lines if line)


def weekly_body(board: dict[str, Any]) -> str:
    sleep = board.get("sleep", {})
    capacity = board.get("exercise", {}).get("capacity", {})
    consistency = board.get("exercise", {}).get("consistency", {})
    weight = board.get("weight", {})

    def hours(minutes: object) -> str:
        return "—" if minutes is None else f"{float(minutes) / 60:.1f} h"

    def kilograms(value: object) -> str:
        return "—" if value is None else f"{float(value):.1f} kg"

    capacity_z = capacity.get("capacity_z")
    return "\n".join([
        f"sleep 7n {hours(sleep.get('mean_7n_min'))} vs 60d {hours(sleep.get('mean_60d_min'))}",
        f"capacity z {capacity_z:+.1f}" if capacity_z is not None else "capacity —",
        f"runs {consistency.get('runs_this_week', 0)}/{consistency.get('runs_per_week_target', 3)}"
        f" · strength {consistency.get('strength_this_week', 0)}"
        f"/{consistency.get('strength_per_week_target', 2)}",
        f"weight 7d {kilograms(weight.get('weight_7d'))}"
        f" vs 28d {kilograms(weight.get('weight_28d'))}",
    ])


def should_push(
    board: dict[str, Any], state: dict[str, Any], today: date
) -> tuple[bool, dict[str, Any]]:
    """The decision and the state to persist afterwards."""
    overall = str(board.get("status", {}).get("overall", "NO DATA"))
    keys = _alerting_keys(board)
    since = state.get("alerting_since")
    previous_keys = list(state.get("alert_keys", []))
    last_push = state.get("last_push_date")

    if overall != "ALERT":
        return False, {"last_push_date": last_push, "alert_keys": [], "alerting_since": None}

    if since is None:
        return True, {"last_push_date": today.isoformat(), "alert_keys": keys,
                      "alerting_since": today.isoformat()}

    new_keys = [key for key in keys if key not in previous_keys]
    stale = (
        last_push is not None
        and (today - date.fromisoformat(str(last_push))).days >= REPEAT_AFTER_DAYS
    )
    if new_keys or stale:
        return True, {"last_push_date": today.isoformat(),
                      "alert_keys": sorted(set(previous_keys) | set(keys)),
                      "alerting_since": since}
    return False, {"last_push_date": last_push, "alert_keys": previous_keys,
                   "alerting_since": since}


def push(config: HealthConfig, title: str, body: str, *, tags: str = "heart") -> bool:
    """Never raises: a failed notification must not fail the nightly run."""
    if not config.ntfy_topic:
        logger.info("ntfy: no topic configured, not pushing")
        return False
    headers = {"Title": title, "Tags": tags, "Priority": "default"}
    if config.ntfy_token:
        headers["Authorization"] = f"Bearer {config.ntfy_token}"
    try:
        response = httpx.post(
            f"{config.ntfy_url.rstrip('/')}/{config.ntfy_topic}",
            content=body.encode("utf-8"), headers=headers, timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("ntfy: push failed: %s", exc)
        return False
    return True


def nightly_push(
    config: HealthConfig, board: dict[str, Any], today: date, *,
    state_path: Path | None = None, sender: Sender = push,
) -> bool:
    path = state_path or (config.state_dir / "notify.json")
    decided, state = should_push(board, load_state(path), today)
    save_state(path, state)
    if not decided:
        return False
    return bool(sender(config, "atlas health: ALERT", alert_body(board), tags="rotating_light"))


def weekly_push(
    config: HealthConfig, board: dict[str, Any], *, sender: Sender = push
) -> bool:
    if not config.weekly_summary:
        return False
    return bool(sender(config, "atlas health: week", weekly_body(board), tags="chart"))
```

- [ ] **Step 4: Run, expect PASS; lint; type-check**

Run: `uv run pytest tests/test_notify.py -v && uv run ruff check . && uv run mypy src`
Expected: PASS, clean. The suite makes no network call: every test passes its own `sender`.

- [ ] **Step 5: Commit**

```bash
git add src/atlas_health/notify.py tests/test_notify.py
git commit -m "health: ntfy alerts with the new-signal and every-third-day repeat rules"
```

---

### Task 14: SQLite history and the working CLI

**Files:**
- Create: `src/atlas_health/store.py`, `tests/test_store.py`, `tests/test_cli.py`
- Modify: `src/atlas_health/cli.py`

**Interfaces:**
- Produces:
  - `store.SCHEMA_SQL: str`, `store.connect(path: Path) -> sqlite3.Connection`, and
    `store.save_history(path: Path, board: dict) -> int` returning the number of night rows
    written; it creates the file and the `nights`, `runs` and `status_history` tables if absent
    and upserts into them.
  - `cli.write_board(board: dict, path: Path) -> None` — atomic write (temp file, then replace).
  - `cli.summary_line(board: dict) -> str`.
  - `cli.main(argv)` implementing `nightly`, `weekly`, `dump-fixture`, `render`, returning 0 on
    success and 1 when the board could not be built.
- `nightly` prints one `atlas-summary {json}` line so the Runs panel shows figures
  (`scripts/repos.py` reads the last such line): keys `nights`, `alerts`, `runs`, `weigh_ins`.
- The history database is `/var/lib/atlas-health/health.db` — `config.state_dir / "health.db"`,
  beside `board.json`. Nothing is written to Postgres, ever.

- [ ] **Step 1: Write the store module**

`src/atlas_health/store.py`:

```python
"""Keep the computed nights and runs in SQLite, beside the board document.

The board only needs the JSON document, but a history table is what makes an
ad-hoc question ("what did my sleeping HR do in March?") answerable without
recomputing a year of stages.

SQLite rather than a schema inside FreeReps' Postgres: the `analysis` role
reads `public` and writes nothing, so a database this project owns outright is
one fewer permission to hold, it costs a file instead of a migration, and the
whole write path can be tested in a tmp directory with no server running. The
file lives in the state directory, never in the checkout, because deploys
replace the checkout.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nights (
    night_date    TEXT PRIMARY KEY,
    asleep_min    REAL,
    deep_min      REAL,
    rem_min       REAL,
    core_min      REAL,
    awake_min     REAL,
    interruptions INTEGER,
    naps_min      REAL,
    bedtime       REAL,
    waketime      REAL,
    midsleep      REAL,
    hr_min_frac   REAL,
    sleep_avg_hr  REAL,
    sleep_min_hr  REAL,
    hrv           REAL,
    resp          REAL,
    rhr           REAL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    run_date     TEXT PRIMARY KEY,
    miles        REAL,
    pace_min_mi  REAL,
    avg_hr       REAL,
    max_hr       REAL,
    hrr          REAL,
    speed_at_hr  REAL,
    ef           REAL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS status_history (
    day        TEXT PRIMARY KEY,
    overall    TEXT NOT NULL,
    domains    TEXT NOT NULL,
    action     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_NIGHT_COLUMNS = (
    "night_date", "asleep_min", "deep_min", "rem_min", "core_min", "awake_min",
    "interruptions", "naps_min", "bedtime", "waketime", "midsleep", "hr_min_frac",
    "sleep_avg_hr", "sleep_min_hr", "hrv", "resp", "rhr",
)
_RUN_COLUMNS = (
    "run_date", "miles", "pace_min_mi", "avg_hr", "max_hr", "hrr", "speed_at_hr", "ef",
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    # A nightly job and a curious interactive session should not block each
    # other, and WAL survives the Pi losing power mid-write better than the
    # rollback journal does.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    return conn


def _upsert(columns: tuple[str, ...], table: str) -> str:
    names = ", ".join(columns)
    holders = ", ".join(f":{name}" for name in columns)
    updates = ", ".join(f"{name} = excluded.{name}" for name in columns[1:])
    return (
        f"INSERT INTO {table} ({names}) VALUES ({holders}) "
        f"ON CONFLICT({columns[0]}) DO UPDATE SET {updates}, "
        f"updated_at = datetime('now')"
    )


def save_history(path: Path, board: dict[str, Any]) -> int:
    """Upsert nights, runs and today's status. Returns the night count.

    Re-running on the same day is a no-op beyond refreshed values: every table
    is keyed by its date, so a job that fires twice cannot double-count.
    """
    heart_by_date = {row["date"]: row for row in board["heart"]["nights"]}
    nights: list[dict[str, Any]] = []
    for row in board["sleep"]["nights"]:
        heart_row = heart_by_date.get(row["date"], {})
        nights.append({
            "night_date": row["date"],
            **{key: row.get(key) for key in _NIGHT_COLUMNS[1:12]},
            "sleep_avg_hr": heart_row.get("sleep_avg_hr"),
            "sleep_min_hr": heart_row.get("sleep_min_hr"),
            "hrv": heart_row.get("hrv"), "resp": heart_row.get("resp"),
            "rhr": heart_row.get("rhr"),
        })
    runs = [
        {"run_date": row["date"], "miles": row["miles"], "pace_min_mi": row["pace_min_mi"],
         "avg_hr": row["avg_hr"], "max_hr": row["max_hr"], "hrr": row["hrr"],
         "speed_at_hr": row["speed_at_hr"], "ef": row["ef"]}
        for row in board["exercise"]["runs"]
    ]
    with connect(path) as conn:
        if nights:
            conn.executemany(_upsert(_NIGHT_COLUMNS, "nights"), nights)
        if runs:
            conn.executemany(_upsert(_RUN_COLUMNS, "runs"), runs)
        conn.execute(
            "INSERT INTO status_history (day, overall, domains, action) "
            "VALUES (:day, :overall, :domains, :action) "
            "ON CONFLICT(day) DO UPDATE SET overall = excluded.overall, "
            "domains = excluded.domains, action = excluded.action",
            {"day": board["generated_at"][:10], "overall": board["status"]["overall"],
             "domains": json.dumps(board["status"]["domains"]),
             "action": board["action"]},
        )
    return len(nights)
```

A `sqlite3.Connection` used as a context manager commits on success and rolls back on an
exception; it does not close the connection, which is what we want in a process that exits
straight afterwards.

- [ ] **Step 2: Write the store tests and run them**

`tests/test_store.py`:

```python
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from atlas_health import store
from atlas_health.board import build_board
from atlas_health.config import HealthConfig
from atlas_health.source import CsvSource


def _board(overall: str = "ALERT") -> dict[str, Any]:
    return {
        "generated_at": "2026-09-07T10:00:00-04:00",
        "status": {"overall": overall,
                   "domains": {"sleep": overall, "heart": "OK", "fitness": "WATCH",
                               "weight": "NO DATA"},
                   "drivers": []},
        "action": "Bed by 23:50 tonight.",
        "sleep": {"nights": [
            {"date": "2026-09-06", "asleep_min": 372.0, "deep_min": 58.0, "rem_min": 84.0,
             "core_min": 230.0, "awake_min": 11.0, "interruptions": 3, "naps_min": 0.0,
             "bedtime": 25.6, "waketime": 7.8, "midsleep": 28.7, "hr_min_frac": 0.74,
             "state": "WATCH"},
        ]},
        "heart": {"nights": [
            {"date": "2026-09-06", "sleep_avg_hr": 51.0, "sleep_min_hr": 44.0, "hrv": 78.0,
             "resp": 16.4, "rhr": None},
        ]},
        "exercise": {"runs": [
            {"date": "2026-09-05", "miles": 2.41, "pace_min_mi": 11.6, "avg_hr": 162.0,
             "max_hr": 194.0, "hrr": 28.0, "speed_at_hr": 2.9, "ef": 0.0176, "zones": [],
             "state": "WATCH"},
        ]},
    }


def test_save_history_creates_the_database_and_the_rows(tmp_path: Path) -> None:
    path = tmp_path / "health.db"
    assert store.save_history(path, _board()) == 1
    conn = sqlite3.connect(path)
    night = conn.execute("SELECT night_date, asleep_min, hrv, rhr FROM nights").fetchone()
    assert night == ("2026-09-06", 372.0, 78.0, None)
    run = conn.execute("SELECT run_date, miles, hrr FROM runs").fetchone()
    assert run == ("2026-09-05", 2.41, 28.0)
    day = conn.execute("SELECT day, overall, domains FROM status_history").fetchone()
    assert day[0] == "2026-09-07" and day[1] == "ALERT"
    assert json.loads(day[2])["weight"] == "NO DATA"


def test_a_second_run_updates_rather_than_duplicating(tmp_path: Path) -> None:
    path = tmp_path / "health.db"
    store.save_history(path, _board())
    changed = _board(overall="WATCH")
    changed["sleep"]["nights"][0]["asleep_min"] = 400.0
    store.save_history(path, changed)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT count(*) FROM nights").fetchone()[0] == 1
    assert conn.execute("SELECT asleep_min FROM nights").fetchone()[0] == 400.0
    assert conn.execute("SELECT count(*) FROM status_history").fetchone()[0] == 1
    assert conn.execute("SELECT overall FROM status_history").fetchone()[0] == "WATCH"


def test_the_real_board_saves_every_night_it_carries(
    source: CsvSource, config: HealthConfig, as_of: pd.Timestamp, tmp_path: Path
) -> None:
    board = build_board(source, config, as_of)
    written = store.save_history(tmp_path / "health.db", board)
    assert written == len(board["sleep"]["nights"])
    conn = sqlite3.connect(tmp_path / "health.db")
    assert conn.execute("SELECT count(*) FROM nights").fetchone()[0] == written
```

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS. No server is involved, so this is the first task whose write path is covered
end to end by the hermetic suite.

- [ ] **Step 3: Write the failing CLI tests**

`tests/test_cli.py`:

```python
import json
from pathlib import Path

from atlas_health import cli


def test_render_writes_a_board_from_the_fixture_without_a_database(tmp_path: Path) -> None:
    out = tmp_path / "board.json"
    code = cli.main([
        "--env-file", str(tmp_path / "none.env"),
        "--as-of", "2026-09-07T10:00",
        "render", str(Path(__file__).parent / "fixtures"), str(out),
    ])
    assert code == 0
    board = json.loads(out.read_text(encoding="utf-8"))
    assert board["schema"] == 1
    assert board["status"]["overall"] in {"OK", "GOOD", "WATCH", "ALERT", "NO DATA"}


def test_write_board_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "board.json"
    cli.write_board({"schema": 1}, target)
    assert json.loads(target.read_text(encoding="utf-8")) == {"schema": 1}
    assert list(target.parent.iterdir()) == [target]


def test_summary_line_carries_the_board_figures() -> None:
    board = {
        "coverage": {"nights_with_data_60": 20, "weigh_ins_7d": 0},
        "signals": [{"key": "S3", "state": "ALERT"}, {"key": "F6", "state": "WATCH"}],
        "exercise": {"runs": [{"date": "2026-09-05"}, {"date": "2026-09-01"}]},
    }
    line = cli.summary_line(board)
    assert line.startswith("atlas-summary ")
    figures = json.loads(line.removeprefix("atlas-summary "))
    assert figures == {"nights": 20, "alerts": 1, "runs": 2, "weigh_ins": 0}
```

- [ ] **Step 4: Run, expect failure**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `render` still returns 2 and `write_board` / `summary_line` do not exist.

- [ ] **Step 5: Finish the CLI**

Replace the body of `src/atlas_health/cli.py` below `build_parser` with:

```python
def _as_of(raw: str | None, tz: str) -> pd.Timestamp:
    if raw is None:
        return pd.Timestamp.now(tz=tz)
    stamp = pd.Timestamp(raw)
    return stamp.tz_localize(tz) if stamp.tzinfo is None else stamp.tz_convert(tz)


def write_board(board: dict[str, Any], path: Path) -> None:
    """Atomic: the board polls this file, and a half-written document would
    show as a broken screen rather than a stale one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(board, allow_nan=False, indent=1), encoding="utf-8")
    temporary.replace(path)


def summary_line(board: dict[str, Any]) -> str:
    """The one line scripts/repos.py lifts onto the RUNS panel."""
    figures = {
        "nights": int(board["coverage"]["nights_with_data_60"]),
        "alerts": sum(1 for s in board["signals"] if s["state"] == "ALERT"),
        "runs": len(board["exercise"]["runs"]),
        "weigh_ins": int(board["coverage"]["weigh_ins_7d"]),
    }
    return "atlas-summary " + json.dumps(figures)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = HealthConfig.from_env(args.env_file)
    as_of = _as_of(args.as_of, config.tz)

    if args.cmd == "dump-fixture":
        dump_fixture(PostgresSource(config, as_of), args.out_dir)
        print(f"fixture written to {args.out_dir}")
        return 0

    if args.cmd == "render":
        board = build_board(CsvSource(args.fixture_dir), config, as_of)
        write_board(board, args.out_file)
        print(f"board written to {args.out_file}: {board['status']['overall']}")
        return 0

    try:
        board = build_board(PostgresSource(config, as_of), config, as_of)
    except Exception as exc:  # noqa: BLE001 - reported, then the run fails
        print(f"atlas-health: could not build the board: {exc}", file=sys.stderr)
        return 1

    if args.cmd == "weekly":
        pushed = notify.weekly_push(config, board)
        print(f"weekly summary {'pushed' if pushed else 'skipped'}")
        return 0

    write_board(board, config.state_dir / "board.json")
    try:
        nights = store.save_history(config.state_dir / "health.db", board)
        print(f"history: {nights} nights upserted into {config.state_dir / 'health.db'}")
    except Exception as exc:  # noqa: BLE001 - history is secondary to the screen
        print(f"atlas-health: history not saved: {exc}", file=sys.stderr)
    pushed = notify.nightly_push(config, board, as_of.date())
    print(f"status {board['status']['overall']} · action: {board['action']}")
    print(f"ntfy {'pushed' if pushed else 'not pushed'}")
    print(summary_line(board))
    return 0
```

with the module imports:

```python
import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from atlas_health import notify, store
from atlas_health.board import build_board
from atlas_health.config import HealthConfig
from atlas_health.source import CsvSource, PostgresSource, dump_fixture
```

- [ ] **Step 6: Run the whole suite, expect PASS; lint; type-check**

Run: `uv run pytest -v && uv run ruff check . && uv run mypy src`
Expected: PASS, clean.

- [ ] **Step 7: Run `nightly` for real against the live database on atlas**

This is the first end-to-end run: Postgres in, `board.json` and `health.db` out. It writes to
`/tmp` rather than `/var/lib/atlas-health`, which Task 20 creates.

```bash
git push
ssh domdd@atlas 'git -C /tmp/health-dump pull -q && cd /tmp/health-dump && STATE_DIR=/tmp/health-state ATLAS_HEALTH_ENV_FILE=/home/domdd/atlas-health-analysis/.env ~/.local/bin/uv run atlas-health --as-of 2026-09-07T10:00 nightly'
ssh domdd@atlas 'ls -la /tmp/health-state && sqlite3 /tmp/health-state/health.db "select count(*) from nights; select count(*) from runs; select day, overall from status_history;"'
```

Expected: the run prints `history: N nights upserted`, the status line
`status ALERT · action: Bed by ...` from spec §5.1, `ntfy not pushed` (no topic configured
yet), and one `atlas-summary {...}` line. The state directory holds `board.json` and
`health.db`; the night count matches the `nights` figure in the summary line. If `sqlite3` is
absent on the Pi, use
`python3 -c "import sqlite3;print(sqlite3.connect('/tmp/health-state/health.db').execute('select count(*) from nights').fetchone())"`.

- [ ] **Step 8: Commit and push**

```bash
git add -A
git commit -m "SQLite history and a working nightly/weekly/render CLI"
git push
```

---
## Part C: the runner passes the document through

### Task 15: `HealthBoardReader` — a fault-tolerant file read

**Files:**
- Create: `runner/src/atlas/telemetry/infrastructure/health_board.py`,
  `runner/tests/unit/telemetry/test_health_board.py`,
  `runner/tests/fixtures/health/board.json`
- Modify: `runner/src/atlas/config.py` (one setting), `runner/src/atlas/bootstrap/container.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) HealthBoardState` with `available: bool`, `detail: str`,
    `document: dict[str, Any] | None`, `generated_at: datetime | None`, `stale: bool`.
  - `HealthBoardReader(path: Path, tz: str, stale_after: timedelta = timedelta(hours=30))`
    with `read(now: datetime) -> HealthBoardState`.
  - `Settings.health_board_path: Path = Path("/var/lib/atlas-health/board.json")`.
  - `Application.health_board: HealthBoardReader`.
- The reader never raises and never opens a socket: it reads one file, exactly as
  `hosted_repos.py` reads its state directory, for the same reason (a missing file must not take
  down the panel whose job is to show failures).
- 30 hours, not 24: the nightly job runs at 10:00, so a board written yesterday morning is
  normal until this morning's run should have landed.

- [ ] **Step 1: Write the sample document**

`runner/tests/fixtures/health/board.json` — a minimal but schema-1-shaped document. The keys
must match Task 12 exactly, because Tasks 18 and 19 render this same fixture in the browser.

```json
{
  "schema": 1,
  "generated_at": "2026-09-07T10:00:00-04:00",
  "as_of_night": "2026-09-06",
  "status": {
    "overall": "ALERT",
    "domains": {"sleep": "ALERT", "heart": "OK", "fitness": "WATCH", "weight": "NO DATA"},
    "drivers": ["bedtime regularity 77 min (vs 30 target)", "easy share 10 % (vs 70 % target)"]
  },
  "action": "Bed by 23:50 tonight. Bedtime varies +/-77 min; aim within 30.",
  "deviations": [
    {"label": "bedtime regularity", "value": "77 min", "vs_avg": "vs 30 target",
     "arrow": "up", "state": "ALERT"},
    {"label": "easy share", "value": "10 %", "vs_avg": "vs 70 % target",
     "arrow": "down", "state": "WATCH"},
    {"label": "sleep 7n", "value": "6h35", "vs_avg": "-0.2 h vs avg",
     "arrow": "down", "state": "OK"},
    {"label": "HRV 7n", "value": "81 ms", "vs_avg": "+2 ms (z7 +0.2)",
     "arrow": "up", "state": "GOOD"}
  ],
  "signals": [
    {"key": "S3", "domain": "sleep", "label": "bedtime regularity", "state": "ALERT",
     "display": "77 min", "vs_avg": "vs 30 target", "z": null, "arrow": "up"},
    {"key": "F6", "domain": "fitness", "label": "easy share", "state": "WATCH",
     "display": "10 %", "vs_avg": "vs 70 % target", "z": null, "arrow": "down"}
  ],
  "tiles": [
    {"key": "sleep", "title": "SLEEP", "value": "6h12", "arrow": "down", "state": "WATCH",
     "lines": ["7n 6h35", "-0.2 h vs avg"]},
    {"key": "sleep_hr", "title": "SLEEP HR", "value": "49", "arrow": "flat", "state": "OK",
     "lines": ["60d 50", "min 43"]},
    {"key": "hrv", "title": "HRV 7n", "value": "81 ms", "arrow": "up", "state": "GOOD",
     "lines": ["60d 79", "+/-11"]},
    {"key": "weight", "title": "WEIGHT 7d", "value": "--", "arrow": "flat", "state": "BLANK",
     "lines": ["no data", "n 0/7"]},
    {"key": "last_run", "title": "LAST RUN", "value": "2d", "arrow": "flat", "state": "OK",
     "lines": ["0.7/wk", "tgt 3/wk"]}
  ],
  "sleep": {
    "goal_h": 7.5, "target_bedtime": "23:50", "median_bedtime": 24.67,
    "mean_7n_min": 395.0, "mean_60d_min": 401.0, "debt_14n_h": 4.2,
    "bedtime_sd_min": 77.0, "social_jet_lag_h": 1.67,
    "score": {"duration": 37.0, "consistency": 5.0, "interruptions": 20.0,
              "total": 62.0, "lever": "consistency"},
    "nights": [
      {"date": "2026-09-05", "asleep_min": 402.0, "deep_min": 63.0, "rem_min": 91.0,
       "core_min": 248.0, "awake_min": 14.0, "interruptions": 2, "naps_min": 0.0,
       "bedtime": 24.5, "waketime": 7.2, "midsleep": 27.85, "hr_min_frac": 0.42,
       "state": "OK"},
      {"date": "2026-09-06", "asleep_min": 372.0, "deep_min": 58.0, "rem_min": 84.0,
       "core_min": 230.0, "awake_min": 11.0, "interruptions": 3, "naps_min": 0.0,
       "bedtime": 25.6, "waketime": 7.8, "midsleep": 28.7, "hr_min_frac": 0.74,
       "state": "WATCH"}
    ]
  },
  "heart": {
    "sleep_hr_mean_60d": 50.1, "sleep_hr_sd_60d": 3.8, "hrv_mean_60d": 78.6,
    "hrv_7n_sd": 11.0, "rhr_latest": 52.0, "rhr_60d": 53.0, "resp_latest": 16.4,
    "hrr_last4": [15.5, 35.9, 29.8, 28.0],
    "nights": [
      {"date": "2026-09-05", "sleep_avg_hr": 49.0, "sleep_min_hr": 43.0, "hrv": 84.0,
       "resp": 16.2, "rhr": 52.0},
      {"date": "2026-09-06", "sleep_avg_hr": 51.0, "sleep_min_hr": 44.0, "hrv": 78.0,
       "resp": 16.4, "rhr": null}
    ]
  },
  "exercise": {
    "zone_edges": [141.2, 155.9, 170.6, 185.3], "hr_max": 200, "rhr_anchor": 53.0,
    "easy_share_60d": 0.10, "easy_share_target": 0.7, "vdot": 39.1,
    "capacity": {"metric": "speed_at_hr", "speed_at_hr_latest": 2.9,
                 "speed_at_hr_vs_avg_pct": 1.0, "speed_at_hr_trend": "flat",
                 "speed_at_hr_n_of_10": 6, "ef_latest": 0.017, "ef_vs_avg_pct": 0.5,
                 "hrr_latest": 28.0, "hrr_mean": 32.7, "hrr_sd": 7.0, "hrr_z": -0.67,
                 "hrr_last4": [15.5, 35.9, 29.8, 28.0], "hrr_n": 24, "f1_z": 0.3,
                 "capacity_z": -0.2, "durability_28d_min": 28.0, "durability_60d_min": 36.0},
    "consistency": {"days_since_run": 2, "days_since_strength": 14,
                    "runs_per_week_4w": 0.75, "strength_per_week_4w": 0.25,
                    "runs_this_week": 0, "strength_this_week": 0,
                    "longest_run_gap_60d": 21, "runs_per_week_target": 3,
                    "strength_per_week_target": 2},
    "load": {"load_week": 180.0, "load_4w_avg": 95.0, "load_7d": 180.0,
             "load_28d": 380.0, "acwr": null, "workouts_28d": 4},
    "weekly": [
      {"week_start": "2026-08-17", "trimp": 0.0, "runs": 0, "strength": 0, "miles": 0.0},
      {"week_start": "2026-08-24", "trimp": 145.0, "runs": 3, "strength": 1, "miles": 5.1},
      {"week_start": "2026-08-31", "trimp": 120.0, "runs": 2, "strength": 0, "miles": 4.4},
      {"week_start": "2026-09-07", "trimp": 0.0, "runs": 0, "strength": 0, "miles": 0.0}
    ],
    "runs": [
      {"date": "2026-09-05", "miles": 2.41, "pace_min_mi": 11.6, "avg_hr": 162.0,
       "max_hr": 194.0, "hrr": 28.0, "speed_at_hr": 2.9, "ef": 0.0176,
       "zones": [0.0, 2.0, 12.0, 12.0, 2.0], "state": "WATCH"},
      {"date": "2026-09-01", "miles": 2.01, "pace_min_mi": 8.5, "avg_hr": 183.0,
       "max_hr": 198.0, "hrr": 29.8, "speed_at_hr": null, "ef": 0.0181,
       "zones": [0.0, 0.0, 1.0, 8.0, 8.0], "state": "WATCH"}
    ]
  },
  "weight": {
    "readings": [], "last_kg": 81.6, "last_date": "2026-01-08", "n_7": 0, "n_28": 0,
    "weight_7d": null, "weight_28d": null, "week_change": null, "bmi": null,
    "weigh_ins_7d": 0, "drift_kg": null, "bmi_stale": true
  },
  "coverage": {
    "nights_with_data_7": 4, "nights_with_data_60": 20, "weigh_ins_7d": 0,
    "last_import_at": "2026-09-07 07:12:00-04:00",
    "last_hr_sample_at": "2026-09-07 06:58:00-04:00",
    "last_weight_at": "2026-01-08"
  }
}
```

- [ ] **Step 2: Write the failing test**

`runner/tests/unit/telemetry/test_health_board.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atlas.telemetry.infrastructure.health_board import HealthBoardReader

FIXTURE = Path(__file__).parents[2] / "fixtures" / "health" / "board.json"
NOW = datetime(2026, 9, 7, 15, 0, tzinfo=UTC)


def test_missing_file_is_unavailable_not_an_error(tmp_path: Path) -> None:
    state = HealthBoardReader(tmp_path / "nope.json", "UTC").read(NOW)
    assert state.available is False
    assert state.document is None
    assert "no health board" in state.detail


def test_unparseable_file_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "board.json"
    path.write_text("{not json", encoding="utf-8")
    state = HealthBoardReader(path, "UTC").read(NOW)
    assert state.available is False and state.document is None


def test_a_document_of_the_wrong_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "board.json"
    path.write_text(json.dumps({"schema": 99}), encoding="utf-8")
    state = HealthBoardReader(path, "UTC").read(NOW)
    assert state.available is False and "schema" in state.detail


def test_the_fixture_reads_and_is_fresh() -> None:
    state = HealthBoardReader(FIXTURE, "America/New_York").read(
        datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    )
    assert state.available is True
    assert state.stale is False
    assert state.document is not None
    assert state.document["status"]["overall"] == "ALERT"
    assert state.generated_at == datetime.fromisoformat("2026-09-07T10:00:00-04:00")


def test_an_old_document_is_still_served_but_marked_stale() -> None:
    state = HealthBoardReader(FIXTURE, "America/New_York").read(
        datetime(2026, 9, 7, 10, 0, tzinfo=UTC) + timedelta(days=3)
    )
    assert state.available is True and state.stale is True
    assert state.document is not None
```

- [ ] **Step 3: Run, expect ImportError**

Run from `runner/`: `uv run --frozen pytest tests/unit/telemetry/test_health_board.py -v`

- [ ] **Step 4: Implement the reader**

`runner/src/atlas/telemetry/infrastructure/health_board.py`:

```python
"""The health screen's document, as the board sees it.

`atlas-health` (the separate dominickdupuy/health repo, run as a hosted repo)
writes /var/lib/atlas-health/board.json once a night. The runner does not
compute anything here and never touches Postgres: it reads one file and
passes it through, so the API suite stays hermetic and a broken analysis job
can only make the health screen say so.

Fault-tolerant for the same reason system_metrics.py and hosted_repos.py are:
a missing or half-written file must not take down /api/status.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STALE_AFTER = timedelta(hours=30)
"""The job runs at 10:00, so yesterday's document is normal until this
morning's run should have landed. Beyond that the screen says so rather than
presenting month-old numbers as today's."""


@dataclass(frozen=True)
class HealthBoardState:
    available: bool
    detail: str
    document: dict[str, Any] | None = None
    generated_at: datetime | None = None
    stale: bool = False


class HealthBoardReader:
    def __init__(self, path: Path, tz: str, stale_after: timedelta = STALE_AFTER) -> None:
        self._path = path
        self._tz = ZoneInfo(tz)
        self._stale_after = stale_after

    def _generated_at(self, raw: object) -> datetime | None:
        if not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed.replace(tzinfo=self._tz) if parsed.tzinfo is None else parsed

    def read(self, now: datetime) -> HealthBoardState:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return HealthBoardState(False, f"no health board at {self._path}")
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.debug("health board: unparseable %s: %s", self._path, exc)
            return HealthBoardState(False, "health board is unreadable")
        if not isinstance(document, dict):
            return HealthBoardState(False, "health board is not an object")
        schema = document.get("schema")
        if schema != SCHEMA_VERSION:
            return HealthBoardState(
                False, f"health board schema {schema!r}, expected {SCHEMA_VERSION}"
            )
        generated_at = self._generated_at(document.get("generated_at"))
        stale = generated_at is None or (now - generated_at) > self._stale_after
        detail = "nightly analysis"
        if stale and generated_at is not None:
            hours = (now - generated_at).total_seconds() / 3600
            detail = f"last computed {hours:.0f} h ago"
        return HealthBoardState(True, detail, document, generated_at, stale)
```

- [ ] **Step 5: Add the setting and wire the reader**

In `runner/src/atlas/config.py`, directly after the `repos_state_dir` setting:

```python
    # Written nightly by the health/ package (a hosted repo, not an atlas job).
    # The runner only ever READS it: nothing here computes health.
    health_board_path: Path = Path("/var/lib/atlas-health/board.json")
```

In `runner/src/atlas/bootstrap/container.py`, import the reader beside the hosted-repo one:

```python
from atlas.telemetry.infrastructure.health_board import HealthBoardReader
```

add the field to `Application`, after `hosted_repos`:

```python
    health_board: HealthBoardReader
```

and construct it in `build_application`, after the `hosted_repos=` argument:

```python
        health_board=HealthBoardReader(settings.health_board_path, settings.tz),
```

- [ ] **Step 6: Point the test settings at a temp path**

In `runner/tests/integration/conftest.py`, inside the `Settings(...)` call, beside
`repos_state_dir`:

```python
        # Same reasoning as the repo paths above: the default is a real host
        # file, and a suite that reads it would pass or fail depending on what
        # this machine computed last night.
        health_board_path=tmp_path / "health-board.json",
```

- [ ] **Step 7: Run, expect PASS; lint; type-check**

Run from `runner/`:
`uv run --frozen pytest -m "not mqtt" && uv run --frozen mypy src tests && uv run --frozen ruff check . && uv run --frozen ruff format --check .`
Expected: PASS, clean.

- [ ] **Step 8: Commit**

```bash
git add runner/src/atlas/telemetry/infrastructure/health_board.py runner/src/atlas/config.py \
        runner/src/atlas/bootstrap/container.py runner/tests/unit/telemetry/test_health_board.py \
        runner/tests/fixtures/health/board.json runner/tests/integration/conftest.py
git commit -m "runner: read the nightly health board document, fault-tolerantly"
```

---

### Task 16: `/api/status` carries the health document

**Files:**
- Modify: `runner/src/atlas/presentation/http/status.py`,
  `runner/src/atlas/presentation/http/runs.py`
- Test: `runner/tests/integration/test_api_status.py` (add cases),
  `runner/tests/integration/test_dashboard_runs_timeline.py` (add one case)

**Interfaces:**
- Produces: `class HealthInfo(_Frozen)` with `available: bool`, `detail: str`,
  `generated_at: datetime | None = None`, `stale: bool = False`,
  `document: dict[str, Any] | None = None`; and `StatusSnapshot.health: HealthInfo`.
- The runner does not model the health schema. The document is passed through as an opaque
  object so the analysis package can add a field without a runner release — the same reason the
  board reads `board.json` keys directly in Tasks 18 and 19.
- Also produces the RUNS-panel figure labels for the nightly job: `("nights", "{n} nights")` and
  `("alerts", "{n} alerts")` appended to `_FIGURE_LABELS` in `runs.py`.

- [ ] **Step 1: Write the failing tests**

Append to `runner/tests/integration/test_api_status.py`:

```python
async def test_status_reports_no_health_board_when_none_exists(client: AsyncClient) -> None:
    response = await client.get("/api/status", headers=AUTH)
    health = response.json()["health"]
    assert health["available"] is False
    assert health["document"] is None
    assert "no health board" in health["detail"]


async def test_status_passes_the_health_document_through(
    application: Application, client: AsyncClient
) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "health" / "board.json"
    application.settings.health_board_path.write_text(
        fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )
    response = await client.get("/api/status", headers=AUTH)
    health = response.json()["health"]
    assert health["available"] is True
    assert health["document"]["status"]["overall"] == "ALERT"
    assert health["document"]["tiles"][0]["key"] == "sleep"
    # Passed through, not re-modelled: a key the runner has never heard of survives.
    assert health["document"]["coverage"]["nights_with_data_60"] == 20


async def test_a_broken_health_board_does_not_break_the_status(
    application: Application, client: AsyncClient
) -> None:
    application.settings.health_board_path.write_text("{ truncated", encoding="utf-8")
    response = await client.get("/api/status", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["health"]["available"] is False
```

with `from pathlib import Path` added to that file's imports.

Append to `runner/tests/integration/test_dashboard_runs_timeline.py`:

```python
async def test_health_job_figures_reach_the_runs_panel(
    application: Application, client: AsyncClient
) -> None:
    """The nightly analysis reports its figures like any hosted repo."""
    state_dir = application.settings.repos_state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    application.settings.repos_registry.write_text(
        '[[repo]]\nname = "health-nightly"\npath = "/opt/health"\nkind = "job"\n',
        encoding="utf-8",
    )
    (state_dir / "health-nightly.json").write_text(
        json.dumps({
            "status": "ok", "trigger": "cron", "started": "2026-09-07T10:00:00",
            "duration_seconds": 12.0, "exit": 0,
            "summary": {"nights": 20, "alerts": 1, "runs": 8, "weigh_ins": 0},
        }),
        encoding="utf-8",
    )
    response = await client.get("/api/status", headers=AUTH)
    history = response.json()["run_timeline"]["history"]
    entry = next(item for item in history if item["name"] == "health-nightly")
    assert "20 nights" in entry["detail"] and "1 alerts" in entry["detail"]
```

with `import json` and the `Application` import present in that file.

- [ ] **Step 2: Run, expect failure**

Run from `runner/`:
`uv run --frozen pytest tests/integration/test_api_status.py -k health -v`
Expected: FAIL with `KeyError: 'health'`.

- [ ] **Step 3: Add `HealthInfo` and put it on the snapshot**

In `runner/src/atlas/presentation/http/status.py`, add `from typing import Any` to the imports,
then define the model beside `WeatherInfo`:

```python
class HealthInfo(_Frozen):
    """The nightly health document, passed through untouched.

    The runner does not model this schema. It is authored by the health/
    package and read by board.js; re-declaring every field here would mean a
    runner release for every analysis change, and would give the board two
    definitions of one document to disagree about.
    """

    available: bool
    detail: str
    generated_at: datetime | None = None
    stale: bool = False
    document: dict[str, Any] | None = None
```

Add the field to `StatusSnapshot`, after `weather`:

```python
    health: HealthInfo
```

and in `StatusAssembler.snapshot`, before the `return StatusSnapshot(`:

```python
        health_state = app.health_board.read(now)
```

then pass it, after `weather=weather,`:

```python
            health=HealthInfo(
                available=health_state.available,
                detail=health_state.detail,
                generated_at=health_state.generated_at,
                stale=health_state.stale,
                document=health_state.document,
            ),
```

- [ ] **Step 4: Add the two figure labels**

In `runner/src/atlas/presentation/http/runs.py`, extend `_FIGURE_LABELS`:

```python
_FIGURE_LABELS = (
    ("transactions", "{n:,} transactions"),
    ("reviewed", "{n:,} reviewed"),
    ("uncategorized", "{n:,} uncategorized"),
    ("decisions", "{n} decisions"),
    ("rules", "{n} new rules"),
    ("nights", "{n} nights"),
    ("alerts", "{n} alerts"),
)
```

- [ ] **Step 5: Run, expect PASS; lint; type-check**

Run from `runner/`:
`uv run --frozen pytest -m "not mqtt" && uv run --frozen mypy src tests && uv run --frozen ruff check . && uv run --frozen ruff format --check .`
Expected: PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add runner/src/atlas/presentation/http/status.py runner/src/atlas/presentation/http/runs.py \
        runner/tests/integration/test_api_status.py \
        runner/tests/integration/test_dashboard_runs_timeline.py
git commit -m "runner: serve the health document on /api/status and label its run figures"
```

---

## Part D: the second screen

### Task 17: A second screen, switched by the dial's keys and scrolled by the dial

**Files:**
- Modify: `runner/src/atlas/presentation/templates/board.html`,
  `runner/src/atlas/presentation/static/board.js`,
  `runner/src/atlas/presentation/static/board.css`

**Interfaces:**
- Produces, in `board.js`: `setScreen(name)` (`"ops"` or `"health"`), `scrollHealth(step)`,
  `armHealthTimers()`, and the module-level `screen` variable. `renderHealth(s)` is declared here
  as a stub that draws the unavailable state only; Tasks 18 and 19 fill it in.
- Produces, in `board.html`: `<div class="main main-health" id="main-health" hidden>` containing
  `#health-tiles`, `#health-action`, `#health-sleep`, `#health-exercise`, `#health-weight`,
  `#health-detail`, `#health-footer`, and `#health-unavailable`.
- Behaviour (spec §7): key 1 (`Shift+Alt+Digit1`) shows ops, key 2 shows health and lights its
  LED; the dial scrolls the health region 140 px per detent; 60 s after the last scroll the
  region snaps back to the top; 10 minutes after the screen was opened it returns to ops. The
  dial's round screen reads `HEALTH` while the screen is up.
- Above the fold means the first 984 px under the 96 px header: the tiles row, the action strip
  and the sleep block must fit in that height without scrolling.

- [ ] **Step 1: Add the health region to `board.html`**

Immediately after the closing `</div>` of `<div class="main" id="main">` (that is, after the
`</aside>` and its parent div), insert:

```html
        <!--
          The health screen (spec §7). A second `main` region, hidden until
          the dial's top-right key selects it. It is the only scrolling region
          on the board: everything needed to answer "how did I sleep, how is
          my heart, what should I change" is above the fold, and the dial
          reveals detail below it. Still output only — the dial is a device
          beside the screen, not a control on it.
        -->
        <div class="main main-health" id="main-health" hidden>
          <p class="health-unavailable" id="health-unavailable" hidden></p>

          <section class="health-tiles" id="health-tiles"></section>
          <section class="health-action" id="health-action"></section>

          <section class="panel health-block" id="health-sleep">
            <div class="panel-head">
              <span class="panel-title">SLEEP · 60 NIGHTS</span>
              <span class="panel-source" id="health-sleep-source">—</span>
            </div>
            <div class="health-split" id="health-sleep-body"></div>
          </section>

          <section class="panel health-block" id="health-exercise">
            <div class="panel-head">
              <span class="panel-title">EXERCISE</span>
              <span class="panel-source" id="health-exercise-source">—</span>
            </div>
            <div class="health-split" id="health-exercise-body"></div>
          </section>

          <section class="panel health-block" id="health-weight">
            <div class="panel-head">
              <span class="panel-title">WEIGHT · 60 DAYS</span>
              <span class="panel-source" id="health-weight-source">—</span>
            </div>
            <div class="health-weight-body" id="health-weight-body"></div>
          </section>

          <section class="panel health-block" id="health-detail">
            <div class="panel-head">
              <span class="panel-title">SLEEP DETAIL</span>
              <span class="panel-source" id="health-detail-source">—</span>
            </div>
            <div class="health-split" id="health-detail-body"></div>
          </section>

          <p class="health-footer" id="health-footer"></p>
        </div>
```

- [ ] **Step 2: Add the screen chrome to `board.css`**

Append to `runner/src/atlas/presentation/static/board.css`:

```css
/* --- health screen -------------------------------------------------------- */

/* The one scrolling region on the board (spec §7). Everything that answers
   the three questions sits in the first 984px; the dial reveals the rest. */
.main-health {
  flex-direction: column;
  overflow-y: auto;
  overflow-x: hidden;
  gap: 12px;
  scroll-behavior: smooth;
  /* No pointer here either, and a visible bar on a wall display is noise. */
  scrollbar-width: none;
}

.main-health::-webkit-scrollbar {
  display: none;
}

.health-unavailable {
  margin: 0;
  padding: 22px 24px;
  border-radius: var(--radius);
  background: var(--glass);
  border: 1px solid var(--glass-edge);
  color: var(--ink-55);
  font-size: 20px;
}

.health-tiles {
  flex: none;
  display: grid;
  grid-template-columns: 2fr 1fr 1fr 1fr 1fr 1fr;
  gap: 12px;
  height: 128px;
}

.health-tile {
  border-radius: var(--radius);
  background: var(--glass);
  border: 1px solid var(--glass-edge);
  box-shadow: var(--glass-inset);
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  overflow: hidden;
}

.health-tile-title {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.14em;
  color: var(--ink-50);
}

.health-tile-value {
  font-size: 40px;
  font-weight: 300;
  letter-spacing: -0.02em;
  line-height: 1.05;
}

.health-tile-line {
  font-size: 13px;
  color: var(--ink-45);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* State is the whole point of this screen, so it colours values and chips and
   nothing else: a red border on every panel would say "everything is wrong". */
[data-state="ALERT"] .health-tile-value,
.health-state[data-state="ALERT"] {
  color: var(--red-text);
}

[data-state="WATCH"] .health-tile-value,
.health-state[data-state="WATCH"] {
  color: var(--amber-text);
}

[data-state="GOOD"] .health-tile-value,
.health-state[data-state="GOOD"] {
  color: var(--green-text);
}

.health-state[data-state="NO DATA"],
.health-state[data-state="BLANK"] {
  color: var(--ink-35);
  font-style: italic;
}

.health-chips {
  display: flex;
  gap: 14px;
  font-size: 15px;
  color: var(--ink-60);
  flex-wrap: wrap;
}

.health-drivers {
  font-size: 13px;
  color: var(--ink-42);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.health-action {
  flex: none;
  min-height: 160px;
  border-radius: var(--radius);
  background: var(--glass);
  border: 1px solid var(--glass-edge);
  box-shadow: var(--glass-inset);
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.health-action-line {
  font-size: 24px;
  font-weight: 400;
}

.health-dev {
  display: grid;
  grid-template-columns: 220px 110px 1fr 24px 80px;
  gap: 8px;
  font-size: 15px;
  color: var(--ink-55);
  align-items: baseline;
}

.health-block {
  flex: none;
  padding: 16px 20px;
  gap: 10px;
}

.health-split {
  display: grid;
  grid-template-columns: 900px 1fr;
  gap: 16px;
  min-height: 0;
}

.health-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.health-sub {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: var(--ink-50);
}

.health-note {
  font-size: 13px;
  color: var(--ink-42);
}

.health-chart {
  width: 100%;
  display: block;
}

.health-footer {
  flex: none;
  margin: 0;
  padding: 0 6px 8px;
  font-size: 13px;
  color: var(--ink-35);
}

.health-rows {
  display: grid;
  gap: 4px;
  font-size: 14px;
  color: var(--ink-55);
}

.health-row {
  display: grid;
  grid-template-columns: 88px 60px 66px 74px 60px 1fr;
  gap: 8px;
  align-items: center;
}

.health-arrow[data-arrow="up"]::after {
  content: "^";
}

.health-arrow[data-arrow="down"]::after {
  content: "v";
}

.health-arrow[data-arrow="flat"]::after {
  content: "-";
}
```

- [ ] **Step 3: Write the screen switching in `board.js`**

Add these constants beside `DAY_VIEW_IDLE_MS`:

```js
  // The health screen (spec §7). The dial scrolls it; it snaps back to the
  // top a minute after the last detent, and hands the wall back to the ops
  // board after ten minutes, so a screen left on health is not what anyone
  // finds tomorrow morning.
  var HEALTH_SCROLL_PX = 140;
  var HEALTH_SNAP_MS = 60000;
  var HEALTH_RETURN_MS = 600000;
```

and these variables beside `dayView`:

```js
  var screenName = "ops";
  var healthSnapTimer = null;
  var healthReturnTimer = null;
```

Add the functions above `renderTimeline`:

```js
  function setScreen(name) {
    if (screenName === name) {
      // Pressing the key for the screen already up restarts its clock rather
      // than doing nothing: the person is standing there, reading it.
      if (name === "health") armHealthTimers();
      return;
    }
    screenName = name;
    var health = name === "health";
    $("main").hidden = health;
    $("main-health").hidden = !health;
    [1, 2].forEach(function (key) {
      var node = document.querySelector('.deck-key[data-key="' + key + '"]');
      if (node === null) return;
      if ((key === 2) === health) node.setAttribute("data-active", "true");
      else node.removeAttribute("data-active");
    });
    if (health) {
      $("main-health").scrollTop = 0;
      if (lastSnapshot !== null) renderHealth(lastSnapshot);
      armHealthTimers();
    } else {
      if (healthSnapTimer !== null) clearTimeout(healthSnapTimer);
      if (healthReturnTimer !== null) clearTimeout(healthReturnTimer);
      healthSnapTimer = null;
      healthReturnTimer = null;
    }
    deckSay(health ? "HEALTH" : "OPS");
  }

  function armHealthTimers() {
    if (healthReturnTimer !== null) clearTimeout(healthReturnTimer);
    healthReturnTimer = setTimeout(function () {
      healthReturnTimer = null;
      setScreen("ops");
    }, HEALTH_RETURN_MS);
  }

  function armHealthSnap() {
    if (healthSnapTimer !== null) clearTimeout(healthSnapTimer);
    healthSnapTimer = setTimeout(function () {
      healthSnapTimer = null;
      $("main-health").scrollTop = 0;
    }, HEALTH_SNAP_MS);
  }

  function scrollHealth(step) {
    var region = $("main-health");
    var limit = region.scrollHeight - region.clientHeight;
    region.scrollTop = Math.max(0, Math.min(limit, region.scrollTop + step * HEALTH_SCROLL_PX));
    armHealthSnap();
    armHealthTimers();
  }
```

Replace the dial and key handling inside `onDialKey`'s `if (prefixed) {` block with:

```js
      var step = e.code === DIAL_BACK ? -1 : e.code === DIAL_FORWARD ? 1 : 0;
      if (step !== 0) {
        e.preventDefault();
        deckTurn(step);
        if (screenName === "health") scrollHealth(step);
        else shiftDays(step);
        return;
      }

      var key = DECK_KEY_CODES.indexOf(e.code) + 1;
      if (key > 0) {
        e.preventDefault();
        deckPressKey(key);
        // Two screens exist now. The other two keys still say they arrived
        // and nothing more, rather than pretending to switch to something
        // that does not exist yet.
        if (key === DECK_HOME_KEY) setScreen("ops");
        else if (key === 2) setScreen("health");
        else deckSay("K" + key);
        return;
      }
```

In `renderHeader`, immediately before `deckModeLabel = label;`:

```js
    // While the health screen is up the dial's face says so; the display mode
    // takes the face back when the board returns to ops.
    if (screenName === "health") label = "HEALTH";
```

Add the stub renderer above `render`:

```js
  /* Tasks 18 and 19 fill this in. Until then the screen is honest about
     having nothing: an empty region would read as "all clear". */
  function renderHealth(s) {
    var health = s.health || { available: false, detail: "no health data" };
    var note = $("health-unavailable");
    var missing = !health.available || !health.document;
    note.hidden = !missing;
    if (missing) {
      text(note, "No health board: " + (health.detail || "unknown"));
    }
    ["health-tiles", "health-sleep", "health-exercise", "health-weight", "health-detail"].forEach(
      function (id) {
        $(id).hidden = missing;
      }
    );
    $("health-action").hidden = missing;
    $("health-footer").hidden = missing;
  }
```

and call it from `render`, after `renderSystem(s);`:

```js
    if (screenName === "health") renderHealth(s);
```

- [ ] **Step 4: Check it in the browser**

Serve the dev profile with the fixture document and drive the keys:

```bash
cd runner
ATLAS_PROFILE=dev ATLAS_API_TOKEN=dev-token \
  ATLAS_HEALTH_BOARD_PATH=$PWD/tests/fixtures/health/board.json \
  uv run --frozen atlas serve
```

Open `http://127.0.0.1:8100/`, then press Shift+Alt+2. Expected: the ops panels disappear, the
health region shows the placeholder text from Task 12's document (nothing is drawn yet, but the
"No health board" note must NOT be showing), the drawn dial's top-right LED lights and its face
reads HEALTH. Shift+Alt+1 returns to ops. Turning the dial (Shift+Alt+5 / 6) scrolls nothing
yet because the region is still empty — that is expected until Task 18.

- [ ] **Step 5: Run the runner suite (asset version changes, so it must still pass)**

Run from `runner/`:
`uv run --frozen pytest -m "not mqtt" && uv run --frozen ruff check . && uv run --frozen ruff format --check .`
Expected: PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add runner/src/atlas/presentation/templates/board.html \
        runner/src/atlas/presentation/static/board.js \
        runner/src/atlas/presentation/static/board.css
git commit -m "board: a second screen on ColoPlay key 2, scrolled by the dial"
```

---
### Task 18: Above the fold — STATUS, the tiles, the action strip, the sleep block

**Files:**
- Modify: `runner/src/atlas/presentation/static/board.js` (replace the Task 17 stub),
  `runner/src/atlas/presentation/static/board.css` (append)

**Interfaces:**
- Consumes from the document (Task 12): `status`, `action`, `deviations`, `tiles`, `sleep`,
  `heart`, `coverage`.
- Produces, in `board.js`: `renderHealth(s)` in full for the above-the-fold region, plus the
  helpers `healthSvg(width, height)`, `num(value, digits, fallback)`, `dayKeys(endIso, days)`,
  `sparkline(values, options)`, `renderHealthTiles(doc)`, `renderHealthAction(doc)`,
  `renderHealthSleep(doc)`.
- Layout budget (spec §7): tiles 128 px, action strip 160 px, sleep block about 600 px, inside
  the 984 px above the fold. Nothing here may scroll on its own.

- [ ] **Step 1: Add the drawing helpers**

Insert into `board.js` above `renderHealth`:

```js
  // --- health screen: drawing ---------------------------------------------

  /* Every chart on this screen is hand-drawn SVG on the canvas's own pixel
     grid, exactly like the weather chart: no library reaches this Pi, and a
     fixed viewBox keeps the type scale identical to the rest of the board. */
  function healthSvg(width, height) {
    return svgEl("svg", {
      class: "health-chart",
      viewBox: "0 0 " + width + " " + height,
      width: String(width),
      height: String(height),
      preserveAspectRatio: "none",
    });
  }

  function num(value, digits, fallback) {
    if (value === null || value === undefined || isNaN(value)) return fallback || "—";
    return Number(value).toFixed(digits === undefined ? 0 : digits);
  }

  /* The last `days` calendar dates ending at the document's own timestamp, as
     YYYY-MM-DD. The night lists carry only nights the watch was worn, so the
     charts need the calendar to put a gap where a night is missing — which is
     45% of them, and the single most important caveat on this screen. */
  function dayKeys(endIso, days) {
    var end = new Date(endIso);
    var keys = [];
    for (var i = days - 1; i >= 0; i--) {
      var day = new Date(end.getTime() - i * 86400000);
      keys.push(
        day.getFullYear() + "-" + pad(day.getMonth() + 1) + "-" + pad(day.getDate())
      );
    }
    return keys;
  }

  /* A line over a fixed number of slots, with gaps where a value is missing.
     `options`: {width, height, values (array of number|null), min, max, band}
     where `band` is an optional {low, high} shaded range. */
  function sparkline(options) {
    var w = options.width;
    var h = options.height;
    var svg = healthSvg(w, h);
    var values = options.values;
    var min = options.min;
    var max = options.max;
    var span = max - min || 1;
    var step = values.length > 1 ? w / (values.length - 1) : w;

    function y(value) {
      return h - ((value - min) / span) * h;
    }

    if (options.band) {
      svg.appendChild(
        svgEl("rect", {
          x: "0",
          y: String(y(options.band.high)),
          width: String(w),
          height: String(Math.max(1, y(options.band.low) - y(options.band.high))),
          class: "health-band",
        })
      );
    }

    var run = [];
    values.forEach(function (value, index) {
      if (value === null || value === undefined) {
        if (run.length > 1) {
          svg.appendChild(
            svgEl("polyline", {
              points: run.join(" "),
              class: "health-line",
              "vector-effect": "non-scaling-stroke",
            })
          );
        }
        run = [];
        return;
      }
      run.push(index * step + "," + y(value));
    });
    if (run.length > 1) {
      svg.appendChild(
        svgEl("polyline", {
          points: run.join(" "),
          class: "health-line",
          "vector-effect": "non-scaling-stroke",
        })
      );
    }
    return svg;
  }
```

- [ ] **Step 2: Write the tiles and the action strip**

Insert below the helpers:

```js
  function tileNode(title, value, arrow, state, lines) {
    var node = el("div", "health-tile");
    node.setAttribute("data-state", state || "OK");
    node.appendChild(el("div", "health-tile-title", title));
    var head = el("div", "health-tile-value", value);
    if (arrow && arrow !== "flat") {
      var mark = el("span", "health-arrow");
      mark.setAttribute("data-arrow", arrow);
      head.appendChild(mark);
    }
    node.appendChild(head);
    (lines || []).forEach(function (line) {
      node.appendChild(el("div", "health-tile-line", line));
    });
    return node;
  }

  function renderHealthTiles(doc) {
    var host = $("health-tiles");
    clear(host);

    // The STATUS tile is the only tile with chips, and the only one that
    // never shows a raw number other than its two drivers (spec §4.8.6).
    var status = el("div", "health-tile health-tile-status");
    status.setAttribute("data-state", doc.status.overall);
    status.appendChild(el("div", "health-tile-title", "STATUS"));
    status.appendChild(el("div", "health-tile-value", doc.status.overall));
    var chips = el("div", "health-chips");
    ["sleep", "heart", "fitness", "weight"].forEach(function (domain) {
      var state = doc.status.domains[domain] || "NO DATA";
      var chip = el(
        "span",
        "health-state",
        domain.charAt(0).toUpperCase() + domain.slice(1) + " " + state
      );
      chip.setAttribute("data-state", state);
      chips.appendChild(chip);
    });
    status.appendChild(chips);
    status.appendChild(el("div", "health-drivers", (doc.status.drivers || []).join(" · ")));
    host.appendChild(status);

    (doc.tiles || []).forEach(function (tile) {
      host.appendChild(tileNode(tile.title, tile.value, tile.arrow, tile.state, tile.lines));
    });
  }

  function renderHealthAction(doc) {
    var host = $("health-action");
    clear(host);
    host.appendChild(el("div", "health-action-line", "> " + doc.action));
    (doc.deviations || []).forEach(function (row) {
      var line = el("div", "health-dev");
      line.appendChild(el("span", null, row.label));
      line.appendChild(el("span", null, row.value));
      line.appendChild(el("span", null, row.vs_avg));
      var mark = el("span", "health-arrow");
      mark.setAttribute("data-arrow", row.arrow || "flat");
      line.appendChild(mark);
      var state = el("span", "health-state", row.state);
      state.setAttribute("data-state", row.state);
      line.appendChild(state);
      host.appendChild(line);
    });
    var coverage = doc.coverage || {};
    host.appendChild(
      el(
        "div",
        "health-note",
        "watch worn " +
          num(coverage.nights_with_data_7, 0, "0") +
          "/7 nights · " +
          num(coverage.nights_with_data_60, 0, "0") +
          "/60"
      )
    );
  }
```

- [ ] **Step 3: Write the sleep block**

Insert below:

```js
  /* Stacked deep/REM/core per night on a calendar axis, so the 45% of nights
     without the watch appear as gaps rather than being silently closed up.
     A hollow marker sits on the baseline for each of those nights. */
  function sleepBars(doc) {
    var width = 860;
    var height = 300;
    var svg = healthSvg(width, height);
    var keys = dayKeys(doc.generated_at, 60);
    var byDate = {};
    (doc.sleep.nights || []).forEach(function (night) {
      byDate[night.date] = night;
    });
    var top = 600; // 10 h; every recorded night fits, and the goal line lands high
    var slot = width / keys.length;
    var barWidth = Math.max(4, slot - 3);

    function y(minutes) {
      return height - (Math.min(minutes, top) / top) * height;
    }

    var goal = doc.sleep.goal_h * 60;
    svg.appendChild(
      svgEl("line", {
        x1: "0", x2: String(width), y1: String(y(goal)), y2: String(y(goal)),
        class: "health-goal", "vector-effect": "non-scaling-stroke",
      })
    );

    keys.forEach(function (key, index) {
      var x = index * slot;
      var night = byDate[key];
      if (!night) {
        svg.appendChild(
          svgEl("circle", {
            cx: String(x + barWidth / 2), cy: String(height - 5), r: "3",
            class: "health-missing",
          })
        );
        return;
      }
      var parts = [
        { minutes: night.deep_min, cls: "health-deep" },
        { minutes: night.core_min, cls: "health-core" },
        { minutes: night.rem_min, cls: "health-rem" },
      ];
      var base = 0;
      parts.forEach(function (part) {
        var minutes = part.minutes || 0;
        if (minutes <= 0) return;
        svg.appendChild(
          svgEl("rect", {
            x: String(x), width: String(barWidth),
            y: String(y(base + minutes)),
            height: String(Math.max(1, y(base) - y(base + minutes))),
            class: part.cls, "data-state": night.state,
          })
        );
        base += minutes;
      });
    });

    // The seven-night mean, on the same axis, over nights with data.
    var means = [];
    var window = [];
    keys.forEach(function (key) {
      var night = byDate[key];
      if (night) {
        window.push(night.asleep_min);
        if (window.length > 7) window.shift();
      }
      if (window.length === 7) {
        var total = 0;
        window.forEach(function (value) {
          total += value;
        });
        means.push(total / 7);
      } else {
        means.push(null);
      }
    });
    var points = [];
    means.forEach(function (value, index) {
      if (value === null) return;
      points.push(index * slot + slot / 2 + "," + y(value));
    });
    if (points.length > 1) {
      svg.appendChild(
        svgEl("polyline", {
          points: points.join(" "), class: "health-mean",
          "vector-effect": "non-scaling-stroke",
        })
      );
    }
    return svg;
  }

  function scoreBlock(doc) {
    var box = el("div", "health-col");
    var score = doc.sleep.score;
    box.appendChild(el("div", "health-sub", "SLEEP SCORE"));
    if (!score) {
      box.appendChild(el("div", "health-note", "not enough nights"));
      return box;
    }
    box.appendChild(el("div", "health-tile-value", num(score.total, 0)));
    [
      ["duration", 50],
      ["consistency", 30],
      ["interruptions", 20],
    ].forEach(function (pair) {
      var name = pair[0];
      var line = el(
        "div",
        "health-note",
        name + " " + num(score[name], 0) + "/" + pair[1] + (score.lever === name ? "  <- lever" : "")
      );
      box.appendChild(line);
    });
    box.appendChild(
      el("div", "health-note", "bed by " + (doc.sleep.target_bedtime || "—"))
    );
    return box;
  }

  function heartSparks(doc) {
    var box = el("div", "health-col");
    var keys = dayKeys(doc.generated_at, 60);
    var byDate = {};
    (doc.heart.nights || []).forEach(function (night) {
      byDate[night.date] = night;
    });

    function series(field) {
      return keys.map(function (key) {
        var night = byDate[key];
        return night && night[field] !== null ? night[field] : null;
      });
    }

    var hr = series("sleep_avg_hr");
    var hrMean = doc.heart.sleep_hr_mean_60d;
    var hrSd = doc.heart.sleep_hr_sd_60d || 0;
    box.appendChild(
      el("div", "health-sub", "SLEEPING HR · 60 NIGHTS · avg " + num(hrMean, 0))
    );
    box.appendChild(
      sparkline({
        width: 440, height: 90, values: hr,
        min: 35, max: 70,
        band: hrMean === null ? null : { low: hrMean - hrSd, high: hrMean + hrSd },
      })
    );

    var hrv = series("hrv");
    var hrvMean = doc.heart.hrv_mean_60d;
    var hrvSd = doc.heart.hrv_7n_sd || 0;
    box.appendChild(el("div", "health-sub", "HRV · 60 NIGHTS · avg " + num(hrvMean, 0) + " ms"));
    box.appendChild(
      sparkline({
        width: 440, height: 90, values: hrv,
        min: 20, max: 140,
        band: hrvMean === null ? null : { low: hrvMean - hrvSd, high: hrvMean + hrvSd },
      })
    );

    var last4 = (doc.heart.hrr_last4 || [])
      .map(function (value) {
        return num(value, 0);
      })
      .join(" ");
    box.appendChild(
      el(
        "div",
        "health-note",
        "RESTING " + num(doc.heart.rhr_latest, 0) +
          " (60d " + num(doc.heart.rhr_60d, 0) + ")" +
          " · RESP " + num(doc.heart.resp_latest, 1) +
          " · HRR last 4: " + (last4 || "—")
      )
    );
    return box;
  }

  function renderHealthSleep(doc) {
    var host = $("health-sleep-body");
    clear(host);
    var left = el("div", "health-col");
    left.appendChild(sleepBars(doc));
    left.appendChild(
      el(
        "div",
        "health-note",
        "deep · core · REM per night, goal " + num(doc.sleep.goal_h, 1) +
          " h, 7-night mean; hollow marker = watch not worn"
      )
    );
    host.appendChild(left);
    var right = el("div", "health-col");
    right.appendChild(scoreBlock(doc));
    right.appendChild(heartSparks(doc));
    host.appendChild(right);
    text(
      $("health-sleep-source"),
      "7n " + num(doc.sleep.mean_7n_min / 60, 1) + " h · 60d " +
        num(doc.sleep.mean_60d_min / 60, 1) + " h · debt " + num(doc.sleep.debt_14n_h, 1) + " h"
    );
  }
```

- [ ] **Step 4: Replace the stub `renderHealth`**

```js
  function renderHealth(s) {
    var health = s.health || { available: false, detail: "no health data" };
    var doc = health.available ? health.document : null;
    var note = $("health-unavailable");
    note.hidden = doc !== null;
    if (doc === null) {
      text(note, "No health board: " + (health.detail || "unknown"));
    }
    [
      "health-tiles", "health-action", "health-sleep", "health-exercise",
      "health-weight", "health-detail", "health-footer",
    ].forEach(function (id) {
      $(id).hidden = doc === null;
    });
    if (doc === null) return;

    renderHealthTiles(doc);
    renderHealthAction(doc);
    renderHealthSleep(doc);
    // Tasks 19 fills the exercise, weight, detail and footer regions.
    if (health.stale) {
      text(
        $("health-sleep-source"),
        health.detail + " — numbers below are not today's"
      );
    }
  }
```

- [ ] **Step 5: Add the chart styles**

Append to `board.css`:

```css
.health-tile-status {
  gap: 4px;
}

.health-band {
  fill: rgba(124, 192, 255, 0.12);
}

.health-line {
  fill: none;
  stroke: var(--cyan);
  stroke-width: 1.6;
}

.health-mean {
  fill: none;
  stroke: var(--blue-text);
  stroke-width: 2;
}

.health-goal {
  stroke: rgba(226, 238, 252, 0.28);
  stroke-width: 1;
  stroke-dasharray: 4 5;
}

.health-missing {
  fill: none;
  stroke: var(--ink-35);
  stroke-width: 1;
}

.health-deep {
  fill: #2f6fe0;
}

.health-core {
  fill: #4aa3e8;
}

.health-rem {
  fill: #a06ff0;
}

rect[data-state="WATCH"] {
  opacity: 0.75;
}

rect[data-state="ALERT"] {
  fill: #b4553f;
}
```

- [ ] **Step 6: Check it in the browser**

```bash
cd runner
ATLAS_PROFILE=dev ATLAS_API_TOKEN=dev-token \
  ATLAS_HEALTH_BOARD_PATH=$PWD/tests/fixtures/health/board.json \
  uv run --frozen atlas serve
```

Open `http://127.0.0.1:8100/`, press Shift+Alt+2. Expected: `STATUS ALERT` with four chips
reading `Sleep ALERT · Heart OK · Fitness WATCH · Weight NO DATA`, five tiles to its right, the
action line `> Bed by 23:50 tonight...` over four deviation rows, and the sleep block with two
bars near the right edge of the 60-night axis and hollow markers everywhere else. Nothing above
the sleep block's bottom edge may be cut off by the fold.

- [ ] **Step 7: Run the runner suite; lint**

Run from `runner/`:
`uv run --frozen pytest -m "not mqtt" && uv run --frozen ruff check . && uv run --frozen ruff format --check .`
Expected: PASS, clean.

- [ ] **Step 8: Commit**

```bash
git add runner/src/atlas/presentation/static/board.js runner/src/atlas/presentation/static/board.css
git commit -m "board: health screen above the fold — status, tiles, action strip, sleep"
```

---

### Task 19: Below the fold — exercise, weight, sleep detail, coverage footer

**Files:**
- Modify: `runner/src/atlas/presentation/static/board.js`,
  `runner/src/atlas/presentation/static/board.css`

**Interfaces:**
- Consumes: `exercise`, `weight`, `sleep`, `coverage` from the document (Task 12).
- Produces: `renderHealthExercise(doc)`, `renderHealthWeight(doc)`, `renderHealthDetail(doc)`,
  `renderHealthFooter(doc)`, called from `renderHealth`.
- Nothing here is needed to answer the three questions (spec §7): this is detail the dial
  reveals.

- [ ] **Step 1: Write the exercise block**

Insert into `board.js` above `renderHealth`:

```js
  function loadBars(weekly) {
    var width = 860;
    var height = 120;
    var svg = healthSvg(width, height);
    var top = 1;
    weekly.forEach(function (week) {
      if (week.trimp > top) top = week.trimp;
    });
    var slot = width / Math.max(1, weekly.length);
    weekly.forEach(function (week, index) {
      var barHeight = (week.trimp / top) * (height - 18);
      svg.appendChild(
        svgEl("rect", {
          x: String(index * slot + 6), width: String(slot - 12),
          y: String(height - 18 - barHeight), height: String(Math.max(1, barHeight)),
          class: "health-load",
        })
      );
    });
    return svg;
  }

  function zoneBar(zones) {
    var width = 120;
    var height = 10;
    var svg = healthSvg(width, height);
    var total = 0;
    zones.forEach(function (value) {
      total += value;
    });
    if (total <= 0) return svg;
    var x = 0;
    zones.forEach(function (value, index) {
      var span = (value / total) * width;
      if (span <= 0) return;
      svg.appendChild(
        svgEl("rect", {
          x: String(x), y: "0", width: String(span), height: String(height),
          class: "health-zone", "data-zone": String(index + 1),
        })
      );
      x += span;
    });
    return svg;
  }

  function renderHealthExercise(doc) {
    var host = $("health-exercise-body");
    clear(host);
    var ex = doc.exercise;
    var capacity = ex.capacity || {};
    var consistency = ex.consistency || {};
    var load = ex.load || {};

    var left = el("div", "health-col");
    left.appendChild(
      el(
        "div",
        "health-sub",
        "CAPACITY " + (capacity.capacity_z === null ? "—" : "z " + num(capacity.capacity_z, 1))
      )
    );
    if (capacity.metric === "speed_at_hr") {
      left.appendChild(
        el(
          "div",
          "health-note",
          "SPEED @" + ex.zone_edges[1].toFixed(0) + ": " +
            num(capacity.speed_at_hr_latest, 2) + " m/s · vs prev 5 " +
            num(capacity.speed_at_hr_vs_avg_pct, 0) + " % · trend " +
            (capacity.speed_at_hr_trend || "—") + " (n " + capacity.speed_at_hr_n_of_10 + " of 10)"
        )
      );
    } else {
      left.appendChild(
        el(
          "div",
          "health-note",
          "EF (too few speed samples): " + num(capacity.ef_latest, 4) +
            " · vs prev 5 " + num(capacity.ef_vs_avg_pct, 0) + " %"
        )
      );
    }
    left.appendChild(
      el(
        "div",
        "health-note",
        "HR RECOVERY " + num(capacity.hrr_latest, 0) + " (mean " + num(capacity.hrr_mean, 1) +
          ", SD " + num(capacity.hrr_sd, 1) + ", n " + capacity.hrr_n + ")"
      )
    );
    left.appendChild(
      el(
        "div",
        "health-note",
        "CONSISTENCY runs/wk " + num(consistency.runs_per_week_4w, 1) +
          " (tgt " + consistency.runs_per_week_target + ") · strength/wk " +
          num(consistency.strength_per_week_4w, 1) + " (tgt " +
          consistency.strength_per_week_target + ") · this week " +
          consistency.runs_this_week + "/" + consistency.strength_this_week
      )
    );
    left.appendChild(
      el(
        "div",
        "health-note",
        "EASY SHARE 60d " + num(ex.easy_share_60d * 100, 0) + " % (tgt " +
          num(ex.easy_share_target * 100, 0) + " %)"
      )
    );
    left.appendChild(
      el(
        "div",
        "health-note",
        "ZONES (HRR, max " + ex.hr_max + ", rest " + num(ex.rhr_anchor, 0) + "): Z2 " +
          num(ex.zone_edges[0], 0) + "-" + num(ex.zone_edges[1], 0) + " · Z3 " +
          num(ex.zone_edges[1], 0) + "-" + num(ex.zone_edges[2], 0) + " · Z4 " +
          num(ex.zone_edges[2], 0) + "-" + num(ex.zone_edges[3], 0) + " · Z5 " +
          num(ex.zone_edges[3], 0) + "+"
      )
    );
    left.appendChild(el("div", "health-sub", "LOAD · 8 WEEKS (Edwards TRIMP)"));
    left.appendChild(loadBars(ex.weekly || []));
    left.appendChild(
      el(
        "div",
        "health-note",
        "this week " + num(load.load_week, 0) + " · 4-wk avg " + num(load.load_4w_avg, 0) +
          " · " +
          (load.acwr === null
            ? "ACWR hidden (needs 8 workouts in 28 d, have " + load.workouts_28d + ")"
            : "ACWR " + num(load.acwr, 2) + " (caution band, not a rule)")
      )
    );
    host.appendChild(left);

    var right = el("div", "health-col");
    right.appendChild(el("div", "health-sub", "RUNS · LAST 8"));
    var rows = el("div", "health-rows");
    (ex.runs || []).forEach(function (run) {
      var row = el("div", "health-row");
      row.setAttribute("data-state", run.state);
      row.appendChild(el("span", null, run.date));
      row.appendChild(el("span", null, num(run.miles, 2) + " mi"));
      row.appendChild(el("span", null, num(run.pace_min_mi, 1) + "/mi"));
      row.appendChild(el("span", null, num(run.avg_hr, 0) + "/" + num(run.max_hr, 0)));
      row.appendChild(
        el("span", null, run.hrr === null ? "—" : "hrr " + num(run.hrr, 0))
      );
      row.appendChild(zoneBar(run.zones || [0, 0, 0, 0, 0]));
      rows.appendChild(row);
    });
    right.appendChild(rows);
    right.appendChild(
      el(
        "div",
        "health-note",
        ex.vdot === null
          ? "VO2 max: none from Apple, and no run long enough to estimate"
          : "VO2 est. (VDOT) " + num(ex.vdot, 1) + " — est. from a training run, reads low"
      )
    );
    host.appendChild(right);
    text(
      $("health-exercise-source"),
      "durability " + num(capacity.durability_28d_min, 0) + " min in 28 d · " +
        num(capacity.durability_60d_min, 0) + " min in 60 d"
    );
  }
```

- [ ] **Step 2: Write the weight block**

```js
  function renderHealthWeight(doc) {
    var host = $("health-weight-body");
    clear(host);
    var weight = doc.weight;
    var width = 1180;
    var height = 160;
    var svg = healthSvg(width, height);
    var keys = dayKeys(doc.generated_at, 60);
    var byDate = {};
    (weight.readings || []).forEach(function (reading) {
      byDate[reading.date] = reading.kg;
    });

    var values = [];
    keys.forEach(function (key) {
      if (byDate[key] !== undefined) values.push(byDate[key]);
    });
    if (values.length === 0) {
      host.appendChild(
        el(
          "div",
          "health-note",
          "No readings yet. Last known " + num(weight.last_kg, 1) + " kg on " +
            (weight.last_date || "—") + " — the scale writes daily once it is in use."
        )
      );
      text($("health-weight-source"), "weigh-ins 0/7");
      return;
    }

    var low = Math.min.apply(null, values) - 1;
    var high = Math.max.apply(null, values) + 1;
    var slot = width / keys.length;

    function y(kg) {
      return height - ((kg - low) / (high - low || 1)) * height;
    }

    keys.forEach(function (key, index) {
      var kg = byDate[key];
      if (kg === undefined) return;
      svg.appendChild(
        svgEl("circle", {
          cx: String(index * slot + slot / 2), cy: String(y(kg)), r: "3",
          class: "health-dot",
        })
      );
    });
    [
      { value: weight.weight_7d, cls: "health-mean" },
      { value: weight.weight_28d, cls: "health-goal" },
    ].forEach(function (line) {
      if (line.value === null || line.value === undefined) return;
      svg.appendChild(
        svgEl("line", {
          x1: "0", x2: String(width), y1: String(y(line.value)), y2: String(y(line.value)),
          class: line.cls, "vector-effect": "non-scaling-stroke",
        })
      );
    });
    host.appendChild(svg);
    host.appendChild(
      el(
        "div",
        "health-note",
        "7d " + num(weight.weight_7d, 1) + " kg · 28d " + num(weight.weight_28d, 1) +
          " kg · week " + num(weight.week_change, 1) + " kg · BMI " + num(weight.bmi, 1) +
          (weight.bmi_stale ? " (stale)" : "") + " · weigh-ins " + weight.weigh_ins_7d + "/7"
      )
    );
    text($("health-weight-source"), "weigh-ins " + weight.weigh_ins_7d + "/7");
  }
```

- [ ] **Step 3: Write the sleep detail block and the footer**

```js
  /* One thin bar per night from bedtime to wake on a 21:00-10:00 axis. The
     bedtime scale in the document is the spec's shifted one (00:40 = 24.67),
     so 21:00 is 21 and 10:00 is 34. */
  function bedtimeStrip(doc) {
    var width = 860;
    var height = 200;
    var axisLow = 21;
    var axisHigh = 34;
    var svg = healthSvg(width, height);
    var nights = doc.sleep.nights || [];
    var rowHeight = nights.length ? Math.min(6, height / nights.length) : 6;

    function x(hour) {
      return ((Math.max(axisLow, Math.min(axisHigh, hour)) - axisLow) / (axisHigh - axisLow)) * width;
    }

    nights.forEach(function (night, index) {
      var wake = night.waketime < 12 ? night.waketime + 24 : night.waketime;
      var y = index * (height / Math.max(1, nights.length));
      svg.appendChild(
        svgEl("rect", {
          x: String(x(night.bedtime)), y: String(y),
          width: String(Math.max(2, x(wake) - x(night.bedtime))),
          height: String(Math.max(2, rowHeight - 1)),
          class: "health-core", "data-state": night.state,
        })
      );
    });
    [
      { hour: doc.sleep.median_bedtime, cls: "health-mean" },
      {
        hour: doc.sleep.target_bedtime
          ? Number(doc.sleep.target_bedtime.slice(0, 2)) +
            Number(doc.sleep.target_bedtime.slice(3)) / 60
          : null,
        cls: "health-goal",
      },
    ].forEach(function (line) {
      if (line.hour === null || line.hour === undefined) return;
      var hour = line.hour < 12 ? line.hour + 24 : line.hour;
      svg.appendChild(
        svgEl("line", {
          x1: String(x(hour)), x2: String(x(hour)), y1: "0", y2: String(height),
          class: line.cls, "vector-effect": "non-scaling-stroke",
        })
      );
    });
    return svg;
  }

  function renderHealthDetail(doc) {
    var host = $("health-detail-body");
    clear(host);
    var left = el("div", "health-col");
    left.appendChild(el("div", "health-sub", "BEDTIME · WAKE (21:00 to 10:00)"));
    left.appendChild(bedtimeStrip(doc));
    left.appendChild(
      el(
        "div",
        "health-note",
        "median bedtime line, target bedtime dashed · SD " +
          num(doc.sleep.bedtime_sd_min, 0) + " min"
      )
    );
    host.appendChild(left);

    var right = el("div", "health-col");
    right.appendChild(
      el(
        "div",
        "health-sub",
        "SOCIAL JET LAG " +
          (doc.sleep.social_jet_lag_h === null
            ? "— (needs 3 free and 3 work nights)"
            : num(doc.sleep.social_jet_lag_h, 1) + " h")
      )
    );
    right.appendChild(el("div", "health-sub", "HR-MIN TIMING · per night"));
    right.appendChild(
      sparkline({
        width: 440, height: 80,
        values: (doc.sleep.nights || []).map(function (night) {
          return night.hr_min_frac;
        }),
        min: 0, max: 1, band: { low: 0.5, high: 0.7 },
      })
    );
    right.appendChild(
      el("div", "health-note", "band = mid; above it is late (a heuristic, never an alert)")
    );
    var naps = 0;
    (doc.sleep.nights || []).forEach(function (night) {
      naps += night.naps_min || 0;
    });
    right.appendChild(el("div", "health-note", "NAPS " + num(naps, 0) + " min over 60 days"));
    host.appendChild(right);
    text($("health-detail-source"), "nights with the watch only");
  }

  function renderHealthFooter(doc) {
    var coverage = doc.coverage || {};
    text(
      $("health-footer"),
      "nights with data " + coverage.nights_with_data_7 + "/7, " +
        coverage.nights_with_data_60 + "/60 · weigh-ins " + coverage.weigh_ins_7d +
        "/7 · last import " + (coverage.last_import_at || "—") +
        " · last HR " + (coverage.last_hr_sample_at || "—") +
        " · last weight " + (coverage.last_weight_at || "—")
    );
  }
```

- [ ] **Step 4: Call them from `renderHealth`**

Replace the `// Tasks 19 fills ...` comment in `renderHealth` with:

```js
    renderHealthExercise(doc);
    renderHealthWeight(doc);
    renderHealthDetail(doc);
    renderHealthFooter(doc);
```

- [ ] **Step 5: Add the remaining styles**

Append to `board.css`:

```css
.health-weight-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.health-load {
  fill: rgba(124, 192, 255, 0.55);
}

.health-dot {
  fill: var(--cyan);
}

.health-zone[data-zone="1"] {
  fill: #3d6f9e;
}

.health-zone[data-zone="2"] {
  fill: #4aa3e8;
}

.health-zone[data-zone="3"] {
  fill: var(--orange);
}

.health-zone[data-zone="4"] {
  fill: #e8734a;
}

.health-zone[data-zone="5"] {
  fill: var(--red);
}

.health-row[data-state="WATCH"] {
  color: var(--amber-text);
}

.health-row[data-state="ALERT"] {
  color: var(--red-text);
}
```

- [ ] **Step 6: Check it in the browser**

Serve as in Task 18, press Shift+Alt+2, then turn the dial (Shift+Alt+6 repeatedly).

Expected: the exercise block scrolls into view with the runs list and eight load bars; the
weight block says "No readings yet. Last known 81.6 kg on 2026-01-08"; the sleep detail block
draws two bedtime bars; the footer reads
`nights with data 4/7, 20/60 · weigh-ins 0/7 · last import 2026-09-07 07:12:00-04:00 ...`.
Stop turning: after 60 s the region returns to the top on its own. Leave it: after 10 minutes
the ops board comes back and key 1's LED relights.

- [ ] **Step 7: Run the runner suite; lint**

Run from `runner/`:
`uv run --frozen pytest -m "not mqtt" && uv run --frozen ruff check . && uv run --frozen ruff format --check .`
Expected: PASS, clean.

- [ ] **Step 8: Commit**

```bash
git add runner/src/atlas/presentation/static/board.js runner/src/atlas/presentation/static/board.css
git commit -m "board: health screen below the fold — exercise, weight, sleep detail, coverage"
```

---

## Part E: running it on atlas

### Task 20: Deploy the health repo to atlas, schedule it, and verify on the wall

**Files (in `pi-home`):**
- Modify: `infra/repos.toml`, `docs/repos.md`, `docs/repo-map.md`, `WORKLOG.md`
- Host-side, in neither repo: `~/.ssh/` deploy key and config on atlas,
  `/home/domdd/atlas-health-analysis/.env`, `/var/lib/atlas-health/`.

**Interfaces:**
- Produces: two `[[repo]]` entries, `health-nightly` (10:00 daily) and `health-weekly`
  (Sunday 20:00), cloning `dominickdupuy/health` to `/opt/health`.
- The runner reads `/var/lib/atlas-health/board.json`; `scripts/repos.py` clones, schedules and
  runs the job and lifts its `atlas-summary` line onto the RUNS panel. Nothing else connects the
  two repositories.

- [ ] **Step 1: Give atlas a deploy key for the private repo**

`dominickdupuy/health` is private, so the Pi needs its own key, in the pattern `docs/repos.md`
already documents for `Host github-finance`:

```bash
ssh domdd@atlas 'ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_health -C "atlas health deploy" && cat ~/.ssh/id_health.pub'
```

Add that public key to the repo as a deploy key (read-only is enough — the job never pushes):
GitHub → `dominickdupuy/health` → Settings → Deploy keys → Add deploy key. Then teach ssh the
alias the registry will use:

```bash
ssh domdd@atlas 'cat >> ~/.ssh/config <<EOF

Host github-health
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_health
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config'
ssh domdd@atlas 'ssh -T git@github-health 2>&1 | head -2'
```

Expected: `Hi dominickdupuy/health! You've successfully authenticated, but GitHub does not
provide shell access.`

- [ ] **Step 2: Prepare the host directories and configuration**

```bash
ssh domdd@atlas 'sudo install -d -o domdd -g domdd /var/lib/atlas-health && ls -ld /var/lib/atlas-health'
ssh domdd@atlas 'cat >> /home/domdd/atlas-health-analysis/.env <<EOF
DOB=2005-08-31
HEIGHT_M=1.854
SEX=male
HR_MAX=200
SLEEP_GOAL_H=7.5
RUNS_PER_WEEK_TARGET=3
STRENGTH_PER_WEEK_TARGET=2
EASY_SHARE_TARGET=0.70
FIXED_HR_BAND=150-160
FREE_DAYS=Sat,Sun
STATE_DIR=/var/lib/atlas-health
WEEKLY_SUMMARY=false
EOF
grep -c = /home/domdd/atlas-health-analysis/.env'
```

The file already holds `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`,
`FREEREPS_LOGIN` and an empty `NTFY_TOPIC`. Set `NTFY_TOPIC` to the topic the phone is
subscribed to; until it has a value, `push` logs and returns False, and the screen is the only
delivery.

- [ ] **Step 3: Add the two repo entries**

Append to `infra/repos.toml`:

```toml
[[repo]]
name = "health-nightly"
description = "Nightly health analysis: FreeReps Postgres -> board.json + health.db + ntfy on ALERT"
path = "/opt/health"
url = "git@github-health:dominickdupuy/health.git"
branch = "main"
kind = "job"
# 10:00, after the phone's morning sync has landed the night. The board polls
# /api/status every ten seconds, so the new document is on the wall within one
# poll of the write.
schedule = "0 10 * * *"
job = "ATLAS_HEALTH_ENV_FILE=/home/domdd/atlas-health-analysis/.env /home/domdd/.local/bin/uv run atlas-health nightly"
setup = "/home/domdd/.local/bin/uv sync"
setup_marker = ".venv/bin/python"
timeout_seconds = 900

[[repo]]
name = "health-weekly"
description = "Sunday summary push (off unless WEEKLY_SUMMARY=true)"
path = "/opt/health"
url = "git@github-health:dominickdupuy/health.git"
branch = "main"
kind = "job"
schedule = "0 20 * * 0"
job = "ATLAS_HEALTH_ENV_FILE=/home/domdd/atlas-health-analysis/.env /home/domdd/.local/bin/uv run atlas-health weekly"
timeout_seconds = 300
```

Both entries name the same checkout deliberately: `repos.py` fast-forwards it before each run,
so the weekly job cannot execute a different revision than the nightly one. `apply` must be run
as root because it writes `/etc/cron.d/atlas-repos`.

- [ ] **Step 4: Document it**

Add to `docs/repos.md`, in the list of hosted repositories:

> **health-nightly / health-weekly** — `dominickdupuy/health`, cloned to `/opt/health` through
> the `github-health` deploy key. Reads the FreeReps database through the read-only `analysis`
> role and writes `/var/lib/atlas-health/board.json` (schema 1; the runner reads it and
> `/api/status` passes it through as `health`) plus `/var/lib/atlas-health/health.db`, a SQLite
> history of nights, runs and daily status. Pushes ntfy when the overall state is ALERT. It
> writes nothing to Postgres. Configuration and credentials live in
> `/home/domdd/atlas-health-analysis/.env`, outside both checkouts. Spec and plan:
> `docs/superpowers/specs/2026-09-07-health-screen-spec.md` and
> `docs/superpowers/plans/2026-09-07-health-screen.md` in this repository.

Add to `docs/repo-map.md`, in the code map: `telemetry/infrastructure/health_board.py` as the
runner's reader of that document, and a line saying the analysis itself lives in the separate
`dominickdupuy/health` repository — this repository renders it and never computes it.

Add a `WORKLOG.md` entry naming the date, the ColoPlay key-2 screen, and the fact that the
document is authored outside this repository.

- [ ] **Step 5: Apply and run it once by hand**

```bash
ssh domdd@atlas 'cd /opt/atlas && sudo python3 scripts/repos.py apply && python3 scripts/repos.py run health-nightly'
ssh domdd@atlas 'tail -30 /var/log/atlas-repos/health-nightly/*.log'
ssh domdd@atlas 'ls -la /var/lib/atlas-health && python3 -c "import json;d=json.load(open(\"/var/lib/atlas-health/board.json\"));print(d[\"schema\"], d[\"status\"]);print(d[\"action\"])"'
```

Expected: the clone succeeds on the first `apply`, `uv sync` runs once (the `setup_marker` keeps
it from running again), exit 0, an `atlas-summary {"nights": ..., "alerts": ..., ...}` line in
the log, `board.json` with `schema 1` and the status from spec §5.1 (Sleep ALERT, Overall
ALERT), and `health.db` beside it.

- [ ] **Step 6: Refresh the monitor and verify on the wall**

```bash
ssh domdd@atlas 'cd /opt/atlas && bash scripts/refresh-dashboard.sh && curl -s -H "Authorization: Bearer $ATLAS_API_TOKEN" localhost:8100/api/status | python3 -c "import json,sys;d=json.load(sys.stdin)[\"health\"];print(d[\"available\"], d[\"stale\"], d[\"detail\"])"'
```

Expected: `True False nightly analysis`. Then press the ColoPlay's top-right key at the desk:
the wall board must switch to the health screen, its LED must light, the dial's face must read
HEALTH, and turning the dial must scroll. Press the top-left key to return. Check the RUNS panel
on the ops board too — `health-nightly` should appear with `20 nights · 1 alerts`.

- [ ] **Step 7: Confirm the alert delivery end to end**

With `NTFY_TOPIC` set, re-run the job and confirm the phone receives one notification whose
first line is the action line. Re-run it again the same day and confirm no second notification
arrives (spec §4.10: push again only on a new ALERT signal or every third day). The dedupe state
is `/var/lib/atlas-health/notify.json`; delete it to re-arm during testing.

- [ ] **Step 8: Commit and ship**

```bash
git add infra/repos.toml docs/repos.md docs/repo-map.md WORKLOG.md
git commit -m "atlas: schedule the nightly health analysis and document the health screen"
git push -u origin health-screen
```

Open the PR; the deploy timer moves `origin/release` onto `/opt/atlas` once it merges. The
health repo is already live — `repos.py` fast-forwards `/opt/health` before every run, so its
`main` ships without a pi-home release.

---

## Open questions carried from the spec

These are §8 of the spec. None of them blocks a task; each has a default already coded, and
changing one is a config edit rather than a code change unless noted.

| # | Question | Default in this plan | Where to change it |
|---|---|---|---|
| 1 | Sex for the reference tables | `SEX=male` | host `.env` |
| 2 | `HR_MAX` | 200 (observed) | host `.env` |
| 3 | The 2 h gap rule | 2 h, longest session is the night | `sleep.cluster_nights(gap_hours=)` |
| 4 | `SLEEP_GOAL_H` | 7.5 | host `.env` |
| 5 | Weekly targets | 3 runs, 2 strength, 70 % easy | host `.env` |
| 6 | `FREE_DAYS` | Sat, Sun | host `.env` |
| 7 | `FIXED_HR_BAND` | 150-160 | host `.env` |
| 8 | Keep S3 firing daily | kept, as the spec recommends | `deviations.sleep_signals` |
| 9 | Recalibrate the sleep score | not done; revisit after a month | `sleep.sleep_score` |
| 10 | Miles or kilometres | miles primary, km in the document | `board.py` run rows |
| 11 | VeSync `source` string | not filtered on; any source counts | `weight._daily` |
| 12 | Scroll behaviour | 60 s snap, 10 min return to ops | `board.js` constants |
| 13 | ntfy cadence for a persistent ALERT | every third day | `notify.REPEAT_AFTER_DAYS` |

Rows 3, 8, 9, 10 and 11 change code in the health repo; row 12 changes `board.js` in pi-home;
the rest are edits to `/home/domdd/atlas-health-analysis/.env` on atlas with no release at all.
