#!/usr/bin/env bash
# Refresh the installed runner after validated changes have been deployed.
# The board detects its new asset version on the next /api/status poll.
set -euo pipefail

ATLAS_REFRESH_URL="${ATLAS_REFRESH_URL:-http://127.0.0.1:8100}"
sudo -n systemctl restart atlas
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 --output /dev/null "$ATLAS_REFRESH_URL/healthz"; then
    printf 'Atlas restarted and healthy; the dashboard refreshes on its next poll.\n'
    exit 0
  fi
  sleep 1
done
printf 'Atlas did not become healthy; inspect journalctl -u atlas.\n' >&2
exit 1
