"""Constants and pydantic models for everything turnstyl stores in Sibyl Memory.

This module is the single source of truth for the memory layout. Nothing else
in turnstyl should hand-build a memory key or a stored dict; it should build a
model from here and call ``.model_dump()``.

Memory layout
-------------
HOT   state "job:<job_id>"      -> JobState
HOT   state "active_jobs"       -> list[str] of job_ids that are not complete
HOT   state "fake_payments"     -> {"<job_id>:<step>": tx_hash}  (FakePayments only)
REF   "contract:<hash>"         -> the contract source text
WARM  entity ("buyer", <addr>)  -> BuyerLedger            (shared across job types)
WARM  entity ("job", <job_id>)  -> JobEntity
WARM  entity ("step_cost", "<type>/<n>")   -> StepCost
WARM  entity ("findings", "<type>/<hash>") -> FindingsEntity
REF   "pricing_rules"           -> PricingRules
COLD  journal                   -> one event per decision

Work products and cost history are per job type: a test suite for a contract is
not an audit of it, and step 3 of one is not priced by step 3 of the other. The
buyer ledger is deliberately NOT namespaced: trust belongs to the buyer, so
paying for audits earns credit on test suites. Rows written before job types
existed carry no type and are read as "audit", which is what they were.
"""
from __future__ import annotations

import os

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ----------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------
# What a step is called and what it costs belongs to the job type, not here:
# see turnstyl.jobtypes. Every service so far runs four steps with the first
# one free, and these two constants are the only thing the rest of turnstyl
# assumes about shape.
FIRST_STEP = 1
DEFAULT_JOB_TYPE = "audit"

# ----------------------------------------------------------------------
# Pricing (USDC)
# ----------------------------------------------------------------------
USDC_DECIMALS = 6
USDC_UNITS = 10**USDC_DECIMALS


def usdc_base_units(amount_usdc: float) -> int:
    """USDC has 6 decimals; 0.50 USDC is 500000 base units."""
    return round(amount_usdc * USDC_UNITS)


CACHED_MULTIPLIER = 0.5
EXPENSIVE_MULTIPLIER = 1.5
EXPENSIVE_TOKEN_THRESHOLD = 6000

# What the agent learns about a buyer from watching, rather than from the
# ledger. A buyer who settles quickly costs less to carry: the invoice is not
# outstanding, the worker is not re-checking it, and the job does not sit half
# done. That is worth a tenth off, and nothing else.
PROMPT_PAYER_MULTIPLIER = 0.9
PROMPT_PAYER_MAX_SECONDS = 300      # median, not mean: one slow night cannot spoil it
PROMPT_PAYER_MIN_PAYMENTS = 3       # nothing is inferred from one payment
PRICE_FLOOR_USDC = 0.05             # no multiplier may take a paid step below this

# ----------------------------------------------------------------------
# Decisions returned by policy.decide
# ----------------------------------------------------------------------
RUN_FREE = "RUN_FREE"
RUN_PAID = "RUN_PAID"
RUN_ON_CREDIT = "RUN_ON_CREDIT"
WAIT_FOR_PAYMENT = "WAIT_FOR_PAYMENT"
REFUSE = "REFUSE"
DECISIONS = (RUN_FREE, RUN_PAID, RUN_ON_CREDIT, WAIT_FOR_PAYMENT, REFUSE)
Decision = Literal["RUN_FREE", "RUN_PAID", "RUN_ON_CREDIT", "WAIT_FOR_PAYMENT", "REFUSE"]

# Trust tiers
TRUST_NEW = "new"
TRUST_TRUSTED = "trusted"
TRUST_BLOCKED = "blocked"
TrustTier = Literal["new", "trusted", "blocked"]

# Trust thresholds (policy.recompute_trust_tier is the only consumer).
TRUSTED_MIN_PAID_JOBS = 3          # fully paid, completed jobs before credit
BLOCKED_MIN_UNPAID_PRIOR_JOBS = 2
# Two defaults stops the relationship, but does not end it. One default can be
# worked off with four consecutive paid steps and nothing outstanding; a block
# takes settling every debt and then six paid steps, at which point the buyer is
# a stranger again rather than a pariah, and earns credit back the ordinary way.
BLOCKED_MIN_DEFAULTS = 2
EARN_BACK_PAID_STEPS = 4
UNBLOCK_PAID_STEPS = 6

