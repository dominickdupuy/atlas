# Lights over HTTPS, and the phone as the voice front end

Design for two capabilities atlas does not have today:

1. **Lights.** Four Matter bulbs controlled from the runner, over HTTPS, from a
   phone or PC on the tailnet. Full control: on/off, brightness, colour
   temperature, hue and saturation, and named scenes. No approval step.
2. **Voice through the phone.** An iOS Shortcut captures speech, posts the
   transcript to the runner over the same HTTPS front door, and speaks the reply.
   The runner is the brain; the phone is the ears and, for now, the mouth.

Both ride one HTTPS name, `atlas.tail5c9e82.ts.net`, served by the Pi's existing
Tailscale node. Nothing is public. D12 stands.

Home Assistant is **not** in the loop. The runner talks to a standalone Matter
controller over a loopback WebSocket.

---

## 1. Facts established on 2026-09-12

Queried on atlas over ssh. These shape every choice below.

| Fact | Value | Consequence |
|---|---|---|
| Bulbs | Lightinginside E12 candelabra RGBCW, four, Matter over 2.4 GHz Wi-Fi | ColorControl is required. No Thread hardware needed. |
| Bulb state | All four commissioned to Apple Home; Apple Home stays as an admin | Commission with a temporary pairing code from Apple Home, `network_only: true`. No Bluetooth, no Wi-Fi password, no factory reset. |
| Matter controller | python-matter-server 8.1.2 is its declared final release; successor is `matterjs-server`, same WebSocket API, v1.4.0 (2026-08-07), `1.4.0` tag published for arm64 | Pin `ghcr.io/matter-js/matterjs-server:1.4.0`. See D21. |
| IPv6 on wlan0 | Link-local and global addresses; forwarding off | Matter's mDNS and operational traffic work as-is. |
| LAN | Pi at 192.168.0.38 on wlan0; bulbs on the same 2.4 GHz network | Same L2 segment, which Matter requires. |
| Ports 5580 and 443 | Free | Controller on 5580 loopback; Tailscale serve on 443. |
| Tailscale | Node `atlas`, MagicDNS `atlas.tail5c9e82.ts.net`, that name is an issued cert domain, no serve config yet | `tailscale serve` can terminate TLS today. HTTPS certs are already enabled on the tailnet. |
| Sudo | Password required for `domdd`; only `systemctl restart atlas` is passwordless | `tailscale serve` is an operator step. Docker needs no sudo (`domdd` is in `docker`). |
| Runner | Native under uv, `atlas.service`, listens on 127.0.0.1:8100; secrets in `/etc/atlas/atlas.env` | Unchanged. Config lands in that env file and `/opt/atlas/config.toml`. |
| Other stack | A Home Assistant container from `/opt/stack` holds the Bluetooth adapter | Irrelevant once commissioning is network-only. Left alone. |
| Pairing codes | Four 11-digit codes from the box, stored at `~/.config/atlas/matter-pairing-codes` (mode 600) on atlas | Not needed while the bulbs stay in Apple Home. Kept for a factory-reset day. Never in git. |
| Speaker | None connected to the Pi. A Beats Pill (Bluetooth A2DP) exists | Room-speaker replies are a later phase gated on a speaker test (section 8). |

---

## 2. Decisions

These extend `docs/architecture.md`. The owner records them there; this spec
proposes the wording.

**D20. Lights is a bounded context.** `atlas/lights/{domain,application,infrastructure}`.
Naming, capability rules, colour maths, and scenes are domain logic, not adapter
glue. The Matter WebSocket client is that context's infrastructure. `connectors`
stays the tool-gateway context; when jobs or voice need lights as a tool, the
gateway calls the lights application service in-process. No new process, no MCP
wrapper. This is D19 applied to a loopback socket: the controller is one
unauthenticated local endpoint, so it is a plain client inside the runner.

**D21. Matter controller is `matterjs-server`, pinned.** `ghcr.io/matter-js/matterjs-server:1.4.0`,
host networking, loopback listener, named volume, no Bluetooth. python-matter-server
is end-of-life upstream; the WebSocket contract is the same, so the runner is
indifferent. Upgrade procedure is the compose one: bump the tag, pull, watch the
board, commit.

