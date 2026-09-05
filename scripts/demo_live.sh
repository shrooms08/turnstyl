#!/usr/bin/env bash
# turnstyl live demo — Base Sepolia, real USDC, real receipts.
#
#   scripts/demo_live.sh
#
# PAYMENTS=base so every settlement is a Paid log on the receipts contract, and
# MOCK_LLM=1 so the step outputs are canned and the run costs no model spend.
# Runs against ./data/demo_live.db so it never touches ./data/turnstyl.db.
#
# Prints PASS or FAIL per beat, writes every transaction hash it saw to
# data/demo_live_txs.txt, and exits non-zero if any beat fails.

set -uo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "turnstyl demo_live: no .env in $(pwd). It must define BASE_SEPOLIA_RPC," >&2
  echo "  RECEIPTS_ADDRESS, USDC_ADDRESS, AGENT_* and BUYER_*." >&2
  exit 1
fi
set -a; source .env; set +a

for required in BASE_SEPOLIA_RPC RECEIPTS_ADDRESS USDC_ADDRESS AGENT_ADDRESS BUYER_ADDRESS; do
  if [ -z "${!required:-}" ]; then
    echo "turnstyl demo_live: $required is not set in .env." >&2
    exit 1
  fi
done

# Honour an externally chosen store; default to the demo one.
DB="${TURNSTYL_DB:-./data/demo_live.db}"
export TURNSTYL_DB="$DB"
export PAYMENTS=base
export MOCK_LLM=1
export COLUMNS=200
export NO_COLOR=1

PY=.venv/bin/python
CLI="$PY -m turnstyl.cli"
CONTRACT=examples/Vault.sol
TXFILE=data/demo_live_txs.txt
OUT=$(mktemp -d)/out.txt
EXPLORER=https://sepolia.basescan.org

mkdir -p data
: > "$TXFILE"
rm -f "$DB" "$DB-wal" "$DB-shm"

FAILURES=0
declare -a FAILED_BEATS=()

# Rich draws panels and soft-wraps. Strip the borders and collapse every run of
# whitespace so a substring check sees the text an operator sees.
flatten() { sed 's/[│┃╭╮╰╯─━┏┓┗┛┡┩╇┳┻┌┐└┘═║]/ /g' "$1" | tr '\n' ' ' | tr -s ' '; }

collect_tx() {
  grep -oE "$EXPLORER/tx/0x[0-9a-fA-F]{64}" "$1" 2>/dev/null \
    | grep -oE '0x[0-9a-fA-F]{64}' >> "$TXFILE"
}

run() {  # run <label> <command...> ; captures stdout+stderr to $OUT
  local label="$1"; shift
  echo "--- $label"
  "$@" >"$OUT" 2>&1
  local rc=$?
  collect_tx "$OUT"
  if [ $rc -ne 0 ]; then
    echo "    command exited $rc:"
    sed 's/^/      /' "$OUT" | tail -20
  fi
  return $rc
}

run_quiet() {  # run without printing a label; same capture and tx collection
  "$@" >"$OUT" 2>&1
  local rc=$?
  collect_tx "$OUT"
  if [ $rc -ne 0 ]; then
    echo "    command exited $rc:"
    sed 's/^/      /' "$OUT" | tail -20
  fi
  return $rc
}

check() {  # check <beat> <description> <needle>
  local beat="$1" desc="$2" needle="$3"
  if flatten "$OUT" | grep -qF -- "$needle"; then
    echo "  ok   $desc"
    return 0
  fi
  echo "  FAIL $desc"
  echo "       looked for: $needle"
  FAILURES=$((FAILURES + 1))
  FAILED_BEATS+=("$beat: $desc")
  return 1
}

check_not() {  # check_not <beat> <description> <needle>
  local beat="$1" desc="$2" needle="$3"
  if flatten "$OUT" | grep -qF -- "$needle"; then
    echo "  FAIL $desc"
    echo "       must NOT contain: $needle"
    FAILURES=$((FAILURES + 1))
    FAILED_BEATS+=("$beat: $desc")
    return 1
  fi
  echo "  ok   $desc"
  return 0
}

