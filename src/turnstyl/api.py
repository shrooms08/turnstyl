"""HTTP view of turnstyl's memory, plus one way in.

Every GET answers from the same Sibyl Memory store the agent uses, through the
same ``TurnstylMemory`` wrapper, and writes nothing. The one write endpoint,
``POST /api/jobs``, hands a contract to the engine exactly as the CLI's
``job new`` does; the engine, not this module, decides what to store. The store
is only opened when the database file already exists, so no request can bring
one into being: after the delete beat, POST answers 409 and every GET answers
200 with ``memory_missing: true``.

The delete beat is a first-class case. When the file is gone, ``/api/status``
reports ``db_exists: false`` and every other endpoint answers 200 with empty
data and ``memory_missing: true``. The page stays up while the memory does not.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth
from . import policy
from . import schema as S
from .engine import Engine
from . import jobtypes
from .jobtypes import GATE_COMPILE, GATE_FORGE_TEST
from .memory import TENANT_ID, TurnstylMemory, TurnstylStore, count_records, default_db_path
from .payments import (
    ERC20_ABI,
    PAY_METHOD_X402,
    RECEIPTS_ABI,
    get_backend,
    hex0x,
    memo_bytes32,
)

CHAIN_ID = 84532
EXPLORER = "https://sepolia.basescan.org"

# What the page needs to pay from the browser, served rather than hardcoded:
# the receipts contract's pay() and Paid, and the USDC calls the flow makes.
PAGE_RECEIPTS_ABI = [e for e in RECEIPTS_ABI if e.get("name") in ("pay", "Paid")]
PAGE_USDC_ABI = [e for e in ERC20_ABI if e.get("name") in ("approve", "allowance", "balanceOf")] + [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    # The EIP-712 domain the x402 authorisation is signed against. The 402's
    # `extra` carries these too; the page reads them off the contract and uses
    # extra only as a fallback, so a wrong domain cannot come from the server.
    {
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "version",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
]

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(
    title="turnstyl",
    description="The agent's memory over HTTP, and one endpoint that gives it work.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# The page is published on GitHub Pages and talks to this API through a
# tunnel, so it arrives from another origin. Exactly these origins, GET and
# POST, and the headers the page sends. Nothing wider.
ALLOWED_ORIGINS = [
    "https://shrooms08.github.io",
    "http://127.0.0.1:8787",
    "http://localhost:8787",
]

logger = logging.getLogger("turnstyl.api")

# Daily cap on job creation, on top of the per-minute limit below. In-process,
# keyed by UTC date, so it resets at UTC midnight (and on restart, which is
# acceptable for a demo agent; a real deployment would count in the store).
MAX_JOBS_PER_DAY = int(os.environ.get("MAX_JOBS_PER_DAY") or 150)
_daily = {"date": "", "count": 0}
_daily_lock = threading.Lock()


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def daily_remaining() -> int:
    with _daily_lock:
        if _daily["date"] != _utc_day():
            _daily["date"], _daily["count"] = _utc_day(), 0
        return max(0, MAX_JOBS_PER_DAY - _daily["count"])


def daily_take() -> bool:
    """Reserve one creation for today; False when the cap is spent."""
    with _daily_lock:
        if _daily["date"] != _utc_day():
            _daily["date"], _daily["count"] = _utc_day(), 0
        if _daily["count"] >= MAX_JOBS_PER_DAY:
            return False
        _daily["count"] += 1
        return True


def daily_give_back() -> None:
    with _daily_lock:
        if _daily["date"] == _utc_day() and _daily["count"] > 0:
            _daily["count"] -= 1


# ----------------------------------------------------------------------
# Store access
# ----------------------------------------------------------------------
def db_path() -> Path:
    """Resolved per request, so the server follows TURNSTYL_DB and survives a
    database that is deleted and recreated underneath it."""
    return default_db_path()


def open_store() -> TurnstylStore | None:
    """A store handle, or None when there is no database to read.

    The existence check comes first on purpose: ``TurnstylMemory`` bootstraps a
    schema on open, so constructing one against a missing path would CREATE the
    database. A read-only API must not do that, least of all in the seconds
    after the delete beat.
    """
    path = db_path()
    if not path.is_file():
        return None
    return TurnstylStore(TurnstylMemory(path))


def missing(payload: dict[str, Any]) -> dict[str, Any]:
    """The answer shape when memory is gone: 200, empty, and honest about it."""
    return {"memory_missing": True, "db_path": str(db_path()), **payload}


def read_archived_job(path: Path, job_id: str) -> dict[str, Any] | None:
    """Read one archived job entity straight from the store, read-only.

    The SDK archives entities (``archive_entity``) but exposes no reader for
    them — ``archive_entity`` is the only public name containing "archiv", and
    nothing in the package selects from ``archived_entities``. A completed job's
    per-step record would otherwise be invisible to this API, so it is read here
    over a ``mode=ro`` connection, which the driver refuses to write through.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT body, archived_at FROM archived_entities "
            "WHERE tenant_id = ? AND category = ? AND name = ?",
            (TENANT_ID, S.CAT_JOB, job_id),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        return {"body": json.loads(row["body"]), "archived_at": row["archived_at"]}
    except (json.JSONDecodeError, ValueError, IndexError):
        return None


def record_count(path: Path) -> int:
    """State keys + entities + journal events held in the store (see
    ``memory.count_records``; one implementation, shared with the CLI)."""
    return count_records(path)


# ----------------------------------------------------------------------
# Who is asking: public, the buyer who paid, or the operator
# ----------------------------------------------------------------------
# What a job says is the thing the buyer bought. That it exists, how far it
# got, and what it cost stay public — the meter is the demo — but findings,
# patches, test suites, the contract itself and the buyer's ledger are theirs.
# See auth.py for the three identities and the login message.
def caller(authorization: str | None) -> auth.Identity:
    return auth.identify(authorization)


SIGN_IN_HINT = (
    "Sign in with that wallet: GET /api/auth/nonce?address=<address>, sign the "
    "message it returns, POST /api/auth/verify, then send "
    "Authorization: Bearer <token>. An operator sends OPERATOR_TOKEN instead."
)


def require_visible(ident: auth.Identity, buyer: str | None, what: str) -> None:
    """Let the buyer who paid, and the operator, through. Nobody else."""
    if ident.sees(buyer):
        return
    if not ident.signed_in:
        raise HTTPException(
            status_code=401,
            detail=f"{what} is private to the buyer who paid for it. {SIGN_IN_HINT}",
        )
    raise HTTPException(
        status_code=403,
        detail=(
            f"{what} belongs to {buyer}; you are signed in as {ident.address}. "
            f"A session only opens the jobs of the address that signed it."
        ),
    )


def redact_job_summary(row: dict[str, Any], ident: auth.Identity) -> dict[str, Any]:
    """A job row anyone may read: it exists, whose shape it has, how far it got."""
    if ident.sees(row.get("buyer")):
        return row
    out = dict(row)
    out["buyer"] = auth.trunc_address(row.get("buyer"))
    out["contract_hash"] = None
    out["redacted"] = True
    return out


def redact_job_detail(detail: dict[str, Any], ident: auth.Identity) -> dict[str, Any]:
    """The job's shape without its contents.

    Prices, statuses, payment and commit transactions stay: they are on a
    public chain already, and the output hash is the very thing the agent
    published. The outputs those hashes commit to do not.
    """
    if ident.sees(detail.get("buyer")):
        return detail
    out = dict(detail)
    out["buyer"] = auth.trunc_address(detail.get("buyer"))
    out["contract_hash"] = None
    out["steps"] = [dict(s, output=None) for s in (detail.get("steps") or [])]
    out["redacted"] = True
    out["private"] = (
        "step outputs and the contract are visible to the buyer who paid for "
        "them, and to the operator"
    )
    return out


def redact_journal_event(event: dict[str, Any], ident: auth.Identity) -> dict[str, Any]:
    """One decision, one sentence. No evaluated lines, no acted lines.

    The evaluated lines are the memory reads — key names, prices, ledger
    counters — and the acted lines quote what was produced. Publicly this is
    the decision, when it happened, which step, and the sentence the engine
    wrote to explain itself.
    """
    extra = event.get("extra") or {}
    if ident.sees(extra.get("buyer")):
        return event
    decision = event.get("decision")
    step = event.get("step")
    summary = extra.get("summary") or (
        f"{decision} on step {step}" if decision else "a decision was recorded"
    )
    return {
        "ts": event.get("ts"),
        "decision": decision,
        "step": step,
        "buyer": None,
        "evaluated": [],
        "acted": [],
        "forward": [],
        "extra": {"decision": decision, "step": step, "summary": summary},
        "redacted": True,
    }