**D22. The phone is the first voice front end.** `POST /api/voice` takes text and
returns text to speak. An iOS Shortcut is the client. The Wyoming pipeline in
`docs/voice.md` becomes a second client of the same endpoint, later, if a room
microphone is ever wanted. Phase 6 is no longer blocked on hardware.

**D23. HTTPS is Tailscale serve, not a reverse proxy in the stack.** The runner
keeps listening on loopback. `tailscaled` terminates TLS with the tailnet cert and
proxies to 127.0.0.1:8100. No Caddy, no certificate files, no new container.
Bearer auth stays exactly as D16 specifies.

---

## 3. Matter controller deployment

New service in `compose.yaml`, profile `matter`:

```yaml
  matter-server:
    image: ghcr.io/matter-js/matterjs-server:1.4.0
    profiles: [matter]
    restart: unless-stopped
    network_mode: host          # mDNS and IPv6 link-local; not optional for Matter
    environment:
      LISTEN_ADDRESS: 127.0.0.1 # the WebSocket has no auth; loopback only
      STORAGE_PATH: /data
      LOG_LEVEL: info
    volumes:
      - matter-data:/data       # fabric keys; a deploy's git reset must never touch these
```

Why these and not more:

- **No `/run/dbus`, no `BLUETOOTH_ADAPTER`.** Network-only commissioning needs
  neither. Dropping them keeps the container unprivileged with no host sockets.
- **Named volume, not a bind mount under `/opt/atlas`.** `scripts/deploy.sh` runs
  `git reset --hard` there. The image runs as UID 1000, which is `domdd`, so the
  volume needs no ownership fix.
- **`LISTEN_ADDRESS=127.0.0.1`.** The server's dashboard and WebSocket are
  unauthenticated. Loopback plus an ssh tunnel is how an operator reaches the
  dashboard for debugging.
- **Internet egress** is required for device attestation against the Matter DCL.
  The Pi has it.

Started by hand once: `docker compose --profile matter up -d matter-server` from
`/opt/atlas`. `restart: unless-stopped` covers reboots. The existing
`atlas-compose.service` unit is not active on atlas; this spec does not change that.

Health: the runner's `/api/status` gains a `matter` service probe (TCP 5580), the
same shape as the existing probes, so the board shows the controller like it shows
Mosquitto.

---

## 4. The `lights` bounded context

### 4.1 Domain (`lights/domain/`)

Pure. No I/O, no clock reads, no Matter client.

```
Light           name (slug), node_id, endpoint_id, features: {dimming, color_temperature, color}
                color_temp_range_k: (min, max)      # from the bulb's mireds attributes
LightState      on, brightness (0-100), color_temp_k, hue (0-360), saturation (0-100),
                reachable, observed_at
LightCommand    on | brightness | color_temp_k | (hue, saturation) | transition_ms
Scene           name, states: {light name -> LightCommand}
ClusterCommand  endpoint_id, cluster_id, name, payload      # what the controller sends
```

Rules the domain owns:

- `LightCommand` validation: `color_temp_k` and `hue/saturation` are mutually
  exclusive. `brightness == 0` means off. `brightness > 0` implies on unless `on`
  is explicitly false. Ranges are checked here, once.
- `plan(light, command) -> list[ClusterCommand]`. Translates a command into
  Matter cluster commands in the order the bulb needs them. Colour before level
  before on/off, so a bulb never flashes its old colour. Rejects a feature the
  light lacks with a typed error rather than sending a command the bulb ignores.
- `decode(light, attributes) -> LightState`. Reads the attribute map the
  controller reports and produces state. Unknown attributes are ignored.
- Unit conversions live here and nowhere else:
  - brightness percent to Matter level: `round(pct / 100 * 254)`, minimum 1 when on.
  - kelvin to mireds: `round(1_000_000 / K)`, clamped to the bulb's physical range.
  - hue degrees to Matter hue: `round(deg / 360 * 254)`; saturation percent to `round(pct / 100 * 254)`.
  - `transition_ms` to Matter tenths of a second.

