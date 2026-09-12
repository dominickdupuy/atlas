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
docker compose logs -f matter-server        # first start migrates nothing; expect a line showing it listening on 127.0.0.1:5580
```

### Commissioning a bulb that lives in Apple Home

The bulbs stay in Apple Home; atlas joins as a second admin. `atlas lights`
opens its own short-lived connection to the controller, so it needs
`ATLAS_MATTER_WS_URL` even though the CLI is run by hand: that variable
lives in `/etc/atlas/atlas.env`, which systemd loads for the `atlas`
service but an interactive shell does not, so each command below sets it
inline. Per bulb:

1. Home app, the bulb, settings, **Turn On Pairing Mode**. Note the code.
2. On atlas, from `/opt/atlas/runner`:
   `ATLAS_MATTER_WS_URL=ws://127.0.0.1:5580/ws uv run --frozen atlas lights commission <code>`.
   The controller finds the bulb on the LAN and prints the new node ID.
3. `ATLAS_MATTER_WS_URL=ws://127.0.0.1:5580/ws uv run --frozen atlas lights identify <node_id>`
   blinks it. Add it to `lights.yaml` under its name (`ceiling-1` to `ceiling-4`).
4. After all four: commit `lights.yaml`, `sudo systemctl restart atlas`.

`ATLAS_MATTER_WS_URL=ws://127.0.0.1:5580/ws uv run --frozen atlas lights nodes` lists
what the controller knows, with names. A failed commission prints the
controller's error name; `NodeCommissionFailed` usually means the Apple
Home pairing window expired. Open it again and retry.

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

## Voice through the phone

`POST /api/voice {"text": "..."}` returns `{"speech", "intent", "tier", "result"}`
(D29). Tier 1 is offline and deterministic; tier 2 asks Claude to classify and
validates the answer before anything moves. Every utterance is logged and
never purged. From `/opt/atlas/runner` on atlas:

```sh
cd /opt/atlas/runner && uv run --frozen atlas voice log --tier 2
```

This lists what tier 1 could not handle, which is the promotion queue for
new tier-1 rules (D26). The command reads the SQLite path from settings, so
it follows `ATLAS_DB_PATH` if that is set in `/etc/atlas/atlas.env`, and
otherwise falls back to the default under `/opt/atlas/runner/data/`.

### The `Atlas` Shortcut (D24)

One shortcut. Invocation: "Hey Siri, Atlas", then speak.

1. *Text*, value set to **Ask Each Time**.
2. *Get Contents of URL*: URL `https://atlas.tail5c9e82.ts.net/api/voice`,
   Method POST, Headers `Authorization: Bearer <token>` and
   `Content-Type: application/json`, Request Body JSON with key `text` = the
   Text variable.
3. *Get Dictionary Value*: key `speech` from Contents of URL.
4. *Speak Text* with that value.

The token is `ATLAS_API_TOKEN` from `/etc/atlas/atlas.env` on atlas; read it
with `sudo cat`, type it into the Shortcut once. The phone needs the Tailscale
app signed in and connected. Siri's own "turn off the lights" through Apple
Home stays as the fallback that works when atlas is down.

### What tier 1 understands

Targets: `ceiling 1` to `ceiling 4`, `bedroom`, `all`/`everything`/`the lights`.
Hyphenated light names are spoken with a space: "ceiling 1", not "ceiling-1".
Power: on, off, toggle. Brightness: "to 40 percent". Colour: red, orange,
amber, yellow, green, teal, blue, purple, pink, white; warm, neutral, cool.
Scenes by name, with "mode"/"lights"/"scene" ignored: "evening mode". A weak
fuzzy match on a scene name is not acted on directly; it falls through to
tier 2 for confirmation. Queries: "is ceiling one on", "are the lights on".
Filler words such as "hey atlas", "lamp", "in", and "now" are ignored, and a
timing word ("in five minutes", "later") makes tier 1 defer to tier 2 instead
of guessing a schedule it cannot keep.