def redact_buyer(payload: dict[str, Any], ident: auth.Identity) -> dict[str, Any]:
    """Trust tier and how many jobs were paid in full. Nothing else.

    How much a wallet has spent, what it still owes and which jobs are its own
    are the buyer's business; whether the agent will extend it credit is the
    part the demo is about.
    """
    if ident.sees(payload.get("buyer")):
        return payload
    ledger = payload.get("ledger") or {}
    trust = payload.get("trust") or {}
    tier = ledger.get("trust_tier") or trust.get("trust_tier")
    completed = ledger.get("completed_paid_jobs")
    return {
        "memory_missing": False,
        "buyer": auth.trunc_address(payload.get("buyer")),
        "known": payload.get("known"),
        "ledger": {"trust_tier": tier, "completed_paid_jobs": completed},
        "trust": {"trust_tier": tier, "completed_paid_jobs": completed},
        "outstanding": [],
        "jobs": [],
        "redacted": True,
        "private": (
            "paid steps, USDC paid, outstanding invoices and defaults are "
            "visible to this buyer, and to the operator"
        ),
        "source": "entity buyer/<address>, reduced to the public facts",
    }


def archived_job_ids(path: Path) -> list[str]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT name FROM archived_entities WHERE tenant_id = ? AND category = ?",
            (TENANT_ID, S.CAT_JOB),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return [r[0] for r in rows]


# ----------------------------------------------------------------------
# /api/status
# ----------------------------------------------------------------------
@app.get("/api/status")
def api_status() -> dict[str, Any]:
    path = db_path()
    exists = path.is_file()
    return {
        "db_path": str(path),
        "db_exists": exists,
        "db_size_bytes": path.stat().st_size if exists else 0,
        "records": record_count(path) if exists else 0,
        "tenant": TENANT_ID,
        "agent_address": os.environ.get("AGENT_ADDRESS"),
        "receipts_address": os.environ.get("RECEIPTS_ADDRESS"),
        "chain_id": CHAIN_ID,
        "explorer": EXPLORER,
        "payments_backend": (os.environ.get("PAYMENTS") or "fake").strip().lower(),
        "usdc_address": os.environ.get("USDC_ADDRESS"),
        "receipts_abi": PAGE_RECEIPTS_ABI,
        "usdc_abi": PAGE_USDC_ABI,
        "max_jobs_per_day": MAX_JOBS_PER_DAY,
        "remaining_today": daily_remaining(),
        "job_types": [t.to_public() for t in jobtypes.all_types()],
        "default_job_type": jobtypes.DEFAULT_TYPE_ID,
        "x402": {
            "enabled": bool(x402_status["enabled"]),
            "network": X402_NETWORK,
            "facilitator": X402_FACILITATOR,
            "reason": x402_status["reason"],
        },
        "memory_missing": not exists,
    }


# ----------------------------------------------------------------------
# /api/auth: prove you hold the wallet, get a session
# ----------------------------------------------------------------------
class VerifyLoginRequest(BaseModel):
    address: str = Field(description="Buyer wallet, 0x + 40 hex.")
    signature: str = Field(description="personal_sign over the message from /api/auth/nonce.")
    nonce: str | None = Field(
        default=None, description="Optional: the nonce that was signed, when the caller kept it."
    )


@app.get("/api/auth/nonce")
def api_auth_nonce(
    address: str = Query(description="The wallet that wants to sign in.")
) -> dict[str, Any]:
    """A one-time nonce, and the exact message to sign with it.

    The message is returned in full so the wallet signs bytes this server
    built. It expires in five minutes and can be used once.
    """
    addr = auth.normalise(address)
    if not ADDRESS_RE.match(addr):
        raise HTTPException(
            status_code=400,
            detail="address must be a 0x-prefixed 40-hex-character address",
        )
    return auth.issue_nonce(addr)


@app.post("/api/auth/verify")
def api_auth_verify(body: VerifyLoginRequest) -> dict[str, Any]:
    """Exchange a signature for a session token, valid 24 hours."""
    try:
        session = auth.verify_login(body.address, body.signature, body.nonce)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"signed_in": True, **session}


@app.get("/api/auth/me")
def api_auth_me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """What this request's credential is worth. No credential is not an error."""
    ident = caller(authorization)
    return {
        "kind": ident.kind,
        "address": ident.address,
        "signed_in": ident.signed_in,
        "operator": ident.is_operator,
    }


# ----------------------------------------------------------------------
# /api/stats: the public figures, with nobody named
# ----------------------------------------------------------------------
# Six numbers about the whole store and not one fact about any single job or
# buyer. This is what the story page's operator strip and the app's header
# line read, so neither needs the job list, which is an operator view.
STATS_TTL_SECONDS = 10.0
_stats_cache: tuple[float, dict[str, Any]] | None = None
_stats_lock = threading.Lock()


