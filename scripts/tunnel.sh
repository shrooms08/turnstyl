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
#
# Publishing is never assumed. Every write of web/config.js is read back, every
# push is checked against what gh-pages actually serves, and --status reports
# "running but unpublished" as its own outcome (exit 2) rather than as success.
set -uo pipefail
cd "$(dirname "$0")/.."

PIDFILE=./data/tunnel.pid
DAEMON_LOG=./data/tunnel.log
MODE="${1:-run}"

pid_of(){ [ -f "$PIDFILE" ] && sed -n "s/^$1=//p" "$PIDFILE" | head -1; }
alive(){ [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

PUBLISHED=""

# Every action this script takes, with a timestamp, in one file. In --supervise
# mode stdout already goes there, so the line is written once either way.
log(){
  local line
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ)  $*"
  if [ "$MODE" = "--supervise" ]; then
    echo "$line"
  else
    echo "$line"
    echo "$line" >> "$DAEMON_LOG"
  fi
}

config_url(){   # config_url <config.js body> : the URL inside it, or empty
  printf '%s' "${1:-}" | sed -nE 's/.*TURNSTYL_API = "([^"]*)".*/\1/p' | head -1
}

published_config(){   # what gh-pages actually publishes right now
  # The branch, not the Pages CDN: the branch is authoritative the instant the
  # push lands, while the CDN trails it by about a minute and caches. The CDN
  # is what scripts/tunnel_check.sh looks at, which is the other half of this.
  git fetch -q origin gh-pages >/dev/null 2>&1 || true
  git show origin/gh-pages:config.js 2>/dev/null | tr -d '\n'
}

publish_config(){  # publish_config <url-or-empty> : write it, publish it, prove it
  local want="${1:-}" got
  # web/config.js is marked skip-worktree in this checkout, so git will not
  # report this change and `git add web/config.js` would ignore it. That is
  # harmless here on purpose -- pages.sh copies the file into the gh-pages
  # worktree with cp, never with git add -- but it does mean a write that did
  # not land would be completely invisible, so it is read back before anything
  # is published.
  printf 'window.TURNSTYL_API = "%s";\n' "$want" > web/config.js
  got=$(config_url "$(tr -d '\n' < web/config.js 2>/dev/null)")
  if [ "$got" != "$want" ]; then
    PUBLISHED=""
    log "PUBLISH FAILED: web/config.js reads '${got}' after writing '${want}'." \
        "The file did not take the write, so nothing was published."
    return 1
  fi

  scripts/pages.sh --config-only 2>&1 | sed 's/^/  /'

  # Assert, do not assume. pages.sh can fail to push for reasons this script
  # cannot see -- no credentials in a detached process, a rejected push, a
  # worktree it could not build -- and every one of those used to end with the
  # page quietly pointing at nothing.
  got=$(config_url "$(published_config)")
  if [ "$got" = "$want" ]; then
    PUBLISHED="$want"
    log "published config.js -> ${want:-(empty: the page reads 'agent offline')}; gh-pages agrees"
    return 0
  fi
  PUBLISHED=""
  log "PUBLISH MISMATCH: gh-pages publishes '${got:-nothing}' but the live URL is" \
      "'${want:-(empty)}'. The page does NOT point at this agent. See the pages.sh" \
      "output just above for why the push did not land."
  return 1
}

case "$MODE" in
  --status)
    # Three outcomes, not two: running and published (0), not running (1), and
    # running but the page does not point at it (2). That third one is the one
    # that used to look identical to success.
    SUP=$(pid_of supervisor)
    URLNOW=$(pid_of url)
    PUBNOW=$(config_url "$(published_config)")
    if alive "$SUP"; then
      echo "turnstyl tunnel: running (supervisor $SUP)"
      echo "  url        ${URLNOW:-not up yet}"
      if [ -z "$PUBNOW" ]; then
        echo "  published  NO -- gh-pages publishes an empty config.js, so the page reads 'agent offline'"
      elif [ "$PUBNOW" = "$URLNOW" ]; then
        echo "  published  yes -- gh-pages points at this tunnel"
      else
        echo "  published  MISMATCH -- gh-pages points at $PUBNOW"
      fi
      echo "  serve      $(pid_of serve)   cloudflared $(pid_of cloudflared)   caffeinate $(pid_of caffeinate)"
      LAST=$(pid_of lastcheck); RESULT=$(pid_of lastresult)
      echo "  check      ${LAST:-not yet}   ${RESULT:-not checked yet}"
      echo "  log        $DAEMON_LOG"
      if [ -n "$PUBNOW" ] && [ "$PUBNOW" = "$URLNOW" ]; then
        exit 0
      fi
      echo
      echo "  The agent is running but the published page does not point at it."
      echo "  Look for a PUBLISH line in $DAEMON_LOG, or republish by hand:"
      echo "    printf 'window.TURNSTYL_API = \"%s\";\\n' '${URLNOW}' > web/config.js && scripts/pages.sh --config-only"
      exit 2
    fi
    echo "turnstyl tunnel: not running"
    if [ -n "$PUBNOW" ]; then
      echo "  but gh-pages still publishes $PUBNOW, which nothing is serving."
      echo "  Run scripts/tunnel.sh --stop to put the page back to 'agent offline'."
    fi
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
    # tunnel that is gone. Through publish_config so the empty default is
    # proved to have landed too -- a stop that fails to republish leaves the
    # page pointing at a dead hostname, which is worse than 'agent offline'.
    if publish_config ""; then
      rm -f "$PIDFILE"
      exit 0
    fi
    echo "turnstyl tunnel: the page may still point at the tunnel that just stopped." >&2
    rm -f "$PIDFILE"
    exit 1
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
    # Wait for the URL to be *published*, not merely printed. This is the
    # ordering that regressed: cloudflared announces a hostname within a second
    # or two, but that hostname still has to answer a request and then reach
    # gh-pages, and it is only that last step that makes the page live. Waiting
    # on the announcement meant --daemon reported success, and --status showed
    # a URL, while config.js was still the empty default.
    DEADLINE=$(( $(date +%s) + ${DAEMON_WAIT:-360} ))
    SAWURL=""; SAWSUP=""
    while [ "$(date +%s)" -lt "$DEADLINE" ]; do
      SUP=$(pid_of supervisor)
      # The supervisor writes the pid file a moment after it starts, so an
      # empty one here means "not yet", not "dead". Only a supervisor that was
      # seen alive and then went away is a reason to stop waiting.
      if [ -n "$SUP" ]; then
        SAWSUP=1
        alive "$SUP" || break
      elif [ -n "$SAWSUP" ]; then
        break
      fi
      [ -n "$(pid_of published)" ] && break
      SEEN=$(pid_of url)
      if [ -n "$SEEN" ] && [ "$SEEN" != "$SAWURL" ]; then
        SAWURL="$SEEN"
        echo "turnstyl tunnel: cloudflared says $SAWURL; verifying it answers, then publishing"
      fi
      sleep 2
    done
    if [ -z "$(pid_of url)" ]; then
      echo "turnstyl tunnel: no tunnel came up within ${DAEMON_WAIT:-360}s; see $DAEMON_LOG" >&2
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
WATCH_SECONDS="${WATCH_SECONDS:-30}"
# How many watchdog passes between full re-checks of what gh-pages publishes.
REPUBLISH_EVERY="${REPUBLISH_EVERY:-20}"
PASSES=0
PY_BIN=.venv/bin/python

