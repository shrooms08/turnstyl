#!/usr/bin/env bash
# turnstyl API + worker test: the curl sequence from the day-5 brief, with
# PASS/FAIL per check. Serves ./data/ui.db with the worker on a private port,
# so a demo server on 8787 is left alone.
#
#   scripts/test_api.sh
set -uo pipefail
cd "$(dirname "$0")/.."

PORT=8791
BASE="http://127.0.0.1:$PORT"
DB=./data/ui.db
BUYER=0x0964dc1e37aca77c6df395db7c0eec848b1ceff8
INTERVAL=2
export TURNSTYL_DB="$DB" PAYMENTS=fake MOCK_LLM=1 NO_COLOR=1
PY=.venv/bin/python
FAILS=0

ok(){ echo "  PASS $1"; }
bad(){ echo "  FAIL $1"; [ -n "${2:-}" ] && echo "       $2"; FAILS=$((FAILS+1)); }
jget(){ curl -s "$BASE$1"; }
jq_(){ $PY -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }
wait_step(){ # wait_step <job> <step> <status> : poll up to 2 intervals + slack
  local job="$1" step="$2" want="$3" i
  for i in $(seq 1 12); do
    got=$(jget "/api/jobs/$job" | jq_ "[s['status'] for s in d['steps'] if s['step']==$step][0]")
    [ "$got" = "$want" ] && return 0
    sleep 1
  done
  return 1
}

PID=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null | head -1); [ -n "$PID" ] && kill "$PID" && sleep 1
rm -f "$DB" "$DB-wal" "$DB-shm"
# the worker never creates a database; give it one to watch
.venv/bin/turnstyl status >/dev/null 2>&1

.venv/bin/turnstyl serve --with-worker --port $PORT --interval $INTERVAL --db "$DB" > /tmp/turnstyl-test-serve.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
for i in $(seq 1 20); do curl -s -o /dev/null "$BASE/api/status" && break; sleep 0.5; done

echo "turnstyl API + worker test against $BASE (db $DB, interval ${INTERVAL}s)"

# ---------------------------------------------------------------- POST
SRC=$($PY -c "import json;print(json.dumps(open('examples/Vault.sol').read()))")
RESP=$(curl -s -w '\n%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' \
  -d "{\"buyer\":\"$BUYER\",\"source\":$SRC,\"filename\":\"Vault.sol\"}")
CODE=$(echo "$RESP" | tail -1); BODY=$(echo "$RESP" | sed '$d')
[ "$CODE" = "200" ] && ok "POST /api/jobs returns 200" || bad "POST /api/jobs returns 200" "got $CODE: $BODY"
JOB=$(echo "$BODY" | jq_ "d['job_id']")
[ -n "$JOB" ] && ok "job id issued: $JOB" || bad "job id issued"
[ "$(echo "$BODY" | jq_ "[s['status'] for s in d['steps'] if s['step']==1][0]")" = "done" ] && ok "step 1 done on creation" || bad "step 1 done on creation"
[ "$(echo "$BODY" | jq_ "d['open_invoice']['step'], d['open_invoice']['amount_usdc']")" = "2 0.5" ] && ok "invoice for step 2 at 0.50" || bad "invoice for step 2 at 0.50"
[ "$(echo "$BODY" | jq_ "d['resumed']")" = "False" ] && ok "resumed is false on a new job" || bad "resumed is false on a new job"
grep -qiE "private_key|0x[0-9a-f]{64}" <<< "$BODY" && bad "no key material in the response" || ok "no key material in the response"

