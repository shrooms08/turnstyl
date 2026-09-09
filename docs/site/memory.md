# Memory

This is the layer. Everything else on this site is something that layer makes
possible.

turnstyl keeps one Sibyl Memory file — `./data/turnstyl.db`, SQLite, gitignored,
chmod 0600 by the SDK — under the tenant `turnstyl`, a plain string passed to
`MemoryClient.local` rather than the SDK's default UUID, so turnstyl's rows never
mingle with another local consumer's. Override the path per process with
`TURNSTYL_DB`.

Every key below is written by `TurnstylStore` in
[`memory.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py),
which is the only module that talks to the SDK. Nothing else in turnstyl
hand-builds a memory key; it builds a model from
[`schema.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/schema.py)
and calls `.model_dump()`.

## The three lines

**Persist.** Job state, per-step outputs and their sha256, step token and time
costs, pricing rules, contract source, and a per-buyer ledger of paid steps,
completed paid jobs, outstanding items, defaults and trust tier are written as
they happen, one journal event per decision.

**Recall, in a fresh session.** A new process opens the same store and reads
`active_jobs` and `job:<job_id>` to find where the work stopped, `job/<job_id>`
to see which steps already have output, and `buyer/<address>` to see what this
buyer has paid and still owes — with no state carried over from the process it
replaced.

**Changes the agent's decision by.** What it reads sets the price it quotes,
whether it runs at all, and whether it calls a model or serves the answer it
already holds.

## The tiers

| Tier | What lives there | Why that tier |
| --- | --- | --- |
| **HOT** state | `job:<job_id>`, `active_jobs`, `x402_payments`, `fake_payments` | read on every pass of the worker and on every API request; the smallest thing that answers "where is this job" |
| **WARM** entity | `buyer/<address>`, `job/<job_id>`, `step_cost/<type>/<n>`, `findings/<type>/<hash>`, `pattern/<address>`, `digest/<date>` | the working set: bigger documents, read when a decision needs them, indexed for search |
| **COLD** journal | one event per decision, plus `PAYMENT_SEEN` and `TRUST_CHANGED` | append-only history; read in bulk by reflection and the digest, never on the hot path of a single step |
| **REFERENCE** | `pricing_rules`, `contract:<hash>` | written once and read back verbatim; not a decision and not a fact about a buyer |
| **ARCHIVE** | `job/<job_id>` on completion | a finished job leaves the working set without leaving the store, so `active_jobs` stays the live queue |
| **FTS5** | `search_entities` over `findings/*` | full-text over cached work products, queried with the contract's own function names |

## Every key turnstyl writes

### HOT state

