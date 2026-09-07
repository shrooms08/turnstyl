#!/usr/bin/env bash
# Run the turnstyl MCP server over stdio against a throwaway turnstyl and check
# every tool. Uses a private port and its own database, so the operator's agent
# on 8787 is left alone.
#
#   scripts/test_mcp.sh
#
# PAYMENTS=fake, so the paying tool is exercised through the simulate path and
# x402 must report itself unavailable with a reason. The real gasless rail is
# covered end to end by the x402 beat in scripts/demo_live.sh.
set -uo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "turnstyl test_mcp: .env is missing; BUYER_PRIVATE_KEY is needed to sign in." >&2; exit 1; }
set -a; . ./.env; set +a
: "${BUYER_PRIVATE_KEY:?turnstyl test_mcp: BUYER_PRIVATE_KEY is not set in .env}"

PORT=8794
BASE="http://127.0.0.1:$PORT"
DB=./data/mcp_test_$$.db
PY=.venv/bin/python

[ -x .venv/bin/turnstyl-mcp ] || {
  echo "turnstyl test_mcp: .venv/bin/turnstyl-mcp is missing. Install it with:" >&2
  echo "  uv pip install -e ." >&2
  exit 1
}

PID=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null | head -1)
[ -n "$PID" ] && kill "$PID" && sleep 1
rm -f "$DB" "$DB-wal" "$DB-shm"

TURNSTYL_DB="$DB" PAYMENTS=fake MOCK_LLM=1 \
  .venv/bin/turnstyl serve --with-worker --port $PORT --interval 1 --db "$DB" \
  > /tmp/turnstyl-mcp-serve.log 2>&1 &
SERVER=$!
cleanup(){ kill $SERVER 2>/dev/null; rm -f "$DB" "$DB-wal" "$DB-shm"; }
trap cleanup EXIT

for i in $(seq 1 30); do curl -s -o /dev/null "$BASE/api/status" && break; sleep 0.5; done
if ! curl -s -o /dev/null "$BASE/api/status"; then
  echo "turnstyl test_mcp: the test agent never came up on $BASE." >&2
  echo "  server log: /tmp/turnstyl-mcp-serve.log" >&2
  tail -5 /tmp/turnstyl-mcp-serve.log >&2
  exit 1
fi

$PY scripts/mcp_probe.py "$BASE"
RESULT=$?
[ $RESULT -ne 0 ] && echo "  (test agent log: /tmp/turnstyl-mcp-serve.log)" >&2
exit $RESULT
