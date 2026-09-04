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

DB=./data/demo_live.db
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

# ----------------------------------------------------------------- beat 1
before=$FAILURES
echo "BEAT 1: top the buyer up so it can pay its invoices"
# topup_buyer sends a fixed 1 USDC per call when the buyer is under 2.50. A full
# run costs the buyer 2.25 USDC, so call it until it reports nothing left to do.
for _ in 1 2 3 4; do
  run "topup_buyer" $PY scripts/topup_buyer.py
  flatten "$OUT" | grep -qF "TOPUP SKIPPED" && break
done
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
echo "BEAT 2: new job — free scope, then an invoice for step 2"
run "job new" $CLI job new "$CONTRACT" --buyer "$BUYER_ADDRESS"
JOB1=$(job_id_from_output)
if [ -z "$JOB1" ]; then
  echo "turnstyl demo_live: could not read a job id out of 'job new' output." >&2
  sed 's/^/  /' "$OUT" | head -20 >&2
  exit 1
fi
echo "    job 1 = $JOB1"
check 2 "step 1 ran free" "STEP 1: scope"
check 2 "decision is RUN_FREE" "DECISION: RUN_FREE"
check 2 "invoice for step 2 at 0.50 USDC" "amount 0.50 USDC"
check 2 "invoice names the receipts contract" "$RECEIPTS_ADDRESS"
check 2 "invoice tells the buyer exactly what to run" "scripts/buyer_pay.py $JOB1 2"
beat_result 2 "job opened, step 2 invoiced at 0.50 USDC" $before

# ----------------------------------------------------------------- beat 3
before=$FAILURES
echo "BEAT 3: buyer pays step 2 on chain; agent runs it and commits the output"
run "buyer_pay $JOB1 2" $PY scripts/buyer_pay.py "$JOB1" 2
check 3 "payment landed on chain" "PAID 0.50 USDC tx"
sleep 4
run_until_paid "$JOB1"
check 3 "decision is RUN_PAID" "DECISION: RUN_PAID"
check 3 "step 2 executed" "STEP 2: findings"
check 3 "reentrancy finding delivered" "Reentrancy in withdraw()"
check 3 "output committed on chain" "COMMITTED"
check 3 "invoice for step 3 at 0.75 USDC" "amount 0.75 USDC"
beat_result 3 "paid step executed and committed" $before

# ----------------------------------------------------------------- beat 4
before=$FAILURES
echo "BEAT 4: buyer pays step 3; two paid steps earn the trusted tier"
run "buyer_pay $JOB1 3" $PY scripts/buyer_pay.py "$JOB1" 3
check 4 "payment landed on chain" "PAID 0.75 USDC tx"
sleep 4
run_until_paid "$JOB1"
check 4 "decision is RUN_PAID" "DECISION: RUN_PAID"
check 4 "step 3 executed" "STEP 3: patch"
check 4 "output committed on chain" "COMMITTED"
run "ledger" $CLI ledger "$BUYER_ADDRESS"
check 4 "buyer has 2 paid steps" "paid steps 2"
check 4 "buyer trust tier is trusted" "trust tier trusted"
beat_result 4 "two paid steps, buyer now trusted" $before

# ----------------------------------------------------------------- beat 5
before=$FAILURES
echo "BEAT 5: fresh process runs step 4 on credit and closes the job"
run "job run $JOB1 (credit)" $CLI job run "$JOB1"
check 5 "decision is RUN_ON_CREDIT" "DECISION: RUN_ON_CREDIT"
check 5 "reason names paid_steps=2" "paid_steps=2"
check 5 "step 4 executed" "STEP 4: verify"
check 5 "output committed on chain" "COMMITTED"
check 5 "job is complete" "COMPLETE"
DECISION_5=$(decision_line "$OUT")
run "ledger" $CLI ledger "$BUYER_ADDRESS"
check 5 "step 4 is carried as an outstanding invoice" "4 (verify)"
check 5 "the debt is recorded against the buyer" "unpaid from prior jobs 1"
beat_result 5 "job complete with one unpaid step on credit" $before

# ----------------------------------------------------------------- beat 6
before=$FAILURES
echo "BEAT 6: same buyer, same contract — the agent refuses paid work"
run "job new (second job)" $CLI job new "$CONTRACT" --buyer "$BUYER_ADDRESS"
JOB2=$(job_id_from_output)
if [ -z "$JOB2" ] || [ "$JOB2" = "$JOB1" ]; then
  echo "  FAIL a second job id was not created (got '${JOB2:-empty}')"
  FAILURES=$((FAILURES + 1)); FAILED_BEATS+=("6: second job id")
