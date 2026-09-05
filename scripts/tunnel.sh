#!/usr/bin/env bash
# Go live: the API on this Mac behind a Cloudflare quick tunnel, the page on
# GitHub Pages pointed at it. One command; Ctrl-C takes it all down again.
#
#   scripts/tunnel.sh
#
# Runs: caffeinate (no sleep), `turnstyl serve --with-worker` with PAYMENTS=base
# and the real model, and `cloudflared tunnel --url`. Writes the tunnel URL into
# web/config.js and pushes only that file to gh-pages. On exit it stops all
# three and publishes an empty config.js so the page shows "agent offline".
set -uo pipefail
cd "$(dirname "$0")/.."

command -v cloudflared >/dev/null 2>&1 || {
  echo "turnstyl tunnel: cloudflared is not installed. brew install cloudflared" >&2; exit 1; }
[ -f .env ] || { echo "turnstyl tunnel: no .env in $(pwd)" >&2; exit 1; }
set -a; source .env; set +a
for v in AGENT_ADDRESS AGENT_PRIVATE_KEY RECEIPTS_ADDRESS BASE_SEPOLIA_RPC USDC_ADDRESS ANTHROPIC_API_KEY; do
  [ -n "${!v:-}" ] || { echo "turnstyl tunnel: $v is not set in .env" >&2; exit 1; }
done

PORT="${PORT:-8787}"
export PAYMENTS=base
unset MOCK_LLM
export TURNSTYL_DB="${TURNSTYL_DB:-./data/turnstyl.db}"
export LLM_MODEL="${LLM_MODEL:-claude-haiku-4-5}"
LOGDIR=./data; mkdir -p "$LOGDIR"

CAFF=""; SERVE=""; CF=""
publish_config(){  # publish_config <url-or-empty>
  printf 'window.TURNSTYL_API = "%s";\n' "$1" > web/config.js
  scripts/pages.sh --config-only 2>&1 | sed 's/^/  /'
}
cleanup(){
  echo; echo "turnstyl tunnel: stopping"
  [ -n "$CF" ]    && kill "$CF"    2>/dev/null
  [ -n "$SERVE" ] && kill "$SERVE" 2>/dev/null
  [ -n "$CAFF" ]  && kill "$CAFF"  2>/dev/null
  wait 2>/dev/null
  echo "turnstyl tunnel: publishing an empty config.js so the page reads 'agent offline'"
  publish_config ""
  echo "turnstyl tunnel: down"
  exit 0
}
trap cleanup INT TERM

caffeinate -dims & CAFF=$!
.venv/bin/turnstyl serve --with-worker --port "$PORT" --db "$TURNSTYL_DB" > "$LOGDIR/serve.log" 2>&1 & SERVE=$!
for i in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status" && break; sleep 0.5; done
curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status" || { echo "turnstyl tunnel: serve did not come up; see $LOGDIR/serve.log" >&2; cleanup; }

cloudflared tunnel --url "http://127.0.0.1:$PORT" > "$LOGDIR/cloudflared.log" 2>&1 & CF=$!
URL=""
for i in $(seq 1 60); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGDIR/cloudflared.log" | head -1)
  [ -n "$URL" ] && break
  kill -0 "$CF" 2>/dev/null || break
  sleep 1
done
[ -n "$URL" ] || { echo "turnstyl tunnel: cloudflared did not print a trycloudflare.com URL; see $LOGDIR/cloudflared.log" >&2; cleanup; }
for i in $(seq 1 20); do curl -s -o /dev/null -H "ngrok-skip-browser-warning: true" "$URL/api/status" && break; sleep 1; done

echo "turnstyl tunnel: publishing config.js -> $URL"
publish_config "$URL"

PAGES=$(scripts/pages.sh --config-only 2>/dev/null | grep -o 'https://[^ ]*github.io/[^ ]*' | tail -1)
cat <<STATUS

  turnstyl is live
  page   ${PAGES:-https://shrooms08.github.io/turnstyl/}
  api    $URL
  local  http://127.0.0.1:$PORT
  db     $TURNSTYL_DB   payments=base   model=$LLM_MODEL
  pids   caffeinate $CAFF   serve $SERVE   cloudflared $CF
  logs   $LOGDIR/serve.log   $LOGDIR/cloudflared.log

  Ctrl-C stops everything and publishes an empty config.js.
STATUS
wait
