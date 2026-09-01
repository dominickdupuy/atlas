#!/usr/bin/env bash
#
# atlas deploy: fast-forward the checkout to origin/<branch> and restart.
#
# Run by atlas-deploy.service on a 5-minute timer. Exits 0 without touching
# anything when the remote has not moved, which is the overwhelmingly common
# case — a timer that reinstalls dependencies every five minutes would chew
# through the microSD this host boots from.
#
# Everything is overridable by environment variable so the same script can be
# exercised against a scratch clone without pointing it at the live tree.
#
#   ATLAS_REPO          checkout to update            (default /opt/atlas)
#   ATLAS_DEPLOY_BRANCH branch to track               (default release)
#   ATLAS_REMOTE        remote name                   (default origin)
#   ATLAS_RESTART_CMD   how to restart the service    (default sudo systemctl restart atlas)
#   ATLAS_UV            uv binary                     (default: PATH, then ~/.local/bin/uv)
#
set -euo pipefail

REPO="${ATLAS_REPO:-/opt/atlas}"
BRANCH="${ATLAS_DEPLOY_BRANCH:-release}"
REMOTE="${ATLAS_REMOTE:-origin}"
RESTART_CMD="${ATLAS_RESTART_CMD:-sudo systemctl restart atlas}"

log() { printf '%s deploy: %s\n' "$(date -Is)" "$*"; }
die() { printf '%s deploy: ERROR %s\n' "$(date -Is)" "$*" >&2; exit 1; }

resolve_uv() {
  if [[ -n "${ATLAS_UV:-}" ]]; then
    printf '%s' "$ATLAS_UV"
  elif command -v uv >/dev/null 2>&1; then
    command -v uv
  elif [[ -x "$HOME/.local/bin/uv" ]]; then
    printf '%s' "$HOME/.local/bin/uv"
  else
    die "uv not found; set ATLAS_UV"
  fi
}

[[ -d "$REPO/.git" ]] || die "$REPO is not a git checkout"
cd "$REPO"

log "checking $REMOTE/$BRANCH"
git fetch --quiet "$REMOTE" "$BRANCH" || die "fetch failed"

current="$(git rev-parse HEAD)"
target="$(git rev-parse "$REMOTE/$BRANCH")"

if [[ "$current" == "$target" ]]; then
  log "already at ${target:0:12}; nothing to do"
  exit 0
fi

log "updating ${current:0:12} -> ${target:0:12}"
# Hard reset, not merge or rebase: the deployed tree is a mirror of the
# branch, never a place work happens, so any local divergence is corruption
# rather than something to preserve.
git reset --hard --quiet "$target"

UV="$(resolve_uv)"
log "syncing dependencies with $UV"
( cd "$REPO/runner" && "$UV" sync --frozen ) || die "uv sync failed"

log "restarting: $RESTART_CMD"
# Unquoted on purpose: the command is a configured word list, not a filename.
# shellcheck disable=SC2086
$RESTART_CMD || die "restart failed"

log "deployed ${target:0:12}"
