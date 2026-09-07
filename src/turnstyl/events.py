"""Journal events that record a fact rather than a decision.

A decision event says what the agent chose and why. These two say what
happened: money arrived, or a buyer's standing changed. They are written from
three different places each (every payment rail; every point that recomputes a
tier), so they live here rather than in whichever module got there first, and
because ``payments`` cannot import ``engine`` without a cycle.

Both are read back by ``reflect`` and ``digest``. Before they existed those two
had to infer a settlement time from the moment the agent *noticed* a payment,
which is an upper bound; with these the figures mean what their labels say.
"""
from __future__ import annotations

from typing import Any

from . import schema as S
from .memory import TurnstylStore

# Facts, not decisions. Anything reading the journal for what the agent chose
# must skip these; anything reading it for what happened must not.
PAYMENT_SEEN = "PAYMENT_SEEN"
TRUST_CHANGED = "TRUST_CHANGED"
FACTS = (PAYMENT_SEEN, TRUST_CHANGED)

# How an invoice was settled, in the words the event uses.
RAIL_FAKE = "the fake backend"
RAIL_RECEIPTS = "the receipts contract"
RAIL_X402 = "x402"


def rail_for(payments: Any, job_id: str, step: int) -> str:
    """Which rail settled this invoice, asked of the backend that would know."""
    try:
        if payments.x402_paid(job_id, step):
            return RAIL_X402
    except Exception:  # noqa: BLE001 - a backend without the x402 rail
        pass
    return RAIL_RECEIPTS if getattr(payments, "name", "") == "base" else RAIL_FAKE


def payment_seen(
    store: TurnstylStore,
    state: S.JobState,
    invoice: S.OpenInvoice,
    rail: str,
    tx_hash: str | None,
) -> None:
    """One event per invoice, at the moment it is first seen settled.

    Written by whichever rail noticed: the fake backend's mark, the Paid-log
    sync, or the x402 recorder. Carries the invoice's own issue timestamp, so a
    later reader can time the settlement without pairing two events and without
    guessing which earlier event created the invoice.

    Callers must only call this when they actually flipped the invoice from
    unpaid to paid, so an invoice produces exactly one of these.
    """
    tx = tx_hash or "no transaction"
    summary = (
        f"Payment of {invoice.amount_usdc:.2f} USDC for step {invoice.step} "
        f"seen on {rail} ({tx})."
    )
    store.journal(
        S.JournalEntry(
            evaluated=[
                f"{S.job_state_key(state.job_id)} -> open_invoice step "
                f"{invoice.step} at {invoice.amount_usdc:.2f} USDC, issued "
                f"{invoice.issued_at}",
                f"invoice memo {invoice.memo} -> settled on {rail}",
            ],
            acted=[summary],
            forward=[f"run step {invoice.step} of {state.job_id}"],
            extra={
                "job_id": state.job_id,
                "buyer": state.buyer,
                "step": invoice.step,
                "decision": PAYMENT_SEEN,
                "amount": invoice.amount_usdc,
                "price": invoice.amount_usdc,
                "rail": rail,
                "tx": tx_hash,
                "issued_at": invoice.issued_at,
                "summary": summary,
            },
        )
    )


def trust_changed(
    store: TurnstylStore,
    buyer: str,
    before: str,
    after: str,
    ledger: S.BuyerLedger,
    reason: str = "",
) -> None:
    """One event when a buyer's tier actually moves. No event when it does not.

    The tier is recomputed on every close and every reconcile, so writing an
    event each time would drown the journal in restatements of the same fact.
    """
    if before == after:
        return
    short = f"{buyer[:6]}…{buyer[-4:]}" if len(buyer) > 12 else buyer
    why = reason or (
        f"completed_paid_jobs={ledger.completed_paid_jobs}, "
        f"defaults={ledger.defaults}, "
        f"unpaid_from_prior_jobs={ledger.unpaid_from_prior_jobs}, "
        f"consecutive_paid_since_default={ledger.consecutive_paid_since_default}"
    )
    summary = f"Buyer {short} is now {after} ({why})."
    store.journal(
        S.JournalEntry(
            evaluated=[f"entity buyer/{buyer} -> trust_tier was {before}; {why}"],
            acted=[summary],
            forward=[
                "credit is extended"
                if after == S.TRUST_TRUSTED
                else "paid work is sold up front"
            ],
            extra={
                "buyer": buyer,
                "decision": TRUST_CHANGED,
                "from": before,
                "to": after,
                "summary": summary,
            },
        )
    )
