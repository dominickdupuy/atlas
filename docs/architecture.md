# pi-home: Architecture Specification

**Status:** Draft v1.4
**Author:** Dominick Dupuy
**Last updated:** August 2026

_Changes in v1.4: repository scaffolded. Added D18 (bounded contexts inside the monolith, declarative job definitions) and D19 (connector sourcing and credential scoping). Section 9 updated to the scaffolded layout. Reasoning provider resolved to Anthropic; the section 11 provider question narrowed to its STT remainder._

_Changes since v1.2: monitor confirmed non-touch. D11 resolved to a hybrid (passive board on the monitor, approvals on phone), superseding the open D11-alt question. D8 now names the approval channel explicitly. Added D17, display UI stack._

_Changes in v1.2: added D16, the approval return path. Section 6.2 and the job schema updated accordingly._

_Changes in v1.1: added D14 (implementation language) and D15 (network transport). Storage and networking constraints made concrete. D11 gained a headless variant, pending a decision on the monitor. Added Appendix A, hardware bill of materials._

---

## 1. Purpose

`pi-home` is an always-on personal automation host running on a single Raspberry Pi 5. It exists to do two things well:

1. **Run agentic cron jobs.** Scheduled, unattended tasks that gather information across services, reason about it when reasoning is warranted, and surface the result somewhere I will actually see it.
2. **Provide voice control across connectors.** A single spoken interface that spans smart home devices, Google Calendar, and whatever else gets added, without needing a separate app or vocabulary per service.

The unifying idea is that both paths use the same tool layer. A scheduled job and a spoken request are two front doors into one set of capabilities.

The Pi is not trying to be a compute node. It is the nervous system: always on, holds the credentials and the schedule, knows how to reach everything, and delegates intelligence over the network.

---

## 2. Scope

### In scope

- Scheduled job execution with three levels of autonomy
- Voice input, intent resolution, and spoken response
- Smart home device control via Home Assistant
- Google Calendar read and (gated) write access
- An ambient monitor display showing job status, approvals, and daily context
- Local event bus for loose coupling between components
- Git-managed, reproducible deployment

### Out of scope (for v1)

- Local LLM inference
- Local wake word training beyond off-the-shelf models
- Multi-room or multi-satellite voice
- Public internet exposure of any service
- Camera, video, or continuous vision workloads
- Multi-user support

### Non-goals

- **Fully local operation.** Google Calendar is already a cloud service and the reasoning tier is a cloud API. Pretending otherwise would add cost and latency for a privacy property this system does not have.
- **High availability.** This is a single node in an apartment. If it is down, it is down.
- **A general-purpose agent.** The system should do a bounded set of things reliably rather than an unbounded set unpredictably.

---

## 3. Constraints

| Constraint                                            | Implication                                                                                                                                              |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Single Raspberry Pi 5                                 | Memory budget is the binding limit, not CPU. Container count matters.                                                                                    |
| Cloud inference only                                  | Network is a hard dependency for the reasoning tier. Cost is metered.                                                                                    |
| 128GB SSD                                             | Not a limit. OS ~10GB, Docker images 5 to 10GB, HA database 1 to 5GB at default retention. Recorder tuning is a performance concern, not a capacity one. |
| Wi-Fi only, no Ethernet drop at the install location  | Adds tens of milliseconds to voice latency, which is immaterial against a 1 to 2 second budget. Requires power-save disabled and a static lease.         |
| Display attached to the same host, **no touch input** | Needs a desktop session, which rules out Home Assistant OS. The screen is output only, so no workflow may require acting on it. See D11.                 |
| Credentials live on the device                        | Secrets handling and backup hygiene are security-relevant, not cosmetic.                                                                                 |
| Solo maintainer                                       | Debuggability outranks architectural elegance.                                                                                                           |

---

## 4. Design decisions

Each decision below states what was chosen, why, and what was rejected.

### D1. Single node, cloud inference

**Decision:** Everything runs on the Pi 5. All model inference happens via cloud API.

**Rationale:** Local inference was the only argument for a second machine. A Pi 5 runs a 3B model at roughly 5 tokens per second, which produces 15 to 30 second voice responses. A cloud call returns a first token in under a second. Since the calendar connector is cloud-bound anyway, local-only operation was never achievable, so the tradeoff that local inference buys does not apply.

**Rejected:** An Intel N100 mini PC as a compute companion. Revisit only if local LLM inference, Whisper medium or large, or continuous camera object detection enters scope.

**Memory budget on 8GB:**