else
  echo "  ok   second job created: $JOB2"
fi
check 6 "step 1 is still free" "STEP 1: scope"
check 6 "decision is RUN_FREE" "DECISION: RUN_FREE"
run "job run $JOB2" $CLI job run "$JOB2"
check 6 "decision is REFUSE" "DECISION: REFUSE"
check 6 "reason names the unpaid prior step" "unpaid on a completed job"
check_not 6 "no paid step was executed" "STEP 2: findings"
DECISION_6=$(decision_line "$OUT")
beat_result 6 "refused: the buyer owes for a closed job" $before

# ----------------------------------------------------------------- beat 7
before=$FAILURES
echo "BEAT 7: buyer settles the old debt; reconcile restores its standing"
run "buyer_pay $JOB1 4" $PY scripts/buyer_pay.py "$JOB1" 4
check 7 "the old step-4 invoice was paid on chain" "PAID 0.25 USDC tx"
sleep 4
run "job run $JOB2" $CLI job run "$JOB2"
check 7 "the debt was reconciled from chain" "RECONCILED"
check 7 "decision is WAIT_FOR_PAYMENT" "DECISION: WAIT_FOR_PAYMENT"
check 7 "step 2 is priced at 0.25 USDC" "amount 0.25 USDC"
check 7 "the price reason names the cached findings" "findings cached for this contract hash"
DECISION_7=$(decision_line "$OUT")
beat_result 7 "debt cleared from chain, repeat work half price" $before

# ----------------------------------------------------------------- beat 8
before=$FAILURES
echo "BEAT 8: buyer pays the discounted step; it is served from memory"
run "buyer_pay $JOB2 2" $PY scripts/buyer_pay.py "$JOB2" 2
check 8 "payment landed on chain" "PAID 0.25 USDC tx"
sleep 4
run_until_paid "$JOB2"
check 8 "decision is RUN_PAID" "DECISION: RUN_PAID"
check 8 "step 2 was served from memory, not the model" "from memory (cached)"
check 8 "output committed on chain" "COMMITTED"
beat_result 8 "cached step sold at half price, no model call" $before

# ----------------------------------------------------------------- beat 9
before=$FAILURES
echo "BEAT 9: DELETE TEST — wipe memory and watch the agent forget"
rm -f "$DB" "$DB-wal" "$DB-shm"
if [ -f "$DB" ]; then
  echo "  FAIL could not delete $DB"
  FAILURES=$((FAILURES + 1)); FAILED_BEATS+=("9: delete db")
fi
run "job new (after delete)" $CLI job new "$CONTRACT" --buyer "$BUYER_ADDRESS"
JOB3=$(job_id_from_output)
if [ -n "$JOB3" ] && [ "$JOB3" != "$JOB1" ] && [ "$JOB3" != "$JOB2" ]; then
  echo "  ok   a brand new job id was issued: $JOB3"
else
  echo "  FAIL expected a new job id, got '${JOB3:-empty}'"
  FAILURES=$((FAILURES + 1)); FAILED_BEATS+=("9: new job id")
fi
check 9 "step 1 ran again from scratch" "STEP 1: scope"
check_not 9 "the cached findings are gone" "from memory (cached)"
check 9 "step 2 is invoiced at 0.50 USDC again" "amount 0.50 USDC"
run "ledger (after delete)" $CLI ledger "$BUYER_ADDRESS"
check 9 "buyer trust tier is back to new" "trust tier new"
check 9 "the buyer's paid history is gone" "paid steps 0"
# An invoice is only an offer. Pay it, and the double charge stops being a
# claim about what would happen and becomes a second Paid log on the same
# contract, for work this buyer already bought.
run "buyer_pay $JOB3 2 (the double charge)" $PY scripts/buyer_pay.py "$JOB3" 2
check 9 "the buyer is charged a second time for work already paid for" "PAID 0.50 USDC tx"
DOUBLE_TX=$(grep -oE '0x[0-9a-fA-F]{64}' "$OUT" | tail -1)
if [ "$FAILURES" -eq "$before" ]; then
  echo
  echo "DOUBLE CHARGE REPRODUCED"
  echo "  memory deleted; this buyer paid 1.75 USDC on chain across two jobs, and"
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
echo "  beat 5: $DECISION_5"
echo "  beat 6: $DECISION_6"
echo "  beat 7: $DECISION_7"
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
