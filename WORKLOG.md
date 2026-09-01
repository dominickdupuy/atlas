# Atlas — overnight build log

Branch: `overnight-build`, from `main` @ c365de1. Worked 2026-08-31 23:51 → 2026-09-01 00:35 EDT.

---

## SUMMARY

**Deploy loop: PROVEN WORKING.** Timer-driven, both paths, evidence below.

| # | Task | State |
|---|---|---|
| 1 | Project scaffold | **Already existed.** Verified green on arrival, not rebuilt. See BLOCKED #1. |
| 2 | Config and modes | **Done.** `config.toml` support added; the mode enum already existed and is well covered. |
| 3 | Job model | **Already existed**, and more complete than the brief assumed. Not rebuilt. |
| 4 | Approval queue | **Already existed.** SQLite, migrations, TTL, sweep, idempotency, all tested. |
| 5 | FastAPI service | **Done.** `/api/status` added; health and approvals endpoints already existed. |
| 6 | MQTT publisher | **Done.** Exponential backoff replaced a fixed 5s retry; 8 new tests, no broker needed. |
| 7 | Ops dashboard + kiosk | **Done.** New board at `/dashboard`, live on the monitor now. |
| 8 | Deploy pipeline | **Proven, but installed in user scope, not `/etc`.** sudo needs a password. See BLOCKED #2. |
| 9 | CI workflow | **Done.** `release` job added, yamllint clean. **You must confirm the first Actions run.** |
| 10 | README | **Done.** |

Checks at the end, all green:

```
ruff check .          All checks passed!
ruff format --check   132 files already formatted
mypy src tests        Success: no issues found in 131 source files
pytest -m "not mqtt"  183 passed, 1 deselected
atlas validate-jobs   OK: 3 job definition(s) valid
```

Baseline on arrival was 135 passing; I added 48 tests and changed no existing one.

### Verify my work

```sh
# 1. The board, live on the monitor right now
curl -s -H "Authorization: Bearer $(sed -n 's/^ATLAS_API_TOKEN=//p' ~/.config/atlas/atlas.env)" \
    http://127.0.0.1:8100/api/status | python3 -m json.tool | head -40

# 2. The full check suite
cd /opt/atlas/runner && uv sync --frozen && uv run ruff check . && \
    uv run ruff format --check . && uv run mypy src tests && uv run pytest -m "not mqtt"

# 3. The deploy loop, both paths
journalctl --user-unit=atlas-deploy-proof.service --no-pager | grep deploy:

# 4. What is running
systemctl --user status atlas.service atlas-proof.service
systemctl --user list-timers atlas-deploy-proof.timer

# 5. The diff you came here for
git log --oneline main..overnight-build
git diff main..overnight-build --stat
```

---

## BLOCKED / NEEDS DOMINICK

### 1. The brief's premise did not match the repository — read this first

The brief describes a greenfield build: "Project scaffold. uv-managed, src layout,
package `atlas`", then config, job model, approval queue, FastAPI, MQTT, dashboard.

**None of that was greenfield.** `origin/main` @ c365de1 already contained a
complete, working implementation of tasks 1–6 at `runner/src/atlas/`, in the DDD
layout that `docs/architecture.md` §9 and D18 mandate. Baseline on arrival, before
I touched anything: ruff clean, ruff format clean, **mypy strict clean on 121
files, 135 tests passing**.

`/opt/atlas` looked empty only because the clone had never been checked out — local
`main` had zero commits while `origin/main` had four. I ran
`git checkout -B main origin/main` to materialise it, then branched.

**What I did about it:** I did NOT scaffold a second `atlas` package at the repo
root. That would have produced a duplicate package shadowing the real one,
contradicted §9 and D18, and buried working code under a worse copy of itself. I
treated tasks 1–6 as *verify, then close the specific gaps*, and spent the time on
tasks 7–10, which were genuinely unbuilt.

**Your call:** if you actually wanted a from-scratch rewrite at the repo root, none
of tonight's work is what you asked for and you should discard this branch. I judged
that unlikely, but it is your decision, not mine.

