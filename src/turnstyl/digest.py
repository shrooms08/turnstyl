"""What the agent did today, counted from what it wrote down.

Everything here is derived from the COLD journal and the WARM entities that are
already in the store. The one thing it writes is ``("digest", <YYYY-MM-DD>)``:
the day's figures, consolidated, so a later digest of the same day is a single
entity read rather than a walk of the journal. That is the consolidation
primitive doing real work rather than being named in a table.

Model spend is an estimate and is labelled one: token counts are recorded per
step, the price per million tokens is not, so the figure is the recorded tokens
priced at the list rate for the configured model.
"""
from __future__ import annotations

import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from . import schema as S
from .memory import TurnstylStore, archived_job_ids, read_archived_job
from .reflect import PAID_X402, RAN_DECISIONS, events_for, parse_ts

# List price per million tokens, the same table scripts/eval.py uses. Only ever
# multiplied by token counts the agent recorded itself.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (15.00, 75.00),
}
FALLBACK_PRICE = (1.00, 5.00)

# Figures a caller with no credential may see: counts about the whole store and
# nothing that names a buyer, a job, or a contract. The same rule /api/stats
# follows, applied to the same kind of data.
PUBLIC_FIGURES = (
    "jobs_opened",
    "jobs_completed",
    "usdc_settled",
    "steps_served_from_memory",
    "steps_run",
    "new_buyers",
    "trust_changes",
    "defaults",
    "refusals",
    "injection_flags",
    "median_seconds_payment_to_output",
    "payment_to_output_observations",
)


def all_job_ids(store: TurnstylStore) -> list[str]:
    """Every job the store can still name: active, archived, and remembered by
    a buyer's ledger. The same three sources the API's job list walks."""
    seen = set(store.get_active_jobs()) | set(archived_job_ids(store.db_path))
    for row in store.memory.list_entities(S.CAT_BUYER, limit=200):
        seen.update(S.BuyerLedger.model_validate(row["body"]).jobs)
    return sorted(seen)


