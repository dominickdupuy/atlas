#!/usr/bin/env bash
#
# Install the atlas system units. Requires root:
#
#     sudo /opt/atlas/scripts/install-systemd.sh
#
# Idempotent. Validates the sudoers drop-in BEFORE installing it, because a
# malformed file in /etc/sudoers.d locks every user out of sudo on this box
# and the recovery for that is a physical console.
#
set -euo pipefail

REPO="${ATLAS_REPO:-/opt/atlas}"
SERVICE_USER="${ATLAS_USER:-domdd}"
UNIT_DIR=/etc/systemd/system
ENV_FILE=/etc/atlas/atlas.env

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$1" >&2; }

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

# --- 1. sudoers, validated first -------------------------------------------
log "Validating the sudoers drop-in"
src_sudoers="$REPO/infra/sudoers/atlas"
if ! visudo -c -f "$src_sudoers"; then
  echo "REFUSING to install: $src_sudoers does not parse" >&2
  exit 1
fi
install -o root -g root -m 0440 "$src_sudoers" /etc/sudoers.d/atlas
log "Re-validating the whole sudoers set"
visudo -c >/dev/null || {
  rm -f /etc/sudoers.d/atlas
  echo "sudoers broke with the drop-in installed; removed it again" >&2
  exit 1
}

# --- 2. secrets file --------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
  log "Creating $ENV_FILE with a fresh API token"
  mkdir -p /etc/atlas
  token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'ATLAS_API_TOKEN=%s\n' "$token" > "$ENV_FILE"
  chown "root:$SERVICE_USER" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
else
  log "$ENV_FILE exists; leaving it alone"
fi

# --- 3. a user-scope atlas would fight this one for the port ---------------
if runuser -l "$SERVICE_USER" -c 'systemctl --user is-enabled atlas.service' >/dev/null 2>&1; then
  warn "A user-scope atlas.service is enabled for $SERVICE_USER and will bind"
  warn "the same port. Disable it first:"
  warn "    systemctl --user disable --now atlas.service"
fi

# --- 4. units ---------------------------------------------------------------
log "Installing units"
for unit in atlas.service atlas-deploy.service atlas-deploy.timer; do
  install -o root -g root -m 0644 "$REPO/infra/systemd/$unit" "$UNIT_DIR/$unit"
  echo "  $unit"
done

systemctl daemon-reload
log "Enabling atlas.service and atlas-deploy.timer"
systemctl enable --now atlas.service
systemctl enable --now atlas-deploy.timer

log "Status"
systemctl --no-pager --lines=0 status atlas.service || true
systemctl --no-pager list-timers atlas-deploy.timer || true

cat <<EOF

Installed. Verify with:
  systemctl status atlas
  curl -sS -H "Authorization: Bearer \$(sudo sed -n 's/^ATLAS_API_TOKEN=//p' $ENV_FILE)" \\
      http://127.0.0.1:8100/api/status | head
  journalctl -u atlas-deploy -n 50 --no-pager
EOF
