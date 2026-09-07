"""Pricing and credit decisions. Pure functions only.

No LLM, no network, no clock, no memory client. Every fact these functions use
arrives as an argument, so a decision is reproducible from the memory rows that
produced it — which is exactly what the journal event records.
"""
from __future__ import annotations

from .jobtypes import JobType, StepSpec
from .schema import (
    BLOCKED_MIN_DEFAULTS,
    BLOCKED_MIN_UNPAID_PRIOR_JOBS,
    EARN_BACK_PAID_STEPS,
    CACHED_MULTIPLIER,
    EXPENSIVE_MULTIPLIER,
    EXPENSIVE_TOKEN_THRESHOLD,
    PRICE_FLOOR_USDC,
    PROMPT_PAYER_MULTIPLIER,
    REFUSE,
    RUN_FREE,
    RUN_ON_CREDIT,
    RUN_PAID,
    TRUST_BLOCKED,
    TRUST_NEW,
    TRUST_TRUSTED,
    TRUSTED_MIN_PAID_JOBS,
    UNBLOCK_PAID_STEPS,
    WAIT_FOR_PAYMENT,
    BuyerLedger,
    BuyerPattern,
    Decision,
    JobState,
    StepCost,
    TrustTier,
)


def price(
    step_spec: StepSpec,
    buyer_entity: BuyerLedger,
    step_cost_entity: StepCost,
    findings_cached: bool,
    buyer_pattern: BuyerPattern | None = None,
) -> tuple[float, str]:
    """Price one step in USDC.

    The base price comes from the job type's spec; the multipliers are the
    agent's, and are the same for every service it sells: base * 0.5 when this
    contract's output for this step is already in memory, * 1.5 when the
    recorded average token cost for this step exceeds the threshold, and * 0.9
    when the reflection pass has watched this buyer settle promptly. The prompt
    payer discount is applied last, after the other two, and no combination of
    multipliers may take a paid step below PRICE_FLOOR_USDC. Rounded to 2
    decimals.

    ``buyer_pattern`` is what reflection learned by reading the journal (see
    reflect.py); None means the agent has formed no opinion and prices as it
    always did. Credit and refusal are decided elsewhere and are untouched by
    it: this buys a discount, never trust.

    Returns (amount_usdc, reason).
    """
    step = step_spec.n
    base = step_spec.base_price_usdc
    amount = base
    parts = [f"base {base:.2f} for step {step} ({step_spec.name})"]

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
    # Applied last, so it discounts whatever the other rules arrived at.
    if buyer_pattern is not None and buyer_pattern.pays_promptly:
        amount *= PROMPT_PAYER_MULTIPLIER
        # A median under a second is a real answer, not zero: show a decimal
        # rather than round a fast payer's record down to "0s".
        median = buyer_pattern.median_seconds_invoice_to_payment
        shown = f"{median:.1f}" if median < 10 else f"{median:.0f}"
        parts.append(
            f"x{PROMPT_PAYER_MULTIPLIER} because this buyer has paid within a "
            f"median of {shown}s over {buyer_pattern.payments_observed} payments"
        )
    parts.append(f"buyer trust_tier={buyer_entity.trust_tier}")

    amount = round(amount, 2)
    # A free step stays free; a paid one never rounds away to nothing.
    if base > 0 and amount < PRICE_FLOOR_USDC:
        amount = PRICE_FLOOR_USDC
        parts.append(f"floored at {PRICE_FLOOR_USDC:.2f} USDC")
    return amount, f"{'; '.join(parts)} = {amount:.2f} USDC"


def earned_back(buyer_entity: BuyerLedger) -> bool:
    """Has a buyer with one default paid its way back to credit?"""
    return (
        buyer_entity.defaults == 0
        or buyer_entity.consecutive_paid_since_default >= EARN_BACK_PAID_STEPS
    )


