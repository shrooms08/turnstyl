#!/usr/bin/env bash
# Prepare a recording take of the turnstyl demo.
#
#   scripts/video_prep.sh
#
# Idempotent: run it as many times as you like between takes. It clears the
# recording store, funds the buyer, checks both wallets can afford the whole
# sequence, and prints the one line to export in both terminals.
#
# It never prints a key. It reads .env and reports only public values.

set -uo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
DB=./data/video.db
EXPLORER=https://sepolia.basescan.org

# What one take costs, from the price table and the command sequence in
# docs/VIDEO_COMMANDS.md. Five buyer payments:
#   job 1 step 2  0.50   job 1 step 3  0.75   job 1 step 4  0.25 (paid late)
#   job 2 step 2  0.25   job 3 step 2  0.50 (the double charge)
REQUIRED_USDC=2.25
# Four agent commit() transactions: job 1 steps 2, 3, 4 and job 2 step 2.
AGENT_COMMITS=4
BUYER_PAYS=5
# Observed cost on Base Sepolia is ~0.0000006 ETH per transaction. These floors
# are ~30x that, so a take cannot die halfway on gas.
MIN_AGENT_ETH=0.00002
MIN_BUYER_ETH=0.00002

die() { echo; echo "NOT READY: $*" >&2; exit 1; }

if [ ! -f .env ]; then
  die "no .env in $(pwd). It must define BASE_SEPOLIA_RPC, USDC_ADDRESS,
  RECEIPTS_ADDRESS, AGENT_ADDRESS, AGENT_PRIVATE_KEY, BUYER_ADDRESS,
  BUYER_PRIVATE_KEY and ANTHROPIC_API_KEY."
fi
set -a; source .env; set +a

for required in BASE_SEPOLIA_RPC USDC_ADDRESS RECEIPTS_ADDRESS AGENT_ADDRESS BUYER_ADDRESS; do
  [ -n "${!required:-}" ] || die "$required is not set in .env."
done
for secret in AGENT_PRIVATE_KEY BUYER_PRIVATE_KEY ANTHROPIC_API_KEY; do
  [ -n "${!secret:-}" ] || die "$secret is not set in .env. (Its value is never printed.)"
done
command -v cast >/dev/null || die "the 'cast' tool (Foundry) is not on PATH."

echo "turnstyl video prep"
echo "==================="
echo

# ---------------------------------------------------------------- secrets
echo "SECRETS"
echo "  ANTHROPIC_API_KEY  present (${#ANTHROPIC_API_KEY} characters, value not shown)"
echo "  AGENT_PRIVATE_KEY  present (value not shown)"
echo "  BUYER_PRIVATE_KEY  present (value not shown)"
echo

# ---------------------------------------------------------------- chain
CHAIN=$(cast chain-id --rpc-url "$BASE_SEPOLIA_RPC" 2>/dev/null) \
  || die "cannot reach BASE_SEPOLIA_RPC. Check the network and the RPC URL."
[ "$CHAIN" = "84532" ] || die "expected Base Sepolia (chain 84532), got chain $CHAIN."

CODESIZE=$(cast codesize "$RECEIPTS_ADDRESS" --rpc-url "$BASE_SEPOLIA_RPC" 2>/dev/null || echo 0)
[ "${CODESIZE:-0}" -gt 0 ] || die "no contract deployed at RECEIPTS_ADDRESS=$RECEIPTS_ADDRESS."
ONCHAIN_AGENT=$(cast call "$RECEIPTS_ADDRESS" 'agent()(address)' --rpc-url "$BASE_SEPOLIA_RPC" 2>/dev/null)
if [ "$(echo "$ONCHAIN_AGENT" | tr 'A-Z' 'a-z')" != "$(echo "$AGENT_ADDRESS" | tr 'A-Z' 'a-z')" ]; then
  die "the receipts contract pays $ONCHAIN_AGENT but AGENT_ADDRESS is $AGENT_ADDRESS.
  Payments would go to a wallet this agent does not control."
fi
echo "RECEIPTS CONTRACT"
echo "  address   $RECEIPTS_ADDRESS"
echo "  chain     $CHAIN (Base Sepolia)"
echo "  code      $CODESIZE bytes"
echo "  agent()   $ONCHAIN_AGENT  (matches AGENT_ADDRESS)"
echo "  explorer  $EXPLORER/address/$RECEIPTS_ADDRESS"
echo

# ---------------------------------------------------------------- store
echo "RECORDING STORE"
if [ -f "$DB" ]; then
  echo "  clearing $DB ($(wc -c < "$DB" | tr -d ' ') bytes) from the last take"
  rm -f "$DB" "$DB-wal" "$DB-shm"
else
  echo "  $DB does not exist yet"
