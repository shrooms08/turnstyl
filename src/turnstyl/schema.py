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
WARM  entity ("buyer", <addr>)  -> BuyerLedger
WARM  entity ("job", <job_id>)  -> JobEntity
WARM  entity ("step_cost", "n") -> StepCost
WARM  entity ("findings", <hash>) -> FindingsEntity
REF   "pricing_rules"           -> PricingRules
COLD  journal                   -> one event per decision
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ----------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------
STEP_SCOPE = 1
STEP_FINDINGS = 2
STEP_PATCH = 3
STEP_VERIFY = 4
FIRST_STEP = STEP_SCOPE
LAST_STEP = STEP_VERIFY
ALL_STEPS = (STEP_SCOPE, STEP_FINDINGS, STEP_PATCH, STEP_VERIFY)

STEP_NAMES: dict[int, str] = {
    STEP_SCOPE: "scope",
    STEP_FINDINGS: "findings",
    STEP_PATCH: "patch",
    STEP_VERIFY: "verify",
}

# The findings entity stores one output slot per step, keyed by the step's name.
STEP_SLOTS: dict[int, str] = dict(STEP_NAMES)

# ----------------------------------------------------------------------
# Pricing (USDC). Step 1 is free; 2-4 are metered.
# ----------------------------------------------------------------------
BASE_PRICES: dict[int, float] = {
    STEP_SCOPE: 0.00,
    STEP_FINDINGS: 0.50,
    STEP_PATCH: 0.75,
    STEP_VERIFY: 0.25,
}
CACHED_MULTIPLIER = 0.5
EXPENSIVE_MULTIPLIER = 1.5
EXPENSIVE_TOKEN_THRESHOLD = 6000

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
TRUSTED_MIN_PAID_STEPS = 2
BLOCKED_MIN_UNPAID_PRIOR_JOBS = 2

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
REF_PRICING_RULES = "pricing_rules"


def contract_ref_key(contract_hash: str) -> str:
    """REFERENCE key holding the contract source, so a resumed job can run a
    fresh step without the operator re-supplying the .sol file."""
    return f"contract:{contract_hash}"

CAT_BUYER = "buyer"
CAT_JOB = "job"
CAT_STEP_COST = "step_cost"
CAT_FINDINGS = "findings"


def job_state_key(job_id: str) -> str:
    """HOT state key for a job."""
    return f"job:{job_id}"


def invoice_memo(job_id: str, step: int) -> str:
    """The on-chain memo a buyer references when paying for one step."""
    return f"turnstyl:{job_id}:step{step}"


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


class JobState(_Model):
    """HOT: state "job:<job_id>". The resume point."""

    job_id: str
    buyer: str
    contract_hash: str
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
    tokens: int = 0
    seconds: float = 0.0
    cached: bool = False


class JobEntity(_Model):
    """WARM: entity ("job", <job_id>). The per-step work product."""

    buyer: str
    contract_hash: str
    steps: dict[str, StepRecord] = Field(default_factory=dict)


class OutstandingItem(_Model):
    """A delivered-but-unpaid step. ADDITION to the day-2 spec — see BuyerLedger."""

    job_id: str
    step: int
    amount_usdc: float


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
    trust_tier: TrustTier = TRUST_NEW
    jobs: list[str] = Field(default_factory=list)
    outstanding: list[OutstandingItem] = Field(default_factory=list)


class StepCost(_Model):
    """WARM: entity ("step_cost", "<n>"). Rolling averages over real executions."""

    runs: int = 0
    avg_tokens: float = 0.0
    avg_seconds: float = 0.0


class FindingsEntity(_Model):
    """WARM: entity ("findings", <contract_hash>). Serves repeat contracts."""

    scope: str | None = None
    findings: str | None = None
    patch: str | None = None
    verify: str | None = None

    def slot(self, step: int) -> str | None:
        return getattr(self, STEP_SLOTS[step])

    def with_step(self, step: int, output: str) -> "FindingsEntity":
        return self.model_copy(update={STEP_SLOTS[step]: output})


class PricingRules(_Model):
    """REFERENCE: "pricing_rules". Written once, on first run."""

    base_prices: dict[str, float] = Field(
        default_factory=lambda: {str(k): v for k, v in BASE_PRICES.items()}
    )
    cached_multiplier: float = CACHED_MULTIPLIER
    expensive_multiplier: float = EXPENSIVE_MULTIPLIER
    expensive_token_threshold: int = EXPENSIVE_TOKEN_THRESHOLD
    note: str = (
        "Base price per step in USDC. A step whose output is already in the "
        "findings entity for this contract costs cached_multiplier of base. A "
        "step whose recorded avg_tokens exceeds expensive_token_threshold costs "
        "expensive_multiplier of base."
    )


class JournalEntry(_Model):
    """COLD: one journal event per decision."""

    evaluated: list[str]
    acted: list[str]
    forward: list[str]
    extra: dict[str, Any]
