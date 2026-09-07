"""Who is asking, and what they are allowed to see.

Three identities, and nothing in between:

* **public** — no credential. Sees that a job exists and how far it got, never
  what it says. Job rows, a job's shape without its outputs, the journal
  reduced to one sentence per decision, and a buyer's trust tier.
* **buyer** — proved control of an address by signing a login message with it.
  Sees everything about their own jobs, and nothing more about anyone else's.
* **operator** — holds ``OPERATOR_TOKEN`` from ``.env``. Sees everything.

A buyer session is a wallet signature over a fixed message, exchanged for a
bearer token. Both the nonce store and the session store are in-process: a
restart signs everyone out, which is the right default for an agent whose
whole state is one file the operator can delete.
"""
from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct

PROJECT_ROOT = Path(__file__).resolve().parents[2]

NONCE_TTL_SECONDS = 300         # 5 minutes to sign
SESSION_TTL_SECONDS = 24 * 3600  # 24 hours signed in

KIND_PUBLIC = "public"
KIND_BUYER = "buyer"
KIND_OPERATOR = "operator"


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise(address: str | None) -> str:
    return (address or "").strip().lower()


def trunc_address(address: str | None) -> str | None:
    """``0x1234…abcd``: enough to recognise your own wallet, not enough to be a
    directory of everyone who has ever paid this agent."""
    a = (address or "").strip()
    if len(a) <= 12:
        return a or None
    return a[:6] + "…" + a[-4:]


# ----------------------------------------------------------------------
# The message a buyer signs
# ----------------------------------------------------------------------
def login_message(address: str, nonce: str, issued: str) -> str:
    """The exact bytes the wallet is asked to sign.

    Built here and nowhere else: the browser signs what ``GET /api/auth/nonce``
    hands it, and ``POST /api/auth/verify`` rebuilds the same string from the
    nonce it issued, so a client cannot sign one thing and present another.
    """
    return (
        "turnstyl login\n"
        "\n"
        f"address: {normalise(address)}\n"
        f"nonce: {nonce}\n"
        f"issued: {issued}"
    )


# ----------------------------------------------------------------------
# Nonces: one-time, five minutes, in process
# ----------------------------------------------------------------------
_nonces: dict[str, dict[str, object]] = {}
_nonce_lock = threading.Lock()


def _sweep_nonces(now: float) -> None:
    for value in [k for k, v in _nonces.items() if float(v["expires"]) <= now]:
        _nonces.pop(value, None)


def issue_nonce(address: str) -> dict[str, str]:
    """A fresh nonce for this address, with the message to sign."""
    addr = normalise(address)
    nonce = secrets.token_hex(16)
    issued = iso_now()
    now = _now()
    with _nonce_lock:
        _sweep_nonces(now)
        _nonces[nonce] = {
            "address": addr,
            "issued": issued,
            "expires": now + NONCE_TTL_SECONDS,
        }
    return {
        "address": addr,
        "nonce": nonce,
        "issued": issued,
        "message": login_message(addr, nonce, issued),
        "expires_in_seconds": str(NONCE_TTL_SECONDS),
    }


def _candidate_nonces(address: str) -> list[tuple[str, str]]:
    """Every unexpired (nonce, issued) this address was given, newest last.

    A buyer may have two tabs open; both asked for a nonce, only one is being
    signed. Verify tries each rather than making the second tab fail.
    """
    addr = normalise(address)
    now = _now()
    with _nonce_lock:
        _sweep_nonces(now)
        return [
            (value, str(v["issued"]))
            for value, v in _nonces.items()
            if v["address"] == addr
        ]


def consume_nonce(nonce: str) -> None:
    with _nonce_lock:
        _nonces.pop(nonce, None)


# ----------------------------------------------------------------------
# Sessions: a bearer token bound to one address
# ----------------------------------------------------------------------
_sessions: dict[str, dict[str, object]] = {}
_session_lock = threading.Lock()


def _sweep_sessions(now: float) -> None:
    for token in [k for k, v in _sessions.items() if float(v["expires"]) <= now]:
        _sessions.pop(token, None)


