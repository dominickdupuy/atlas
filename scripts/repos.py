#!/usr/bin/env python3
"""Hosted repositories on this machine: jobs on a schedule, services kept up.

    repos.py status              # every repo: next run, last result, service state
    repos.py queue               # everything due in the next 7 days, soonest first
    repos.py run NAME            # run a job now, logged like a scheduled run
    repos.py enqueue NAME --in 20m | --at "2026-09-06 08:30"
    repos.py logs NAME [-n 50]   # tail the latest log
    repos.py apply               # clone, set up, render cron + units (uses sudo)
    repos.py tick                # cron calls this every minute for queued runs

The registry is infra/repos.toml beside this script's repo. Each entry is a
git checkout somewhere on disk with a kind: `job` (runs on a cron schedule),
`service` (a server systemd keeps running), or `both`. Nothing here knows
what any repo does; it clones, keeps the checkout current, runs the declared
command, and records what happened.

Every run leaves two things: a full log under /var/log/atlas-repos/NAME/ and
a small JSON summary under /var/lib/atlas-repos/, which `status` reads and
which the atlas board can read the same way. The queue of one-off runs lives
beside them; `tick` (a once-a-minute cron line) fires whatever is due.

Standard library only, so it runs on a fresh Pi with nothing installed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
REGISTRY = Path(os.environ.get("REPOS_REGISTRY", REPO_ROOT / "infra" / "repos.toml"))
LOG_ROOT = Path(os.environ.get("REPOS_LOG_DIR", "/var/log/atlas-repos"))
STATE_ROOT = Path(os.environ.get("REPOS_STATE_DIR", "/var/lib/atlas-repos"))
CRON_FILE = Path("/etc/cron.d/atlas-repos")
UNIT_DIR = Path("/etc/systemd/system")
USER = os.environ.get("REPOS_USER") or os.environ.get("USER") or "domdd"
KEEP_LOGS = 60


def now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


# --- registry ----------------------------------------------------------------

def load_registry() -> list[dict]:
    if not REGISTRY.exists():
        sys.exit(f"no registry at {REGISTRY}")
    doc = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    repos = doc.get("repo") or []
    seen = set()
    for r in repos:
        for key in ("name", "path", "url", "kind"):
            if key not in r:
                sys.exit(f"registry: entry missing {key!r}: {r}")
        if r["name"] in seen:
            sys.exit(f"registry: duplicate name {r['name']!r}")
        seen.add(r["name"])
        if r["kind"] not in ("job", "service", "both"):
            sys.exit(f"registry: {r['name']}: kind must be job, service or both")
        if r["kind"] in ("job", "both") and not (r.get("schedule") and r.get("job")):
            sys.exit(f"registry: {r['name']}: a job needs `schedule` and `job`")
        if r["kind"] in ("service", "both") and not r.get("service"):
            sys.exit(f"registry: {r['name']}: a service needs `service` (the command)")
        r.setdefault("branch", "main")
        r.setdefault("enabled", True)
    return repos


def find(name: str) -> dict:
    for r in load_registry():
        if r["name"] == name:
            return r
    sys.exit(f"no repo named {name!r} in {REGISTRY}")


# --- cron expressions ----------------------------------------------------------

def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        out.update(range(a, b + 1, step))
    return out


def next_runs(expr: str, start: datetime, count: int = 1, horizon_days: int = 366) -> list[datetime]:
    """Next `count` firings of a five-field cron expression after `start`.

    Minute-by-minute walk. Slow in theory, instant in practice for anything
    that fires at least yearly, and it has no dependency and no edge cases
    of its own to get wrong.
    """
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron expression needs 5 fields: {expr!r}")
    mins = _field(parts[0], 0, 59)
    hours = _field(parts[1], 0, 23)
    doms = _field(parts[2], 1, 31)
    mons = _field(parts[3], 1, 12)
    dows = {d % 7 for d in _field(parts[4], 0, 7)}  # 7 == Sunday == 0
    dom_any, dow_any = parts[2] == "*", parts[4] == "*"

    found: list[datetime] = []
    t = start + timedelta(minutes=1)
    end = start + timedelta(days=horizon_days)
    while t <= end and len(found) < count:
        if t.month in mons and t.hour in hours and t.minute in mins:
            dom_ok = t.day in doms
            dow_ok = (t.weekday() + 1) % 7 in dows
            # Vixie cron: when both day fields are restricted, either matches.
            ok = (dom_ok and dow_ok) if (dom_any or dow_any) else (dom_ok or dow_ok)
            if ok:
                found.append(t)
        # Skip to the next candidate hour when the hour cannot match.
        if t.hour not in hours:
            t = (t + timedelta(hours=1)).replace(minute=0)
        else:
            t += timedelta(minutes=1)
    return found


# --- state and logs --------------------------------------------------------------

def state_path(name: str) -> Path:
    return STATE_ROOT / f"{name}.json"


def read_state(name: str) -> dict | None:
    p = state_path(name)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except json.JSONDecodeError:
        return None


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def queue_path() -> Path:
    return STATE_ROOT / "queue.json"


def read_queue() -> list[dict]:
    p = queue_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except json.JSONDecodeError:
        return []


def prune_logs(dirpath: Path) -> None:
    logs = sorted(dirpath.glob("*.log"))
    for old in logs[:-KEEP_LOGS]:
        old.unlink(missing_ok=True)


# --- running -----------------------------------------------------------------------

def update_checkout(repo: dict, log) -> None:
    """Fast-forward the checkout; never discard local changes silently."""
    path = Path(repo["path"])
    if not (path / ".git").exists():
        log(f"cloning {repo['url']} -> {path}")
        subprocess.run(["git", "clone", "-q", "-b", repo["branch"], repo["url"], str(path)],
                       check=True, stdout=log.file, stderr=subprocess.STDOUT)
        return
    r = subprocess.run(["git", "-C", str(path), "pull", "--ff-only", "-q"],
                       stdout=log.file, stderr=subprocess.STDOUT)
    log("checkout updated" if r.returncode == 0 else
        f"git pull failed (exit {r.returncode}); running with what is on disk")


def ensure_setup(repo: dict, log) -> None:
    """Run the declared setup once, keyed on a marker the setup would create."""
    marker = repo.get("setup_marker")
    setup = repo.get("setup")
    if not setup:
        return
    path = Path(repo["path"])
    if marker and (path / marker).exists():
        return
    log(f"setup: {setup}")
    subprocess.run(setup, shell=True, cwd=path, check=True,
                   stdout=log.file, stderr=subprocess.STDOUT)


class Log:
    def __init__(self, name: str, kind: str):
        d = LOG_ROOT / name
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{time.strftime('%Y-%m-%d_%H%M%S')}_{kind}.log"
        self.file = open(self.path, "a", buffering=1, encoding="utf-8")
        latest = d / "latest.log"
        latest.unlink(missing_ok=True)
        try:
            latest.symlink_to(self.path.name)
        except OSError:
            pass
        prune_logs(d)

    def __call__(self, msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        self.file.write(line + "\n")
        print(line, flush=True)


def run_job(repo: dict, trigger: str = "manual") -> int:
    """One logged run: update, set up if needed, execute, summarise."""
    name = repo["name"]
    log = Log(name, "job")
    started = time.time()
    summary = {"name": name, "kind": "job", "trigger": trigger,
               "started": datetime.fromtimestamp(started).isoformat(timespec="seconds"),
               "log": str(log.path), "status": "running"}
    write_json(state_path(name), summary)
    code = 1
    try:
        log(f"run {name} ({trigger}): {repo['job']}")
        update_checkout(repo, log)
        ensure_setup(repo, log)
        env = dict(os.environ)
        env.setdefault("HOME", str(Path.home()))
        env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}"
        r = subprocess.run(repo["job"], shell=True, cwd=repo["path"], env=env,
                           stdout=log.file, stderr=subprocess.STDOUT,
                           timeout=int(repo.get("timeout_seconds", 3600)))
        code = r.returncode
        log(f"exit {code}")
    except subprocess.TimeoutExpired:
        log("timed out")
        code = 124
    except Exception as exc:  # noqa: BLE001 - recorded, then reported
        log(f"failed: {exc}")
        code = 1
    finally:
        finished = time.time()
        tail = log.path.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        summary.update({
            "finished": datetime.fromtimestamp(finished).isoformat(timespec="seconds"),
            "duration_seconds": round(finished - started, 1),
            "exit": code,
            "status": "ok" if code == 0 else "failed",
            "tail": tail,
        })
        write_json(state_path(name), summary)
        log.file.close()
    return code


# --- services (systemd) ---------------------------------------------------------------

def unit_name(repo: dict) -> str:
    return f"atlas-repo-{repo['name']}.service"


def render_unit(repo: dict) -> str:
    env_lines = "".join(f"Environment={k}={shlex.quote(v)}\n"
                        for k, v in (repo.get("env") or {}).items())
    return f"""[Unit]