# A job that closes with delivered work unpaid puts the buyer in arrears, not
# in default. Not paying yet is not the same as not paying, and the agent has
# no way to tell them apart at the moment a job closes. The debt is refused
# work and suspends credit immediately; it only becomes a default, with the
# counters it resets, once it has gone this long unsettled.
GRACE_HOURS = float(os.environ.get("TURNSTYL_GRACE_HOURS") or 24)

# Job statuses
STATUS_NEW = "new"
STATUS_AWAITING_PAYMENT = "awaiting_payment"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
JobStatus = Literal["new", "awaiting_payment", "running", "complete"]

# ----------------------------------------------------------------------
# Memory keys / categories
# ----------------------------------------------------------------------
STATE_ACTIVE_JOBS = "active_jobs"
STATE_FAKE_PAYMENTS = "fake_payments"
# Settlements that arrived over x402: {"<job_id>:<step>": {"tx", "payer"}}.
# A real USDC transfer on Base, but an EIP-3009 one submitted by a facilitator,
# so it leaves no Paid event on the receipts contract and has to be recorded
# here for check_paid to find it.
STATE_X402_PAYMENTS = "x402_payments"
REF_PRICING_RULES = "pricing_rules"


def contract_ref_key(contract_hash: str) -> str:
    """REFERENCE key holding the contract source, so a resumed job can run a
    fresh step without the operator re-supplying the .sol file."""
    return f"contract:{contract_hash}"

CAT_BUYER = "buyer"
CAT_JOB = "job"
CAT_STEP_COST = "step_cost"
CAT_FINDINGS = "findings"
# Written by the reflection pass, not by any decision: what the journal says
# about how a buyer behaves, as opposed to what the ledger says they owe.
CAT_PATTERN = "pattern"
# One consolidated day of figures, so a later digest need not re-read the
# whole journal to say what happened.
CAT_DIGEST = "digest"


def findings_name(job_type: str, contract_hash: str) -> str:
    """Entity name for a contract's work product under one job type.

    Slashes are permitted in SDK identifiers (only ".." and shell metacharacters
    are rejected), so the type is a path segment rather than a separate category.
    """
    return f"{job_type}/{contract_hash}"


def step_cost_name(job_type: str, step: int) -> str:
    """Entity name for one step's rolling cost under one job type."""
    return f"{job_type}/{step}"


def job_state_key(job_id: str) -> str:
    """HOT state key for a job."""
    return f"job:{job_id}"


def invoice_memo(job_id: str, step: int) -> str:
    """The human-readable memo the fake backend shows on an invoice."""
    return f"turnstyl:{job_id}:step{step}"


def invoice_memo_raw(job_id: str, step: int) -> str:
    """The exact string hashed to bytes32 for the on-chain memo.

    Deliberately bare — "<job_id>:<step>" — so a buyer, an explorer, or an
    auditor can recompute keccak256 of it without knowing turnstyl's conventions.
    """
    return f"{job_id}:{step}"