def journal_count(path: Path) -> int:
    """Journal events in the store, counted over a read-only connection.

    The SDK's read_events clamps its limit and cannot report a true total, the
    same reason memory.count_records goes to sqlite directly.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        total = conn.execute("SELECT count(*) FROM journal_events").fetchone()[0]
        conn.close()
    except sqlite3.Error:
        return 0
    return int(total)


def compute_stats(store: TurnstylStore) -> dict[str, Any]:
    """Count the store. Walks every job entity, hence the cache above."""
    jobs = every_job(store)
    buyers = {j["buyer"] for j in jobs if j.get("buyer")}
    completed = sum(1 for j in jobs if j["status"] == S.STATUS_COMPLETE)

    # USDC settled is the buyers' own ledgers added up, not a re-derivation
    # from step records: paid_usdc is the number the policy itself acts on.
    settled = 0.0
    for address in buyers:
        settled += store.get_buyer(store.buyer_key(address)).paid_usdc

    cached_steps = 0
    for j in jobs:
        entity = store.get_job_entity(j["job_id"])
        if entity is None:
            archive = read_archived_job(db_path(), j["job_id"])
            if archive is None:
                continue
            entity = S.JobEntity.model_validate(archive["body"])
        cached_steps += sum(1 for rec in entity.steps.values() if rec.cached)

    return {
        "memory_missing": False,
        "jobs": len(jobs),
        "jobs_completed": completed,
        "buyers": len(buyers),
        "usdc_settled": round(settled, 2),
        "decisions": journal_count(db_path()),
        "served_from_memory": cached_steps,
        "cache_seconds": int(STATS_TTL_SECONDS),
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": (
            "job states, each buyer's ledger, the job entities' step records, "
            "and a count of the journal table. No job id, address or output "
            "appears in this response."
        ),
    }


@app.get("/api/stats")
def api_stats() -> dict[str, Any]:
    """The public figures. No auth, and nothing here identifies anyone."""
    global _stats_cache
    now = time.monotonic()
    with _stats_lock:
        if _stats_cache is not None and now - _stats_cache[0] < STATS_TTL_SECONDS:
            return _stats_cache[1]

    store = open_store()
    if store is None:
        # Not cached: the delete beat must show as soon as the file goes.
        return missing(
            {
                "jobs": 0,
                "jobs_completed": 0,
                "buyers": 0,
                "usdc_settled": 0.0,
                "decisions": 0,
                "served_from_memory": 0,
            }
        )
    stats = compute_stats(store)
    with _stats_lock:
        _stats_cache = (time.monotonic(), stats)
    return stats


@app.get("/api/job_types")
def api_job_types() -> dict[str, Any]:
    """The services on offer, with their steps, prices and gates.

    A job type is a spec; the engine, memory, payments, credit and verify
    underneath are shared, so this is the whole of what differs between them.
    """
    return {
        "memory_missing": not db_path().is_file(),
        "default": jobtypes.DEFAULT_TYPE_ID,
        "job_types": [t.to_public() for t in jobtypes.all_types()],
    }


# ----------------------------------------------------------------------
# /api/jobs
# ----------------------------------------------------------------------
def job_summary(state: S.JobState, archived: bool, source: str) -> dict[str, Any]:
    return {
        "job_id": state.job_id,
        "buyer": state.buyer,
        "contract_hash": state.contract_hash,
        "job_type": state.job_type,
        "current_step": state.current_step,
        "status": state.status,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "archived": archived,
        "source": source,
    }


def every_job(store: TurnstylStore) -> list[dict[str, Any]]:
    """Every job this store can still name, newest first.

    Three sources, in order, because a job id outlives its entity: the active
    list, the read-only archive table, and each buyer's own jobs list. Shared
    by the job list and by /api/stats so the two can never disagree about how
    many jobs the agent is carrying.
    """
    path = db_path()
    active = set(store.get_active_jobs())
    archived = set(archived_job_ids(path))

    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job_id in sorted(active | archived):
        state = store.get_job_state(job_id)
        if state is None:
            continue
        seen.add(job_id)
        jobs.append(
            job_summary(
                state,
                archived=job_id in archived,
                source=(
                    "state + archived_entities (read-only)"
                    if job_id in archived
                    else "state + active_jobs"
                ),
            )
        )

    # Anything a buyer remembers being part of that neither list caught — a job
    # whose entity was archived before this build, say. Kept because the buyer
    # ledger is the other place a job id is durably written down.
    for row in store.memory.list_entities(S.CAT_BUYER, limit=200):
        for job_id in S.BuyerLedger.model_validate(row["body"]).jobs:
            if job_id in seen:
                continue
            state = store.get_job_state(job_id)
            if state is None:
                continue
            seen.add(job_id)
            jobs.append(
                job_summary(state, archived=True, source="state + buyer.jobs")
            )

    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs


@app.get("/api/jobs")
def api_jobs(
    buyer: str | None = Query(default=None, description="Only this buyer's jobs."),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """One buyer's jobs, or every job for the operator.

    An index of who has bought what is not part of the meter. The public
    figures live at /api/stats, which counts without naming anyone; a buyer
    asks for their own address; only the operator may ask for all of them.
    """
    ident = caller(authorization)
    buyer_key = buyer.strip().lower() if buyer else None
    if buyer_key is None:
        if not ident.is_operator:
            raise HTTPException(
                status_code=403,
                detail=(
                    "the job list is an operator view. Ask for one buyer's jobs "
                    "with ?buyer=<address> and a session for that address, or "
                    "use GET /api/stats for the public figures."
                ),
            )
    else:
        require_visible(ident, buyer_key, f"the job list for {buyer_key}")

    store = open_store()
    if store is None:
        return missing({"jobs": [], "buyer": buyer, "source": "no database"})

    jobs = every_job(store)
    if buyer_key:
        jobs = [j for j in jobs if j["buyer"] == buyer_key]
    jobs = [redact_job_summary(j, ident) for j in jobs]
    return {
        "memory_missing": False,
        "buyer": buyer_key,
        "viewer": ident.kind,
        "jobs": jobs,
        "source": (
            "The SDK archives entities but exposes no reader for them, so "
            "completed jobs are recovered from the archived_entities table over "
            "a read-only connection, then from each buyer's jobs list. Every "
            "row names its own source."
        ),
    }


# ----------------------------------------------------------------------
# /api/jobs/{job_id}
# ----------------------------------------------------------------------
def step_view(spec: jobtypes.JobType, step: int, record: S.StepRecord) -> dict[str, Any]:
    step_spec = spec.step(step)
    view = {
        "step": step,
        "name": step_spec.name,
        "status": "done",
        "price_usdc": record.price_usdc,
        "paid": record.paid,
        "cached": record.cached,
        "tokens_in": record.input_tokens,
        "tokens_out": record.output_tokens,
        "seconds": record.seconds,
        "output_sha256": record.output_sha256,
        "commit_tx": record.commit_tx,
        "pay_tx": record.tx_hash,
        "pay_method": record.pay_method,
        "output": record.output,
    }
    if step_spec.gate == GATE_COMPILE:
        view["compiles"] = record.compiles
    if step_spec.gate == GATE_FORGE_TEST:
        view["compiles"] = record.compiles
        view["tests_total"] = record.tests_total
        view["tests_passed"] = record.tests_passed
        view["tests_failed"] = record.tests_failed
    return view


def not_started_view(spec: jobtypes.JobType, step: int) -> dict[str, Any]:
    """A step the job has not reached. Same keys as a recorded step, so a
    consumer can render the cards from one shape."""
    step_spec = spec.step(step)
    view = {
        "step": step,
        "name": step_spec.name,
        "status": "not_started",
        "price_usdc": step_spec.base_price_usdc,
        "paid": False,
        "cached": False,
        "tokens_in": None,
        "tokens_out": None,
        "seconds": None,
        "output_sha256": None,
        "commit_tx": None,
        "pay_tx": None,
        "pay_method": None,
        "output": None,
    }
    if step_spec.gate in (GATE_COMPILE, GATE_FORGE_TEST):
        view["compiles"] = None
    if step_spec.gate == GATE_FORGE_TEST:
        view["tests_total"] = None
        view["tests_passed"] = None
        view["tests_failed"] = None
    return view


def job_detail(store: TurnstylStore, job_id: str) -> dict[str, Any]:
    """The full view of one job. Shared by GET and by the POST that made it."""
    state = store.get_job_state(job_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no job {job_id!r} in {db_path()}. Try GET /api/jobs for the "
                f"job ids this store knows about."
            ),
        )

    spec = jobtypes.get(state.job_type)
    entity = store.get_job_entity(job_id)
    entity_source = "entity job/<id>"
    archived_at = None
    if entity is None:
        archive = read_archived_job(db_path(), job_id)
        if archive is not None:
            entity = S.JobEntity.model_validate(archive["body"])
            archived_at = archive["archived_at"]
            entity_source = "archived_entities (read-only)"
        else:
            entity_source = "none: the job entity is gone and not in the archive"

    # The shape is always complete: every step 1-4 is present, and one that has
    # not run yet says so and carries its base price from the pricing rules.
    recorded = {}
    if entity is not None:
        recorded = {
            int(k): step_view(spec, int(k), record)
            for k, record in entity.steps.items()
        }
    steps = [recorded.get(n) or not_started_view(spec, n) for n in spec.all_steps]

    invoice = state.open_invoice
    return {
        "memory_missing": False,
        "job_id": state.job_id,
        "buyer": state.buyer,
        "contract_hash": state.contract_hash,
        "job_type": spec.id,
        "job_type_name": spec.name,
        "job_type_description": spec.description,
        "last_step": spec.last_step,
        "status": state.status,
        "current_step": state.current_step,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "archived": entity_source.startswith("archived"),
        "archived_at": archived_at,
        "open_invoice": (
            {
                "step": invoice.step,
                "amount_usdc": invoice.amount_usdc,
                "memo": invoice.memo,
                "invoice_block": invoice.invoice_block,
                "paid": invoice.paid,
                "tx_hash": invoice.tx_hash,
            }
            if invoice is not None
            else None
        ),
        "steps": steps,
        "source": entity_source,
    }


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """One job by id, to anyone who has the id.

    Deliberately not behind the same gate as the job list. A link to a job is a
    receipt: the buyer may want to show someone what they paid for and what the
    agent committed on chain, and that has to work without handing over a
    session. So this stays the public meter shape for a stranger, with the
    outputs and the contract hash removed, while the list of who has bought
    what remains an operator view. Guessing an id is the only way in, and an id
    reveals nothing the chain does not already carry.
    """
    store = open_store()
    if store is None:
        return missing({"job": None})
    ident = caller(authorization)
    detail = redact_job_detail(job_detail(store, job_id), ident)
    detail["viewer"] = ident.kind
    return detail


# ----------------------------------------------------------------------
# POST /api/jobs: the one write
# ----------------------------------------------------------------------
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOURCE_MAX_BYTES = 65536

# In-process rate limit: at most 10 job creations per minute across all
# callers. This is a demo guard against a runaway client, not an access
# control; a real deployment would put a proxy in front of this.
RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 60.0
_recent_posts: deque[float] = deque()
_rate_lock = threading.Lock()


def rate_limited() -> bool:
    now = time.monotonic()
    with _rate_lock:
        while _recent_posts and now - _recent_posts[0] > RATE_WINDOW_SECONDS:
            _recent_posts.popleft()
        if len(_recent_posts) >= RATE_LIMIT:
            return True
        _recent_posts.append(now)
        return False


class NewJobRequest(BaseModel):
    buyer: str = Field(description="Buyer wallet, 0x + 40 hex.")
    source: str = Field(description="Solidity source text, 1 to 65536 bytes.")
    filename: str | None = Field(default="Vault.sol", description="Display name only.")
    job_type: str | None = Field(
        default=None, description="Service id from GET /api/job_types; default audit."
    )


# ----------------------------------------------------------------------
# Report: the audit as a document the buyer can keep
# ----------------------------------------------------------------------
def payment_label(step: dict[str, Any]) -> str:
    if step.get("status") != "done":
        return "not started"
    if float(step.get("price_usdc") or 0) == 0:
        return "free"
    return "paid" if step.get("paid") else "credit (delivered before the invoice cleared)"


def report_data(store: TurnstylStore, job_id: str) -> dict[str, Any]:
    """Everything the report needs, from the same view the job page renders
    (live entity first, then the read-only archive)."""
    detail = job_detail(store, job_id)
    receipts = os.environ.get("RECEIPTS_ADDRESS")
    steps = []
    for st in detail["steps"]:
        steps.append(
            {
                "step": st["step"],
                "name": st["name"],
                "status": st["status"],
                "price_usdc": st["price_usdc"],
                "payment": payment_label(st),
                "cached": st.get("cached", False),
                "pay_tx": st.get("pay_tx"),
                "pay_tx_url": f"{EXPLORER}/tx/{st['pay_tx']}" if st.get("pay_tx") and not str(st["pay_tx"]).startswith("0xfake") else None,
                "pay_method": st.get("pay_method"),
                "commit_tx": st.get("commit_tx"),
                "commit_tx_url": f"{EXPLORER}/tx/{st['commit_tx']}" if st.get("commit_tx") else None,
                "output_sha256": st.get("output_sha256"),
                "output": st.get("output"),
                "compiles": st.get("compiles"),
                "tests_total": st.get("tests_total"),
                "tests_passed": st.get("tests_passed"),
                "tests_failed": st.get("tests_failed"),
            }
        )
    return {
        "job_id": detail["job_id"],
        "contract_hash": detail["contract_hash"],
        "buyer": detail["buyer"],
        "job_type": detail["job_type"],
        "job_type_name": detail["job_type_name"],
        "status": detail["status"],
        "created_at": detail["created_at"],
        "updated_at": detail["updated_at"],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "archived": detail["archived"],
        "chain_id": CHAIN_ID,
        "explorer": EXPLORER,
        "receipts_address": receipts,
        "receipts_url": f"{EXPLORER}/address/{receipts}" if receipts else None,
        "steps": steps,
        "verification": [
            {"step": s_["step"], "name": s_["name"], "output_sha256": s_["output_sha256"],
             "commit_tx": s_["commit_tx"], "commit_tx_url": s_["commit_tx_url"]}
            for s_ in steps if s_["status"] == "done"
        ],
    }


def report_markdown(r: dict[str, Any]) -> str:
    """The report as Markdown. Outputs go in tilde fences: model output for the
    patch step contains backtick fences of its own."""
    lines = [
        f"# turnstyl {r['job_type_name'].lower()} report: job {r['job_id']}",
        "",
        f"- service: {r['job_type_name']} (`{r['job_type']}`)",
        f"- contract sha256: `{r['contract_hash']}`",
        f"- buyer: `{r['buyer']}`",
        f"- job status: {r['status']}" + (" (archived)" if r["archived"] else ""),
        f"- opened: {r['created_at']}",
        f"- generated: {r['generated_at']}",
        f"- chain: Base Sepolia ({r['chain_id']}), receipts contract "
        + (f"[{r['receipts_address']}]({r['receipts_url']})" if r["receipts_address"] else "not configured"),
        "",
    ]
    for st in r["steps"]:
        lines += [f"## Step {st['step']}: {st['name']}", ""]
        lines.append(f"- price: {float(st['price_usdc'] or 0):.2f} USDC, {st['payment']}")
        if st["cached"]:
            lines.append("- served from memory (a prior audit of this contract)")
        if st["pay_tx"]:
            rail = {"x402": " over x402, gasless", "receipts": " through the receipts contract"}.get(
                st.get("pay_method") or "", ""
            )
            lines.append(
                f"- payment{rail}: [{st['pay_tx']}]({st['pay_tx_url']})"
                if st["pay_tx_url"]
                else f"- payment: `{st['pay_tx']}` (fake backend)"
            )
        if st["commit_tx"]:
            lines.append(f"- commit: [{st['commit_tx']}]({st['commit_tx_url']})")
        if st.get("tests_total") is not None:
            lines.append(
                f"- forge test: {st['tests_passed']} passed, "
                f"{st['tests_failed']} failed, {st['tests_total']} total"
            )
        elif st.get("compiles") is not None:
            lines.append(f"- compiles: {'yes' if st['compiles'] else 'no'}")
        if st["output_sha256"]:
            lines.append(f"- output sha256: `{st['output_sha256']}`")
        lines.append("")
        if st["status"] == "done":
            lines += ["~~~~text", st["output"] or "", "~~~~", ""]
        else:
            lines += ["_not started_", ""]
    lines += [
        "## Verification",
        "",
        "Each step's output is hashed with sha256 and, when it was paid for, the hash was "
        "committed on Base Sepolia as `Committed(memo, outputHash)` on the receipts contract "
        "at payment time. Open the commit transaction on BaseScan and compare its `outputHash` "
        "with the sha256 below; recompute the sha256 from the output above to check the "
        "report itself.",
        "",
        "| step | sha256 of output | Committed event tx |",
        "| --- | --- | --- |",
    ]
    for v in r["verification"]:
        tx = f"[{v['commit_tx']}]({v['commit_tx_url']})" if v["commit_tx"] else "no commit (free step or fake backend)"
        lines.append(f"| {v['step']} {v['name']} | `{v['output_sha256']}` | {tx} |")
    lines.append("")
    return "\n".join(lines)


def report_guard(store: TurnstylStore, job_id: str, ident: auth.Identity) -> None:
    """A report is the whole of the work. Only the buyer and the operator."""
    state = store.get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r} in {db_path()}")
    require_visible(ident, state.buyer, f"the report for job {job_id}")


@app.get("/api/jobs/{job_id}/report.json")
def api_report_json(
    job_id: str,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None, description="Session token, for a plain link."),
) -> dict[str, Any]:
    store = open_store()
    if store is None:
        return missing({"job_id": job_id, "report": None})
    report_guard(store, job_id, caller(authorization or (f"Bearer {token}" if token else None)))
    return {"memory_missing": False, **report_data(store, job_id)}


@app.get("/api/jobs/{job_id}/report.md", include_in_schema=True)
def api_report_md(
    job_id: str,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None, description="Session token, for a plain link."),
):
    store = open_store()
    if store is None:
        return PlainTextResponse(
            "memory missing: no report can be produced from an absent store\n",
            media_type="text/markdown",
        )
    # A download is a navigation, not a fetch, so it carries no Authorization
    # header. The page appends ?token= to the link instead; same session, same
    # check, and nothing else accepts a token in the query string.
    report_guard(store, job_id, caller(authorization or (f"Bearer {token}" if token else None)))
    md = report_markdown(report_data(store, job_id))
    return PlainTextResponse(
        md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="turnstyl-audit-{job_id}.md"'},
    )


# ----------------------------------------------------------------------
# Verify: does the output the buyer holds match the hash committed at payment?
# ----------------------------------------------------------------------
VERIFY_TTL_SECONDS = 60.0
_verify_cache: dict[tuple[str, int, str], tuple[float, dict[str, Any]]] = {}
_verify_lock = threading.Lock()
COMMITTED_SIGNATURE = "Committed(bytes32,bytes32)"


def _rpc_retry(fn, *args):
    """Three attempts with backoff; the caller turns a final failure into a
    per-step 'rpc unavailable', never a 500."""
    last = None
    for attempt in range(3):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001 - retried, then reported per step
            last = e
            if attempt < 2:
                time.sleep(1.0 * (2**attempt))
    raise RuntimeError(f"{type(last).__name__}: {last}")


def verify_step(
    spec: jobtypes.JobType, job_id: str, step: int, record: S.StepRecord
) -> dict[str, Any]:
    recomputed = S.sha256_text(record.output)
    memo = hex0x(memo_bytes32(job_id, step))
    result: dict[str, Any] = {
        "step": step,
        "name": spec.step_name(step),
        "memo": memo,
        "cached": record.cached,
        "output_sha256_stored": record.output_sha256,
        "output_sha256_recomputed": recomputed,
        "onchain_hash": None,
        "onchain_memo": None,
        "tx": record.commit_tx,
        "tx_url": f"{EXPLORER}/tx/{record.commit_tx}" if record.commit_tx else None,
        "block": None,
        "matches": None,
        "reason": None,
    }
    if not record.commit_tx:
        result["reason"] = "no commit for this step (free step, or the fake payment backend)"
        return result

    key = (job_id, step, record.commit_tx)
    now = time.monotonic()
    with _verify_lock:
        hit = _verify_cache.get(key)
        if hit and now - hit[0] < VERIFY_TTL_SECONDS:
            cached = dict(hit[1])
            cached["from_cache"] = True
            return cached

    rpc = os.environ.get("BASE_SEPOLIA_RPC")
    receipts = os.environ.get("RECEIPTS_ADDRESS")
    if not rpc or not receipts:
        result["reason"] = "rpc not configured (BASE_SEPOLIA_RPC / RECEIPTS_ADDRESS)"
        return result
    try:
        from web3 import HTTPProvider, Web3

        w3 = Web3(HTTPProvider(rpc, request_kwargs={"timeout": 20}))
        receipt = _rpc_retry(w3.eth.get_transaction_receipt, record.commit_tx)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "not found" in msg.lower():
            result["reason"] = "transaction not found on Base Sepolia"
        else:
            result["reason"] = f"rpc unavailable: {msg[:160]}"
        return result

    topic = hex0x(Web3.keccak(text=COMMITTED_SIGNATURE))
    log = None
    for lg in receipt["logs"]:
        if str(lg["address"]).lower() == receipts.lower() and lg["topics"] and hex0x(lg["topics"][0]) == topic:
            log = lg
            break
    result["block"] = int(receipt["blockNumber"])
    if log is None:
        result["matches"] = False
        result["reason"] = "no Committed event from the receipts contract in this transaction"
    else:
        result["onchain_memo"] = hex0x(log["topics"][1]) if len(log["topics"]) > 1 else None
        data = bytes(log["data"])
        result["onchain_hash"] = hex0x(data[-32:]) if len(data) >= 32 else None
        hash_ok = result["onchain_hash"] == "0x" + recomputed
        memo_ok = result["onchain_memo"] == memo
        result["matches"] = bool(hash_ok and memo_ok)
        if result["matches"]:
            result["reason"] = "matches on-chain commit"
        elif not memo_ok:
            result["reason"] = "the committed memo is not this job and step"
        elif recomputed != record.output_sha256:
            result["reason"] = "the stored output no longer hashes to its recorded sha256; the on-chain hash matches the original"
        else:
            result["reason"] = "the on-chain hash differs from the output the buyer holds"
    with _verify_lock:
        _verify_cache[key] = (now, dict(result))
    return result


@app.get("/api/jobs/{job_id}/verify")
def api_verify(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Prove, per step, that the output in memory is the one committed on chain.

    Needs both sides: the chain holds the hash, memory holds the output. Either
    alone proves nothing. Steps served from memory carry their own commit and
    are checked like any other; a step without a commit says so.
    """
    store = open_store()
    if store is None:
        return missing({"job_id": job_id, "steps": []})
    state = store.get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r} in {db_path()}")
    # Verify recomputes the sha256 of each output, so it reads the outputs.
    require_visible(caller(authorization), state.buyer, f"verification of job {job_id}")
    entity = store.get_job_entity(job_id)
    source = "entity job/<id>"
    if entity is None:
        archive = read_archived_job(db_path(), job_id)
        if archive is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} has no step records to verify")
        entity = S.JobEntity.model_validate(archive["body"])
        source = "archived_entities (read-only)"
    spec = jobtypes.get(state.job_type)
    steps = [
        verify_step(spec, job_id, int(k), rec)
        for k, rec in sorted(entity.steps.items(), key=lambda kv: int(kv[0]))
    ]
    return {
        "memory_missing": False,
        "job_id": job_id,
        "job_type": state.job_type,
        "contract_hash": state.contract_hash,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cache_ttl_seconds": int(VERIFY_TTL_SECONDS),
        "steps": steps,
        "summary": {
            "checked": len(steps),
            "matches": sum(1 for x in steps if x["matches"] is True),
            "mismatches": sum(1 for x in steps if x["matches"] is False),
            "no_commit": sum(1 for x in steps if x["matches"] is None),
        },
        "source": source + "; Committed events decoded from each commit transaction's receipt",
    }