def credit_jobs(buyer_entity: BuyerLedger) -> int:
    """Fully paid jobs that count toward credit right now.

    Every one of them, unless this buyer has been blocked: a block is worked
    off by paying, and what that buys back is the right to be served, not the
    standing they had before. Credit after a block is earned on jobs completed
    since it, by the same rule as any stranger.
    """
    if buyer_entity.defaults >= BLOCKED_MIN_DEFAULTS:
        return max(
            0,
            buyer_entity.completed_paid_jobs
            - buyer_entity.completed_paid_jobs_at_block,
        )
    return buyer_entity.completed_paid_jobs


def jobs_until_credit(buyer_entity: BuyerLedger) -> int:
    """Fully paid jobs still needed before credit is extended. 0 once earned."""
    return max(0, TRUSTED_MIN_PAID_JOBS - credit_jobs(buyer_entity))


def steps_until_credit(buyer_entity: BuyerLedger) -> int:
    """Kept for callers of the old name; same value as ``jobs_until_credit``."""
    return jobs_until_credit(buyer_entity)


def outstanding_usdc(buyer_entity: BuyerLedger) -> float:
    """What this buyer owes on closed jobs, in USDC."""
    return round(sum(item.amount_usdc for item in buyer_entity.outstanding), 2)


def steps_until_unblocked(buyer_entity: BuyerLedger) -> int:
    """Paid steps still needed before a block lifts. 0 once they are behind."""
    return max(0, UNBLOCK_PAID_STEPS - buyer_entity.consecutive_paid_since_block)


def unblock_terms(buyer_entity: BuyerLedger) -> str:
    """Exactly what a blocked buyer must do, in one clause.

    Written once and read everywhere: the REFUSE reason, the CLI ledger card,
    the API's trust explanation and the app all show this sentence, so the
    terms cannot drift between the place they are enforced and the places they
    are quoted.
    """
    owed = outstanding_usdc(buyer_entity)
    steps = steps_until_unblocked(buyer_entity)
    return (
        f"blocked after {buyer_entity.defaults} defaults: settle "
        f"{owed:.2f} USDC outstanding, then {steps} more consecutive paid "
        f"steps to be served again"
    )


def recompute_trust_tier(buyer_entity: BuyerLedger) -> TrustTier:
    """blocked at two defaults until worked off; trusted on three fully paid
    jobs and a clean record, or on a single default worked off.

    Credit is extended on a record of paying for whole jobs, not steps: a
    buyer must have let TRUSTED_MIN_PAID_JOBS jobs close with every paid step
    settled. A buyer who lets a job close with work unpaid takes a default.
    Paying the debt clears ``unpaid_from_prior_jobs`` and lifts the refusal,
    but not the credit: they buy per step, up front, until they have settled
    EARN_BACK_PAID_STEPS steps in a row without defaulting again.

    A second default blocks them, and a block is a stop rather than an ending.
    It holds while anything is still outstanding, and then while fewer than
    UNBLOCK_PAID_STEPS paid steps have been settled since it began. Clear the
    debt, pay six steps up front, and the buyer is "new" again: a stranger with
    a history, who can earn credit back by the ordinary three-fully-paid-jobs
    rule. Two further defaults block them again, with the clock back at zero.
    """
    if buyer_entity.defaults >= BLOCKED_MIN_DEFAULTS and (
        buyer_entity.unpaid_from_prior_jobs > 0
        or buyer_entity.consecutive_paid_since_block < UNBLOCK_PAID_STEPS
    ):
        return TRUST_BLOCKED
    if (
        credit_jobs(buyer_entity) >= TRUSTED_MIN_PAID_JOBS
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
        and credit_jobs(buyer_entity) >= TRUSTED_MIN_PAID_JOBS
        and buyer_entity.open_invoices == 0
        and buyer_entity.unpaid_from_prior_jobs == 0
        and earned_back(buyer_entity)
    )


