# Hosted repositories

Other projects run on this Pi beside atlas: some are jobs that fire on a
schedule, some are servers that must stay up, some are both. They are not
atlas jobs (those are LLM-driven, declared in `jobs/*.yaml`, and run inside
the runner); they are ordinary checkouts with a command. `scripts/repos.py`
keeps them cloned, current, scheduled and logged, from one registry file.

## The registry

`infra/repos.toml`, one `[[repo]]` per checkout. Field reference is in the
file's header. After editing it:

```sh
/opt/atlas/scripts/repos.py validate    # parses every entry and schedule
/opt/atlas/scripts/repos.py apply       # clone, set up, render cron + units
```

`apply` needs sudo for three things only: creating `/var/log/atlas-repos`
and `/var/lib/atlas-repos`, installing `/etc/cron.d/atlas-repos`, and
installing `atlas-repo-<name>.service` units. Everything else runs as the
service user.

## Seeing what will run

```sh
/opt/atlas/scripts/repos.py status      # per repo: next run, last result, service state
/opt/atlas/scripts/repos.py queue       # everything due in the next 7 days, soonest first
```

`status` also writes `/var/lib/atlas-repos/status.json`, the same table as
JSON, for the ops board to read.

Queue a one-off run without touching the schedule:

```sh
/opt/atlas/scripts/repos.py enqueue finance --in 20m
/opt/atlas/scripts/repos.py enqueue finance --at "08:30" --note "after fixing the key"
/opt/atlas/scripts/repos.py run finance    # right now, logged the same way
```

Queued runs sit in `/var/lib/atlas-repos/queue.json` and appear in `queue`;
the once-a-minute `tick` cron line fires them when due.

## Logs and reporting

Each run writes `/var/log/atlas-repos/<name>/<timestamp>_job.log` (the last
60 kept, `latest.log` pointing at the newest) and overwrites
`/var/lib/atlas-repos/<name>.json` with a summary: trigger, start, end,
duration, exit code, and the last fifteen lines of the log. A service's
stdout goes to `service.log` in the same directory.

```sh
/opt/atlas/scripts/repos.py logs finance -n 80
```

## How a run goes

1. `git pull --ff-only` in the checkout. A failed pull is logged and the run
   continues on what is on disk; local changes are never discarded.
2. `setup` runs if `setup_marker` is absent (a venv, `npm ci`, and so on).
3. `job` runs in the checkout with `~/.local/bin` on PATH and `HOME` set,
   so tools installed per-user (`claude`, `uv`) resolve under cron.
4. Exit code and duration are recorded. Non-zero shows as `failed` in
   `status` until the next successful run.

## Credentials

A repo that needs its own GitHub deploy key gets an ssh alias in
`~/.ssh/config` and uses it in `url`:

```
Host github-finance
  HostName github.com
  User git
  IdentityFile ~/.ssh/finance_deploy
  IdentitiesOnly yes
```

Secrets the job itself needs (a `.env`) live inside the checkout and are
gitignored there; `apply` never touches them.