| Component              | Approximate resident |
| ---------------------- | -------------------- |
| Home Assistant         | 500MB to 1GB         |
| Chromium kiosk         | 400 to 700MB         |
| Job runner (Python)    | ~100MB idle          |
| Mosquitto              | ~10MB                |
| STT service (if local) | 300MB to 1GB         |
| OS and headroom        | remainder            |

### D2. Raspberry Pi OS with Home Assistant in Docker

**Decision:** Raspberry Pi OS (Bookworm, Wayland) as the base. Home Assistant Container as one service in a Compose stack.

**Rationale:** The attached monitor needs a desktop session for the kiosk display. Home Assistant OS takes over the machine and provides no desktop, so it is incompatible with the display requirement.

**Cost of this choice:** No HA add-on store. Mosquitto, STT, and TTS become ordinary Compose services rather than one-click add-ons. Acceptable, and it aligns with keeping the whole stack in one git-managed Compose file.

**Dependency on D11:** The kiosk is the only hard argument against Home Assistant OS. Since D11 resolved in favor of keeping the monitor, this decision is settled. Even headless it would stand, because a custom Python service under HAOS has to live as a local add-on with a clumsier development loop than a Compose service in a git repo.

### D3. Build on Home Assistant Assist, do not build a parallel voice pipeline

**Decision:** Wake word, speech to text, intent matching, and text to speech are handled by Home Assistant's Assist pipeline over the Wyoming protocol. No custom voice pipeline is written.

**Rationale:** Assist already provides swappable Wyoming services (openWakeWord, faster-whisper, Piper), the satellite protocol for remote audio endpoints, conversation agent integrations with tool calling, and an entity exposure model for controlling what the model can touch. Rebuilding this is months of work to arrive at a worse version of something actively maintained.

**Rejected:** A custom `wake-word` / `stt` / `tts` / `audio-gateway` service chain.

### D4. MCP as the universal connector layer

**Decision:** Every external capability is exposed as an MCP server. Both the job runner and the voice conversation agent consume the same MCP tool set.

**Rationale:** The alternative is a bespoke bridge service per integration, each with its own API shape, auth handling, and error semantics, all of which must be maintained forever. MCP gives one interface. Home Assistant ships both an MCP Server integration (exposing entities as tools) and an MCP Client integration (letting HA call out to other MCP servers), so the smart home side is native.

**Consequence:** Adding a capability to voice control is a configuration change, not a new service. This is the single highest-leverage decision in the spec.

### D5. Modular monolith, not microservices

**Decision:** The custom code is one well-structured Python service with clean internal module boundaries. Genuinely separate processes are reserved for third-party software (Home Assistant, Mosquitto, Chromium, STT) that already ships as its own container.

**Rationale:** Splitting one host's workload into a dozen containers pays every cost of microservices (per-container memory overhead, network hops, N build targets, distributed debugging) and collects none of the benefits, which all derive from deploying across multiple machines. Extract a module into its own service at the point it needs to move to another host, not before.

**Rejected:** The 12-to-15 service Compose topology from the initial sketch.

### D6. MQTT as an event bus, scoped deliberately

**Decision:** Mosquitto carries state and lifecycle events. It is not the internal call mechanism between modules of the job runner.

**Rationale:** Home Assistant speaks MQTT natively, so the broker is nearly free and gives the display a clean way to react to events without polling. But using a message bus for what should be a function call turns a stack trace into a forensic exercise.

**Topic namespace:**

```
pihome/jobs/<job_id>/started
pihome/jobs/<job_id>/completed
pihome/jobs/<job_id>/failed
pihome/jobs/<job_id>/awaiting_approval
pihome/display/mode
pihome/budget/status
pihome/system/health
```

### D7. Three-tier job model

**Decision:** Every scheduled job declares a tier. The runner treats them differently.

| Tier | Shape                                 | Model involvement  | Example                                                               |
| ---- | ------------------------------------- | ------------------ | --------------------------------------------------------------------- |
| 1    | Deterministic script                  | None               | Pull today's calendar, publish to display                             |
| 2    | Deterministic fetch, model summarizes | One call, fixed    | Gather calendar + weather + repo activity, write the morning briefing |
| 3    | Agent loop with tool access           | Iterative, bounded | Find scheduling conflicts this week and propose fixes                 |

**Rationale:** Most recurring value lives in tiers 1 and 2. Tier 3 is where unattended systems fail expensively and silently. Making the tier explicit forces the choice to be deliberate rather than defaulting to the most powerful option.

**Rule:** Tier 1 is the default. A job must opt in to a higher tier.

### D8. Read / propose / write modes with an approval queue

