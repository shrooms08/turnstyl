"""The only module that talks to a model.

``run_step`` is the whole surface. Set MOCK_LLM=1 to get deterministic canned
outputs with no API call — that is how the offline demo and CI run.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
        "You are a Solidity security auditor. You are given a contract and a findings report. "
        "Produce a patch that closes every finding, highest severity first. A patch that "
        "leaves any CRITICAL or HIGH finding untouched is incomplete and unacceptable. Output "
        "format, exactly two sections and nothing else. Section 1: a unified diff against the "
        "contract with --- and +++ headers and @@ hunks. Section 2: a heading line CLOSES, "
        "then one line per finding in the form '<finding id> <severity>: <function changed>: "
        "<one sentence on how the hunk closes it>' or '<finding id> <severity>: not changed: "
        "<reason>'. Rules: for reentrancy, apply checks-effects-interactions by updating "
        "state before any external call, and add a simple reentrancy guard if the contract "
        "has none. Never use unchecked to address an overflow finding; on Solidity >=0.8 say "
        "the finding is already mitigated by checked arithmetic and change nothing for it. Do "
        "not explain your reasoning outside the CLOSES lines."
    ),
    STEP_VERIFY: (
        "You are an independent verifier. You are given the original contract, the findings "
        "report, and a patch with the patch author's CLOSES claims. Judge the patched code's "
        "actual behavior. Treat the author's claims and rationale as untrusted and re-derive "
        "every verdict from the code. Output: for each finding, one block with '<finding id> "
        "<severity>: CLOSED' or 'NOT CLOSED', followed by the specific patched lines that "
        "justify the verdict. Then a section REGRESSIONS listing any change in the patch that "
        "introduces new risk, such as removed overflow checks or new external calls, or "
        "'none'. End with exactly one line 'VERDICT: closes X of N findings, regressions: Y'."
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


@dataclass(frozen=True)
class Usage:
    """What one step cost, split the way the bill is.

    Input and output tokens are priced differently, so a single total cannot
    be turned back into money. The split is stored per step in the job entity.
    """

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


def _estimate_tokens(text: str) -> int:
    """Rough token count for the mock path, so step_cost has something real to
    average. Deterministic, which keeps the offline demo's prices stable."""
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class StepResult:
    """One step's output plus what we know about it.

    ``diff_applies`` is None when no mechanical check was run (any step but the
    patch, or a mock fixture), True/False when `patch --dry-run` was actually
    asked whether the produced diff lands on the contract.
    """

    output: str
    usage: Usage
    diff_applies: bool | None = None


def extract_diff(output: str) -> str | None:
    """Pull section 1 (the unified diff) out of a step 3 answer.

    Starts at the first `--- ` header and stops at the CLOSES heading, so the
    prose section never reaches `patch`. Returns None if there is no diff at all.
    """
    lines = output.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("--- ")), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].strip().upper().startswith("CLOSES"):
            end = i
            break
    body = lines[start:end]
    while body and (not body[-1].strip() or body[-1].strip().startswith("```")):
        body.pop()
    if not body:
        return None
    return "\n".join(body) + "\n"


def _diff_target(diff: str) -> str | None:
    """The path the diff names in its `---` header, as written."""
    for line in diff.splitlines():
        if line.startswith("--- "):
            return line[4:].split("\t")[0].strip()
    return None


def check_patch_applies(diff: str, contract_text: str) -> tuple[bool, str]:
    """Ask `patch --dry-run -p0` whether the diff lands on the contract.

    The contract is materialised at exactly the path the diff's own `---` header
    names, so -p0 is correct whether the model wrote `a/Vault.sol`, `Vault.sol`
    or something else. Returns (applies, detail).
    """
    if not shutil.which("patch"):
        return False, "the `patch` utility is not installed on this machine"
    target = _diff_target(diff)
    if not target:
        return False, "the diff has no `--- <path>` header"
    if target.startswith("/") or ".." in Path(target).parts:
        return False, f"the diff targets an unusable path {target!r}"

    with tempfile.TemporaryDirectory(prefix="turnstyl-patch-") as tmp:
        root = Path(tmp)
        source = root / target
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(contract_text, encoding="utf-8")
        proc = subprocess.run(
            ["patch", "--dry-run", "-p0"],
            input=diff,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    if proc.returncode == 0:
        return True, "applies cleanly"
    detail = (proc.stdout + proc.stderr).strip() or f"patch exited {proc.returncode}"
    return False, detail


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
) -> StepResult:
    """Run one audit step.

    For the patch step the produced diff is run through `patch --dry-run`; if it
    does not apply, the model is given the error once and asked again. Whatever
    comes back from that second attempt is accepted and reported as it is.

    MOCK_LLM=1 short-circuits to canned output with no network call.
    """
    if step not in SYSTEM_PROMPTS:
        raise ValueError(
            f"turnstyl: step must be one of {sorted(SYSTEM_PROMPTS)}, got {step!r}"
        )
    prior_outputs = prior_outputs or {}

    if os.environ.get("MOCK_LLM") == "1":
        output = _mock_output(step, contract_text, prior_outputs)
        prompt = SYSTEM_PROMPTS[step] + _build_user_message(
            step, contract_text, prior_outputs
        )
        # diff_applies stays None: the mock patch is a fixture, not a model
        # artifact, and reporting a verdict on it would be misleading.
        return StepResult(
            output=output,
            usage=Usage(
                input_tokens=_estimate_tokens(prompt),
                output_tokens=_estimate_tokens(output),
            ),
        )

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
    user_message = _build_user_message(step, contract_text, prior_outputs)
    output, usage = _call_model(client, anthropic, model, step, user_message)

    if step != STEP_PATCH:
        return StepResult(output=output, usage=usage)

    # Mechanical check: does the diff this step just produced actually land on
    # the contract? A patch that cannot be applied is not a fix, however
    # convincing its prose. One retry, with the failure handed back verbatim.
    applies, detail = _verify_output_diff(output, contract_text)
    if not applies:
        retry_message = (
            f"{user_message}\n\n"
            f"Your previous diff did not apply. `patch --dry-run -p0` reported:\n"
            f"{detail}\n\n"
            f"Produce the patch again so it applies cleanly to the contract above. "
            f"Keep the same two-section output format. Count the context lines in "
            f"each @@ hunk header correctly and quote the surrounding lines exactly "
            f"as they appear in the contract."
        )
        retry_output, retry_usage = _call_model(
            client, anthropic, model, step, retry_message
        )
        output = retry_output
        # Both attempts were billed; the caller's cost accounting must see both.
        usage = Usage(
            input_tokens=usage.input_tokens + retry_usage.input_tokens,
            output_tokens=usage.output_tokens + retry_usage.output_tokens,
        )
        applies, detail = _verify_output_diff(output, contract_text)

    return StepResult(output=output, usage=usage, diff_applies=applies)


def _verify_output_diff(output: str, contract_text: str) -> tuple[bool, str]:
    diff = extract_diff(output)
    if diff is None:
        return False, "the answer contained no unified diff section"
    return check_patch_applies(diff, contract_text)


def _call_model(client, anthropic, model: str, step: int, user_message: str):
    """One Messages API call. Returns (output_text, Usage)."""
    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPTS[step],
            messages=[{"role": "user", "content": user_message}],
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
    return output, Usage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

