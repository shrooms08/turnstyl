"""Payment backends. Day 2 ships the fake one; the Base one lands day 3.

Both backends answer the same two questions the engine cares about: what memo
should the buyer reference, and has this step been settled?
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from .memory import TurnstylMemory
from .schema import STATE_FAKE_PAYMENTS, invoice_memo

FAKE = "fake"
BASE = "base"


class PaymentBackend(ABC):
    """Settlement for one step of one job."""

    name: str = "abstract"

    @abstractmethod
    def issue_invoice(
        self, job_id: str, step: int, amount_usdc: float, buyer: str
    ) -> str:
        """Register an invoice and return the memo the buyer must reference."""

    @abstractmethod
    def check_paid(self, job_id: str, step: int) -> str | None:
        """Return the settling tx hash, or None if the step is still unpaid."""

    def mark_paid(self, job_id: str, step: int, tx_hash: str | None = None) -> str:
        """Record a settlement out of band. Only a test backend can do this."""
        raise NotImplementedError(
            f"turnstyl: the {self.name!r} payment backend cannot mark an invoice "
            f"paid by hand; a real payment must land on chain."
        )


class FakePayments(PaymentBackend):
    """Settlement by fiat of the operator, for demos and tests.

    Paid invoices live in the HOT state key "fake_payments" as
    {"<job_id>:<step>": "<tx_hash>"}, so they survive a process restart exactly
    like every other turnstyl fact.
    """

    name = FAKE

    def __init__(self, memory: TurnstylMemory) -> None:
        self.memory = memory

    def _load(self) -> dict[str, str]:
        record = self.memory.get_state(STATE_FAKE_PAYMENTS)
        return dict(record["body"]) if record else {}

    @staticmethod
    def _key(job_id: str, step: int) -> str:
        return f"{job_id}:{step}"

    def issue_invoice(
        self, job_id: str, step: int, amount_usdc: float, buyer: str
    ) -> str:
        return invoice_memo(job_id, step)

    def check_paid(self, job_id: str, step: int) -> str | None:
        return self._load().get(self._key(job_id, step))

    def mark_paid(self, job_id: str, step: int, tx_hash: str | None = None) -> str:
        paid = self._load()
        key = self._key(job_id, step)
        resolved = tx_hash or f"0xfake{job_id.replace('-', '')[:24]}{step}"
        paid[key] = resolved
        self.memory.set_state(STATE_FAKE_PAYMENTS, paid)
        return resolved


class BasePayments(PaymentBackend):
    """USDC on Base Sepolia. Day 3.

    Day 3 will watch USDC Transfer events to AGENT_ADDRESS on Base Sepolia:
    subscribe to the ERC-20 Transfer topic on the USDC contract at USDC_ADDRESS
    over BASE_SEPOLIA_RPC, filter to transfers whose `to` is AGENT_ADDRESS, and
    match each one to an open invoice by amount and by the memo the buyer
    references. ``check_paid`` will return the settling transaction hash once
    the transfer has the required confirmations.
    """

    name = BASE

    def __init__(self, memory: TurnstylMemory) -> None:
        self.memory = memory

    def issue_invoice(
        self, job_id: str, step: int, amount_usdc: float, buyer: str
    ) -> str:
        raise NotImplementedError(
            "turnstyl: the Base Sepolia payment backend lands on day 3. "
            "Run with PAYMENTS=fake for now."
        )

    def check_paid(self, job_id: str, step: int) -> str | None:
        raise NotImplementedError(
            "turnstyl: the Base Sepolia payment backend lands on day 3. "
            "Run with PAYMENTS=fake for now."
        )


def get_backend(memory: TurnstylMemory, name: str | None = None) -> PaymentBackend:
    """Select a backend from PAYMENTS (fake|base). Defaults to fake."""
    selected = (name or os.environ.get("PAYMENTS") or FAKE).strip().lower()
    if selected == FAKE:
        return FakePayments(memory)
    if selected == BASE:
        return BasePayments(memory)
    raise RuntimeError(
        f"turnstyl: unknown payment backend {selected!r}. "
        f"Set PAYMENTS to {FAKE!r} or {BASE!r}."
    )
