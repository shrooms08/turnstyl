# turnstyl

turnstyl is a metered AI agent that does multi-step work, gets paid per step in USDC on Base, and keeps job state, findings, and a buyer ledger in Sibyl Memory so it can resume any job, price any step, and decide who gets credit.

Status: day 3: live payments and output commits on Base Sepolia

## What it does

A buyer submits a Solidity contract for a four-step audit. Step 1 (scope) is
free. Steps 2 (findings), 3 (patch) and 4 (verify) are sold per step in USDC.
The agent decides what to charge and who to trust entirely from what it
remembers, and every decision it makes is written back to memory as a journal
event naming the facts it used.

Delete the memory and the agent forgets it was ever paid. That failure is
reproduced on purpose, on chain, as the last beat of the live demo — see
**The delete test** below.

## Architecture

### Memory (Sibyl Memory, one SQLite store at `./data/turnstyl.db`)

| Tier | Key | Holds |
| --- | --- | --- |
| HOT | `job:<job_id>` | job state: current step, status, open invoice, buyer, contract hash |
| HOT | `active_jobs` | the job ids that are not complete, so a restart can find them |
| HOT | `fake_payments` | settled invoices, offline backend only |
| WARM | `buyer/<address>` | paid steps, USDC paid, outstanding invoices, defaults, trust tier |
| WARM | `job/<job_id>` | per-step output, sha256, price, tokens, seconds, commit tx |
| WARM | `step_cost/<n>` | rolling average tokens and seconds per step, which feeds pricing |
| WARM | `findings/<contract_hash>` | the four outputs, so a repeat contract is served from memory |
| REFERENCE | `pricing_rules` | base prices and multipliers, written once on first run |
| REFERENCE | `contract:<hash>` | the contract source, so a resumed job needs no file path |
| COLD | journal | one event per decision: what memory said, what the agent did, what it expects next |

A completed job's entity is archived and its outputs are copied into the
findings entity, which is what makes the second audit of the same contract cheap.

### Policy (`src/turnstyl/policy.py`, pure functions, no I/O)

Prices in USDC: step 1 free, step 2 0.50, step 3 0.75, step 4 0.25.

- **half price** when this contract's output for that step is already in memory
- **1.5x** when the recorded average token cost for that step exceeds 6000
- **RUN_FREE** — step 1, never gated
- **RUN_PAID** — the invoice for this step is settled on chain
- **RUN_ON_CREDIT** — unpaid, but the buyer has 2+ paid steps, nothing
  outstanding, and no default on record
- **WAIT_FOR_PAYMENT** — unpaid and credit not earned
- **REFUSE** — the buyer left work unpaid when a previous job closed

A buyer who lets a job close with work unpaid carries a permanent `defaults`
count. Paying the debt lifts the refusal, but not the credit: they buy per step,
up front, from then on.

### Receipts contract

`contracts/src/TurnstylReceipts.sol` on Base Sepolia (chain 84532):

**`0xD2Bb3c9741D7c26A8B161895bb91471706B17477`**
<https://sepolia.basescan.org/address/0xD2Bb3c9741D7c26A8B161895bb91471706B17477>

- `pay(bytes32 memo, uint256 amount)` moves USDC straight from the buyer to the
  agent and emits `Paid`. The contract never takes custody.
- `commit(bytes32 memo, bytes32 outputHash)` lets the agent publish the sha256 of
  what it delivered, and emits `Committed`. Agent only.
- No owner, no pause, no upgrade path.

The memo is `keccak256("<job_id>:<step>")` — a bare string anyone can recompute
without knowing turnstyl's conventions. A payment counts when a `Paid` log
carries that memo, a payer matching the invoiced buyer, and at least the
invoiced amount. The agent trusts the log, never the buyer's word.

## Running it

### Offline — no API key, no chain

```bash
.venv/bin/python scripts/demo_offline.py          # seven-beat acceptance test

export MOCK_LLM=1 PAYMENTS=fake
.venv/bin/turnstyl job new examples/Vault.sol --buyer 0xYourAddress
.venv/bin/turnstyl status
.venv/bin/turnstyl pay <job_id> 2
.venv/bin/turnstyl job run <job_id>
.venv/bin/turnstyl ledger 0xYourAddress
```

### Live — Base Sepolia

`.env` must define `BASE_SEPOLIA_RPC`, `USDC_ADDRESS`, `RECEIPTS_ADDRESS`,
`RECEIPTS_DEPLOY_BLOCK`, `AGENT_ADDRESS`, `AGENT_PRIVATE_KEY`, `BUYER_ADDRESS`
and `BUYER_PRIVATE_KEY`. It is gitignored and must stay that way.

```bash
scripts/demo_live.sh                              # nine-beat run on Base Sepolia
```

It runs against `./data/demo_live.db` with `PAYMENTS=base MOCK_LLM=1`, so it
never touches the main store and costs no model spend. Every transaction hash it
sees lands in `data/demo_live_txs.txt`.

Driving it by hand:

```bash
export PAYMENTS=base MOCK_LLM=1
.venv/bin/python scripts/topup_buyer.py           # fund the buyer, idempotent
.venv/bin/turnstyl job new examples/Vault.sol --buyer $BUYER_ADDRESS
.venv/bin/python scripts/buyer_pay.py <job_id> 2  # buyer approves + pays on chain
.venv/bin/turnstyl job run <job_id>               # agent verifies, works, commits
```

### The delete test

The last beat of the live demo deletes the database and starts over. The agent
re-invoices 0.50 USDC for a step this buyer already paid for, the buyer pays it,
and both payments sit on chain while the agent remembers only the second one.

Nothing in turnstyl rebuilds memory from chain events, deliberately. The chain
proves a payment happened; it does not tell an agent what it already knew.

## Contracts

```bash
cd contracts && forge test
```

`forge init --no-git` vendored `forge-std` as plain files, so `contracts/lib/` is
tracked in this repo and **a fresh clone builds with no `forge install` step**.

## Layout

| Path | What |
| --- | --- |
| `src/turnstyl/schema.py` | every stored shape, as pydantic models |
| `src/turnstyl/policy.py` | pricing and credit decisions, pure |
| `src/turnstyl/memory.py` | Sibyl Memory wrapper and the typed store |
| `src/turnstyl/llm.py` | the only module that calls a model |
| `src/turnstyl/payments.py` | `FakePayments` and `BasePayments` |
| `src/turnstyl/engine.py` | the orchestrator |
| `src/turnstyl/cli.py` | `turnstyl` command line |
| `contracts/` | Foundry project for `TurnstylReceipts` |
| `examples/Vault.sol` | the deliberately reentrant sample contract |
