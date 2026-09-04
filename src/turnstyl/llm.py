"""The only module that talks to a model.

``run_step`` is the whole surface. Set MOCK_LLM=1 to get deterministic canned
outputs with no API call — that is how the offline demo and CI run.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from .schema import (
    STEP_FINDINGS,
    STEP_NAMES,
    STEP_PATCH,
    STEP_SCOPE,
    STEP_VERIFY,
    sha256_text,
)

# Loaded once, at import. load_dotenv never overwrites an already-set variable,
# so an explicit `MOCK_LLM=1 turnstyl ...` always wins over the file.
load_dotenv()

DEFAULT_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1200
MAX_CONTRACT_CHARS = 12000

SYSTEM_PROMPTS: dict[int, str] = {
    STEP_SCOPE: (
        "You are a Solidity security auditor. Scope this contract: list its "
        "public and external entry points, the trust assumptions, and which "
        "functions move value. Do not report vulnerabilities yet. Be brief."
    ),
    STEP_FINDINGS: (
        "You are a Solidity security auditor. Report concrete vulnerabilities in "
        "this contract. For each: a title, the affected function, the severity, "
        "and why it is exploitable. Be specific and brief. No preamble."
    ),
    STEP_PATCH: (
        "You are a Solidity security auditor. Produce a fix for the reported "
        "findings as a unified diff against the contract, and nothing else. "
        "Use standard unified diff syntax with --- / +++ headers and @@ hunks."
    ),
    STEP_VERIFY: (
        "You are a Solidity security auditor. Verify the proposed patch against "
        "the reported findings. State, per finding, whether the patch closes it, "
        "and name any regression the patch introduces. Be brief."
    ),
}


def _mock_output(step: int, contract_text: str, prior_outputs: dict[int, str]) -> str:
    """Deterministic, offline, and specific enough to be worth reading."""
    short = sha256_text(contract_text)[:8]
    if step == STEP_SCOPE:
        return (
            f"SCOPE (contract {short})\n"
            f"Entry points: deposit() payable, withdraw(uint256), getBalance(address) view.\n"
            f"Value-moving functions: deposit(), withdraw().\n"
            f"Trust assumptions: no owner, no pause, no upgrade path; any address "
            f"may deposit and withdraw its own balance.\n"
            f"Out of scope: compiler version, gas optimisation, deployment scripts."
        )
    if step == STEP_FINDINGS:
        return (
            f"FINDINGS (contract {short})\n"
            f"1. Reentrancy in withdraw() - HIGH. withdraw() sends ETH with a raw "
            f"call to msg.sender BEFORE it reduces balances[msg.sender]. A "
            f"contract caller re-enters withdraw() from its receive() hook while "
            f"its recorded balance is still the pre-withdrawal amount and drains "
            f"the vault. Violates checks-effects-interactions.\n"
            f"2. Unchecked low-level call return value in withdraw() - MEDIUM. A "
            f"failed transfer does not revert, so the balance change is lost.\n"
            f"3. No event emitted on deposit/withdraw - LOW. Off-chain accounting "
            f"cannot follow vault state."
        )
    if step == STEP_PATCH:
        return (
            f"PATCH (contract {short})\n"
            f"--- a/Vault.sol\n"
            f"+++ b/Vault.sol\n"
            f"@@ function withdraw(uint256 amount) @@\n"
            f"     require(balances[msg.sender] >= amount, \"insufficient\");\n"
            f"-    (bool ok, ) = msg.sender.call{{value: amount}}(\"\");\n"
            f"-    balances[msg.sender] -= amount;\n"
            f"+    balances[msg.sender] -= amount;\n"
            f"+    (bool ok, ) = msg.sender.call{{value: amount}}(\"\");\n"
            f"+    require(ok, \"transfer failed\");\n"
            f"+    emit Withdrawn(msg.sender, amount);\n"
            f"Effects now precede the interaction, and the call result is checked."
        )
    if step == STEP_VERIFY:
        saw_findings = STEP_FINDINGS in prior_outputs
        return (
            f"VERIFY (contract {short})\n"
            f"Finding 1 (reentrancy): CLOSED. The balance decrement now happens "
            f"before the external call, so a re-entrant withdraw() sees the "
            f"reduced balance and fails the require.\n"
            f"Finding 2 (unchecked call): CLOSED. require(ok) reverts the whole "
            f"withdrawal on a failed transfer.\n"
            f"Finding 3 (no events): CLOSED for withdraw via Withdrawn; deposit "
            f"still emits nothing.\n"
            f"No regression found. Patch checked against "
            f"{'the recorded findings output' if saw_findings else 'the contract only'}."
        )
    raise ValueError(f"turnstyl: no mock output defined for step {step!r}")


def _estimate_tokens(text: str) -> int:
    """Rough token count for the mock path, so step_cost has something real to
    average. Deterministic, which keeps the offline demo's prices stable."""
    return max(1, len(text) // 4)


def _build_user_message(
    step: int, contract_text: str, prior_outputs: dict[int, str]
) -> str:
    """The contract, plus whatever earlier steps produced."""
    truncated = contract_text[:MAX_CONTRACT_CHARS]
    parts = [f"CONTRACT:\n{truncated}"]
    if len(contract_text) > MAX_CONTRACT_CHARS:
        # Loud, in-band, and visible to the model: never silently truncate.
        parts.append(
            f"[turnstyl: contract truncated to the first {MAX_CONTRACT_CHARS} of "
            f"{len(contract_text)} characters for this step]"
        )
    for prior_step in sorted(prior_outputs):
        if prior_step >= step:
            continue
        parts.append(
            f"{STEP_NAMES[prior_step].upper()} (from step {prior_step}):\n"
            f"{prior_outputs[prior_step]}"
        )
    parts.append(f"Now produce the {STEP_NAMES[step]} for step {step}.")
    return "\n\n".join(parts)


def run_step(
    step: int,
    contract_text: str,
    prior_outputs: dict[int, str] | None = None,
) -> tuple[str, int]:
    """Run one audit step. Returns (output_text, tokens_used).

    MOCK_LLM=1 short-circuits to canned output with no network call.
    """
    if step not in SYSTEM_PROMPTS:
        raise ValueError(
            f"turnstyl: step must be one of {sorted(SYSTEM_PROMPTS)}, got {step!r}"
        )
    prior_outputs = prior_outputs or {}

    if os.environ.get("MOCK_LLM") == "1":
        output = _mock_output(step, contract_text, prior_outputs)
        return output, _estimate_tokens(output)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "turnstyl: ANTHROPIC_API_KEY is not set and MOCK_LLM is not 1.\n"
            "  Fix one of these:\n"
            "    - add ANTHROPIC_API_KEY=... to the .env file in the repo root, or\n"
            "    - set MOCK_LLM=1 to run the agent offline with canned step outputs."
        )

    import anthropic

    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPTS[step],
            messages=[
                {
                    "role": "user",
                    "content": _build_user_message(step, contract_text, prior_outputs),
                }
            ],
        )
    except anthropic.APIStatusError as e:
        raise RuntimeError(
            f"turnstyl: the Anthropic API rejected the step {step} request "
            f"(model={model!r}, HTTP {e.status_code}): {e.message}\n"
            f"  Set MOCK_LLM=1 to run offline, or check LLM_MODEL / ANTHROPIC_API_KEY."
        ) from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError(
            f"turnstyl: could not reach the Anthropic API for step {step} "
            f"(model={model!r}): {e}\n"
            f"  Set MOCK_LLM=1 to run offline."
        ) from e

    if response.stop_reason == "refusal":
        raise RuntimeError(
            f"turnstyl: the model declined step {step} "
            f"({STEP_NAMES[step]}); stop_details={response.stop_details}"
        )
    output = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not output:
        raise RuntimeError(
            f"turnstyl: step {step} ({STEP_NAMES[step]}) returned no text content "
            f"(stop_reason={response.stop_reason!r}, model={model!r})."
        )
    tokens = response.usage.input_tokens + response.usage.output_tokens
    return output, tokens