**Decision:** Every job declares a mode. `write` jobs do not execute writes directly by default; they land a proposed action in an approval queue, delivered as a push notification to the phone.

| Mode      | Behavior                                                         |
| --------- | ---------------------------------------------------------------- |
| `read`    | Gathers and reports. Cannot mutate anything.                     |
| `propose` | Computes an action, publishes it for approval, does not execute. |
| `write`   | Executes directly. Requires explicit `auto_approve: true`.       |

**Rationale:** The failure mode of an unattended agent with calendar and device write access is not abstract. A documented case from a comparable build had an LLM cut power to a server rack in response to "turn off the office." An approval step costs one tap and eliminates the entire category.

**Channel:** phone push notification with accept and reject actions (HA Companion app, ntfy, or Telegram), calling the endpoint in D16. Not the monitor, which has no touch input and is therefore output only. The monitor still _shows_ the pending queue so it is visible at a glance; it simply cannot be acted on there.

**Voice is explicitly not an approval channel** for anything with real consequences. A misheard confirmation defeats the purpose of this decision, and speech recognition is precisely where an ambiguous yes originates.

### D9. Deterministic-first voice routing

**Decision:** `prefer_local_intents: true` in the Assist pipeline. Known commands resolve against HA's local intent matcher without a model call. Only unmatched utterances fall through to the conversation agent.

**Rationale:** Roughly 200ms versus roughly 2 seconds, zero cost versus metered cost, and no dependence on the network for the most common requests. The guiding principle, borrowed from builders who learned it the hard way: for a known set of home commands you need determinism, not intelligence.

### D10. Split speech to text by vocabulary

**Decision:** Home Assistant's Speech-to-Phrase handles the constrained command vocabulary locally. Open-ended utterances use cloud STT.

**Rationale:** Speech-to-Phrase resolves in roughly 150ms on a Pi 5 because it only recognizes a known grammar. Anything open-ended is already going to a cloud model, so transcribing it in the cloud costs nothing extra in latency terms. Avoids paying, in cents or in seconds, to transcribe "lights off."

**Fallback:** faster-whisper `base.en` locally runs near realtime on a Pi 5 if cloud STT is undesirable for a given utterance class.

### D11. The monitor is a passive operations board

**Decision:** The monitor displays job status: what ran, what it did, what failed, what is pending approval, plus today's schedule. It is **output only**. No workflow may require acting on it, because it has no touch input.

**Rationale:** A system running unattended actions is only usable if its behavior is legible. Ambient visibility is the mechanism that makes autonomy trustworthy, and a wall-mounted board does that better than a dashboard you have to remember to open. A photo slideshow is a fine idle state, not the point of the screen.

**Why not approvals here.** The monitor is non-touch. Acting on it would mean walking to a keyboard and mouse, which is worse than pulling out a phone. Approvals therefore go to the phone per D8, and the monitor renders the pending queue for visibility only.

**Resolution of the earlier headless question.** v1.1 recorded an open choice between keeping the monitor (D11) and going fully headless (D11-alt). The non-touch constraint resolves it into a hybrid that takes the better half of each: ambient visibility from the monitor, actionable input from the phone. Consequently:

- The monitor is retained. D2 stands unchanged.
- Approvals move to push notifications, which was D11-alt's genuine advantage and is now adopted regardless.
- The arguments against going headless (losing scannable tier 2 output and visual state feedback) no longer apply, since the screen stays.
- The only residual cost of keeping the display is Chromium's memory footprint, which current headroom absorbs.

**Display states:** `OPS` (default), `APPROVAL_PENDING`, `LISTENING`, `THINKING`, `SPEAKING`, `CALENDAR`, `IDLE`.

**Design constraints imposed by no input:** nothing may depend on hover, scroll, click, or pagination. Content either fits on one screen or rotates automatically on a timer. Type sized for the actual viewing distance, not desk distance. Failures must be legible from across the room without squinting.

### D12. Private by default, Tailscale only

**Decision:** Nothing is exposed to the public internet. Remote access is via Tailscale. Devices sit on an isolated VLAN with restricted egress where the network supports it.

**Rationale:** This host holds a live microphone, credentials for a calendar, and control over physical devices. The blast radius of a compromise is unusually concrete.

### D13. Boot from SSD, not SD card

**Decision:** A 128GB SSD as the boot and data volume.

**Rationale:** Home Assistant's database and job logs generate sustained writes that kill SD cards. This is the highest-value reliability change available for the money.

**Capacity is not a concern.** OS around 10GB, Docker images 5 to 10GB, and the HA database 1 to 5GB at the default 10-day recorder retention. The real risk is database bloat from high-frequency entities (power meters, anything reporting every few seconds), which degrades HA responsiveness long before it threatens disk space. Configure recorder exclusions during phase 1, not after the database is already large.

