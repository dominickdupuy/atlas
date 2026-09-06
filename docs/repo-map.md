# Atlas repository map

Prepared 2026-09-05 from branch `runs-timeline`, commit `99791b1`. Checkout on
this host: `/home/domdd/atlas-deploy`; origin: `dominickdupuy/atlas`.
Updated later on 2026-09-05: the user requested queued runs on the monitor and
refreshes after changes. That dashboard work is now implemented and applied
locally to the running checkout at `/opt/atlas`; `status.json` refresh is deferred.

Evening of 2026-09-05: the header's drawn ColoPlay deck, the dial's day panning
(Shift+Alt+Digit5/6, a fortnight of `timeline_days`, `timeline_visible_days`)
and their tests were **recovered from session transcripts** after the deploy
timer's `git reset --hard` at 16:44 wiped them as uncommitted edits in
`/opt/atlas`. Also applied: Atlas runs shown (labelled `stub`) with repeated
outcomes collapsed, a two-column Runs panel, the System card trimmed to host
metrics with load as a percentage of cores (`load_percent`), one-line weather
metric tiles, and the `calendar-today` / `morning-briefing` jobs retired
(definitions deleted; a retired job's history leaves the Runs panel with it).
Later still: the Runs panel is back to one column with larger rows, and a
hosted job can put figures on it. `scripts/repos.py` keeps the last
`atlas-summary {json}` line of a job's log under `summary` in
`/var/lib/atlas-repos/<name>.json`; `HostedRepoReader` passes integer values
through; `runs.py` renders the known keys (transactions, reviewed,
uncategorized, decisions, rules) on the run's detail. The finance repo prints
that line from `fintrack.autoreview` (pushed as `af40cc8` on
`dominickdupuy/finances`); the first run after that commit populates it.
All of this is uncommitted in both checkouts until shipped through CI — the
next release deploy will erase it from `/opt/atlas` again.

## Runtime structure

Atlas is an always-on personal automation host for a Raspberry Pi 5. Its
custom service is a Python modular monolith; Mosquitto, ntfy, Home Assistant,
and optional voice/MCP services are separate processes defined in Compose.

| Area | Start here | Responsibility |
| --- | --- | --- |
| CLI and config | `runner/src/atlas/__main__.py`, `config.py` | Serve, execute a child job, validate YAML, migrate; settings precedence and profiles |
| Composition | `runner/src/atlas/bootstrap/container.py`, `child.py`, `connectors_factory.py` | Parent wiring and separate child wiring; real versus stub adapters |
| Jobs | `runner/src/atlas/jobs/` | YAML catalog, timezone-aware cron, run lifecycle, subprocess launch, mode gate |
| Approvals | `runner/src/atlas/approvals/` | Freeze proposed actions, notify, decide idempotently, expire by TTL |
| Budget | `runner/src/atlas/budget/` | Token costs in integer micro-dollars, ledger, daily ceiling, scheduler pause |
| Connectors | `runner/src/atlas/connectors/` | Scoped tool gateway; tier executors; Anthropic, MCP, ICS, weather, ntfy adapters |
| Telemetry | `runner/src/atlas/telemetry/` | MQTT publisher, in-process SSE stream, health, host metrics, hosted repo reader |
| Persistence | `runner/src/atlas/persistence/` | SQLite connection and migrations: job_runs, approvals, budget_ledger |
| Presentation | `runner/src/atlas/presentation/` | FastAPI/auth, status assembly, Jinja/htmx panels, passive board JavaScript/CSS |
| Hosted repos | `scripts/repos.py`, `infra/repos.toml`, `docs/repos.md` | Ordinary external projects, cron, queued runs, systemd services, logs/state |
| Deployment | `scripts/deploy.sh`, `.github/workflows/ci.yml`, `infra/systemd/` | CI promotes green main to release; deploy timer follows release |

The job execution path is `CronScheduler` -> `ExecuteJobService` -> budget
preflight -> `SubprocessJobLauncher` -> `python -m atlas execute-job` -> tier
executor and tool gateway. The child returns NDJSON; the parent records costs,
persists the result, gates proposed writes, and publishes events. Child code
does not own SQLite or MQTT. UI SSE is backed by `InProcessEventBus` and keeps
working if external MQTT telemetry is unavailable.

Tier 1 is deterministic, tier 2 adds one summary call, and tier 3 is currently
a bounded single planning round. Voice, the full tier 3 agent loop, and some
production MCP wiring are deliberately deferred. Consult the architecture
decisions before expanding them.

## Two dashboard paths

| Route | Rendering and refresh | Main code |
| --- | --- | --- |
| `/dashboard` | Passive board; polls `/api/status` every 10 seconds, 8-second fetch timeout, stale after 25 seconds | `presentation/templates/board.html`, `static/board.js`, `http/status.py` |
| `/` | Jinja/htmx board; SSE and `/partials/{panel}` refreshes | `presentation/templates/dashboard.html`, `http/panels.py`, `http/routers/events.py` |
| `/api/status` | JSON assembled on each request by `StatusAssembler.snapshot()` | `presentation/http/routers/status.py`, `http/status.py` |

The paths above are relative to `runner/src/atlas/`. Both HTML routes are
registered by `presentation/http/routers/dashboard.py`. The API factory in
`presentation/http/app.py` wires the assemblers and starts the scheduler,
MQTT loop, approval sweep, and health heartbeat. Auth accepts the configured
bearer token or the cookie established by an initial token-bearing visit.

The combined Atlas/hosted-repo Runs timeline now lives in
`presentation/http/runs.py`, shared by `PanelRenderer._timeline_context()`
and `StatusAssembler`. `/api/status` exposes it as `run_timeline`, and
`board.js` renders pending work above history, with origin/state labels and
counts for omitted entries. In the dev profile, the passive board hides
stub Atlas work while still showing real hosted-repo queues and history.
Running hosted jobs are pending work, not failures.

`presentation/http/assets.py` computes a content hash of the board HTML,
CSS, and JavaScript. The server pins it at startup, includes it in the HTML
and `service.asset_version`, and the browser reloads on a changed version.
Asset URLs use that hash; the HTML response is `Cache-Control: no-store`.
After applying validated changes to `/opt/atlas`, run
`bash scripts/refresh-dashboard.sh` to restart the runner and check health.
This is a refresh after validated changes, not a file-save watcher.

## Follow-up task status

Both were originally deferred until after repository understanding and
Graphify setup. The later request to show queued runs authorized task 2.

1. **Refresh `status.json` so it does not become stale.**
   `scripts/repos.py:status()` writes `/var/lib/atlas-repos/status.json` with
   `generated`, `repos`, and `upcoming`. Currently only the CLI `status`
   command writes it. `render_cron()` schedules `tick` once a minute, but
   `tick()` returns immediately when no queued work is due; it never refreshes
   this snapshot. `run_job()` instead writes `<name>.json` at start and finish.
   Read `write_json`, `run_job`, `enqueue`, `tick`, `status`, and `render_cron`
   before choosing a refresh lifecycle. Validate idle refresh as well as run
   transitions, queued changes, timestamp freshness, and readable atomic output.
2. **Use `/api/status` for the dashboard — implemented locally.**
   The monitor's `/dashboard` now receives the combined Runs timeline through
   `StatusAssembler` and `board.js`. API tests cover rereading queued files,
   active hosted jobs, stub profiles, ordering, and truncation counts. A real
   Chromium check verified visible queued finance work, overflow counts,
   retained data during API failures, and one reload on an asset change.

`HostedRepoReader` intentionally avoids `status.json` today because it is not
refreshed automatically. It reads `infra/repos.toml`, per-repo summaries,
and `queue.json` directly and computes cron fires. Any later switch to the
snapshot must account for missing, malformed, and old files. Its `failed`
property now excludes both `ok` and `running`; unknown states still fail visibly.

Useful existing coverage:

- `runner/tests/unit/telemetry/test_hosted_repos.py`
- `runner/tests/integration/test_dashboard_runs_timeline.py`
- `runner/tests/integration/test_api_status.py`
- `runner/tests/integration/test_api_dashboard.py`
- `runner/tests/integration/test_api_auth.py`

The stdlib-only `scripts/repos.py` is outside the runner's normal mypy/ruff
targets. Its later refresh change needs isolated temporary-state tests;
do not invoke `apply`, `tick`, or `run` against live hosted repos to test it.

## Graphify setup

Tool: `graphifyy[sql]==0.9.55`, installed in uv's isolated tool environment.
The graph uses local AST extraction, including Python, JavaScript, shell,
dependency manifests, SQL, and document headings added by the update pass.
No external LLM extraction is used. Documentation semantics and deployment
formats skipped by the parser are covered by this manual map.
`.graphifyignore` excludes credentials, runtime state, caches, and vendored
minified JavaScript. Generated output is gitignored.

Reproduce from the repository root:

```sh
uv tool install 'graphifyy[sql]==0.9.55'
PYTHONHASHSEED=0 graphify extract . --code-only --no-cluster --max-workers 2
PYTHONHASHSEED=0 GRAPHIFY_MAX_WORKERS=2 graphify update .
graphify hook install
graphify hook status
```

For later source changes: `PYTHONHASHSEED=0 graphify update .`. Use `--force`
only after checking that a smaller graph reflects intentional removals.
The report is `graphify-out/GRAPH_REPORT.md`, the queryable graph is
`graphify-out/graph.json`, and the interactive view is `graphify-out/graph.html`.
Keep those generated files local; commit the instructions and ignore rules.
The initial preparation graph had 1,535 nodes, 3,917 edges, and 107 communities;
later refreshes include dashboard changes. Graphify's graph is undirected, so
a path identifies structural connections;
check source before interpreting it as a directional call or impact chain.

`graphify hook install` installs Git `post-commit` and `post-checkout` hooks,
preserves other hook content, and registers its graph merge driver. Hooks
rebuild code locally in the background and log to
`~/.cache/graphify-rebuild.log`. They do not refresh on every uncommitted edit.
The installation is local to this clone; rerun it on another machine.
The installed checkout hook was smoke-tested directly: it rebuilt the graph
in the background without changing the branch or commit.

`.codex/hooks.json` adds a read-only `SessionStart` hook for startup, resume,
clear, and compaction. It loads graph counts and these notes; it does not
rebuild the graph, start services, or call an LLM. Graphify 0.9.55's bundled
Codex `hook-check` is an intentional no-op, so Atlas uses this explicit
context hook instead. `AGENTS.md` provides persistent graph guidance too.

Start Codex in the Atlas repository. Codex requires review of a new hook
definition in `/hooks` before it runs; this setup does not bypass that trust
mechanism. See the [official hook documentation](https://learn.chatgpt.com/docs/hooks).
Graphify commands are documented in its [CLI reference](https://graphify.com/docs/cli).

## Verified baseline

On 2026-09-05, using the locked runner environment:

- `pytest -m "not mqtt" -q`: **223 passed, 1 deselected**.
- `mypy src tests`: clean, 137 source files.
- `ruff check .`: clean.
- `ruff format --check .`: 138 files already formatted.
- `atlas validate-jobs --jobs-dir ../jobs`: three valid definitions.

After the dashboard update: **230 tests passed, 1 deselected**; mypy was clean
on 140 source files, ruff and formatting passed (141 files), and all three
job definitions validated. Chromium checks used fixtures without running
the Atlas scheduler or contacting external feeds.

The tested dashboard patch was applied to `/opt/atlas` over `19277e7`, the
runner restarted, and the kiosk browser relaunched under the transient user
unit `atlas-kiosk-session.service`. The installed release deploy timer is
unchanged; these are uncommitted local edits until shipped through CI.
The pre-update files and patch are in `/tmp/atlas-dashboard-deploy-n9pb07il`
on this host for rollback. Hosted schedules and credentials were unchanged.

The excluded MQTT test requires a live broker.
`docs/development.md` predates some deployment
work (for example, its claim that the Pi has never run the stack);
use the source and recorded deployment work rather than treating that
historical status paragraph as current.