beat_result() {  # beat_result <beat> <title> <failures_before>
  if [ "$FAILURES" -eq "$3" ]; then
    echo "PASS $1: $2"
  else
    echo "FAIL $1: $2"
  fi
  echo
}

# A public RPC can lag a block behind a receipt it just returned. Re-ask the
# agent a few times rather than calling a settled invoice unpaid.
run_until_paid() {  # run_until_paid <job_id>
  local job="$1" attempt
  for attempt in 1 2 3 4 5 6; do
    run "job run $job (attempt $attempt)" $CLI job run "$job"
    if flatten "$OUT" | grep -qF "DECISION: RUN_PAID"; then
      return 0
    fi
    if ! flatten "$OUT" | grep -qF "DECISION: WAIT_FOR_PAYMENT"; then
      return 0  # some other decision; let the checks report it
    fi
    echo "    payment not visible yet; waiting for the log to propagate"
    sleep 5
  done
  return 0
}

job_id_from_output() { sed -n 's/^job \([0-9a-f][0-9a-f]*\) .*/\1/p' "$OUT" | head -1; }

# Rich soft-wraps a long reason across lines; flatten first, then cut at the
# "memory read:" line the CLI always prints straight after.
decision_line() {
  flatten "$1" | grep -oE 'DECISION: .*' | sed 's/ memory read:.*//' | head -1
}

echo "turnstyl live demo — Base Sepolia (chain 84532)"
echo "receipts : $RECEIPTS_ADDRESS"
echo "explorer : $EXPLORER/address/$RECEIPTS_ADDRESS"
echo "database : $DB"
echo "buyer    : $BUYER_ADDRESS"
echo

# ----------------------------------------------------------------- helpers
topup(){  # bring the buyer to at least 2.50 USDC (1 USDC per call, idempotent)
  echo "--- topup_buyer"
  for _ in 1 2 3 4 5; do
    run_quiet $PY scripts/topup_buyer.py
    flatten "$OUT" | grep -qF "TOPUP SKIPPED" && break
  done
}
pay_run(){  # pay_run <beat> <job> <step> <amount> [cached]
  local beat="$1" job="$2" step="$3" amount="$4" cached="${5:-}" attempt
  # The public RPC drops out for half a minute now and then. A payment that did
  # not land is retried, never treated as a failed beat on the first miss.
  for attempt in 1 2 3; do
    run "buyer_pay $job $step (attempt $attempt)" $PY scripts/buyer_pay.py "$job" "$step"
    flatten "$OUT" | grep -qF "PAID $amount USDC tx" && break
    echo "    payment did not land (RPC?); retrying in 20s"; sleep 20
  done
  check "$beat" "step $step paid on chain ($amount USDC)" "PAID $amount USDC tx"
  sleep 4
  run_until_paid "$job"
  check "$beat" "step $step ran as paid work" "DECISION: RUN_PAID"
  check "$beat" "step $step committed on chain" "COMMITTED"
  [ -n "$cached" ] && check "$beat" "step $step served from memory" "from memory (cached)"
}
complete_paid_job(){  # complete_paid_job <beat> -> sets NEWJOB; repeat contract, cached prices
  local beat="$1"
  run "job new" $CLI job new "$CONTRACT" --buyer "$BUYER_ADDRESS"
  NEWJOB=$(job_id_from_output)
  [ -n "$NEWJOB" ] || { echo "turnstyl demo_live: no job id from 'job new'" >&2; exit 1; }
  echo "    job = $NEWJOB"
  check "$beat" "step 1 served from memory" "from memory (cached)"
  check "$beat" "step 2 invoiced at 0.25 USDC (cached)" "amount 0.25 USDC"
  pay_run "$beat" "$NEWJOB" 2 0.25 cached
  check "$beat" "step 3 invoiced at 0.38 USDC (cached)" "amount 0.38 USDC"
  pay_run "$beat" "$NEWJOB" 3 0.38 cached
  check "$beat" "step 4 invoiced at 0.12 USDC (cached)" "amount 0.12 USDC"
  pay_run "$beat" "$NEWJOB" 4 0.12 cached
  check "$beat" "job complete" "COMPLETE"
}