Matter identifiers the domain encodes as constants:

| Cluster | ID | Attributes read | Commands sent |
|---|---|---|---|
| OnOff | 6 | 0 OnOff | `on`, `off`, `toggle` |
| LevelControl | 8 | 0 CurrentLevel | `moveToLevelWithOnOff {level, transitionTime, optionsMask: 0, optionsOverride: 0}` |
| ColorControl | 768 | 0 CurrentHue, 1 CurrentSaturation, 7 ColorTemperatureMireds, 8 ColorMode, 16395 ColorTempPhysicalMinMireds, 16396 ColorTempPhysicalMaxMireds | `moveToColorTemperature {colorTemperatureMireds, transitionTime, optionsMask, optionsOverride}`, `moveToHueAndSaturation {hue, saturation, transitionTime, optionsMask, optionsOverride}` |
| BasicInformation | 40 | 1 VendorName, 3 ProductName, 5 NodeLabel | none |

Command names are matter.js camelCase, taken from the server's API document. The
first integration run against a real bulb confirms them; the plan has that step.

### 4.2 Application (`lights/application/`)

```
MatterController (port, Protocol)
    async def nodes() -> list[MatterNode]
    async def send(node_id, endpoint_id, cluster_id, name, payload) -> None
    def subscribe(callback: (node_id, attribute_path, value) -> None) -> None
    async def commission_with_code(code, network_only) -> int          # node_id
    async def server_info() -> ServerInfo

LightsRegistry
    Loaded from lights.yaml. Maps names to node and endpoint IDs, holds scenes.
    Validated at startup like jobs/ is validated: unknown scene light, duplicate
    name, and bad ranges fail loudly at boot, not at 11pm.

LightsService
    list() -> LightsSnapshot
    get(name) -> LightState
    apply(name, LightCommand) -> LightState
    toggle(name) -> LightState
    scenes() -> list[Scene]
    activate(scene_name) -> LightsSnapshot
```

`LightsSnapshot` carries `fetched_at` and `error`, the same last-good-data shape
the weather and calendar reports use, so a reader can see staleness instead of
inferring it.

State is a cache the service owns. After `start_listening`, the controller pushes
every attribute change; the cache updates from those events, and a read never
round-trips to a bulb. `apply` sends the planned commands, then waits up to one
second for the matching attribute events before returning the cache. If nothing
arrives it returns the last-known state with `error` set; the bulb may still have
obeyed. This keeps a POST under a second even when a bulb is slow.

Scenes fan out one `apply` per light concurrently and return when all have
settled or timed out. A scene with one unreachable bulb still sets the others.

### 4.3 Infrastructure (`lights/infrastructure/`)

**`matter_ws.py`, `MatterWsClient`.** A long-lived client, modelled on
`telemetry/infrastructure/mqtt_bus.py`:

- `run()` coroutine started as a fifth lifespan task in `presentation/http/app.py`.
- Connects to `ws://127.0.0.1:5580/ws`. Reads the initial `server_info`, refuses a
  `schema_version` outside `[11, 13]` (logs and backs off rather than guessing),
  sends `start_listening`, stores the returned nodes, then loops on events.
- Requests carry a `message_id`; responses resolve a future keyed on it. An
  `error_code` in the reply becomes a typed `MatterError(code, details)`.
- Reconnect with the existing `ReconnectBackoff` (1 s to 60 s, jitter). While
  disconnected, `send` fails immediately with `ControllerUnavailable`; nothing
  queues, because a light command executed a minute late is worse than one
  refused.
- Injectable `connect` factory and `sleep`, the way the MQTT adapter does it, so
  unit tests drive it with an in-memory fake socket.
- Node IDs are parsed by Python's `json` module, which keeps big integers exact.
  No float path touches an ID.

