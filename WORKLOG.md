# Atlas — overnight build log

Branch: `overnight-build` (from `main` @ c365de1). Started 2026-08-31 23:51 EDT.

---

## BLOCKED / NEEDS DOMINICK

### 1. The brief's premise does not match the repository — read this first

The brief describes a greenfield build: "Project scaffold. uv-managed, src layout,
package `atlas`", then config, job model, approval queue, FastAPI, MQTT, dashboard.

**None of that is greenfield.** `origin/main` @ c365de1 already contains a complete,
working implementation of tasks 1–6, at `runner/src/atlas/`, in the DDD layout that
`docs/architecture.md` §9 and D18 mandate. Baseline on arrival, before I touched
anything: **ruff clean, ruff format clean, mypy strict clean on 121 files**, and a
full test suite (25 test modules, unit + integration).

`/opt/atlas` looked empty only because the clone had never been checked out — the
local `main` had zero commits while `origin/main` had four. I ran
`git checkout -B main origin/main` to materialise it, then branched.

**What I did about it:** I did NOT scaffold a second `atlas` package at the repo root.
That would have produced a duplicate package shadowing the real one, contradicted
§9 and D18, and buried working code. I treated tasks 1–6 as *verify, then close the
specific gaps*, and spent the time on tasks 7–10, which are genuinely unbuilt.

**Your call:** if you actually wanted a from-scratch rewrite at the repo root, none of
tonight's work is what you asked for and you should discard this branch. I judged that
unlikely enough to proceed, but it is your decision, not mine.

### 2. The brief says 16 decisions; the doc has 19

`docs/architecture.md` is v1.4 with **D1–D19**. The brief says "16 numbered
architectural decisions". D17 (display UI stack), D18 (bounded contexts), D19
(connector sourcing) are the three most recent — and D17/D18 are exactly the ones
the brief's instructions contradict. That is consistent with the brief being written
against v1.2 or earlier. Where they conflict I had to choose; each choice is logged
below under "Deviations", with the reasoning. Please confirm you are happy with them.

### 3. Deviations from the architecture doc, made deliberately

| # | Brief says | Architecture says | What I did | Why |
|---|---|---|---|---|
| a | Dashboard auto-refreshes every 10s via `fetch` against a JSON status endpoint | **D17**: server-rendered Jinja fragments pushed over **SSE + htmx**; SPA/polling rejected | Kept SSE as the live path, **added** the JSON status endpoint and a 10s poll as a fallback/degradation path | Satisfies both. The poll is also what makes the brief's "stale data" requirement implementable — SSE alone cannot tell you how old the data is when the server is gone |
| b | Kiosk autostart via **labwc** `~/.config/labwc/autostart`; do not disturb the existing desktop autologin session | **D17**: Chromium under **cage**, its own systemd unit, `Restart=always`, `MemoryMax=800M` | Followed the **brief** (labwc autostart) | There is a live desktop autologin session on seat0/tty1. `cage` wants its own seat/TTY; starting it would fight the running session, which the brief explicitly forbids. D17's cage design is right for a dedicated kiosk box, wrong for this box as it stands today. `infra/systemd/atlas-kiosk.service` (cage) is left in the repo untouched for when that changes. |
| c | Dashboard at `/dashboard`, service on port **8000** | **D16**: board at `/`, existing config defaults to **8100** | Serve at **both** `/` and `/dashboard`; left the port default at 8100 and pointed the kiosk at it | Changing the default port would have broken `infra/systemd/atlas-kiosk.service` and `public_url` for no benefit. Both routes cost one line. |
| d | `atlas.service` runs uvicorn directly as `domdd` under systemd | Architecture ships the runner as a **Docker** service in `compose.yaml` | Followed the **brief** (native systemd unit) | Also the safer reading of your hard boundaries: a native unit means the deploy loop never touches the Docker daemon or the HA/Mosquitto containers. |

---

## Summary

_(Written at the end of the session — see the bottom of this file for the running log.)_

**Status: in progress.**

---

## Running log

### 2026-08-31 23:51 — Orientation

- `docs/architecture.md` did not exist in the working tree. It does exist on
  `origin/main`. Cause: unchecked-out clone (see BLOCKED #1). Read it end to end,
  all 572 lines, D1–D19.
- Surveyed the tree: 161 tracked files, `runner/src/atlas/` with contexts
  `jobs`, `approvals`, `budget`, `connectors`, `telemetry` split
  domain/application/infrastructure exactly as D18 requires.
- Created branch `overnight-build`.

### 2026-08-31 23:54 — Baseline before touching anything

`uv sync --frozen` → rc=0 (deps resolved from the committed lockfile).

| Check | Result |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 122 files already formatted |
| `mypy src tests` (strict) | Success: no issues found in 121 source files |
| `pytest -m "not mqtt"` | _running at time of writing_ |

This baseline matters: it establishes that anything red later is mine, not inherited.

