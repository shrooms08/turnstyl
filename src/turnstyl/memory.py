"""Thin wrapper around sibyl-memory-client for turnstyl.

One job: open a MemoryClient against the project-local database at
``./data/turnstyl.db`` and re-expose the four memory tiers under their own
names, so the rest of turnstyl never constructs a client itself.

Tier map (from the SDK source, see docs/SIBYL_API.md):
    HOT       set_state / get_state              job progress
    WARM      set_entity / get_entity / ...      buyers, findings
    COLD      write_event / read_events          append-only journal
    REFERENCE set_reference / get_reference      pricing tables, prompts
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sibyl_memory_client import MemoryClient
from sibyl_memory_client.exceptions import NotFoundError

# The SDK does NOT read ~/.sibyl-memory/credentials.json — tenant_id is a plain
# constructor argument defaulting to DEFAULT_TENANT (a shared UUID). We pass our
# own so turnstyl's rows never mingle with any other local Sibyl consumer.
TENANT_ID = "turnstyl"

# Repo-root-relative, resolved from this file (src/turnstyl/memory.py -> ../..).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "turnstyl.db"

# Environment override, read at call time (not import time) so a subprocess that
# sets TURNSTYL_DB gets its own store. The offline demo uses this to run against
# a scratch database without touching ./data/turnstyl.db.
DB_PATH_ENV_VAR = "TURNSTYL_DB"


def default_db_path() -> Path:
    """The database this process should open: $TURNSTYL_DB, else ./data/turnstyl.db."""
    override = os.environ.get(DB_PATH_ENV_VAR)
    if override and override.strip():
        return Path(override).expanduser()
    return DB_PATH


class TurnstylMemory:
    """turnstyl's handle on Sibyl Memory. Construct one per process."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        tenant_id: str = TENANT_ID,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        # Storage.__init__ also does this, but doing it here means a permission
        # problem fails with our path in the message, not deep in the SDK.
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"turnstyl: cannot create the memory directory {self.db_path.parent}: {e}"
            ) from e
        try:
            self.client = MemoryClient.local(self.db_path, tenant_id=tenant_id)
        except Exception as e:
            raise RuntimeError(
                f"turnstyl: failed to open the memory database at {self.db_path}: "
                f"{type(e).__name__}: {e}"
            ) from e
        self.tenant_id = tenant_id

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"TurnstylMemory(db_path={self.db_path!s}, tenant_id={self.tenant_id!r})"

    # ---------------- HOT: state documents ----------------
    def set_state(self, key: str, body: dict[str, Any] | list[Any]) -> None:
        self.client.set_state(key, body)

    def get_state(self, key: str) -> dict[str, Any] | None:
        """Returns {"body": ..., "updated_at": ...} or None if the key is unset."""
        return self.client.get_state(key)

    # ---------------- WARM: entities ----------------
    def set_entity(
        self,
        category: str,
        name: str,
        body: dict[str, Any] | list[Any],
        *,
        status: str | None = None,
    ) -> dict[str, Any]:
        return self.client.set_entity(category, name, body, status=status)

    def get_entity(self, category: str, name: str) -> dict[str, Any] | None:
        """Returns the entity row, or None if absent.

        The SDK raises NotFoundError here; turnstyl wants a None so callers can
        branch on "first time we've seen this buyer" without a try/except.
        """
        try:
            return self.client.get_entity(category, name)
        except NotFoundError:
            return None

    def list_entities(
        self,
        category: str | None = None,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.client.list_entities(category, status=status, limit=limit)

    def search_entities(
        self,
        query: str,
        *,
        limit: int = 20,
        prefix: bool = False,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """FTS5 search over entity name/category/body. Returns a list subclass
        that also carries ``.verdict`` (OK / NO_MATCH)."""
        return self.client.search_entities(
            query, limit=limit, prefix=prefix, category=category
        )

    def archive_entity(
        self, category: str, name: str, reason: str | None = None
    ) -> dict[str, Any]:
        return self.client.archive_entity(category, name, reason)

    def delete_entity(self, category: str, name: str) -> bool:
        return self.client.delete_entity(category, name)

    # ---------------- COLD: journal ----------------
    def write_event(
        self,
        *,
        evaluated: Any = None,
        acted: Any = None,
        forward: Any = None,
        extra: Any = None,
        ts: str | None = None,
    ) -> str:
        """Append one journal event. Returns the new event id."""
        return self.client.write_event(
            evaluated=evaluated, acted=acted, forward=forward, extra=extra, ts=ts
        )

    def read_events(
        self,
        *,
        limit: int = 50,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest first (ORDER BY ts DESC, id DESC)."""
        return self.client.read_events(limit=limit, since=since, until=until)

    # ---------------- REFERENCE: static documents ----------------
    def set_reference(
        self,
        key: str,
        body: str | dict[str, Any] | list[Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.client.set_reference(key, body, metadata=metadata)

    def get_reference(self, key: str) -> dict[str, Any] | None:
        """Returns {"body": str, "metadata": ..., "updated_at": ...} or None.

        NOTE: ``body`` comes back as a STRING even when a dict was stored — the
        SDK canonicalizes dict/list bodies to JSON on write and does not decode
        on read. Use json.loads() if you stored structured data.
        """
        return self.client.get_reference(key)


# ----------------------------------------------------------------------
# Typed layer
# ----------------------------------------------------------------------
# TurnstylMemory above stays a thin, untyped pass-through to the SDK. The store
# below is the layer the engine talks to: it validates every row against the
# models in schema.py on the way in and on the way out, so a layout drift fails
# at the read with a pydantic error naming the field, not three calls later.
from . import schema as S  # noqa: E402


class TurnstylStore:
    """Model-typed access to turnstyl's memory layout."""

    def __init__(self, memory: TurnstylMemory | None = None) -> None:
        self.memory = memory or TurnstylMemory()

    @property
    def db_path(self) -> Path:
        return self.memory.db_path

    # ---------------- pricing rules (REFERENCE) ----------------
    def ensure_pricing_rules(self) -> S.PricingRules:
        """Write the pricing rules once, on first run; return what memory holds."""
        record = self.memory.get_reference(S.REF_PRICING_RULES)
        if record is None:
            rules = S.PricingRules()
            self.memory.set_reference(S.REF_PRICING_RULES, rules.model_dump())
            return rules
        import json

        return S.PricingRules.model_validate(json.loads(record["body"]))

    # ---------------- contract source (REFERENCE) ----------------
    def put_contract_source(self, contract_hash: str, text: str) -> None:
        """Immutable source text, keyed by its own hash and shared across jobs."""
        self.memory.set_reference(
            S.contract_ref_key(contract_hash),
            text,
            metadata={"sha256": contract_hash, "chars": len(text)},
        )

    def get_contract_source(self, contract_hash: str) -> str | None:
        record = self.memory.get_reference(S.contract_ref_key(contract_hash))
        return record["body"] if record else None

    # ---------------- job state (HOT) ----------------
    def get_job_state(self, job_id: str) -> S.JobState | None:
        record = self.memory.get_state(S.job_state_key(job_id))
        if record is None:
            return None
        return S.JobState.model_validate(record["body"])

    def put_job_state(self, state: S.JobState) -> S.JobState:
        state.updated_at = S.utc_now()
        self.memory.set_state(S.job_state_key(state.job_id), state.model_dump())
        return state

    # ---------------- active jobs (HOT) ----------------
    def get_active_jobs(self) -> list[str]:
        record = self.memory.get_state(S.STATE_ACTIVE_JOBS)
        return list(record["body"]) if record else []

    def put_active_jobs(self, job_ids: list[str]) -> None:
        self.memory.set_state(S.STATE_ACTIVE_JOBS, list(job_ids))

    def add_active_job(self, job_id: str) -> None:
        active = self.get_active_jobs()
        if job_id not in active:
            active.append(job_id)
            self.put_active_jobs(active)

    def remove_active_job(self, job_id: str) -> None:
        active = self.get_active_jobs()
        if job_id in active:
            self.put_active_jobs([j for j in active if j != job_id])

    # ---------------- buyer (WARM) ----------------
    @staticmethod
    def buyer_key(buyer: str) -> str:
        """Buyer addresses are stored lowercased; 0xAB and 0xab are one buyer."""
        return buyer.strip().lower()

    def get_buyer(self, buyer: str) -> S.BuyerLedger:
        """Absent buyer reads as a zeroed ledger — a first-time buyer is not an error."""
        row = self.memory.get_entity(S.CAT_BUYER, self.buyer_key(buyer))
        if row is None:
            return S.BuyerLedger()
        return S.BuyerLedger.model_validate(row["body"])

    def buyer_exists(self, buyer: str) -> bool:
        return self.memory.get_entity(S.CAT_BUYER, self.buyer_key(buyer)) is not None

    def put_buyer(self, buyer: str, ledger: S.BuyerLedger) -> S.BuyerLedger:
        self.memory.set_entity(
            S.CAT_BUYER,
            self.buyer_key(buyer),
            ledger.model_dump(),
            status=ledger.trust_tier,
        )
        return ledger

    # ---------------- job entity (WARM) ----------------
    def get_job_entity(self, job_id: str) -> S.JobEntity | None:
        row = self.memory.get_entity(S.CAT_JOB, job_id)
        if row is None:
            return None
        return S.JobEntity.model_validate(row["body"])

    def put_job_entity(self, job_id: str, entity: S.JobEntity) -> S.JobEntity:
        self.memory.set_entity(S.CAT_JOB, job_id, entity.model_dump())
        return entity

    def archive_job_entity(self, job_id: str, reason: str) -> bool:
        """Archive on completion. Returns False if it was already archived."""
        if self.memory.get_entity(S.CAT_JOB, job_id) is None:
            return False
        self.memory.archive_entity(S.CAT_JOB, job_id, reason)
        return True

    # ---------------- step cost (WARM) ----------------
    def get_step_cost(self, step: int) -> S.StepCost:
        row = self.memory.get_entity(S.CAT_STEP_COST, str(step))
        if row is None:
            return S.StepCost()
        return S.StepCost.model_validate(row["body"])

    def record_step_cost(self, step: int, tokens: int, seconds: float) -> S.StepCost:
        """Fold one real execution into the rolling averages for this step."""
        current = self.get_step_cost(step)
        runs = current.runs + 1
        updated = S.StepCost(
            runs=runs,
            avg_tokens=(current.avg_tokens * current.runs + tokens) / runs,
            avg_seconds=(current.avg_seconds * current.runs + seconds) / runs,
        )
        self.memory.set_entity(S.CAT_STEP_COST, str(step), updated.model_dump())
        return updated

    # ---------------- findings (WARM) ----------------
    def get_findings(self, contract_hash: str) -> S.FindingsEntity:
        row = self.memory.get_entity(S.CAT_FINDINGS, contract_hash)
        if row is None:
            return S.FindingsEntity()
        return S.FindingsEntity.model_validate(row["body"])

    def findings_exist(self, contract_hash: str) -> bool:
        return self.memory.get_entity(S.CAT_FINDINGS, contract_hash) is not None

    def put_findings(
        self, contract_hash: str, findings: S.FindingsEntity
    ) -> S.FindingsEntity:
        self.memory.set_entity(
            S.CAT_FINDINGS, contract_hash, findings.model_dump()
        )
        return findings

    # ---------------- journal (COLD) ----------------
    def journal(self, entry: S.JournalEntry) -> str:
        return self.memory.write_event(
            evaluated=entry.evaluated,
            acted=entry.acted,
            forward=entry.forward,
            extra=entry.extra,
        )

    def read_journal(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.memory.read_events(limit=limit)
