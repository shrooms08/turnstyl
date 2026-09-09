# Quickstart

Everything on this page runs offline: no API key, no chain, no spend. Every
command was run from a fresh clone before it was written down.

## Install

turnstyl is Python 3.12 and is installed with [uv](https://docs.astral.sh/uv/).
There is no Node, no npm and no build step anywhere in the project.

```bash
git clone https://github.com/shrooms08/turnstyl
cd turnstyl
uv venv --python 3.12
uv pip install -e ".[mcp]"
```

`.[mcp]` adds the four dependencies the MCP server needs (`mcp`, `httpx`,
`eth-account`, `x402`). Plain `uv pip install -e .` is enough if you are not
going to buy over MCP.

Everything below runs the interpreter in the virtualenv explicitly — `.venv/bin/python`,
`.venv/bin/turnstyl` — so nothing depends on which shell you activated.

## Prove the memory file works

The smoke test writes in one process and reads in another. Run the two modes as
separate commands; that separation is the whole point, because a process that
verifies its own writes proves only that it has RAM.

```bash
.venv/bin/python scripts/smoke_memory.py write
.venv/bin/python scripts/smoke_memory.py read
```

The second command ends with `READ OK: cross-process persistence confirmed`. It
creates `./data/turnstyl.db` if it is not there.

## Run the offline demo

This is the acceptance test: thirteen beats, end to end, each one driving the
real CLI as a separate subprocess against a throwaway database in `/tmp`.
`MOCK_LLM=1` and `PAYMENTS=fake` are forced inside the script, so no model is
called and no chain is touched.

```bash
.venv/bin/python scripts/demo_offline.py
```

It prints PASS or FAIL per beat and ends with `RESULT: PASS — all 13 beats
passed`. It covers resume across processes, pricing out of memory, the cache,
credit, arrears, defaults, blocking and earning back, the injection scan, the
prompt-payer discount, the daily digest, and trust carrying across services.

## Submit a job and pay for it

Two switches make this offline: `MOCK_LLM=1` serves canned step outputs, and
`PAYMENTS=fake` settles invoices in memory. Neither is on in anything served
publicly.

```bash
export MOCK_LLM=1 PAYMENTS=fake

.venv/bin/turnstyl job new examples/Vault.sol --buyer 0x0964dc1e37aca77c6df395db7c0eec848b1ceff8
```

That runs step 1 free and prints its output, then the invoice for step 2:

```text
      step  2 (findings)
    amount  0.50 USDC
      memo  turnstyl:4cf587213784:step2
    status  unpaid
    priced  base 0.50 for step 2 (findings); no discount (not cached), no
            surcharge (step_cost/2 avg_tokens=0 over 0 run(s)); buyer
            trust_tier=new = 0.50 USDC
buyer runs  .venv/bin/turnstyl pay 4cf587213784 2
```

Take the job id from that output and settle the invoice, then run the paid step:

```bash
.venv/bin/turnstyl pay <job_id> 2
.venv/bin/turnstyl job run <job_id>
.venv/bin/turnstyl ledger 0x0964dc1e37aca77c6df395db7c0eec848b1ceff8
```

`turnstyl pay` exists only on the fake backend — on Base a payment is a `Paid`
log on the receipts contract and nothing else counts. Repeat `pay` and `job run`
for steps 3 and 4 to close the job, or start the worker below and it will run
each paid step for you.

Other useful commands:

```bash
.venv/bin/turnstyl types                    # the services on offer, with prices
.venv/bin/turnstyl status                   # the active jobs held in memory
.venv/bin/turnstyl digest                   # what the agent did today
.venv/bin/turnstyl reflect                  # read the journal, write the buyer patterns
```

## Run the app locally

```bash
export MOCK_LLM=1 PAYMENTS=fake
.venv/bin/turnstyl serve --with-worker --db ./data/turnstyl.db
```

That serves on <http://127.0.0.1:8787> and runs the worker loop in a background
thread of the same process, so a step runs as soon as its invoice settles.

| Path | What it is |
| --- | --- |
| `/` | the story page, with the particle scene |
| `/app.html` | the app: submit a contract, pay, read the report, verify it |
| `/docs.html` | these docs |
| `/api/status` | what the agent is and which store it has open |

Submitting from the browser needs a wallet: the page asks you to connect one and
sign a login message, because a job is created in a wallet's name and its
contents belong to that wallet from that moment on. With no wallet to hand, use
the CLI above — it writes to the same store, and the app will show the job.

To watch the app against the store the CLI just wrote, keep `--db ./data/turnstyl.db`
on both.

## The delete test, offline

```bash
.venv/bin/turnstyl reset --db ./data/turnstyl.db
```

It reports what the store held before removing it, and refuses any path outside
`./data/`. Start the agent again and it is a stranger: the ledger is empty, the
cache is gone, and the next job for a contract it has already audited is quoted
at full price and run from scratch. See [Overview](#overview) for what that costs
a buyer when the payments were real.

## Next

- [Concepts](#concepts) — the vocabulary and the exact rules
- [Services](#services) — the two job types, and how to add a third
- [Buying](#buying) — the two payment rails, for real USDC on Base Sepolia
- [Deploy](#deploy) — running the agent behind a tunnel with the page on Pages