### 2. sudo requires a password, so nothing could be installed into /etc

Task 8 says sudo is permitted for systemd units and the sudoers drop-in. It is
permitted, but **not passwordless**: `sudo -n true` → `a password is required`, and
there is no NOPASSWD rule for `domdd`. Earlier commands in this session succeeded on
a cached sudo timestamp that expired partway through the night. You were asleep, so I
could not get the password.

Everything root-requiring is therefore **written and validated but not installed**:

| Artifact | State |
|---|---|
| `infra/systemd/atlas.service` | Written. Not in `/etc/systemd/system`. |
| `infra/systemd/atlas-deploy.service` / `.timer` | Written. Not installed. |
| `infra/sudoers/atlas` | Written, **`visudo -c` → parsed OK**. Not in `/etc/sudoers.d`. |
| `/etc/atlas/atlas.env` | Could not create. Token is at `~/.config/atlas/atlas.env` instead. |

**One command installs all of it when you are awake:**

```sh
sudo /opt/atlas/scripts/install-systemd.sh
```

It re-runs `visudo -c` before installing the drop-in, re-validates the whole sudoers
set afterwards, and backs the file out if anything stops parsing. It also warns you
about the port clash in the next paragraph.

**Before you run it**, disable my interim user units or they will fight the system
ones for port 8100:

```sh
systemctl --user disable --now atlas.service
systemctl --user disable --now atlas-proof.service atlas-deploy-proof.timer
rm -rf ~/atlas-deploy ~/.config/systemd/user/atlas*.service ~/.config/systemd/user/atlas*.timer
```

### 3. The brief says 16 decisions; the doc has 19

`docs/architecture.md` is v1.4 with **D1–D19**. D17 (display UI stack), D18 (bounded
contexts) and D19 (connector sourcing) are the three newest — and D17/D18 are exactly
the ones the brief's instructions contradict, which is consistent with the brief
being written against v1.2 or earlier. Where they conflicted I had to choose. Every
choice is in the table below. **Please confirm you are happy with them.**

### 4. Deviations from the architecture doc, made deliberately

| # | Brief says | Architecture says | What I did | Why |
|---|---|---|---|---|
| a | Dashboard polls a JSON status endpoint every 10s | **D17**: Jinja fragments over **SSE + htmx** | Kept the SSE board at `/`, **added** the polling board at `/dashboard` | Satisfies both. The poll is also what makes the brief's stale-data requirement *possible*: SSE cannot tell you how old the data is once the server is gone. |
| b | Kiosk via **labwc** autostart; do not disturb the desktop session | **D17**: Chromium under **cage**, own systemd unit | Followed the **brief** | There is a live desktop autologin on seat0/tty1. `cage` wants its own seat and would fight it, which the brief forbids. D17's design is right for a dedicated kiosk box, wrong for this one today. `infra/systemd/atlas-kiosk.service` left untouched for when that changes. |
| c | Board at `/dashboard`, port **8000** | **D16**: board at `/`, config defaults to **8100** | Both routes; kept port **8100** | Changing the default would break `atlas-kiosk.service` and `public_url` for no benefit. Two routes cost one line. |
| d | `atlas.service` runs uvicorn directly | Architecture ships the runner in **Docker** | Followed the **brief** | Also the safer reading of your boundaries: a native unit means the deploy loop never touches the Docker daemon or the HA/Mosquitto containers. |
| e | "current mode (read/propose/write)" as one value | **D8**: mode is **per job** | Board shows how many enabled jobs can write unattended, red when non-zero | There is no global mode to display. The count is the thing that actually governs the system's authority, so that is what the screen reports. |
| f | Tiers 2 and 3 as `NotImplementedError` stubs | — | **Left them implemented** | They already worked. Replacing working code with stubs to match the brief would have destroyed value. Tier 3 is deliberately one bounded planning round (phase 7 ships the real loop). |

### 5. Decisions I could not make for you