### D14. Python as the implementation language

**Decision:** The job runner and dashboard are written in Python. Each job executes in a short-lived forked subprocess rather than inside the scheduler process.

**Rationale:** Memory is the wrong lens. A runner doing scheduling, MQTT, HTTP, and a small dashboard costs roughly 10 to 20MB in Rust, 15 to 30MB in Go, and 80 to 120MB in Python. That 100MB spread is a rounding error next to Home Assistant's 500MB to 1GB, and it buys iteration speed on a solo project.

The deciding factor is SDK maturity around MCP, which is D4 and therefore load-bearing. Python is a reference implementation for MCP alongside TypeScript, and the model provider SDKs are first-class. Go and Rust have official SDKs but trail on examples, transport edge cases, and the long tail of off-the-shelf connector servers. The workload is also entirely IO-bound, waiting on API responses and MQTT publishes, so runtime speed is irrelevant and Python's genuine weakness never surfaces.

**Rejected:** Go, a defensible second choice if a single cross-compiled static binary were a priority. Rust, because nothing here is CPU-bound or memory-critical enough to justify the cost, and the one place it would win (real-time audio) is owned by Wyoming and the satellite.

**Subprocess-per-job rationale:** long-lived Python processes drift upward in memory over weeks. Forking each job reclaims memory on completion, prevents a hung or leaking job from taking down the scheduler, and gives per-job crash isolation and clean timeout enforcement. This is better design independent of language.

### D15. Wi-Fi transport, tuned

**Decision:** The Pi connects over Wi-Fi. No Ethernet run to the install location.

**Rationale:** Bandwidth requirements are trivial. MQTT messages are bytes, API calls are small, and the voice satellite is on Wi-Fi regardless since it is an ESP32 device. The added latency is tens of milliseconds against a 1 to 2 second voice budget.

**Required configuration, in order of importance:**

1. **Disable Wi-Fi power save.** The chip idles into a low-power state and adds latency or drops packets on an otherwise quiet connection, which is exactly this host's profile. `sudo iw wlan0 set power_save off`, made persistent via a systemd unit since it resets on reboot.
2. **Static IP via DHCP reservation.** The dashboard, the satellite, and any kiosk need a stable address.
3. **Prefer the 5GHz band.** Less congestion, and it leaves 2.4GHz clear for a Zigbee coordinator if one is added later. The two share spectrum and interfere, which is a common source of flaky Zigbee.

**Fallback:** a powerline adapter if the link proves unreliable in that spot, which is cheaper than running cable.

### D16. Approval return path over HTTP

**Decision:** The runner exposes a small HTTP API (FastAPI) for inbound commands. Approve and reject decisions arrive as authenticated POSTs. The resulting state change is then published to MQTT. The dashboard is served from the same process.

**Rationale:** D6 scopes MQTT to events, not commands. Approvals need request/response semantics: validation, an acknowledgment the caller can act on, and a definite outcome. Publishing a command as a fire-and-forget message discards all three and turns a failed approval into a forensic exercise, which is precisely what D6 exists to prevent.

The stronger argument is the approval channel itself. Per D8 and D11, approvals arrive as phone push notifications, and notification actions (ntfy action buttons, Telegram callback handlers) work by calling an HTTP endpoint. A phone notification cannot publish to the broker. HTTP is therefore not the cleaner option here, it is the only one that functions.

**Rejected, MQTT command topics:** no new surface to secure, but commands-as-messages lose acknowledgment semantics and violate D6.

**Rejected, approvals as Home Assistant entities:** reuses HA auth, but couples the D8 guardrail to the availability and configuration of the system it guards. An HA restart or config error could leave approvals in an ambiguous state, and HA entities model an expiring payload poorly. The safety mechanism must not depend on the thing it constrains.

**Interface:**

```
POST /api/approvals/{id}/decision    {"decision": "approve" | "reject"}
GET  /api/approvals                  # pending queue
GET  /api/jobs/{id}                  # job detail
GET  /healthz                        # liveness, no auth
GET  /                               # dashboard
```

**Required properties:**

1. **Frozen payload.** The concrete action is serialized at proposal time and executed verbatim on approval. It is never re-derived from the agent at decision time. Without this you approve "dim bedroom to 30%" and execute whatever the model recomputes on a second pass. This is the actual security property of D8.
2. **Idempotency keyed on approval ID.** A repeated POST returns the existing terminal state rather than executing again. A double tap on a flaky connection must not fire the action twice.
3. **TTL with an explicit `expired` state.** Lifecycle is `pending -> approved | rejected | expired`, with expiry enforced server-side at decision time rather than only by a background sweep. An approval tapped three days later does not run.
4. **Persistence in SQLite.** In-memory approvals vanish on restart, silently violating the rule that every job publishes a terminal event.

