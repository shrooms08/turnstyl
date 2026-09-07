"""The agent reads its own journal and learns something from it.

The ledger records what a buyer owes. This records how they behave: how long
they take to settle an invoice, how much of a job they buy, how often they walk
away. It is derived only from the COLD journal and the buyer entities that are
already there, it writes only ``("pattern", <address>)``, and it changes exactly
one thing downstream: a tenth off the price for a buyer who pays quickly
(``policy.price``).

Two rules it will not bend:

* nothing is inferred from a single payment. Below
  ``PROMPT_PAYER_MIN_PAYMENTS`` observations the entity records the counts it
  has and ``pays_promptly`` stays None, which prices exactly as before.
* the median, never the mean. One buyer who paid overnight once should not lose
  the discount, and one who paid instantly once should not earn it.

**Timing, stated exactly.** The journal holds an event per decision, not per
wire transfer, so "invoice to payment" is measured between two events:

* *issued* is the event that created the invoice for step N, which is the event
  that ran step N-1 for that job.
* *settled* is the ``PAID_X402`` event for step N when there is one, which is
  written at the moment the facilitator settled. Otherwise it is the
  ``RUN_PAID`` event for step N, which is when the agent noticed the payment
  and ran the step. That second case is an upper bound, and the pattern's
  ``basis`` field says so.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

from . import schema as S
from .memory import TurnstylStore

# One pass reads this much journal. The store is small and this runs hourly;
# a bigger window costs a read, not a write.
JOURNAL_WINDOW = 2000

RAN_DECISIONS = (S.RUN_FREE, S.RUN_PAID, S.RUN_ON_CREDIT)
PAID_X402 = "PAID_X402"


def parse_ts(value: Any) -> datetime | None:
    """The SDK writes ISO with a Z. Anything unparseable is skipped, not guessed."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def events_for(store: TurnstylStore, limit: int = JOURNAL_WINDOW) -> list[dict[str, Any]]:
    """Journal events oldest first, each with a parsed timestamp attached."""
    rows = []
    for event in store.read_journal(limit=limit):
        when = parse_ts(event.get("ts"))
        if when is None:
            continue
        extra = event.get("extra") or {}
        rows.append(
            {
                "at": when,
                "decision": extra.get("decision"),
                "job_id": extra.get("job_id"),
                "buyer": (extra.get("buyer") or "").strip().lower() or None,
                "step": extra.get("step"),
                "price": extra.get("price"),
                "extra": extra,
            }
        )
    rows.sort(key=lambda r: r["at"])
    return rows


def observe(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per buyer: the settle times, the steps bought per job, the jobs seen.

    Pure: takes events, returns numbers. Nothing here reads or writes memory,
    so the arithmetic can be checked against a fixture without a store.
    """
    ran_at: dict[tuple[str, int], datetime] = {}      # (job, step) -> when it ran
    paid_at: dict[tuple[str, int], datetime] = {}     # (job, step) -> when it settled
    exact: set[tuple[str, int]] = set()               # settled by a PAID_X402 event
    buyer_of: dict[str, str] = {}
    steps_of: dict[str, set[int]] = {}
    paid_steps: dict[str, list[int]] = {}

    for event in events:
        job, step, buyer = event["job_id"], event["step"], event["buyer"]
        if not job or not isinstance(step, int):
            continue
        if buyer:
            buyer_of.setdefault(job, buyer)
        if event["decision"] in RAN_DECISIONS:
            ran_at.setdefault((job, step), event["at"])
            steps_of.setdefault(job, set()).add(step)
            if event["decision"] == S.RUN_PAID:
                paid_at.setdefault((job, step), event["at"])
                paid_steps.setdefault(job, []).append(step)
        elif event["decision"] == PAID_X402:
            # The exact settlement moment beats the moment the agent noticed.
            paid_at[(job, step)] = event["at"]
            exact.add((job, step))

    out: dict[str, dict[str, Any]] = {}
    for job, buyer in buyer_of.items():
        row = out.setdefault(
            buyer, {"seconds": [], "steps_per_job": [], "jobs": set(), "exact": 0}
        )
        row["jobs"].add(job)
        if job in steps_of:
            row["steps_per_job"].append(len(steps_of[job]))

    for (job, step), settled in sorted(paid_at.items(), key=lambda kv: kv[1]):
        buyer = buyer_of.get(job)
        if not buyer:
            continue
        issued = ran_at.get((job, step - 1))
        if issued is None:
            continue                      # no event created this invoice in view
        seconds = (settled - issued).total_seconds()
        if seconds < 0:
            continue                      # clock skew or a replayed event
        out[buyer]["seconds"].append(seconds)
        if (job, step) in exact:
            out[buyer]["exact"] += 1
    return out


def pattern_for(
    address: str, observed: dict[str, Any], ledger: S.BuyerLedger
) -> S.BuyerPattern:
    """Turn one buyer's observations into the entity that gets written."""
    seconds = sorted(observed.get("seconds") or [])
    steps_per_job = observed.get("steps_per_job") or []
    jobs = len(observed.get("jobs") or ())
    exact = int(observed.get("exact") or 0)

    median = round(statistics.median(seconds), 1) if seconds else None
    closed = ledger.completed_paid_jobs + ledger.defaults
    default_rate = round(ledger.defaults / closed, 3) if closed else None

    prompt: bool | None = None
    if len(seconds) >= S.PROMPT_PAYER_MIN_PAYMENTS:
        prompt = bool(median is not None and median < S.PROMPT_PAYER_MAX_SECONDS)

    if not seconds:
        basis = "no invoice was seen settled in the journal window"
    elif exact == len(seconds):
        basis = "measured from the invoice to the x402 settlement event"
    elif exact:
        basis = (
            f"{exact} of {len(seconds)} measured to the x402 settlement event, "
            f"the rest to the step running, which is an upper bound"
        )
    else:
        basis = (
            "measured from the invoice to the step running, which is an upper "
            "bound on when the payment landed"
        )

    return S.BuyerPattern(
        address=address,
        payments_observed=len(seconds),
        median_seconds_invoice_to_payment=median,
        steps_per_job_median=(
            round(statistics.median(steps_per_job), 1) if steps_per_job else None
        ),
        jobs_observed=jobs,
        default_rate=default_rate,
        pays_promptly=prompt,
        basis=basis,
    )


def reflect(store: TurnstylStore, limit: int = JOURNAL_WINDOW) -> list[S.BuyerPattern]:
    """Read the journal, write one pattern per buyer it saw. Returns them."""
    observed = observe(events_for(store, limit=limit))
    written: list[S.BuyerPattern] = []
    for address in sorted(observed):
        pattern = pattern_for(address, observed[address], store.get_buyer(address))
        store.put_pattern(pattern)
        written.append(pattern)
    return written


def summary(patterns: list[S.BuyerPattern]) -> str:
    """One line for the CLI and the worker log."""
    if not patterns:
        return "reflection: no buyer had a settled invoice in the journal window"
    prompt = [p for p in patterns if p.pays_promptly]
    return (
        f"reflection: {len(patterns)} buyer pattern(s) written, "
        f"{len(prompt)} paying promptly "
        f"(median under {S.PROMPT_PAYER_MAX_SECONDS}s over at least "
        f"{S.PROMPT_PAYER_MIN_PAYMENTS} payments)"
    )
