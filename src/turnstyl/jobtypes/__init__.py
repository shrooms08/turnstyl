"""The registry of services turnstyl sells.

A job type is a spec (see ``base.JobType``). Everything under it — the engine,
memory, pricing, credit, payments, commit and verify — is shared, so adding a
service is adding a spec, not a code path.
"""
from __future__ import annotations

from .audit import AUDIT
from .base import (
    GATE_COMPILE,
    GATE_FORGE_TEST,
    GATE_NONE,
    INPUT_SOLIDITY_SOURCE,
    JobType,
    StepSpec,
)
from .tests import TESTS_TYPE

DEFAULT_TYPE_ID = AUDIT.id

_REGISTRY: dict[str, JobType] = {t.id: t for t in (AUDIT, TESTS_TYPE)}


def all_types() -> list[JobType]:
    """Every service, in the order a buyer should be offered them."""
    return list(_REGISTRY.values())


def get(type_id: str | None) -> JobType:
    """Look up a job type. ``None`` or "" means the default.

    A row written before job types existed carries no type and reads as the
    audit, which is what it was.
    """
    key = (type_id or DEFAULT_TYPE_ID).strip().lower()
    spec = _REGISTRY.get(key)
    if spec is None:
        raise ValueError(
            f"turnstyl: unknown job type {type_id!r}. "
            f"Known types: {', '.join(sorted(_REGISTRY))}."
        )
    return spec


def is_known(type_id: str | None) -> bool:
    return (type_id or DEFAULT_TYPE_ID).strip().lower() in _REGISTRY


__all__ = [
    "AUDIT", "TESTS_TYPE", "JobType", "StepSpec", "DEFAULT_TYPE_ID",
    "GATE_NONE", "GATE_COMPILE", "GATE_FORGE_TEST", "INPUT_SOLIDITY_SOURCE",
    "all_types", "get", "is_known",
]
