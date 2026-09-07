#!/usr/bin/env python3
"""Pay one turnstyl invoice over x402: gasless, no approval.

    .venv/bin/python scripts/buyer_pay_x402.py <job_id> <step>
    .venv/bin/python scripts/buyer_pay_x402.py --settle <buyer> <job_id> <step>

The buyer signs an EIP-3009 transferWithAuthorization; the facilitator submits
it and pays the gas. See docs/X402.md for the wire formats. The receipts-contract
path (scripts/buyer_pay.py) remains the fallback and needs ETH.

Exit codes: 0 paid, 2 usage, 3 the agent or facilitator refused (the reason is
printed, and the caller may treat it as SKIP rather than a failure).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

API = (os.environ.get("TURNSTYL_API") or "http://127.0.0.1:8787").rstrip("/")
REFUSED = 3


def die(message: str, code: int = REFUSED) -> None:
    print(f"turnstyl x402: {message}", file=sys.stderr)
    raise SystemExit(code)


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--settle":
        if len(argv) != 5:
            print(
                "usage: buyer_pay_x402.py --settle <buyer> <job_id> <step>",
                file=sys.stderr,
            )
            return 2
        buyer_arg, job_id, step_s = argv[2], argv[3], argv[4]
        url = f"{API}/api/buyers/{buyer_arg}/settle-x402/{job_id}/{step_s}"
    elif len(argv) == 3:
        job_id, step_s = argv[1], argv[2]
        url = f"{API}/api/jobs/{job_id}/pay-x402/{step_s}"
    else:
        print(
            "usage: buyer_pay_x402.py <job_id> <step>\n"
            "       buyer_pay_x402.py --settle <buyer> <job_id> <step>",
            file=sys.stderr,
        )
        return 2
    try:
        step = int(step_s)
    except ValueError:
        die(f"step must be a number, got {step_s!r}", 2)

    key = os.environ.get("BUYER_PRIVATE_KEY")
    if not key:
        die("BUYER_PRIVATE_KEY is not set in .env")

    import httpx
    from eth_account import Account
    from x402 import x402Client
    from x402.mechanisms.evm.exact.register import register_exact_evm_client
    from x402.schemas import PaymentRequired

    buyer = Account.from_key(key)
    print(f"job {job_id} step {step}")
    print(f"buyer    {buyer.address}")
    print(f"endpoint {url}")

    # 1. Ask, unpaid, and read the requirements out of the PAYMENT-REQUIRED header
    try:
        first = httpx.post(url, timeout=60)
    except Exception as e:  # noqa: BLE001
        die(f"could not reach the agent at {API}: {type(e).__name__}: {e}")
    if first.status_code != 402:
        die(
            f"expected 402 with payment requirements, got {first.status_code}: "
            f"{first.text[:300]}"
        )
    raw = first.headers.get("payment-required") or first.headers.get("x-payment-required")
    if not raw:
        die("the 402 carried no PAYMENT-REQUIRED header")
    requirements = json.loads(base64.b64decode(raw))
    accepts = requirements.get("accepts") or []
    if not accepts:
        die("the 402 offered no payment requirements")
    want = accepts[0]
    amount_usdc = int(want["amount"]) / 1_000_000
    print(f"amount   {amount_usdc:.2f} USDC on {want['network']}, pay to {want['payTo']}")

    # 2. Sign an EIP-3009 authorisation with the package's own client
    client = x402Client()
    register_exact_evm_client(client, buyer)
    try:
        payload = asyncio.run(
            client.create_payment_payload(PaymentRequired.model_validate(requirements))
        )
    except Exception as e:  # noqa: BLE001
        die(f"could not sign the payment authorisation: {type(e).__name__}: {e}")
    header = base64.b64encode(
        json.dumps(
            payload.model_dump(by_alias=True, exclude_none=True), separators=(",", ":")
        ).encode()
    ).decode()
    print("signed   EIP-3009 transferWithAuthorization (no gas, no approval)")

    # 3. Re-post with the signature; the facilitator settles
    try:
        second = httpx.post(url, headers={"PAYMENT-SIGNATURE": header}, timeout=180)
    except Exception as e:  # noqa: BLE001
        die(f"the paid request failed: {type(e).__name__}: {e}")
    if second.status_code != 200:
        reason = second.text[:400]
        receipt = second.headers.get("payment-response")
        if receipt:
            try:
                reason = json.dumps(json.loads(base64.b64decode(receipt)))
            except Exception:  # noqa: BLE001
                pass
        die(f"the payment was refused ({second.status_code}): {reason}")

    body = second.json()
    settlement = body.get("settlement") or {}
    tx = settlement.get("transaction")
    if not tx:
        die(f"settled without a transaction hash: {json.dumps(body)[:300]}")
    print(f"PAID     {amount_usdc:.2f} USDC over x402, tx {tx}")
    print(f"https://sepolia.basescan.org/tx/{tx}")
    recorded = body.get("recorded") or {}
    print(f"recorded {recorded.get('recorded')}  payer {settlement.get('payer')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
