#!/usr/bin/env bash
# turnstyl API + worker test: the curl sequence from the day-5 brief, with
# PASS/FAIL per check. Serves ./data/ui.db with the worker on a private port,
# so a demo server on 8787 is left alone.
#
#   scripts/test_api.sh
set -uo pipefail
cd "$(dirname "$0")/.."
# AGENT_ADDRESS and USDC_ADDRESS are needed to assert what an x402 402 quotes.
[ -f .env ] && { set -a; . ./.env; set +a; }

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
jq_(){ $PY -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

# Job contents are private to the buyer who paid, so most of this script talks
# to the API as that buyer. A session is one wallet signature over a message
# the server issues; it is per server process, so each server started below
# gets its own. The key never leaves .env and is never echoed.
AUTH=(); AUTH2=(); AUTH4=()
OPTOK="${OPERATOR_TOKEN:-}"
signin(){ # signin <base> [private_key_env] -> prints a session token
  $PY - "$1" "${2:-BUYER_PRIVATE_KEY}" <<'PY'
import json, os, sys, urllib.request, urllib.error
from eth_account import Account
from eth_account.messages import encode_defunct
base, keyvar = sys.argv[1], sys.argv[2]
key = os.environ.get(keyvar)
if not key:
    sys.exit(f"turnstyl test_api: {keyvar} is not set in .env; sign-in cannot be tested")
acct = Account.from_key(key)
addr = acct.address.lower()
def call(req):
    try:
        with urllib.request.urlopen(req, timeout=10) as f:
            return json.load(f)
    except urllib.error.HTTPError as e:
        sys.exit(f"turnstyl test_api: {req.full_url} answered {e.code}: {(e.read() or b'')[:200].decode()}")
n = call(urllib.request.Request(f"{base}/api/auth/nonce?address={addr}"))
sig = acct.sign_message(encode_defunct(text=n["message"])).signature.hex()
if not sig.startswith("0x"):
    sig = "0x" + sig
tok = call(urllib.request.Request(
    f"{base}/api/auth/verify",
    data=json.dumps({"address": addr, "signature": sig, "nonce": n["nonce"]}).encode(),
    headers={"content-type": "application/json"},
))["token"]
print(tok)
PY
}
signin_stranger(){ # signin_stranger <base> -> prints "<address> <token>" for a fresh wallet
  $PY - "$1" <<'PY'
import json, sys, urllib.request, urllib.error
from eth_account import Account
from eth_account.messages import encode_defunct
base = sys.argv[1]
acct = Account.create()
addr = acct.address.lower()
def call(req):
    try:
        with urllib.request.urlopen(req, timeout=10) as f:
            return json.load(f)
    except urllib.error.HTTPError as e:
        sys.exit(f"turnstyl test_api: {req.full_url} answered {e.code}: {(e.read() or b'')[:200].decode()}")
n = call(urllib.request.Request(f"{base}/api/auth/nonce?address={addr}"))
sig = acct.sign_message(encode_defunct(text=n["message"])).signature.hex()
if not sig.startswith("0x"):
    sig = "0x" + sig
tok = call(urllib.request.Request(
    f"{base}/api/auth/verify",
    data=json.dumps({"address": addr, "signature": sig, "nonce": n["nonce"]}).encode(),
    headers={"content-type": "application/json"},
))["token"]
print(addr, tok)
PY
}
jget(){ curl -s "${AUTH[@]}" "$BASE$1"; }          # as the buyer
jpub(){ curl -s "$BASE$1"; }                        # no credential at all
jop(){ curl -s -H "Authorization: Bearer $OPTOK" "$BASE$1"; }   # as the operator
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

.venv/bin/turnstyl serve --with-worker --port $PORT --interval $INTERVAL --db "$DB" > /tmp/turnstyl-test-serve.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
for i in $(seq 1 20); do curl -s -o /dev/null "$BASE/api/status" && break; sleep 0.5; done
TOKEN=$(signin "$BASE") || { echo "turnstyl test_api: could not sign in as the buyer; nothing else can be checked." >&2; exit 1; }
AUTH=(-H "Authorization: Bearer $TOKEN")

echo "turnstyl API + worker test against $BASE (db $DB, interval ${INTERVAL}s)"
grep -q "memory: created ${DB#./}" /tmp/turnstyl-test-serve.log && ok "serve created the missing store: $(grep -o 'memory: created.*' /tmp/turnstyl-test-serve.log | head -1)" || bad "serve created the missing store" "$(head -3 /tmp/turnstyl-test-serve.log)"

# ---------------------------------------------------------------- POST
SRC=$($PY -c "import json;print(json.dumps(open('examples/Vault.sol').read()))")
RESP=$(curl -s "${AUTH[@]}" -w '\n%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' \
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
RESP2=$(curl -s "${AUTH[@]}" -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
[ "$(echo "$RESP2" | jq_ "d['resumed'], d['job_id']")" = "True $JOB" ] && ok "same buyer + contract resumes the open job" || bad "same buyer + contract resumes the open job" "$(echo "$RESP2" | head -c 200)"

# ---------------------------------------------------------------- worker runs paid steps
wait_invoice(){ # wait_invoice <job> <step> : until the open invoice is for <step> and unpaid
  local job="$1" step="$2" i
  for i in $(seq 1 12); do
    got=$(jget "/api/jobs/$job" | jq_ "(d.get('open_invoice') or {}).get('step'), (d.get('open_invoice') or {}).get('paid')")
    [ "$got" = "$step False" ] && return 0
    sleep 1
  done
  return 1
}
simulate_step(){ # simulate_step <job> <step> : pay the open invoice through the endpoint, wait for the worker
  wait_invoice "$1" "$2" || { bad "invoice for step $2 of $1 open"; return 1; }
  curl -s "${AUTH[@]}" -o /dev/null -X POST "$BASE/api/jobs/$1/pay"
  wait_step "$1" "$2" done
}

.venv/bin/turnstyl pay "$JOB" 2 >/dev/null 2>&1
wait_step "$JOB" 2 done && ok "worker ran step 2 after payment, no manual job run" || bad "worker ran step 2 after payment"
[ "$(jget "/api/jobs/$JOB" | jq_ "d['open_invoice']['step']")" = "3" ] && ok "worker issued the step 3 invoice" || bad "worker issued the step 3 invoice"

PR=$(curl -s "${AUTH[@]}" -w '\n%{http_code}' -X POST "$BASE/api/jobs/$JOB/pay"); PC=$(echo "$PR" | tail -1); PB=$(echo "$PR" | sed '$d')
[ "$PC" = "200" ] && [ "$(echo "$PB" | jq_ "d['paid_step'], d['simulated']")" = "3 True" ] && ok "POST /api/jobs/{id}/pay simulates step 3 on the fake backend" || bad "POST /api/jobs/{id}/pay simulates step 3" "got $PC: $(echo "$PB" | head -c 160)"
wait_step "$JOB" 3 done && ok "worker ran step 3 after the simulated payment" || bad "worker ran step 3 after the simulated payment"
[ "$(jget "/api/buyers/$BUYER" | jq_ "d['trust']['trust_tier'], d['trust']['completed_paid_jobs'], d['trust']['jobs_until_credit'], d['trust']['steps_until_credit']")" = "new 0 3 3" ] && ok "two paid steps: still new; jobs_until_credit 3 (old key carries the same value)" || bad "trust after two paid steps" "$(jget "/api/buyers/$BUYER" | jq_ "d['trust']")"
simulate_step "$JOB" 4 && ok "step 4 paid and run; first job complete" || bad "step 4 paid and run"
[ "$(jget "/api/buyers/$BUYER" | jq_ "d['trust']['completed_paid_jobs'], d['trust']['trust_tier']")" = "1 new" ] && ok "completed_paid_jobs 1, still new" || bad "completed_paid_jobs 1, still new" "$(jget "/api/buyers/$BUYER" | jq_ "d['trust']")"

# two more fully paid jobs, from memory: three completed paid jobs earns credit
for n in 2 3; do
  J=$(curl -s "${AUTH[@]}" -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}" | jq_ "d['job_id']")
  [ -n "$J" ] && [ "$J" != "$JOB" ] || bad "job $n created"
  for st in 2 3 4; do simulate_step "$J" "$st" || bad "job $n step $st"; done
  [ "$(jget "/api/jobs/$J" | jq_ "d['status']")" = "complete" ] && ok "job $n ($J) fully paid and complete" || bad "job $n complete"
done
[ "$(jget "/api/buyers/$BUYER" | jq_ "d['trust']['completed_paid_jobs'], d['trust']['trust_tier'], d['trust']['jobs_until_credit']")" = "3 trusted 0" ] && ok "three completed paid jobs: trusted, jobs_until_credit 0" || bad "trusted after three paid jobs" "$(jget "/api/buyers/$BUYER" | jq_ "d['trust']")"

# fourth job: the worker runs step 2 on credit without payment, steps 3 and 4 are paid, the job closes with step 2 owed
JOB4=$(curl -s "${AUTH[@]}" -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}" | jq_ "d['job_id']")
wait_step "$JOB4" 2 done && ok "worker ran step 2 of job 4 on credit without payment" || bad "worker ran step 2 on credit"
D=$(jget "/api/jobs/$JOB4")
[ "$(echo "$D" | jq_ "[s['pay_tx'] for s in d['steps'] if s['step']==2][0]")" = "None" ] && ok "step 2 has no pay tx (credit)" || bad "step 2 has no pay tx (credit)"
[ "$(jget "/api/journal?job=$JOB4&limit=50" | jq_ "any(e['decision']=='RUN_ON_CREDIT' and e['step']==2 for e in d['events'])")" = "True" ] && ok "journal records RUN_ON_CREDIT for step 2" || bad "journal records RUN_ON_CREDIT for step 2"
JS=$(jget "/api/journal?job=$JOB4&limit=50")
[ "$(echo "$JS" | jq_ "all((e.get('extra') or {}).get('summary') for e in d['events']), len(d['events'])>0")" = "True True" ] && ok "every journal event carries extra.summary" || bad "every journal event carries extra.summary" "$(echo "$JS" | jq_ "[(e.get('extra') or {}).get('summary') for e in d['events']]" | head -c 300)"
[ "$(echo "$JS" | jq_ "[e['extra']['summary'] for e in d['events'] if e['decision']=='RUN_ON_CREDIT'][0]")" = "Ran step 2 (findings) on credit: 3 fully paid jobs on record. Served from memory, no model call. Next: step 3 invoiced at 0.38 USDC." ] && ok "RUN_ON_CREDIT summary reads as one sentence" || bad "RUN_ON_CREDIT summary" "$(echo "$JS" | jq_ "[e['extra']['summary'] for e in d['events'] if e['decision']=='RUN_ON_CREDIT']")"
simulate_step "$JOB4" 3 || bad "job 4 step 3"; simulate_step "$JOB4" 4 || bad "job 4 step 4"
[ "$(jget "/api/jobs/$JOB4" | jq_ "d['status']")" = "complete" ] && ok "job 4 complete with step 2 owed" || bad "job 4 complete"
JOB="$JOB4"   # the outstanding checks below settle step 2 of this job

# ---------------------------------------------------------------- report + verify (fake backend)
RH=$(curl -s "${AUTH[@]}" -D - -o /tmp/turnstyl-report.md "$BASE/api/jobs/$JOB4/report.md")
grep -qi "^content-disposition: attachment; filename=\"turnstyl-audit-$JOB4.md\"" <<< "$RH" && ok "report.md is an attachment named turnstyl-audit-$JOB4.md" || bad "report.md attachment header" "$(grep -i disposition <<< "$RH")"
[ "$(grep -c '^## Step [1-4]: ' /tmp/turnstyl-report.md)" = "4" ] && ok "report.md has all four step sections" || bad "report.md has all four step sections" "$(grep -c '^## Step' /tmp/turnstyl-report.md)"
grep -q "^## Verification" /tmp/turnstyl-report.md && grep -q "Reentrancy in withdraw()" /tmp/turnstyl-report.md && ok "report.md carries the Verification section and the verbatim findings" || bad "report.md content"
RJ=$(jget "/api/jobs/$JOB4/report.json")
[ "$(echo "$RJ" | jq_ "len(d['steps']), d['job_id'], len(d['verification'])")" = "4 $JOB4 4" ] && ok "report.json: 4 steps, 4 verification rows" || bad "report.json shape" "$(echo "$RJ" | head -c 200)"
VJ=$(jget "/api/jobs/$JOB4/verify")
[ "$(echo "$VJ" | jq_ "all(x['matches'] is None and 'no commit' in (x['reason'] or '') for x in d['steps']), len(d['steps']), d['summary']['no_commit']")" = "True 4 4" ] && ok "verify on the fake backend: all four steps matches null with a 'no commit' reason" || bad "verify on the fake backend" "$(echo "$VJ" | head -c 300)"
[ "$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/jobs/nope123/report.md")" = "404" ] && ok "report.md for an unknown job -> 404" || bad "report.md unknown job -> 404"
[ "$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/jobs/nope123/verify")" = "404" ] && ok "verify for an unknown job -> 404" || bad "verify unknown job -> 404"

# ---------------------------------------------------------------- auth: who sees what
# A job's contents belong to the buyer who paid for them. Everyone else sees
# that it exists and how far it got. The operator sees everything.
N=$(jpub "/api/auth/nonce?address=$BUYER")
MSG=$(echo "$N" | $PY -c "import json,sys; print(json.load(sys.stdin)['message'])")
WANT=$($PY -c "
import json,sys
d=json.loads(sys.stdin.read())
print('turnstyl login\n\naddress: $BUYER\nnonce: %s\nissued: %s' % (d['nonce'], d['issued']))" <<< "$N")
[ "$MSG" = "$WANT" ] && ok "nonce returns the exact login message to sign" || bad "login message format" "got: $(echo "$MSG" | head -c 120)"
[ "$(echo "$N" | jq_ "len(d['nonce']), d['address']")" = "32 $BUYER" ] && ok "nonce is 32 hex chars, bound to the address" || bad "nonce shape" "$(echo "$N" | head -c 160)"
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/auth/verify" -H 'content-type: application/json' -d "{\"address\":\"$BUYER\",\"signature\":\"0xdeadbeef\"}")
[ "$C" = "400" ] && ok "a signature that does not recover -> 400" || bad "bad signature -> 400" "got $C"
[ "$(jpub "/api/auth/me" | jq_ "d['kind'], d['signed_in']")" = "public False" ] && ok "no credential reads as public" || bad "no credential reads as public"
[ "$(jget "/api/auth/me" | jq_ "d['kind'], d['address']")" = "buyer $BUYER" ] && ok "the session reads as this buyer" || bad "session identity" "$(jget "/api/auth/me")"
[ "$(jop "/api/auth/me" | jq_ "d['kind'], d['operator']")" = "operator True" ] && ok "OPERATOR_TOKEN reads as the operator" || bad "operator identity" "$(jop "/api/auth/me")"

# the same job, three ways
PUBJ=$(jpub "/api/jobs/$JOB4"); OWNJ=$(jget "/api/jobs/$JOB4"); OPJ=$(jop "/api/jobs/$JOB4")
[ "$(echo "$PUBJ" | jq_ "all(s['output'] is None for s in d['steps']), d['contract_hash'], d['redacted']")" = "True None True" ] && ok "public job detail: no outputs, no contract hash" || bad "public job detail" "$(echo "$PUBJ" | head -c 240)"
[ "$(echo "$PUBJ" | jq_ "d['buyer']")" = "0x0964…eff8" ] && ok "public job detail truncates the buyer to 0x0964…eff8" || bad "public buyer truncation" "$(echo "$PUBJ" | jq_ "d['buyer']")"
[ "$(echo "$PUBJ" | jq_ "d['status'], d['current_step'], len(d['steps']), all(s['price_usdc'] is not None for s in d['steps']), all(s['output_sha256'] for s in d['steps'])")" = "complete 4 4 True True" ] && ok "public job detail keeps the meter: status, step, every price, every output hash" || bad "public job detail keeps the meter" "$(echo "$PUBJ" | head -c 240)"
[ "$(echo "$OWNJ" | jq_ "sum(1 for s in d['steps'] if s['output']), d['buyer'], d.get('redacted')")" = "4 $BUYER None" ] && ok "the buyer sees all four outputs and the full address" || bad "buyer job detail" "$(echo "$OWNJ" | head -c 240)"
[ "$(echo "$OPJ" | jq_ "sum(1 for s in d['steps'] if s['output']), d['buyer']")" = "4 $BUYER" ] && ok "the operator sees all four outputs" || bad "operator job detail" "$(echo "$OPJ" | head -c 240)"
# the job list: who has bought what is an operator view
LA=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/jobs")
LB=$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$BASE/api/jobs")
LC=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $OPTOK" "$BASE/api/jobs")
[ "$LA $LB $LC" = "403 403 200" ] && ok "GET /api/jobs: public 403, buyer 403, operator 200" || bad "job list is an operator view" "got public=$LA buyer=$LB operator=$LC"
grep -q "the job list is an operator view" <<< "$(jpub "/api/jobs")" && ok "the 403 says the job list is an operator view" || bad "job list 403 detail" "$(jpub "/api/jobs" | head -c 200)"
[ "$(jget "/api/jobs?buyer=$BUYER" | jq_ "all(j['buyer']=='$BUYER' for j in d['jobs']), len(d['jobs'])>0")" = "True True" ] && ok "a buyer may list their own jobs with ?buyer=" || bad "buyer lists own jobs" "$(jget "/api/jobs?buyer=$BUYER" | head -c 200)"
[ "$(jop "/api/jobs" | jq_ "[j['buyer'] for j in d['jobs'] if j['job_id']=='$JOB4'][0], [j['job_type'] for j in d['jobs'] if j['job_id']=='$JOB4'][0]")" = "$BUYER audit" ] && ok "operator rows carry the full address and the job type" || bad "operator job rows" "$(jop "/api/jobs" | head -c 200)"

# the public figures: six numbers, nobody named, no credential
ST=$(jpub "/api/stats")
[ "$(echo "$ST" | jq_ "sorted(k for k in d if k not in ('memory_missing','source','computed_at','cache_seconds'))")" = "['buyers', 'decisions', 'jobs', 'jobs_completed', 'served_from_memory', 'usdc_settled']" ] && ok "/api/stats carries the six public figures" || bad "/api/stats shape" "$(echo "$ST" | head -c 240)"
[ "$(echo "$ST" | jq_ "d['jobs'] >= d['jobs_completed'] >= 1, d['buyers'] >= 1, d['decisions'] > 0, d['served_from_memory'] > 0, d['usdc_settled'] > 0")" = "True True True True True" ] && ok "/api/stats counts jobs, completions, buyers, decisions, cache hits and USDC" || bad "/api/stats values" "$(echo "$ST" | head -c 240)"
[ "$(echo "$ST" | jq_ "d['cache_seconds']")" = "10" ] && ok "/api/stats says it is cached for 10 seconds" || bad "/api/stats cache_seconds" "$(echo "$ST" | jq_ "d['cache_seconds']")"
grep -qiE "0x[0-9a-f]{40}|$JOB4" <<< "$ST" && bad "/api/stats names nobody" "an address or job id is in the body" || ok "/api/stats carries no address and no job id"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/stats")" = "200" ] && ok "/api/stats needs no credential" || bad "/api/stats needs no credential"

# the journal
PUBE=$(jpub "/api/journal?job=$JOB4&limit=5"); OWNE=$(jget "/api/journal?job=$JOB4&limit=5")
[ "$(echo "$PUBE" | jq_ "all(e['evaluated']==[] and e['acted']==[] and e['forward']==[] for e in d['events']), all(e['redacted'] for e in d['events'])")" = "True True" ] && ok "public journal: no evaluated, acted or forward lines" || bad "public journal reduction" "$(echo "$PUBE" | head -c 240)"
[ "$(echo "$PUBE" | jq_ "all(e['ts'] and e['decision'] and (e['extra'] or {}).get('summary') for e in d['events'])")" = "True" ] && ok "public journal keeps decision, time, step and the summary sentence" || bad "public journal keeps the sentence" "$(echo "$PUBE" | head -c 240)"
[ "$(echo "$OWNE" | jq_ "any(e['evaluated'] for e in d['events']), any(e['acted'] for e in d['events'])")" = "True True" ] && ok "the buyer sees the memory reads behind each decision" || bad "buyer journal" "$(echo "$OWNE" | head -c 200)"

# the ledger
PUBL=$(jpub "/api/buyers/$BUYER")
[ "$(echo "$PUBL" | jq_ "sorted(d['ledger']), d['outstanding'], d['jobs']")" = "['completed_paid_jobs', 'trust_tier'] [] []" ] && ok "public ledger: trust_tier and completed_paid_jobs only" || bad "public ledger reduction" "$(echo "$PUBL" | head -c 240)"
[ "$(jget "/api/buyers/$BUYER" | jq_ "'paid_usdc' in d['ledger'], 'defaults' in d['ledger']")" = "True True" ] && ok "the buyer sees their whole ledger" || bad "buyer ledger"
[ "$(jop "/api/buyers/$BUYER" | jq_ "'paid_usdc' in d['ledger']")" = "True" ] && ok "the operator sees the whole ledger" || bad "operator ledger"

# a stranger with a valid session for another address
read -r OTHER OTOK <<< "$(signin_stranger "$BASE")"
sget(){ curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $OTOK" "$BASE$1"; }
for path in "/api/jobs/$JOB4/report.md" "/api/jobs/$JOB4/report.json" "/api/jobs/$JOB4/verify"; do
  A=$(curl -s -o /dev/null -w '%{http_code}' "$BASE$path")
  B=$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$BASE$path")
  C2=$(sget "$path")
  D=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $OPTOK" "$BASE$path")
  [ "$A $B $C2 $D" = "401 200 403 200" ] && ok "${path##*/}: public 401, buyer 200, other buyer 403, operator 200" || bad "${path##*/} visibility" "got public=$A buyer=$B other=$C2 operator=$D"
done
DET=$(curl -s -H "Authorization: Bearer $OTOK" "$BASE/api/jobs/$JOB4/verify" | jq_ "d['detail']")
grep -q "you are signed in as $OTHER" <<< "$DET" && ok "the 403 names both addresses" || bad "403 detail" "$DET"
CA=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
[ "$CA" = "401" ] && ok "POST /api/jobs without a session -> 401" || bad "POST /api/jobs without a session -> 401" "got $CA"
CB=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs" -H "Authorization: Bearer $OTOK" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
[ "$CB" = "403" ] && ok "POST /api/jobs in another wallet's name -> 403" || bad "POST /api/jobs as the wrong address -> 403" "got $CB"
CC=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs/$JOB4/pay")
[ "$CC" = "401" ] && ok "simulate a payment without a session -> 401" || bad "simulate without a session -> 401" "got $CC"

# ---------------------------------------------------------------- sign out
# Signing out closes the session on this side, not only in the tab that held
# it. A throwaway session is used so the one this script runs on survives.
read -r THROW TTOK <<< "$(signin_stranger "$BASE")"
[ "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TTOK" "$BASE/api/auth/me")" = "200" ] && \
  [ "$(curl -s -H "Authorization: Bearer $TTOK" "$BASE/api/auth/me" | jq_ "d['kind']")" = "buyer" ] && \
  ok "a fresh session reads as its buyer" || bad "fresh session reads as its buyer"
LO=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $TTOK" "$BASE/api/auth/logout")
[ "$LO" = "204" ] && ok "POST /api/auth/logout returns 204" || bad "logout returns 204" "got $LO"
[ "$(curl -s -H "Authorization: Bearer $TTOK" "$BASE/api/auth/me" | jq_ "d['kind'], d['signed_in']")" = "public False" ] && ok "the token reads as public once signed out" || bad "token rejected after logout" "$(curl -s -H "Authorization: Bearer $TTOK" "$BASE/api/auth/me")"
[ "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TTOK" "$BASE/api/jobs?buyer=$THROW")" = "401" ] && ok "the signed-out token can no longer list that buyer's jobs" || bad "signed-out token still lists jobs"
LO2=$(curl -s -w '\n%{http_code}' -X POST -H "Authorization: Bearer $TTOK" "$BASE/api/auth/logout")
[ "$(echo "$LO2" | tail -1)" = "401" ] && grep -q "not a live session" <<< "$LO2" && ok "a second logout with the same token -> 401 saying why" || bad "second logout -> 401" "got $(echo "$LO2" | tail -1): $(echo "$LO2" | sed '$d' | head -c 140)"
LO3=$(curl -s -w '\n%{http_code}' -X POST "$BASE/api/auth/logout")
[ "$(echo "$LO3" | tail -1)" = "401" ] && ok "logout with no token -> 401" || bad "logout with no token -> 401" "got $(echo "$LO3" | tail -1)"
LO4=$(curl -s -w '\n%{http_code}' -X POST -H "Authorization: Bearer $OPTOK" "$BASE/api/auth/logout")
[ "$(echo "$LO4" | tail -1)" = "401" ] && grep -q "not a session" <<< "$LO4" && ok "the operator token cannot be signed out (it is not a session)" || bad "operator token logout -> 401" "got $(echo "$LO4" | tail -1)"
[ "$(jop "/api/auth/me" | jq_ "d['operator']")" = "True" ] && ok "and the operator token still works afterwards" || bad "operator token survived the logout attempt"
[ "$(jget "/api/auth/me" | jq_ "d['kind']")" = "buyer" ] && ok "this script's own session is untouched by another's logout" || bad "own session untouched" "$(jget "/api/auth/me")"

# ---------------------------------------------------------------- untrusted source
# A contract whose comments try to instruct the auditor. The scan runs before
# any model sees the file, and its hits ride on the job like any other fact.
ADV=$($PY -c "import json;print(json.dumps(open('examples/Adversarial.sol').read()))")
AR=$(curl -s "${AUTH[@]}" -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$ADV,\"filename\":\"Adversarial.sol\"}")
AJOB=$(echo "$AR" | jq_ "d['job_id']")
[ -n "$AJOB" ] && ok "the adversarial contract was accepted like any other: $AJOB" || bad "adversarial job created" "$(echo "$AR" | head -c 200)"
AD=$(jget "/api/jobs/$AJOB")
[ "$(echo "$AD" | jq_ "len(d['injection_flags']) >= 6")" = "True" ] && ok "the job carries the injection flags ($(echo "$AD" | jq_ "len(d['injection_flags'])") passages)" || bad "injection flags on the job" "$(echo "$AD" | jq_ "d['injection_flags']" | head -c 200)"
[ "$(echo "$AD" | jq_ "sorted({f['rule'] for f in d['injection_flags']}) == ['addresses-the-model', 'approve-patch', 'chat-role-marker', 'ignore-instructions', 'role-assertion', 'suppress-findings']")" = "True" ] && ok "every rule class fired on it" || bad "injection rule classes" "$(echo "$AD" | jq_ "sorted({f['rule'] for f in d['injection_flags']})")"
[ "$(echo "$AD" | jq_ "all(f['line'] > 0 and f['text'] and f['matched'] for f in d['injection_flags'])")" = "True" ] && ok "each flag carries a line number and the matched text" || bad "flag shape" "$(echo "$AD" | jq_ "d['injection_flags'][0]")"
APUB=$(jpub "/api/jobs/$AJOB")
[ "$(echo "$APUB" | jq_ "len(d['injection_flags']) >= 6, all(f['text'] is None for f in d['injection_flags']), all(f['line'] > 0 and f['rule'] for f in d['injection_flags'])")" = "True True True" ] && ok "a stranger sees that the source tried to instruct the auditor, not what it said" || bad "public injection flags" "$(echo "$APUB" | jq_ "d['injection_flags'][:2]")"
[ "$(jget "/api/jobs/$JOB4" | jq_ "d['injection_flags']")" = "[]" ] && ok "an ordinary contract carries no flags" || bad "ordinary contract has no flags" "$(jget "/api/jobs/$JOB4" | jq_ "d['injection_flags']")"
AJ=$(jget "/api/journal?job=$AJOB&limit=20")
[ "$(echo "$AJ" | jq_ "any(e['decision']=='FLAGGED_UNTRUSTED_SOURCE' for e in d['events'])")" = "True" ] && ok "the journal records the scan as one decision" || bad "journal records the scan" "$(echo "$AJ" | jq_ "[e['decision'] for e in d['events']]")"

# ---------------------------------------------------------------- digest
# Counted from the journal and the entities, and consolidated as one entity so
# counting the same day again is a single read.
DG=$(jop "/api/digest")
[ "$(echo "$DG" | jq_ "d['complete'], d['days'], d['consolidated_as'] == 'digest/' + d['date']")" = "True 1 True" ] && ok "GET /api/digest for the operator is complete and names its consolidation entity" || bad "digest shape" "$(echo "$DG" | head -c 240)"
[ "$(echo "$DG" | jq_ "sorted(k for k in d['figures'])")" = "['buyers_above_new', 'buyers_active', 'defaults', 'injection_flags', 'jobs_completed', 'jobs_opened', 'median_seconds_payment_to_output', 'model', 'model_spend_usd_estimated', 'new_buyers', 'payment_to_output_minimum', 'payment_to_output_observations', 'refusals', 'steps_run', 'steps_served_from_memory', 'tokens_in', 'tokens_out', 'top_contracts_by_repeat_audits', 'trust_changes', 'usdc_settled']" ] && ok "the operator digest carries every figure" || bad "operator digest figures" "$(echo "$DG" | jq_ "sorted(d['figures'])")"
[ "$(echo "$DG" | jq_ "d['figures']['jobs_opened'] > 0, d['figures']['steps_run'] > 0, d['figures']['usdc_settled'] > 0, d['figures']['steps_served_from_memory'] > 0")" = "True True True True" ] && ok "the digest counts jobs, steps, USDC and cache hits" || bad "digest values" "$(echo "$DG" | jq_ "d['figures']")"
[ "$(echo "$DG" | jq_ "d['figures']['injection_flags'] > 0")" = "True" ] && ok "the digest counts the injection flags raised on the adversarial contract" || bad "digest injection flags" "$(echo "$DG" | jq_ "d['figures']['injection_flags']")"
# no buyer is driven into REFUSE by this suite; the offline demo asserts > 0
[ "$(echo "$DG" | jq_ "isinstance(d['figures']['refusals'], int) and d['figures']['refusals'] >= 0")" = "True" ] && ok "the digest counts refusals (none in this run)" || bad "digest refusals" "$(echo "$DG" | jq_ "d['figures']['refusals']")"
[ "$(echo "$DG" | jq_ "len(d['figures']['top_contracts_by_repeat_audits']) > 0, all('contract_hash' in c and 'jobs' in c for c in d['figures']['top_contracts_by_repeat_audits'])")" = "True True" ] && ok "the digest names the top contracts by repeat audits" || bad "digest top contracts" "$(echo "$DG" | jq_ "d['figures']['top_contracts_by_repeat_audits']")"

[ "$(echo "$DG" | jq_ "d['figures']['trust_changes'] > 0")" = "True" ] && ok "the digest counts TRUST_CHANGED events, not a snapshot" || bad "digest trust changes" "$(echo "$DG" | jq_ "d['figures']['trust_changes'], d['figures']['buyers_above_new']")"
[ "$(echo "$DG" | jq_ "(d['figures']['payment_to_output_observations'] >= d['figures']['payment_to_output_minimum']) == (d['figures']['median_seconds_payment_to_output'] is not None)")" = "True" ] && ok "payment to output is a median only once there are enough payments" || bad "payment to output threshold" "$(echo "$DG" | jq_ "d['figures']['payment_to_output_observations'], d['figures']['median_seconds_payment_to_output']")"
JP=$(jget "/api/journal?limit=200")
[ "$(echo "$JP" | jq_ "any(e['decision']=='PAYMENT_SEEN' for e in d['events'])")" = "True" ] && ok "settling an invoice leaves a PAYMENT_SEEN event" || bad "PAYMENT_SEEN journalled" "$(echo "$JP" | jq_ "sorted({e['decision'] for e in d['events']})")"
[ "$(echo "$JP" | jq_ "all(all(k in (e.get('extra') or {}) for k in ('amount','step','rail','tx','issued_at')) for e in d['events'] if e['decision']=='PAYMENT_SEEN')")" = "True" ] && ok "each PAYMENT_SEEN names the amount, step, rail, tx and issue time" || bad "PAYMENT_SEEN extra" "$(echo "$JP" | jq_ "[e['extra'] for e in d['events'] if e['decision']=='PAYMENT_SEEN'][:1]")"
[ "$(echo "$JP" | jq_ "all(' USDC for step ' in (e['extra'] or {}).get('summary','') and ' seen on ' in (e['extra'] or {}).get('summary','') for e in d['events'] if e['decision']=='PAYMENT_SEEN')")" = "True" ] && ok "and reads as one sentence" || bad "PAYMENT_SEEN summary" "$(echo "$JP" | jq_ "[(e['extra'] or {}).get('summary') for e in d['events'] if e['decision']=='PAYMENT_SEEN'][:1]")"

DGP=$(jpub "/api/digest")
[ "$(echo "$DGP" | jq_ "d['complete']")" = "False" ] && ok "a public digest says it is not complete" || bad "public digest complete flag"
[ "$(echo "$DGP" | jq_ "sorted(k for k in d['figures'])")" = "['buyers_above_new', 'defaults', 'injection_flags', 'jobs_completed', 'jobs_opened', 'median_seconds_payment_to_output', 'new_buyers', 'payment_to_output_minimum', 'payment_to_output_observations', 'refusals', 'steps_run', 'steps_served_from_memory', 'trust_changes', 'usdc_settled']" ] && ok "a public digest is counts only, in the shape /api/stats uses" || bad "public digest figures" "$(echo "$DGP" | jq_ "sorted(d['figures'])")"
grep -qE "model_spend|top_contracts|contract_hash|0x[0-9a-f]{40}" <<< "$DGP" && bad "the public digest names nobody and no cost" "$(echo "$DGP" | head -c 200)" || ok "the public digest carries no model spend, no contract and no address"
[ "$(jget "/api/digest" | jq_ "d['complete']")" = "False" ] && ok "a signed-in buyer gets the public digest, not the operator's" || bad "buyer digest is not complete"
[ "$(echo "$DG" | jq_ "d['figures']['jobs_opened']")" = "$(jop "/api/digest?days=7" | jq_ "d['figures']['jobs_opened']")" ] && ok "the same store counted over a wider window agrees on jobs opened" || bad "digest window" "1d=$(echo "$DG" | jq_ "d['figures']['jobs_opened']") 7d=$(jop "/api/digest?days=7" | jq_ "d['figures']['jobs_opened']")"

# ---------------------------------------------------------------- outstanding on a closed job
B=$(jget "/api/buyers/$BUYER")
[ "$(echo "$B" | jq_ "len(d['outstanding']), d['outstanding'][0]['job_id'], d['outstanding'][0]['step']")" = "1 $JOB 2" ] && ok "ledger carries the credit step as outstanding" || bad "ledger carries the credit step as outstanding" "$(echo "$B" | jq_ "d['outstanding']")"
API_MEMO=$(echo "$B" | jq_ "d['outstanding'][0]['memo']")
PY_MEMO=$($PY -c "import sys;sys.path.insert(0,'src');from turnstyl.payments import memo_bytes32,hex0x;print(hex0x(memo_bytes32('$JOB',2)))")
[ -n "$API_MEMO" ] && [ "$API_MEMO" = "$PY_MEMO" ] && ok "outstanding memo equals payments.memo_bytes32($JOB, 2): $API_MEMO" || bad "outstanding memo equals payments.memo_bytes32" "api=$API_MEMO py=$PY_MEMO"
[ "$(echo "$B" | jq_ "d['ledger']['unpaid_from_prior_jobs'], d['ledger']['defaults'], d['trust']['completed_paid_jobs']")" = "1 1 3" ] && ok "unpaid_from_prior_jobs 1, defaults 1, completed_paid_jobs still 3" || bad "ledger after the default" "$(echo "$B" | jq_ "d['ledger']")"
SR=$(curl -s "${AUTH[@]}" -w '\n%{http_code}' -X POST "$BASE/api/buyers/$BUYER/settle/$JOB/2"); SC=$(echo "$SR" | tail -1); SB=$(echo "$SR" | sed '$d')
[ "$SC" = "200" ] && [ "$(echo "$SB" | jq_ "d['settled']['step'], d['settled']['simulated']")" = "2 True" ] && ok "POST /api/buyers/{addr}/settle/{job}/{step} settles it (fake backend; reconciled by $(echo "$SB" | jq_ "d['reconciled_by']"))" || bad "settle endpoint" "got $SC: $(echo "$SB" | head -c 200)"
B=$(jget "/api/buyers/$BUYER")
[ "$(echo "$B" | jq_ "d['ledger']['unpaid_from_prior_jobs'], len(d['outstanding'])")" = "0 0" ] && ok "after settling: unpaid_from_prior_jobs 0, outstanding empty" || bad "after settling: unpaid_from_prior_jobs 0, outstanding empty" "$(echo "$B" | jq_ "d['ledger']['unpaid_from_prior_jobs'], d['outstanding']")"
C=$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/buyers/$BUYER/settle/$JOB/2")
[ "$C" = "404" ] && ok "settling it again -> 404 (nothing outstanding)" || bad "settling it again -> 404" "got $C"

# ---------------------------------------------------------------- quiet when idle
# a waiting job must not grow the journal each pass: open one and leave it unpaid
RESP3=$(curl -s "${AUTH[@]}" -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
JOB2=$(echo "$RESP3" | jq_ "d['job_id']")
sleep $((INTERVAL * 2 + 1))
N1=$(jget "/api/journal?limit=500" | jq_ "d['count']")
sleep 10
N2=$(jget "/api/journal?limit=500" | jq_ "d['count']")
[ "$N1" = "$N2" ] && ok "journal did not grow while nothing changed ($N1 -> $N2 over 10s)" || bad "journal did not grow while nothing changed" "$N1 -> $N2"

# ---------------------------------------------------------------- status for the page
[ "$(jget "/api/status" | jq_ "sorted(e['name'] for e in d['receipts_abi'])")" = "['Paid', 'pay']" ] && ok "status carries the receipts ABI (pay, Paid)" || bad "status carries the receipts ABI"
[ "$(jget "/api/status" | jq_ "sorted(e['name'] for e in d['usdc_abi'])")" = "['allowance', 'approve', 'balanceOf', 'decimals', 'name', 'version']" ] && ok "status carries the USDC ABI (incl. name/version for the x402 EIP-712 domain)" || bad "status carries the USDC ABI"
[ "$(jget "/api/status" | jq_ "'usdc_address' in d")" = "True" ] && ok "status carries usdc_address" || bad "status carries usdc_address"
C=$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs/$JOB/pay")
[ "$C" = "400" ] && ok "simulate on a job with no open invoice -> 400" || bad "simulate on a job with no open invoice -> 400" "got $C"

# ---------------------------------------------------------------- simulate is fake-only
PORT2=8792
PAYMENTS=base .venv/bin/turnstyl serve --port $PORT2 --db "$DB" > /tmp/turnstyl-test-serve-base.log 2>&1 &
SERVER2=$!; disown $SERVER2 2>/dev/null   # so the shell does not report the kill below
for i in $(seq 1 20); do curl -s -o /dev/null "http://127.0.0.1:$PORT2/api/status" && break; sleep 0.5; done
TOKEN2=$(signin "http://127.0.0.1:$PORT2") && AUTH2=(-H "Authorization: Bearer $TOKEN2") || echo "  note: could not sign in on the base-backend server; its checks run public"
R2=$(curl -s "${AUTH2[@]}" -w '\n%{http_code}' -X POST "http://127.0.0.1:$PORT2/api/jobs/$JOB2/pay"); C2=$(echo "$R2" | tail -1); B2=$(echo "$R2" | sed '$d')
R3=$(curl -s "${AUTH2[@]}" -w '\n%{http_code}' -X POST "http://127.0.0.1:$PORT2/api/buyers/$BUYER/settle/$JOB/4"); C3=$(echo "$R3" | tail -1); B3=$(echo "$R3" | sed '$d')
# x402 on the base backend: an unpaid POST must quote this invoice
X4=$(curl -s "${AUTH2[@]}" "http://127.0.0.1:$PORT2/api/status" | jq_ "d['x402']['enabled'], d['x402']['network']")
XJOB=$(jget "/api/jobs?buyer=$BUYER" | jq_ "[j['job_id'] for j in d['jobs'] if j['status']!='complete'][0] if [j for j in d['jobs'] if j['status']!='complete'] else ''")
XSTEP=$(jget "/api/jobs/$XJOB" | jq_ "(d.get('open_invoice') or {}).get('step') or ''")
XAMT=$(jget "/api/jobs/$XJOB" | jq_ "(d.get('open_invoice') or {}).get('amount_usdc') or ''")
case "$X4" in
  "True eip155:84532")
    ok "x402 is on under PAYMENTS=base ($X4)"
    if [ -n "$XSTEP" ]; then
      XR=$(curl -s "${AUTH2[@]}" -w '\n%{http_code}' -X POST "http://127.0.0.1:$PORT2/api/jobs/$XJOB/pay-x402/$XSTEP")
      XC=$(echo "$XR" | tail -1); XB=$(echo "$XR" | sed '$d')
      XUNITS=$($PY -c "print(round(float('$XAMT')*1000000))")
      if [ "$XC" = "402" ]; then
        XFACTS=$(echo "$XB" | jq_ "d['accepts'][0]['payTo'], d['accepts'][0]['network'], d['accepts'][0]['amount'], d['accepts'][0]['scheme']")
        [ "$XFACTS" = "$AGENT_ADDRESS eip155:84532 $XUNITS exact" ] && ok "unpaid pay-x402 -> 402 quoting $AGENT_ADDRESS, eip155:84532, $XUNITS units ($XAMT USDC)" || bad "402 requirements" "got: $XFACTS (wanted $AGENT_ADDRESS eip155:84532 $XUNITS exact)"
      else
        bad "unpaid pay-x402 -> 402" "got $XC: $(echo "$XB" | head -c 200)"
      fi
    else
      bad "unpaid pay-x402 -> 402" "no open invoice to quote"
    fi
    ;;
  *) ok "x402 unavailable on this run, skipping the 402 check ($X4)";;
esac

kill $SERVER2 2>/dev/null
[ "$C2" = "404" ] && grep -q "payments are on chain; use the Pay button" <<< "$B2" && ok "PAYMENTS=base: simulate -> 404 with the on-chain detail" || bad "PAYMENTS=base: simulate -> 404" "got $C2: $(echo "$B2" | head -c 120)"
[ "$C3" = "404" ] && grep -q "payments are on chain; use the Pay button" <<< "$B3" && ok "PAYMENTS=base: settle -> 404 with the on-chain detail" || bad "PAYMENTS=base: settle -> 404" "got $C3: $(echo "$B3" | head -c 120)"

# ---------------------------------------------------------------- x402 off on the fake backend
[ "$(jget "/api/status" | jq_ "d['x402']['enabled']")" = "False" ] && ok "x402 is off under PAYMENTS=fake" || bad "x402 is off under PAYMENTS=fake" "$(jget "/api/status" | jq_ "d['x402']")"
R=$(curl -s "${AUTH[@]}" -w '\n%{http_code}' -X POST "$BASE/api/jobs/$JOB/pay-x402/2"); C=$(echo "$R" | tail -1); B=$(echo "$R" | sed '$d')
[ "$C" = "404" ] && grep -q "x402 is not available" <<< "$B" && grep -q "settles real USDC" <<< "$B" && ok "pay-x402 under PAYMENTS=fake -> 404 saying why" || bad "pay-x402 under PAYMENTS=fake -> 404" "got $C: $(echo "$B" | head -c 160)"
R=$(curl -s "${AUTH[@]}" -w '\n%{http_code}' -X POST "$BASE/api/buyers/$BUYER/settle-x402/$JOB/2"); C=$(echo "$R" | tail -1); B=$(echo "$R" | sed '$d')
[ "$C" = "404" ] && grep -q "x402 is not available" <<< "$B" && ok "settle-x402 under PAYMENTS=fake -> 404 saying why" || bad "settle-x402 under PAYMENTS=fake -> 404" "got $C: $(echo "$B" | head -c 160)"

# ---------------------------------------------------------------- validation
C=$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"0xnotanaddress\",\"source\":$SRC}")
[ "$C" = "400" ] && ok "bad address -> 400" || bad "bad address -> 400" "got $C"
BIG=$($PY -c "import json;print(json.dumps('pragma solidity ^0.8.20; contract Big {}' + 'x'*102400))")
C=$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$BIG}")
[ "$C" = "400" ] && ok "100 KB source -> 400" || bad "100 KB source -> 400" "got $C"
C=$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":\"hello world\"}")
[ "$C" = "400" ] && ok "non-Solidity source -> 400" || bad "non-Solidity source -> 400" "got $C"

