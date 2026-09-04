#!/usr/bin/env python3
"""Pay one turnstyl invoice from the buyer wallet, on Base Sepolia.

    .venv/bin/python scripts/buyer_pay.py <job_id> <step>

Reads the amount and memo from the agent's memory — the invoice the agent
actually issued, not a number typed on the command line — approves USDC to the
receipts contract once if needed, then calls pay(memo, amount).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from turnstyl import schema as S  # noqa: E402
from turnstyl.memory import TurnstylMemory, TurnstylStore  # noqa: E402
from turnstyl.payments import (  # noqa: E402
    CHAIN_ID,
    ERC20_ABI,
    BasePayments,
    explorer_tx,
    hex0x,
    memo_bytes32,
    require_env,
    with_retry,
)

load_dotenv()

APPROVE_USDC = 100  # one approval covers the whole demo


def resolve_invoice(store: TurnstylStore, job_id: str, step: int) -> tuple[float, str]:
    """(amount_usdc, buyer_address) for this invoice, from memory.

    Looks at the job's open invoice first, then the buyer's outstanding list —
    a step delivered on credit keeps no open invoice once the job closes, but it
    is still owed and still payable.
    """
    state = store.get_job_state(job_id)
    if state is None:
        raise SystemExit(
            f"turnstyl buyer_pay: no job {job_id!r} in memory at {store.db_path}.\n"
            f"  Run '.venv/bin/turnstyl status' to list the jobs this database knows."
        )
    invoice = state.open_invoice
    if invoice is not None and invoice.step == step:
        return invoice.amount_usdc, state.buyer

    ledger = store.get_buyer(state.buyer)
    for item in ledger.outstanding:
        if item.job_id == job_id and item.step == step:
            return item.amount_usdc, state.buyer

    raise SystemExit(
        f"turnstyl buyer_pay: job {job_id} has no invoice for step {step}.\n"
        f"  Its open invoice is "
        f"{('step ' + str(invoice.step)) if invoice else 'none'}, and the buyer "
        f"ledger lists no outstanding step {step} for this job.\n"
        f"  Run '.venv/bin/turnstyl job run {job_id}' to have the agent issue one."
    )


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: .venv/bin/python scripts/buyer_pay.py <job_id> <step>",
            file=sys.stderr,
        )
        return 2
    job_id = argv[1]
    try:
        step = int(argv[2])
    except ValueError:
        raise SystemExit(f"turnstyl buyer_pay: step must be a number, got {argv[2]!r}")

    from eth_account import Account
    from web3 import HTTPProvider, Web3

    store = TurnstylStore(TurnstylMemory())
    amount_usdc, buyer_in_memory = resolve_invoice(store, job_id, step)
    amount_units = S.usdc_base_units(amount_usdc)

    rpc = require_env("BASE_SEPOLIA_RPC", "Set it to https://sepolia.base.org")
    usdc_address = require_env("USDC_ADDRESS", "Base Sepolia USDC, 6 decimals.")
    receipts_address = require_env(
        "RECEIPTS_ADDRESS", "Deploy TurnstylReceipts and record its address."
    )
    buyer_key = require_env(
        "BUYER_PRIVATE_KEY", "The buyer signs pay() with its own key."
    )

    w3 = Web3(HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    buyer = Account.from_key(buyer_key)
    receipts = w3.to_checksum_address(receipts_address)
    usdc = w3.eth.contract(address=w3.to_checksum_address(usdc_address), abi=ERC20_ABI)

    if buyer.address.lower() != buyer_in_memory.lower():
        raise SystemExit(
            f"turnstyl buyer_pay: BUYER_PRIVATE_KEY controls {buyer.address}, but "
            f"job {job_id} was invoiced to {buyer_in_memory}.\n"
            f"  check_paid matches on the payer address, so a payment from this "
            f"wallet would never settle that invoice."
        )

    memo = memo_bytes32(job_id, step)
    print(f"job {job_id} step {step} ({S.STEP_NAMES.get(step, '?')})")
    print(f"amount   {amount_usdc:.2f} USDC ({amount_units} base units)")
    print(f"memo     0x{memo.hex()}")
    print(f"receipts {receipts}")
    print(f"buyer    {buyer.address}")

    balance = with_retry(
        "reading the buyer USDC balance", usdc.functions.balanceOf(buyer.address).call
    )
    if balance < amount_units:
        raise SystemExit(
            f"turnstyl buyer_pay: buyer holds {balance / S.USDC_UNITS:.2f} USDC but "
            f"the invoice is {amount_usdc:.2f} USDC.\n"
            f"  Run: .venv/bin/python scripts/topup_buyer.py"
        )

    def send(name: str, fn, gas: int) -> str:
        nonce = with_retry(
            f"reading the buyer nonce for {name}",
            w3.eth.get_transaction_count,
            buyer.address,
            "pending",
        )
        tx = fn.build_transaction(
            {
                "from": buyer.address,
                "nonce": nonce,
                "chainId": CHAIN_ID,
                "gas": gas,
                "maxFeePerGas": w3.to_wei(0.02, "gwei"),
                "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
            }
        )
        signed = buyer.sign_transaction(tx)
        tx_hash = with_retry(
            f"broadcasting {name}", w3.eth.send_raw_transaction, signed.raw_transaction
        )
        receipt = with_retry(
            f"waiting for the {name} receipt",
            w3.eth.wait_for_transaction_receipt,
            tx_hash,
            180,
        )
        if receipt["status"] != 1:
            raise SystemExit(
                f"turnstyl buyer_pay: {name} reverted on chain (tx {hex0x(tx_hash)}).\n"
                f"  {explorer_tx(hex0x(tx_hash))}"
            )
        return hex0x(tx_hash)

    allowance = with_retry(
        "reading the USDC allowance",
        usdc.functions.allowance(buyer.address, receipts).call,
    )
    if allowance < amount_units:
        approve_units = APPROVE_USDC * S.USDC_UNITS
        print(
            f"allowance {allowance / S.USDC_UNITS:.2f} USDC is below the invoice; "
            f"approving {APPROVE_USDC} USDC once"
        )
        approve_tx = send("approve", usdc.functions.approve(receipts, approve_units), 80_000)
        print(f"APPROVE tx {approve_tx}")
        print(explorer_tx(approve_tx))

    receipts_contract = BasePayments(store.memory).contract
    pay_tx = send("pay", receipts_contract.functions.pay(memo, amount_units), 150_000)
    print(f"PAID {amount_usdc:.2f} USDC tx {pay_tx}")
    print(explorer_tx(pay_tx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
