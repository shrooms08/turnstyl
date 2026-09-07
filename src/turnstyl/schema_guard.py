"""Check that what is in the store still matches the models reading it.

Every model forbids unknown fields, on purpose: a layout drift should fail at
the read rather than be silently half-understood. The cost of that choice is
this failure mode, which happened for real: a schema change landed, an older
server was still running, and every ledger read raised `extra_forbidden`. The
API answered 500 and the page, which treats a failed fetch as "no data",
showed a store full of jobs as empty.

Both halves of that are fixed here. On startup the server reads a sample of
every entity kind and refuses to serve if any of them no longer parse, naming
the entity and the field. At runtime a validation error becomes a 503 with the
same sentence rather than a traceback, and the page says the agent needs a
restart instead of showing nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError

from . import schema as S
from .memory import TurnstylStore

# One sample of each is enough: a drift is a change of shape, not of one row.
# More would only make startup slower without making the answer different.
SAMPLE = 5

# Every entity kind, the model that has to read it, and the function the rest
# of the code actually reads it with. That third column is not decoration: the
# guard was once stricter than the real read path, because it called
# model_validate where the code called FindingsEntity.from_body, and it
# condemned a healthy store over a row the agent reads perfectly well. A guard
# that does not use the reader is testing something nobody runs.
KINDS: tuple[tuple[str, type, Callable[[dict], Any]], ...] = (
    (S.CAT_BUYER, S.BuyerLedger, S.BuyerLedger.model_validate),
    (S.CAT_JOB, S.JobEntity, S.JobEntity.model_validate),
    (S.CAT_STEP_COST, S.StepCost, S.StepCost.model_validate),
    (S.CAT_FINDINGS, S.FindingsEntity, S.FindingsEntity.from_body),
    (S.CAT_PATTERN, S.BuyerPattern, S.BuyerPattern.model_validate),
    (S.CAT_DIGEST, S.DigestEntity, S.DigestEntity.model_validate),
)

RESTART = (
    "Restart the agent on the code that wrote this store, or run the migration "
    "that brings the store up to this code."
)


@dataclass(frozen=True)
class Problem:
    """One row the current models cannot read, said in a way an operator can act on."""

    kind: str
    name: str
    field: str
    detail: str

    def line(self) -> str:
        return (
            f"entity {self.kind}/{self.name} has {self.field}, which this build's "
            f"{self.kind} model does not accept ({self.detail}). {RESTART}"
        )


def _first_error(error: ValidationError) -> tuple[str, str]:
    """The field and the reason, from the first thing pydantic objected to."""
    problems = error.errors()
    if not problems:
        return "an unreadable field", "no detail"
    first = problems[0]
    where = ".".join(str(part) for part in first.get("loc") or ()) or "an unnamed field"
    kind = str(first.get("type") or "invalid")
    if kind == "extra_forbidden":
        return f"an unknown field {where!r}", "extra_forbidden"
    if kind == "missing":
        return f"no {where!r}", "missing"
    return f"a bad {where!r}", kind


def check(store: TurnstylStore, sample: int = SAMPLE) -> list[Problem]:
    """Read a sample of every entity kind. Returns what does not parse."""
    problems: list[Problem] = []
    for kind, _model, read in KINDS:
        try:
            rows = store.memory.list_entities(kind, limit=sample)
        except Exception:  # noqa: BLE001 - a kind with no table yet is not drift
            continue
        for row in rows:
            try:
                read(row.get("body") or {})
            except ValidationError as e:
                field, detail = _first_error(e)
                problems.append(
                    Problem(kind=kind, name=str(row.get("name") or "?"),
                            field=field, detail=detail)
                )
                break               # one example per kind is the whole story
    return problems


def message(problems: list[Problem]) -> str:
    """The one line the operator sees, whether at startup or in a 503."""
    if not problems:
        return ""
    first = problems[0]
    more = (
        f" ({len(problems) - 1} other entity kind(s) are affected too)"
        if len(problems) > 1
        else ""
    )
    return first.line() + more


def read_error_message(error: Exception) -> str:
    """A validation error raised while serving, as the same sentence."""
    if isinstance(error, ValidationError):
        title = getattr(error, "title", "") or "an entity"
        field, detail = _first_error(error)
        return (
            f"this store holds a {title} that this build cannot read: it has "
            f"{field} ({detail}). {RESTART}"
        )
    return f"this store could not be read: {type(error).__name__}: {error}. {RESTART}"


def state() -> dict[str, Any]:
    """What ``/api/status`` reports, filled in by the API at startup."""
    return {"ok": True, "problem": None}
