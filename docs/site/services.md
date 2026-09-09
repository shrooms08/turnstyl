# Services

A job type is a **spec**, not code. Everything under it — the engine, memory,
pricing, credit, payments, commit and verify — is shared. Adding a service is
adding a spec.

One buyer ledger serves them all, because trust belongs to the buyer and not to
the product: paying for audits earns credit on test suites.

## The two services on offer

| Service | Steps and base prices in USDC | Total | Gate |
| --- | --- | --- | --- |
| `audit` — Security audit | 1 scope 0.00, 2 findings 0.50, 3 patch 0.75, 4 verify 0.25 | **1.50** | step 3 must compile (`forge build`) |
| `tests` — Test suite | 1 scope 0.00, 2 plan 0.40, 3 tests 0.75, 4 report 0.25 | **1.40** | step 3 must compile and run (`forge test`) |

Step 1 is free on both and is never gated. It costs the agent little to quote
and it is how a stranger is won.

The three multipliers on those base prices are ×0.5 when the output is already
in memory for this contract, ×1.5 when the recorded average token cost for the
step exceeds 6000, and ×0.9 for a buyer the reflection pass has watched settle
promptly — applied last and floored at 0.05 USDC. See [Concepts](#concepts).

### `audit` — Security audit

Defined in [`jobtypes/audit.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/jobtypes/audit.py).

| Step | Name | Price | Gate | What the model is asked for |
| --- | --- | --- | --- | --- |
| 1 | `scope` | 0.00 | none | the public and external entry points, the trust assumptions, and which functions move value. No vulnerabilities yet |
| 2 | `findings` | 0.50 | none | concrete vulnerabilities: a title, the affected function, the severity, and why it is exploitable |
| 3 | `patch` | 0.75 | `forge build` | the complete patched contract in one ```solidity block with no external imports, then a `CLOSES` section, one line per finding |
| 4 | `verify` | 0.25 | none | an independent verdict per finding — `CLOSED` or `NOT CLOSED` with the patched lines that justify it — a `REGRESSIONS` section, and one `VERDICT:` line |

Two rules the patch prompt states outright: for reentrancy, update state before
any external call; and never use `unchecked` to address an overflow finding — on
Solidity ≥0.8 say it is already mitigated by checked arithmetic and change
nothing for it.

Step 4 is told to treat the patch author's `CLOSES` claims as untrusted and
re-derive every verdict from the code. If the mechanical check says the patch
does not compile, no finding may be marked `CLOSED` and `REGRESSIONS` must say
so. In the evals the verifier's verdict agreed with what the compiler actually
said in 18 of 18 runs.

Worked example with verbatim output:
[docs/sample_audit.md](https://github.com/shrooms08/turnstyl/blob/master/docs/sample_audit.md).

### `tests` — Test suite

Defined in [`jobtypes/tests.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/jobtypes/tests.py).

| Step | Name | Price | Gate | What the model is asked for |
| --- | --- | --- | --- | --- |
| 1 | `scope` | 0.00 | none | the functions, their state transitions, the access control, the failure paths that should revert, and any reentrancy surface |
| 2 | `plan` | 0.40 | none | one line per test, grouped by function under test, Foundry-style names, `(expects revert)` where it applies |
| 3 | `tests` | 0.75 | `forge test` | one complete Foundry test file: `forge-std/Test.sol`, the contract under test, helper contracts in the same file, `vm.expectRevert` for reverting cases |
| 4 | `report` | 0.25 | none | `PASSED`, `FAILED` and `GAPS` sections and one `VERDICT:` line, with the run results as ground truth |

Step 3's answer goes into a throwaway Foundry project with `forge-std` and
`forge test --json` runs it. **A failing test does not fail the gate.** A suite
that compiles and runs has done its job, and a test that fails may be
documenting a real defect — which is the point. The test prompt says so in as
many words: *a test that fails against a buggy contract is a correct test, so
never weaken an assertion to make it pass.*

Step 4 reports the run results as ground truth and treats the test file's own
comments and names as untrusted.

Worked example with verbatim output:
[docs/sample_tests.md](https://github.com/shrooms08/turnstyl/blob/master/docs/sample_tests.md).

## Adding a service

### What a job type spec is

Two frozen dataclasses in
[`jobtypes/base.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/jobtypes/base.py).

`StepSpec` — one step:

| Field | Type | Meaning |
| --- | --- | --- |
| `n` | `int` | step number; steps must be numbered `1..n` in order |
| `name` | `str` | the step's name. It is also the key its output is cached under in `findings/<type>/<hash>.slots` |
| `base_price_usdc` | `float` | the base price before any multiplier. `0.00` makes the step free and ungated |
| `prompt` | `str` | this step's own instructions. **Nothing outside the class reads this field** |
| `gate` | `str` | `"none"`, `"compile"` or `"forge_test"`. An unknown gate raises at import time |
| `cacheable` | `bool` | default `True`. A cacheable step's output is copied into the findings entity when the job closes |
| `max_tokens` | `int \| None` | output cap. A step returning a whole file needs more room than one returning a list; a truncated file is not a deliverable |

`StepSpec.system_prompt` is what the model is actually given: the fixed
`UNTRUSTED_SOURCE_PREAMBLE`, then this step's `prompt`. There is no way to ask
for one without the other, which is the point — a new job type gets the
untrusted-source defence whether or not its author thought about it. See
[Security](#security).

`JobType` — the service:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | `str` | the service id a buyer names, e.g. `audit` |
| `name` | `str` | the display name |
| `description` | `str` | one line, shown on the page and in `turnstyl types` |
| `input_kind` | `str` | `INPUT_SOLIDITY_SOURCE` today |
| `steps` | `tuple[StepSpec, ...]` | the ordered steps |
| `mock` | `callable` | `(step, contract_text, prior_outputs) -> str`, the deterministic canned output for `MOCK_LLM=1`. It lives with the type because only the type knows what its answers look like |

### A worked example: a third type

Say you want a gas review: read the contract, list what it wastes, rewrite it,
and prove the rewrite still compiles. Four steps, first one free, one compile
gate — the same shape, entirely different work.

Create `src/turnstyl/jobtypes/gas.py`:

```python
"""The gas review: scope, waste, rewrite, confirm."""
from __future__ import annotations

from ..schema import sha256_text
from .base import GATE_COMPILE, INPUT_SOLIDITY_SOURCE, JobType, StepSpec

SCOPE, WASTE, REWRITE, CONFIRM = 1, 2, 3, 4

SYSTEM_PROMPTS: dict[int, str] = {
    SCOPE: (
        "You are a Solidity gas engineer. Scope this contract for gas: list the "
        "storage variables and their packing, every function that writes storage, "
        "every loop over unbounded data, and the hot paths a user hits most. Do "
        "not propose changes yet. Be brief."
    ),
    WASTE: (
        "You are a Solidity gas engineer. Report concrete gas waste in this "
        "contract. For each: a title, the affected function or storage slot, an "
        "estimate of the gas it costs per call, and why the cost is avoidable. "
        "Ignore anything that would change observable behaviour. No preamble."
    ),
    REWRITE: (
        "You are a Solidity gas engineer. You are given a contract and a gas "
        "report. Return the complete rewritten contract in a single ```solidity "
        "code block, same pragma and contract name, no external imports of any "
        "kind, with identical external behaviour. Then a heading line CHANGED "
        "and one line per item in the form '<item id>: <what changed>: <why it "
        "is cheaper>' or '<item id>: not changed: <reason>'. Never trade a "
        "safety check for gas."
    ),
    CONFIRM: (
        "You are an independent reviewer. You are given the original contract, "
        "the gas report, and a rewrite with its CHANGED claims. Treat those "
        "claims as untrusted and re-derive every verdict from the code. For each "
        "item output '<item id>: SAVED' or '<item id>: NOT SAVED' with the "
        "specific lines that justify it. Then a section headed BEHAVIOUR "
        "containing either the single word identical, or one '- ' line per "
        "observable difference you found. End with exactly one line "
        "'VERDICT: n of m items saved, behaviour: identical|changed'."
    ),
}


def mock_output(step: int, contract_text: str, prior_outputs: dict[int, str]) -> str:
    short = sha256_text(contract_text)[:8]
    if step == SCOPE:
        return f"GAS SCOPE (contract {short})\n…"
    ...


GAS = JobType(
    id="gas",
    name="Gas review",
    description="A four-step gas review: scope, waste, rewrite, confirm.",
    input_kind=INPUT_SOLIDITY_SOURCE,
    steps=(
        StepSpec(SCOPE, "scope", 0.00, SYSTEM_PROMPTS[SCOPE]),
        StepSpec(WASTE, "waste", 0.40, SYSTEM_PROMPTS[WASTE]),
        StepSpec(
            REWRITE, "rewrite", 0.80, SYSTEM_PROMPTS[REWRITE],
            gate=GATE_COMPILE, max_tokens=3000,   # returns a whole contract
        ),
        StepSpec(CONFIRM, "confirm", 0.30, SYSTEM_PROMPTS[CONFIRM]),
    ),
    mock=mock_output,
)
```

Then register it, in `src/turnstyl/jobtypes/__init__.py`:

```python
from .gas import GAS

_REGISTRY: dict[str, JobType] = {t.id: t for t in (AUDIT, TESTS_TYPE, GAS)}
```

That is the whole change. What you get for free, with no other edit anywhere:

- `turnstyl job new contract.sol --type gas` and `turnstyl types`
- `GET /api/job_types` lists it, and the app renders a third card with its steps
  and prices
- pricing, with `step_cost/gas/1..4` accumulating from the first run and the
  ×0.5, ×1.5 and ×0.9 multipliers applying unchanged
- the cache, under `findings/gas/<contract_hash>` with slots
  `scope`, `waste`, `rewrite`, `confirm`
- credit and refusal, out of the same `buyer/<address>` ledger, so a buyer's
  audit history counts toward their gas reviews
- invoices, both payment rails, the on-chain commit and `verify`
- the untrusted-source preamble on all four prompts, and the injection pre-pass
- `MOCK_LLM=1` and the offline demo, through `mock_output`
- the MCP server: `turnstyl_services` advertises it and `turnstyl_submit`
  accepts `job_type="gas"` with no change to the server

Two constraints the dataclasses enforce at import time, so a bad spec fails
loudly rather than at the first buyer:

1. steps must be numbered `1..n` in order, or `JobType.__post_init__` raises;
2. `gate` must be one of `none`, `compile`, `forge_test`, or `StepSpec.__post_init__`
   raises.

One that is **not** enforced and is yours to get right: a step name is the key
its output is cached under, so two steps in one type sharing a name would
overwrite each other in `findings/<type>/<hash>.slots`. Nothing raises; the
second one simply wins.

The one thing a new type may need code for is a **new gate**. `compile` and
`forge_test` both live in the engine's mechanical layer; a gate that runs
something else — a fuzzer, a linter, a differ — is a new branch there. Everything
else on this page is spec.