CAFF=""; SERVE=""; CF=""
VERIFIED=""
LASTCHECK=""; LASTRESULT="not checked yet"
PUBLIC_FAILS=0
mkdir -p ./data

record_pids(){   # the pid file is how --status and --stop find this run
  {
    echo "supervisor=$$"
    echo "caffeinate=$CAFF"
    echo "serve=$SERVE"
    echo "cloudflared=$CF"
    echo "url=${URL:-}"
    # url is whatever cloudflared last printed; published is what gh-pages was
    # proved to be serving. They are different facts and the gap between them
    # is exactly the state that used to be invisible.
    echo "verified=${VERIFIED:-}"
    echo "published=${PUBLISHED:-}"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "lastcheck=${LASTCHECK:-}"
    echo "lastresult=${LASTRESULT:-}"
  } > "$PIDFILE"
}

local_ok(){ curl -s -m 5 -o /dev/null "http://127.0.0.1:$PORT/api/status"; }

worker_ok(){
  # The worker is a thread inside serve, so the process says so out loud and
  # this reads it. A pass older than four intervals is a stalled worker.
  curl -s -m 5 "http://127.0.0.1:$PORT/api/status" 2>/dev/null | "$PY_BIN" -c "
import json, sys
try:
    w = (json.load(sys.stdin) or {}).get('worker') or {}
except Exception:
    sys.exit(1)
if not w.get('running'):
    sys.exit(1)
age = w.get('seconds_since_pass')
sys.exit(0 if age is None or age < 120 else 1)
" 2>/dev/null
}

