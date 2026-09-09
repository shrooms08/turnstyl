# Concepts

The vocabulary, and the rules behind each word. Every constant quoted here comes
from [`schema.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/schema.py)
and is applied by [`policy.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/policy.py),
which is pure: no clock, no network, no memory client. Every fact a decision
uses arrives as an argument, so a decision is reproducible from the rows that
produced it.

## Job

A job is one buyer, one input, one service, and an ordered list of steps. It is
identified by a 12-character job id and carries a `contract_hash`, the sha256 of
the submitted source. Its live state lives in one HOT memory document,
`job:<job_id>`: current step, status, open invoice, buyer, contract hash, job
type.

A job has four statuses: `new`, `awaiting_payment`, `running`, `complete`.
Submitting the same source twice as the same buyer resumes the open job rather
than opening a second one, so nothing is charged twice.

## Step

A step is the unit of work and the unit of price. It has a number, a name, a
base price in USDC, a system prompt, and an optional mechanical gate its answer
must survive — `forge build` for a patch, `forge test` for a test suite.

Step 1 is free on both services and is never gated by payment. It costs the
agent little to quote and it is how a stranger is won. Every other step is
invoiced one at a time and is only run once the agent has seen that invoice
settled — or has decided to extend credit.

A step's record in the `job/<job_id>` entity holds the output, its sha256, the
price, the tokens and seconds it took, the payment transaction, the commit
transaction and the gate verdict.

## Invoice

One open invoice at a time, on the job's state document. It carries the step, the
amount in USDC, the memo, whether it is paid, its settlement transaction, and the
sentence that produced the price.

The memo is `keccak256("<job_id>:<step>")` — a bare string anyone can recompute.
A payment counts when a `Paid` log carries that memo, a payer matching the
invoiced buyer, and at least the invoiced amount. The agent trusts the log, not
the buyer.

### Price

```text
price = base × cached? × expensive? × prompt_payer?, rounded to 2dp, floored
```

| Multiplier | Constant | When it applies | The memory row it reads |
| --- | --- | --- | --- |
| cached | `CACHED_MULTIPLIER = 0.5` | this contract's output for this step is already stored | `findings/<type>/<hash>` |
| expensive | `EXPENSIVE_MULTIPLIER = 1.5` | the recorded average token cost for this step exceeds `EXPENSIVE_TOKEN_THRESHOLD = 6000` | `step_cost/<type>/<n>.avg_tokens` |
| prompt payer | `PROMPT_PAYER_MULTIPLIER = 0.9` | reflection has watched this buyer settle promptly | `pattern/<address>.pays_promptly` |

The prompt-payer discount is applied **last**, after the other two, and no
combination may take a paid step below `PRICE_FLOOR_USDC = 0.05`. A free step
stays free. The discount buys a discount and nothing else: credit and refusal
never read it.

`pays_promptly` needs a median under `PROMPT_PAYER_MAX_SECONDS = 300` over at
least `PROMPT_PAYER_MIN_PAYMENTS = 3` payments. The median, never the mean —
one slow night should not cost a buyer the discount, and one instant payment
should not earn it. Below three observations `pays_promptly` stays null and the
price is exactly what it always was.

Every invoice carries the reason as a sentence, so a buyer reads the arithmetic
rather than a total:

```text
base 0.50 for step 2 (findings); no discount (not cached), no surcharge
(step_cost/2 avg_tokens=744 over 1 run(s)); x0.9 because this buyer has paid
within a median of 0.2s over 18 payments; buyer trust_tier=trusted = 0.45 USDC
```

## The four decisions

`policy.decide` returns one of five values, in this precedence. The reason it
returns names the memory facts it used, and that reason is what the journal
event records.

| Decision | When |
| --- | --- |
| `RUN_FREE` | the step's base price is 0.00, so no payment check applies. Checked first: a free step is never gated. |
| `REFUSE` | the buyer is blocked, or carries unpaid work from a job that has already closed |
| `RUN_PAID` | the invoice for this step is settled |
| `RUN_ON_CREDIT` | unpaid, but the buyer has earned the trusted tier |
| `WAIT_FOR_PAYMENT` | none of the above |

The one exception inside `REFUSE`: a blocked buyer who owes nothing and has
**already paid** for the step in front of them is served it. That is the whole
route back — the money is in hand, the work is owed, and serving it is what the
six steps are counted from.

