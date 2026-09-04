# turnstyl memory note

Persist: every job's state, per-step outputs and their sha256, step token and
time costs, pricing rules, contract source, and a per-buyer ledger of paid
steps, outstanding invoices, defaults and trust tier are written to Sibyl Memory
as they happen, one journal event per decision.

Recall (fresh session): a new process opens the same store, reads `active_jobs`
and `job:<job_id>` to find where the work stopped, reads `job/<job_id>` to see
which steps already have output, and reads `buyer/<address>` to see what this
buyer has paid and is owed — with no state carried in the process it replaced.

Changes the agent's decision by: the price it quotes (half when
`findings/<contract_hash>` already holds that step, 1.5x when
`step_cost/<n>.avg_tokens` exceeds 6000), whether it runs at all (RUN_PAID,
RUN_ON_CREDIT, WAIT_FOR_PAYMENT or REFUSE, chosen from the buyer entity's
paid_steps, open_invoices, defaults and consecutive_paid_since_default), and
whether it calls a model or serves the answer it already has.

## What breaks when memory is deleted

The agent re-invoices a buyer for steps that buyer already paid for, re-runs
work it already did, and treats a proven payer as a stranger with no credit. The
payments are still on chain and the outputs are still hashed there, but nothing
on chain tells the agent which invoice it already collected, so it charges again.

## Primitives used

| Primitive | Where it is called | What it does |
| --- | --- | --- |
| recall | `TurnstylStore.get_job_state` / `get_buyer` (`memory.py`), called at the top of `Engine.run` and `Engine.new_job` | reads the HOT state document and WARM buyer entity that every decision is derived from |
| entities | `TurnstylStore.put_job_entity` / `put_buyer` / `record_step_cost` / `put_findings` (`memory.py:289-345`) | the four WARM entity families: job, buyer, step_cost, findings |
| temporal | `TurnstylStore.journal` (`memory.py:350`) writes one COLD event per decision; `read_journal` (`memory.py:358`) reads them back newest first | an append-only record of what memory said, what was done, and what was expected next |
| reflection | `Engine._advance` builds the `evaluated` list before acting, so each journal event states the facts the decision rested on (`engine.py`) | the agent's own account of why it charged or refused, replayable after the fact |
| consolidation | `Engine._complete` (`engine.py:808`) copies the four step outputs into `findings/<contract_hash>` and archives the job entity via `TurnstylStore.archive_job_entity` (`memory.py:293`) | closed jobs leave the working set; their outputs become the cache that prices the next audit of the same contract |
| semantic search (FTS5) | `TurnstylStore.search_findings` (`memory.py:329`), called from `Engine._memory_hints` (`engine.py:297`) on every `job new` | queries `findings/*` with the contract's own function names and prints any hit as a dim "memory hint" line |

## Why not rebuild memory from chain

The chain holds two things: that a payment of some amount arrived under a memo,
and that some 32-byte output hash was committed. It does not hold the findings,
the step outputs, the prices quoted or why, the token and time cost history that
sets those prices, or any of the trust reasoning about a buyer. A memo is
`keccak256("<job_id>:<step>")` — an opaque hash that only means something if you
already hold the job id it was built from, which is exactly what a deleted
database no longer has. Reconstruction would recover a list of payments to
unknown invoices, not an agent that knows what it sold.

## Files

- Database: `./data/turnstyl.db` (SQLite, gitignored, chmod 0600 by the SDK).
  Overridable per process with `TURNSTYL_DB`; the live demo uses
  `./data/demo_live.db`.
- Tenant: `turnstyl` — a plain string passed to `MemoryClient.local`, not the
  SDK default UUID, so turnstyl's rows never mingle with another local consumer.
- Size after the nine-beat live demo: `data/demo_live.db` is 384K (335,872
  bytes) holding 3 entities, 2 state documents, 2 reference documents and 1
  journal event. It is small because the demo's last beat deletes the store and
  starts one fresh job — that is the delete test, and what survives it is the
  point. A store carrying a finished four-step audit
  (`data/real_run3.db`) is 512K: 6 entities, 1 archived job, 3 state documents,
  2 reference documents, 4 journal events. Most of both figures is SQLite page
  and FTS5 index overhead, not turnstyl's rows.