**Security:** bind to the Tailscale interface rather than `0.0.0.0`, and require a bearer token regardless, since the dashboard is a browser on the LAN. Per D12, nothing here is publicly reachable.

**Direction of traffic:**

```
commands in   -> HTTP (validated, acknowledged, persisted)
state out     -> MQTT (pihome/jobs/<id>/approved, etc.)
```

This is what makes D6 a coherent rule rather than an arbitrary one.

### D17. Display UI stack

**Decision:** A server-rendered dashboard from the same FastAPI process as D16, pushed to the browser over Server-Sent Events, running in Chromium under `cage` on Wayland.

| Layer              | Choice                                       | Why                                                                                                                                                                                               |
| ------------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Server             | FastAPI + Jinja2                             | Same process as the D16 API. One surface, one auth path, one thing to restart.                                                                                                                    |
| Live updates       | Server-Sent Events                           | One-way push is exactly the shape of this problem. Auto-reconnect is built into `EventSource`. The runner already subscribes to MQTT and re-emits, so the browser never needs broker credentials. |
| Interactivity      | htmx (SSE extension)                         | Server sends HTML fragments, htmx swaps them in. No build step, no Node on the Pi, no bundler. Fits the Python-first choice in D14.                                                               |
| Styling            | Plain CSS with custom properties             | A dashboard of roughly six components does not justify a build pipeline. Dark background, high contrast, large type.                                                                              |
| Compositor         | `cage`                                       | A single-app Wayland kiosk compositor. Cleaner than `labwc`, which is the Raspberry Pi OS default but not intended for kiosk use.                                                                 |
| Browser            | Chromium, `--kiosk --ozone-platform=wayland` | The standard path. Needs a startup delay to avoid the known white-screen-on-autostart issue.                                                                                                      |
| Process management | systemd unit, `Restart=always`, `MemoryMax=` | Watchdog and the memory cap from section 8, in one place.                                                                                                                                         |
| Screen blanking    | `swayidle` + `wlopm`                         | X11-era tools like `unclutter` do not work under Wayland. Blank overnight to limit burn-in.                                                                                                       |

**Rejected, MQTT over WebSockets in the browser:** would mean a second listener on Mosquitto, broker credentials shipped to the client, and a second auth path. SSE from a process already subscribed to MQTT gets the same result with less surface.

**Rejected, React or any SPA framework:** a build step, a Node toolchain, and a bundle, for a read-only board with no client-side state. The interactivity budget here is zero.

### D18. Bounded contexts inside the monolith

**Decision:** The runner is structured as bounded contexts — `jobs`, `approvals`, `budget`, `connectors`, `telemetry` — each split into `domain` / `application` / `infrastructure` layers, with a deliberately tiny shared kernel. Contexts communicate only through application services and in-process domain events. Presentation (HTTP routers, Jinja templates, static assets) lives inside the runner package, since it is the monolith's own UI layer and ships in the Docker image.

**Rationale:** D5 keeps one process; this decision keeps that process extractable. Domain logic is pure, with no IO, so the D8 and D16 safety properties are unit-testable without a database or a broker. If a context ever needs to move to another host, its application layer plus its ports already is the service API: swap the SQLite adapter and the in-process bus for a real transport, and the domain code does not change.

**Job definitions are fully declarative.** The section 7 schema gains tier-gated fields: `steps` (ordered deterministic tool calls, results bound to names), `synthesize` (a prompt template over step results — required for tier 2, forbidden for tier 1), `goal` (natural-language objective, tier 3 only), `propose` (the tool-call template that becomes the frozen payload of D16), and `enabled`. This keeps D4's rule — adding a capability is configuration, not code — true for jobs as well.

**Rejected:** per-job Python handlers, where every new job becomes code to review and maintain. A parallel dataclass hierarchy for domain values: frozen Pydantic models are IO-free and halve the mapping boilerplate, a pragmatic exception stated openly.

### D19. Connector sourcing and credential scoping

**Decision:** Off-the-shelf MCP servers only, pinned to exact versions, never floating tags: Home Assistant's native MCP Server integration, the official GitHub MCP server, and a vetted community Google Calendar server whose source is read before it is granted a token. Credentials are scoped at the provider, not only at the allowlist: read-only OAuth for Calendar until D8 writes are actually wanted, and a fine-grained read-only PAT for GitHub. Anything that is one unauthenticated endpoint — weather — is a plain HTTP function inside the runner, not an MCP server.

