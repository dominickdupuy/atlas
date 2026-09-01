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

Dashboard: <http://127.0.0.1:8100/?token=change-me> (the token from `.env`).
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
