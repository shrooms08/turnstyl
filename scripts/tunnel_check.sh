#!/usr/bin/env bash
# From any network: is the published page pointing at a reachable agent?
#
#   scripts/tunnel_check.sh
#
# Three failures that used to look alike, kept apart here, because the fix for
# each is different:
#
#   no URL published     gh-pages serves the empty default. The page says
#                        "agent offline". Nothing is wrong with the tunnel;
#                        the publish never happened.
#   published != running a tunnel is running on this Mac at one hostname and
#                        the page points at another. Visitors reach a dead
#                        hostname while the operator sees a healthy agent.
#   published but down   the page points at a hostname that does not answer.
#
# The middle one can only be told from this machine, so it is checked only when
# ./data/tunnel.pid says a supervisor is alive here. Run from anywhere else and
# the check still does the other two.
set -uo pipefail
cd "$(dirname "$0")/.."

PAGES="${PAGES_URL:-https://shrooms08.github.io/turnstyl/}"
PIDFILE=./data/tunnel.pid

pid_of(){ [ -f "$PIDFILE" ] && sed -n "s/^$1=//p" "$PIDFILE" | head -1; }

# What is running here, if anything. Empty when this is not the operator's Mac.
RUNNING=""
SUP=$(pid_of supervisor)
if [ -n "$SUP" ] && kill -0 "$SUP" 2>/dev/null; then
  RUNNING=$(pid_of url)
fi

CFG=$(curl -s --max-time 15 "${PAGES}config.js?v=$(date +%s)")
URL=$(printf '%s' "$CFG" | sed -nE 's/.*TURNSTYL_API = "([^"]*)".*/\1/p' | head -1)
echo "page   $PAGES"
[ -n "$RUNNING" ] && echo "local  a tunnel is running here at $RUNNING"

if [ -z "$CFG" ]; then
  echo "OFFLINE: could not fetch config.js from GitHub Pages"; exit 1
fi

if [ -z "$URL" ]; then
  if [ -n "$RUNNING" ]; then
    echo "UNPUBLISHED: config.js publishes no API URL, but a tunnel is running here at $RUNNING."
    echo "  The agent is up and the page cannot see it. Publish the running URL:"
    echo "    scripts/tunnel.sh --status        # says the same thing, with the log to read"
    exit 2
  fi
  echo "OFFLINE: config.js publishes no API URL (the operator has not run scripts/tunnel.sh)"
  exit 1
fi

echo "api    $URL"

if [ -n "$RUNNING" ] && [ "$URL" != "$RUNNING" ]; then
  echo "MISMATCH: the page publishes $URL but the tunnel running here is $RUNNING."
  echo "  Visitors are being sent to a hostname this Mac is not serving. The"
  echo "  publish is stale -- scripts/tunnel.sh --status will also report this,"
  echo "  and the watchdog republishes on its next pass."
  exit 2
fi

CODE=$(curl -s -o /tmp/turnstyl-tunnel-status.json -w '%{http_code}' --max-time 15 -H "ngrok-skip-browser-warning: true" "$URL/api/status")
if [ "$CODE" = "200" ]; then
  echo "OK: $(python3 -c "import json;d=json.load(open('/tmp/turnstyl-tunnel-status.json'));print('records', d.get('records'), '| payments', d.get('payments_backend'), '| remaining today', d.get('remaining_today'), '| memory', 'missing' if d.get('memory_missing') else 'present')" 2>/dev/null || echo "status 200")"
else
  echo "OFFLINE: $URL/api/status answered ${CODE:-nothing}"; exit 1
fi
