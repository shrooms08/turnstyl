"""Payment backends.

Both backends answer the same questions the engine asks: what memo should the
buyer reference, has this step been settled, and can the delivered output be
committed. ``fake`` answers them from memory; ``base`` answers them from Base
Sepolia.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod

from dotenv import load_dotenv

from . import policy
from . import schema as S
from .memory import TurnstylMemory, TurnstylStore

load_dotenv()

FAKE = "fake"
BASE = "base"

EXPLORER = "https://sepolia.basescan.org"


def hex0x(value) -> str:
    """A 0x-prefixed hex string.

    hexbytes 2.x dropped the 0x prefix from HexBytes.hex(), so a raw .hex() call
    yields a hash no explorer and no `cast` invocation will accept. Everything
    that leaves turnstyl as a hash goes through here.
    """
    if isinstance(value, str):
        return value if value.startswith("0x") else "0x" + value
    if hasattr(value, "to_0x_hex"):
        return value.to_0x_hex()
    return "0x" + bytes(value).hex()


def explorer_tx(tx_hash) -> str:
    return f"{EXPLORER}/tx/{hex0x(tx_hash)}"


def explorer_address(address: str) -> str:
    return f"{EXPLORER}/address/{address}"


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
            f"paid by hand; a real payment must land on chain.\n"
            f"  Have the buyer run: .venv/bin/python scripts/buyer_pay.py "
            f"<job_id> <step>"
        )

    # ---- optional capabilities; the default answers keep `fake` unchanged ----
    def current_block(self) -> int | None:
        """Chain height when an invoice is issued, stored as invoice_block."""
        return None

    def reconcile(self, buyer: str) -> list[dict]:
        """Settle any outstanding invoice this buyer has since paid.

        Shared by both backends: the only thing that differs is what counts as
        evidence of payment, and that is ``check_paid``. A buyer who clears a
        debt gets their standing back without anyone editing the database.
        """
        store = getattr(self, "store", None)
        if store is None:
            return []
        buyer_key = store.buyer_key(buyer)
        ledger = store.get_buyer(buyer_key)
        if not ledger.outstanding:
            return []

        cleared: list[dict] = []
        remaining: list[S.OutstandingItem] = []
        for item in ledger.outstanding:
            tx_hash = self.check_paid(item.job_id, item.step)
            if not tx_hash:
                remaining.append(item)
                continue
            cleared.append(
                {
                    "job_id": item.job_id,
                    "step": item.step,
                    "amount_usdc": item.amount_usdc,
                    "tx_hash": tx_hash,
                }
            )
            ledger.paid_steps += 1
            ledger.consecutive_paid_since_default += 1
            ledger.paid_usdc = round(ledger.paid_usdc + item.amount_usdc, 2)
            ledger.unpaid_from_prior_jobs = max(
                0, ledger.unpaid_from_prior_jobs - 1
            )

        if not cleared:
            return []

        ledger.outstanding = remaining
        before = ledger.trust_tier
        ledger.trust_tier = policy.recompute_trust_tier(ledger)
        store.put_buyer(buyer_key, ledger)

        for item in cleared:
            store.journal(
                S.JournalEntry(
                    evaluated=[
                        f"entity buyer/{buyer_key} -> outstanding step "
                        f"{item['step']} of job {item['job_id']} at "
                        f"{item['amount_usdc']:.2f} USDC",
                        f"payments {self.name} -> settled in {item['tx_hash']}",
                    ],
                    acted=[
                        f"reconciled step {item['step']} of job "
                        f"{item['job_id']}; paid_steps={ledger.paid_steps}, "
                        f"paid_usdc={ledger.paid_usdc:.2f}, "
                        f"unpaid_from_prior_jobs="
                        f"{ledger.unpaid_from_prior_jobs}, "
                        f"consecutive_paid_since_default="
                        f"{ledger.consecutive_paid_since_default}, "
                        f"trust_tier {before} -> {ledger.trust_tier}"
                    ],
                    forward=["buyer standing updated; paid work may resume"],
                    extra={
                        "job_id": item["job_id"],
                        "buyer": buyer_key,
                        "step": item["step"],
                        "decision": "RECONCILED",
                        "price": item["amount_usdc"],
                        "tx_hash": item["tx_hash"],
                    },
                )
            )
        return cleared

    def commit_output(self, job_id: str, step: int, output_sha256: str) -> str | None:
        """Publish the hash of a delivered output. Returns a tx hash."""
        return None

    def payee_address(self) -> str:
        """What the INVOICE panel tells the buyer to pay."""
        return os.environ.get("AGENT_ADDRESS") or "(AGENT_ADDRESS not set in .env)"

    def buyer_command(self, job_id: str, step: int) -> str:
        """The exact command a buyer runs to settle."""
        return f".venv/bin/turnstyl pay {job_id} {step}"


class FakePayments(PaymentBackend):
    """Settlement by fiat of the operator, for demos and tests.

    Paid invoices live in the HOT state key "fake_payments" as
    {"<job_id>:<step>": "<tx_hash>"}, so they survive a process restart exactly
    like every other turnstyl fact.
    """

    name = FAKE

    def __init__(self, memory: TurnstylMemory) -> None:
        self.memory = memory
        self.store = TurnstylStore(memory)

    def _load(self) -> dict[str, str]:
        record = self.memory.get_state(S.STATE_FAKE_PAYMENTS)
        return dict(record["body"]) if record else {}

    @staticmethod
    def _key(job_id: str, step: int) -> str:
        return f"{job_id}:{step}"

    def issue_invoice(
        self, job_id: str, step: int, amount_usdc: float, buyer: str
    ) -> str:
        return S.invoice_memo(job_id, step)

    def check_paid(self, job_id: str, step: int) -> str | None:
        return self._load().get(self._key(job_id, step))

    def mark_paid(self, job_id: str, step: int, tx_hash: str | None = None) -> str:
        paid = self._load()
        key = self._key(job_id, step)
        resolved = tx_hash or f"0xfake{job_id.replace('-', '')[:24]}{step}"
        paid[key] = resolved
        self.memory.set_state(S.STATE_FAKE_PAYMENTS, paid)
        return resolved


# ----------------------------------------------------------------------
# Base Sepolia
# ----------------------------------------------------------------------
PAID_EVENT_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "memo", "type": "bytes32"},
        {"indexed": True, "name": "payer", "type": "address"},
        {"indexed": False, "name": "amount", "type": "uint256"},
    ],
    "name": "Paid",
    "type": "event",
}
RECEIPTS_ABI = [
    PAID_EVENT_ABI,
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "memo", "type": "bytes32"},
            {"indexed": False, "name": "outputHash", "type": "bytes32"},
        ],
        "name": "Committed",
        "type": "event",
    },
    {
        "inputs": [
            {"name": "memo", "type": "bytes32"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "pay",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "memo", "type": "bytes32"},
            {"name": "outputHash", "type": "bytes32"},
        ],
        "name": "commit",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

ERC20_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

CHAIN_ID = 84532
LOG_CHUNK_BLOCKS = 5_000
RPC_ATTEMPTS = 6            # 1.5+3+6+12+24 = 46.5s of backoff before giving up
RPC_BACKOFF_SECONDS = 1.5


def require_env(name: str, hint: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise RuntimeError(f"turnstyl: {name} is not set in .env. {hint}")
    return value.strip()


def with_retry(what: str, fn, *args, **kwargs):
    """Public RPCs rate-limit. Back off and retry rather than losing the run."""
    last: Exception | None = None
    for attempt in range(RPC_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - retried, then re-raised loudly
            last = e
            if attempt == RPC_ATTEMPTS - 1:
                break
            time.sleep(RPC_BACKOFF_SECONDS * (2**attempt))
    raise RuntimeError(
        f"turnstyl: {what} failed after {RPC_ATTEMPTS} attempts against "
        f"BASE_SEPOLIA_RPC: {type(last).__name__}: {last}"
    ) from last


def memo_bytes32(job_id: str, step: int) -> bytes:
    """keccak256 of the utf-8 string "<job_id>:<step>". The invoice's identity."""
    from web3 import Web3

    return Web3.keccak(text=S.invoice_memo_raw(job_id, step))