1. **Two boards now exist** (`/` SSE, `/dashboard` polling). They share every
   service and differ only in transport. Pick one and delete the other's template
   when you have decided; I was not willing to delete working code you did not ask
   me to remove.
2. **`ATLAS_PROFILE=dev`** on this host, so every connector is a stub: no
   credentials, no API calls, no spend. Nothing real happens until you set
   `ANTHROPIC_API_KEY` and the MCP URLs and flip to `prod`. `prod` refuses to start
   half-configured rather than degrading silently.
3. **`release` currently points at an `overnight-build` commit.** If you
   **squash-merge** to main, the CI release job's fast-forward push will fail
   (loudly, by design). Either merge with a real merge commit, or delete and
   recreate `release` from `main` afterwards.
4. **The API token is in `~/.config/atlas/atlas.env`**, not `/etc/atlas/atlas.env`,
   for the sudo reason above. `install-systemd.sh` generates a fresh one in the
   right place; the kiosk script prefers `/etc` when it exists.
5. **The kiosk URL carries `?token=` and is visible in `ps`.** Single-user host, so
   I judged it acceptable, but it is a real exposure worth knowing about. A page
   navigation cannot send an `Authorization` header, so the alternatives are a
   pre-seeded cookie file or an unauthenticated loopback-only board route.
6. **CI has never run.** I have no GitHub credentials to watch Actions. **You must
   confirm the first run yourself** — particularly the new `release` job, which
   needs `contents: write` on the default token; if your org restricts that, the job
   will fail on the push.
7. **journald is not persisting.** `Storage=persistent` is configured but
   `/var/log/journal/` has no machine-id directory, so logs are still in the
   volatile runtime journal and **deploy history will not survive a reboot**. Needs
   root to fix; I left it alone. Also note `journalctl --user` finds nothing —
   `journalctl --user-unit=NAME` works.

---

## Evidence: the deploy loop actually works

Proven in a scratch clone at `~/atlas-deploy` on port 8101, driven by a real
5-minute systemd timer, restarting a real service. It is in **user scope** because
of the sudo blocker; the mechanism is otherwise identical to the shipped units, and
`scripts/deploy.sh` is the same script byte for byte.

I used a separate clone deliberately: pointing the timer at `/opt/atlas` would have
`git reset --hard`-ed your review branch every five minutes for the rest of the
night, and destroyed any uncommitted work in progress.

**Path 1 — remote unchanged, exits without touching anything (the common case):**

```
Sep 01 00:24:53 atlas-deploy-proof[62687]: deploy: checking origin/release
Sep 01 00:24:53 atlas-deploy-proof[62687]: deploy: already at f608dd998c8f; nothing to do
Sep 01 00:24:53 systemd[2387]: Finished atlas-deploy-proof.service.
```

**Before:** `version=0.1.0  revision=f608dd9` — then I pushed a visible change
(version bump to 0.1.1 plus the version string in the board header) to `release` at
`d541714`, and waited for the timer. **No manual trigger.**

**Path 2 — remote moved, full deploy:**

```
Sep 01 00:29:55 atlas-deploy-proof[65797]: deploy: checking origin/release
Sep 01 00:29:56 atlas-deploy-proof[65797]: deploy: updating f608dd998c8f -> d541714a4f62
Sep 01 00:29:56 atlas-deploy-proof[65797]: deploy: syncing dependencies with /home/domdd/.local/bin/uv
Sep 01 00:29:56 atlas-deploy-proof[65812]:  - atlas==0.1.0 (from file:///home/domdd/atlas-deploy/runner)
Sep 01 00:29:56 atlas-deploy-proof[65812]:  + atlas==0.1.1 (from file:///home/domdd/atlas-deploy/runner)
Sep 01 00:29:56 atlas-deploy-proof[65797]: deploy: restarting: systemctl --user restart atlas-proof.service
Sep 01 00:29:56 atlas-deploy-proof[65797]: deploy: deployed d541714a4f62
Sep 01 00:29:56 systemd[2387]: Finished atlas-deploy-proof.service.
```

