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
completed_paid_jobs, open_invoices, defaults and consecutive_paid_since_default),
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
| temporal | `TurnstylStore.journal` (`memory.py:350`) writes one COLD event per decision; `read_journal` (`memory.py:358`) reads them back newest first | an append-only record of what memory said, what was done, and what was expected next |
| reflection | two things. Per decision: `Engine._advance` builds the `evaluated` list before acting, so each journal event states the facts it rested on (`engine.py`). Over time: `reflect.reflect` (`reflect.py`) reads the journal hourly from `Worker._reflect` (`worker.py`) or on demand from `turnstyl reflect`, and writes `("pattern", <address>)` | the agent's own account of why it charged or refused, and what watching a buyer over many jobs has taught it |
| consolidation | two things. Per job: `Engine._complete` (`engine.py:808`) copies the four step outputs into `findings/<contract_hash>` and archives the job entity via `TurnstylStore.archive_job_entity` (`memory.py:293`). Per day: `digest.build` (`digest.py`) writes `("digest", <YYYY-MM-DD>)` from `turnstyl digest` and `GET /api/digest` | closed jobs leave the working set and their outputs become the cache that prices the next audit; a counted day becomes one entity read instead of a walk of the journal |
| semantic search (FTS5) | `TurnstylStore.search_findings` (`memory.py:329`), called from `Engine._memory_hints` (`engine.py:297`) on every `job new` | queries `findings/*` with the contract's own function names and prints any hit as a dim "memory hint" line |

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

A pair is *issued* at the event that ran step N-1 of that job, which is the
event that created the invoice for step N, and *settled* at the `PAID_X402`
event for step N when there is one (the exact moment the facilitator settled) or
else the `RUN_PAID` event for step N (when the agent noticed and ran it, which
is an upper bound). The pattern's `basis` field says which of the two was used.

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