## The buyer ledger

One WARM entity per address, `buyer/<address>`, and deliberately **not**
namespaced by job type. Trust belongs to the buyer, not the product, so paying
for audits earns credit on test suites.

| Field | What it counts |
| --- | --- |
| `paid_steps`, `paid_usdc` | settled steps and USDC across every job |
| `completed_paid_jobs` | jobs that closed with every paid step settled — the thing credit is extended on |
| `open_invoices` | unsettled invoices on live jobs |
| `unpaid_from_prior_jobs` | delivered steps left unpaid on a job that has closed |
| `outstanding[]` | those debts, each with its job, step, amount and `closed_at` |
| `defaults` | arrears that ran out of grace |
| `consecutive_paid_since_default` | the earn-back counter |
| `consecutive_paid_since_block`, `completed_paid_jobs_at_block` | the unblock counters |
| `trust_tier` | `new`, `trusted` or `blocked` |
| `jobs[]` | this buyer's job ids |

## Trust tiers

`recompute_trust_tier` derives the tier from the counters rather than storing it
as an opinion, so a stored tier and the live facts can never disagree.

| Tier | Reached when |
| --- | --- |
| `new` | the default, and where a worked-off block lands you |
| `trusted` | `TRUSTED_MIN_PAID_JOBS = 3` fully paid completed jobs, no open invoices, nothing unpaid from prior jobs, and any single default worked off |
| `blocked` | `BLOCKED_MIN_DEFAULTS = 2` defaults, while anything is outstanding or fewer than `UNBLOCK_PAID_STEPS = 6` paid steps have landed since the block |

One default is worked off with `EARN_BACK_PAID_STEPS = 4` consecutive paid steps
and nothing outstanding. A block takes settling every debt and then six paid
steps, at which point the buyer is `new` again — a stranger with a history, who
earns credit back by the ordinary three-fully-paid-jobs rule. Credit after a
block is counted only on jobs completed since it: `completed_paid_jobs_at_block`
is subtracted, because what paying off a block buys back is the right to be
served, not the standing you had before.

A blocked buyer is told exactly one sentence, written once in
`policy.unblock_terms` so the terms cannot drift between the place they are
enforced and the places they are quoted:

```text
blocked after 2 defaults: settle 0.45 USDC outstanding, then 6 more
consecutive paid steps to be served again
```

## Arrears and defaults

Late is not the same as gone. When a job closes with a delivered step unpaid, the
debt moves from `open_invoices` to `unpaid_from_prior_jobs` and the item is
stamped with `closed_at`. From that moment it suspends credit and refuses paid
work — immediately, no grace at all for that. What the grace period buys is only
the right not to be called a defaulter yet.

`GRACE_HOURS` defaults to **24** and is overridable per process with
`TURNSTYL_GRACE_HOURS`. Past that, the item is *overdue*, and
`Engine.promote_arrears` — the only place in the codebase a default is ever
written — records it, but only after checking settlement first.

While the clock runs, the refusal counts down instead of accusing:

```text
in arrears: 0.25 USDC owed on job 0e0ba16947ac step 2, due in 24h before it
counts as a default
```

## The cache

When a job completes, `Engine._complete` copies every cacheable step's output
into `findings/<type>/<contract_hash>`. That entity is what makes a second job
for the same contract and service cost nothing: the step is served out of the
store with no model call at all, and the price is halved on top.

Across all 18 second passes in the evals: **0 tokens in, 0 tokens out,
$0.0000**, every step served from memory in 100% of runs. See [Evals](#evals).

A cached step still gets its own commit and is verified like any other. Serving
from memory is not serving something unproved.

Separately, `search_findings` runs FTS5 over `findings/*` with the contract's own
function names on every `job new`, so the agent can say it has seen `withdraw()`
before on a contract whose bytes it has never seen. That is a hint printed
alongside the work, not a price change.

## Where to read the code

| Concept | Where |
| --- | --- |
| price, decide, trust, arrears | [`policy.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/policy.py) |
| constants and stored models | [`schema.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/schema.py) |
| the step loop and the journal | [`engine.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/engine.py) |
| every memory read and write | [`memory.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/memory.py) |
| the two rails | [`payments.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/payments.py) |
