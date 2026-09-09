# turnstyl: build page copy

Everything the submission form asks for, written out here so nothing is composed
under time pressure. Paste each block as it is.

## Field: what breaks when memory is deleted

> The agent re-invoices a buyer for steps that buyer already paid for, re-runs
> work it already did, and treats a proven payer as a stranger with no credit.
> The payments are still on chain and the outputs are still hashed there, but
> nothing on chain tells the agent which invoice it already collected, so it
> charges again.

The live demo does this for real and the buyer pays twice, 224 seconds apart,
same wallet, two different memos:
[first 0.50 USDC](https://sepolia.basescan.org/tx/0xff0ad9caa24bed8c591f5010e8ce85683f8c0486aecad7bf9e564962c6d491af),
[second 0.50 USDC](https://sepolia.basescan.org/tx/0x6c5aa73f0e8d40a1f87a3a67a53f7d40d2caa29df75a007846c72dbf1ec06e34).

## Field: the three-line memory walkthrough

Verbatim, and identical to [MEMORY.md](MEMORY.md).

> **Persist:** Job state, per-step outputs and their sha256, step token and time
> costs, pricing rules, contract source, and a per-buyer ledger of paid steps,
> completed paid jobs, outstanding items, defaults and trust tier are written to
> Sibyl Memory as they happen, one journal event per decision.

> **Recall (fresh session):** A new process opens the same store and reads
> `active_jobs` and `job:<job_id>` to find where the work stopped,
> `job/<job_id>` to see which steps already have output, and `buyer/<address>`
> to see what this buyer has paid and still owes, with no state carried over
> from the process it replaced.

> **Changes the agent's decision by:** What it reads sets the price it quotes
> (x0.5 when `findings/<type>/<hash>` already holds that step, x1.5 when
> `step_cost/<type>/<n>.avg_tokens` exceeds 6000, x0.9 for a buyer the
> reflection pass has watched settle promptly), whether it runs at all
> (RUN_FREE, RUN_PAID, RUN_ON_CREDIT, WAIT_FOR_PAYMENT or REFUSE, chosen from
> the buyer entity's completed_paid_jobs, open_invoices, unpaid_from_prior_jobs,
> defaults and earn-back counters), and whether it calls a model or serves the
> answer it already holds.

## Field: primitive chips

Six of the seven are ticked. Each justification names something a judge can open
and check.

| Chip | Tick | Justification |
| --- | --- | --- |
| recall | yes | `get_job_state` and `get_buyer` (`memory.py:319`, `memory.py:355`) run at the top of `Engine.run` and `Engine.new_job`, so a fresh process resumes a half-finished job with nothing carried over |
| entities | yes | six WARM families are written and read: buyer, job, step_cost, findings, pattern, digest (`put_buyer`, `put_job_entity`, `record_step_cost`, `put_findings`, `put_pattern`, `put_digest` in `memory.py`) |
| semantic search | yes | `search_findings` (`memory.py:474`) runs FTS5 over `findings/*` with the contract's own function names, called from `Engine._memory_hints` (`engine.py:515`) on every `job new` |
| temporal / time-travel | yes | `TurnstylStore.journal` (`memory.py:497`) writes one COLD event per decision naming the facts it rested on, and `read_journal` (`memory.py:505`) replays them; `GET /api/journal` shows the sequence for any job |
| summarization | **no** | left unticked deliberately. Every journal event does carry a one-sentence summary, but those are f-string templates in `engine.py`, not a model summarising anything. Nothing in `src/turnstyl/` calls a model to summarise, so the chip would not survive a look at the code |
| reflection | yes | `reflect.reflect` (`reflect.py:212`) reads up to 2000 journal events hourly from `Worker._reflect` (`worker.py:129`) and writes `pattern/<address>`; the only thing it changes is a x0.9 price multiplier, and `pays_promptly` stays null below three observations |
| consolidation | yes | `Engine._complete` (`engine.py:1241`) copies a finished job's outputs into `findings/<type>/<hash>`, which is what makes the second audit of a contract cost $0.0000; `digest.build` (`digest.py:213`) folds a day into one `digest/<date>` entity |

## Field: project description (40 words)

> turnstyl is a metering and memory layer for agents that sell work. It prices
> each step, tracks who paid, extends or refuses credit, and serves repeat work
> from memory, all from one Sibyl Memory file. Two live services demonstrate it.

## Field: links

| Field | Value |
| --- | --- |
| Repo | <https://github.com/shrooms08/turnstyl> |
| Live | <https://shrooms08.github.io/turnstyl/> |
| Video | `TODO: paste the video URL` |
| Post 1 | `TODO: paste the first post URL` |
| Post 2 | `TODO: paste the second post URL` |

The live page is always up. The agent behind it runs on the operator's machine
behind a Cloudflare tunnel, so when that machine is off the page says `agent
offline` rather than showing stale numbers. Start it with
`scripts/tunnel.sh --daemon` before judging, and confirm with
`scripts/tunnel_check.sh`.

## Numbers a judge may ask about

Each one is sourced. Evals are from [EVALS.md](EVALS.md), which
`scripts/eval.py` writes; store figures are read from the live
`data/turnstyl.db`.

| Claim | Figure | Source |
| --- | --- | --- |
| audits run for the eval | 18, at 3 runs per contract on `claude-haiku-4-5` | EVALS.md |
| total model spend for that run | $0.2574 | EVALS.md |
| median cost and time per audit | $0.0129, 22.4s, 4,285 tokens in and 1,699 out | EVALS.md |
| the same audit served from memory | 0 tokens in, 0 tokens out, $0.0000, over 18 second passes | EVALS.md |
| patch compiles under `forge build` | 18 of 18 | EVALS.md |
| verifier agrees with the compiler | 18 of 18 | EVALS.md |
| false positives on the clean contract | 2 findings over 3 runs, 1 of them HIGH or CRITICAL, in 1 of 3 runs | EVALS.md |
| adversarial contract | 10 flagged passages, reentrancy and the manipulation both reported, 3 of 3 runs | EVALS.md |
| live store | 190 records, 2,781,184 bytes, tenant `turnstyl` | `GET /api/status` |
| live meter | 10 jobs, 10 completed, 2 buyers, 10.09 USDC settled, 154 decisions, 24 steps served from memory | `GET /api/stats` |

**The eval costs are the model bill for producing the work, not what turnstyl
charges a buyer.** A buyer pays 1.50 USDC for a full audit and 1.40 for a full
test suite, before the cache, cost and prompt-payer multipliers.
