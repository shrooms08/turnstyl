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

import json
import os
import re
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import policy
from . import schema as S
from .engine import Engine
from .memory import TENANT_ID, TurnstylMemory, TurnstylStore, default_db_path
from .payments import get_backend

CHAIN_ID = 84532
EXPLORER = "https://sepolia.basescan.org"

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(
    title="turnstyl",
    description="The agent's memory over HTTP, and one endpoint that gives it work.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


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
    """State keys + entities + journal events held in the store.

    Counted over a read-only connection rather than the SDK's list methods,
    which clamp their limits and so cannot report a true total. Archived
    entities are deliberately not included: this is a count of what the agent
    is actively carrying.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        total = 0
        for table in ("state_documents", "entities", "journal_events"):
            total += conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        conn.close()
    except sqlite3.Error:
        return 0
    return total


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
        "memory_missing": not exists,
    }


# ----------------------------------------------------------------------
# /api/jobs
# ----------------------------------------------------------------------
def job_summary(state: S.JobState, archived: bool, source: str) -> dict[str, Any]:
    return {
        "job_id": state.job_id,
        "buyer": state.buyer,
        "contract_hash": state.contract_hash,
        "current_step": state.current_step,
        "status": state.status,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "archived": archived,
        "source": source,
    }


@app.get("/api/jobs")
def api_jobs(
    buyer: str | None = Query(default=None, description="Only this buyer's jobs."),
) -> dict[str, Any]:
    store = open_store()
    if store is None:
        return missing({"jobs": [], "buyer": buyer, "source": "no database"})
    buyer_key = buyer.strip().lower() if buyer else None

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

    if buyer_key:
        jobs = [j for j in jobs if j["buyer"] == buyer_key]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return {
        "memory_missing": False,
        "buyer": buyer_key,
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
def step_view(step: int, record: S.StepRecord) -> dict[str, Any]:
    view = {
        "step": step,
        "name": S.STEP_NAMES.get(step, str(step)),
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
        "output": record.output,
    }
    if step == S.STEP_PATCH:
        view["compiles"] = record.compiles
    return view


def not_started_view(step: int) -> dict[str, Any]:
    """A step the job has not reached. Same keys as a recorded step, so a
    consumer can render the four cards from one shape."""
    view = {
        "step": step,
        "name": S.STEP_NAMES.get(step, str(step)),
        "status": "not_started",
        "price_usdc": S.BASE_PRICES.get(step, 0.0),
        "paid": False,
        "cached": False,
        "tokens_in": None,
        "tokens_out": None,
        "seconds": None,
        "output_sha256": None,
        "commit_tx": None,
        "pay_tx": None,
        "output": None,
    }
    if step == S.STEP_PATCH:
        view["compiles"] = None
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
        recorded = {int(k): step_view(int(k), record) for k, record in entity.steps.items()}
    steps = [recorded.get(n) or not_started_view(n) for n in S.ALL_STEPS]

    invoice = state.open_invoice
    return {
        "memory_missing": False,
        "job_id": state.job_id,
        "buyer": state.buyer,
        "contract_hash": state.contract_hash,
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
def api_job(job_id: str) -> dict[str, Any]:
    store = open_store()
    if store is None:
        return missing({"job": None})
    return job_detail(store, job_id)


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


@app.post("/api/jobs")
def api_create_job(body: NewJobRequest) -> dict[str, Any]:
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
    filename = (body.filename or "contract.sol").strip() or "contract.sol"
    filename = os.path.basename(filename)[:80]

    store = TurnstylStore(TurnstylMemory(db_path()))
    engine = Engine(store=store, payments=get_backend(store.memory))
    try:
        outcome = engine.new_job_from_source(source, buyer, filename=filename)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    detail = job_detail(store, outcome.job_id)
    detail["resumed"] = bool(outcome.resumed)
    detail["decision"] = outcome.decision
    return detail


# ----------------------------------------------------------------------
# /api/buyers/{address}
# ----------------------------------------------------------------------
@app.get("/api/buyers/{address}")
def api_buyer(address: str) -> dict[str, Any]:
    store = open_store()
    if store is None:
        return missing({"buyer": None})

    key = store.buyer_key(address)
    ledger = store.get_buyer(key)
    known = store.buyer_exists(key)

    # The explanation is policy.py's own words, not a paraphrase: ask it what it
    # would decide if this buyer requested the next paid step right now.
    probe = S.JobState(
        job_id="probe",
        buyer=key,
        contract_hash="0" * 64,
        current_step=S.STEP_FINDINGS,
        status=S.STATUS_AWAITING_PAYMENT,
    )
    decision, reason = policy.decide(S.STEP_FINDINGS, ledger, probe)

    return {
        "memory_missing": False,
        "buyer": key,
        "known": known,
        "ledger": ledger.model_dump(),
        "trust": {
            "trust_tier": ledger.trust_tier,
            "would_decide": decision,
            "explanation": reason,
            "steps_until_credit": policy.steps_until_credit(ledger),
            "earned_back": policy.earned_back(ledger),
        },
        "outstanding": [item.model_dump() for item in ledger.outstanding],
        "jobs": ledger.jobs,
        "source": (
            "entity buyer/<address>; trust.explanation is the reason string "
            "policy.decide produces for the next paid step"
        ),
    }


# ----------------------------------------------------------------------
# /api/journal
# ----------------------------------------------------------------------
@app.get("/api/journal")
def api_journal(
    job: str | None = Query(default=None, description="Filter to one job id."),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    store = open_store()
    if store is None:
        return missing({"events": [], "job": job, "limit": limit})

    # Filtering happens after the read, so ask for a wider window than the caller
    # wants when they are narrowing to one job.
    window = min(500, limit * 10) if job else limit
    events = []
    for event in store.read_journal(limit=window):
        extra = event.get("extra") or {}
        if job and extra.get("job_id") != job:
            continue
        events.append(
            {
                "ts": event.get("ts"),
                "decision": extra.get("decision"),
                "step": extra.get("step"),
                "buyer": extra.get("buyer"),
                "evaluated": event.get("evaluated") or [],
                "acted": event.get("acted") or [],
                "forward": event.get("forward") or [],
                "extra": extra,
            }
        )
        if len(events) >= limit:
            break

    return {
        "memory_missing": False,
        "job": job,
        "limit": limit,
        "count": len(events),
        "events": events,
        "source": "journal (COLD tier), newest first",
    }


# ----------------------------------------------------------------------
# Static page
# ----------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    page = WEB_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"turnstyl: {page} is missing; the web UI was not installed.",
        )
    return FileResponse(page)


if (WEB_DIR / "static").is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