**Dependency.** `websockets` 17.0.1 is already in `uv.lock` as a transitive of
`uvicorn[standard]`. It becomes an explicit runtime dependency at that exact
version. No new package enters the supply chain.

**`stubs.py`, `StubMatterController`.** Four canned bulbs with the real
attribute paths, used by the dev profile and by tests. Records every `send`.

### 4.4 Configuration

`config.py` gains, following the `calendar_ics_url` pattern:

```
matter_ws_url: str = ""                 # ATLAS_MATTER_WS_URL; empty means lights are off
lights_file: Path = <repo>/lights.yaml  # ATLAS_LIGHTS_FILE
```

The capability is gated on the URL, not on the profile. Dev with the stub
controller sets the URL to a sentinel the factory recognises, so the routes and
the board are exercisable with no Pi.

`lights.yaml` at the repo root, next to `jobs/`:

```yaml
lights:
  desk:        { node_id: 1, endpoint_id: 1 }
  bedside:     { node_id: 2, endpoint_id: 1 }
  shelf-left:  { node_id: 3, endpoint_id: 1 }
  shelf-right: { node_id: 4, endpoint_id: 1 }

scenes:
  evening:
    desk:    { brightness: 40, color_temp_k: 2700 }
    bedside: { brightness: 20, color_temp_k: 2200 }
  off:
    desk: { on: false }
    bedside: { on: false }
    shelf-left: { on: false }
    shelf-right: { on: false }
```

Node IDs are assigned at commissioning, so the file is filled in during section 6
and committed afterwards. They are not secrets. Features and colour ranges are
read from the bulb, not written here.

---

## 5. HTTP surface

All under the existing `BearerAuthMiddleware`. JSON in, JSON out. Errors are
`JSONResponse` with a status code, matching `approvals.py`.

```
GET  /api/lights                          200 LightsSnapshot
GET  /api/lights/{name}                   200 LightState | 404
POST /api/lights/{name}                   200 LightState | 404 | 422 (bad command) | 409 (feature unsupported) | 503 (controller down)
     {"on": true, "brightness": 60, "color_temp_k": 2700, "transition_ms": 500}
     {"hue": 30, "saturation": 80}
POST /api/lights/{name}/toggle            200 LightState
GET  /api/lights/scenes                   200 [Scene]
POST /api/lights/scenes/{name}/activate   200 LightsSnapshot | 404
```

`503` is the only case where the caller should retry; it means the WebSocket is
down, and the backoff is already reconnecting.

There is no commissioning route. Pairing is an operator action with physical
presence (section 6). Keeping it off the authenticated surface keeps that surface
small.

---

## 6. Commissioning, as a CLI

`python -m atlas lights ...` subcommands, wired in `__main__.py` beside `serve`
and `validate-jobs`. Each opens its own short-lived `MatterWsClient`; the running
service is not involved and need not be restarted.

```
atlas lights nodes                     # what the controller knows: node_id, vendor, product, label, available
atlas lights commission <code>         # commission_with_code, network_only=true; prints the new node_id
atlas lights identify <name>           # blinks the bulb (Identify cluster 3) so a node_id gets the right name
atlas lights remove <node_id>          # remove_node; asks for confirmation
```

Procedure per bulb, since they live in Apple Home:

1. Home app, bulb, settings, **Turn On Pairing Mode**. Apple shows a temporary
   setup code and the bulb stays commissionable for a few minutes.
2. `atlas lights commission <that code>`. The controller finds the bulb on the LAN
   by mDNS, performs PASE and CASE, and adds it to the atlas fabric as a second
   admin. Apple Home is untouched.
3. `atlas lights identify` against the new node to see which bulb it is, then
   add it to `lights.yaml` under a name.

Four bulbs, four rounds, then commit `lights.yaml`. The printed codes in
`~/.config/atlas/matter-pairing-codes` stay unused unless a bulb is ever reset.

---

## 7. Voice through the phone

### 7.1 Endpoint

```
POST /api/voice     {"text": "turn the desk lamp to forty percent"}
                    200 {"speech": "Desk at forty percent.", "intent": {...} | null}
```