Description=hosted repo {repo['name']}: {repo.get('description', 'service')}
Documentation=file://{REGISTRY}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={USER}
Group={USER}
WorkingDirectory={repo['path']}
ExecStart=/bin/bash -lc {shlex.quote(repo['service'])}
Restart=on-failure
RestartSec=10
Environment=HOME=/home/{USER}
Environment=PATH=/home/{USER}/.local/bin:/usr/local/bin:/usr/bin:/bin
{env_lines}StandardOutput=append:{LOG_ROOT}/{repo['name']}/service.log
StandardError=append:{LOG_ROOT}/{repo['name']}/service.log
SyslogIdentifier=atlas-repo-{repo['name']}

[Install]
WantedBy=multi-user.target
"""


def service_state(repo: dict) -> str:
    r = subprocess.run(["systemctl", "is-active", unit_name(repo)],
                       capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


# --- apply: cron + units ----------------------------------------------------------------

def render_cron(repos: list[dict]) -> str:
    me = HERE / "repos.py"
    lines = [
        "# Hosted repositories on this machine. RENDERED by scripts/repos.py apply",
        f"# from {REGISTRY}; edit that file, not this one.",
        "SHELL=/bin/bash",
        f"PATH=/home/{USER}/.local/bin:/usr/local/bin:/usr/bin:/bin",
        f"HOME=/home/{USER}",
        "MAILTO=\"\"",
        "",
        f"* * * * * {USER} {me} tick >> {LOG_ROOT}/tick.log 2>&1",
    ]
    for r in repos:
        if r["kind"] in ("job", "both") and r["enabled"]:
            lines.append(f"{r['schedule']} {USER} {me} run {r['name']} --trigger cron"
                         f" >> {LOG_ROOT}/{r['name']}/cron.log 2>&1")
    return "\n".join(lines) + "\n"


def sudo(*args: str) -> None:
    subprocess.run(["sudo", "-n", *args], check=True)


def apply() -> int:
    repos = load_registry()
    for d in (LOG_ROOT, STATE_ROOT):
        if not d.exists():
            sudo("mkdir", "-p", str(d))
            sudo("chown", f"{USER}:{USER}", str(d))
    for r in repos:
        (LOG_ROOT / r["name"]).mkdir(parents=True, exist_ok=True)
        log = Log(r["name"], "apply")
        try:
            update_checkout(r, log)
            ensure_setup(r, log)
        except Exception as exc:  # noqa: BLE001
            log(f"apply: {exc} (continuing; fix and re-run apply)")
        finally:
            log.file.close()

    # Cron file: rendered to a temp path, validated for shape, installed as root.
    text = render_cron(repos)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".cron") as f:
        f.write(text)
    sudo("install", "-o", "root", "-g", "root", "-m", "0644", f.name, str(CRON_FILE))
    os.unlink(f.name)
    print(f"installed {CRON_FILE}")

    # Units for services.
    changed = False
    for r in repos:
        if r["kind"] not in ("service", "both"):
            continue
        unit = render_unit(r)
        target = UNIT_DIR / unit_name(r)
        if target.exists() and target.read_text(encoding="utf-8") == unit:
            continue
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".service") as f:
            f.write(unit)
        sudo("install", "-o", "root", "-g", "root", "-m", "0644", f.name, str(target))
        os.unlink(f.name)
        changed = True
        print(f"installed {target}")
    if changed:
        sudo("systemctl", "daemon-reload")
    for r in repos:
        if r["kind"] in ("service", "both"):
            if r["enabled"]:
                sudo("systemctl", "enable", "--now", unit_name(r))
                sudo("systemctl", "restart", unit_name(r)) if changed else None
            else:
                sudo("systemctl", "disable", "--now", unit_name(r))
    return status()


# --- queue of one-off runs ------------------------------------------------------------------

def parse_when(at: str | None, delay: str | None) -> datetime:
    if delay:
        m = re.fullmatch(r"(\d+)\s*([smhd])", delay.strip())
        if not m:
            sys.exit("--in wants a number and unit, e.g. 20m, 2h, 1d")
        n, unit = int(m.group(1)), m.group(2)
        return now() + timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[unit]: n})
    if at:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%H:%M"):
            try:
                t = datetime.strptime(at.strip(), fmt)
                if fmt == "%H:%M":
                    t = now().replace(hour=t.hour, minute=t.minute)
                    if t <= now():
                        t += timedelta(days=1)
                return t
            except ValueError:
                continue
        sys.exit("--at wants 'YYYY-MM-DD HH:MM' or 'HH:MM'")
    return now()


def enqueue(name: str, when: datetime, note: str = "") -> None:
    find(name)
    q = read_queue()
    q.append({"id": f"{name}-{int(time.time())}", "name": name,
              "at": when.isoformat(timespec="minutes"), "note": note,
              "queued": datetime.now().isoformat(timespec="seconds")})
    q.sort(key=lambda e: e["at"])
    queue_path().parent.mkdir(parents=True, exist_ok=True)
    queue_path().write_text(json.dumps(q, indent=2), encoding="utf-8")
    print(f"queued {name} at {when:%Y-%m-%d %H:%M}")


def tick() -> int:
    q = read_queue()
    due = [e for e in q if datetime.fromisoformat(e["at"]) <= now()]
    if not due:
        return 0
    remaining = [e for e in q if e not in due]
    queue_path().write_text(json.dumps(remaining, indent=2), encoding="utf-8")
    code = 0
    for e in due:
        repo = find(e["name"])
        code |= run_job(repo, trigger=f"queued{(' ' + e['note']) if e.get('note') else ''}")
    return code


# --- status and queue views -------------------------------------------------------------------

def upcoming(repos: list[dict], days: int = 7) -> list[dict]:
    t0 = now()
    horizon = t0 + timedelta(days=days)
    items: list[dict] = []
    for r in repos:
        if r["kind"] in ("job", "both") and r["enabled"]:
            for t in next_runs(r["schedule"], t0, count=50, horizon_days=days + 1):
                if t <= horizon:
                    items.append({"at": t, "name": r["name"], "source": "cron"})
    for e in read_queue():
        items.append({"at": datetime.fromisoformat(e["at"]), "name": e["name"],
                      "source": "queued" + (f": {e['note']}" if e.get("note") else "")})
    return sorted(items, key=lambda i: i["at"])


def rel(t: datetime) -> str:
    d = t - now()
    s = int(d.total_seconds())
    sign = "in " if s >= 0 else ""
    s = abs(s)
    if s < 3600:
        return f"{sign}{s // 60}m"
    if s < 86400:
        return f"{sign}{s // 3600}h {(s % 3600) // 60:02d}m"
    return f"{sign}{s // 86400}d {(s % 86400) // 3600}h"


def status() -> int:
    repos = load_registry()
    rows = []
    for r in repos:
        st = read_state(r["name"]) or {}
        nxt = None
        if r["kind"] in ("job", "both") and r["enabled"]:
            runs = next_runs(r["schedule"], now(), 1)
            nxt = runs[0] if runs else None
        svc = service_state(r) if r["kind"] in ("service", "both") else "-"
        rows.append({
            "name": r["name"], "kind": r["kind"], "enabled": r["enabled"],
            "schedule": r.get("schedule", "-"),
            "next_run": nxt.isoformat(timespec="minutes") if nxt else None,
            "next_in": rel(nxt) if nxt else "-",
            "last_run": st.get("started"), "last_status": st.get("status"),
            "last_exit": st.get("exit"), "last_duration": st.get("duration_seconds"),
            "service": svc, "log": st.get("log"),
        })
    doc = {"generated": datetime.now().isoformat(timespec="seconds"),
           "repos": rows,
           "upcoming": [{"at": i["at"].isoformat(timespec="minutes"), "name": i["name"],
                         "source": i["source"]} for i in upcoming(repos)]}
    try:
        write_json(STATE_ROOT / "status.json", doc)
    except OSError:
        pass

    w = max(len(r["name"]) for r in rows) if rows else 4
    print(f"{'repo':{w}}  kind     next run            last run                 result    service")
    for r in rows:
        nr = f"{r['next_run'] or '-':16} {r['next_in']:>8}" if r["next_run"] else f"{'-':25}"
        lr = (r["last_run"] or "-")[:16]
        res = "-" if not r["last_status"] else (
            f"{r['last_status']} ({r['last_duration']}s)" if r["last_status"] != "running" else "running")
        print(f"{r['name']:{w}}  {r['kind']:8} {nr}  {lr:16}  {res:24} {r['service']}")
    return 0


def queue_view(days: int) -> int:
    items = upcoming(load_registry(), days)
    if not items:
        print(f"nothing scheduled in the next {days} day(s)")
        return 0
    print(f"{'when':16}  {'in':>9}  job        source")
    for i in items:
        print(f"{i['at']:%Y-%m-%d %H:%M}  {rel(i['at']):>9}  {i['name']:10} {i['source']}")
    return 0


def logs(name: str, n: int) -> int:
    find(name)
    latest = LOG_ROOT / name / "latest.log"
    if not latest.exists():
        print(f"no runs yet for {name}")
        return 0
    lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"# {latest.resolve()}")
    print("\n".join(lines[-n:]))
    return 0


# --- cli ------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Every repo: next run, last result, service state.")
    q = sub.add_parser("queue", help="Everything due soon, soonest first.")
    q.add_argument("--days", type=int, default=7)
    r = sub.add_parser("run", help="Run a job now, logged.")
    r.add_argument("name")
    r.add_argument("--trigger", default="manual")
    e = sub.add_parser("enqueue", help="Queue a one-off run.")
    e.add_argument("name")
    e.add_argument("--in", dest="delay", help="e.g. 20m, 2h")
    e.add_argument("--at", help="'YYYY-MM-DD HH:MM' or 'HH:MM'")
    e.add_argument("--note", default="")
    lg = sub.add_parser("logs", help="Tail the latest log.")
    lg.add_argument("name")
    lg.add_argument("-n", type=int, default=50)
    sub.add_parser("apply", help="Clone, set up, render cron and units (sudo).")
    sub.add_parser("tick", help="Run queued one-offs that are due (cron, every minute).")
    sub.add_parser("validate", help="Parse the registry and every schedule.")
    args = ap.parse_args()

    if args.cmd == "status":
        return status()
    if args.cmd == "queue":
        return queue_view(args.days)
    if args.cmd == "run":
        return run_job(find(args.name), trigger=args.trigger)
    if args.cmd == "enqueue":
        enqueue(args.name, parse_when(args.at, args.delay), args.note)
        return 0
    if args.cmd == "logs":
        return logs(args.name, args.n)
    if args.cmd == "apply":
        return apply()
    if args.cmd == "tick":
        return tick()
    if args.cmd == "validate":
        repos = load_registry()
        for r in repos:
            if r.get("schedule"):
                nxt = next_runs(r["schedule"], now(), 1)
                print(f"{r['name']}: {r['kind']}, next {nxt[0] if nxt else 'never'}")
            else:
                print(f"{r['name']}: {r['kind']}")
        print(f"{len(repos)} repo(s) ok")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
