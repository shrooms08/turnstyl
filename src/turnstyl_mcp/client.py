"""An HTTP client for one turnstyl instance, with a wallet.

This package is a *client*. It never opens the agent's memory file, never
imports the agent, and knows nothing about Sibyl Memory: everything it does
goes over the same HTTP API a browser uses, with the same wallet signatures.

Two environment variables:

* ``TURNSTYL_API`` - the operator's API origin. Defaults to
  ``http://127.0.0.1:8787``, which is what ``turnstyl serve`` binds. It is the
  API origin, not the GitHub Pages URL: the page is static and answers no API
  calls.
* ``BUYER_PRIVATE_KEY`` - the wallet that pays. Read once, at import, held in a
  module-private, and never logged, returned, or put in an error message. If it
  is unset the paying tool is not registered at all and every read-only tool
  still works.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Only the .env in the working directory the harness started us in, and only
# for variables the environment did not already set. Deliberately not
# dotenv's default search, which walks up from this file and would find the
# .env of whatever checkout this package happens to be installed from.
load_dotenv(Path.cwd() / ".env", override=False)

DEFAULT_API = "http://127.0.0.1:8787"
TIMEOUT = 60.0
SETTLE_TIMEOUT = 180.0


class TurnstylError(Exception):
    """Something a caller can act on, in one sentence. Never a traceback."""


def api_base() -> str:
    """The turnstyl API origin, without a trailing slash."""
    raw = (os.environ.get("TURNSTYL_API") or DEFAULT_API).strip().rstrip("/")
    if "github.io" in raw:
        raise TurnstylError(
            f"TURNSTYL_API is set to {raw}, which is the published page, not the "
            f"API. The page is static and answers no API calls. Set TURNSTYL_API "
            f"to the operator's API origin (the tunnel URL, or "
            f"{DEFAULT_API} when the agent runs on this machine)."
        )
    return raw or DEFAULT_API


# Read once. `_KEY` never leaves this module, and nothing below ever formats it
# into a message; the address it derives is public and is used freely.
_KEY = (os.environ.get("BUYER_PRIVATE_KEY") or "").strip()


def have_key() -> bool:
    return bool(_KEY)


def _account():
    from eth_account import Account

    if not _KEY:
        raise TurnstylError(
            "BUYER_PRIVATE_KEY is not set, so this server has no wallet to pay "
            "or sign in with. Set it in the environment and restart the MCP "
            "server. Read-only tools work without it."
        )
    try:
        return Account.from_key(_KEY)
    except Exception as exc:  # noqa: BLE001 - never echo the key back
        raise TurnstylError(
            f"BUYER_PRIVATE_KEY is not a usable private key ({type(exc).__name__}). "
            f"It must be 32 bytes of hex, with or without a 0x prefix."
        ) from None


def buyer_address() -> str | None:
    """The wallet's address, lowercase. Public; the key it came from is not."""
    if not _KEY:
        return None
    try:
        return _account().address.lower()
    except TurnstylError:
        return None


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------
HEADERS = {"accept": "application/json", "ngrok-skip-browser-warning": "true"}

_session_token: str | None = None
_session_address: str | None = None
_session_lock = asyncio.Lock()


def _unreachable(exc: Exception) -> TurnstylError:
    return TurnstylError(
        f"the turnstyl agent at {api_base()} is not answering "
        f"({type(exc).__name__}). It runs on the operator's machine: it may be "
        f"offline, or TURNSTYL_API may point somewhere else."
    )


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return (response.text or "")[:300]
    if isinstance(body, dict) and body.get("detail"):
        detail = body["detail"]
        return detail if isinstance(detail, str) else json.dumps(detail)[:300]
    return json.dumps(body)[:300]


async def request(
    method: str,
    path: str,
    *,
    auth: bool = False,
    json_body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float = TIMEOUT,
) -> httpx.Response:
    """One HTTP call. Raises TurnstylError with a sentence, never a traceback."""
    headers = dict(HEADERS)
    if auth:
        headers["Authorization"] = f"Bearer {await session_token()}"
    if extra_headers:
        headers.update(extra_headers)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(
                method, f"{api_base()}{path}", headers=headers, json=json_body
            )
    except httpx.HTTPError as exc:
        raise _unreachable(exc) from None


async def get_json(path: str, *, auth: bool = False) -> dict[str, Any]:
    response = await request("GET", path, auth=auth)
    if response.status_code == 401:
        raise TurnstylError(
            f"the agent refused the request to {path} as unauthenticated: "
            f"{_detail(response)}"
        )
    if response.status_code == 403:
        raise TurnstylError(
            f"this wallet may not read {path}: {_detail(response)}"
        )
    if response.status_code == 404:
        raise TurnstylError(f"nothing at {path}: {_detail(response)}")
    if response.status_code != 200:
        raise TurnstylError(
            f"the agent answered {response.status_code} for {path}: {_detail(response)}"
        )
    return response.json()


# ----------------------------------------------------------------------
# Signing in: one signature over a message the agent issues
# ----------------------------------------------------------------------
async def session_token() -> str:
    """A bearer token for this wallet, obtained once and reused.

    The agent builds the message and returns it in full, so the bytes signed
    here are the bytes it verifies. Nothing is spent and no transaction is sent.
    """
    global _session_token, _session_address
    address = buyer_address()
    if not address:
        raise TurnstylError(
            "signing in needs a wallet: set BUYER_PRIVATE_KEY in the environment "
            "and restart the MCP server."
        )
    async with _session_lock:
        if _session_token and _session_address == address:
            return _session_token

        from eth_account.messages import encode_defunct

        response = await request("GET", f"/api/auth/nonce?address={address}")
        if response.status_code != 200:
            raise TurnstylError(
                f"the agent would not issue a login nonce "
                f"({response.status_code}): {_detail(response)}"
            )
        nonce = response.json()
        signature = _account().sign_message(
            encode_defunct(text=nonce["message"])
        ).signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature

        verified = await request(
            "POST",
            "/api/auth/verify",
            json_body={
                "address": address,
                "signature": signature,
                "nonce": nonce["nonce"],
            },
        )
        if verified.status_code != 200:
            raise TurnstylError(
                f"the agent rejected the login signature "
                f"({verified.status_code}): {_detail(verified)}"
            )
        _session_token = verified.json()["token"]
        _session_address = address
        return _session_token