@app.post("/api/jobs")
def api_create_job(
    body: NewJobRequest, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Give the agent a contract. Same path as `turnstyl job new`.

    Validates the buyer address and the source, then hands the text to
    ``Engine.new_job_from_source``, which stores the source under the same
    contract reference the CLI uses, runs the free scope step and issues the
    invoice for step 2. Returns the job as GET /api/jobs/{id} would, plus
    ``resumed`` when an open job for this buyer and contract already existed.
    """
    if not db_path().is_file():
        raise HTTPException(
            status_code=409, detail="memory file missing; the agent cannot take jobs"
        )
    buyer = (body.buyer or "").strip()
    if not ADDRESS_RE.match(buyer):
        raise HTTPException(
            status_code=400,
            detail="buyer must be a 0x-prefixed 40-hex-character address",
        )
    buyer = buyer.lower()
    # A job is created in a wallet's name, and its contents belong to that
    # wallet from this moment on. Only that wallet may open one.
    ident = caller(authorization)
    if not ident.signed_in:
        raise HTTPException(
            status_code=401,
            detail=(
                f"creating a job as {buyer} needs a session for that wallet. "
                f"{SIGN_IN_HINT}"
            ),
        )
    if not ident.is_operator and ident.address != buyer:
        raise HTTPException(
            status_code=403,
            detail=(
                f"you are signed in as {ident.address}; a job cannot be created "
                f"in the name of {buyer}"
            ),
        )
    source = body.source or ""
    size = len(source.encode("utf-8"))
    if size < 1 or size > SOURCE_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"source must be 1 to {SOURCE_MAX_BYTES} bytes of Solidity; got {size}",
        )
    if "contract" not in source and "pragma" not in source:
        raise HTTPException(
            status_code=400,
            detail="source does not look like Solidity: no 'contract' or 'pragma' in it",
        )
    if rate_limited():
        raise HTTPException(
            status_code=429,
            detail=f"at most {RATE_LIMIT} job creations per minute; try again shortly",
        )
    if not daily_take():
        raise HTTPException(
            status_code=429,
            detail=(
                f"the agent has taken its {MAX_JOBS_PER_DAY} jobs for today "
                f"(UTC); the cap resets at 00:00 UTC"
            ),
        )
    requested_type = (body.job_type or "").strip() or None
    if requested_type is not None and not jobtypes.is_known(requested_type):
        known = ", ".join(t.id for t in jobtypes.all_types())
        raise HTTPException(
            status_code=400,
            detail=f"unknown job_type {requested_type!r}; known types are {known}",
        )
    filename = (body.filename or "contract.sol").strip() or "contract.sol"
    filename = os.path.basename(filename)[:80]

    store = TurnstylStore(TurnstylMemory(db_path()))
    engine = Engine(store=store, payments=get_backend(store.memory))
    try:
        outcome = engine.new_job_from_source(
            source, buyer, filename=filename, job_type=requested_type
        )
    except RuntimeError as e:
        daily_give_back()
        raise HTTPException(status_code=400, detail=str(e)) from e
    if outcome.resumed:
        daily_give_back()   # a resume created nothing; only creations count

    detail = job_detail(store, outcome.job_id)
    detail["resumed"] = bool(outcome.resumed)
    detail["decision"] = outcome.decision
    return detail


@app.post("/api/jobs/{job_id}/pay")
def api_simulate_pay(
    job_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Mark the job's open invoice paid. Fake backend only.

    The same thing `turnstyl pay` does, so the browser flow can be exercised
    end to end without a wallet. On the Base backend a payment is a Paid log on
    the receipts contract and nothing else counts, so this answers 404 there.
    """
    backend = (os.environ.get("PAYMENTS") or "fake").strip().lower()
    if backend != "fake":
        raise HTTPException(
            status_code=404, detail="payments are on chain; use the Pay button"
        )
    if not db_path().is_file():
        raise HTTPException(
            status_code=409, detail="memory file missing; the agent cannot take payments"
        )
    store = TurnstylStore(TurnstylMemory(db_path()))
    state = store.get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r} in {db_path()}")
    require_visible(caller(authorization), state.buyer, f"the invoice on job {job_id}")
    invoice = state.open_invoice
    if invoice is None or invoice.paid:
        raise HTTPException(
            status_code=400,
            detail="this job has no open invoice to pay"
            + (" (the current one is already settled)" if invoice else ""),
        )
    engine = Engine(store=store, payments=get_backend(store.memory))
    tx_hash = engine.pay(job_id, invoice.step)
    detail = job_detail(store, job_id)
    detail["paid_step"] = invoice.step
    detail["tx_hash"] = tx_hash
    detail["simulated"] = True
    return detail