public_ok(){  # public_ok <url>
  local url="${1:-}" host ip rc
  [ -n "$url" ] || return 1
  curl -s -m 15 -o /dev/null -H "ngrok-skip-browser-warning: true" "$url/api/status"
  rc=$?
  [ "$rc" -eq 0 ] && return 0
  # curl 6 is "could not resolve host", and for a hostname cloudflared minted
  # seconds ago that usually means this machine's resolver has not caught up
  # yet -- not that the tunnel is dead. Visitors resolve it with their own DNS,
  # so refusing to publish over a local lookup would take the page down for
  # everyone to fix a problem only this Mac has. Ask a resolver directly and
  # try the edge itself before believing it. Any other curl failure is a real
  # one and is returned as such.
  [ "$rc" -eq 6 ] || return 1
  host=${url#https://}; host=${host%%/*}
  ip=$(dig +short "$host" 2>/dev/null | grep -E '^[0-9]+\.' | head -1)
  [ -n "$ip" ] || return 1
  curl -s -m 15 -o /dev/null --resolve "$host:443:$ip" \
       -H "ngrok-skip-browser-warning: true" "$url/api/status"
}

wait_public(){  # wait_public <url> <seconds> : does the hostname actually serve?
  local url="$1" limit="${2:-90}" waited=0
  while [ "$waited" -lt "$limit" ]; do
    public_ok "$url" && return 0
    sleep 3; waited=$((waited + 3))
  done
  return 1
}

start_cloudflared(){   # sets CF and URL, or returns 1
  CF=""; URL=""
  cloudflared tunnel --url "http://127.0.0.1:$PORT" > "$LOGDIR/cloudflared.log" 2>&1 & CF=$!
  local i
  for i in $(seq 1 60); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGDIR/cloudflared.log" | tail -1)
    [ -n "$URL" ] && break
    kill -0 "$CF" 2>/dev/null || break
    sleep 1
  done
  [ -n "$URL" ] || return 1
  return 0
}

restart_serve(){
  log "serve is not answering on 127.0.0.1:$PORT; restarting it"
  [ -n "$SERVE" ] && kill "$SERVE" 2>/dev/null
  .venv/bin/turnstyl serve --with-worker --port "$PORT" --db "$TURNSTYL_DB" \
    >> "$LOGDIR/serve.log" 2>&1 & SERVE=$!
  local i
  for i in $(seq 1 40); do local_ok && break; sleep 0.5; done
  if local_ok; then
    log "serve is back (pid $SERVE)"
  else
    log "serve did NOT come back; see $LOGDIR/serve.log"
  fi
  record_pids
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

# A hostname is published only once it has actually served a request through
# the public internet. Publishing one that merely got printed is how the page
# ended up pointing at a tunnel that never finished registering.
ATTEMPT=0
while : ; do
  ATTEMPT=$((ATTEMPT + 1))
  if ! start_cloudflared; then
    log "cloudflared printed no trycloudflare.com URL (attempt $ATTEMPT); see $LOGDIR/cloudflared.log"
    [ -n "$CF" ] && kill "$CF" 2>/dev/null
    [ "$ATTEMPT" -ge 3 ] && { log "giving up after $ATTEMPT attempts"; cleanup; }
    sleep 5; continue
  fi
  record_pids
  log "cloudflared says $URL (attempt $ATTEMPT); checking it answers before publishing"
  if wait_public "$URL" 90; then
    log "$URL answers /api/status; publishing"
    break
  fi
  log "$URL never answered in 90s; killing that cloudflared and starting another"
  kill "$CF" 2>/dev/null
  [ "$ATTEMPT" -ge 3 ] && {
    log "three tunnels in a row failed to serve; not publishing a dead URL"
    cleanup
  }
  sleep 5
done

# Write the verified URL into web/config.js and get it onto gh-pages before
# anything reports success. LASTRESULT is set from what publish_config proved,
# not from having reached this line.
VERIFIED="$URL"
LASTCHECK=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if publish_config "$URL"; then
  LASTRESULT="ok (published)"
else
  LASTRESULT="running but UNPUBLISHED"
  log "the agent is up at $URL but the page does not point at it; " \
      "scripts/tunnel.sh --status will say so, and --stop then --daemon retries"
fi
record_pids

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
  empty config.js. A watchdog checks serve, the worker and the public
  hostname every ${WATCH_SECONDS}s and repairs what it can.
STATUS

log "watchdog running every ${WATCH_SECONDS}s"
while : ; do
  sleep "$WATCH_SECONDS"
  LASTCHECK=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  if ! local_ok; then
    LASTRESULT="serve down, restarting"; record_pids
    restart_serve
  elif ! worker_ok; then
    LASTRESULT="worker stalled, restarting serve"; record_pids
    log "the worker has not completed a pass recently; restarting serve to revive it"
    restart_serve
  fi

  if public_ok "$URL"; then
    [ "$PUBLIC_FAILS" -gt 0 ] && log "$URL is answering again after $PUBLIC_FAILS failure(s)"
    PUBLIC_FAILS=0
    LASTRESULT="ok"
    # A healthy tunnel nobody can reach from the page is still an outage. Retry
    # immediately while our own record says we are unpublished, and otherwise
    # re-prove it against gh-pages every REPUBLISH_EVERY passes rather than on
    # every one -- the check costs a git fetch, which is not worth doing twice
    # a minute forever.
    PASSES=$((PASSES + 1))
    if [ "$PUBLISHED" != "$URL" ] || [ $((PASSES % REPUBLISH_EVERY)) -eq 0 ]; then
      if [ "$(config_url "$(published_config)")" != "$URL" ]; then
        log "gh-pages does not publish $URL; publishing it again"
        if publish_config "$URL"; then
          LASTRESULT="ok (republished)"
        else
          LASTRESULT="running but UNPUBLISHED"
        fi
      else
        PUBLISHED="$URL"
      fi
    fi
  else
    PUBLIC_FAILS=$((PUBLIC_FAILS + 1))
    LASTRESULT="public unreachable ($PUBLIC_FAILS/3)"
    log "$URL did not answer ($PUBLIC_FAILS of 3 before a rebuild)"
    if [ "$PUBLIC_FAILS" -ge 3 ]; then
      log "three consecutive public failures; rebuilding the tunnel"
      kill "$CF" 2>/dev/null
      OLD_URL="$URL"
      if start_cloudflared && wait_public "$URL" 90; then
        VERIFIED="$URL"
        log "new tunnel $URL answers; republishing config.js (was $OLD_URL)"
        if publish_config "$URL"; then
          PUBLIC_FAILS=0
          LASTRESULT="ok (rebuilt)"
        else
          PUBLIC_FAILS=0
          LASTRESULT="rebuilt but UNPUBLISHED"
        fi
      else
        log "the replacement tunnel did not answer either; leaving config.js at $OLD_URL and retrying"
        URL="$OLD_URL"
        LASTRESULT="rebuild failed"
      fi
    fi
  fi
  record_pids
done