# a second identical POST resumes rather than duplicates
RESP2=$(curl -s -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
[ "$(echo "$RESP2" | jq_ "d['resumed'], d['job_id']")" = "True $JOB" ] && ok "same buyer + contract resumes the open job" || bad "same buyer + contract resumes the open job" "$(echo "$RESP2" | head -c 200)"

# ---------------------------------------------------------------- worker runs paid steps
.venv/bin/turnstyl pay "$JOB" 2 >/dev/null 2>&1
wait_step "$JOB" 2 done && ok "worker ran step 2 after payment, no manual job run" || bad "worker ran step 2 after payment"
[ "$(jget "/api/jobs/$JOB" | jq_ "d['open_invoice']['step']")" = "3" ] && ok "worker issued the step 3 invoice" || bad "worker issued the step 3 invoice"

PR=$(curl -s -w '\n%{http_code}' -X POST "$BASE/api/jobs/$JOB/pay"); PC=$(echo "$PR" | tail -1); PB=$(echo "$PR" | sed '$d')
[ "$PC" = "200" ] && [ "$(echo "$PB" | jq_ "d['paid_step'], d['simulated']")" = "3 True" ] && ok "POST /api/jobs/{id}/pay simulates step 3 on the fake backend" || bad "POST /api/jobs/{id}/pay simulates step 3" "got $PC: $(echo "$PB" | head -c 160)"
wait_step "$JOB" 3 done && ok "worker ran step 3 after the simulated payment" || bad "worker ran step 3 after the simulated payment"
[ "$(jget "/api/buyers/$BUYER" | jq_ "d['trust']['trust_tier']")" = "trusted" ] && ok "buyer trusted after two paid steps" || bad "buyer trusted after two paid steps"

# step 4: invoiced, unpaid, buyer trusted -> the worker runs it on credit
wait_step "$JOB" 4 done && ok "worker ran step 4 on credit without payment" || bad "worker ran step 4 on credit without payment"
D=$(jget "/api/jobs/$JOB")
[ "$(echo "$D" | jq_ "d['status']")" = "complete" ] && ok "job complete" || bad "job complete"
[ "$(echo "$D" | jq_ "[s['pay_tx'] for s in d['steps'] if s['step']==4][0]")" = "None" ] && ok "step 4 has no pay tx (credit)" || bad "step 4 has no pay tx (credit)"
[ "$(jget "/api/journal?job=$JOB&limit=50" | jq_ "any(e['decision']=='RUN_ON_CREDIT' and e['step']==4 for e in d['events'])")" = "True" ] && ok "journal records RUN_ON_CREDIT for step 4" || bad "journal records RUN_ON_CREDIT for step 4"

# ---------------------------------------------------------------- outstanding on a closed job
B=$(jget "/api/buyers/$BUYER")
[ "$(echo "$B" | jq_ "len(d['outstanding']), d['outstanding'][0]['job_id'], d['outstanding'][0]['step']")" = "1 $JOB 4" ] && ok "ledger carries the credit step as outstanding" || bad "ledger carries the credit step as outstanding" "$(echo "$B" | jq_ "d['outstanding']")"
API_MEMO=$(echo "$B" | jq_ "d['outstanding'][0]['memo']")
PY_MEMO=$($PY -c "import sys;sys.path.insert(0,'src');from turnstyl.payments import memo_bytes32,hex0x;print(hex0x(memo_bytes32('$JOB',4)))")
[ -n "$API_MEMO" ] && [ "$API_MEMO" = "$PY_MEMO" ] && ok "outstanding memo equals payments.memo_bytes32($JOB, 4): $API_MEMO" || bad "outstanding memo equals payments.memo_bytes32" "api=$API_MEMO py=$PY_MEMO"
[ "$(echo "$B" | jq_ "d['ledger']['unpaid_from_prior_jobs']")" = "1" ] && ok "unpaid_from_prior_jobs is 1 before settling" || bad "unpaid_from_prior_jobs is 1 before settling"
SR=$(curl -s -w '\n%{http_code}' -X POST "$BASE/api/buyers/$BUYER/settle/$JOB/4"); SC=$(echo "$SR" | tail -1); SB=$(echo "$SR" | sed '$d')
[ "$SC" = "200" ] && [ "$(echo "$SB" | jq_ "d['settled']['step'], d['settled']['simulated']")" = "4 True" ] && ok "POST /api/buyers/{addr}/settle/{job}/{step} settles it (fake backend; reconciled by $(echo "$SB" | jq_ "d['reconciled_by']"))" || bad "settle endpoint" "got $SC: $(echo "$SB" | head -c 200)"
B=$(jget "/api/buyers/$BUYER")
[ "$(echo "$B" | jq_ "d['ledger']['unpaid_from_prior_jobs'], len(d['outstanding'])")" = "0 0" ] && ok "after settling: unpaid_from_prior_jobs 0, outstanding empty" || bad "after settling: unpaid_from_prior_jobs 0, outstanding empty" "$(echo "$B" | jq_ "d['ledger']['unpaid_from_prior_jobs'], d['outstanding']")"
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/buyers/$BUYER/settle/$JOB/4")
[ "$C" = "404" ] && ok "settling it again -> 404 (nothing outstanding)" || bad "settling it again -> 404" "got $C"

# ---------------------------------------------------------------- quiet when idle
# a waiting job must not grow the journal each pass: open one and leave it unpaid
RESP3=$(curl -s -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
JOB2=$(echo "$RESP3" | jq_ "d['job_id']")
sleep $((INTERVAL * 2 + 1))
N1=$(jget "/api/journal?limit=500" | jq_ "d['count']")
sleep 10
N2=$(jget "/api/journal?limit=500" | jq_ "d['count']")
[ "$N1" = "$N2" ] && ok "journal did not grow while nothing changed ($N1 -> $N2 over 10s)" || bad "journal did not grow while nothing changed" "$N1 -> $N2"

# ---------------------------------------------------------------- status for the page
[ "$(jget "/api/status" | jq_ "sorted(e['name'] for e in d['receipts_abi'])")" = "['Paid', 'pay']" ] && ok "status carries the receipts ABI (pay, Paid)" || bad "status carries the receipts ABI"
[ "$(jget "/api/status" | jq_ "sorted(e['name'] for e in d['usdc_abi'])")" = "['allowance', 'approve', 'balanceOf', 'decimals']" ] && ok "status carries the USDC ABI" || bad "status carries the USDC ABI"
[ "$(jget "/api/status" | jq_ "'usdc_address' in d")" = "True" ] && ok "status carries usdc_address" || bad "status carries usdc_address"
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs/$JOB/pay")
[ "$C" = "400" ] && ok "simulate on a job with no open invoice -> 400" || bad "simulate on a job with no open invoice -> 400" "got $C"

# ---------------------------------------------------------------- simulate is fake-only
PORT2=8792
PAYMENTS=base .venv/bin/turnstyl serve --port $PORT2 --db "$DB" > /tmp/turnstyl-test-serve-base.log 2>&1 &
SERVER2=$!; disown $SERVER2 2>/dev/null   # so the shell does not report the kill below
for i in $(seq 1 20); do curl -s -o /dev/null "http://127.0.0.1:$PORT2/api/status" && break; sleep 0.5; done
R2=$(curl -s -w '\n%{http_code}' -X POST "http://127.0.0.1:$PORT2/api/jobs/$JOB2/pay"); C2=$(echo "$R2" | tail -1); B2=$(echo "$R2" | sed '$d')
R3=$(curl -s -w '\n%{http_code}' -X POST "http://127.0.0.1:$PORT2/api/buyers/$BUYER/settle/$JOB/4"); C3=$(echo "$R3" | tail -1); B3=$(echo "$R3" | sed '$d')
kill $SERVER2 2>/dev/null
[ "$C2" = "404" ] && grep -q "payments are on chain; use the Pay button" <<< "$B2" && ok "PAYMENTS=base: simulate -> 404 with the on-chain detail" || bad "PAYMENTS=base: simulate -> 404" "got $C2: $(echo "$B2" | head -c 120)"
[ "$C3" = "404" ] && grep -q "payments are on chain; use the Pay button" <<< "$B3" && ok "PAYMENTS=base: settle -> 404 with the on-chain detail" || bad "PAYMENTS=base: settle -> 404" "got $C3: $(echo "$B3" | head -c 120)"

# ---------------------------------------------------------------- validation
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"0xnotanaddress\",\"source\":$SRC}")
[ "$C" = "400" ] && ok "bad address -> 400" || bad "bad address -> 400" "got $C"
BIG=$($PY -c "import json;print(json.dumps('pragma solidity ^0.8.20; contract Big {}' + 'x'*102400))")
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$BIG}")
[ "$C" = "400" ] && ok "100 KB source -> 400" || bad "100 KB source -> 400" "got $C"
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":\"hello world\"}")
[ "$C" = "400" ] && ok "non-Solidity source -> 400" || bad "non-Solidity source -> 400" "got $C"