def sha256_text(text: str) -> str:
    """Stable content hash used for contract_hash and per-step output_sha256."""
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    """ISO-8601 UTC, millisecond precision — matches the SDK's own timestamps."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
class _Model(BaseModel):
    """Base: reject unknown fields so a layout drift fails loudly at the read."""

    model_config = ConfigDict(extra="forbid")


class OpenInvoice(_Model):
    """An invoice offered for one step.

    ADDITION to the day-2 spec (which named step/amount_usdc/memo only): the
    settlement state lives here too, so a job's state document is self-describing
    across a process restart and ``policy.decide`` can stay a pure function of
    (step, buyer_entity, job_state).
    """

    step: int
    amount_usdc: float
    memo: str
    paid: bool = False
    tx_hash: str | None = None
    # Day 3. The block the invoice was issued at bounds the Paid-log scan, so a
    # settlement search never walks the whole chain; the reason travels with the
    # invoice so any later display can say why the step costs what it costs.
    invoice_block: int | None = None
    price_reason: str = ""
    # When this invoice was offered. The settlement event carries it, so the
    # time a buyer took to pay is read off one event rather than inferred by
    # pairing it with whichever earlier event happened to create the invoice.
    issued_at: str = Field(default_factory=utc_now)


class InjectionFlag(_Model):
    """One passage in the submitted source that reads like an instruction.

    Produced by injection.scan before step 1 runs, so it is a fact about the
    contract the buyer handed over, recorded once and never re-derived.
    """

    line: int
    kind: str            # "comment" or "string"
    rule: str            # which pattern fired
    why: str             # why that pattern exists, in one phrase
    text: str            # the passage, trimmed
    matched: str = ""    # the part of it that matched


class JobState(_Model):
    """HOT: state "job:<job_id>". The resume point."""

    job_id: str
    buyer: str
    contract_hash: str
    # Instruction-like text found in the source before any model saw it. Empty
    # for an ordinary contract.
    injection_flags: list[InjectionFlag] = Field(default_factory=list)
    # Which service this job is. Absent on rows written before job types, and
    # those were all audits.
    job_type: str = DEFAULT_JOB_TYPE
    current_step: int = FIRST_STEP
    status: JobStatus = STATUS_NEW
    open_invoice: OpenInvoice | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class StepRecord(_Model):
    """One executed step inside the job entity."""

    output: str
    output_sha256: str
    price_usdc: float
    paid: bool = False
    tx_hash: str | None = None
    # How the invoice for this step was settled: "receipts" (a Paid log on the
    # receipts contract), "x402" (an EIP-3009 authorisation settled by a
    # facilitator), or None for free and unsettled steps.
    pay_method: str | None = None
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    cached: bool = False
    commit_tx: str | None = None
    # Patch step only; None on every other step.
    # diff_applies is True by construction: the diff is generated here with
    # difflib from the model's whole-file answer, so there is nothing to verify.
    # compiles is the verdict that has to be earned, from a real solc run.
    diff_applies: bool | None = None
    patched_source: str | None = None
    generated_diff: str | None = None
    compiles: bool | None = None
    compiler_output: str | None = None
    # forge_test gate only (the test-suite type's step 3). A suite that compiles
    # and runs passes the gate even when tests fail: a failing test may be
    # documenting a real defect, which is the product working.
    tests_total: int | None = None
    tests_passed: int | None = None
    tests_failed: int | None = None
    test_output: str | None = None


class JobEntity(_Model):
    """WARM: entity ("job", <job_id>). The per-step work product."""

    buyer: str
    contract_hash: str
    job_type: str = DEFAULT_JOB_TYPE
    steps: dict[str, StepRecord] = Field(default_factory=dict)


class OutstandingItem(_Model):
    """A delivered-but-unpaid step. ADDITION to the day-2 spec — see BuyerLedger.

    Carries its own memo and issue block so ``BasePayments.reconcile`` can settle
    it long after the job closed and its state document dropped the invoice.
    """

    job_id: str
    step: int
    amount_usdc: float
    memo: str = ""
    invoice_block: int | None = None
    # Set when the job this item belongs to closed with it still unpaid. That
    # is the moment the grace period starts; None means the job is still open,
    # so nothing is owed yet in the sense that matters here.
    closed_at: str | None = None


class BuyerLedger(_Model):
    """WARM: entity ("buyer", <address lowercased>).

    ``open_invoices`` counts invoices for work ALREADY DELIVERED and not yet
    settled — a real receivable. An invoice merely OFFERED for a step that has
    not run yet is not a debt and is not counted, otherwise a buyer who has paid
    for everything asked of them could never reach the "trusted" tier.

    ``outstanding`` (ADDITION) names those receivables. It is needed because a
    job entity is archived on completion and the SDK exposes no reader for
    archived rows, so the ledger would otherwise be unable to say which step is
    unpaid.
    """

    paid_steps: int = 0
    paid_usdc: float = 0.0
    open_invoices: int = 0
    unpaid_from_prior_jobs: int = 0
    # Day 3. Lifetime count of steps delivered on credit and left unpaid when a
    # job closed. `unpaid_from_prior_jobs` falls back to 0 the moment the debt is
    # settled; this does not. Settling a debt buys back the right to be served,
    # not the right to be served on credit again.
    defaults: int = 0
    # Paid steps settled since the last default, counted so a buyer who defaulted
    # once can earn credit back. Any new default resets it to 0.
    consecutive_paid_since_default: int = 0
    # The same clock for a buyer who was blocked, counted only while the tier is
    # blocked and reset by any new default. A ledger written before this field
    # existed reads as 0, which is correct: nothing has been proved yet.
    consecutive_paid_since_block: int = 0
    # completed_paid_jobs at the moment a block began. Credit after a block is
    # earned on jobs completed since it, by the same three-fully-paid-jobs rule,
    # so working a block off returns a buyer to "new" rather than handing back
    # the standing they had before they defaulted twice.
    completed_paid_jobs_at_block: int = 0
    # Jobs that reached complete with every paid step settled: nothing carried
    # as outstanding at close. Credit is extended on this, not on step counts.
    # A ledger written before this field existed reads as 0 (pydantic default).
    completed_paid_jobs: int = 0
    trust_tier: TrustTier = TRUST_NEW
    jobs: list[str] = Field(default_factory=list)
    outstanding: list[OutstandingItem] = Field(default_factory=list)


class StepCost(_Model):
    """WARM: entity ("step_cost", "<n>"). Rolling averages over real executions."""

    runs: int = 0
    avg_tokens: float = 0.0
    avg_seconds: float = 0.0


class FindingsEntity(_Model):
    """WARM: entity ("findings", "<type>/<contract_hash>").

    A mapping from step name to that step's cached output, and nothing else.
    The keys are data: they come from whichever job type wrote the row, so
    audit rows are keyed scope/findings/patch/verify and tests rows
    plan/tests/report, and a job type added tomorrow will use names this file
    has never heard of. What is validated is the values, never the key set.

    That is also why this model reads two shapes. Rows written before job types
    existed put the step names at the top level; rows written since nest them
    under ``slots``. Both are folded into ``slots`` on the way in, so a caller
    only ever sees one shape and neither needs migrating.
    """

    slots: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _fold_step_names(cls, data: Any) -> Any:
        """Any key that is not ``slots`` is a step name, so it belongs in it.

        Without this the base model's extra="forbid" rejects every legacy row,
        which is correct for a field that should not exist and wrong for a step
        name the writer was entitled to choose. Values are still validated by
        the annotation below: a step whose output is not a string is a real
        problem and is reported as one.
        """
        if not isinstance(data, dict):
            return data
        named = {k: v for k, v in data.items() if k != "slots"}
        if not named:
            return data
        merged = dict(data.get("slots") or {})
        merged.update(named)
        return {"slots": merged}

    def slot(self, step_name: str) -> str | None:
        return self.slots.get(step_name)

    def with_step(self, step_name: str, output: str) -> "FindingsEntity":
        merged = dict(self.slots)
        merged[step_name] = output
        return self.model_copy(update={"slots": merged})

    @property
    def filled(self) -> list[str]:
        return sorted(k for k, v in self.slots.items() if v)

    @classmethod
    def from_body(cls, body: dict[str, Any]) -> "FindingsEntity":
        """Read a stored row, of either shape.

        Kept as the name the rest of the code calls, but there is nothing left
        for it to do: ``model_validate`` now folds both shapes itself, which is
        what lets the startup guard use the ordinary reader and get the same
        answer this does.
        """
        return cls.model_validate(body)


class PricingRules(_Model):
    """REFERENCE: "pricing_rules". Written once, on first run.

    ``base_prices`` is per job type: {"<type>": {"<step>": price}}.
    """

    base_prices: dict[str, dict[str, float]] = Field(default_factory=dict)
    cached_multiplier: float = CACHED_MULTIPLIER
    expensive_multiplier: float = EXPENSIVE_MULTIPLIER
    expensive_token_threshold: int = EXPENSIVE_TOKEN_THRESHOLD
    note: str = (
        "Base price per step in USDC, per job type. A step whose output is "
        "already in the findings entity for this contract and type costs "
        "cached_multiplier of base. A step whose recorded avg_tokens exceeds "
        "expensive_token_threshold costs expensive_multiplier of base."
    )


class BuyerPattern(_Model):
    """WARM entity ("pattern", <address>): what watching this buyer has taught.

    Derived from the journal by ``reflect.py`` and by nothing else. It is an
    observation, never an obligation: the ledger says what a buyer owes, this
    says how they have behaved. ``pays_promptly`` stays None until there are
    enough payments to mean anything.
    """

    address: str
    payments_observed: int = 0
    median_seconds_invoice_to_payment: float | None = None
    steps_per_job_median: float | None = None
    jobs_observed: int = 0
    default_rate: float | None = None
    pays_promptly: bool | None = None
    last_reflected_at: str = Field(default_factory=utc_now)
    # How the median was measured, in one phrase, so a price that cites it can
    # be read back years later without reading this file.
    basis: str = ""


class DigestEntity(_Model):
    """WARM entity ("digest", <YYYY-MM-DD>): one day's figures, consolidated.

    The only write the digest makes. Recomputing a past day from the journal
    gives the same answer, so this is a cache with a date for a key, and it is
    what lets a later digest be cheap.
    """

    date: str
    generated_at: str = Field(default_factory=utc_now)
    days: int = 1
    figures: dict[str, Any] = Field(default_factory=dict)


class JournalEntry(_Model):
    """COLD: one journal event per decision."""

    evaluated: list[str]
    acted: list[str]
    forward: list[str]
    extra: dict[str, Any]