def model_price() -> tuple[str, tuple[float, float]]:
    model = os.environ.get("LLM_MODEL") or "claude-haiku-4-5"
    return model, PRICES_PER_MTOK.get(model, FALLBACK_PRICE)


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def compute(store: TurnstylStore, days: int = 1) -> dict[str, Any]:
    """The figures for the last ``days`` days, from the journal and entities."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    events = [e for e in events_for(store) if e["at"] >= since]

    jobs_opened: set[str] = set()
    jobs_completed: set[str] = set()
    buyers_seen: set[str] = set()
    steps_run = 0
    refusals = 0
    injection_flags = 0
    usdc = 0.0
    paid_at: dict[tuple[str, int], datetime] = {}
    payment_to_output: list[float] = []

    for event in events:
        decision, job, step = event["decision"], event["job_id"], event["step"]
        if job:
            jobs_opened.add(job) if decision == S.RUN_FREE else None
        if event["buyer"]:
            buyers_seen.add(event["buyer"])
        if decision in RAN_DECISIONS:
            steps_run += 1
            if decision == S.RUN_PAID and isinstance(event["price"], (int, float)):
                usdc += float(event["price"])
            key = (job, step)
            if key in paid_at:
                seconds = (event["at"] - paid_at[key]).total_seconds()
                if seconds >= 0:
                    payment_to_output.append(seconds)
        elif decision == PAID_X402:
            paid_at[(job, step)] = event["at"]
        elif decision == S.REFUSE:
            refusals += 1
        elif decision == "FLAGGED_UNTRUSTED_SOURCE":
            injection_flags += int((event["extra"] or {}).get("injection_flags") or 0)

    # Entities carry what the journal cannot: which jobs actually closed, what
    # each step cost in tokens, whether it came out of memory, and every
    # contract's audit count.
    model, price = model_price()
    tokens_in = tokens_out = 0
    cached_steps = 0
    contracts: dict[str, dict[str, Any]] = {}
    new_buyers = 0
    trust_changes = 0
    defaults = 0

    for job_id in all_job_ids(store):
        state = store.get_job_state(job_id)
        if state is None:
            continue
        created = parse_ts(state.created_at)
        if created is not None and created >= since:
            jobs_opened.add(job_id)
        updated = parse_ts(state.updated_at)
        if state.status == S.STATUS_COMPLETE and updated is not None and updated >= since:
            jobs_completed.add(job_id)

        entity = store.get_job_entity(job_id)
        if entity is None:
            archive = read_archived_job(store.db_path, job_id)
            if archive is None:
                continue
            entity = S.JobEntity.model_validate(archive["body"])
        contract = state.contract_hash or entity.contract_hash
        if contract:
            seen = contracts.setdefault(
                contract, {"contract_hash": contract, "jobs": 0, "steps_from_memory": 0}
            )
            seen["jobs"] += 1
        for record in entity.steps.values():
            if record.cached:
                cached_steps += 1
                if contract:
                    contracts[contract]["steps_from_memory"] += 1
            tokens_in += record.input_tokens or 0
            tokens_out += record.output_tokens or 0

    for row in store.memory.list_entities(S.CAT_BUYER, limit=200):
        ledger = S.BuyerLedger.model_validate(row["body"])
        defaults += ledger.defaults
        if ledger.trust_tier != S.TRUST_NEW:
            trust_changes += 1
        created = parse_ts(row.get("created_at") or row.get("updated_at"))
        if created is not None and created >= since:
            new_buyers += 1

    spend = tokens_in / 1e6 * price[0] + tokens_out / 1e6 * price[1]
    repeat = sorted(
        (c for c in contracts.values() if c["jobs"] > 0),
        key=lambda c: (-c["jobs"], c["contract_hash"]),
    )[:3]

    return {
        "jobs_opened": len(jobs_opened),
        "jobs_completed": len(jobs_completed),
        "usdc_settled": round(usdc, 2),
        "steps_served_from_memory": cached_steps,
        "steps_run": steps_run,
        "model_spend_usd_estimated": round(spend, 4),
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "new_buyers": new_buyers,
        "trust_changes": trust_changes,
        "defaults": defaults,
        "refusals": refusals,
        "injection_flags": injection_flags,
        "median_seconds_payment_to_output": (
            round(statistics.median(payment_to_output), 1) if payment_to_output else None
        ),
        "payment_to_output_observations": len(payment_to_output),
        "top_contracts_by_repeat_audits": [
            {"contract_hash": c["contract_hash"][:16], "jobs": c["jobs"],
             "steps_from_memory": c["steps_from_memory"]}
            for c in repeat
        ],
        "buyers_active": len(buyers_seen),
    }


def build(store: TurnstylStore, days: int = 1, write: bool = True) -> S.DigestEntity:
    """Compute the digest and consolidate it under today's date.

    The write is the whole point of the consolidation chip: a day that has
    already been counted is one entity read away next time.
    """
    figures = compute(store, days=days)
    entity = S.DigestEntity(date=today(), days=max(1, int(days)), figures=figures)
    if write:
        store.put_digest(entity)
    return entity


def public(figures: dict[str, Any]) -> dict[str, Any]:
    """Counts only, nobody named: what /api/digest answers without a credential."""
    return {k: figures.get(k) for k in PUBLIC_FIGURES}


def lines(entity: S.DigestEntity) -> list[str]:
    """The digest as text, for the CLI."""
    f = entity.figures
    window = "today" if entity.days == 1 else f"the last {entity.days} days"
    median = f.get("median_seconds_payment_to_output")
    top = f.get("top_contracts_by_repeat_audits") or []
    return [
        f"turnstyl digest for {window} ({entity.date})",
        "",
        f"  jobs opened              {f.get('jobs_opened')}",
        f"  jobs completed           {f.get('jobs_completed')}",
        f"  USDC settled             {f.get('usdc_settled'):.2f}",
        f"  steps run                {f.get('steps_run')}",
        f"  steps served from memory {f.get('steps_served_from_memory')}",
        f"  model spend (estimated)  ${f.get('model_spend_usd_estimated'):.4f} "
        f"on {f.get('model')} ({f.get('tokens_in'):,} in / {f.get('tokens_out'):,} out)",
        f"  new buyers               {f.get('new_buyers')}",
        f"  trust changes            {f.get('trust_changes')}",
        f"  defaults                 {f.get('defaults')}",
        f"  refusals                 {f.get('refusals')}",
        f"  injection flags raised   {f.get('injection_flags')}",
        f"  payment to output        "
        + (
            f"median {median:.1f}s over {f.get('payment_to_output_observations')} payment(s)"
            if median is not None
            else "not observed in this window"
        ),
        "",
        "  top contracts by repeat audits:"
        if top
        else "  top contracts by repeat audits: none yet",
        *[
            f"    {c['contract_hash']}...  {c['jobs']} job(s), "
            f"{c['steps_from_memory']} step(s) from memory"
            for c in top
        ],
        "",
        f"  consolidated as entity digest/{entity.date}",
    ]
