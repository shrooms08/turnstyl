#!/usr/bin/env bash
# From any network: is the published page pointing at a reachable agent?
#
#   scripts/tunnel_check.sh
set -uo pipefail
PAGES="${PAGES_URL:-https://shrooms08.github.io/turnstyl/}"
CFG=$(curl -s --max-time 15 "${PAGES}config.js?v=$(date +%s)")
URL=$(echo "$CFG" | grep -oE 'https://[a-z0-9.-]+' | head -1)
echo "page   $PAGES"
if [ -z "$CFG" ]; then echo "OFFLINE: could not fetch config.js from GitHub Pages"; exit 1; fi
if [ -z "$URL" ]; then echo "OFFLINE: config.js publishes no API URL (the operator has not run scripts/tunnel.sh)"; exit 1; fi
echo "api    $URL"
CODE=$(curl -s -o /tmp/turnstyl-tunnel-status.json -w '%{http_code}' --max-time 15 -H "ngrok-skip-browser-warning: true" "$URL/api/status")
if [ "$CODE" = "200" ]; then
  echo "OK: $(python3 -c "import json;d=json.load(open('/tmp/turnstyl-tunnel-status.json'));print('records', d.get('records'), '| payments', d.get('payments_backend'), '| remaining today', d.get('remaining_today'), '| memory', 'missing' if d.get('memory_missing') else 'present')" 2>/dev/null || echo "status 200")"
else
  echo "OFFLINE: $URL/api/status answered ${CODE:-nothing}"; exit 1
fi
