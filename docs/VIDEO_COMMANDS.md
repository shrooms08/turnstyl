# turnstyl demo — command script

Two ways to play the buyer. **The browser is the primary path**: the buyer
connects a wallet on the turnstyl page, submits the contract, and pays each
invoice with a signature. The terminal scripts below are the fallback for a take
without a wallet extension, and they produce the same on-chain receipts.

Run once, before recording:

```bash
scripts/video_prep.sh
```

The agent side is one process, with the worker in it:

```bash
cd ~/Projects/turnstyl
export PAYMENTS=base TURNSTYL_DB=./data/video.db LLM_MODEL=claude-haiku-4-5
unset MOCK_LLM
.venv/bin/turnstyl serve --with-worker --db ./data/video.db
```

Open http://127.0.0.1:8787 in a browser with Rabby (or MetaMask) holding the
buyer key, on Base Sepolia. The worker runs every step the moment its invoice
clears, so nothing on the agent side is typed during the take.

Every transaction hash shown is a real one on Base Sepolia. The explorer link
pattern is `https://sepolia.basescan.org/tx/<hash>`, and for the contract itself
`https://sepolia.basescan.org/address/0xD2Bb3c9741D7c26A8B161895bb91471706B17477`.

---

## Before the take: build the history

Credit is extended on three fully paid jobs, so the buyer arrives at the take
with a record. With the agent live, submit `examples/Vault.sol` three times and
pay every invoice of each job (the second and third are served from memory at
0.25 / 0.38 / 0.12 USDC, about a minute each). The ledger card then reads
`completed paid jobs 3`, `trust tier trusted`. Leave that page open. The take
opens with job 4.

## Browser flow (primary)

1. **Connect.** Top right, `Connect wallet`. Rabby asks for the account, then
   to switch to Base Sepolia (or add it). The bar shows the truncated address and
   the USDC balance.
2. **Submit job 4.** Drop `examples/Vault.sol` on the `new audit` panel and
   press `Submit for scope (free)`. Step 1 comes from memory; step 2 is invoiced
   at `0.25 USDC` (findings cached).
3. **Credit.** Do nothing. The buyer has three fully paid jobs, so the worker
   runs step 2 before the invoice clears: the findings card fills in with a
   `commit` link, the header reads `step 2 started on credit, invoice open`,
   and the timeline shows `RUN_ON_CREDIT … completed_paid_jobs=3 >= 3`.
4. **Pay step 3.** `Pay 0.38 USDC`. First time in this wallet:
   `approving 100 USDC once`, then `paying`, `confirming`, `confirmed`. The
   worker runs step 3; the invoice panel asks for step 4 at `0.12 USDC`.
5. **Pay step 4.** Same button. The job goes `complete` with step 2 still owed:
   the ledger shows it under `outstanding` and `defaults 1`.
6. **Refusal.** Submit `Vault.sol` again (job 5). Step 1 is free; the
   timeline then shows `REFUSE`: job 4 closed with step 2 unpaid.
7. **Settle the debt.** On the ledger card, the outstanding step 2 of job 4
   has its own `Pay 0.25 USDC` button. Pay it. Reconciliation clears it from the
   chain on the next pass; job 5's invoice becomes payable with
   `WAIT_FOR_PAYMENT … credit returns after 4 consecutive paid steps, currently 1`.
   The refusal is lifted; the credit is not.
8. **Earn it back.** Pay job 5's steps 2, 3 and 4 (`0.25`, `0.38`, `0.12`,
   all served from memory, all committed). The ledger reads `paid since default
   4`, `trust tier trusted`. Submit job 6: step 2 runs on credit again,
   `consecutive_paid_since_default=4` in the reason.
10. **Delete the memory.** In the agent terminal: `.venv/bin/turnstyl reset
    --db ./data/video.db`. The page goes red: `memory deleted`, every panel
    says what was lost, the `new audit` panel reads `the agent cannot take
    jobs while memory is missing`.
11. **The double charge.** Restart the agent (`scripts/tunnel.sh` again;
    serve creates an empty store), submit `Vault.sol` again: step 2 is invoiced
    at `0.50 USDC`, full price, to a buyer the agent now calls `new` with
    `completed paid jobs 0`. Pay it. `Paid` logs from the same buyer for the
    same work sit on the contract; only the agent's memory of them is gone.

