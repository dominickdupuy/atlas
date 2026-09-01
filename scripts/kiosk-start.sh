#!/usr/bin/env bash
#
# Launch the ops board in Chromium kiosk mode on the attached monitor.
#
# Started from ~/.config/labwc/autostart inside the existing desktop session
# (NOT under cage, which would need its own seat and would fight the desktop
# autologin already running on tty1 — see WORKLOG.md).
#
set -uo pipefail

URL_BASE="${ATLAS_KIOSK_URL:-http://127.0.0.1:8100}"
ENV_FILE="${ATLAS_ENV_FILE:-$HOME/.config/atlas/atlas.env}"
[[ -r /etc/atlas/atlas.env ]] && ENV_FILE=/etc/atlas/atlas.env

# Chromium state on tmpfs: this host boots from microSD with no SSD, and a
# browser running 24/7 is a continuous-write machine. Nothing here is worth
# preserving across a reboot — the ?token= visit re-establishes the session
# cookie on every start.
RUNTIME_DIR="${ATLAS_KIOSK_RUNTIME:-/dev/shm/atlas-kiosk}"
mkdir -p "$RUNTIME_DIR/cache" "$RUNTIME_DIR/profile"

log() { printf '%s kiosk: %s\n' "$(date -Is)" "$*"; }

# Wait for the API rather than sleeping a fixed interval: the white-screen
# race on autostart is just Chromium arriving before the server is listening.
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null "$URL_BASE/healthz"; then
    break
  fi
  sleep 1
done

TOKEN=""
if [[ -r "$ENV_FILE" ]]; then
  TOKEN="$(sed -n 's/^ATLAS_API_TOKEN=//p' "$ENV_FILE" | head -1)"
fi
URL="$URL_BASE/dashboard"
if [[ -n "$TOKEN" ]]; then
  # One-time ?token= sets the auth cookie and redirects (see auth.py); a page
  # navigation cannot carry an Authorization header.
  URL="$URL_BASE/dashboard?token=$TOKEN"
fi

log "starting chromium against $URL_BASE/dashboard"

exec /usr/bin/chromium \
  --kiosk \
  --ozone-platform=wayland \
  --user-data-dir="$RUNTIME_DIR/profile" \
  --disk-cache-dir="$RUNTIME_DIR/cache" \
  --disk-cache-size=52428800 \
  --no-first-run \
  --no-default-browser-check \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --hide-crash-restore-bubble \
  --disable-features=Translate,TranslateUI,InfiniteSessionRestore \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --autoplay-policy=no-user-gesture-required \
  "$URL"
