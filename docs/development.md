# Continuing development

The onboarding document for picking this project up on a new machine — in
particular, on the Pi 5 itself once it is in hand. It records where the build
stands, what is deliberately unfinished and why, and the facts about the
implementation that are not obvious from reading the code top-down.

The [architecture spec](architecture.md) (v1.4, decisions D1–D19) is
authoritative. When code and spec disagree, the spec wins; when a decision
needs to change, amend the spec first and record it in the decision log.
[operations.md](operations.md) is the "how do I" runbook for the running
system; this file is about developing it.

## Where the build stands

The runner (phases 2–5 of spec §10) is code-complete and tested: scheduler,
three-tier job execution, approvals with TTL sweep, budget ledger with the
daily ceiling, MQTT telemetry, and the FastAPI dashboard with SSE. All of it
was developed and tested on a Windows dev machine — **the Pi has never run
this stack**. Phases 1 and 3 (OS install, compose stack on the Pi, kiosk
display) are therefore the next physical work, and the systemd units in
`infra/systemd/` are as yet unexercised.

The `dev` profile (`ATLAS_PROFILE=dev`) wires stub connectors so the whole
system runs with zero credentials; `prod` wires the real adapters
(`runner/src/atlas/bootstrap/connectors_factory.py` is the switch point).

## Deliberately deferred — do not "fix" these in passing

- **Google Calendar MCP server: not yet chosen.** Per D19 a community server
  must be source-vetted (procedure in operations.md) before it gets a token.
  Its compose entry is commented out until that happens.
- **GitHub MCP server transport flags** for streamable HTTP on the compose
  network need verifying when the `mcp` profile is first brought up for real
  (noted in `compose.yaml`).
- **Phase 6 (voice) is docs-only** (`voice.md`). Wyoming images stay unpinned
  until that phase starts. The cloud STT provider is an open question (§11).
- **Tier 3 executor is a bounded single-round stub.** The real agent loop is
  phase 7, last on purpose — it ships on top of guardrails that already
  exist. `conflict-finder.yaml` is shipped disabled for the same reason.
- **Calendar writes:** `google-calendar.update_event` is allowlisted in
  conflict-finder so proposals can name it, but the OAuth scope stays
  read-only until writes are actually wanted (§11). An approved change
  failing at the provider is the intended behavior until then.
- **Ansible provisioning** waits until the manual Pi setup has happened once
  (`infra/ansible/README.md` holds the checklist to follow by hand).

## Non-obvious implementation facts

Things a fresh reading of the code tends to get wrong:

- **Jobs run as spawn-based subprocesses** (`python -m atlas execute-job`,
  NDJSON on stdout, final line is the RunReport). Spawn, not fork, is why
  Windows dev works. Keep child imports cheap.
- **Children never touch SQLite or MQTT.** The parent owns all persistence
  and publishes all terminal events. If a child ever writes state directly,
  that is a bug, not a shortcut.
- **The dashboard SSE stream feeds off the in-process event bus, not MQTT.**
  Mosquitto being down degrades external telemetry only; the board keeps
  updating.
- **httpx's ASGITransport cannot stream SSE**, so the SSE integration test
  drives the ASGI app directly. Don't rewrite it to use the test client.
- The MQTT integration test (`-m mqtt`) needs a live broker:
  `docker compose up -d mosquitto` first. It is excluded in CI and fails on
  a machine without Docker running — that failure is environmental.

## Setting up a dev environment (any machine, including the Pi)

Requires [uv](https://docs.astral.sh/uv/) and Docker (arm64 images all have
official builds; the runner Dockerfile is multi-arch).

```sh
git clone https://github.com/dominickdupuy/atlas.git && cd atlas
cp .env.example .env        # dev defaults are fine; stub connectors
cd runner
uv sync                     # creates .venv with dev tools pinned by uv.lock
```

The verification loop — run all four before considering any change done:

```sh
uv run pytest               # unit + integration; -m "not mqtt" without a broker
uv run mypy src tests       # strict, must be clean
uv run ruff check .
uv run atlas validate-jobs --jobs-dir ../jobs
```

Pre-commit hooks mirror these (`.pre-commit-config.yaml`), and CI
(`.github/workflows/ci.yml`) runs them on push.

Run the service: `uv run atlas serve` (bare) or `docker compose up --build`
(full stack: mosquitto + ntfy + runner). Dashboard at
`http://127.0.0.1:8100/?token=<ATLAS_API_TOKEN>`. Debug a single job the way
the scheduler runs it: `uv run atlas execute-job < request.json`.

## Picking it up on the Pi 5

The phase order in spec §10 is the plan of record. From the current state:

1. **Phase 1** — flash Raspberry Pi OS to the SSD (D13), disable Wi-Fi power
   save (`infra/systemd/wifi-powersave-off.service`), set a static DHCP
   lease, join the tailnet (D12). Bring up `docker compose --profile ha up -d`
   and configure the HA recorder exclusions (already in
   `homeassistant/configuration.yaml`).
2. **Phase 3 hardware half** — install `atlas-compose.service` and
   `atlas-kiosk.service`; the kiosk token lives in `/etc/atlas/kiosk.env`
   (operations.md). ntfy one-time user setup is in operations.md.
3. **First prod credentials** — set `ATLAS_PROFILE=prod` and fill `.env`
   following the D19 scoping table in operations.md, narrowest scope first.
4. **Phase 5 completion** — vet and pin a Google Calendar MCP server, verify
   the GitHub MCP transport flags, uncomment their compose entries.
5. Then phases 6 and 7, in that order.

Document what the manual Pi setup actually required as you go — that record
becomes the Ansible playbook (`infra/ansible/README.md`).

## Conventions

- **Bounded contexts** (D18): each of `jobs/`, `approvals/`, `budget/`,
  `connectors/`, `telemetry/` keeps the `domain/ application/ infrastructure/`
  split. Domain code imports nothing from other contexts; cross-context talk
  happens through application services and events. `shared/` stays tiny.
- **Supply chain** (D19): every image and dependency is pinned; community MCP
  servers are read-at-tag vetted before receiving any credential.
- **History note:** the project was renamed from `pi-home` to `atlas`
  (2026-08-31); the env prefix went `PIHOME_*` → `ATLAS_*`, the CLI
  `pihome` → `atlas`, MQTT namespace `pihome/#` → `atlas/#`, and the ntfy
  topic to `atlas-approvals`. Any stale reference to the old names is safe
  to update on sight.
