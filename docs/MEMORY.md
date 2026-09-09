# turnstyl memory note

turnstyl is a metering and memory layer for agents that sell work. Everything
below is what that layer keeps and what keeping it changes. The two services on
top of it, a Solidity audit and a Foundry test suite, are demonstrations.

## The three lines

**Persist:** Job state, per-step outputs and their sha256, step token and time
costs, pricing rules, contract source, and a per-buyer ledger of paid steps,
completed paid jobs, outstanding items, defaults and trust tier are written to
Sibyl Memory as they happen, one journal event per decision.

**Recall (fresh session):** A new process opens the same store and reads
`active_jobs` and `job:<job_id>` to find where the work stopped, `job/<job_id>`
to see which steps already have output, and `buyer/<address>` to see what this
buyer has paid and still owes, with no state carried over from the process it
replaced.

**Changes the agent's decision by:** What it reads sets the price it quotes
(x0.5 when `findings/<type>/<hash>` already holds that step, x1.5 when
`step_cost/<type>/<n>.avg_tokens` exceeds 6000, x0.9 for a buyer the reflection
pass has watched settle promptly), whether it runs at all (RUN_FREE, RUN_PAID,
RUN_ON_CREDIT, WAIT_FOR_PAYMENT or REFUSE, chosen from the buyer entity's
completed_paid_jobs, open_invoices, unpaid_from_prior_jobs, defaults and
earn-back counters), and whether it calls a model or serves the answer it
already holds.

## What breaks when memory is deleted

The agent re-invoices a buyer for steps that buyer already paid for, re-runs work
it already did, and treats a proven payer as a stranger with no credit. The
payments are still on chain and the outputs are still hashed there, but nothing
on chain tells the agent which invoice it already collected, so it charges again.

## Primitives used

| Primitive | Code path | What it does |
| --- | --- | --- |
| recall | `TurnstylStore.get_job_state` (`memory.py:319`) and `get_buyer` (`memory.py:355`), called at the top of `Engine.run` (`engine.py:340`) and `Engine.new_job` (`engine.py:174`) | reads the HOT state document and WARM buyer entity that every decision is derived from |
| entities | `put_job_entity` (`memory.py:411`), `put_buyer` (`memory.py:365`), `record_step_cost` (`memory.py:437`), `put_findings` (`memory.py:486`), `put_pattern` (`memory.py:384`), `put_digest` (`memory.py:400`) | the WARM families: job, buyer, step_cost, findings, pattern, digest. Work products and cost history are namespaced by job type; the buyer ledger deliberately is not, because trust belongs to the buyer and not the product |
| temporal | `TurnstylStore.journal` (`memory.py:497`) writes one COLD event per decision, plus `PAYMENT_SEEN` and `TRUST_CHANGED` which record a fact rather than a choice (`events.py`); `read_journal` (`memory.py:505`) reads them back newest first | an append-only record of what memory said, what was done, and what was expected next |
| reflection | per decision, `Engine._advance` (`engine.py:604`) builds the `evaluated` list before acting, so each event states the facts it rested on; over time, `reflect.reflect` (`reflect.py:212`) reads the journal from `Worker._reflect` (`worker.py:129`) hourly or from `turnstyl reflect`, and writes `pattern/<address>` | the agent's own account of why it charged or refused, and what watching a buyer across many jobs has taught it |
| consolidation | per job, `Engine._complete` (`engine.py:1241`) copies the step outputs into `findings/<type>/<hash>`; per day, `digest.build` (`digest.py:213`) writes `digest/<YYYY-MM-DD>` | closed work becomes the cache that prices the next job, and a counted day becomes one entity read instead of a walk of the journal |
| semantic search (FTS5) | `TurnstylStore.search_findings` (`memory.py:474`), called from `Engine._memory_hints` (`engine.py:515`) on every `job new` | queries `findings/*` with the contract's own function names and surfaces any hit as a memory hint before the work starts |
| archive | `TurnstylStore.archive_job_entity` (`memory.py:415`) on completion, read back by `read_archived_job` (`memory.py:63`) | a finished job leaves the working set without leaving the store, so `active_jobs` stays the live queue and the history stays queryable |

## Why the chain cannot rebuild memory

The chain holds exactly two things: that a payment of some amount arrived under
some memo, and that some 32-byte output hash was committed. It does not hold the
step outputs, the prices quoted or the reasons for them, the token and time cost
history that sets those prices, or any of the trust reasoning about a buyer. And
the memo is `keccak256("<job_id>:<step>")`, an opaque hash that means something
only if you already hold the job id it was built from, which is precisely what a
deleted database no longer has, so reconstruction would recover a list of
payments to unknown invoices rather than an agent that knows what it sold.

## Files

Read from the live store on 2026-09-09.

| | |
| --- | --- |
| Database | `./data/turnstyl.db`, SQLite, gitignored, chmod 0600 by the SDK. Overridable per process with `TURNSTYL_DB` |
| Tenant | `turnstyl`, a plain string passed to `MemoryClient.local` rather than the SDK's default UUID, so turnstyl's rows never mingle with another local consumer |
| Size | 2,781,184 bytes. Most of that is SQLite page and FTS5 index overhead, not turnstyl's rows |
| Records | 190, which is 23 entities plus 154 journal events plus 13 state documents |
| Entities | 3 buyer, 12 step_cost, 5 findings, 2 pattern, 1 digest |
| Alongside the 190 | 10 archived job entities and 3 reference documents |
