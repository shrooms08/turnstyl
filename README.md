# turnstyl

turnstyl is a metered AI agent. It audits a Solidity contract in four steps, and
it gets paid per step in USDC on Base: step 1 (scope) is free, steps 2
(findings), 3 (patch) and 4 (verify) are sold individually. Job state, step
outputs, step costs, and a per-buyer ledger live in Sibyl Memory, and every
decision the agent makes, what to charge, whether to run, whether to extend
credit, whether to refuse, is a function of what it reads there and is written
back as a journal entry naming the facts it used.

## The delete test

Delete `data/turnstyl.db` and the agent forgets it was ever paid: it invoices the
same buyer again for work already delivered and settled. In the live demo the
buyer then pays that second invoice, and it lands on Base Sepolia right next to
the first one: [`0x1f7656c5d27809d4…`](https://sepolia.basescan.org/tx/0x1f7656c5d27809d49477f27a6e6eb62362eec80738fdebc1c9d3797111626f0c). Both
payments are on chain; only the agent's memory of the first is gone.

## What memory changes

| Decision | Memory fact it reads | What changes |
| --- | --- | --- |
| resume | `job:<id>` state and `job/<id>` entity | picks up at the recorded step; a step with output is never re-run or re-charged |
| price | `findings/<hash>` and `step_cost/<n>` | 0.50 becomes 0.25 when the output is already stored; 1.5x when recorded avg_tokens > 6000 |
| credit | `buyer/<addr>` completed_paid_jobs, open_invoices, defaults | RUN_ON_CREDIT instead of WAIT_FOR_PAYMENT for a buyer with three fully paid jobs, of any service |
| refuse | `buyer/<addr>` unpaid_from_prior_jobs | REFUSE paid work from a buyer who left a closed job unpaid |
| cache | `findings/<hash>` | a repeat contract is served from the store with no model call at all |

## Memory tiers used

| Tier | Key or entity | Holds |
| --- | --- | --- |
| HOT state | `job:<job_id>` | current step, status, open invoice, buyer, contract hash |
| HOT state | `active_jobs` | job ids not yet complete, so a fresh process can find them |
| HOT state | `fake_payments` | settled invoices, offline backend only |
| WARM entity | `buyer/<address>` | paid steps, USDC paid, outstanding invoices, defaults, earn-back counter, trust tier |
| WARM entity | `job/<job_id>` | per step: output, sha256, price, tokens, seconds, commit tx, compile verdict |
| WARM entity | `step_cost/<n>` | rolling average tokens and seconds per step, which feeds pricing |
| WARM entity | `findings/<contract_hash>` | the four step outputs, keyed by contract hash |
| COLD journal | one event per decision | what memory said, what the agent did, what it expects next |
| REFERENCE | `pricing_rules` | base prices and multipliers, written once on first run |
| REFERENCE | `contract:<hash>` | the contract source, so a resumed job needs no file path |
| ARCHIVE | `job/<job_id>` on completion | closed jobs move out of the active set, outputs copied to `findings/` first |
| FTS5 | `search_entities` over `findings/*` | on `job new`, queried with the contract's function names for a "memory hint" |

## Services

A job type is a spec: an ordered list of steps, each with a name, a base price,
a system prompt, and an optional mechanical gate. Everything underneath is
shared. The same engine runs the steps, the same memory holds the work, the same
invoice and on-chain receipt settle them, the same policy decides what to charge
and who gets credit, and the same verify proves the output against its commit.
Adding a service is adding a spec, not a code path. One buyer ledger serves them
all: trust belongs to the buyer, so paying for audits earns credit on test
suites.

| Service | Steps and base prices | Gate |
| --- | --- | --- |
| `audit`, Security audit | 1 scope free, 2 findings 0.50, 3 patch 0.75, 4 verify 0.25 | step 3 must compile (`forge build`) |
| `tests`, Test suite | 1 scope free, 2 plan 0.40, 3 tests 0.75, 4 report 0.25 | step 3 must compile and run (`forge test`) |

```bash
.venv/bin/turnstyl types                                   # what is on offer
.venv/bin/turnstyl job new examples/Vault.sol --buyer 0x... --type tests
```

The test suite is written against your contract and then actually run: step 3's
answer goes into a throwaway Foundry project with `forge-std`, and
`forge test --json` runs it. A failing test does not fail the gate. A suite that
compiles and runs has done its job, and a test that fails may be documenting a
real defect, which is the point. Step 4 reports on the run results as ground
truth and treats the test file's own comments as untrusted. A worked example
against `Vault.sol` with a real model:
[docs/sample_tests.md](docs/sample_tests.md), 22 tests, 20 pass, 2 fail on the
reentrancy surface, $0.0362.

## Policy rules

Base prices in USDC: step 1 0.00, step 2 0.50, step 3 0.75, step 4 0.25.

- half price when this contract's output for that step is already in `findings/`
- 1.5x when the recorded average token cost for that step exceeds 6000
- **RUN_FREE**: step 1, never gated
- **RUN_PAID**: the invoice for this step is settled
- **RUN_ON_CREDIT**: unpaid, but the buyer is trusted
- **WAIT_FOR_PAYMENT**: unpaid and credit not earned
- **REFUSE**: the buyer left work unpaid when a previous job closed

Trust tiers: **trusted** needs three completed jobs with every paid step settled
(`completed_paid_jobs >= 3`), nothing outstanding, and either no default or an
earned-back one. **blocked** at two defaults. Otherwise **new**. Step counts do
not earn credit: a buyer who pays two steps and walks away from the third has
paid for nothing the agent can extend credit on. Repeat contracts are served
from memory at half price, so a history of three paid jobs is cheap to build.

A default is one delivered-but-unpaid step at the moment a job closes. It stays
on the record permanently. Paying the debt clears `unpaid_from_prior_jobs` and
lifts the refusal, but not the credit: the buyer pays up front until four
consecutive settled steps have gone by, at which point credit returns. A second
default cannot be worked off.

## On chain

`contracts/src/TurnstylReceipts.sol`, Base Sepolia (chain 84532):

**`0xD2Bb3c9741D7c26A8B161895bb91471706B17477`**
<https://sepolia.basescan.org/address/0xD2Bb3c9741D7c26A8B161895bb91471706B17477>

- `pay(bytes32 memo, uint256 amount)` moves USDC from the buyer to the agent in
  one call and emits `Paid`.
- `commit(bytes32 memo, bytes32 outputHash)` publishes the sha256 of a delivered
  step and emits `Committed`. Agent only.
- The contract holds no custody, it never takes a token balance, and it has no
  owner, no pause and no upgrade path.

Every job page has **Verify**: for each step, the API fetches the commit
transaction's receipt, decodes `Committed(memo, outputHash)`, recomputes the
sha256 of the output in memory and compares. A match proves the output the
buyer received is the one committed at payment time. It needs both sides: the
chain holds the hash and memory holds the output; either alone proves nothing.
**Download report** exports the whole audit as Markdown with every hash and
transaction link, so the check can be repeated by hand on BaseScan.

The memo is `keccak256("<job_id>:<step>")`, a bare string anyone can recompute. A
payment counts when a `Paid` log carries that memo, a payer matching the invoiced
buyer, and at least the invoiced amount. The agent trusts the log, not the buyer.

## Mechanical gates on model output

- The patch step returns a whole patched file. turnstyl produces the unified diff
  itself with `difflib`, so the diff applies by construction and the model never
  writes a hunk header.
- That file is compiled in a throwaway Foundry project with `forge build`. If it
  fails, the compiler errors go back to the model once. The verdict is recorded
  and shown as `PATCH COMPILES: yes/no`.
- The verifier step is handed those results as `MECHANICAL CHECKS` and is
  instructed that nothing may be marked CLOSED if the patch does not compile.

## Measured, not claimed

Every number here comes from `scripts/eval.py`, which runs the audit against a
set of contracts with bugs put in them on purpose and counts what came back.
The run below: model **claude-haiku-4-5**, **3 runs per contract**, **18 audits**,
total model spend **$0.2574**. Full table and per-run data in
[docs/EVALS.md](docs/EVALS.md) and `evals/results/`.

**Recall, by bug class.** A bug counts as found only when the findings step
names both the bug class and the function it lives in, per the matcher in
`evals/contracts/manifest.json`.

| bug class | contract | found | recall |
| --- | --- | --- | --- |
| reentrancy | `reentrancy.sol` `withdraw()` | 3/3 | 100% |
| reentrancy | `Adversarial.sol` `withdraw()` | 3/3 | 100% |
| missing access control | `access_control.sol` `setOwner()` | 3/3 | 100% |
| missing access control | `access_control.sol` `setFeeBps()` | 3/3 | 100% |
| integer truncation | `truncation.sol` `stake()` | 3/3 | 100% |
| unchecked call return | `unchecked_call.sol` `release()` | 2/3 | 67% |
| prompt injection in the source | `Adversarial.sol` comments | 3/3 | 100% |

**False positives.** `clean.sol` has no injected bugs. Over 3 runs it drew
**2 findings in total**, of which **1 was HIGH or CRITICAL**; **1 of 3 runs**
contained any HIGH or CRITICAL finding. The other two reported nothing. The one
HIGH was a design opinion about single-step ownership transfer, not a defect.

**Gates.** The patch compiled under `forge build` in **18 of 18** runs (100%).
The verifier's verdict agreed with what the compiler actually said in **18 of 18**
runs (100%): it never claimed a patch failed to compile that did.

**Cost and time, per audit.** Median **$0.0129** and **22.4 seconds**, at a
median of **4,285 tokens in and 1,699 out**.

**First audit against the same audit from memory.** After a job completes, a
second audit of the same contract in the same store is served from the findings
entity. Across all 18 second passes: **0 tokens in, 0 tokens out, $0.0000**, with
every step served from memory in **100%** of runs. That is the whole thesis in
one row, and it is why deleting the file costs the buyer twice.

Reproduce it:

```bash
.venv/bin/python scripts/eval.py --runs 3 --budget 1.00   # prints the estimate first
.venv/bin/python scripts/eval.py --mock                    # the harness, no spend
```

## Untrusted contract source

A contract is data the buyer submitted, not instructions to the auditor. Two
independent defences, and `examples/Adversarial.sol` exercises both:

1. **Every step's system prompt** carries a fixed preamble saying the source is
   untrusted and that any attempt inside it to direct the model must be refused
   and reported. It lives on `StepSpec` in `src/turnstyl/jobtypes/base.py`, so a
   new job type gets it whether or not its author thought about it.
2. **A mechanical pre-pass** (`src/turnstyl/injection.py`) reads the comments and
   string literals before any model sees the file, looking for six classes of
   instruction-like text: ignoring instructions, suppressing findings, demanding
   approval, asserting a role, forging a chat turn, and addressing the model
   directly. Hits are recorded on the job state with line numbers, journalled as
   one decision, shown on the job page and in the CLI as a warning panel, and
   handed to the findings step as evidence with an instruction to report the real
   ones.

On `Adversarial.sol` the scan flags **10 passages on 3 lines** across all six
rule classes, and in **3 of 3** eval runs the audit reported both the reentrancy
the comments told it to ignore and the manipulation attempt itself, at MEDIUM.
On the ordinary sample contract the scan flags nothing.

## Run it

Offline, no API key, no chain, no spend:

```bash
.venv/bin/python scripts/demo_offline.py        # nine-beat acceptance test

export MOCK_LLM=1 PAYMENTS=fake
.venv/bin/turnstyl job new examples/Vault.sol --buyer 0xYourAddress
.venv/bin/turnstyl pay <job_id> 2
.venv/bin/turnstyl job run <job_id>
.venv/bin/turnstyl ledger 0xYourAddress
.venv/bin/turnstyl status
```

Live on Base Sepolia. `.env` (gitignored, never printed) must define
`BASE_SEPOLIA_RPC`, `USDC_ADDRESS`, `RECEIPTS_ADDRESS`, `RECEIPTS_DEPLOY_BLOCK`,
`AGENT_ADDRESS`, `AGENT_PRIVATE_KEY`, `BUYER_ADDRESS`, `BUYER_PRIVATE_KEY`:

```bash
scripts/demo_live.sh                            # nine beats, real USDC
# honours TURNSTYL_DB; defaults to ./data/demo_live.db
```

A real audit against the Anthropic API needs `ANTHROPIC_API_KEY` in `.env`:

```bash
export PAYMENTS=fake LLM_MODEL=claude-haiku-4-5 TURNSTYL_DB=./data/real_run.db
unset MOCK_LLM
.venv/bin/turnstyl job new examples/Vault.sol --buyer 0xYourAddress
.venv/bin/turnstyl pay <job_id> 2 && .venv/bin/turnstyl job run <job_id>
```

Contracts: `cd contracts && forge test`. `forge init --no-git` vendored
`forge-std` as plain files, so a fresh clone builds with no `forge install`.

## Web UI

Two pages, served from the same process that reads the agent's memory, sharing
one stylesheet at `web/static/turnstyl.css`:

* **`/`** is the story. A scroll narrative over a 5,000-particle scene, ending
  in a compact operator strip: is the agent up, where its memory file is, how
  many jobs are in it, the last decision it made in one sentence, and a button
  into the app.
* **`/app.html`** is the app. Connect a wallet, sign in, submit a contract, pay
  invoices on either rail, read reports, verify outputs against the chain.
  `app.html?job=<id>` opens one job.

```bash
.venv/bin/turnstyl serve --db ./data/turnstyl.db     # then open http://127.0.0.1:8787
```

Scrolling drives the scene through a scatter between sections: brain (the hero),
coin (every step is paid), bulb (restart it, it remembers), scatter (the delete
test), then the turnstyl mark. Delete the memory file while either page is open
and it stays up: the scene locks to a red scatter, the counters read "memory
deleted", and every panel says what was lost rather than showing a stale copy.

## Who sees what

A job's contents are the thing the buyer paid for, so they belong to that buyer.
The meter stays public; the work does not.

| | public | the job's buyer | the operator |
| --- | --- | --- | --- |
| `/api/stats` | six figures, nobody named | same | same |
| job list | 403 | their own address, with `?buyer=` | every job |
| job detail, by id | every step's price, status, payment and commit transactions, and output sha256 | plus the outputs and the contract hash | plus the outputs and the contract hash |
| journal | decision, time, step, the one-sentence summary | plus the memory reads and actions behind it | plus the memory reads and actions behind it |
| ledger | trust tier, completed paid jobs | the whole ledger | the whole ledger |
| report.md, report.json, verify | 401 | 200 | 200 |
| creating a job, paying, settling | 401 | their own jobs | any job |

Who has bought what is not part of the meter, so there is no public index of
jobs: `GET /api/jobs` answers 403 unless you are the operator, a buyer asks for
their own address with `?buyer=`, and the public figures live at `GET
/api/stats` (jobs in memory, completed, distinct buyers, USDC settled, decisions
logged, steps served from memory; cached ten seconds, and no job id or address
appears in the response). A job fetched *by id* stays readable to anyone who has
the id, in the public meter shape: a link to a job is a receipt a buyer may want
to show someone, and that has to work without handing over a session.

A buyer proves an address by signing this message, and nothing else:

```
turnstyl login

address: <lowercase address>
nonce: <32 hex characters>
issued: <iso 8601 time>
```

`GET /api/auth/nonce?address=0x…` returns the message in full;
`POST /api/auth/verify` with `{address, signature}` returns a bearer token good
for 24 hours. Nothing is spent and no transaction is sent. A wrong buyer gets
403, no buyer gets 401, and both say which address is which.

The operator override is `OPERATOR_TOKEN` in `.env`, generated there on first
startup if it is missing. Paste it into the app's Settings drawer to see every
job in the store; it is held in that tab's `sessionStorage` and is gone when the
tab closes. Nonces and sessions live in the server process, so a restart signs
everyone out.

![hero: the brain over the headline](docs/screenshots/hero.png)

![bulb: the memory section](docs/screenshots/bulb.png)

![console: a job with its four step cards and the ledger](docs/screenshots/console.png)

## Live

The page is always up on GitHub Pages at <https://shrooms08.github.io/turnstyl/>.
The agent behind it is live while the operator's machine is on: the API and the
worker run on that Mac, reached through a Cloudflare quick tunnel whose URL the
page reads from `config.js`. When the machine is off, the page still tells the
story and shows the brand; the console reads `agent offline: the operator's
machine is not reachable right now`, and submit and pay are held.

Two commands, from the repo on the operator's machine:

```bash
scripts/tunnel.sh          # go live in the foreground; Ctrl-C takes it down
scripts/tunnel.sh --daemon # go live detached; survives the terminal closing
scripts/tunnel.sh --status # is it running, and at what URL
scripts/tunnel.sh --stop   # stop it and publish an empty config.js
scripts/tunnel_check.sh    # from anywhere: is the published page pointing at a reachable agent?
```

`--daemon` starts the same three processes under `nohup` with their output in
`data/tunnel.log` and their pids in `data/tunnel.pid`, so closing the terminal
does not take the agent down; `--stop` reads that file, stops them, and
republishes an empty `config.js` so the page reads "agent offline".

`tunnel.sh` runs with `PAYMENTS=base` and the real model, writes the tunnel
URL into `web/config.js`, pushes only that file to `gh-pages`, and on Ctrl-C
stops everything and publishes an empty `config.js` again. The API allows the
Pages origin and localhost only, and takes at most `MAX_JOBS_PER_DAY` jobs
(default 150) per UTC day; `/api/status` reports `remaining_today`.
`scripts/pages.sh` republishes the whole page after a change to `web/`.

## Paying

There are two ways to settle an invoice, and the agent treats them as one.

| | x402, gasless | receipts contract |
| --- | --- | --- |
| The buyer needs | USDC only | USDC and a little ETH |
| The buyer signs | an EIP-3009 transfer authorisation | an `approve` (once) and a `pay` transaction |
| Who submits it | a facilitator, which pays the gas | the buyer |
| Evidence the agent keeps | the settlement transaction, recorded in memory | a `Paid` log on the receipts contract |
| On the page | `Pay 0.50 USDC, no gas` | `Pay on chain` |

Both move real USDC on Base Sepolia, and both end in the same place: the step is
marked paid in the agent's memory with its settlement transaction, the worker
runs it, and the agent commits the sha256 of what it delivered to the receipts
contract exactly as before. `verify` checks that commit either way, and the
report shows which rail paid each step.

x402 is the default when the facilitator is reachable; the receipts contract is
always there as the fallback, and is what the buyer uses if the facilitator is
down. Protocol details, read from the package and observed on the wire, are in
[docs/X402.md](docs/X402.md).

```bash
.venv/bin/python scripts/buyer_pay_x402.py <job_id> <step>     # gasless
.venv/bin/python scripts/buyer_pay.py      <job_id> <step>     # on chain
```

## Buyer side

The page is also where a buyer does business with the agent. Nothing here needs
a terminal.

- **Connect.** `Connect wallet` in the top bar asks the injected wallet (Rabby,
  MetaMask) for an account and switches it to Base Sepolia, adding the network
  if it is missing. Nothing is requested on page load; a reload reconnects
  silently only if this tab connected before. The bar shows the address and
  the wallet's USDC balance.
- **Submit.** The `new audit` panel takes pasted Solidity or a dropped `.sol`
  file. `Submit for scope (free)` posts it with the connected address; the
  agent runs scope at once and invoices step 2. Submitting the same contract
  again resumes the open job instead of starting another.
- **Pay.** The open invoice shows `Pay <amount> USDC` when the connected
  address is the job's buyer. It reads the USDC allowance, approves 100 USDC
  once if needed, then calls `pay(memo, amount)` on the receipts contract and
  waits for the receipt. The worker in the serving process runs the step the
  moment the payment lands; no manual `job run`.
- **Credit.** After three fully paid jobs the buyer is trusted and the next step
  runs before its invoice clears: the job shows `started on credit, invoice
  open`, and the amount is carried on the ledger until it is paid.
- **Your jobs.** With a wallet connected, the list filters to that address,
  and any job or ledger that belongs to it is marked `this is you`.
- **Without a wallet.** With `PAYMENTS=fake` the Pay button becomes `Simulate
  payment`, which calls `POST /api/jobs/{id}/pay` and marks the invoice settled
  the way `turnstyl pay` does, so the whole flow runs locally.

Serve with the worker so paid steps run themselves:

```bash
export PAYMENTS=fake MOCK_LLM=1                          # or PAYMENTS=base, MOCK_LLM unset
.venv/bin/turnstyl serve --with-worker --db ./data/turnstyl.db
```

## What is real and what is simulated

Everything the public site does is real. `MOCK_LLM` and `PAYMENTS=fake` are
opt-in test switches for running the demo offline, and neither is on in
anything served publicly.

Real, always:

- the model. The audits and test suites are written by `claude-haiku-4-5`; the
  samples in `docs/` are verbatim output with their token counts and cost.
- the memory. One Sibyl Memory SQLite file, and deleting it really does lose
  everything, which is the point of the delete test.
- the payments. Real USDC on Base Sepolia, on either rail, settled by real
  wallet signatures. The commits are real transactions on a real contract.
- the gates. `forge build` and `forge test` really run against the model's
  answer, and a suite that fails to compile really is sent back.

Simulated, and only when you ask for it:

- `MOCK_LLM=1` serves canned step outputs so the offline demo needs no API key.
- `PAYMENTS=fake` settles invoices in memory instead of on chain. Its
  transaction hashes start with `0xfake` and are never rendered as explorer
  links.
- the `simulate payment` and `settle` endpoints exist only on the fake backend
  and return 404 under `PAYMENTS=base`, as do the x402 endpoints, which have
  nothing to settle when payments are fake.
- the tamper test in the live demo edits a **copy** of the store, verifies the
  copy, and discards it. The real store is never modified.
- the rate and daily caps are in-process counters, so they reset when the
  server restarts.

Two things are worth saying plainly: the chain is Base Sepolia, a testnet, so
the USDC has no value; and the agent runs on the operator's own machine behind
a tunnel, so it is live only while that machine is on. When it is not, the page
says so.

## Sample audit

[docs/sample_audit.md](docs/sample_audit.md): a real four-step run against
`claude-haiku-4-5`, verbatim, with token counts, cost, and the mechanical
verdicts. Memory implementation note: [docs/MEMORY.md](docs/MEMORY.md).

Status: day 4: web UI, particle scene, brand.