**Rationale:** An off-the-shelf server is a supply chain surface holding live credentials. Pinning, vetting, and minimal provider-side scopes are the controls that survive a bug in the allowlist logic. MCP earns its cost where there is OAuth to manage and a real tool surface; the per-job `tools` allowlist remains, enforced by the runner, but it is the second layer of defense, not the first.

**Rejected:** custom in-repo servers, which are the per-integration maintenance burden D4 exists to avoid. Wrapping trivial HTTP APIs in MCP: a process, a protocol, and a schema that buy nothing.

---

## 5. Component inventory

| Component       | Type                       | Responsibility                                                                                   |
| --------------- | -------------------------- | ------------------------------------------------------------------------------------------------ |
| Home Assistant  | Container (third party)    | Device state, local intents, Assist pipeline, MCP server for entities                            |
| Mosquitto       | Container (third party)    | Event bus                                                                                        |
| Job runner      | Container (custom)         | Schedule, execute, budget, and report on jobs; owns the approval queue                           |
| MCP servers     | Containers or subprocesses | Google Calendar, Home Assistant, GitHub, and future connectors                                   |
| STT service     | Container (third party)    | Speech-to-Phrase and/or faster-whisper via Wyoming                                               |
| TTS service     | Container (third party)    | Piper via Wyoming                                                                                |
| Display kiosk   | systemd unit               | Chromium under `cage`, rendering the passive ops board over SSE. Output only, no touch. See D17. |
| Voice satellite | External hardware          | Microphone, wake word, echo cancellation                                                         |

**Hardware still required:** a microphone. The Home Assistant Voice Preview Edition (~$59) is the recommended option; it runs wake word on its own ESP32, has hardware echo cancellation and a physical mute switch, and joins as an Assist satellite with no driver work. A USB conference microphone is the fallback. Bluetooth speaker microphones are explicitly rejected: classic Bluetooth cannot do stereo playback and microphone capture simultaneously, and the resulting audio is degraded before transcription ever sees it.

---

## 6. Data flows

### 6.1 Voice command

```
Utterance
  -> Satellite (wake word detected on-device)
  -> HA Assist pipeline
  -> Speech-to-Phrase (local, ~150ms)
       |
       +-- match -> local intent -> device action -> TTS response
       |
       +-- no match
              -> cloud STT
              -> conversation agent (cloud LLM + MCP tools)
              -> tool calls (calendar, HA, etc.)
              -> TTS response
  -> MQTT event -> display reflects state
```

### 6.2 Scheduled job

```
Scheduler fires job definition
  -> runner publishes jobs/<id>/started
  -> tier 1: run deterministic steps
     tier 2: run steps, then one model call to synthesize
     tier 3: agent loop, bounded by token budget and wall clock
  -> mode check
       read    -> publish result
       propose -> freeze payload, persist to SQLite,
                  publish awaiting_approval
       write   -> execute (only if auto_approve), publish result
  -> publish completed | failed | awaiting_approval
  -> display renders outcome
```

### 6.3 Approval decision (return path)

```
Display or push notification
  -> POST /api/approvals/<id>/decision   (bearer token, over Tailscale)
  -> runner validates: exists, still pending, not expired
  -> writes terminal state to SQLite (idempotent on approval ID)
  -> returns decision outcome to caller
  -> executes the frozen payload if approved
  -> publishes jobs/<id>/approved | rejected | expired
  -> display and any other subscriber react
```

---

## 7. Job definition schema

```yaml
id: morning-briefing
description: Assemble and speak the morning briefing
schedule: "0 7 * * 1-5" # cron expression
tier: 2 # 1 | 2 | 3
mode: read # read | propose | write
auto_approve: false
approval_ttl_seconds: 86400 # applies to propose mode

tools: # MCP tools this job may use
  - google-calendar.list_events
  - weather.get_forecast
  - github.list_notifications

budget:
  max_tokens: 4000
  max_wall_clock_seconds: 60
  max_tool_calls: 10

on_failure:
  notify: display # display | voice | both
  retry: 1
  escalate_after: 2 # consecutive failures before loud alert

output:
  publish_to: pihome/jobs/morning-briefing/completed
  speak: true
```

---

## 8. Failure modes and mitigations