Use `Your jobs` in the list to filter to the connected wallet, and `this is
you` marks the buyer on any job that is yours.

---

## Terminal fallback

Two terminals, side by side. **LEFT is the agent**, **RIGHT is the buyer**. The
agent never holds the buyer's key and the buyer never touches the agent's store.

In **both** terminals:

```bash
cd ~/Projects/turnstyl
export PAYMENTS=base TURNSTYL_DB=./data/video.db LLM_MODEL=claude-haiku-4-5
export TURNSTYL_WIDTH=100
unset MOCK_LLM
export BUYER=0x0964Dc1E37aCa77c6Df395DB7c0EeC848B1CefF8
```

Build the same history first: run steps 1-5 below three times over, paying
every invoice (`buyer_pay.py $JOB 2`, `3`, `4` and `job run` after each), then
begin the take at step 6 with a fourth job. Set `JOB1`... from the job ids as
they are printed: `.venv/bin/turnstyl job new` prints `job <id>  buyer <addr>`
on its first line. Under the current rule the credit beat (7) reads
`DECISION: RUN_ON_CREDIT, because step 2 is unpaid but buyer is trusted:
completed_paid_jobs=3 >= 3, open_invoices=0, unpaid_from_prior_jobs=0` and the
refusal names step 2, not step 4; the amounts on repeat jobs are 0.25 / 0.38 /
0.12 USDC.

---

## 1. Open the job. Step 1 is free.

**LEFT**
```bash
.venv/bin/turnstyl job new examples/Vault.sol --buyer $BUYER
```
Expect: a `STEP 1: scope` panel, an `INVOICE` panel for step 2 at `0.50 USDC`
naming the receipts contract, and the last line:
`DECISION: RUN_FREE, because step 1 (scope) is free at base 0.00 USDC …`

```bash
export JOB1=<the job id printed above>
```

## 2. Buyer pays step 2.

**RIGHT**
```bash
.venv/bin/python scripts/buyer_pay.py $JOB1 2
```
Expect: `PAID 0.50 USDC tx 0x…` and its explorer link. On the very first
payment it also prints `APPROVE tx 0x…` once.

## 3. Agent sees the payment, works, and commits the output.

**LEFT**
```bash
.venv/bin/turnstyl job run $JOB1
```
Expect: `STEP 2: findings` naming the reentrancy in `withdraw()`, a
`COMMITTED <sha> tx 0x…` line, an `INVOICE` for step 3 at `0.75 USDC`, and last:
`DECISION: RUN_PAID, because invoice 0x… for step 2 is settled at 0.50 USDC …`

## 4. Buyer pays step 3.

**RIGHT**
```bash
.venv/bin/python scripts/buyer_pay.py $JOB1 3
```
Expect: `PAID 0.75 USDC tx 0x…`

## 5. Agent runs step 3 and commits it.

**LEFT**
```bash
.venv/bin/turnstyl job run $JOB1
```
Expect: `STEP 3: patch` with `PATCH COMPILES: yes` in the panel subtitle, a
`COMMITTED` line, an `INVOICE` for step 4 at `0.25 USDC`, and last:
`DECISION: RUN_PAID, …`

## 6. Kill the agent. Restart it.

There is no daemon to restart: every `turnstyl` command is a fresh process that
holds nothing between invocations. Close the LEFT terminal, open a new one, and
re-export. The next command is the restart.

**LEFT (new shell)**
```bash
cd ~/Projects/turnstyl
export PAYMENTS=base TURNSTYL_DB=./data/video.db LLM_MODEL=claude-haiku-4-5
export TURNSTYL_WIDTH=100 BUYER=0x0964Dc1E37aCa77c6Df395DB7c0EeC848B1CefF8
export JOB1=<the same job id>
.venv/bin/turnstyl status
```
Expect: the `ACTIVE JOBS` table showing `$JOB1` at step `4/4`, awaiting payment,
recovered entirely from the store.

## 7. Step 4 runs on credit.

**LEFT**
```bash
.venv/bin/turnstyl job run $JOB1
```
Nobody paid for step 4. The buyer has two settled steps and nothing outstanding,
so the agent extends credit.