fi
[ -f "$DB" ] && die "could not delete $DB."
echo "  clean"
echo

# ---------------------------------------------------------------- funding
balances() {  # prints: <agent_eth> <buyer_eth> <agent_usdc> <buyer_usdc>
  local ae be au bu
  ae=$(cast balance "$AGENT_ADDRESS" --rpc-url "$BASE_SEPOLIA_RPC" --ether)
  be=$(cast balance "$BUYER_ADDRESS" --rpc-url "$BASE_SEPOLIA_RPC" --ether)
  au=$(cast call "$USDC_ADDRESS" 'balanceOf(address)(uint256)' "$AGENT_ADDRESS" --rpc-url "$BASE_SEPOLIA_RPC" | cut -d' ' -f1)
  bu=$(cast call "$USDC_ADDRESS" 'balanceOf(address)(uint256)' "$BUYER_ADDRESS" --rpc-url "$BASE_SEPOLIA_RPC" | cut -d' ' -f1)
  echo "$ae $be $au $bu"
}
usdc() { $PY -c "print(f'{int('$1')/1_000_000:.2f}')"; }
ge()   { $PY -c "import sys; sys.exit(0 if float('$1') >= float('$2') else 1)"; }

read -r AGENT_ETH BUYER_ETH AGENT_UNITS BUYER_UNITS <<<"$(balances)"
echo "BALANCES BEFORE TOP-UP"
echo "  agent  $AGENT_ADDRESS"
echo "         $AGENT_ETH ETH   $(usdc "$AGENT_UNITS") USDC"
echo "  buyer  $BUYER_ADDRESS"
echo "         $BUYER_ETH ETH   $(usdc "$BUYER_UNITS") USDC"
echo

echo "TOP-UP"
for _ in 1 2 3 4 5; do
  OUT=$($PY scripts/topup_buyer.py 2>&1) || { echo "$OUT" >&2; die "topup_buyer.py failed."; }
  echo "$OUT" | grep -E "TOPUP (SENT|SKIPPED)" | sed 's/^/  /'
  echo "$OUT" | grep -q "TOPUP SKIPPED" && break
done
echo

read -r AGENT_ETH BUYER_ETH AGENT_UNITS BUYER_UNITS <<<"$(balances)"
AGENT_USDC=$(usdc "$AGENT_UNITS"); BUYER_USDC=$(usdc "$BUYER_UNITS")
echo "BALANCES AFTER TOP-UP"
echo "  agent  $AGENT_ETH ETH   $AGENT_USDC USDC"
echo "  buyer  $BUYER_ETH ETH   $BUYER_USDC USDC"
echo

# ---------------------------------------------------------------- gates
echo "CAN THIS TAKE FINISH?"
ge "$AGENT_ETH" "$MIN_AGENT_ETH" \
  || die "agent has $AGENT_ETH ETH, needs at least $MIN_AGENT_ETH for $AGENT_COMMITS commit() transactions.
  Fund $AGENT_ADDRESS with Base Sepolia ETH."
echo "  ok  agent ETH $AGENT_ETH >= $MIN_AGENT_ETH ($AGENT_COMMITS commits)"

ge "$BUYER_ETH" "$MIN_BUYER_ETH" \
  || die "buyer has $BUYER_ETH ETH, needs at least $MIN_BUYER_ETH for $BUYER_PAYS pay() transactions.
  Fund $BUYER_ADDRESS with Base Sepolia ETH."
echo "  ok  buyer ETH $BUYER_ETH >= $MIN_BUYER_ETH ($BUYER_PAYS payments)"

ge "$BUYER_USDC" "$REQUIRED_USDC" \
  || die "buyer has $BUYER_USDC USDC, the sequence spends $REQUIRED_USDC.
  The agent holds $AGENT_USDC USDC; run scripts/topup_buyer.py again, or send USDC to
  $BUYER_ADDRESS."
echo "  ok  buyer USDC $BUYER_USDC >= $REQUIRED_USDC (0.50 + 0.75 + 0.25 + 0.25 + 0.50)"
echo

# ---------------------------------------------------------------- go
echo "READY TO RECORD"
echo
echo "Export this in BOTH terminals (MOCK_LLM must stay unset):"
echo
echo "  export PAYMENTS=base TURNSTYL_DB=./data/video.db LLM_MODEL=claude-haiku-4-5"
echo
echo "Optional, for a 100-column recording:  export TURNSTYL_WIDTH=100"
echo "Script for the take:                   docs/VIDEO_COMMANDS.md"
if [ -n "${MOCK_LLM:-}" ]; then
  echo
  echo "WARNING: MOCK_LLM is set to '${MOCK_LLM}' in this shell. Run 'unset MOCK_LLM'"
  echo "         in both terminals or the take will use canned outputs."
fi
