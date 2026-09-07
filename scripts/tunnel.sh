#!/usr/bin/env bash
# Go live: the API on this Mac behind a Cloudflare quick tunnel, the page on
# GitHub Pages pointed at it.
#
#   scripts/tunnel.sh            run in the foreground; Ctrl-C takes it down
#   scripts/tunnel.sh --daemon   run detached; survives the terminal closing
#   scripts/tunnel.sh --status   is it running, and at what URL
#   scripts/tunnel.sh --stop     stop it and publish an empty config.js
#
# Runs: caffeinate (no sleep), `turnstyl serve --with-worker` with PAYMENTS=base
# and the real model, and `cloudflared tunnel --url`. Writes the tunnel URL into
# web/config.js and pushes only that file to gh-pages. On the way down it stops
# all three and publishes an empty config.js so the page shows "agent offline".
set -uo pipefail
cd "$(dirname "$0")/.."

PIDFILE=./data/tunnel.pid
DAEMON_LOG=./data/tunnel.log
MODE="${1:-run}"

pid_of(){ [ -f "$PIDFILE" ] && sed -n "s/^$1=//p" "$PIDFILE" | head -1; }
alive(){ [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

case "$MODE" in
  --status)
    SUP=$(pid_of supervisor)
    if alive "$SUP"; then
      echo "turnstyl tunnel: running (supervisor $SUP)"
      echo "  url    $(pid_of url)"
      echo "  serve  $(pid_of serve)   cloudflared $(pid_of cloudflared)   caffeinate $(pid_of caffeinate)"
      echo "  log    $DAEMON_LOG"
      exit 0
    fi
    echo "turnstyl tunnel: not running"
    [ -f "$PIDFILE" ] && echo "  (stale $PIDFILE from a previous run)"
    exit 1
    ;;
  --stop)
    SUP=$(pid_of supervisor)
    STOPPED=0
    if alive "$SUP"; then
      # the supervisor's own TERM trap stops the three and republishes
      kill "$SUP" 2>/dev/null && STOPPED=1
      for _ in $(seq 1 30); do alive "$SUP" || break; sleep 0.5; done
    fi
    for name in cloudflared serve caffeinate; do
      P=$(pid_of "$name"); alive "$P" && { kill "$P" 2>/dev/null; STOPPED=1; }
    done
    if [ "$STOPPED" = "1" ]; then
      echo "turnstyl tunnel: stopped"
    else
      echo "turnstyl tunnel: nothing was running"
    fi
    # belt and braces: whatever happened above, the page must not point at a
    # tunnel that is gone
    printf 'window.TURNSTYL_API = "";\n' > web/config.js
    scripts/pages.sh --config-only 2>&1 | sed 's/^/  /'
    rm -f "$PIDFILE"
    exit 0
    ;;
  --daemon)
    if alive "$(pid_of supervisor)"; then
      echo "turnstyl tunnel: already running (supervisor $(pid_of supervisor)); " \
           "use --stop first" >&2
      exit 1
    fi
    mkdir -p ./data
    : > "$DAEMON_LOG"
    nohup "$0" --supervise >> "$DAEMON_LOG" 2>&1 &
    disown $! 2>/dev/null
    echo "turnstyl tunnel: starting in the background, log $DAEMON_LOG"
    for _ in $(seq 1 90); do
      [ -n "$(pid_of url)" ] && break
      sleep 1
    done
    URL=$(pid_of url)
    if [ -z "$URL" ]; then
      echo "turnstyl tunnel: did not come up within 90s; see $DAEMON_LOG" >&2
      tail -20 "$DAEMON_LOG" >&2
      exit 1
    fi
    exec "$0" --status
    ;;
  --supervise|run|"") ;;   # fall through and run
  *)
    echo "turnstyl tunnel: unknown option $MODE" >&2
    echo "  usage: scripts/tunnel.sh [--daemon|--status|--stop]" >&2
    exit 1
    ;;
esac

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
mkdir -p ./data
record_pids(){   # the pid file is how --status and --stop find this run
  {
    echo "supervisor=$$"
    echo "caffeinate=$CAFF"
    echo "serve=$SERVE"
    echo "cloudflared=$CF"
    echo "url=${URL:-}"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$PIDFILE"
}
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
  rm -f "$PIDFILE"
  echo "turnstyl tunnel: down"
  exit 0
}
trap cleanup INT TERM

caffeinate -dims & CAFF=$!
record_pids
.venv/bin/turnstyl serve --with-worker --port "$PORT" --db "$TURNSTYL_DB" > "$LOGDIR/serve.log" 2>&1 & SERVE=$!
for i in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status" && break; sleep 0.5; done
curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status" || { echo "turnstyl tunnel: serve did not come up; see $LOGDIR/serve.log" >&2; cleanup; }

cloudflared tunnel --url "http://127.0.0.1:$PORT" > "$LOGDIR/cloudflared.log" 2>&1 & CF=$!
record_pids
URL=""
for i in $(seq 1 60); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGDIR/cloudflared.log" | head -1)
  [ -n "$URL" ] && break
  kill -0 "$CF" 2>/dev/null || break
  sleep 1
done
[ -n "$URL" ] || { echo "turnstyl tunnel: cloudflared did not print a trycloudflare.com URL; see $LOGDIR/cloudflared.log" >&2; cleanup; }
for i in $(seq 1 20); do curl -s -o /dev/null -H "ngrok-skip-browser-warning: true" "$URL/api/status" && break; sleep 1; done

record_pids
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

  Ctrl-C (or scripts/tunnel.sh --stop) stops everything and publishes an
  empty config.js.
STATUS
wait
