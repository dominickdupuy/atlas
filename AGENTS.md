# Working on Atlas

Read `docs/repo-map.md` for the current code map, validation baseline, and
follow-up status. The `status.json` refresh remains deferred. The user has
since requested queued runs on the monitor and dashboard refreshes after changes.

## Architecture and conventions

- Python 3.12+, uv-managed project under `runner/`, package in `runner/src/atlas/`.
- `docs/architecture.md` records decisions D1–D19. Preserve the bounded contexts
  (`jobs`, `approvals`, `budget`, `connectors`, `telemetry`) and their
  `domain/application/infrastructure` split. Wire adapters in `bootstrap/`.
- Job children report over NDJSON; only the parent writes SQLite and publishes
  MQTT. The dashboard's SSE stream uses the in-process bus.
- The screen is passive. Preserve explicit staleness, bounded lists, and
  last-good-data behavior. `/` and `/dashboard` are different implementations.
  Both share the Runs timeline builder in `presentation/http/runs.py`.
- Some onboarding prose predates the Pi deployment. Consult source, the latest
  commits, and `WORKLOG.md` for implementation state; record spec disagreements.
- Use the dev profile and fixtures for checks. Secrets and live runtime data
  are not repository context. Deploying or changing host schedules is separate
  from editing and testing code.

## Graphify context

The local graph is `graphify-out/graph.json`; its report and interactive view
are `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.html`.
Use graph queries to find relationships, then verify behavior in source:

```sh
graphify query "HostedRepoReader PanelRenderer StatusAssembler" --budget 1500
graphify explain "StatusAssembler"
graphify explain "scripts/repos.py"
graphify path "HostedRepoReader" "PanelRenderer"
```

Run these from the repository root. If Graphify is absent from PATH, the local
installation is normally `~/.local/bin/graphify`. Installation, refresh, and
hook details are in `docs/repo-map.md`.

The graph is a local AST index, including SQL schema relationships and document
headings; documentation was read manually and summarized in the repo map.
The generated graph is undirected: paths show connections, not call direction.
Static extraction can miss
dependency injection, HTTP URLs, and dynamic dispatch. Graph connectivity is
navigation evidence, not proof that a runtime path is exercised.

After source edits, run `PYTHONHASHSEED=0 graphify update .` before relying on
the index. Installed Git hooks also refresh it after commits and branch
switches; uncommitted edits require the explicit update. Keep generated graph
artifacts ignored. Update `docs/repo-map.md` when architecture or task status
changes.

## Verification

From `runner/`, use the locked environment:

```sh
uv run --frozen pytest -m "not mqtt"
uv run --frozen mypy src tests
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen atlas validate-jobs --jobs-dir ../jobs
```

The MQTT integration test requires a live broker and is excluded from the
normal hermetic suite. Do not start the production service to run checks.

## Refreshing the monitor after changes

The user wants dashboard changes visible on the screen. Refresh after a
validated batch of changes, not every saved file: the API process also owns
the scheduler. The running system service uses `/opt/atlas/runner`, whereas
this development checkout is `/home/domdd/atlas-deploy`.

For user-requested dashboard work, apply the tested, reviewed patch to the
live checkout while preserving unrelated edits, then run
`bash scripts/refresh-dashboard.sh`. A restart alone does not copy code from
the development checkout. Verify `/healthz`, `/api/status`, and the actual
monitor after refreshing. This routine refresh is already user-authorized.

The browser compares its asset content version with `/api/status` every ten
seconds and reloads when they differ, including for uncommitted UI edits.
The current kiosk runs under the user unit `atlas-kiosk-session.service`.
Local deployed edits remain subject to the normal release deploy timer;
record them as local until committed and shipped through CI.