def forget_session() -> None:
    """Drop the cached token, so the next call signs in again."""
    global _session_token, _session_address
    _session_token, _session_address = None, None


async def with_session(fn):
    """Run an authenticated call, signing in again once if the token lapsed."""
    try:
        return await fn()
    except TurnstylError as first:
        if "unauthenticated" not in str(first):
            raise
        forget_session()
        return await fn()


# ----------------------------------------------------------------------
# Paying: EIP-3009 over x402, gasless, or the fake backend's simulate path
# ----------------------------------------------------------------------
async def pay_x402(path: str) -> dict[str, Any]:
    """Settle one invoice over x402 at ``path``. Returns the settlement.

    The buyer signs a USDC transferWithAuthorization; a facilitator submits it
    and pays the gas. Exactly the flow scripts/buyer_pay_x402.py runs, and the
    same wire formats docs/X402.md records: the requirements arrive in the
    PAYMENT-REQUIRED header and the signed payload goes back in
    PAYMENT-SIGNATURE.
    """
    first = await request("POST", path, auth=True, timeout=TIMEOUT)
    if first.status_code == 404:
        raise TurnstylError(
            f"this agent cannot take an x402 payment right now: {_detail(first)}"
        )
    if first.status_code in (401, 403):
        raise TurnstylError(f"the agent refused the payment: {_detail(first)}")
    if first.status_code != 402:
        raise TurnstylError(
            f"expected a 402 with payment requirements, got {first.status_code}: "
            f"{_detail(first)}"
        )

    raw = first.headers.get("payment-required")
    requirements = None
    if raw:
        try:
            requirements = json.loads(base64.b64decode(raw))
        except Exception:  # noqa: BLE001
            requirements = None
    if requirements is None:
        try:
            body = first.json()
            requirements = body if body.get("accepts") else None
        except Exception:  # noqa: BLE001
            requirements = None
    if not requirements or not requirements.get("accepts"):
        raise TurnstylError("the 402 carried no payment requirements to satisfy")

    want = requirements["accepts"][0]
    amount_usdc = int(want["amount"]) / 1_000_000

    from x402 import x402Client
    from x402.mechanisms.evm.exact.register import register_exact_evm_client
    from x402.schemas import PaymentRequired

    client = x402Client()
    register_exact_evm_client(client, _account())
    try:
        payload = await client.create_payment_payload(
            PaymentRequired.model_validate(requirements)
        )
    except Exception as exc:  # noqa: BLE001 - never echo the key
        raise TurnstylError(
            f"could not sign the payment authorisation ({type(exc).__name__}). "
            f"The wallet did not spend anything."
        ) from None

    header = base64.b64encode(
        json.dumps(
            payload.model_dump(by_alias=True, exclude_none=True), separators=(",", ":")
        ).encode()
    ).decode()

    settled = await request(
        "POST",
        path,
        auth=True,
        extra_headers={"PAYMENT-SIGNATURE": header},
        timeout=SETTLE_TIMEOUT,
    )
    if settled.status_code != 200:
        reason = _detail(settled)
        receipt = settled.headers.get("payment-response")
        if receipt:
            try:
                decoded = json.loads(base64.b64decode(receipt))
                reason = (
                    decoded.get("errorMessage")
                    or decoded.get("error_message")
                    or decoded.get("errorReason")
                    or decoded.get("error_reason")
                    or reason
                )
            except Exception:  # noqa: BLE001
                pass
        if "insufficient" in str(reason).lower():
            raise TurnstylError(
                f"the payment was rejected for insufficient USDC: {reason}. "
                f"Fund {buyer_address()} with test USDC on Base Sepolia "
                f"(https://faucet.circle.com) and try again."
            )
        raise TurnstylError(
            f"the payment was rejected ({settled.status_code}): {reason}. "
            f"Nothing was settled."
        )

    body = settled.json()
    settlement = body.get("settlement") or {}
    if not settlement.get("transaction"):
        raise TurnstylError(
            "the agent reported success but no settlement transaction; nothing "
            "can be shown as proof of payment"
        )
    return {
        "method": "x402",
        "amount_usdc": amount_usdc,
        "tx": settlement["transaction"],
        "payer": settlement.get("payer"),
        "recorded": bool((body.get("recorded") or {}).get("recorded")),
        "job": body.get("job"),
    }


async def simulate_pay(job_id: str) -> dict[str, Any]:
    """Mark an invoice paid on the fake backend. No wallet, no chain, no money."""
    response = await request(
        "POST", f"/api/jobs/{job_id}/pay", auth=True, timeout=TIMEOUT
    )
    if response.status_code != 200:
        raise TurnstylError(
            f"the agent would not settle that invoice ({response.status_code}): "
            f"{_detail(response)}"
        )
    body = response.json()
    return {
        "method": "simulated",
        "amount_usdc": None,
        "tx": body.get("tx_hash"),
        "payer": None,
        "recorded": True,
        "job": body,
    }