| Failure                                                      | Mitigation                                                                                                                                                       |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Internet outage                                              | Local intents keep device control working. Job runner fails fast and loudly to the display rather than retrying into a dead network.                             |
| Runaway tier 3 job                                           | Per-job token budget, wall clock timeout, and tool call ceiling. Global daily spend ceiling pauses the scheduler and posts to the display.                       |
| Silent job failure                                           | Every job must publish a terminal event. A job that produces no event within its timeout is treated as failed and surfaced.                                      |
| Unwanted write action                                        | `propose` mode by default; `write` requires explicit `auto_approve`. Sensitive entities excluded from the model's exposed set with explicit never-control rules. |
| Chromium memory leak starving HA                             | `MemoryMax=` on the kiosk systemd unit plus `Restart=always`.                                                                                                    |
| Wi-Fi power save causing intermittent latency or packet loss | Power save disabled at boot via systemd unit. Static DHCP lease. 5GHz preferred.                                                                                 |
| HA database bloat degrading responsiveness                   | Recorder exclusions for high-frequency entities configured in phase 1. Periodic purge.                                                                           |
| Credential leak                                              | Secrets in a mounted env file, never in the repo. Backups exclude or encrypt secrets. Tokens scoped as narrowly as the provider allows.                          |
| SD card death                                                | Boot from SSD. Config reproducible from git; data volumes backed up with restic.                                                                                 |
| Cloud STT misheard a destructive command                     | Destructive intents require confirmation before execution.                                                                                                       |

---

## 9. Repository layout

```
pi-home/
├── compose.yaml                  # full stack; profiles: ha, mcp, voice
├── .env.example                  # secrets template, real .env gitignored
│
├── runner/                       # the modular monolith (Docker build context)
│   ├── Dockerfile                # multi-arch: arm64 for the Pi, amd64 for dev
│   ├── pyproject.toml            # uv-managed; ruff, strict mypy, pytest config
│   ├── src/pihome/
│   │   ├── __main__.py           # CLI: serve | execute-job | validate-jobs | migrate
│   │   ├── config.py             # pydantic-settings, env-driven
│   │   ├── shared/               # shared kernel: domain events, clock, IDs — kept tiny
│   │   ├── jobs/                 # context: scheduling + execution (D7, D14)
│   │   ├── approvals/            # context: the D8/D16 guardrail
│   │   ├── budget/               # context: ledger, ceilings, pre-flight checks
│   │   ├── connectors/           # context: MCP clients, LLM provider, weather, notifier
│   │   ├── telemetry/            # context: events out (D6), SSE stream, health
│   │   │                         # each context: domain/ application/ infrastructure/
│   │   ├── persistence/          # SQLite connection + numbered SQL migrations
│   │   ├── presentation/         # FastAPI app, routers, Jinja templates, static (D16, D17)
│   │   └── bootstrap/            # composition roots: parent (serve) and child (execute-job)
│   └── tests/                    # unit (pure domain) + integration (repos, API, subprocess)
│
├── jobs/                         # one YAML per job, validated against the section 7 schema
│   ├── calendar-today.yaml
│   ├── morning-briefing.yaml
│   └── conflict-finder.yaml
│
├── homeassistant/                # HA config (runtime dirs and secrets excluded)
│
├── infra/
│   ├── mosquitto/                # broker config
│   ├── ntfy/                     # notification server config
│   ├── systemd/                  # kiosk, wifi power save, compose-on-boot units
│   └── ansible/                  # Pi provisioning, later
│
└── docs/
    ├── architecture.md           # this document
    ├── voice.md                  # phase 6 setup notes
    └── operations.md             # runbook
```

---

## 10. Build phases

**Phase 1: Foundation.** Raspberry Pi OS on SSD. Wi-Fi power save disabled and a static DHCP lease set before anything else, since every later phase assumes a stable address. Compose stack with Home Assistant and Mosquitto. Recorder exclusions configured. One physical device controllable from the HA UI. No custom code yet.

**Phase 2: Job runner, tier 1 only.** Scheduler, job YAML parsing, MQTT publishing. First job pulls the calendar and publishes it. No model involved anywhere.

**Phase 3: Display and notifications.** Chromium under `cage` rendering the passive ops board over SSE, plus the phone notification channel for approvals. At this point the system is useful with zero AI in it.

**Phase 4: Tier 2 and budgets.** Add the single-model-call summarization path. Implement token budgets, timeouts, and the global spend ceiling before the first model call ships, not after.

**Phase 5: MCP layer.** Stand up Google Calendar and Home Assistant MCP servers. Wire the tool registry into the runner. Add approvals and the `propose` mode.

**Phase 6: Voice.** Microphone hardware, Assist pipeline, Speech-to-Phrase, conversation agent pointed at the same MCP tools.

