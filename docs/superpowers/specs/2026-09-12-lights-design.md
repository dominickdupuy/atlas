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

The following six were written by the owner on 2026-09-12 and extend D22.

**D24. One voice door: the "Atlas" shortcut.** Invocation is "Hey Siri, Atlas"
followed by a dictated utterance. A single iOS Shortcut named `Atlas` captures
free text via Ask Each Time and POSTs it to `/api/voice`. Named per-command
shortcuts are NOT created: they fragment the grammar across the phone, where it
cannot be changed without editing Shortcuts by hand, and they compete with
Siri's own native handling of the bulbs. The phone parses nothing. It is a
microphone and a speaker. All interpretation lives in the runner, where it is
versioned, testable, and deployable.

Consequence to accept: for the bulbs already in Apple Home, Siri's native "turn
off the lights" remains faster and keeps working when Atlas is down. That path
is deliberately retained as the failure fallback. Atlas owns everything else.

**D25. No MCP server for lights.** The lights application service is in-process
per D20; the voice tier calls it directly through the connectors gateway. MCP
would serialize a call to ourselves. MCP is reconsidered only if an external LLM
client (e.g. Claude Desktop) needs lights as a tool. Not now.

**D26. Two-tier intent parsing, one output contract.** Both tiers emit the same
validated `Intent` object; the application service cannot tell which tier
produced it.

```
intent:  set_light | apply_scene | query | unknown
targets: list[str]        # resolved names, or ["all"]
state:   power, brightness_pct, color
scene:   str              # apply_scene only
```

Tier 1 is deterministic: keyword table plus regex over power words, percentages,
colour words, scene names, target names. Sub-millisecond, offline, no API cost.
It must handle the common utterances without network access, so lights still
respond when WAN is down.

Tier 2 fires only when tier 1 returns `unknown` or a partial parse. The utterance
goes to Claude with the schema and a JSON-only instruction; the response is
validated against the Pydantic model before anything touches a bulb. Validation
failure returns "didn't catch that" and changes nothing. Never act on an
unvalidated parse.

Every utterance is logged with the tier that handled it and the resulting
Intent. Frequent tier-2 hits get promoted into tier-1 rules. Tier 2 usage
trending toward zero is the success metric.

**D27. Scenes are config, not parsing.** Scenes live in a YAML file as a name to
list-of-target-states mapping. The parser's only job is fuzzy-matching a spoken
phrase to a scene key, so "morning", "morning mode", and "morning lights" all
resolve to `morning`. Adding a scene is a config change and never touches parser
code. The same file backs `GET /api/lights/scenes`.

**D28. Colour maths is domain logic.** The parser emits colour names only. The
domain layer owns the conversion: named colour to hue/saturation on the 0-254
scale for MoveToHueAndSaturation; colour *temperature* words ("warm", "cool") to
mireds for MoveToColorTemperature, which is a different cluster command despite
sounding like the same request; brightness percent to level as
round(pct * 254 / 100). Named colours come from a fixed table in the domain,
never from the LLM, so "red" is the same red every time. Note level 0 is not
equivalent to off on most firmware; power is always set explicitly.

**D29. Voice response contract.** `/api/voice` returns both a `speech` string and
the structured result (per the D22 note), so a future non-speaking client is not
parsing prose. Responses are short enough to speak: confirm what changed, do not
enumerate four bulbs.

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
- Power is always sent explicitly (D28). Level 0 is not off on most firmware,
  so `plan` never relies on a level command to switch a bulb off, and never
  relies on `moveToLevelWithOnOff` alone to switch it on.
- Named colours are a fixed table in this layer (D28): the parser hands over
  "red", the domain hands over hue and saturation. Colour-temperature words map
  to kelvin here too: warm 2700 K, neutral 4000 K, cool 5500 K, clamped to the
  bulb's range.
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

Governed by D22 and D24 to D29. This section says how those decisions land in
code.

### 7.1 Endpoint (D29)

```
POST /api/voice     {"text": "turn the desk lamp to forty percent"}
                    200 {"speech": "Desk at forty percent.",
                         "intent": {"intent": "set_light", "targets": ["desk"],
                                    "state": {"brightness_pct": 40}},
                         "tier": 1,
                         "result": {"applied": ["desk"], "failed": []}}
```

`speech` is always present, short, and spoken verbatim by the Shortcut. `intent`
is the validated `Intent` from D26, or the `unknown` intent. `tier` is 1, 2, or
0 for the echo step. `result` is what the lights service did. A future
non-speaking client reads the structured fields and ignores `speech`.

Built in three steps so the Shortcut can be finished on day one:

1. **Echo.** Returns `{"speech": "Heard: <text>", "tier": 0}` and no intent. The
   phone side is built and tested against this.
2. **Tier 1.** Deterministic parser, offline, no network.
3. **Tier 2.** Claude fallback with validation, only after tier 1 is in use.

### 7.2 The `voice` bounded context