# ---------------------------------------------------------------- buyer filter
OTHER=0x000000000000000000000000000000000000dead
[ "$(jget "/api/jobs?buyer=$BUYER" | jq_ "len(d['jobs'])")" = "2" ] && ok "GET /api/jobs?buyer= lists this buyer's 2 jobs" || bad "GET /api/jobs?buyer= lists this buyer's 2 jobs" "$(jget "/api/jobs?buyer=$BUYER" | jq_ "len(d['jobs'])")"
[ "$(jget "/api/jobs?buyer=$(echo $BUYER | tr a-f A-F)" | jq_ "len(d['jobs'])")" = "2" ] && ok "buyer filter is case-insensitive" || bad "buyer filter is case-insensitive"
[ "$(jget "/api/jobs?buyer=$OTHER" | jq_ "len(d['jobs'])")" = "0" ] && ok "buyer filter excludes other buyers" || bad "buyer filter excludes other buyers"
[ "$(jget "/api/jobs" | jq_ "len(d['jobs'])")" = "2" ] && ok "GET /api/jobs without the param lists all" || bad "GET /api/jobs without the param lists all"

# ---------------------------------------------------------------- missing memory
rm -f "$DB" "$DB-wal" "$DB-shm"
sleep 1
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
[ "$C" = "409" ] && ok "POST against a missing db -> 409" || bad "POST against a missing db -> 409" "got $C"
[ "$(jget "/api/status" | jq_ "d['memory_missing']")" = "True" ] && ok "GET /api/status reports memory_missing" || bad "GET /api/status reports memory_missing"
sleep $((INTERVAL * 2 + 1))
[ ! -f "$DB" ] && ok "worker did not recreate the deleted database" || bad "worker did not recreate the deleted database"

echo
if [ "$FAILS" -eq 0 ]; then echo "RESULT: PASS - API and worker behave"; exit 0; fi
echo "RESULT: FAIL - $FAILS check(s) failed (server log: /tmp/turnstyl-test-serve.log)"; exit 1