Built in two steps so the Shortcut can be finished on day one:

1. **Echo.** Returns `{"speech": "Heard: <text>"}`. The phone side is built and
   tested against this.
2. **Light intents.** A small deterministic grammar in a `voice` context
   (`voice/domain/intent.py`, `voice/application/parse.py`,
   `voice/application/handle.py`): on, off, toggle, brightness by percent, warm
   and cool, a fixed colour-word table, and scene names. Light names and scene
   names come from the registry, so the grammar cannot drift from
   `lights.yaml`. Anything it cannot parse returns a polite refusal with the
   heard text, and `intent: null`. No model call. A tier 2 or tier 3 fallback
   for open-ended requests is phase 7 work and is explicitly out of scope here.

`speech` is plain text, short, and always present, because the Shortcut speaks it
verbatim.

### 7.2 iOS Shortcut

Two shortcuts, both calling the same endpoint. The owner builds these (section 10).

- **"Atlas"**: Dictate Text, then *Get Contents of URL* `POST
  https://atlas.tail5c9e82.ts.net/api/voice`, headers
  `Authorization: Bearer <ATLAS_API_TOKEN>` and `Content-Type: application/json`,
  body `{"text": <dictated>}`; *Get Dictionary Value* `speech`; *Speak Text*.
- **Fixed phrases** such as "Lights off": the same request with a constant body.
  One utterance, no dictation turn.

Token handling: the token is read off the Pi with `cat /etc/atlas/atlas.env`
and typed into the Shortcut once. It never passes through chat.

### 7.3 Phone prerequisites

- Tailscale app on the iPhone 17, signed into the same tailnet, connected when
  the Shortcut runs. The tailnet already has MagicDNS and HTTPS certificates on,
  which is what makes `atlas.tail5c9e82.ts.net` resolve and verify.
- Nothing else. No profile, no certificate install: the cert chains to a public CA.

---

## 8. Room speaker replies (deferred, designed)

`POST /api/say {"text": ...}` runs local TTS and plays through a speaker on the
Pi, so the room answers even though the phone listened. It is not in the first
build because no speaker is attached and the Bluetooth candidate is a poor fit:

- The Beats Pill sleeps when idle; the first word after a pause waits 3 to 10 s
  on A2DP reconnect, which makes one-line replies feel broken. A silent keepalive
  tone every few minutes masks it at the cost of the Pill never sleeping.
- A wired USB or 3.5 mm speaker has none of that.

When a speaker exists: Piper TTS pinned (`rhasspy/wyoming-piper` is already the
planned image in `compose.yaml`, or the Piper binary natively), one voice model
pinned by file hash, playback via PipeWire in the `domdd` user session that the
kiosk already runs under. `/api/voice` gains an optional `"reply_via": "room"`
that routes the reply there instead of, or as well as, the phone. The endpoint
contract does not change.

---

## 9. HTTPS front door

Once, by the operator (needs sudo):

```
sudo tailscale serve --bg --https=443 http://127.0.0.1:8100
tailscale serve status
```

Then in `/etc/atlas/atlas.env`:

```
ATLAS_PUBLIC_URL=https://atlas.tail5c9e82.ts.net
```

so ntfy approval buttons use it too. Serve config persists across reboots.

Code change: `auth.py` sets the token cookie with `httponly` and `samesite=lax`
but not `secure`. Behind TLS it should. The kiosk loads the board over plain
loopback HTTP, where a `secure` cookie would be dropped, so the flag is set from
the request scheme (`X-Forwarded-Proto: https` from serve, or the ASGI scheme),
not unconditionally.

Not done: trusting `Tailscale-User-Login`. Serve adds it, but the runner also
serves the kiosk over loopback without it, and the bearer token already meets
D16. It stays available as a second factor later.

---

## 10. Owner tasks

Things only the repository owner can do. Everything else is the implementation
plan's job.