```
voice/domain/intent.py        Intent, LightStateWords (power, brightness_pct, color name)
                              Frozen pydantic models; the D26 contract. Colour is a
                              *name* here (D28); the lights domain turns it into numbers.
voice/application/tier1.py    parse(text, vocabulary) -> Intent
                              Pure. Vocabulary = light names, scene names, colour
                              names, power words, injected from the lights registry
                              and the colour table, so the grammar cannot drift from
                              lights.yaml (D27).
voice/application/tier2.py    LlmIntentParser: builds the JSON-only prompt from the
                              same vocabulary and the Intent JSON schema, calls the
                              existing LlmProvider port, validates the reply with
                              Intent.model_validate_json. Any validation error, any
                              non-JSON reply, any name not in the vocabulary -> unknown.
voice/application/handle.py   VoiceService.handle(text) -> VoiceResponse
                              tier 1; if unknown or partial, tier 2; dispatch the
                              Intent to the lights service through the connectors
                              gateway (D25); compose speech; log the utterance.
voice/infrastructure/         UtteranceLog: SQLite table voice_utterances
                              (id, heard_at, text, tier, intent_json, outcome).
```

Rules that follow from the decisions:

- **Partial parse.** Tier 1 reports `partial` when it recognised a target or a
  state word but not a complete intent (for example "desk" alone, or "dimmer").
  Partial goes to tier 2 with the partial fields as a hint. A complete tier-1
  parse never goes to tier 2.
- **Tier 2 is gated three ways.** `ANTHROPIC_API_KEY` present, the budget
  context's daily ceiling not reached (the same pre-flight jobs use, recorded to
  the same ledger under a `voice` pseudo-job), and the WAN reachable. Any gate
  closed returns "didn't catch that" from tier 1's `unknown`. Lights keep working
  offline because tier 1 never needs the network (D26).
- **Prompt hygiene.** The tier-2 prompt contains the vocabulary and the schema,
  never the utterance log or any state. The model is asked for one JSON object
  and nothing else. The utterance is user speech, so it is placed as data, not
  as instructions, and the reply is treated as untrusted until validated.
- **Scene matching (D27).** Normalise the phrase (lowercase, strip "mode",
  "lights", "scene", articles), then exact match on scene key, then a bounded
  edit-distance match. Ambiguity between two scenes returns `unknown` rather than
  guessing.
- **Targets.** A light name, "all", or a room alias if `lights.yaml` defines
  one. No target with a state word means "all".
- **Speech composition (D29).** One clause: "Desk at forty percent, warm.",
  "Evening scene.", "All off.", "Didn't catch that." Never a list of bulbs. A
  partial failure says what failed: "Evening scene, bedside didn't respond."
- **Utterance log.** Every call writes one row. `atlas voice log --tier 2`
  prints recent tier-2 rows so promotion into tier-1 rules is a review task, not
  archaeology. The log holds speech transcripts and stays on the Pi's SQLite
  volume; it is not published to MQTT.

### 7.3 iOS Shortcut (D24)

One shortcut, named `Atlas`, built by the owner (section 10):

1. *Text*, set to **Ask Each Time**. Siri prompts, you dictate.
2. *Get Contents of URL*: `POST https://atlas.tail5c9e82.ts.net/api/voice`,
   headers `Authorization: Bearer <ATLAS_API_TOKEN>` and
   `Content-Type: application/json`, request body JSON `{"text": <Text>}`.
3. *Get Dictionary Value* `speech` from the response.
4. *Speak Text*.

Invocation is "Hey Siri, Atlas", then the utterance. No per-command shortcuts.
Siri's own "turn off the lights" against Apple Home stays as the fallback that
works when atlas is down.

Token handling: the token is read off the Pi with `cat /etc/atlas/atlas.env`
and typed into the Shortcut once. It never passes through chat.

### 7.4 Phone prerequisites

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
| 4 | Build the `Atlas` Shortcut (section 7.3) against the echo endpoint | Any time after task 1 |
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
- **Voice**: tier-1 parse table, one row per phrase family including partials
  and ambiguous scenes; tier 2 with a fake `LlmProvider` returning valid JSON,
  invalid JSON, an out-of-vocabulary name, and prose, asserting that only the
  first reaches the lights service; the three tier-2 gates; speech composition;
  the utterance log row; and the echo contract.
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
- **Exposing lights to tier 2/3 *jobs*.** Voice reaches lights through the
  connectors gateway (D25), so the in-process `lights.*` tool namespace exists
  after this work. Letting a scheduled job allowlist it is a job-schema change
  for another spec.
- **An MCP server for lights.** D25.
- **Open-ended conversation.** Tier 2 classifies one utterance into one
  `Intent`; it does not chat, plan, or call tools. Anything beyond lights
  returns `unknown`.
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
2. Colour words: the fixed table proposed is red, orange, amber, yellow, green,
   teal, blue, purple, pink, white, plus the temperature words warm, neutral,
   cool. Add or remove any.
4. Tier-2 model: `ATLAS_MODEL` as configured for jobs, or a cheaper one for
   classification? Proposed: the same setting, one place to change.
5. Utterance retention: keep the log forever, or purge after 90 days? Proposed:
   90 days, since its purpose is rule promotion, not history.
3. Whether `atlas/lights/<name>/changed` should also be emitted for changes made
   from Apple Home (the controller reports them too). Proposed: yes, since the
   board should reflect the room, not just atlas's own writes.