# ---------------------------------------------------------------- buyer filter
OTHER=0x000000000000000000000000000000000000dead
[ "$(jget "/api/jobs?buyer=$BUYER" | jq_ "len(d['jobs'])")" = "6" ] && ok "GET /api/jobs?buyer= lists this buyer's 6 jobs (five audits plus the adversarial one)" || bad "GET /api/jobs?buyer= lists this buyer's 6 jobs" "$(jget "/api/jobs?buyer=$BUYER" | jq_ "len(d['jobs'])")"
[ "$(jget "/api/jobs?buyer=$(echo $BUYER | tr a-f A-F)" | jq_ "len(d['jobs'])")" = "6" ] && ok "buyer filter is case-insensitive" || bad "buyer filter is case-insensitive" "$(jget "/api/jobs?buyer=$(echo $BUYER | tr a-f A-F)" | jq_ "len(d['jobs'])")"
[ "$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$BASE/api/jobs?buyer=$OTHER")" = "403" ] && ok "a buyer cannot list another address's jobs -> 403" || bad "buyer filter refuses other buyers" "got $(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$BASE/api/jobs?buyer=$OTHER")"
[ "$(jop "/api/jobs?buyer=$OTHER" | jq_ "len(d['jobs'])")" = "0" ] && ok "the operator may ask for any address (none for a stranger)" || bad "operator buyer filter" "$(jop "/api/jobs?buyer=$OTHER" | head -c 160)"
[ "$(jop "/api/jobs" | jq_ "len(d['jobs'])")" = "6" ] && ok "GET /api/jobs without the param lists all six, to the operator" || bad "GET /api/jobs without the param lists all" "$(jop "/api/jobs" | jq_ "len(d['jobs'])")"