| Key | Body | Written by | Read for |
| --- | --- | --- | --- |
| `job:<job_id>` | `JobState`: current step, status, open invoice, buyer, contract hash, job type, timestamps | `put_job_state` ([memory.py:325](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L325)) | resuming a job in a fresh process; every API read of a job |
| `active_jobs` | list of job ids not yet complete | `add_active_job` / `remove_active_job` ([memory.py:338](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L338)) | the worker's queue; the job list |
| `x402_payments` | `{"<job_id>:<step>": {"tx", "payer"}}` | `record_x402` ([payments.py:87](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/payments.py#L87)) | `check_paid`, because an EIP-3009 transfer leaves no `Paid` log on the receipts contract |
| `fake_payments` | `{"<job_id>:<step>": tx_hash}` | `FakePayments.mark_paid` ([payments.py:262](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/payments.py#L262)) | the offline backend only; never present under `PAYMENTS=base` |

### WARM entities

| Entity | Body | Written by | Read for |
| --- | --- | --- | --- |
| `buyer/<address>` | `BuyerLedger`: paid steps, USDC paid, open invoices, outstanding items with their `closed_at`, defaults, earn-back and unblock counters, trust tier, job ids | `put_buyer` ([memory.py:365](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L365)) | every credit, refusal and trust decision |
| `job/<job_id>` | `JobEntity`: per step, the output, its sha256, price, tokens, seconds, pay tx, commit tx, gate verdict | `put_job_entity` ([memory.py:411](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L411)) | resume, the job page, the report, and `verify` |
| `step_cost/<type>/<n>` | `StepCost`: `runs`, `avg_tokens`, `avg_seconds` | `record_step_cost` ([memory.py:437](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L437)) | the ×1.5 surcharge on the next quote for that step |
| `findings/<type>/<hash>` | `FindingsEntity`: `slots`, a map from step name to that step's output | `put_findings` ([memory.py:486](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L486)) | the cache hit: the ×0.5 discount and serving the step with no model call |
| `pattern/<address>` | `BuyerPattern`: payments observed, median seconds invoice→payment, steps per job, jobs observed, default rate, `pays_promptly`, `basis` | `put_pattern` ([memory.py:384](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L384)) | the ×0.9 prompt-payer discount, and nothing else |
| `digest/<YYYY-MM-DD>` | `DigestEntity`: one day's figures | `put_digest` ([memory.py:400](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L400)) | `GET /api/digest` and `turnstyl digest`, so a counted day is one entity read instead of a walk of the journal |

### Reference and archive

| Key | Body | Read for |
| --- | --- | --- |
| `pricing_rules` | base prices and the three multipliers, written once on first run by `ensure_pricing_rules` ([memory.py:281](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L281)) | so the rules the store was priced under are recorded in the store |
| `contract:<hash>` | the submitted source, verbatim | so a resumed job can run a fresh step without the operator re-supplying the `.sol` file |
| archived `job/<job_id>` | the completed `JobEntity` | the job page, the report and `verify` for a closed job; read back over a read-only connection by `read_archived_job` ([memory.py:63](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L63)), because the SDK archives entities but exposes no reader for them |

## What "per job type" means

Two of those families are namespaced by the service that wrote them, and one
deliberately is not.

```text
step_cost/audit/1   step_cost/audit/2   step_cost/audit/3   step_cost/audit/4
step_cost/tests/1   step_cost/tests/2   step_cost/tests/3   step_cost/tests/4

findings/audit/<contract_hash>     slots: scope, findings, patch, verify
findings/tests/<contract_hash>     slots: scope, plan, tests, report

buyer/<address>                    one ledger, every service
```

A test suite for a contract is not an audit of it, and step 3 of one is not
priced by step 3 of the other, so work products and cost history carry the type
as a path segment (`findings_name`, `step_cost_name` in
[schema.py:142](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/schema.py#L142)).
The buyer ledger carries no type at all, because trust belongs to the buyer and
not to the product: paying for audits earns credit on test suites, and the
offline demo asserts exactly that.

The `slots` keys are **data**, not schema. They come from whichever job type
wrote the row, so a service added tomorrow will use step names `schema.py` has
never heard of. What the model validates is the values, never the key set —
which is what makes adding a service a spec change and not a migration.

Rows written before job types existed carry no type and are read as `audit`,
which is what they were; `TurnstylStore._legacy_row`
([memory.py:423](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L423))
falls back to the bare name, and the live store still holds four such
`step_cost/<n>` rows and one bare `findings/<hash>` alongside their namespaced
successors.

## Which decision each row changes

This is the table the whole project turns on. The left column is what the agent
reads; the right is what reading it does.

| Decision | The memory it reads | What changes |
| --- | --- | --- |
| **resume** | `job:<id>` state and the `job/<id>` entity's step records | a fresh process picks up at the recorded step, and a step that already has output is never re-run or re-charged |
| **price ×0.5** | `findings/<type>/<hash>` holds this step | the quote is halved, and the step is served out of the store with no model call |
| **price ×1.5** | `step_cost/<type>/<n>.avg_tokens > 6000` | the quote carries a surcharge, naming the average and the run count it was computed over |
| **price ×0.9** | `pattern/<addr>.pays_promptly` | a tenth off, applied last, floored at 0.05 USDC. Credit and refusal never read it |
| **credit** | `buyer/<addr>`: `completed_paid_jobs`, `open_invoices`, `unpaid_from_prior_jobs` | `RUN_ON_CREDIT` instead of `WAIT_FOR_PAYMENT` once three jobs have closed with every paid step settled |
| **refusal** | `buyer/<addr>.unpaid_from_prior_jobs` | `REFUSE` paid work while a job that has already closed is still unpaid |
| **arrears** | each `outstanding` item's `closed_at`, against the 24 hour grace period | the debt suspends credit and refuses paid work immediately, and is written down as a default only after the grace period expires unsettled |
| **block and recovery** | `buyer/<addr>`: `defaults`, `consecutive_paid_since_block`, `completed_paid_jobs_at_block` | blocked at two defaults; the block lifts once the debt is settled and six paid steps have landed, and credit is then earned again from zero |
| **cache** | `findings/<type>/<hash>` | the step is served out of the store with no model call at all |
| **memory hint** | FTS5 over `findings/*`, queried with the contract's own function names | the job page says the agent has seen these functions before, on a contract whose bytes it has never seen |

## The seven primitives, and where each one lives

| Primitive | Code path | What it does |
| --- | --- | --- |
| **recall** | `get_job_state` ([memory.py:319](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L319)) and `get_buyer` ([memory.py:355](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L355)), at the top of `Engine.run` ([engine.py:340](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/engine.py#L340)) and `Engine.new_job` ([engine.py:174](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/engine.py#L174)) | reads the HOT state and WARM ledger every decision is derived from |
| **entities** | `put_job_entity`, `put_buyer`, `record_step_cost`, `put_findings`, `put_pattern`, `put_digest` | the six WARM families above |
| **temporal** | `journal` ([memory.py:497](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L497)) writes one COLD event per decision; `read_journal` ([memory.py:505](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L505)) replays them newest first | an append-only record of what memory said, what was done, and what was expected next |
| **reflection** | per decision, `Engine._advance` ([engine.py:604](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/engine.py#L604)) builds the `evaluated` list *before* acting; over time, `reflect.reflect` ([reflect.py:212](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/reflect.py#L212)) reads up to 2000 events hourly from `Worker._reflect` ([worker.py:129](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/worker.py#L129)) | the agent's own account of why it charged or refused, and what watching a buyer has taught it |
| **consolidation** | per job, `Engine._complete` ([engine.py:1241](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/engine.py#L1241)); per day, `digest.build` ([digest.py:213](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/digest.py#L213)) | closed work becomes the cache that prices the next job; a counted day becomes one entity read |
| **semantic search** | `search_findings` ([memory.py:474](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L474)), called from `Engine._memory_hints` ([engine.py:515](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/engine.py#L515)) on every `job new` | FTS5 over `findings/*` with the contract's own function names |
| **archive** | `archive_job_entity` ([memory.py:415](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L415)), read back by `read_archived_job` ([memory.py:63](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py#L63)) | a finished job leaves the working set without leaving the store |

**Summarization is deliberately not claimed.** Every journal event carries a
one-sentence summary, but those are f-string templates in `engine.py`, not a
model summarising anything. Nothing in `src/turnstyl/` calls a model to
summarise, so the claim would not survive a look at the code.

## What a journal event actually holds

One event per decision, in four parts: what was **evaluated** (the memory rows,
quoted), what was **acted** on, what is expected **forward**, and an `extra`
block carrying the job, buyer, step, decision, price and one-line summary. Read
verbatim from a store this documentation was written against:

```json
{
  "ts": "2026-09-09T08:06:24.921Z",
  "decision": "RUN_PAID",
  "step": 4,
  "evaluated": [
    "entity buyer/0x0964…eff8 -> completed_paid_jobs=0, paid_steps=2, paid_usdc=1.25, open_invoices=0, unpaid_from_prior_jobs=0, trust_tier=new",
    "job:d3098e24af3d -> current_step=4, status=awaiting_payment, job_type=audit",
    "entity findings/audit/7c779e61ee00... -> step 4 (verify) is not cached"
  ],
  "acted": [
    "RUN_PAID step 4 (verify) via the model; output_sha256=9a1d5df65a4a..., tokens=1681, seconds=0.0",
    "entity step_cost/audit/4 -> runs=1, avg_tokens=1681, avg_seconds=0.00",
    "entity buyer/0x0964…eff8 -> paid_steps=3, paid_usdc=1.50, consecutive_paid_since_default=3",
    "entity findings/audit/7c779e61ee00... -> filled ['findings', 'patch', 'scope', 'verify']",
    "entity buyer/0x0964…eff8 -> job closed fully paid; completed_paid_jobs=1",
    "archived entity job/d3098e24af3d",
    "active_jobs -> removed d3098e24af3d"
  ],
  "forward": [
    "job d3098e24af3d is complete; audit work cached under audit/7c779e61ee00b90008..."
  ]
}
```

The `evaluated` list is built **before** the decision is taken, not after, which
is what makes the event a record of the reasoning rather than a rationalisation
of the outcome. `policy.py` being pure is the other half of that: given the rows
in `evaluated`, the same decision comes out again.

Two event kinds record a fact rather than a choice, and the app renders them
dimmer for that reason: `PAYMENT_SEEN`, written by
[`events.payment_seen`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/events.py#L42)
the moment any rail first sees an invoice settled and carrying the invoice's own
`issued_at`, and `TRUST_CHANGED`.

## What reflection actually learns

`reflect.reflect` reads the journal and writes one `pattern/<address>` per buyer.
It changes exactly one thing downstream: a tenth off the price. It cannot make a
buyer trusted, cannot lift a block, and cannot forgive a debt.

A `pattern` entity from the live store:

```json
{
  "address": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
  "payments_observed": 3,
  "median_seconds_invoice_to_payment": 28.2,
  "steps_per_job_median": 4.0,
  "jobs_observed": 1,
  "default_rate": 0.0,
  "pays_promptly": true,
  "last_reflected_at": "2026-09-09T07:48:01.575Z",
  "basis": "measured from the invoice to the step running, which is an upper bound on when the payment landed"
}
```

`basis` is not decoration. Where the journal has `PAYMENT_SEEN` events the
figure is read straight off one event (`ts - issued_at`) and the basis reads
`payment_seen`. Where it does not — a journal written before those events
existed, as above — the measurement falls back to invoice-issue against the
event that ran the paid step, which is *when the agent noticed* and therefore an
upper bound. Saying which was used is the difference between a median and a
median you can trust.

## Why the chain cannot rebuild it

The chain holds exactly two things: that a payment of some amount arrived under
some memo, and that some 32-byte output hash was committed. It does not hold the
step outputs, the prices quoted or the reasons for them, the token and time cost
history that sets those prices, or any of the trust reasoning about a buyer.

And the memo is `keccak256("<job_id>:<step>")` — an opaque hash that means
something only if you already hold the job id it was built from, which is
precisely what a deleted database no longer has. Reconstruction would recover a
list of payments to unknown invoices, not an agent that knows what it sold.

That is why the [delete test](#overview) cannot be undone from the chain, and
why [verify](#verify) needs both halves.

## The live store, as of 2026-09-09

Read from `./data/turnstyl.db` while writing this page.

| | |
| --- | --- |
| Size | 2,781,184 bytes — most of it SQLite page and FTS5 index overhead, not turnstyl's rows |
| Records | 193: 13 state documents + 24 entities + 156 journal events |
| Entities | 3 `buyer`, 12 `step_cost`, 5 `findings`, 2 `pattern`, 2 `digest` |
| Alongside those | 10 archived job entities and 3 reference documents |
| State keys | `active_jobs`, `x402_payments`, and one `job:<id>` per job |
| References | `pricing_rules` and two `contract:<hash>` documents |

The 12 `step_cost` rows are 4 for `audit`, 4 for `tests`, and 4 bare-numbered
legacy rows. The 5 `findings` rows are `audit/` and `tests/` for each of two
contracts, plus one legacy bare-hash row.

Deeper implementation note, with the code path for every primitive:
[docs/MEMORY.md](https://github.com/shrooms08/turnstyl/blob/master/docs/MEMORY.md).
