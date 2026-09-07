# turnstyl memory note

Persist: every job's state, per-step outputs and their sha256, step token and
time costs, pricing rules, contract source, and a per-buyer ledger of paid
steps, completed paid jobs, outstanding invoices, defaults and trust tier are
written to Sibyl Memory as they happen, one journal event per decision.

Recall (fresh session): a new process opens the same store, reads `active_jobs`
and `job:<job_id>` to find where the work stopped, reads `job/<job_id>` to see
which steps already have output, and reads `buyer/<address>` to see what this
buyer has paid and is owed, with no state carried in the process it replaced.

Changes the agent's decision by: the price it quotes (half when
`findings/<contract_hash>` already holds that step, 1.5x when
`step_cost/<n>.avg_tokens` exceeds 6000), whether it runs at all (RUN_PAID,
RUN_ON_CREDIT, WAIT_FOR_PAYMENT or REFUSE, chosen from the buyer entity's
completed_paid_jobs, open_invoices, defaults, consecutive_paid_since_default and
consecutive_paid_since_block),
and whether it calls a model or serves the answer it already has.

## What breaks when memory is deleted

The agent re-invoices a buyer for steps that buyer already paid for, re-runs
work it already did, and treats a proven payer as a stranger with no credit. The
payments are still on chain and the outputs are still hashed there, but nothing
on chain tells the agent which invoice it already collected, so it charges again.

## Primitives used

| Primitive | Where it is called | What it does |
| --- | --- | --- |
| recall | `TurnstylStore.get_job_state` / `get_buyer` (`memory.py`), called at the top of `Engine.run` and `Engine.new_job` | reads the HOT state document and WARM buyer entity that every decision is derived from |
| entities | `TurnstylStore.put_job_entity` / `put_buyer` / `record_step_cost` / `put_findings` (`memory.py`) | the four WARM entity families: job, buyer, step_cost, findings. Work products and cost history are namespaced by job type (`findings/<type>/<hash>`, `step_cost/<type>/<n>`); the buyer ledger deliberately is not |
| temporal | `TurnstylStore.journal` (`memory.py`) writes one COLD event per decision, plus two that record a fact rather than a choice: `PAYMENT_SEEN` when any rail first sees an invoice settled and `TRUST_CHANGED` when a buyer's tier actually moves (`events.py`); `read_journal` reads them back newest first | an append-only record of what memory said, what was done, what was expected next, and what happened |
| reflection | two things. Per decision: `Engine._advance` builds the `evaluated` list before acting, so each journal event states the facts it rested on (`engine.py`). Over time: `reflect.reflect` (`reflect.py`) reads the journal hourly from `Worker._reflect` (`worker.py`) or on demand from `turnstyl reflect`, and writes `("pattern", <address>)` | the agent's own account of why it charged or refused, and what watching a buyer over many jobs has taught it |
| consolidation | two things. Per job: `Engine._complete` (`engine.py:808`) copies the four step outputs into `findings/<contract_hash>` and archives the job entity via `TurnstylStore.archive_job_entity` (`memory.py:293`). Per day: `digest.build` (`digest.py`) writes `("digest", <YYYY-MM-DD>)` from `turnstyl digest` and `GET /api/digest` | closed jobs leave the working set and their outputs become the cache that prices the next audit; a counted day becomes one entity read instead of a walk of the journal |
| semantic search (FTS5) | `TurnstylStore.search_findings` (`memory.py:329`), called from `Engine._memory_hints` (`engine.py:297`) on every `job new` | queries `findings/*` with the contract's own function names and prints any hit as a dim "memory hint" line |

## Arrears: owed, but not yet a default

`Engine._complete` no longer increments `defaults` when a job closes with
delivered work unpaid. It stamps `closed_at` on each carried `OutstandingItem`
instead, which makes it an arrears item. `unpaid_from_prior_jobs` still counts
it, so paid work is refused and credit is suspended from that moment; what is
withheld is the word "default" and the counters it resets.

`engine.promote_arrears(store, buyer)` is now the only place a default is
recorded. It reads the ledger, finds arrears whose `closed_at` is more than
`GRACE_HOURS` old (`TURNSTYL_GRACE_HOURS`, 24 by default), and for each one
increments `defaults`, resets both earn-back clocks, snapshots
`completed_paid_jobs_at_block` if that takes the buyer to two, recomputes the
tier and journals one `ARREARS_DEFAULTED` event. It clears `closed_at` as it
goes, so the same debt cannot be promoted twice.