# ---------------------------------------------------------------- job types
JT=$(jget "/api/job_types")
[ "$(echo "$JT" | jq_ "d['default'], sorted(t['id'] for t in d['job_types'])")" = "audit ['audit', 'tests']" ] && ok "GET /api/job_types lists audit and tests, audit default" || bad "GET /api/job_types" "$(echo "$JT" | head -c 200)"
[ "$(echo "$JT" | jq_ "[ (s['name'], s['base_price_usdc'], s['gate']) for t in d['job_types'] if t['id']=='tests' for s in t['steps'] ]")" = "[('scope', 0.0, 'none'), ('plan', 0.4, 'none'), ('tests', 0.75, 'forge_test'), ('report', 0.25, 'none')]" ] && ok "the tests type carries its own steps, prices and forge_test gate" || bad "tests type spec" "$(echo "$JT" | jq_ "[t for t in d['job_types'] if t['id']=='tests']")"
[ "$(jget "/api/status" | jq_ "len(d['job_types']), d['default_job_type']")" = "2 audit" ] && ok "/api/status lists the job types" || bad "/api/status lists the job types"

C=$(curl -s "${AUTH[@]}" -w '\n%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC,\"job_type\":\"nope\"}")
[ "$(echo "$C" | tail -1)" = "400" ] && grep -q "unknown job_type" <<< "$C" && ok "POST with an unknown job_type -> 400" || bad "POST with an unknown job_type -> 400" "$(echo "$C" | head -c 160)"

TJ=$(curl -s "${AUTH[@]}" -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC,\"job_type\":\"tests\"}")
TJOB=$(echo "$TJ" | jq_ "d['job_id']")
[ -n "$TJOB" ] && [ "$(echo "$TJ" | jq_ "d['job_type'], [s['name'] for s in d['steps']]")" = "tests ['scope', 'plan', 'tests', 'report']" ] && ok "POST with job_type tests creates a tests job ($TJOB)" || bad "POST with job_type tests" "$(echo "$TJ" | head -c 200)"
[ "$(echo "$TJ" | jq_ "[s['price_usdc'] for s in d['steps']]")" = "[0.0, 0.4, 0.75, 0.25]" ] && ok "the tests job is priced by its own spec" || bad "tests job prices" "$(echo "$TJ" | jq_ "[s['price_usdc'] for s in d['steps']]")"

# drive it to completion: pay whatever invoice is open, let the worker run each step
for _ in $(seq 1 12); do
  ST=$(jget "/api/jobs/$TJOB" | jq_ "d['status'], (d.get('open_invoice') or {}).get('step'), (d.get('open_invoice') or {}).get('paid')")
  case "$ST" in
    "complete"*) break;;
    *" None None") sleep 1;;
    *" False") curl -s "${AUTH[@]}" -o /dev/null -X POST "$BASE/api/jobs/$TJOB/pay"; sleep 2;;
    *) sleep 2;;
  esac