| # | Task | When |
|---|---|---|
| 1 | Run the `tailscale serve` command in section 9 | Before the first phone test |
| 2 | Put `ATLAS_MATTER_WS_URL=ws://127.0.0.1:5580/ws` and the `ATLAS_PUBLIC_URL` line in `/etc/atlas/atlas.env` (root-owned; the assistant cannot write it) | Before the runner restart that enables lights |
| 3 | Open pairing mode in Apple Home for each bulb and read out the code, four times | During commissioning |
| 4 | Build the two Shortcuts (section 7.2) against the echo endpoint | Any time after task 1 |
| 5 | Install and sign in to Tailscale on the iPhone 17 | Before task 4 |
| 6 | Decide on a speaker (section 8) | Whenever |
| 7 | Record D20 to D23 in `docs/architecture.md` and update the §11 open questions (devices and protocols are now known; voice STT is resolved to the phone) | After review |

---

## 11. Error handling and observability

- Controller unreachable: `503` on writes, snapshot with `error` on reads, board
  service tile red, event `atlas/lights/controller/down` and `.../up` on the
  transitions only, not every retry.
- Bulb unreachable: `reachable: false` in its state from the node's `available`
  flag; writes return the last-known state with `error`.
- Schema mismatch on `server_info`: refuse, log once at error level, back off.
  Never partially operate against an unknown schema.
- Every accepted write publishes `atlas/lights/<name>/changed` with the resulting
  state, so the board and any future job can react without polling (D6).
- Commissioning failures surface the controller's `error_code` name and
  `details` verbatim in the CLI. Code 1 is "commission failed", the common case
  when the Apple Home window has expired.

---

## 12. Testing

- **Domain**: pure unit tests for `plan`, `decode`, validation, and every
  conversion, including edge values (0 %, 100 %, kelvin outside the bulb's range,
  hue 360).
- **Adapter**: `MatterWsClient` driven by a fake socket in memory: handshake,
  schema refusal, request/response correlation, event dispatch, error mapping,
  reconnect after a dropped socket. No network; the suite already runs with
  sockets disabled.
- **Service**: stub controller; cache updates from events, `apply` waits for the
  echo, timeout path sets `error`, scene fan-out with one failing bulb.
- **HTTP**: integration tests through the ASGI transport with the stub injected
  by field assignment, as `test_api_approvals.py` does; every status code in
  section 5, plus auth rejection.
- **Voice**: parse table tests, one per phrase family, and the echo contract.
- **Registry**: a bad `lights.yaml` fails startup with a message naming the key.
- **On atlas**: the plan's last task commissions one bulb, runs each route with
  `curl` over the HTTPS name, and records the real command names and attribute
  paths observed, correcting section 4.1 if the bulb disagrees.

---

## 13. Out of scope, and why

- **Approvals for lights.** Owner's decision: low blast radius, direct write.
- **A lights panel on the wall board.** Not requested. The `changed` event makes
  it a small later addition.
- **Thread.** No Thread devices exist. The sysctl and border-router notes in the
  controller's OS requirements apply only then.
- **Exposing lights as a tool to tier 2/3 jobs.** The service is shaped for it;
  wiring it into the `ToolGateway` allowlist is a job-schema change for another
  spec.
- **Moving FreeReps behind the atlas Tailscale node.** Requested the same day,
  separate repository (`meltforce/FreeReps`), and not a config change: FreeReps
  derives user identity from its own tsnet node's WhoIs lookup
  (`server/internal/server/middleware.go`). Behind `tailscale serve` that lookup
  is gone and identity would have to come from the `Tailscale-User-Login`
  header, trusted only from the serve proxy on loopback. That is an auth change
  in FreeReps and needs its own design.

---

## 14. Open questions for the reviewer

1. Light names. The four placeholders in section 4.4 are guesses; the real names
   are chosen at commissioning.
2. Colour words for the voice grammar: a fixed table of a dozen (red, orange,
   amber, yellow, green, teal, blue, purple, pink, white, warm, cool) or fewer?
3. Whether `atlas/lights/<name>/changed` should also be emitted for changes made
   from Apple Home (the controller reports them too). Proposed: yes, since the
   board should reflect the room, not just atlas's own writes.