class BasePayments(PaymentBackend):
    """USDC on Base Sepolia, settled through the TurnstylReceipts contract.

    A payment is a `Paid(memo, payer, amount)` log on the receipts contract. That
    log is the only evidence this backend trusts: it never takes the buyer's word
    and never holds custody. Delivered outputs are committed back to the same
    contract as `Committed(memo, outputHash)`.
    """

    name = BASE

    def __init__(self, memory: TurnstylMemory) -> None:
        self.memory = memory
        self.store = TurnstylStore(memory)
        self.rpc_url = require_env(
            "BASE_SEPOLIA_RPC", "Set it to https://sepolia.base.org"
        )
        self.receipts_address = require_env(
            "RECEIPTS_ADDRESS",
            "Deploy contracts/src/TurnstylReceipts.sol and record its address.",
        )
        self.deploy_block = int(os.environ.get("RECEIPTS_DEPLOY_BLOCK") or 0)
        self._w3 = None
        self._contract = None

    # ---------------- chain plumbing ----------------
    @property
    def w3(self):
        if self._w3 is None:
            from web3 import HTTPProvider, Web3

            self._w3 = Web3(HTTPProvider(self.rpc_url, request_kwargs={"timeout": 30}))
            if not with_retry("connecting to Base Sepolia", self._w3.is_connected):
                raise RuntimeError(
                    f"turnstyl: cannot reach BASE_SEPOLIA_RPC at {self.rpc_url}."
                )
        return self._w3

    @property
    def contract(self):
        if self._contract is None:
            self._contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(self.receipts_address),
                abi=RECEIPTS_ABI,
            )
        return self._contract

    def current_block(self) -> int | None:
        return with_retry("reading the chain height", lambda: self.w3.eth.block_number)

    def payee_address(self) -> str:
        return self.w3.to_checksum_address(self.receipts_address)

    def buyer_command(self, job_id: str, step: int) -> str:
        return f".venv/bin/python scripts/buyer_pay.py {job_id} {step}"

    # ---------------- invoicing ----------------
    def issue_invoice(
        self, job_id: str, step: int, amount_usdc: float, buyer: str
    ) -> str:
        """The memo is deterministic, so an invoice needs no on-chain write."""
        return hex0x(memo_bytes32(job_id, step))

    # ---------------- settlement ----------------
    def _invoice_facts(self, job_id: str, step: int) -> tuple[float, str, int] | None:
        """(expected_usdc, buyer_address, from_block) for one invoice.

        Looks at the job's open invoice first, then the buyer ledger's
        outstanding list — a step delivered on credit keeps no open invoice once
        the job closes, but the debt is still owed and still settleable.
        """
        state = self.store.get_job_state(job_id)
        if state is None:
            return None
        invoice = state.open_invoice
        if invoice is not None and invoice.step == step:
            return (
                invoice.amount_usdc,
                state.buyer,
                invoice.invoice_block or self.deploy_block,
            )
        ledger = self.store.get_buyer(state.buyer)
        for item in ledger.outstanding:
            if item.job_id == job_id and item.step == step:
                return (
                    item.amount_usdc,
                    state.buyer,
                    item.invoice_block or self.deploy_block,
                )
        return None

    def _paid_logs(self, memo: bytes, from_block: int) -> list:
        """Every Paid log carrying this memo, from `from_block` to latest."""
        latest = with_retry("reading the chain height", lambda: self.w3.eth.block_number)
        start = max(0, from_block)
        topics = [
            self.w3.keccak(text="Paid(bytes32,address,uint256)"),
            memo,
        ]
        found: list = []
        while start <= latest:
            end = min(start + LOG_CHUNK_BLOCKS - 1, latest)
            chunk = with_retry(
                f"fetching Paid logs for blocks {start}-{end}",
                self.w3.eth.get_logs,
                {
                    "address": self.w3.to_checksum_address(self.receipts_address),
                    "fromBlock": start,
                    "toBlock": end,
                    "topics": topics,
                },
            )
            found.extend(chunk)
            start = end + 1
        return found

    def check_paid(self, job_id: str, step: int) -> str | None:
        """A Paid log from this buyer for at least the invoiced amount."""
        facts = self._invoice_facts(job_id, step)
        if facts is None:
            return None
        expected_usdc, buyer, from_block = facts
        expected_units = S.usdc_base_units(expected_usdc)
        memo = memo_bytes32(job_id, step)
        for log in self._paid_logs(memo, from_block):
            event = self.contract.events.Paid().process_log(log)
            payer = event["args"]["payer"]
            amount = event["args"]["amount"]
            if amount >= expected_units and payer.lower() == buyer.lower():
                return hex0x(log["transactionHash"])
        return None

    # ---------------- commitments ----------------
    def commit_output(self, job_id: str, step: int, output_sha256: str) -> str | None:
        """Publish sha256 of the delivered output as Committed(memo, outputHash)."""
        from eth_account import Account

        key = require_env(
            "AGENT_PRIVATE_KEY", "The agent signs commit() with its own key."
        )
        account = Account.from_key(key)
        memo = memo_bytes32(job_id, step)
        output_hash = bytes.fromhex(output_sha256)
        if len(output_hash) != 32:
            raise RuntimeError(
                f"turnstyl: output_sha256 must be 32 bytes of hex, got "
                f"{len(output_hash)} bytes for job {job_id} step {step}."
            )

        nonce = with_retry(
            "reading the agent nonce",
            self.w3.eth.get_transaction_count,
            account.address,
            "pending",
        )
        tx = self.contract.functions.commit(memo, output_hash).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": CHAIN_ID,
                "gas": 120_000,
                "maxFeePerGas": self.w3.to_wei(0.02, "gwei"),
                "maxPriorityFeePerGas": self.w3.to_wei(0.001, "gwei"),
            }
        )
        signed = account.sign_transaction(tx)
        tx_hash = with_retry(
            "broadcasting the commit transaction",
            self.w3.eth.send_raw_transaction,
            signed.raw_transaction,
        )
        receipt = with_retry(
            "waiting for the commit receipt",
            self.w3.eth.wait_for_transaction_receipt,
            tx_hash,
            120,
        )
        if receipt["status"] != 1:
            raise RuntimeError(
                f"turnstyl: commit() reverted on chain for job {job_id} step {step} "
                f"(tx {hex0x(tx_hash)})."
            )
        return hex0x(tx_hash)


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