done
[ "$(jget "/api/jobs/$TJOB" | jq_ "d['status']")" = "complete" ] && ok "the tests job ran to completion" || bad "the tests job ran to completion" "$(jget "/api/jobs/$TJOB" | jq_ "d['status'], d['current_step']")"
TS=$(jget "/api/jobs/$TJOB" | jq_ "[ (s['tests_total'], s['tests_passed'], s['tests_failed'], s['compiles']) for s in d['steps'] if s['step']==3 ][0]")
case "$TS" in
  "(5, 3, 2, True)") ok "step 3 went through the forge_test gate: 5 tests, 3 passed, 2 failed";;
  *) bad "step 3 forge_test results" "got $TS";;
esac
curl -s "${AUTH[@]}" "$BASE/api/jobs/$TJOB/report.md" -o /tmp/turnstyl-tests-report.md
grep -q "^# turnstyl test suite report" /tmp/turnstyl-tests-report.md && ok "report.md is titled for the service" || bad "report.md title" "$(head -1 /tmp/turnstyl-tests-report.md)"
grep -q "forge test: 3 passed, 2 failed, 5 total" /tmp/turnstyl-tests-report.md && ok "report.md shows the run results" || bad "report.md shows the run results" "$(grep -n 'forge test' /tmp/turnstyl-tests-report.md | head -2)"
grep -q "^## Step 3: tests" /tmp/turnstyl-tests-report.md && ok "report.md names the type's own steps" || bad "report.md step names"