It is called from `Engine._reconcile`, which runs at the top of every decision
path, and from the worker's buyer sweep on every pass. Both call it *after*
settlement is checked, so a debt paid in the last minute clears rather than
defaulting on the same pass.

`policy` stays free of clocks: `arrears`, `overdue`, `hours_left` and
`arrears_line` all take `now` as an argument, and `decide` accepts an optional
`now` so a caller with a clock gets the countdown and one without gets the
deadline named instead.

## Blocked is a stop, not an ending

Two defaults block a buyer. The block holds while `unpaid_from_prior_jobs > 0`,
and then while `consecutive_paid_since_block < 6`. Both are fields on the buyer
entity, and the second is incremented on every settled paid step while the tier
is blocked (in `Engine._execute`'s money branch and in `PaymentBackend.reconcile`
when a debt is settled), and reset to 0 by any new default alongside
`consecutive_paid_since_default`.

What a blocked buyer can still do: submit jobs, and be served the free scope
step, because `policy.decide` returns RUN_FREE before it looks at the tier at
all. What they cannot do: take work on credit, or have an unpaid step run. What
they *can* do, and what makes the block recoverable: have a step they have
already paid for served, once nothing is outstanding. That is the only route to
six, and `policy.decide` returns RUN_PAID for it with a reason saying which of
the six it is.

`policy.unblock_terms` writes the requirement once, in one of two wordings.
A buyer still carrying a debt has to settle it before anything else matters. A
buyer who has settled everything is not being refused for an old debt at all:
they are buying up front until the count is met, and telling them to "settle
0.00 USDC outstanding" would contradict their own ledger.

```
blocked after 2 defaults: settle 0.45 USDC outstanding, then 6 more
consecutive paid steps to be served again

blocked after 2 defaults: this step must be paid up front, 4 more paid
steps to be served normally
```

`Engine._advance` writes the matching summary sentence: a debt is named with
the job it is owed on, and a blocked buyer with nothing outstanding is told how
many paid steps are left rather than that they left work unpaid.

The REFUSE reason, the CLI ledger card, `trust.unblock` in
`GET /api/buyers/<address>` and the app's "how to be served again" line are all
that one function, so the terms cannot drift between where they are enforced and
where they are quoted.

When the block lifts the buyer is **new**, not trusted. `completed_paid_jobs` is
not reset (it is a lifetime count and the journal would contradict a reset), but
`completed_paid_jobs_at_block` snapshots it when the block begins, and
`policy.credit_jobs` subtracts it for a buyer with two or more defaults. So
credit is earned back on jobs completed *since* the block, by the same
three-fully-paid-jobs rule that applies to a stranger.

## Reflection: what the journal taught the agent

The ledger records what a buyer owes. The pattern entity records how they
behave, and it is derived from the journal and nothing else.

`reflect.reflect(store)` (`src/turnstyl/reflect.py`) reads up to 2000 journal
events, pairs each settled invoice with the event that created it, and writes
one `("pattern", <address>)` per buyer it saw:

| field | how it is derived |
| --- | --- |
| `median_seconds_invoice_to_payment` | median over the pairs below |
| `payments_observed` | how many pairs it could time |
| `steps_per_job_median` | median distinct steps run per job for this buyer |
| `default_rate` | `defaults / (completed_paid_jobs + defaults)` from the ledger |
| `pays_promptly` | `True` when the median is under 300s over at least 3 payments; `False` when it is not; `None` below 3 |
| `last_reflected_at`, `basis` | when it looked, and how the median was measured |

Every rail writes a `PAYMENT_SEEN` event the moment it first sees an invoice
settled, and that event carries the invoice's own `issued_at`. So the wait is
read off one event, `ts - issued_at`, on the fake backend, the receipts contract
and x402 alike, and `basis` reads `payment_seen`.

A journal written before those events existed has none, and for those buyers the
older measurement still applies: *issued* at the event that ran step N-1 (which
is the event that created the invoice for step N), *settled* at the `PAID_X402`
event when there is one and otherwise the `RUN_PAID` event, which is when the
agent noticed and is an upper bound. `basis` always says which was used, so a
median is never read as more precise than it is.

