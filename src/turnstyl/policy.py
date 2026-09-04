"""Pricing and credit decisions. Pure functions only.

No LLM, no network, no clock, no memory client. Every fact these functions use
arrives as an argument, so a decision is reproducible from the memory rows that
produced it — which is exactly what the journal event records.
"""
from __future__ import annotations

from .schema import (
    BASE_PRICES,
    BLOCKED_MIN_DEFAULTS,
    BLOCKED_MIN_UNPAID_PRIOR_JOBS,
    EARN_BACK_PAID_STEPS,
    CACHED_MULTIPLIER,
    EXPENSIVE_MULTIPLIER,
    EXPENSIVE_TOKEN_THRESHOLD,
    FIRST_STEP,
    REFUSE,
    RUN_FREE,
    RUN_ON_CREDIT,
    RUN_PAID,
    STEP_NAMES,
    TRUST_BLOCKED,
    TRUST_NEW,
    TRUST_TRUSTED,
    TRUSTED_MIN_PAID_STEPS,
    WAIT_FOR_PAYMENT,
    BuyerLedger,
    Decision,
    JobState,
    StepCost,
    TrustTier,
)


def price(
    step: int,
    buyer_entity: BuyerLedger,
    step_cost_entity: StepCost,
    findings_cached: bool,
) -> tuple[float, str]:
    """Price one step in USDC.

    base * 0.5 when this contract's output for this step is already in memory,
    * 1.5 when the recorded average token cost for this step exceeds the
    threshold. Rounded to 2 decimals.

    Returns (amount_usdc, reason).
    """
    if step not in BASE_PRICES:
        raise ValueError(
            f"turnstyl: no base price for step {step!r}; steps are {sorted(BASE_PRICES)}"
        )
    base = BASE_PRICES[step]
    amount = base
    parts = [f"base {base:.2f} for step {step} ({STEP_NAMES[step]})"]

    if findings_cached:
        amount *= CACHED_MULTIPLIER
        parts.append(
            f"x{CACHED_MULTIPLIER} because findings cached for this contract hash "
            f"(memory serves it, no model call)"
        )
    if step_cost_entity.avg_tokens > EXPENSIVE_TOKEN_THRESHOLD:
        amount *= EXPENSIVE_MULTIPLIER
        parts.append(
            f"x{EXPENSIVE_MULTIPLIER} because step_cost/{step} avg_tokens="
            f"{step_cost_entity.avg_tokens:.0f} > {EXPENSIVE_TOKEN_THRESHOLD} "
            f"over {step_cost_entity.runs} run(s)"
        )
    if not findings_cached and step_cost_entity.avg_tokens <= EXPENSIVE_TOKEN_THRESHOLD:
        parts.append(
            f"no discount (not cached), no surcharge (step_cost/{step} avg_tokens="
            f"{step_cost_entity.avg_tokens:.0f} over {step_cost_entity.runs} run(s))"
        )
    parts.append(f"buyer trust_tier={buyer_entity.trust_tier}")

    amount = round(amount, 2)
    return amount, f"{'; '.join(parts)} = {amount:.2f} USDC"


def earned_back(buyer_entity: BuyerLedger) -> bool:
    """Has a buyer with one default paid its way back to credit?"""
    return (
        buyer_entity.defaults == 0
        or buyer_entity.consecutive_paid_since_default >= EARN_BACK_PAID_STEPS
    )


def steps_until_credit(buyer_entity: BuyerLedger) -> int:
    """Clean paid steps still owed before credit returns. 0 once earned."""
    return max(
        0, EARN_BACK_PAID_STEPS - buyer_entity.consecutive_paid_since_default
    )


def recompute_trust_tier(buyer_entity: BuyerLedger) -> TrustTier:
    """blocked at two defaults; trusted on a clean record, or on one worked off.

    A buyer who lets a job close with work unpaid takes a default. Paying the
    debt clears ``unpaid_from_prior_jobs`` and lifts the refusal, but not the
    credit: they buy per step, up front, until they have settled
    EARN_BACK_PAID_STEPS steps in a row without defaulting again. A second
    default ends it — that history cannot be worked off.
    """
    if buyer_entity.defaults >= BLOCKED_MIN_DEFAULTS:
        return TRUST_BLOCKED
    if buyer_entity.unpaid_from_prior_jobs >= BLOCKED_MIN_UNPAID_PRIOR_JOBS:
        return TRUST_BLOCKED
    if (
        buyer_entity.paid_steps >= TRUSTED_MIN_PAID_STEPS
        and buyer_entity.open_invoices == 0
        and buyer_entity.unpaid_from_prior_jobs == 0
        and earned_back(buyer_entity)
    ):
        return TRUST_TRUSTED
    return TRUST_NEW