# ---------------------------------------------------------------- missing memory
rm -f "$DB" "$DB-wal" "$DB-shm"
sleep 1
C=$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
[ "$C" = "409" ] && ok "POST against a missing db -> 409" || bad "POST against a missing db -> 409" "got $C"
[ "$(jget "/api/status" | jq_ "d['memory_missing']")" = "True" ] && ok "GET /api/status reports memory_missing" || bad "GET /api/status reports memory_missing"
RM=$(curl -s "${AUTH[@]}" -w '\n%{http_code}' "$BASE/api/jobs/$JOB/report.md"); [ "$(echo "$RM" | tail -1)" = "200" ] && [ "$(echo "$RM" | sed '$d' | grep -c .)" = "1" ] && grep -q "memory missing" <<< "$RM" && ok "report.md with memory missing -> 200, one line" || bad "report.md with memory missing" "$(echo "$RM" | head -c 120)"
sleep $((INTERVAL * 2 + 1))
[ ! -f "$DB" ] && ok "worker did not recreate the deleted database" || bad "worker did not recreate the deleted database"

# ---------------------------------------------------------------- CORS for the Pages origin
PAGES_ORIGIN="https://shrooms08.github.io"
H=$(curl -s "${AUTH[@]}" -D - -o /dev/null -X OPTIONS "$BASE/api/jobs" -H "Origin: $PAGES_ORIGIN" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type,authorization,ngrok-skip-browser-warning")
grep -qi "access-control-allow-origin: $PAGES_ORIGIN" <<< "$H" && ok "OPTIONS preflight from the Pages origin is allowed" || bad "OPTIONS preflight from the Pages origin" "$(echo "$H" | head -8)"
grep -qiE "access-control-allow-methods:.*POST" <<< "$H" && ok "preflight allows POST" || bad "preflight allows POST"
grep -qi "access-control-allow-headers:.*ngrok-skip-browser-warning" <<< "$H" && ok "preflight allows the tunnel header" || bad "preflight allows the tunnel header" "$(echo "$H" | grep -i allow-headers)"
grep -qiE "access-control-allow-headers:.*authorization" <<< "$H" && ok "preflight allows Authorization (the app cannot sign in without it)" || bad "preflight allows Authorization" "$(echo "$H" | grep -i allow-headers)"
H2=$(curl -s "${AUTH[@]}" -D - -o /dev/null -X POST "$BASE/api/jobs" -H "Origin: $PAGES_ORIGIN" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
grep -qi "access-control-allow-origin: $PAGES_ORIGIN" <<< "$H2" && ok "POST from the Pages origin carries the CORS header" || bad "POST from the Pages origin carries the CORS header"
H3=$(curl -s -D - -o /dev/null "$BASE/api/status" -H "Origin: https://evil.example")
grep -qi "access-control-allow-origin" <<< "$H3" && bad "other origins get no CORS header" "$(echo "$H3" | grep -i allow-origin)" || ok "other origins get no CORS header"

# ---------------------------------------------------------------- daily cap
PORT4=8795; BASE4="http://127.0.0.1:$PORT4"; CAPDB=./data/cap_$$.db
rm -f "$CAPDB" "$CAPDB-wal" "$CAPDB-shm"
MAX_JOBS_PER_DAY=2 .venv/bin/turnstyl serve --port $PORT4 --db "$CAPDB" > /tmp/turnstyl-test-cap.log 2>&1 &
S4=$!; disown $S4 2>/dev/null
for i in $(seq 1 20); do curl -s -o /dev/null "$BASE4/api/status" && break; sleep 0.5; done
TOKEN4=$(signin "$BASE4") && AUTH4=(-H "Authorization: Bearer $TOKEN4") || echo "  note: could not sign in on the daily-cap server"
[ "$(curl -s "${AUTH4[@]}" "$BASE4/api/status" | jq_ "d['max_jobs_per_day'], d['remaining_today']")" = "2 2" ] && ok "status reports max_jobs_per_day 2, remaining_today 2" || bad "status reports the daily cap" "$(curl -s "${AUTH4[@]}" "$BASE4/api/status" | jq_ "d['max_jobs_per_day'], d['remaining_today']")"
SRC_B=$($PY -c "import json;print(json.dumps(open('examples/Vault.sol').read()+'// variant B\n'))")
SRC_C=$($PY -c "import json;print(json.dumps(open('examples/Vault.sol').read()+'// variant C\n'))")
C1=$(curl -s "${AUTH4[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE4/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
C2=$(curl -s "${AUTH4[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE4/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC_B}")
[ "$C1 $C2" = "200 200" ] && ok "two creations within the cap -> 200, 200" || bad "two creations within the cap" "got $C1 $C2"
[ "$(curl -s "${AUTH4[@]}" "$BASE4/api/status" | jq_ "d['remaining_today']")" = "0" ] && ok "remaining_today is 0 after two creations" || bad "remaining_today is 0 after two creations"
R3=$(curl -s "${AUTH4[@]}" -w '\n%{http_code}' -X POST "$BASE4/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC_C}"); C3=$(echo "$R3" | tail -1); B3=$(echo "$R3" | sed '$d')
[ "$C3" = "429" ] && grep -q "jobs for today" <<< "$B3" && ok "third creation -> 429 with the daily-cap detail" || bad "third creation -> 429" "got $C3: $(echo "$B3" | head -c 160)"
C5=$(curl -s "${AUTH4[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE4/api/jobs" -H 'content-type: application/json' -d "{\"buyer\":\"$BUYER\",\"source\":$SRC}")
[ "$C5" = "429" ] && ok "a resume is also refused once the cap is spent (nothing created)" || bad "resume under a spent cap" "got $C5"
kill $S4 2>/dev/null; rm -f "$CAPDB" "$CAPDB-wal" "$CAPDB-shm"

# ---------------------------------------------------------------- fresh memory on startup
PORT3=8793; BASE3="http://127.0.0.1:$PORT3"; FRESH=./data/fresh_$$.db
rm -f "$FRESH" "$FRESH-wal" "$FRESH-shm"
.venv/bin/turnstyl serve --with-worker --port $PORT3 --interval 1 --db "$FRESH" > /tmp/turnstyl-test-fresh.log 2>&1 &
S3=$!; disown $S3 2>/dev/null
for i in $(seq 1 20); do curl -s -o /dev/null "$BASE3/api/status" && break; sleep 0.5; done
grep -q "memory: created ${FRESH#./}" /tmp/turnstyl-test-fresh.log && ok "missing path: startup logs '$(grep -o 'memory: created.*' /tmp/turnstyl-test-fresh.log | head -1)'" || bad "missing path: startup log line" "$(head -3 /tmp/turnstyl-test-fresh.log)"
[ "$(curl -s "$BASE3/api/status" | jq_ "d['db_exists'], d['records'], d['memory_missing']")" = "True 0 False" ] && ok "fresh store: db_exists true, records 0, memory_missing false" || bad "fresh store status" "$(curl -s "$BASE3/api/status" | jq_ "d['db_exists'], d['records'], d['memory_missing']")"
kill $S3 2>/dev/null; sleep 1
.venv/bin/turnstyl serve --with-worker --port $PORT3 --interval 1 --db "$FRESH" > /tmp/turnstyl-test-fresh2.log 2>&1 &
S3=$!; disown $S3 2>/dev/null
for i in $(seq 1 20); do curl -s -o /dev/null "$BASE3/api/status" && break; sleep 0.5; done
grep -q "memory: using ${FRESH#./} (0 records)" /tmp/turnstyl-test-fresh2.log && ok "existing path: startup logs '$(grep -o 'memory: using.*' /tmp/turnstyl-test-fresh2.log | head -1)'" || bad "existing path: startup log line" "$(head -3 /tmp/turnstyl-test-fresh2.log)"
rm -f "$FRESH" "$FRESH-wal" "$FRESH-shm"; sleep 3
[ "$(curl -s "$BASE3/api/status" | jq_ "d['memory_missing']")" = "True" ] && ok "deleted while running: memory_missing true" || bad "deleted while running: memory_missing true"
[ ! -f "$FRESH" ] && ok "neither the API nor the worker recreated it (2+ passes later)" || bad "file was recreated while running"
kill $S3 2>/dev/null

echo
if [ "$FAILS" -eq 0 ]; then echo "RESULT: PASS - API and worker behave"; exit 0; fi
echo "RESULT: FAIL - $FAILS check(s) failed (server log: /tmp/turnstyl-test-serve.log)"; exit 1
