#!/usr/bin/env python3
"""Top the buyer up to a working USDC balance, from the agent wallet.

    .venv/bin/python scripts/topup_buyer.py

Idempotent: sends 1 USDC only when the buyer holds less than 2.5 USDC, and
prints what it saw either way.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from turnstyl import schema as S  # noqa: E402
from turnstyl.payments import (  # noqa: E402
    CHAIN_ID,
    ERC20_ABI,
    explorer_tx,
    hex0x,
    require_env,
    with_retry,
)

load_dotenv()

THRESHOLD_USDC = 2.5
TOPUP_USDC = 1.0


def main() -> int:
    from eth_account import Account
    from web3 import HTTPProvider, Web3

    rpc = require_env("BASE_SEPOLIA_RPC", "Set it to https://sepolia.base.org")
    usdc_address = require_env("USDC_ADDRESS", "Base Sepolia USDC, 6 decimals.")
    buyer_address = require_env("BUYER_ADDRESS", "The wallet that pays invoices.")
    agent_key = require_env("AGENT_PRIVATE_KEY", "The agent funds the top-up.")

    w3 = Web3(HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    agent = Account.from_key(agent_key)
    buyer = w3.to_checksum_address(buyer_address)
    usdc = w3.eth.contract(address=w3.to_checksum_address(usdc_address), abi=ERC20_ABI)

    buyer_units = with_retry(
        "reading the buyer USDC balance", usdc.functions.balanceOf(buyer).call
    )
    agent_units = with_retry(
        "reading the agent USDC balance", usdc.functions.balanceOf(agent.address).call
    )
    buyer_usdc = buyer_units / S.USDC_UNITS
    agent_usdc = agent_units / S.USDC_UNITS
    print(f"buyer {buyer} holds {buyer_usdc:.2f} USDC")
    print(f"agent {agent.address} holds {agent_usdc:.2f} USDC")

    if buyer_usdc >= THRESHOLD_USDC:
        print(
            f"TOPUP SKIPPED: buyer is at or above the {THRESHOLD_USDC:.2f} USDC "
            f"threshold; nothing sent"
        )
        return 0

    send_units = S.usdc_base_units(TOPUP_USDC)
    if agent_units < send_units:
        raise SystemExit(
            f"turnstyl topup: agent holds {agent_usdc:.2f} USDC, not enough to send "
            f"{TOPUP_USDC:.2f} USDC to the buyer."
        )

    nonce = with_retry(
        "reading the agent nonce",
        w3.eth.get_transaction_count,
        agent.address,
        "pending",
    )
    tx = usdc.functions.transfer(buyer, send_units).build_transaction(
        {
            "from": agent.address,
            "nonce": nonce,
            "chainId": CHAIN_ID,
            "gas": 120_000,
            "maxFeePerGas": w3.to_wei(0.02, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        }
    )
    signed = agent.sign_transaction(tx)
    tx_hash = with_retry(
        "broadcasting the top-up", w3.eth.send_raw_transaction, signed.raw_transaction
    )
    receipt = with_retry(
        "waiting for the top-up receipt",
        w3.eth.wait_for_transaction_receipt,
        tx_hash,
        180,
    )
    if receipt["status"] != 1:
        raise SystemExit(
            f"turnstyl topup: USDC transfer reverted (tx {hex0x(tx_hash)}).\n"
            f"  {explorer_tx(hex0x(tx_hash))}"
        )
    print(f"TOPUP SENT {TOPUP_USDC:.2f} USDC tx {hex0x(tx_hash)}")
    print(explorer_tx(hex0x(tx_hash)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