# ----------------------------------------------------------------- beat 1
before=$FAILURES
echo "BEAT 1: top the buyer up so it can pay its invoices"
topup
if flatten "$OUT" | grep -qE "TOPUP SENT|TOPUP SKIPPED"; then
  echo "  ok   buyer funding checked"
  flatten "$OUT" | grep -oE "TOPUP (SENT|SKIPPED)[^|]*" | head -1 | sed 's/^/       /'
else
  echo "  FAIL buyer funding check produced no verdict"
  FAILURES=$((FAILURES + 1)); FAILED_BEATS+=("1: topup verdict")
fi
beat_result 1 "buyer funded" $before

# ----------------------------------------------------------------- beat 2
before=$FAILURES
echo "BEAT 2: new job — free scope, then an invoice for step 2, then WAIT"
run "job new" $CLI job new "$CONTRACT" --buyer "$BUYER_ADDRESS"
JOB1=$(job_id_from_output)
[ -n "$JOB1" ] || { echo "turnstyl demo_live: could not read a job id out of 'job new' output." >&2; sed 's/^/  /' "$OUT" | head -20 >&2; exit 1; }
echo "    job 1 = $JOB1"
check 2 "step 1 ran free" "STEP 1: scope"
check 2 "decision is RUN_FREE" "DECISION: RUN_FREE"
check 2 "invoice for step 2 at 0.50 USDC" "amount 0.50 USDC"
check 2 "invoice names the receipts contract" "$RECEIPTS_ADDRESS"
run "job run $JOB1 (unpaid)" $CLI job run "$JOB1"
check 2 "a new buyer waits for payment" "DECISION: WAIT_FOR_PAYMENT"
check 2 "the reason says credit comes after 3 fully paid jobs, currently 0" "credit after 3 fully paid jobs, currently 0"
DECISION_2=$(decision_line "$OUT")
beat_result 2 "job opened, step 2 invoiced, no credit for a stranger" $before

# ----------------------------------------------------------------- beat 3
before=$FAILURES
echo "BEAT 3: first job, every step paid on chain and committed"
pay_run 3 "$JOB1" 2 0.50
check 3 "reentrancy finding delivered" "Reentrancy in withdraw()"
check 3 "invoice for step 3 at 0.75 USDC" "amount 0.75 USDC"
pay_run 3 "$JOB1" 3 0.75
check 3 "invoice for step 4 at 0.25 USDC" "amount 0.25 USDC"
run "job run $JOB1 (step 4 unpaid)" $CLI job run "$JOB1"
check 3 "two paid steps still do not earn credit" "DECISION: WAIT_FOR_PAYMENT"
pay_run 3 "$JOB1" 4 0.25
check 3 "job complete" "COMPLETE"
run "ledger" $CLI ledger "$BUYER_ADDRESS"
check 3 "one completed paid job on the ledger" "completed paid jobs 1"
check 3 "buyer trust tier still new" "trust tier new"
beat_result 3 "first job fully paid, still no credit" $before

# ----------------------------------------------------------------- beat 3b
before=$FAILURES
echo "BEAT 3b: VERIFY. Every paid step's output matches its on-chain commit; a tampered copy does not"
VPORT=8796
$CLI serve --port $VPORT --db "$DB" > /tmp/turnstyl-demo-verify.log 2>&1 & VSRV=$!; disown $VSRV 2>/dev/null
for i in $(seq 1 20); do curl -s -o /dev/null "http://127.0.0.1:$VPORT/api/status" && break; sleep 0.5; done
curl -s "http://127.0.0.1:$VPORT/api/jobs/$JOB1/verify" > "$OUT"
VSUM=$($PY -c "
import json,sys; d=json.load(open('$OUT'))
paid=[x for x in d['steps'] if x['tx']]
print(len(d['steps']), len(paid), all(x['matches'] is True for x in paid), [x['step'] for x in d['steps'] if x['matches'] is None])")
case "$VSUM" in
  "4 3 True [1]") echo "  ok   verify: steps 2-4 match their Committed events; step 1 (free) has no commit";;
  *) echo "  FAIL verify on the first job: $VSUM"; FAILURES=$((FAILURES + 1)); FAILED_BEATS+=("3b: verify");;