def open_session(address: str) -> dict[str, object]:
    addr = normalise(address)
    token = secrets.token_urlsafe(32)
    now = _now()
    expires = now + SESSION_TTL_SECONDS
    with _session_lock:
        _sweep_sessions(now)
        _sessions[token] = {"address": addr, "expires": expires}
    return {
        "token": token,
        "address": addr,
        "expires_at": datetime.fromtimestamp(expires, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "expires_in_seconds": SESSION_TTL_SECONDS,
    }


def session_address(token: str) -> str | None:
    now = _now()
    with _session_lock:
        _sweep_sessions(now)
        row = _sessions.get(token)
        return str(row["address"]) if row else None


def verify_login(address: str, signature: str, nonce: str | None = None) -> dict[str, object]:
    """Check the signature against a nonce this server issued to this address.

    Returns the session on success. Raises ``ValueError`` with a sentence an
    operator can act on: the caller turns it into a 400.
    """
    addr = normalise(address)
    if not addr.startswith("0x") or len(addr) != 42:
        raise ValueError("address must be 0x followed by 40 hex characters")
    sig = (signature or "").strip()
    if not sig:
        raise ValueError("signature is required")

    candidates = _candidate_nonces(addr)
    if nonce:
        candidates = [c for c in candidates if c[0] == nonce]
    if not candidates:
        raise ValueError(
            f"no unexpired login nonce for {addr}; "
            f"call GET /api/auth/nonce?address={addr} and sign the message it returns"
        )

    for value, issued in candidates:
        message = login_message(addr, value, issued)
        try:
            recovered = Account.recover_message(encode_defunct(text=message), signature=sig)
        except Exception:  # noqa: BLE001 - a malformed signature is a 400, not a crash
            continue
        if normalise(recovered) == addr:
            consume_nonce(value)
            return open_session(addr)

    raise ValueError(
        f"the signature does not recover to {addr}; it was signed by another "
        f"wallet, or the message was not the one this server issued"
    )


# ----------------------------------------------------------------------
# The operator token
# ----------------------------------------------------------------------
def _write_operator_token(token: str) -> str | None:
    """Append the token to .env. Returns a problem sentence, or None.

    Never printed, never returned over HTTP, never logged. The operator reads
    it out of their own .env and pastes it into the app's settings drawer.
    """
    env = PROJECT_ROOT / ".env"
    try:
        existing = env.read_text(encoding="utf-8") if env.is_file() else ""
        lead = "" if (not existing or existing.endswith("\n")) else "\n"
        with env.open("a", encoding="utf-8") as fh:
            fh.write(f"{lead}OPERATOR_TOKEN={token}\n")
    except OSError as e:
        return (
            f"could not write OPERATOR_TOKEN to {env}: {type(e).__name__}: {e}. "
            f"This process generated one, but it will not survive a restart; "
            f"add an OPERATOR_TOKEN line to .env yourself."
        )
    return None


_operator_note: str | None = None


def operator_token() -> str:
    """The operator's bearer token, generated into .env on first use."""
    global _operator_note
    token = (os.environ.get("OPERATOR_TOKEN") or "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(32)
    os.environ["OPERATOR_TOKEN"] = token
    _operator_note = _write_operator_token(token)
    return token


def operator_problem() -> str | None:
    """A sentence about the operator token that the operator should see, or
    None. Never contains the token itself."""
    return _operator_note


# ----------------------------------------------------------------------
# Identity of one request
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Identity:
    kind: str
    address: str | None = None

    @property
    def is_operator(self) -> bool:
        return self.kind == KIND_OPERATOR

    @property
    def signed_in(self) -> bool:
        return self.kind in (KIND_BUYER, KIND_OPERATOR)

    def is_buyer(self, address: str | None) -> bool:
        return self.kind == KIND_BUYER and self.address == normalise(address)

    def sees(self, buyer_address: str | None) -> bool:
        """True when this caller may read the contents of that buyer's work."""
        return self.is_operator or self.is_buyer(buyer_address)

    def to_public(self) -> dict[str, object]:
        return {"kind": self.kind, "address": self.address}


PUBLIC = Identity(KIND_PUBLIC)


def bearer(header: str | None) -> str | None:
    value = (header or "").strip()
    if not value:
        return None
    parts = value.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def identify(authorization: str | None) -> Identity:
    """Resolve one request's Authorization header.

    An unknown or expired token reads as public rather than as an error: a
    session that lapsed while a page was open should quietly show less, not
    break browsing. Endpoints that need a session say so themselves.
    """
    token = bearer(authorization)
    if not token:
        return PUBLIC
    if secrets.compare_digest(token, operator_token()):
        return Identity(KIND_OPERATOR)
    addr = session_address(token)
    if addr:
        return Identity(KIND_BUYER, addr)
    return PUBLIC