**After**, from the running service, not from git:

```
$ curl -s -H "Authorization: Bearer proof-token" http://127.0.0.1:8101/api/status
  version=0.1.1  revision=d541714
```

Timer fired 00:29:55, new version served by 00:29:58. The board header on the
monitor now reads `atlas v0.1.1 · bed0d57`.

**Path 2 again, then path 1 again** — repeated on the final commit to show the loop
is not a one-off:

```
Sep 01 00:35:06 deploy: updating d541714a4f62 -> 792b5b9825b1
Sep 01 00:35:06 deploy: deployed 792b5b9825b1
Sep 01 00:40:16 deploy: already at 792b5b9825b1; nothing to do
```

Confirmed from the running service at 00:35:16: `version=0.1.1 revision=792b5b9`.

---

## Running log

### 23:51 — Orientation

- `docs/architecture.md` was missing from the working tree but present on
  `origin/main` (unchecked-out clone, BLOCKED #1). Read it end to end, 572 lines,
  D1–D19.
- 161 tracked files; `runner/src/atlas/` with contexts `jobs`, `approvals`,
  `budget`, `connectors`, `telemetry`, each split domain/application/infrastructure
  exactly as D18 requires.
- Branched `overnight-build`.

### 23:54 — Baseline before touching anything

`uv sync --frozen` rc=0. ruff clean, format clean, **mypy strict clean on 121
files, 135 tests passed**. This matters: it establishes that anything red later is
mine, not inherited.

### 00:05 — Task 2, config.toml

`TomlConfigSettingsSource` placed last in the precedence chain so environment always
beats the file. `ATLAS_CONFIG_FILE` *replaces* the search path rather than merging —
an explicit path is an assertion, and silently also reading a stray
`/opt/atlas/config.toml` is a good way to lose an afternoon. 6 tests.

The read/propose/write enum the task also asked for already existed as
`ExecutionMode` + the gate in `jobs/domain/policies.py`, with an exhaustive decision
table in `test_mode_gate.py` including the read-mode-drops-proposals case. Not
rebuilt.

### 00:10 — Task 6, MQTT backoff

Was a fixed 5s retry forever. Now doubles 1s → 60s with partial jitter (50–100% of
the window, so the unluckiest draw still waits rather than busy-looping) and resets
on every successful connection. Client factory and sleep are injectable — the whole
point, since the failure path only happens when Mosquitto is down and was therefore
the one path never exercised by hand. 8 tests, no broker.

### 00:15 — Tasks 5 and 7, status endpoint and the board

`/api/status` returns one document for the whole screen. One request per refresh so
the board can never paint half of one poll beside half of another, and so the client
has a single unambiguous signal to date its staleness from.

Design decisions worth your review:

- **Failures first**, top-left, largest type. Container-down sorts above job
  failures: what is broken *now* beats what broke earlier.
- **Staleness is the load-bearing feature.** The clock ticks every second off the
  last *successful* poll, so a dead API shows a visibly ageing timestamp within a
  second or two and the whole grid dims and desaturates.
- **A failed poll keeps the last good data on screen.** Blanking would destroy the
  failure information that is the reason the display exists.
- Sized in `vw`, exactly one viewport, `overflow: hidden`. Overflow is truncated
  server-side and reported as `+N more`, because D11 forbids scrolling.
- **No button, form, input or link anywhere**, enforced by a test.

Host metrics are parsed from `/proc` and `/sys` rather than adding psutil: four
small text parsers, no compiled dependency, and the CI matrix includes Windows where
those paths do not exist. Container health is a **TCP connect, not a Docker API
call** — the runner then needs no access to the root-equivalent Docker socket, and
"listening on its port" is a better health signal than "container is running".

### 00:18 — Screenshotting the board found two real bugs

I rendered it headless with seeded data instead of assuming it worked:

1. **The "NO DATA" banner rendered on top of live data.** `.staleness { display:
   flex }` outranks the user agent's `[hidden] { display: none }`, so toggling
   `hidden` from JS did nothing. Exactly the confusion the banner exists to prevent.
   Fixed with an explicit `[hidden] { display: none !important }`.
2. **Probe latency always read `0.0ms`.** It used `loop.time()`, which under uvloop
   is libuv's clock, cached once per loop iteration — so any connect completing
   within one iteration measures as zero. Switched to `perf_counter`.

Then verified the degradation path properly: a stub server that answers `/api/status`
once and then 503s, driven 40s of virtual time. Result: `STALE DATA — last
successful update 40s ago — the API is not responding`, grid dimmed, last good data
still readable. Screenshots in the session scratchpad.

### 00:22 — Kiosk

`~/.config/labwc/autostart` **sources `/etc/xdg/labwc/autostart` first**. labwc reads
the first autostart it finds and does not merge them, so a bare kiosk line would have
silently removed the taskbar, the desktop and the XDG autostart set — precisely the
"do not disturb the existing desktop session" boundary.

Chromium's profile *and* cache are on `/dev/shm`: microSD, no SSD, browser running
24/7. Nothing there needs to survive a reboot because `?token=` re-establishes the
cookie each start. Cache capped at 50MB so it cannot eat RAM that Home Assistant
needs.

The kiosk script **waits for `/healthz`** instead of sleeping a fixed interval — the
white-screen-on-autostart race is just Chromium beating the server to the port, so
waiting on the actual condition fixes it rather than papering over it.

Screen blanking: nothing blanks this display because no idle daemon is running (no
swayidle, no xdg-screensaver — checked). `wlopm --on '*'` runs at login in case an
output was left powered down. **If you turn on blanking in Raspberry Pi
Configuration it will start swayidle and the board will go dark at 3am.**

### 00:24–00:30 — Task 8, and two more bugs

- systemd **splits `Environment=` on whitespace**, so my unquoted
  `ATLAS_RESTART_CMD=systemctl --user restart atlas-proof.service` silently truncated
  to `systemctl`. Caught it in the journal. The shipped system units are unaffected
  (only single-word values), but it would have broken the restart step.
- **A killed Chromium leaves `SingletonLock` pointing at a dead pid**, so the next
  start hands the URL to a corpse and exits silently. That is how the board came back
  as a wallpaper after a restart — found by screenshotting the monitor rather than
  trusting that a process in the list meant a window on screen. The kiosk script now
  clears the locks on start, which is the difference between a kiosk that survives a
  crash at 4am and one that shows a desktop until morning.

### 00:30 — Task 9, CI

Added the `release` job: gated on all four existing jobs, restricted to pushes on
`main`, `contents: write`, fast-forwards `release`. **No `--force`** — a
non-fast-forward push failing loudly is the correct outcome, because it means
`release` carries something `main` does not and forcing would roll the Pi back to
unreviewed code. yamllint clean plus a structural assertion on the parsed document.

Also fixed a **Windows-only crash** while here: `os.getloadavg` does not exist on
Windows and the test matrix runs there; the reader caught `OSError` but an
`AttributeError` would have escaped and 500'd the whole status endpoint on that leg.
Verified by deleting the attribute and re-reading.

### 00:33 — Task 10, README

Configuration precedence, why secrets must not live in `config.toml` (every deploy
`git reset --hard`s that directory), how modes actually work, the deploy pipeline end
to end, the two labwc/tmpfs traps, and an honest table of what is not built.

### What I did not do

- Did not touch ufw, NetworkManager, DNS, sshd, `/boot`, fstab, or any partition.
- Did not reboot, or restart networking, sshd or the Docker daemon.
- Did not stop, remove or reconfigure the homeassistant or mosquitto containers. The
  board only ever opens a TCP connection to them.
- Did not run `apt full-upgrade` or `apt autoremove`.
- Did not expose any new port beyond loopback. Everything binds 127.0.0.1.
- Did not push to `main`, force-push, or rewrite published history.
- `atlas-phase1-provision.sh` in the repo root is left untracked — it is from the
  earlier provisioning session, not part of this work. Commit or delete as you like.