esac
VERIFY_SAMPLE=$($PY -c "import json; d=json.load(open('$OUT')); print(json.dumps([x for x in d['steps'] if x['step']==2][0], indent=2))")
kill $VSRV 2>/dev/null

# tamper: a copy of the store, one byte of step 2's output changed through the
# SDK, verified against the same chain. The archive row of a closed job cannot
# be edited through the SDK, so the job entity is re-materialised from it with
# the altered byte via put_job_entity, which verify reads first (as the page does).
TAMPER="${DB%.db}_tamper.db"
rm -f "$TAMPER" "$TAMPER-wal" "$TAMPER-shm"
cp "$DB" "$TAMPER"; [ -f "$DB-wal" ] && cp "$DB-wal" "$TAMPER-wal"; [ -f "$DB-shm" ] && cp "$DB-shm" "$TAMPER-shm"
TDESC=$(TURNSTYL_DB="$TAMPER" $PY -c "
import sys; sys.path.insert(0,'src')
from pathlib import Path
from turnstyl import schema as S
from turnstyl.memory import TurnstylMemory, TurnstylStore
from turnstyl.api import read_archived_job
store = TurnstylStore(TurnstylMemory(Path('$TAMPER')))
arch = read_archived_job(Path('$TAMPER'), '$JOB1')
ent = S.JobEntity.model_validate(arch['body'])
rec = ent.steps['2']; out = rec.output; i = len(out)//2
rec.output = out[:i] + ('X' if out[i] != 'X' else 'Y') + out[i+1:]   # sha256 field left as recorded
store.put_job_entity('$JOB1', ent)
print('changed byte', i, 'of step 2 output; recorded sha kept', rec.output_sha256[:12])")
echo "    $TDESC"
$CLI serve --port 8797 --db "$TAMPER" > /tmp/turnstyl-demo-tamper.log 2>&1 & TSRV=$!; disown $TSRV 2>/dev/null
for i in $(seq 1 20); do curl -s -o /dev/null "http://127.0.0.1:8797/api/status" && break; sleep 0.5; done
curl -s "http://127.0.0.1:8797/api/jobs/$JOB1/verify" > "$OUT"
TSUM=$($PY -c "
import json; d=json.load(open('$OUT')); s2=[x for x in d['steps'] if x['step']==2][0]
print(s2['matches'], s2['output_sha256_recomputed']!=s2['output_sha256_stored'], s2['onchain_hash']=='0x'+s2['output_sha256_stored'], [x['matches'] for x in d['steps'] if x['step'] in (3,4)])")
case "$TSUM" in
  "False True True [True, True]") echo "  ok   tampered copy: step 2 matches false (output no longer hashes to the committed value); steps 3-4 still match";;
  *) echo "  FAIL tamper test: $TSUM"; FAILURES=$((FAILURES + 1)); FAILED_BEATS+=("3b: tamper");;
esac
TAMPER_REASON=$($PY -c "import json; d=json.load(open('$OUT')); print([x for x in d['steps'] if x['step']==2][0]['reason'])")
echo "    reason: $TAMPER_REASON"
kill $TSRV 2>/dev/null
rm -f "$TAMPER" "$TAMPER-wal" "$TAMPER-shm"
beat_result 3b "outputs verified against their commits; a tampered store is caught" $before

# ----------------------------------------------------------------- beat 4
before=$FAILURES
echo "BEAT 4: two more fully paid jobs, served from memory at half price"
topup
complete_paid_job 4; JOB2=$NEWJOB
run "ledger" $CLI ledger "$BUYER_ADDRESS"
check 4 "two completed paid jobs" "completed paid jobs 2"
check 4 "still new after two" "trust tier new"
topup
complete_paid_job 4; JOB3=$NEWJOB
run "ledger" $CLI ledger "$BUYER_ADDRESS"
check 4 "three completed paid jobs" "completed paid jobs 3"
check 4 "buyer trust tier is trusted" "trust tier trusted"
beat_result 4 "three fully paid jobs, buyer now trusted" $before