It is run by `Worker._reflect` once an hour and by `turnstyl reflect` on demand.
It writes nothing but the pattern entity, and it changes exactly one thing: a
`x0.9` multiplier in `policy.price`, applied after the cache and cost
multipliers and floored at 0.05 USDC. Credit and refusal do not read it at all.
Nothing is inferred from a single payment: below three observations the entity
records the counts it has, `pays_promptly` stays `None`, and the price is
exactly what it was before.

## Digest: one day, consolidated once

`digest.build(store, days)` (`src/turnstyl/digest.py`) counts jobs opened and
completed, USDC settled, steps run against steps served from memory, model spend
estimated from the token counts the agent recorded, new buyers, trust changes,
defaults, refusals, injection flags raised, the median seconds from payment to
output, and the top three contracts by repeat audits. All of it comes from the
journal and the entities already in the store.

Two of those figures mean exactly what they say because of the fact events
above. **Trust changes** counts `TRUST_CHANGED` events in the window, which is a
count of tiers that actually moved; the standing snapshot sits beside it as
`buyers_above_new`. **Payment to output** is the median from the `PAYMENT_SEEN`
event to the event that ran the step it paid for, and it is reported as "not
enough data" rather than as a median until there are at least three
observations.

The one write is `("digest", <YYYY-MM-DD>)`. Recomputing a past day gives the
same answer, so the entity is a cache with a date for a key, and it is what
makes the consolidation primitive do real work rather than appear in a table.
`turnstyl digest [--days N]` prints it; `GET /api/digest` returns it, complete
for the operator and counts-only for everyone else.

## Verify: what the chain proves, and what it cannot

`GET /api/jobs/{id}/verify` decodes the `Committed(memo, outputHash)` event from
each step's commit transaction and compares it with the sha256 of the output
held in memory. A match proves the output the buyer received is the one the
agent committed at payment time. The proof needs both stores: the chain has the
hash, memory has the output; either alone proves nothing, which is also why the
delete test cannot be undone from chain.

## Memory per service, trust per buyer

turnstyl sells more than one service, and a job type is only a spec (see the
README). Two of the four entity families are namespaced by type and two are not,
and the split is the design:

| Key | Namespaced | Why |
| --- | --- | --- |
| `findings/<type>/<contract_hash>` | yes | a test suite for a contract is not an audit of it; caching one as the other would serve the wrong work |
| `step_cost/<type>/<n>` | yes | step 3 of an audit and step 3 of a test suite cost different amounts to run, so neither should price the other |
| `job/<job_id>`, `job:<job_id>` | carries `job_type` | one job is one service; the spec is resolved from the row |
| `buyer/<address>` | no | trust belongs to the buyer, not the product. Three fully paid audits earn credit on a test suite |

Rows written before job types existed carry no type and are read as `audit`,
which is what they were. Nothing is migrated: reads fall back to the untyped
name, writes always use the namespaced one.

## Why not rebuild memory from chain

The chain holds two things: that a payment of some amount arrived under a memo,
and that some 32-byte output hash was committed. It does not hold the findings,
the step outputs, the prices quoted or why, the token and time cost history that
sets those prices, or any of the trust reasoning about a buyer. A memo is
`keccak256("<job_id>:<step>")`, an opaque hash that only means something if you
already hold the job id it was built from, which is exactly what a deleted
database no longer has. Reconstruction would recover a list of payments to
unknown invoices, not an agent that knows what it sold.

## Files

- Database: `./data/turnstyl.db` (SQLite, gitignored, chmod 0600 by the SDK).
  Overridable per process with `TURNSTYL_DB`; the live demo uses
  `./data/demo_live.db`.
- Tenant: `turnstyl`, a plain string passed to `MemoryClient.local`, not the
  SDK default UUID, so turnstyl's rows never mingle with another local consumer.
- Size after the nine-beat live demo: `data/demo_live.db` is 384K (335,872
  bytes) holding 3 entities, 2 state documents, 2 reference documents and 1
  journal event. It is small because the demo's last beat deletes the store and
  starts one fresh job. That is the delete test, and what survives it is the
  point. A store carrying a finished four-step audit
  (`data/real_run3.db`) is 512K: 6 entities, 1 archived job, 3 state documents,
  2 reference documents, 4 journal events. Most of both figures is SQLite page
  and FTS5 index overhead, not turnstyl's rows.