Expect: `STEP 4: verify`, a `COMMITTED` line, a `COMPLETE` panel, and last:
`DECISION: RUN_ON_CREDIT, because step 4 is unpaid but buyer is trusted:
paid_steps=2 >= 2, open_invoices=0, unpaid_from_prior_jobs=0`

## 8. Same contract again. The scope is free and comes from memory.

**LEFT**
```bash
.venv/bin/turnstyl job new examples/Vault.sol --buyer $BUYER
export JOB2=<the new job id>
```
Expect: a dim `memory hint: prior findings for contract … (this contract) hold
scope, findings, patch, verify`, a `STEP 1: scope` panel whose subtitle reads
`from memory (cached)`, an `INVOICE` for step 2 at `0.25 USDC` whose `priced`
row says `findings cached for this contract hash`, and
`DECISION: RUN_FREE, …`

## 9. The agent refuses to sell.

**LEFT**
```bash
.venv/bin/turnstyl job run $JOB2
```
Step 4 of the first job was delivered on credit and never paid, so the job
closed with a default against this buyer.

Expect: no step panel at all, and last:
`DECISION: REFUSE, because buyer left 1 step(s) unpaid on a completed job; …
unpaid_from_prior_jobs=1, defaults=1, …`

## 10. Buyer settles the old debt.

**RIGHT**
```bash
.venv/bin/python scripts/buyer_pay.py $JOB1 4
```
Note this pays a step of `$JOB1`, a job that is already closed. Expect:
`PAID 0.25 USDC tx 0x…`

## 11. The agent reconciles from chain and quotes the repeat price.

**LEFT**
```bash
.venv/bin/turnstyl job run $JOB2
```
Expect: a `RECONCILED step 4 of job … settled on chain` line, the `INVOICE` for
step 2 still at `0.25 USDC`, and last:
`DECISION: WAIT_FOR_PAYMENT, … this buyer has 1 default(s) on record, so work is
sold up front; credit returns after 4 consecutive paid steps, currently 1 …`

The refusal is lifted. The credit is not — that has to be earned back.

## 12. Buyer pays the discounted step.

**RIGHT**
```bash
.venv/bin/python scripts/buyer_pay.py $JOB2 2
```
Expect: `PAID 0.25 USDC tx 0x…` — half the first price, for the same work.

## 13. The agent sells an answer it already has.

**LEFT**
```bash
.venv/bin/turnstyl job run $JOB2
```
Expect: `STEP 2: findings` with `from memory (cached)` and `0 tokens` in the
subtitle — no model call was made — a `COMMITTED` line, and
`DECISION: RUN_PAID, …`

## 14. Delete the memory.

**LEFT**
```bash
.venv/bin/turnstyl reset --db ./data/video.db
```
Expect: an `ABOUT TO DELETE` panel listing the size, state keys, entities and
journal events, then exactly one line:
`memory deleted: …/data/video.db (<n> entities, <m> journal events gone)`

## 15. The agent meets the buyer it has been paid by four times.

**LEFT**
```bash
.venv/bin/turnstyl job new examples/Vault.sol --buyer $BUYER
export JOB3=<the new job id>
```
Expect: no `memory hint` line, a `STEP 1: scope` panel that is **not** cached, an
`INVOICE` for step 2 at `0.50 USDC` — full price for work already delivered and
paid for — and `DECISION: RUN_FREE, …`

## 16. The double charge.

**RIGHT**
```bash
.venv/bin/python scripts/buyer_pay.py $JOB3 2
```
Expect: `PAID 0.50 USDC tx 0x…`

Open that hash next to the one from step 2 at
`https://sepolia.basescan.org/tx/<hash>`. Both payments are on chain, from the
same buyer, for the same work on the same contract. Only the agent's memory of
the first is gone.

## 17. The ledger the agent now believes.

**LEFT**
```bash
.venv/bin/turnstyl ledger $BUYER
```
Expect: a `LEDGER` panel reading `paid steps 0`, `paid 0.00 USDC`,
`defaults on record 0`, `trust tier new` — a first-time customer who has in fact
paid this agent 2.25 USDC across three jobs.

---

## Reset between takes

```bash
scripts/video_prep.sh
```