# ----------------------------------------------------------------- beat 5
before=$FAILURES
echo "BEAT 5: CREDIT. Fourth job: step 2 runs before its invoice clears"
topup
run "job new (fourth job)" $CLI job new "$CONTRACT" --buyer "$BUYER_ADDRESS"
JOB4=$(job_id_from_output); echo "    job 4 = $JOB4"
run "job run $JOB4 (credit)" $CLI job run "$JOB4"
check 5 "decision is RUN_ON_CREDIT" "DECISION: RUN_ON_CREDIT"
check 5 "reason names completed_paid_jobs=3 >= 3" "completed_paid_jobs=3 >= 3"
check 5 "step 2 executed from memory" "from memory (cached)"
check 5 "output committed on chain" "COMMITTED"
DECISION_5=$(decision_line "$OUT")
run "job run $JOB4 (step 3, one step owed)" $CLI job run "$JOB4"
check 5 "step 3 waits while a step is owed" "DECISION: WAIT_FOR_PAYMENT"
pay_run 5 "$JOB4" 3 0.38 cached
pay_run 5 "$JOB4" 4 0.12 cached
check 5 "job complete" "COMPLETE"
run "ledger" $CLI ledger "$BUYER_ADDRESS"
check 5 "step 2 is carried as an outstanding invoice" "2 (findings)"
check 5 "the debt is recorded against the buyer" "unpaid from prior jobs 1"
check 5 "a job closed with a debt does not count as paid" "completed paid jobs 3"
beat_result 5 "credit extended, then one default at close" $before

# ----------------------------------------------------------------- beat 6
before=$FAILURES
echo "BEAT 6: same buyer, same contract — the agent refuses paid work"
run "job new (fifth job)" $CLI job new "$CONTRACT" --buyer "$BUYER_ADDRESS"
JOB5=$(job_id_from_output); echo "    job 5 = $JOB5"
check 6 "step 1 is still free" "STEP 1: scope"
run "job run $JOB5" $CLI job run "$JOB5"
check 6 "decision is REFUSE" "DECISION: REFUSE"
check 6 "reason names the unpaid prior step" "unpaid on a completed job"
check_not 6 "no paid step was executed" "STEP 2: findings"
DECISION_6=$(decision_line "$OUT")
beat_result 6 "refused: the buyer owes for a closed job" $before

# ----------------------------------------------------------------- beat 7
before=$FAILURES
echo "BEAT 7: buyer settles the old debt; refusal lifts, credit must be earned back"
topup
for attempt in 1 2 3; do
  run "buyer_pay $JOB4 2 (attempt $attempt)" $PY scripts/buyer_pay.py "$JOB4" 2
  flatten "$OUT" | grep -qF "PAID 0.25 USDC tx" && break
  echo "    payment did not land (RPC?); retrying in 20s"; sleep 20
done
check 7 "the old step-2 invoice was paid on chain" "PAID 0.25 USDC tx"
sleep 4
run "job run $JOB5" $CLI job run "$JOB5"
check 7 "the debt was reconciled from chain" "RECONCILED"
check 7 "decision is WAIT_FOR_PAYMENT" "DECISION: WAIT_FOR_PAYMENT"
check 7 "credit returns after 4 consecutive paid steps, currently 1" "credit returns after 4 consecutive paid steps, currently 1"
check 7 "step 2 is priced at 0.25 USDC" "amount 0.25 USDC"
DECISION_7=$(decision_line "$OUT")
beat_result 7 "debt cleared from chain, credit not yet" $before

# ----------------------------------------------------------------- beat 8
before=$FAILURES
echo "BEAT 8: EARN-BACK. Three more paid steps (served from memory), then credit again"
pay_run 8 "$JOB5" 2 0.25 cached
pay_run 8 "$JOB5" 3 0.38 cached
pay_run 8 "$JOB5" 4 0.12 cached
run "ledger" $CLI ledger "$BUYER_ADDRESS"
check 8 "four consecutive paid steps since the default" "clean paid steps since 4"
check 8 "trusted again" "trust tier trusted"
run "job new (sixth job)" $CLI job new "$CONTRACT" --buyer "$BUYER_ADDRESS"
JOB6=$(job_id_from_output); echo "    job 6 = $JOB6"
run "job run $JOB6 (credit again)" $CLI job run "$JOB6"
check 8 "the next unpaid step runs on credit" "DECISION: RUN_ON_CREDIT"
check 8 "reason names consecutive_paid_since_default=4" "consecutive_paid_since_default=4"
DECISION_8=$(decision_line "$OUT")
beat_result 8 "one default worked off, credit restored" $before

