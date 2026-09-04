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


class TurnstylMemory:
    """turnstyl's handle on Sibyl Memory. Construct one per process."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        tenant_id: str = TENANT_ID,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else DB_PATH
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