def is_trusted(buyer_entity: BuyerLedger) -> bool:
    """The credit test, stated once: the stored tier and the live facts agree."""
    return (
        buyer_entity.trust_tier == TRUST_TRUSTED
        and buyer_entity.paid_steps >= TRUSTED_MIN_PAID_STEPS
        and buyer_entity.open_invoices == 0
        and buyer_entity.unpaid_from_prior_jobs == 0
        and earned_back(buyer_entity)
    )


def decide(
    step: int,
    buyer_entity: BuyerLedger,
    job_state: JobState,
) -> tuple[Decision, str]:
    """Decide whether to run ``step`` for this buyer, and say why.

    Precedence, in order:
      1. step 1 is free and is never gated — it costs the agent nothing to
         quote and it is how a new buyer is won.
      2. REFUSE  a buyer carrying unpaid work from a completed job, or blocked.
      3. RUN_PAID       the invoice for this step is settled.
      4. RUN_ON_CREDIT  unpaid, but the buyer has earned the trusted tier.
      5. WAIT_FOR_PAYMENT.

    Returns (decision, reason). The reason names the memory facts used.
    """
    facts = (
        f"buyer paid_steps={buyer_entity.paid_steps}, "
        f"paid_usdc={buyer_entity.paid_usdc:.2f}, "
        f"open_invoices={buyer_entity.open_invoices}, "
        f"unpaid_from_prior_jobs={buyer_entity.unpaid_from_prior_jobs}, "
        f"defaults={buyer_entity.defaults}, "
        f"consecutive_paid_since_default="
        f"{buyer_entity.consecutive_paid_since_default}, "
        f"trust_tier={buyer_entity.trust_tier}"
    )

    if step == FIRST_STEP:
        return RUN_FREE, (
            f"step {step} ({STEP_NAMES[step]}) is free at base 0.00 USDC, so no "
            f"payment check applies; {facts}"
        )

    if buyer_entity.trust_tier == TRUST_BLOCKED:
        return REFUSE, (
            f"buyer is blocked: {facts}; blocked at "
            f"unpaid_from_prior_jobs >= {BLOCKED_MIN_UNPAID_PRIOR_JOBS}"
        )
    if buyer_entity.unpaid_from_prior_jobs > 0:
        return REFUSE, (
            f"buyer left {buyer_entity.unpaid_from_prior_jobs} step(s) unpaid on a "
            f"completed job; {facts}"
        )

    invoice = job_state.open_invoice
    if invoice is not None and invoice.step == step and invoice.paid:
        return RUN_PAID, (
            f"invoice {invoice.memo} for step {step} is settled at "
            f"{invoice.amount_usdc:.2f} USDC (tx {invoice.tx_hash}); {facts}"
        )

    if is_trusted(buyer_entity):
        earn_back = (
            f", credit earned back after "
            f"consecutive_paid_since_default="
            f"{buyer_entity.consecutive_paid_since_default} "
            f">= {EARN_BACK_PAID_STEPS} despite defaults={buyer_entity.defaults}"
            if buyer_entity.defaults > 0
            else ""
        )
        return RUN_ON_CREDIT, (
            f"step {step} is unpaid but buyer is trusted: paid_steps="
            f"{buyer_entity.paid_steps} >= {TRUSTED_MIN_PAID_STEPS}, "
            f"open_invoices={buyer_entity.open_invoices}, "
            f"unpaid_from_prior_jobs={buyer_entity.unpaid_from_prior_jobs}"
            f"{earn_back}"
        )

    amount = invoice.amount_usdc if invoice is not None else BASE_PRICES.get(step, 0.0)
    if buyer_entity.defaults > 0:
        return WAIT_FOR_PAYMENT, (
            f"step {step} is unpaid at {amount:.2f} USDC and this buyer has "
            f"{buyer_entity.defaults} default(s) on record, so work is sold up "
            f"front; credit returns after {EARN_BACK_PAID_STEPS} consecutive "
            f"paid steps, currently "
            f"{buyer_entity.consecutive_paid_since_default} "
            f"({steps_until_credit(buyer_entity)} to go) ({facts})"
        )
    return WAIT_FOR_PAYMENT, (
        f"step {step} is unpaid at {amount:.2f} USDC and the buyer has not earned "
        f"credit ({facts}); trusted needs paid_steps >= {TRUSTED_MIN_PAID_STEPS} "
        f"with nothing outstanding"
    )
