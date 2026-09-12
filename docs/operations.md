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

## Lights (Matter)

The controller is `matter-server` in `compose.yaml` (profile `matter`), reached by
the runner at `ws://127.0.0.1:5580/ws`. Start it once; `restart: unless-stopped`
covers reboots.

```sh
cd /opt/atlas && docker compose --profile matter up -d matter-server
docker compose logs -f matter-server        # first start migrates nothing; expect "listening on 127.0.0.1:5580"
```

### Commissioning a bulb that lives in Apple Home

The bulbs stay in Apple Home; atlas joins as a second admin. Per bulb:

1. Home app, the bulb, settings, **Turn On Pairing Mode**. Note the code.
2. On atlas, from `/opt/atlas/runner`: `uv run --frozen atlas lights commission <code>`.
   The controller finds the bulb on the LAN and prints the new node ID.
3. `uv run --frozen atlas lights identify <node_id>` blinks it. Add it to
   `lights.yaml` under its name (`ceiling-1` to `ceiling-4`).
4. After all four: commit `lights.yaml`, `sudo systemctl restart atlas`.

`uv run --frozen atlas lights nodes` lists what the controller knows, with names.
A failed commission prints the controller's error name; `NodeCommissionFailed`
usually means the Apple Home pairing window expired. Open it again and retry.

### Controller dashboard

Unauthenticated, loopback only. From your machine:
`ssh -L 5580:127.0.0.1:5580 domdd@atlas` then open <http://127.0.0.1:5580/>.

### HTTPS front door

`sudo tailscale serve --bg --https=443 http://127.0.0.1:8100` once (D23).
`tailscale serve status` shows it. The runner stays on loopback. Set
`ATLAS_PUBLIC_URL=https://atlas.tail5c9e82.ts.net` in `/etc/atlas/atlas.env`.

### Control

```sh
T=$(sudo grep ATLAS_API_TOKEN /etc/atlas/atlas.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $T" https://atlas.tail5c9e82.ts.net/api/lights | jq
curl -s -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"brightness": 40, "color_temp_k": 2700}' https://atlas.tail5c9e82.ts.net/api/lights/ceiling-1
curl -s -X POST -H "Authorization: Bearer $T" https://atlas.tail5c9e82.ts.net/api/lights/scenes/evening/activate
```

Every confirmed change, from atlas or from Apple Home, publishes
`atlas/lights/<name>/changed`.
