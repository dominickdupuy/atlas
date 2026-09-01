# Operations runbook

Procedures for running and debugging atlas. Architecture and rationale live
in [architecture.md](architecture.md); this file is the "how do I" companion.

## Tokens and secrets

All secrets live in `.env` (gitignored; template in `.env.example`). Scoping
rules are D19: provider-side read-only scopes first, allowlists second.

| Secret | Scope | Rotation |
|---|---|---|
| `ATLAS_API_TOKEN` | The D16 HTTP API + dashboard | Regenerate, update `.env` and `/etc/atlas/kiosk.env`, restart runner + kiosk |
| `ANTHROPIC_API_KEY` | Reasoning tier | Rotate in the Anthropic console, update `.env` |
| `GITHUB_TOKEN` | Fine-grained PAT, read-only, named repos only | GitHub settings → fine-grained tokens |
| `ATLAS_NTFY_TOKEN` | Runner's ntfy publish access | See ntfy setup below |
| Google OAuth | Calendar read-only scope | Google Cloud console |

## ntfy setup (one-time)

ntfy is deny-by-default (`infra/ntfy/server.yml`). Create the two principals:

```sh
# The runner (publishes approval prompts):
docker compose exec ntfy ntfy user add --role=user runner
docker compose exec ntfy ntfy access runner atlas-approvals write-only
docker compose exec ntfy ntfy token add runner   # -> ATLAS_NTFY_TOKEN in .env

# You (subscribes on the phone):
docker compose exec ntfy ntfy user add --role=user dominick
docker compose exec ntfy ntfy access dominick atlas-approvals read-only
```

Then subscribe to `atlas-approvals` in the ntfy app, pointed at the server's
Tailscale address.

## Inspecting state

The runner's state is one SQLite file on the `atlas-data` volume.

```sh
docker compose exec runner python -m atlas migrate --status   # applied migrations
docker compose cp runner:/data/state.db ./state-inspect.db     # copy out, then:
sqlite3 state-inspect.db 'SELECT job_id, state, started_at FROM job_runs ORDER BY started_at DESC LIMIT 20;'
sqlite3 state-inspect.db 'SELECT approval_id, job_id, state, expires_at FROM approvals;'
sqlite3 state-inspect.db "SELECT DATE(recorded_at), SUM(cost_usd_micros)/1e6 FROM budget_ledger GROUP BY 1;"
```

## Watching events

```sh
docker compose exec mosquitto mosquitto_sub -t 'atlas/#' -v
```

Topic namespace is in the spec (D6). Every job must end in a terminal event;
if a job's `started` has no matching `completed`/`failed`/`awaiting_approval`,
that is itself the bug to chase (section 8).

## Debugging a job

Jobs run as short-lived subprocesses (D14). Reproduce one by hand, exactly as
the scheduler would run it:

```sh
cd runner
uv run atlas execute-job < request.json    # request.json: see tests/integration fixtures
```

The child writes NDJSON to stdout (final line is the RunReport) and logs to
stderr. The parent's logs prefix every relayed child line with
`[job_id/run_id]`.

## Logs

```sh
docker compose logs -f runner
journalctl -u atlas-kiosk -f        # on the Pi
```

## Vetting a community MCP server (D19)

Before any community server gets a credential:

1. Pin the exact release; read its token-handling and network code at that tag.
2. Confirm no telemetry or phone-home.
3. Grant the narrowest provider-side scope that works (read-only until D8
   writes are actually wanted).
4. Record the vetted tag and date in a comment next to its compose entry.

## Backups (on the Pi)

`restic` over the data volumes; exclude or encrypt secrets (section 8).
Config is reproducible from git — only `atlas-data` (SQLite) and
`homeassistant/` runtime state are worth backing up.