**Phase 7: Tier 3.** Bounded agent loops, only for jobs where tiers 1 and 2 demonstrably cannot do the work.

The ordering is deliberate: every phase produces something usable on its own, and the riskiest capability ships last, on top of guardrails that already exist.

---

## 11. Open questions

- Pi 5 RAM configuration, which determines whether local STT is comfortable alongside the display stack.
- Which push notification channel to use for approvals: HA Companion app, ntfy, or Telegram. All three satisfy D16; the choice is ergonomic.
- Whether Home Assistant is already running or this is greenfield.
- Which specific smart home devices and protocols are in play (Zigbee, Thread, Z-Wave, Matter, Wi-Fi), since non-Wi-Fi protocols require a USB coordinator.
- Whether calendar writes are actually wanted, or whether read plus propose is sufficient indefinitely.
- Which cloud STT provider handles open-ended utterances in phase 6. The reasoning tier is resolved to Anthropic (v1.4), which does not offer STT, so one-provider-for-both is off the table and the STT choice stays open until phase 6.

---

## 12. Decision log

| ID  | Decision                                           | Rejected alternative                     |
| --- | -------------------------------------------------- | ---------------------------------------- |
| D1  | Single Pi 5, cloud inference                       | N100 companion, local LLM                |
| D2  | Raspberry Pi OS + HA in Docker                     | Home Assistant OS                        |
| D3  | Build on HA Assist and Wyoming                     | Custom voice pipeline                    |
| D4  | MCP as universal connector layer                   | Bespoke bridge per service               |
| D5  | Modular monolith                                   | 12-plus microservices                    |
| D6  | MQTT for events only                               | MQTT as internal RPC                     |
| D7  | Three-tier job model                               | Everything as an agent loop              |
| D8  | Read / propose / write with approvals              | Direct execution                         |
| D9  | Local intents preferred                            | Model call for every utterance           |
| D10 | Split STT by vocabulary                            | Whisper for everything                   |
| D11 | Monitor as a passive ops board, approvals on phone | Fully headless; approvals on the monitor |
| D12 | Tailscale only                                     | Public exposure via tunnel               |
| D13 | SSD boot (128GB)                                   | SD card                                  |
| D14 | Python, subprocess per job                         | Go, Rust                                 |
| D15 | Wi-Fi with power save disabled                     | Ethernet run, powerline                  |
| D16 | HTTP API for commands, MQTT for state              | MQTT command topics, HA entities         |
| D17 | FastAPI + Jinja2 + htmx + SSE, Chromium under cage | Browser MQTT over WebSockets, React SPA  |
| D18 | Bounded contexts + layers, declarative job YAML    | Flat modules, per-job Python handlers    |
| D19 | Pinned off-the-shelf MCP, provider-scoped creds    | Custom servers, MCP for trivial APIs     |

---

## Appendix A: Hardware bill of materials

| Item                                | Status           | Notes                                                                                                                              |
| ----------------------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Raspberry Pi 5                      | Owned            | RAM configuration TBD                                                                                                              |
| Active Cooler                       | Owned            | Required; sustained load throttles at 80C                                                                                          |
| Official 27W USB-C PSU (5V/5A)      | Owned            | Below spec, USB ports throttle to 600mA                                                                                            |
| 128GB SSD                           | Owned            | Sufficient with headroom                                                                                                           |
| Monitor                             | Owned            | **No touch input.** Retained as a passive board per D11.                                                                           |
| Beats Pill                          | Owned            | **Output only.** Connect to the satellite's 3.5mm jack, not to the Pi as a microphone.                                             |
| Home Assistant Voice PE             | **To buy, ~$59** | The microphone. Frequently backordered; check the official retailer list. Dual mics, XMOS DSP, hardware mute, LED ring, 3.5mm out. |
| Zigbee/Thread or Z-Wave coordinator | **Conditional**  | Connect ZBT-2 (~$30) or ZWA-2 (~$50). Only if existing devices use those protocols. Buy after auditing what you own.               |

**Explicitly rejected purchases:**

- **Any ReSpeaker I2S HAT.** Community-maintained driver with open Pi 5 bugs that breaks on routine kernel upgrades.
- **Beats Pill as a microphone.** Classic Bluetooth cannot carry stereo playback and mic capture simultaneously; the HFP profile degrades audio before transcription sees it, and there is no evidence of a USB capture path.
- **Hailo AI HAT or similar accelerator.** Irrelevant under D1.
- **A second machine.** See D1.
- **A RAM upgrade.** This workload does not need 16GB, and 2026 memory pricing makes it a poor purchase regardless.