# ----------------------------------------------------------------- beat 9
before=$FAILURES
echo "BEAT 9: DELETE TEST — wipe memory and watch the agent forget"
rm -f "$DB" "$DB-wal" "$DB-shm"
if [ -f "$DB" ]; then
  echo "  FAIL could not delete $DB"
  FAILURES=$((FAILURES + 1)); FAILED_BEATS+=("9: delete db")
fi
topup
run "job new (after delete)" $CLI job new "$CONTRACT" --buyer "$BUYER_ADDRESS"
JOB7=$(job_id_from_output)
if [ -n "$JOB7" ] && [ "$JOB7" != "$JOB1" ] && [ "$JOB7" != "$JOB6" ]; then
  echo "  ok   a brand new job id was issued: $JOB7"
else
  echo "  FAIL expected a new job id, got '${JOB7:-empty}'"
  FAILURES=$((FAILURES + 1)); FAILED_BEATS+=("9: new job id")
fi
check 9 "step 1 ran again from scratch" "STEP 1: scope"
check_not 9 "the cached findings are gone" "from memory (cached)"
check 9 "step 2 is invoiced at 0.50 USDC again" "amount 0.50 USDC"
run "ledger (after delete)" $CLI ledger "$BUYER_ADDRESS"
check 9 "buyer trust tier is back to new" "trust tier new"
check 9 "the three paid jobs are gone" "completed paid jobs 0"
for attempt in 1 2 3; do
  run "buyer_pay $JOB7 2 (the double charge, attempt $attempt)" $PY scripts/buyer_pay.py "$JOB7" 2
  flatten "$OUT" | grep -qF "PAID 0.50 USDC tx" && break
  echo "    payment did not land (RPC?); retrying in 20s"; sleep 20
done
check 9 "the buyer is charged a second time for work already paid for" "PAID 0.50 USDC tx"
DOUBLE_TX=$(grep -oE '0x[0-9a-fA-F]{64}' "$OUT" | tail -1)
if [ "$FAILURES" -eq "$before" ]; then
  echo
  echo "DOUBLE CHARGE REPRODUCED"
  echo "  memory deleted; this buyer paid for three whole audits on chain, and"
  echo "  the agent re-invoiced 0.50 USDC for work it had already delivered."
  echo "  The buyer just paid it: $DOUBLE_TX"
  echo "  $EXPLORER/tx/$DOUBLE_TX"
  echo "  Both payments are on chain. Only the agent's memory of the first is gone."
  echo
fi
beat_result 9 "memory deleted, buyer charged twice for the same work" $before

# ----------------------------------------------------------------- summary
sort -u "$TXFILE" -o "$TXFILE"
echo "========================================================================"
echo "DECISION lines"
echo "  beat 2 (new buyer waits): $DECISION_2"
echo "  beat 5 (credit):          $DECISION_5"
echo "  beat 6 (refuse):          $DECISION_6"
echo "  beat 7 (after default):   $DECISION_7"
echo "  beat 8 (earned back):     $DECISION_8"
echo
echo "verify response for job 1, step 2 (live run):"
echo "${VERIFY_SAMPLE:-n/a}" | sed 's/^/  /'
echo
echo "double-charge transaction: ${DOUBLE_TX:-none}"
echo
echo "transactions: $(wc -l < "$TXFILE" | tr -d ' ') recorded in $TXFILE"
echo "========================================================================"
if [ "$FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL — $FAILURES check(s) failed"
  for f in "${FAILED_BEATS[@]}"; do echo "  - $f"; done
  exit 1
fi
echo "RESULT: PASS — all 9 beats passed on Base Sepolia"
