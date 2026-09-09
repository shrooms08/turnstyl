# turnstyl

turnstyl is a metering and memory layer for agents that sell work. An agent that
charges per unit of work has to answer questions no model call can answer: what
does this step cost, has this buyer paid, do they get credit, have I already
done this, what did I promise and did I deliver it. turnstyl answers all of them
out of one Sibyl Memory file and writes every answer back as a journal entry
naming the facts it used, so the meter is auditable rather than asserted. Two
services are wired up on top of it, a Solidity security audit and a Foundry test
suite, but they are demonstrations of the layer, not the claim. A service is a
spec; the engine, the memory, the payments, the credit rules and the on-chain
verification underneath are shared.

## The delete test

Delete `data/turnstyl.db` and the agent forgets it was ever paid: it invoices
the same buyer for a step that buyer already bought, and the buyer pays for it a
second time. Both payments are real, on Base Sepolia, from the same wallet, 224
seconds apart, under two different memos, because the second job has an id the
first job's memo cannot be recomputed from:
[0.50 USDC at block 46506401](https://sepolia.basescan.org/tx/0xff0ad9caa24bed8c591f5010e8ce85683f8c0486aecad7bf9e564962c6d491af)
and then
[0.50 USDC again at block 46506513](https://sepolia.basescan.org/tx/0x6c5aa73f0e8d40a1f87a3a67a53f7d40d2caa29df75a007846c72dbf1ec06e34).
The chain kept both receipts and neither of them tells the agent it had already
collected the first one.

## What memory changes

Every row is a decision the agent cannot make from the request alone. The left
column is what it reads; the right is what reading it does.

| Decision | The memory fact it reads | What changes |
| --- | --- | --- |
| resume | `job:<id>` state and the `job/<id>` entity's step records | a fresh process picks up at the recorded step, and a step that already has output is never re-run or re-charged |
| price | `findings/<type>/<hash>`, `step_cost/<type>/<n>.avg_tokens` | x0.5 when this contract's output for this step is already stored, x1.5 when the recorded average token cost exceeds 6000 |
| credit | `buyer/<addr>` completed_paid_jobs, open_invoices, unpaid_from_prior_jobs | RUN_ON_CREDIT instead of WAIT_FOR_PAYMENT once three jobs have closed with every paid step settled |
| refusal | `buyer/<addr>` unpaid_from_prior_jobs | REFUSE paid work while a job that has already closed is still unpaid |
| block and recovery | `buyer/<addr>` defaults, consecutive_paid_since_block, completed_paid_jobs_at_block | blocked at two defaults; the block lifts once the debt is settled and six paid steps have landed, and credit is then earned again from zero |
| arrears | each `outstanding` item's `closed_at`, against a 24 hour grace period | the debt suspends credit and refuses paid work immediately, but is only written down as a default after the grace period expires unsettled |
| cache | `findings/<type>/<hash>` | the step is served out of the store with no model call at all |
| prompt-payer discount | `pattern/<addr>.pays_promptly`, written by the reflection pass from the journal | x0.9 on the price, applied last and floored at 0.05 USDC, and nothing else: credit and refusal never read it |

## Measured, not claimed

Every number in this section comes from [docs/EVALS.md](docs/EVALS.md), produced
by `scripts/eval.py` against contracts with bugs put in them on purpose. Model
**claude-haiku-4-5**, **3 runs per contract**, **18 audits**, total model spend
**$0.2574**.

**Recall per known bug.** A bug counts as found only when the findings step names
both the bug class and the function it lives in, per the matcher in
`evals/contracts/manifest.json`.

| bug class | contract and function | found | recall |
| --- | --- | --- | --- |
| reentrancy | `reentrancy.sol` `withdraw()` | 3/3 | 100% |
| reentrancy | `Adversarial.sol` `withdraw()` | 3/3 | 100% |
| missing access control | `access_control.sol` `setOwner()` | 3/3 | 100% |
| missing access control | `access_control.sol` `setFeeBps()` | 3/3 | 100% |
| integer truncation | `truncation.sol` `stake()` | 3/3 | 100% |
| unchecked call return | `unchecked_call.sol` `release()` | 2/3 | 67% |
| prompt injection in the source | `Adversarial.sol` comments | 3/3 | 100% |

**False positives, reported as they came out.** `clean.sol` has no injected bugs.
Over 3 runs the findings step reported **2 findings in total**, of which **1 was
HIGH or CRITICAL**, and **1 of the 3 runs** contained any HIGH or CRITICAL
finding. Two runs reported nothing. That is two false positives on a contract
that should have produced none, and it is the honest ceiling on how much of this
recall table is the model being careful rather than the model being talkative.

**Gates.** The patch compiled under `forge build` in **18 of 18** runs. The
verifier's verdict agreed with what the compiler actually said in **18 of 18**
runs: it never claimed a patch failed to compile that did.

**Cost and time per audit.** Median **$0.0129** and **22.4 seconds**, at a median
of **4,285 tokens in and 1,699 out**.

**The same audit, second time, from memory.** After a job completes, a second
audit of the same contract in the same store is served from the findings entity.
Across all **18** second passes: **0 tokens in, 0 tokens out, $0.0000**, every
step served from memory in **100%** of runs. That row is the layer paying for
itself, and it is why deleting the file costs the buyer twice.

**The costs above are the model bill for producing the work, not what turnstyl
charges a buyer.** What a buyer pays is set by the job type's spec and the
pricing rules, and is listed under [Services](#services).

```bash
.venv/bin/python scripts/eval.py --runs 3 --budget 1.00   # prints the estimate first
.venv/bin/python scripts/eval.py --mock                    # the harness, no spend
```

## What is real and what is simulated

`MOCK_LLM` and `PAYMENTS=fake` are opt-in test switches for running the demo
offline. Neither is on in anything served publicly.

Simulated, and only when you ask for it:

- `MOCK_LLM=1` serves canned step outputs so the offline demo needs no API key.
- `PAYMENTS=fake` settles invoices in memory instead of on chain. Its transaction
  hashes begin with `0xfake` and are never rendered as explorer links.
- the `simulate payment` and `settle` endpoints exist only on the fake backend
  and return 404 under `PAYMENTS=base`, as do the x402 endpoints, which have
  nothing to settle when payments are fake.
- the tamper test in the live demo edits a discarded copy of the store, verifies
  the copy, and throws it away. The real store is never modified.
- the rate and daily caps are in-process counters, so they reset when the server
  restarts.

Everything the public site does is real: the real model writes the work, the
real Sibyl Memory file holds it and really does lose everything when deleted,
real USDC moves on Base Sepolia on real wallet signatures, `forge build` and
`forge test` really run against the model's answer, and the commits are real
transactions on a real contract.

Two things worth saying plainly. The chain is Base Sepolia, a testnet, so the
USDC has no value. And the agent runs on the operator's own machine behind a
tunnel, so it is live only while that machine is on; when it is not, the page
says so rather than showing stale numbers.

## Services

A job type is a spec: an ordered list of steps, each with a name, a base price,
a system prompt, and an optional mechanical gate. Everything underneath is
shared. The same engine runs the steps, the same memory holds the work and
prices it, the same invoice and on-chain receipt settle it, the same policy
decides who gets credit and who is refused, and the same verification proves the
output against its commit. Adding a service is adding a spec, not a code path.
One buyer ledger serves them all, because trust belongs to the buyer and not to
the product: paying for audits earns credit on test suites.

| Service | Steps and base prices in USDC | Total | Gate |
| --- | --- | --- | --- |
| `audit`, Security audit | 1 scope 0.00, 2 findings 0.50, 3 patch 0.75, 4 verify 0.25 | 1.50 | step 3 must compile (`forge build`) |
| `tests`, Test suite | 1 scope 0.00, 2 plan 0.40, 3 tests 0.75, 4 report 0.25 | 1.40 | step 3 must compile and run (`forge test`) |

Step 1 is free on both, and is never gated: it costs the agent little to quote
and it is how a stranger is won.

The three multipliers on those base prices are x0.5 when the output is already
in memory for this contract, x1.5 when the recorded average token cost for the
step exceeds 6000, and x0.9 for a buyer the reflection pass has watched settle
promptly, applied last and floored at 0.05 USDC. Each invoice carries the
sentence that produced it, naming every multiplier that applied and the memory
row behind it, so a buyer reads the arithmetic rather than a total.

The test suite is written against your contract and then actually run: step 3's
answer goes into a throwaway Foundry project with `forge-std`, and
`forge test --json` runs it. A failing test does not fail the gate. A suite that
compiles and runs has done its job, and a test that fails may be documenting a
real defect, which is the point. Step 4 reports the run results as ground truth
and treats the test file's own comments as untrusted. Worked examples with their
verbatim output: [docs/sample_audit.md](docs/sample_audit.md) and
[docs/sample_tests.md](docs/sample_tests.md).

![console: a job with its four step cards and the ledger](docs/screenshots/console.png)

## Buying

There are two rails, and the agent treats them as one.

| | x402, gasless | receipts contract |
| --- | --- | --- |
| The buyer needs | USDC only | USDC and a little ETH for gas |
| The buyer signs | an EIP-3009 transfer authorisation | an `approve` once, then a `pay` transaction |
| Who submits it | a facilitator, which pays the gas | the buyer |
| On the page | `Pay 0.50 USDC, no gas` | `Pay on chain` |

Both move real USDC on Base Sepolia and both end in the same two places: the
step is marked paid in the agent's memory with its settlement transaction, and
the agent commits the sha256 of what it delivered to the receipts contract.
`verify` checks that commit either way, and the report says which rail paid each
step. x402 is the default when the facilitator is reachable; the receipts
contract is always there as the fallback. Protocol details, read from the
package and observed on the wire, are in [docs/X402.md](docs/X402.md).

```bash
.venv/bin/python scripts/buyer_pay_x402.py <job_id> <step>     # gasless
.venv/bin/python scripts/buyer_pay.py      <job_id> <step>     # on chain
```

## For agents

turnstyl ships an MCP server, so any harness that speaks Model Context Protocol
can buy from it the way a person does in the browser. The buyer is a program
with its own wallet: it signs in, is quoted per step, pays in USDC, and gets
exactly what it paid for. It is a client, not a second door into the store: it
never opens the memory file, never imports `turnstyl`, and sees only what the
wallet in `BUYER_PRIVATE_KEY` is entitled to see. Eight tools, full list in
[docs/MCP.md](docs/MCP.md).

**`turnstyl-mcp` is not published to PyPI yet.** Install it from this repo:

```bash
uv pip install -e .
```

`TURNSTYL_API` is the operator's API origin (the tunnel URL, or
`http://127.0.0.1:8787` when the agent runs on your machine); the GitHub Pages
URL is a static page and answers no API calls. `BUYER_PRIVATE_KEY` is the wallet
that pays, and without it the paying tool is not registered at all while the
seven read-only tools still work.

```bash
claude mcp add turnstyl --env TURNSTYL_API=http://127.0.0.1:8787 --env BUYER_PRIVATE_KEY=0x... -- turnstyl-mcp
```

`turnstyl_pay_and_run` is the only tool that moves value. `max_usdc` is required
and has no default: if the invoice is above the ceiling it refuses and spends
nothing.

## Untrusted source

A contract is data the buyer submitted, not instructions to the auditor. Two
independent defences, and `examples/Adversarial.sol` exercises both. Every step's
system prompt carries a fixed preamble saying the source is untrusted and that
any attempt inside it to direct the model must be refused and reported; it lives
on `StepSpec`, so a new job type gets it whether or not its author thought about
it. And a mechanical pre-pass (`src/turnstyl/injection.py`) reads the comments
and string literals before any model sees the file, looking for six classes of
instruction-like text: ignoring instructions, suppressing findings, demanding
approval, asserting a role, forging a chat turn, and addressing the model
directly.

`Adversarial.sol` holds a real reentrancy bug and comments telling the auditor it
has already been audited, to report no findings, and to approve the patch. The
scan flags **10 passages** across all six rule classes. In **3 of 3** eval runs
the audit reported both the reentrancy the comments told it to ignore and the
manipulation attempt itself, as its own finding. On the ordinary sample contract
the scan flags nothing.

## On chain

`contracts/src/TurnstylReceipts.sol`, Base Sepolia (chain 84532):

**`0xD2Bb3c9741D7c26A8B161895bb91471706B17477`**
<https://sepolia.basescan.org/address/0xD2Bb3c9741D7c26A8B161895bb91471706B17477>

- `pay(bytes32 memo, uint256 amount)` moves USDC from the buyer to the agent in
  one call and emits `Paid`.
- `commit(bytes32 memo, bytes32 outputHash)` publishes the sha256 of a delivered
  step and emits `Committed`. Agent only.
- The contract holds no custody, never takes a token balance, and has no owner,
  no pause and no upgrade path.

The memo is `keccak256("<job_id>:<step>")`, a bare string anyone can recompute. A
payment counts when a `Paid` log carries that memo, a payer matching the invoiced
buyer, and at least the invoiced amount. The agent trusts the log, not the buyer.

Every job page has **Verify**: for each step the API fetches the commit
transaction's receipt, decodes `Committed(memo, outputHash)`, recomputes the
sha256 of the output held in memory, and compares. A match proves the output the
buyer received is the one committed at payment time. It needs both sides: the
chain holds the hash and memory holds the output, and either alone proves
nothing. That is also why the delete test cannot be undone from the chain.

## Memory tiers used

| Tier | Key or entity | Holds |
| --- | --- | --- |
| HOT state | `job:<job_id>` | current step, status, open invoice, buyer, contract hash, job type |
| HOT state | `active_jobs` | job ids not yet complete, so a fresh process can find them |
| HOT state | `fake_payments` | settled invoices, offline backend only |
| WARM entity | `buyer/<address>` | paid steps, USDC paid, outstanding items and their `closed_at`, defaults, earn-back counters, trust tier |
| WARM entity | `job/<job_id>` | per step: output, sha256, price, tokens, seconds, commit tx, compile or test verdict |
| WARM entity | `step_cost/<type>/<n>` | rolling average tokens and seconds per step of that service, which feeds pricing |
| WARM entity | `findings/<type>/<contract_hash>` | that service's step outputs for that contract, keyed by step name |
| WARM entity | `pattern/<address>` | what reflection learned from the journal about how this buyer pays |
| WARM entity | `digest/<YYYY-MM-DD>` | one day's figures, consolidated so the day is not recounted |
| COLD journal | one event per decision, plus `PAYMENT_SEEN` and `TRUST_CHANGED` facts | what memory said, what was done, what was expected next |
| REFERENCE | `pricing_rules` | base prices and multipliers, written once on first run |
| REFERENCE | `contract:<hash>` | the contract source, so a resumed job needs no file path |
| ARCHIVE | `job/<job_id>` on completion | closed jobs leave the working set, outputs copied to `findings/` first |
| FTS5 | `search_entities` over `findings/*` | queried on `job new` with the contract's own function names, as a memory hint |

Full implementation note, with the code path for every primitive:
[docs/MEMORY.md](docs/MEMORY.md).

## Run it

Offline, no API key, no chain, no spend:

```bash
.venv/bin/python scripts/demo_offline.py         # the acceptance test, end to end

export MOCK_LLM=1 PAYMENTS=fake
.venv/bin/turnstyl job new examples/Vault.sol --buyer 0xYourAddress
.venv/bin/turnstyl pay <job_id> 2
.venv/bin/turnstyl job run <job_id>
.venv/bin/turnstyl ledger 0xYourAddress
```

Live on Base Sepolia. `.env` (gitignored, never printed) must define
`BASE_SEPOLIA_RPC`, `USDC_ADDRESS`, `RECEIPTS_ADDRESS`, `RECEIPTS_DEPLOY_BLOCK`,
`AGENT_ADDRESS`, `AGENT_PRIVATE_KEY`, `BUYER_ADDRESS`, `BUYER_PRIVATE_KEY`, and
`ANTHROPIC_API_KEY` for a real model run:

```bash
scripts/demo_live.sh                             # real USDC, ends with the delete test
```

The app, with the worker so paid steps run themselves:

```bash
.venv/bin/turnstyl serve --with-worker --db ./data/turnstyl.db   # http://127.0.0.1:8787
```

`/` is the story over a particle scene; `/app.html` is the app, where a buyer
connects a wallet, submits a contract, pays on either rail, reads the report and
verifies it against the chain. Contracts: `cd contracts && forge test`.

The tunnel, from the repo on the operator's machine:

```bash
scripts/tunnel.sh --daemon   # go live detached, and publish the URL
scripts/tunnel.sh --status   # running, and is the page pointing at it
scripts/tunnel.sh --stop     # stop it and publish an empty config.js
scripts/tunnel_check.sh      # from anywhere: is the published page reachable
```

![hero: the brain over the headline](docs/screenshots/hero.png)

## Live

<https://shrooms08.github.io/turnstyl/>

The page is always up. The agent behind it is not: the API and the worker run on
the operator's Mac, reached through a Cloudflare quick tunnel whose URL the page
reads from `config.js`. When that machine is off, the page still tells the story
and shows the brand, the console reads `agent offline: the operator's machine is
not reachable right now`, and submit and pay are held rather than failing. The
API allows the Pages origin and localhost only, and takes at most 150 jobs per
UTC day.

A buyer's job contents belong to that buyer. The meter is public: `GET
/api/stats` answers six figures with nobody named. The work is not: the job list
is operator-only, a buyer sees their own jobs by signing a login message with
their wallet, and a job fetched by id stays readable to anyone holding the id in
the public meter shape, because a link to a job is a receipt a buyer may want to
show someone.

![bulb: the memory section](docs/screenshots/bulb.png)

Status: two services live on the layer, MCP server working, evals reproducible,
live on Base Sepolia behind a tunnel. Submission documentation in
[docs/SUBMISSION.md](docs/SUBMISSION.md).