# ----------------------------------------------------------------------
# /api/buyers/{address}
# ----------------------------------------------------------------------
@app.get("/api/buyers/{address}")
def api_buyer(
    address: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    store = open_store()
    if store is None:
        return missing({"buyer": None})

    ident = caller(authorization)
    key = store.buyer_key(address)
    ledger = store.get_buyer(key)
    known = store.buyer_exists(key)

    # The explanation is policy.py's own words, not a paraphrase: ask it what it
    # would decide if this buyer requested the next paid step right now.
    probe = S.JobState(
        job_id="probe",
        buyer=key,
        contract_hash="0" * 64,
        current_step=2,
        status=S.STATUS_AWAITING_PAYMENT,
    )
    # The trust question is about the buyer, not the service: any type's first
    # paid step gives the same answer, so the default type asks it.
    default_spec = jobtypes.get(None)
    decision, reason = policy.decide(2, ledger, probe, default_spec)

    return redact_buyer({
        "memory_missing": False,
        "buyer": key,
        "known": known,
        "ledger": ledger.model_dump(),
        "trust": {
            "trust_tier": ledger.trust_tier,
            "would_decide": decision,
            "explanation": reason,
            "jobs_until_credit": policy.jobs_until_credit(ledger),
            # same value under the old name, for any consumer still reading it
            "steps_until_credit": policy.jobs_until_credit(ledger),
            "completed_paid_jobs": ledger.completed_paid_jobs,
            "earned_back": policy.earned_back(ledger),
        },
        "outstanding": outstanding_view(ledger),
        "jobs": ledger.jobs,
        "source": (
            "entity buyer/<address>; trust.explanation is the reason string "
            "policy.decide produces for the next paid step; outstanding[].memo "
            "is keccak256(\"<job_id>:<step>\") computed here, the same bytes "
            "payments.memo_bytes32 puts on chain"
        ),
    }, ident)


def outstanding_view(ledger: S.BuyerLedger) -> list[dict[str, Any]]:
    """The buyer's outstanding items with the memo the chain expects.

    Computed server-side from the same function the payment backend uses,
    so the page never re-implements keccak and can never pay a wrong memo.
    """
    items = []
    for item in ledger.outstanding:
        view = item.model_dump()
        view["memo"] = hex0x(memo_bytes32(item.job_id, item.step))
        view["memo_source"] = "keccak256(\"<job_id>:<step>\")"
        view["amount_units"] = S.usdc_base_units(item.amount_usdc)
        items.append(view)
    return items


@app.post("/api/buyers/{address}/settle/{job_id}/{step}")
def api_settle_outstanding(
    address: str, job_id: str, step: int, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Settle one outstanding item on a closed job. Fake backend only.

    The same thing `turnstyl pay` followed by a reconcile does. On the Base
    backend the only evidence of payment is a Paid log, so this answers 404
    there and the page pays through the wallet instead.
    """
    backend = (os.environ.get("PAYMENTS") or "fake").strip().lower()
    if backend != "fake":
        raise HTTPException(
            status_code=404, detail="payments are on chain; use the Pay button"
        )
    if not db_path().is_file():
        raise HTTPException(
            status_code=409, detail="memory file missing; the agent cannot take payments"
        )
    store = TurnstylStore(TurnstylMemory(db_path()))
    key = store.buyer_key(address)
    require_visible(caller(authorization), key, f"the ledger of {key}")
    if not store.buyer_exists(key):
        raise HTTPException(status_code=404, detail=f"buyer {key} is unknown to memory")
    ledger = store.get_buyer(key)
    if not any(o.job_id == job_id and o.step == step for o in ledger.outstanding):
        raise HTTPException(
            status_code=404,
            detail=f"buyer {key} has no outstanding step {step} on job {job_id}",
        )
    payments = get_backend(store.memory)
    tx_hash = payments.mark_paid(job_id, step)
    # The worker sweeps outstanding buyers every pass and may reconcile this
    # item between the mark and the call below; settlement is judged by
    # re-reading the ledger, not by who got there first.
    cleared = payments.reconcile(key)
    ledger = store.get_buyer(key)
    still_open = any(o.job_id == job_id and o.step == step for o in ledger.outstanding)
    if still_open:
        raise HTTPException(
            status_code=500,
            detail=f"marked step {step} of job {job_id} paid but it is still outstanding",
        )
    return {
        "memory_missing": False,
        "buyer": key,
        "settled": {"job_id": job_id, "step": step, "tx_hash": tx_hash, "simulated": True},
        "reconciled": cleared,
        "reconciled_by": "this request" if cleared else "the worker, between the mark and the check",
        "ledger": ledger.model_dump(),
        "outstanding": outstanding_view(ledger),
    }


# ----------------------------------------------------------------------
# /api/journal
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# x402: a second way to pay an invoice, with no gas and no approval
# ----------------------------------------------------------------------
# The buyer signs an EIP-3009 transferWithAuthorization over EIP-712 typed
# data; a facilitator submits it and pays the gas. See docs/X402.md for the
# wire formats, read from the package and observed live.
#
# The package's resource server speaks x402 v2 only, so the network is the
# CAIP-2 name eip155:84532 rather than the v1 "base-sepolia", and the headers
# are PAYMENT-REQUIRED / PAYMENT-SIGNATURE / PAYMENT-RESPONSE. The middleware
# still accepts X-PAYMENT as an inbound alias.
X402_NETWORK = "eip155:84532"
X402_FACILITATOR = (os.environ.get("X402_FACILITATOR") or "https://x402.org/facilitator").rstrip("/")
X402_TIMEOUT_SECONDS = 600
X402_PAY_PATH = re.compile(r"^/api/jobs/([^/]+)/pay-x402/(\d+)$")
X402_SETTLE_PATH = re.compile(r"^/api/buyers/([^/]+)/settle-x402/([^/]+)/(\d+)$")

x402_status: dict[str, Any] = {"enabled": False, "reason": "not checked"}


def _x402_probe() -> tuple[bool, str]:
    """Decide once, at startup, whether x402 is on. One line either way.

    Off when explicitly disabled, when the payment backend is fake (x402 moves
    real USDC, so there is nothing for it to do there), when the package is
    absent, or when the facilitator does not answer with our network.
    """
    flag = (os.environ.get("X402_ENABLED") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False, "disabled by X402_ENABLED"
    backend = (os.environ.get("PAYMENTS") or "fake").strip().lower()
    if backend != "base":
        return False, f"payments backend is {backend!r}; x402 settles real USDC"
    if not os.environ.get("AGENT_ADDRESS") or not os.environ.get("USDC_ADDRESS"):
        return False, "AGENT_ADDRESS or USDC_ADDRESS is not set"
    try:
        import x402  # noqa: F401
        from x402.http import HTTPFacilitatorClient  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return False, f"the x402 package is not importable: {type(e).__name__}: {e}"
    try:
        import httpx

        r = httpx.get(f"{X402_FACILITATOR}/supported", timeout=8.0)
        r.raise_for_status()
        kinds = r.json().get("kinds") or []
    except Exception as e:  # noqa: BLE001
        return False, f"facilitator {X402_FACILITATOR} did not answer: {type(e).__name__}: {e}"
    for k in kinds:
        if k.get("scheme") == "exact" and k.get("network") == X402_NETWORK:
            return True, f"facilitator {X402_FACILITATOR} supports exact on {X402_NETWORK}"
    return False, f"facilitator {X402_FACILITATOR} does not list exact on {X402_NETWORK}"


def _x402_invoice_amount(job_id: str, step: int) -> float | None:
    """What this invoice costs, from memory. None when there is nothing owed."""
    if not db_path().is_file():
        return None
    try:
        store = TurnstylStore(TurnstylMemory(db_path()))
        state = store.get_job_state(job_id)
        if state is None:
            return None
        inv = state.open_invoice
        if inv is not None and inv.step == step and not inv.paid:
            return inv.amount_usdc
        for item in store.get_buyer(state.buyer).outstanding:
            if item.job_id == job_id and item.step == step:
                return item.amount_usdc
    except Exception:  # noqa: BLE001 - pricing must not raise inside the middleware
        return None
    return None


def _x402_price(context) -> Any:
    """Dynamic price: this invoice's amount, in USDC base units.

    An AssetAmount rather than a dollar string, so the charge is exactly the
    invoice and never a rounded conversion.
    """
    from x402.schemas import AssetAmount

    path = context.path or ""
    amount = None
    m = X402_PAY_PATH.match(path)
    if m:
        amount = _x402_invoice_amount(m.group(1), int(m.group(2)))
    else:
        m = X402_SETTLE_PATH.match(path)
        if m:
            amount = _x402_invoice_amount(m.group(2), int(m.group(3)))
    if amount is None:
        # Nothing owed for this path. Quote a nominal amount so the 402 is
        # well-formed; the handler refuses before anything is settled.
        amount = 0.01
    return AssetAmount(
        amount=str(S.usdc_base_units(amount)),
        asset=os.environ["USDC_ADDRESS"],
        extra={"name": "USDC", "version": "2"},
    )


def _x402_payer(request: Request) -> str | None:
    """The address that signed the authorisation on this request."""
    payload = getattr(request.state, "payment_payload", None)
    if payload is None:
        return None
    inner = getattr(payload, "payload", None)
    if isinstance(inner, dict):
        auth = inner.get("authorization") or {}
        payer = auth.get("from")
        if isinstance(payer, str):
            return payer
    return None


def _x402_check(job_id: str, step: int, request: Request, address: str | None = None):
    """Validate before the middleware settles. Returns (store, state, amount).

    Raises HTTPException, which the middleware treats as a failed handler and
    does not settle. That ordering is the whole safety property here: a payment
    from the wrong wallet, or for an invoice that does not exist, never moves.
    """
    if not x402_status["enabled"]:
        raise HTTPException(
            status_code=404,
            detail=f"x402 is not available: {x402_status['reason']}",
        )
    if not db_path().is_file():
        raise HTTPException(
            status_code=409, detail="memory file missing; the agent cannot take payments"
        )
    store = TurnstylStore(TurnstylMemory(db_path()))
    state = store.get_job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r} in {db_path()}")
    # A session for this job's buyer, on top of the payer check below. The 402
    # quote is public; moving money on this job's behalf is not.
    require_visible(
        caller(request.headers.get("authorization")),
        state.buyer,
        f"paying for job {job_id}",
    )
    if address is not None and store.buyer_key(address) != state.buyer:
        raise HTTPException(
            status_code=400,
            detail=f"job {job_id} belongs to {state.buyer}, not {store.buyer_key(address)}",
        )
    amount = _x402_invoice_amount(job_id, step)
    if amount is None:
        raise HTTPException(
            status_code=404,
            detail=f"job {job_id} has nothing owed for step {step}",
        )
    payer = _x402_payer(request)
    if payer is None:
        raise HTTPException(status_code=400, detail="no x402 payment payload on the request")
    if payer.lower() != state.buyer.lower():
        raise HTTPException(
            status_code=400,
            detail=(
                f"the x402 payer {payer.lower()} is not this job's buyer "
                f"{state.buyer}; nothing was settled"
            ),
        )
    return store, state, amount


def x402_record_settlement(job_id: str, step: int, tx_hash: str, payer: str) -> dict[str, Any]:
    """Write a settled x402 payment into memory, exactly as a Paid log would.

    Called once the facilitator has actually settled, so the recorded hash is
    a real Base Sepolia transaction. The rest of turnstyl cannot tell the
    difference: the worker runs the step, commit() still publishes the output
    hash, and verify still checks it.
    """
    store = TurnstylStore(TurnstylMemory(db_path()))
    state = store.get_job_state(job_id)
    if state is None:
        return {"recorded": False, "reason": f"job {job_id} vanished before settlement"}
    payments = get_backend(store.memory)

    # Mark the invoice settled BEFORE publishing the evidence, and re-read the
    # state immediately before writing it. The worker wakes on the evidence, and
    # a job state read before that and written after would put the job back on
    # the step the worker has just finished, losing the invoice it issued next.
    fresh = store.get_job_state(job_id)
    if (
        fresh is not None
        and fresh.open_invoice is not None
        and fresh.open_invoice.step == step
        and not fresh.open_invoice.paid
    ):
        fresh.open_invoice.paid = True
        fresh.open_invoice.tx_hash = tx_hash
        store.put_job_state(fresh)

    # Not mark_paid: on the Base backend that deliberately refuses, because a
    # receipts-contract payment must land as a Paid log. An x402 settlement is
    # real USDC too, on its own rail, so it is recorded as its own evidence.
    payments.record_x402(job_id, step, tx_hash, payer or "")

    # A step that has already run (the settle path on a closed job) gets its
    # rail stamped here; one that has not is stamped by the engine when it runs.
    entity = store.get_job_entity(job_id)
    if entity is not None and str(step) in entity.steps:
        record = entity.steps[str(step)]
        record.paid = True
        record.tx_hash = tx_hash
        record.pay_method = PAY_METHOD_X402
        store.put_job_entity(job_id, entity)
    cleared = payments.reconcile(state.buyer)
    store.journal(
        S.JournalEntry(
            evaluated=[
                f"{S.job_state_key(job_id)} -> step {step} owed "
                f"{_x402_invoice_amount(job_id, step) or 0:.2f} USDC",
                f"x402 {X402_NETWORK} -> settled by the facilitator in {tx_hash}",
            ],
            acted=[
                f"recorded an x402 settlement for step {step} of job {job_id} "
                f"from payer {payer.lower()} (tx {tx_hash})"
            ],
            forward=["the worker runs this step on its next pass"],
            extra={
                "job_id": job_id,
                "buyer": state.buyer,
                "step": step,
                "decision": "PAID_X402",
                "price": _x402_invoice_amount(job_id, step),
                "tx_hash": tx_hash,
                "pay_method": PAY_METHOD_X402,
                "summary": (
                    f"Step {step} was paid over x402: the buyer signed a USDC "
                    f"transfer and a facilitator submitted it, so the buyer spent "
                    f"no gas."
                ),
            },
        )
    )
    return {"recorded": True, "tx_hash": tx_hash, "payer": payer.lower(), "reconciled": cleared}


@app.post("/api/jobs/{job_id}/pay-x402/{step}")
def api_pay_x402(job_id: str, step: int, request: Request) -> dict[str, Any]:
    """Pay one open invoice over x402. Gasless for the buyer.

    Unpaid, this answers 402 with the payment requirements. With a valid
    PAYMENT-SIGNATURE (or X-PAYMENT) header the middleware verifies, this
    handler checks the payer against the job's buyer, and the middleware
    settles on the way out. The settlement hash is written to memory by
    ``x402_settlement_recorder`` once it exists.
    """
    store, state, amount = _x402_check(job_id, step, request)
    return {
        "x402": "verified",
        "job_id": job_id,
        "step": step,
        "amount_usdc": amount,
        "buyer": state.buyer,
        "note": "settlement is recorded once the facilitator confirms it",
    }


@app.post("/api/buyers/{address}/settle-x402/{job_id}/{step}")
def api_settle_x402(address: str, job_id: str, step: int, request: Request) -> dict[str, Any]:
    """Settle an outstanding item on a closed job over x402. Gasless."""
    store, state, amount = _x402_check(job_id, step, request, address=address)
    return {
        "x402": "verified",
        "job_id": job_id,
        "step": step,
        "amount_usdc": amount,
        "buyer": state.buyer,
        "note": "settlement is recorded once the facilitator confirms it",
    }




# ----------------------------------------------------------------------
# x402 middleware: the paywall, and the recorder that follows it
# ----------------------------------------------------------------------
def _x402_install() -> None:
    """Register the paywall on the two x402 endpoints, if x402 is on."""
    from x402 import x402ResourceServer
    from x402.http import FacilitatorConfig, HTTPFacilitatorClient
    from x402.http.middleware.fastapi import payment_middleware
    from x402.http.types import PaymentOption, RouteConfig
    from x402.mechanisms.evm.exact.register import register_exact_evm_server

    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=X402_FACILITATOR))
    server = x402ResourceServer(facilitator)
    register_exact_evm_server(server)

    def option(description: str) -> PaymentOption:
        return PaymentOption(
            scheme="exact",
            pay_to=os.environ["AGENT_ADDRESS"],
            price=_x402_price,
            network=X402_NETWORK,
            max_timeout_seconds=X402_TIMEOUT_SECONDS,
        )

    # [param] compiles to [^/]+, so these match one path segment each and
    # nothing else on the API is behind the paywall.
    routes = {
        "POST /api/jobs/[job_id]/pay-x402/[step]": RouteConfig(
            accepts=option("one metered step"),
            description="turnstyl: one metered step of an audit or test suite",
        ),
        "POST /api/buyers/[address]/settle-x402/[job_id]/[step]": RouteConfig(
            accepts=option("an outstanding invoice"),
            description="turnstyl: an outstanding invoice on a closed job",
        ),
    }
    paywall = payment_middleware(routes, server)

    @app.middleware("http")
    async def x402_paywall(request: Request, call_next):
        return await paywall(request, call_next)

    # Added last, so it is the OUTERMOST layer and sees the response the
    # paywall produced, including the PAYMENT-RESPONSE header it attaches
    # after settling. The exact scheme settles after the handler, so this is
    # the first point at which the settlement hash exists.
    @app.middleware("http")
    async def x402_settlement_recorder(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        match = X402_PAY_PATH.match(path) or X402_SETTLE_PATH.match(path)
        if match is None:
            return response

        if response.status_code == 402:
            # v2 sends an empty body and puts the requirements in a header. Mirror
            # them into the body too: a browser can only read the header when CORS
            # exposes it, and a reader with the body needs no such permission.
            req_raw = response.headers.get("payment-required")
            if not req_raw:
                return response
            try:
                requirements = json.loads(base64.b64decode(req_raw))
            except Exception:  # noqa: BLE001
                return response
            headers = {
                k: v for k, v in response.headers.items() if k.lower() != "content-length"
            }
            return JSONResponse(content=requirements, status_code=402, headers=headers)

        if response.status_code != 200:
            return response

        raw = response.headers.get("payment-response") or response.headers.get(
            "x-payment-response"
        )
        if not raw:
            return response
        try:
            settled = json.loads(base64.b64decode(raw))
        except Exception:  # noqa: BLE001 - a malformed receipt is not our crash
            return response
        if not settled.get("success") or not settled.get("transaction"):
            return response

        groups = match.groups()
        job_id, step = (groups[0], int(groups[1])) if len(groups) == 2 else (groups[1], int(groups[2]))
        try:
            result = x402_record_settlement(
                job_id, step, str(settled["transaction"]), str(settled.get("payer") or "")
            )
        except Exception as e:  # noqa: BLE001 - the money moved; say so, do not 500
            logger.exception("x402: settled but could not record the payment")
            result = {"recorded": False, "reason": f"{type(e).__name__}: {e}"}

        body = {
            "x402": "settled",
            "settlement": settled,
            "recorded": result,
        }
        try:
            store = TurnstylStore(TurnstylMemory(db_path()))
            body["job"] = job_detail(store, job_id)
        except Exception:  # noqa: BLE001 - the receipt matters more than the view
            body["job"] = None
        headers = {
            k: v for k, v in response.headers.items() if k.lower() != "content-length"
        }
        return JSONResponse(content=body, status_code=200, headers=headers)


@app.get("/api/journal")
def api_journal(
    job: str | None = Query(default=None, description="Filter to one job id."),
    limit: int = Query(default=50, ge=1, le=500),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    store = open_store()
    if store is None:
        return missing({"events": [], "job": job, "limit": limit})
    ident = caller(authorization)

    # Filtering happens after the read, so ask for a wider window than the caller
    # wants when they are narrowing to one job.
    window = min(500, limit * 10) if job else limit
    events = []
    for event in store.read_journal(limit=window):
        extra = event.get("extra") or {}
        if job and extra.get("job_id") != job:
            continue
        # Each event is trimmed to what its own job's buyer allows: the whole
        # entry for them and the operator, one sentence for everyone else.
        events.append(
            redact_journal_event(
                {
                    "ts": event.get("ts"),
                    "decision": extra.get("decision"),
                    "step": extra.get("step"),
                    "buyer": extra.get("buyer"),
                    "evaluated": event.get("evaluated") or [],
                    "acted": event.get("acted") or [],
                    "forward": event.get("forward") or [],
                    "extra": extra,
                },
                ident,
            )
        )
        if len(events) >= limit:
            break

    return {
        "memory_missing": False,
        "job": job,
        "limit": limit,
        "viewer": ident.kind,
        "count": len(events),
        "events": events,
        "source": "journal (COLD tier), newest first",
    }


# ----------------------------------------------------------------------
# Static page
# ----------------------------------------------------------------------
# The operator's token exists from startup, so it is in .env before anyone
# goes looking for it. Never printed: the operator reads it from their own
# .env and pastes it into the app's settings drawer.
auth.operator_token()
if auth.operator_problem():
    print(f"turnstyl auth: {auth.operator_problem()}", flush=True)

# Decide once, at import, and say so once. The paywall can only be registered
# while the app is being built, so the facilitator health check happens here
# rather than on a startup event.
_x402_ok, _x402_reason = _x402_probe()
x402_status["enabled"] = _x402_ok
x402_status["reason"] = _x402_reason
if _x402_ok:
    try:
        _x402_install()
        print(f"x402: enabled on {X402_NETWORK} ({_x402_reason})", flush=True)
    except Exception as _e:  # noqa: BLE001 - a paywall that will not build is off
        x402_status["enabled"] = False
        x402_status["reason"] = f"could not install the paywall: {type(_e).__name__}: {_e}"
        print(f"x402: disabled ({x402_status['reason']})", flush=True)
else:
    print(f"x402: disabled ({_x402_reason})", flush=True)


# CORS is added LAST on purpose. Starlette applies the most recently added
# middleware outermost, and a 402 produced by the x402 paywall never reaches an
# inner layer, so a CORS middleware registered earlier would leave the browser
# unable to read the payment requirements it is being asked to satisfy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=[
        "Content-Type",
        "Accept",
        "ngrok-skip-browser-warning",
        # The buyer's session, and the operator's token. Without this on the
        # allow list the browser's preflight fails and the app can only ever
        # see the public view.
        "Authorization",
        # x402 sends the signed authorisation in a header; v1 name accepted too
        "PAYMENT-SIGNATURE",
        "X-PAYMENT",
    ],
    # The page is served from GitHub Pages and the API from a tunnel, so the
    # x402 headers are cross-origin. Without this the browser can see the 402
    # but not the requirements inside it.
    expose_headers=[
        "PAYMENT-REQUIRED",
        "PAYMENT-RESPONSE",
        "X-PAYMENT-RESPONSE",
    ],
    max_age=600,
)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    page = WEB_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"turnstyl: {page} is missing; the web UI was not installed.",
        )
    return FileResponse(page)


@app.get("/app.html", include_in_schema=False)
def app_page() -> FileResponse:
    """The buyer and operator app: jobs, payments, reports, verification.

    Served here so the same relative paths work locally at /app.html and on
    GitHub Pages at /turnstyl/app.html; index.html is the story page and links
    to this one.
    """
    page = WEB_DIR / "app.html"
    if not page.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"turnstyl: {page} is missing; the web UI was not installed.",
        )
    return FileResponse(page)


@app.get("/config.js", include_in_schema=False)
def config_js() -> FileResponse:
    """The API origin the page should talk to. Same-origin here (""), a
    tunnel URL on GitHub Pages; scripts/tunnel.sh writes it."""
    cfg = WEB_DIR / "config.js"
    if not cfg.is_file():
        raise HTTPException(status_code=404, detail="web/config.js is missing")
    return FileResponse(cfg, media_type="application/javascript", headers={"Cache-Control": "no-store"})


if (WEB_DIR / "static").is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
