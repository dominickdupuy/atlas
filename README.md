# atlas

An always-on personal automation host for a Raspberry Pi 5: scheduled agentic
jobs and voice control, sharing one MCP tool layer, with approvals, budgets,
and a passive ops board.

The full design, including every decision and its rejected alternatives, lives
in [docs/architecture.md](docs/architecture.md). Operational procedures are in
[docs/operations.md](docs/operations.md). Picking the project up on a new
machine (including the Pi itself): [docs/development.md](docs/development.md).

## Layout

| Path | What it is |
|---|---|
| `runner/` | The custom service: a Python modular monolith (bounded contexts, D18) |
| `jobs/` | One YAML per scheduled job, validated against the schema in the spec §7 |
| `compose.yaml` | The whole stack. Profiles: `ha`, `mcp`, `voice` |
| `homeassistant/` | Home Assistant config (runtime state gitignored) |
| `infra/` | Broker/ntfy config, systemd units for the Pi |
| `docs/` | Architecture spec, runbook, development guide, voice setup notes |

## Quickstart (dev machine, no Pi and no credentials required)

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
cp .env.example .env          # defaults are fine for dev (stub connectors)
docker compose up --build     # mosquitto + ntfy + runner
```

Boards: the passive ops board at
<http://127.0.0.1:8100/dashboard?token=change-me> (the one the kiosk renders),
and the htmx/SSE board from D17 at <http://127.0.0.1:8100/?token=change-me>.
The `?token=` visit sets a cookie and redirects; a page navigation cannot carry
an `Authorization` header.

Watch events: `docker compose exec mosquitto mosquitto_sub -t 'atlas/#' -v`

### Working on the runner

```sh
cd runner
uv sync                       # creates .venv with dev tools
uv run pytest                 # unit + integration (no network needed)
uv run mypy src tests         # strict
uv run ruff check .
uv run atlas validate-jobs --jobs-dir ../jobs
```

Run the service outside Docker (uses `ATLAS_*` env or `.env` defaults):

```sh
uv run atlas serve
```

Execute a single job by hand — the same code path the scheduler uses, which is
the intended way to debug a job:

```sh
uv run atlas execute-job < request.json
```

## Deploying to the Pi

Phase-by-phase build order is in the spec §10. The short version: flash
Raspberry Pi OS to the SSD, clone this repo, `cp .env.example .env` and fill
it in, `docker compose --profile ha up -d`, then install the units from
`infra/systemd/`. Details: [docs/operations.md](docs/operations.md).

## Configuration

Settings resolve highest-precedence first: constructor arguments, environment
variables (`ATLAS_*`), `.env`, the secrets directory, then `config.toml`.
Environment beats the file, so a systemd drop-in or a one-off
`ATLAS_PROFILE=dev` always wins over what is on disk.

`config.toml` is searched for at `/opt/atlas/config.toml` and `../config.toml`
relative to `runner/`, which on the Pi are the same file. `ATLAS_CONFIG_FILE`
replaces that search path outright. Start from `config.toml.example`.

**Secrets do not go in `config.toml`.** Every deploy runs `git reset --hard`
in that directory, and the file sits inside the repo. Put the API token and
provider credentials in `/etc/atlas/atlas.env`, which the systemd unit reads
and which no deploy touches.

`ATLAS_PROFILE` selects the connector set: `dev` stubs every connector and
needs no credentials at all; `prod` wires the real adapters and refuses to
start half-configured (it raises if `ANTHROPIC_API_KEY` is missing rather than
silently degrading).

## Modes: read, propose, write

Mode is a property of each **job**, declared in its YAML, not a global switch
on the service. There is deliberately no runtime toggle — changing what the
system may do is a config change that goes through git and CI, per D8.

| Mode | Behavior |
|---|---|
| `read` | Gathers and reports. Any proposal it emits is dropped and surfaced as a warning, because a read job producing an action means it is misconfigured. |
| `propose` | Freezes the action, persists it, and notifies the phone. Nothing executes until approved. |
| `write` | Executes directly. Requires an explicit `auto_approve: true`; the schema rejects the combination without it, and the gate re-asserts it immediately before the mutation. |

To change a job's mode, edit its file in `jobs/`, then:

```sh
cd runner && uv run atlas validate-jobs --jobs-dir ../jobs
```

Commit, merge to `main`, and the deploy pipeline ships it. The ops board shows
how many enabled jobs can write unattended, in red whenever that is not zero —
that count, not a global flag, is what governs the system's authority.

Approvals are decided from the phone (D8/D16):

```
POST /api/approvals/{id}/decision   {"decision": "approve" | "reject"}
```

The monitor deliberately cannot approve anything: it has no input device.

## The deploy pipeline

```
push to main
   -> CI: lint, mypy, test matrix, validate-jobs
   -> CI release job fast-forwards `release` (only if all of them pass)
   -> Pi: atlas-deploy.timer fires every 5 minutes
   -> scripts/deploy.sh: fetch origin/release
        unchanged -> exit 0, touch nothing
        moved     -> git reset --hard, uv sync --frozen, restart atlas
   -> atlas.service comes back on the new revision
```

The board header shows the running version and git revision, so you can tell
from across the room whether a deploy actually landed.

Install the units (validates the sudoers drop-in with `visudo -c` before
installing it, and backs it out if the whole set stops parsing):

```sh
sudo /opt/atlas/scripts/install-systemd.sh
```

Verify:

```sh
systemctl status atlas
systemctl list-timers atlas-deploy.timer
journalctl -u atlas-deploy -n 50 --no-pager
```

The timer's early exit is the common case; `already at <sha>; nothing to do`
in the journal is the pipeline working, not idling. Re-syncing dependencies
every five minutes would wear out the microSD this host boots from.

`sudo` is scoped to exactly `systemctl restart atlas` in
`/etc/sudoers.d/atlas`. Not `systemctl *`, which can start a unit that runs
anything and is therefore equivalent to full root.

## The kiosk

`scripts/kiosk-start.sh` runs Chromium in kiosk mode against `/dashboard`,
started from `~/.config/labwc/autostart` inside the normal desktop session.

Two things worth knowing before editing that autostart file:

- labwc reads the **first** autostart it finds (`~/.config/labwc/` then
  `/etc/xdg/labwc/`) and does **not** merge them. The installed file sources
  the stock one first; removing that line silently takes away the taskbar and
  the desktop.
- Chromium's profile and cache live in `/dev/shm` (tmpfs). Nothing there needs
  to survive a reboot, and a browser running 24/7 on a microSD otherwise
  writes continuously.

## Not yet implemented

| Area | State |
|---|---|
| Tier 3 agent loop | One bounded planning round that may only emit proposals. The real multi-round loop is phase 7 (spec §10 ships the riskiest capability last). |
| MCP connectors | Client exists; no server is wired. `ATLAS_PROFILE=prod` needs `mcp_*_url` settings before any real tool call works. |
| Voice | Nothing. Phase 6 — see [docs/voice.md](docs/voice.md). |
| Notifications | ntfy adapter exists and is wired; the topic and token still need configuring on the Pi before an approval reaches a phone. |
| Display states | Only `OPS` and `APPROVAL_PENDING` are derived. The voice states (`LISTENING`, `THINKING`, `SPEAKING`) arrive with phase 6. |
| Kiosk under cage | D17 specifies `cage`; the Pi currently runs the board inside the existing labwc desktop session instead. See `WORKLOG.md`. |