def decide(
    step: int,
    buyer_entity: BuyerLedger,
    job_state: JobState,
    job_type: JobType,
) -> tuple[Decision, str]:
    """Decide whether to run ``step`` for this buyer, and say why.

    Precedence, in order:
      1. a free step is never gated — it costs the agent nothing to quote and
         it is how a new buyer is won. Every service so far makes step 1 free.
      2. REFUSE  a buyer carrying unpaid work from a completed job, or blocked.
      3. RUN_PAID       the invoice for this step is settled.
      4. RUN_ON_CREDIT  unpaid, but the buyer has earned the trusted tier.
      5. WAIT_FOR_PAYMENT.

    Returns (decision, reason). The reason names the memory facts used.
    """
    facts = (
        f"buyer completed_paid_jobs={buyer_entity.completed_paid_jobs}, "
        f"paid_steps={buyer_entity.paid_steps}, "
        f"paid_usdc={buyer_entity.paid_usdc:.2f}, "
        f"open_invoices={buyer_entity.open_invoices}, "
        f"unpaid_from_prior_jobs={buyer_entity.unpaid_from_prior_jobs}, "
        f"defaults={buyer_entity.defaults}, "
        f"consecutive_paid_since_default="
        f"{buyer_entity.consecutive_paid_since_default}, "
        f"trust_tier={buyer_entity.trust_tier}"
    )

    spec = job_type.step(step)
    if spec.base_price_usdc == 0:
        return RUN_FREE, (
            f"step {step} ({spec.name}) is free at base 0.00 USDC, so no "
            f"payment check applies; {facts}"
        )

    invoice = job_state.open_invoice
    if buyer_entity.trust_tier == TRUST_BLOCKED:
        # A blocked buyer is served a step they have already paid for, and
        # only once nothing is outstanding. That is the whole route back: the
        # money is in hand, the work is owed, and serving it is what the six
        # steps are counted from. Everything else is refused.
        if (
            buyer_entity.unpaid_from_prior_jobs == 0
            and invoice is not None
            and invoice.step == step
            and invoice.paid
        ):
            return RUN_PAID, (
                f"invoice {invoice.memo} for step {step} is settled at "
                f"{invoice.amount_usdc:.2f} USDC (tx {invoice.tx_hash}); this "
                f"buyer is blocked and buying up front, "
                f"{buyer_entity.consecutive_paid_since_block + 1} of "
                f"{UNBLOCK_PAID_STEPS} paid steps toward being served again; "
                f"{facts}"
            )
        return REFUSE, f"{unblock_terms(buyer_entity)}; {facts}"
    if buyer_entity.unpaid_from_prior_jobs > 0:
        return REFUSE, (
            f"buyer left {buyer_entity.unpaid_from_prior_jobs} step(s) unpaid on a "
            f"completed job; {facts}"
        )

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
            f"step {step} is unpaid but buyer is trusted: completed_paid_jobs="
            f"{credit_jobs(buyer_entity)} >= {TRUSTED_MIN_PAID_JOBS}, "
            f"open_invoices={buyer_entity.open_invoices}, "
            f"unpaid_from_prior_jobs={buyer_entity.unpaid_from_prior_jobs}"
            f"{earn_back}"
        )

    amount = invoice.amount_usdc if invoice is not None else spec.base_price_usdc
    if buyer_entity.defaults > 0:
        jobs_note = (
            f"; also credit after {TRUSTED_MIN_PAID_JOBS} fully paid jobs, "
            f"currently {credit_jobs(buyer_entity)}"
            if credit_jobs(buyer_entity) < TRUSTED_MIN_PAID_JOBS
            else ""
        )
        return WAIT_FOR_PAYMENT, (
            f"step {step} is unpaid at {amount:.2f} USDC and this buyer has "
            f"{buyer_entity.defaults} default(s) on record, so work is sold up "
            f"front; credit returns after {EARN_BACK_PAID_STEPS} consecutive "
            f"paid steps, currently "
            f"{buyer_entity.consecutive_paid_since_default} "
            f"({max(0, EARN_BACK_PAID_STEPS - buyer_entity.consecutive_paid_since_default)} to go)"
            f"{jobs_note} ({facts})"
        )
    return WAIT_FOR_PAYMENT, (
        f"step {step} is unpaid at {amount:.2f} USDC and the buyer has not earned "
        f"credit; credit after {TRUSTED_MIN_PAID_JOBS} fully paid jobs, currently "
        f"{credit_jobs(buyer_entity)} ({facts})"
    )
