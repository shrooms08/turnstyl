"""What a job type is: an ordered list of priced, prompted, gated steps.

A job type is a spec, not code. The engine, memory, payments, credit, commit
and verify are shared; a type only says what the steps are called, what they
cost, what the model is told, and what mechanical gate runs on the answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# Gates a step's output can be put through after the model answers.
GATE_NONE = "none"
GATE_COMPILE = "compile"        # the answer is a whole contract; it must compile
GATE_FORGE_TEST = "forge_test"  # the answer is a test file; it must compile and run
GATES = (GATE_NONE, GATE_COMPILE, GATE_FORGE_TEST)

INPUT_SOLIDITY_SOURCE = "solidity_source"


@dataclass(frozen=True)
class StepSpec:
    """One step of a job type."""

    n: int
    name: str
    base_price_usdc: float
    system_prompt: str
    gate: str = GATE_NONE
    cacheable: bool = True
    # Output cap for this step. A step that returns a whole file needs more room
    # than one that returns a list; a truncated file is not a deliverable, and
    # the gate can only report that it does not compile. None uses llm's default.
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.gate not in GATES:
            raise ValueError(
                f"turnstyl: step {self.n} ({self.name}) has gate {self.gate!r}; "
                f"gates are {list(GATES)}"
            )


@dataclass(frozen=True)
class JobType:
    """A service turnstyl sells, as a spec the engine reads."""

    id: str
    name: str
    description: str
    input_kind: str
    steps: tuple[StepSpec, ...]
    # Deterministic canned outputs for MOCK_LLM=1, one per step. Lives with the
    # type because only the type knows what its answers look like.
    mock: Callable[[int, str, dict[int, str]], str] = field(repr=False, default=None)

    def __post_init__(self) -> None:
        numbers = [s.n for s in self.steps]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(
                f"turnstyl: job type {self.id!r} steps must be numbered 1..n in "
                f"order, got {numbers}"
            )

    # ---- the shape the engine asks about ----
    @property
    def first_step(self) -> int:
        return self.steps[0].n

    @property
    def last_step(self) -> int:
        return self.steps[-1].n

    @property
    def all_steps(self) -> tuple[int, ...]:
        return tuple(s.n for s in self.steps)

    def step(self, n: int) -> StepSpec:
        for s in self.steps:
            if s.n == n:
                return s
        raise ValueError(
            f"turnstyl: job type {self.id!r} has no step {n!r}; steps are "
            f"{list(self.all_steps)}"
        )

    def step_name(self, n: int) -> str:
        return self.step(n).name

    @property
    def step_names(self) -> dict[int, str]:
        return {s.n: s.name for s in self.steps}

    @property
    def base_prices(self) -> dict[int, float]:
        return {s.n: s.base_price_usdc for s in self.steps}

    def to_public(self) -> dict:
        """What the API and the page show a buyer."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "input_kind": self.input_kind,
            "steps": [
                {
                    "n": s.n,
                    "name": s.name,
                    "base_price_usdc": s.base_price_usdc,
                    "gate": s.gate,
                    "cacheable": s.cacheable,
                }
                for s in self.steps
            ],
            "total_usdc": round(sum(s.base_price_usdc for s in self.steps), 2),
        }
