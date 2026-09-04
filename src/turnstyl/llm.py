"""The only module that talks to a model.

``run_step`` is the whole surface. Set MOCK_LLM=1 to get deterministic canned
outputs with no API call — that is how the offline demo and CI run.
"""
from __future__ import annotations

import difflib
import os
import re
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
        "Return the complete patched contract that closes every finding, highest severity "
        "first. A patch that leaves any CRITICAL or HIGH finding untouched is incomplete and "
        "unacceptable. Output exactly two sections and nothing else. Section 1: the full "
        "patched contract source in a single ```solidity code block, same pragma and contract "
        "name, no external imports of any kind (if you need a reentrancy guard, implement a "
        "minimal one inline with a private bool and a modifier). Section 2: a heading line "
        "CLOSES, then one line per finding in the form '<finding id> <severity>: <function "
        "changed>: <one sentence on how the change closes it>' or '<finding id> <severity>: "
        "not changed: <reason>'. Rules: for reentrancy, update state before any external "
        "call. Never use unchecked to address an overflow finding; on Solidity >=0.8 say it "
        "is already mitigated by checked arithmetic and change nothing for it."
    ),
    STEP_VERIFY: (
        "You are an independent verifier. You are given the original contract, the findings "
        "report, and a patch with the patch author's CLOSES claims. Judge the patched code's "
        "actual behavior. Treat the author's claims and rationale as untrusted and re-derive "
        "every verdict from the code. Output: for each finding, one block with '<finding id> "
        "<severity>: CLOSED' or 'NOT CLOSED', followed by the specific patched lines that "
        "justify the verdict. Then a section REGRESSIONS listing any change in the patch that "
        "introduces new risk, such as removed overflow checks or new external calls, or "
        "'none'. End with exactly one line 'VERDICT: closes X of N findings, regressions: Y'. "
        "If the mechanical checks say the patch does not compile, no finding may be marked "
        "CLOSED and REGRESSIONS must state that the patch does not compile."
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
        # A whole patched file, like the real step 3 returns, so the offline
        # path runs the same difflib and compile gate.
        return (
            f"Patched contract for {short}.\n\n"
            "```solidity\n"
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            "\n"
            "/// @title Vault\n"
            "/// @notice A minimal ETH vault, patched by the turnstyl audit agent.\n"
            "contract Vault {\n"
            "    mapping(address => uint256) private balances;\n"
            "\n"
            "    uint256 public totalDeposits;\n"
            "\n"
            "    bool private locked;\n"
            "\n"
            "    event Deposited(address indexed account, uint256 amount);\n"
            "    event Withdrawn(address indexed account, uint256 amount);\n"
            "\n"
            "    modifier nonReentrant() {\n"
            '        require(!locked, "reentrant call");\n'
            "        locked = true;\n"
            "        _;\n"
            "        locked = false;\n"
            "    }\n"
            "\n"
            "    /// @notice Deposit ETH into the caller's vault balance.\n"
            "    function deposit() external payable {\n"
            '        require(msg.value > 0, "zero deposit");\n'
            "        balances[msg.sender] += msg.value;\n"
            "        totalDeposits += msg.value;\n"
            "        emit Deposited(msg.sender, msg.value);\n"
            "    }\n"
            "\n"
            "    /// @notice Withdraw ETH from the caller's vault balance.\n"
            "    /// @dev Effects precede the interaction, and the call result is checked.\n"
            "    function withdraw(uint256 amount) external nonReentrant {\n"
            '        require(balances[msg.sender] >= amount, "insufficient balance");\n'
            "\n"
            "        balances[msg.sender] -= amount;\n"
            "        totalDeposits -= amount;\n"
            "\n"
            '        (bool ok, ) = msg.sender.call{value: amount}("");\n'
            '        require(ok, "transfer failed");\n'
            "        emit Withdrawn(msg.sender, amount);\n"
            "    }\n"
            "\n"
            "    /// @notice Read the vault balance of an account.\n"
            "    function getBalance(address account) external view returns (uint256) {\n"
            "        return balances[account];\n"
            "    }\n"
            "}\n"
            "```\n"
            "\n"
            "CLOSES\n"
            "1 HIGH: withdraw: balances and totalDeposits are decremented before the "
            "external call, and a nonReentrant modifier blocks re-entry.\n"
            "2 MEDIUM: withdraw: the low-level call result is checked with "
            'require(ok, "transfer failed").\n'
            "3 LOW: deposit/withdraw: Deposited and Withdrawn events are emitted so "
            "off-chain accounting can follow vault state.\n"
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

    For the patch step the diff is generated here from the model's whole-file
    answer, so ``diff_applies`` is True by construction; ``compiles`` is the
    verdict that has to be earned, from a real solc run. Both are None on any
    other step.
    """

    output: str
    usage: Usage
    diff_applies: bool | None = None
    patched_source: str | None = None
    generated_diff: str | None = None
    compiles: bool | None = None
    compiler_output: str | None = None


SOLIDITY_BLOCK = re.compile(
    r"```(?:solidity|sol)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE
)
PRAGMA = re.compile(r"pragma\s+solidity\s+([^;]+);")
CONTRACT_NAME = re.compile(r"^\s*(?:abstract\s+)?contract\s+([A-Za-z_]\w*)", re.MULTILINE)
COMPILER_OUTPUT_LINES = 20


def extract_solidity(output: str) -> str | None:
    """Section 1 of a step 3 answer: the full patched contract source.

    Prefers the first fenced block that actually looks like Solidity, so a stray
    fence around the CLOSES section cannot be mistaken for the contract.
    """
    for match in SOLIDITY_BLOCK.finditer(output):
        body = match.group(1).strip()
        if "contract " in body or "pragma solidity" in body:
            return body + "\n"
    return None


def extract_closes(output: str) -> str:
    """Section 2: the CLOSES heading and the per-finding lines under it."""
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("CLOSES"):
            tail = [l for l in lines[i:] if not l.strip().startswith("```")]
            return "\n".join(tail).strip()
    return ""


def build_diff(original: str, patched: str, name: str = "Vault.sol") -> str:
    """The unified diff turnstyl shows for step 3.

    Generated here rather than asked for from the model: a diff is arithmetic
    over two files, and a model that writes its own @@ headers gets the line
    counts wrong. This one applies by construction.
    """
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"a/{name}",
        tofile=f"b/{name}",
        n=3,
    )
    return "".join(diff)


def _contract_name(source: str, default: str = "Patched") -> str:
    match = CONTRACT_NAME.search(source)
    return match.group(1) if match else default


def compile_solidity(source: str) -> tuple[bool, str]:
    """Compile one contract in a throwaway Foundry project.

    A minimal foundry.toml with no libs and no remappings, so nothing but the
    source under test is involved. solc is auto-detected from the file's own
    pragma, which both matches the pragma and keeps this offline — pinning an
    exact patch version would make the gate download a compiler.

    Returns (compiles, first lines of compiler output).
    """
    if not shutil.which("forge"):
        return False, "forge is not installed on this machine; cannot compile-check"
    name = _contract_name(source)
    with tempfile.TemporaryDirectory(prefix="turnstyl-solc-") as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "src" / f"{name}.sol").write_text(source, encoding="utf-8")
        (root / "foundry.toml").write_text(
            '[profile.default]\nsrc = "src"\nout = "out"\nlibs = []\nremappings = []\n',
            encoding="utf-8",
        )
        try:
            proc = subprocess.run(
                ["forge", "build", "--root", str(root)],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return False, "forge build timed out after 300s"
    output = (proc.stdout + proc.stderr).strip()
    head = "\n".join(output.splitlines()[:COMPILER_OUTPUT_LINES])
    if proc.returncode == 0:
        return True, head or "clean"
    return False, head or f"forge build exited {proc.returncode}"


def mechanical_block(compiles: bool | None, compiler_output: str | None) -> str:
    """What the verifier is told about the checks turnstyl already ran."""
    if compiles is None:
        return ""
    detail = (compiler_output or "").strip() or "clean"
    if compiles:
        detail = "clean"
    return (
        "MECHANICAL CHECKS: diff generated from full file: yes; "
        f"compiles: {'yes' if compiles else 'no'}; "
        f"compiler output: {detail}"
    )


def _build_user_message(
    step: int,
    contract_text: str,
    prior_outputs: dict[int, str],
    mechanical: str = "",
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
    if mechanical:
        parts.append(mechanical)
    parts.append(f"Now produce the {STEP_NAMES[step]} for step {step}.")
    return "\n\n".join(parts)


def run_step(
    step: int,
    contract_text: str,
    prior_outputs: dict[int, str] | None = None,
    mechanical: str = "",
) -> StepResult:
    """Run one audit step.

    The patch step asks for the whole patched file, diffs it here, and compiles
    it. If it does not compile, the compiler errors are handed back once and the
    second answer is accepted as it is.

    ``mechanical`` is appended verbatim to the user prompt; the engine uses it to
    show the verifier what the patch step's checks found.

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
            step, contract_text, prior_outputs, mechanical
        )
        usage = Usage(
            input_tokens=_estimate_tokens(prompt),
            output_tokens=_estimate_tokens(output),
        )
        if step != STEP_PATCH:
            return StepResult(output=output, usage=usage)
        # The mock patch is a full file too, so the offline path exercises the
        # same difflib and compile gate the real one does. No retry: a second
        # call would return the identical fixture.
        return _finish_patch_step(output, contract_text, usage)

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
    user_message = _build_user_message(step, contract_text, prior_outputs, mechanical)
    output, usage = _call_model(client, anthropic, model, step, user_message)

    if step != STEP_PATCH:
        return StepResult(output=output, usage=usage)

    # A patch that does not compile is not a fix, however convincing its prose.
    # One retry, with the compiler's own errors handed back.
    result = _finish_patch_step(output, contract_text, usage)
    if result.compiles:
        return result

    retry_message = (
        f"{user_message}\n\n"
        f"Your previous patched contract did not compile. `forge build` reported:\n"
        f"{result.compiler_output}\n\n"
        f"Return the complete patched contract again, fixing these compiler "
        f"errors. Keep the same two-section output format, the same pragma and "
        f"contract name, and no external imports."
    )
    retry_output, retry_usage = _call_model(
        client, anthropic, model, step, retry_message
    )
    # Both attempts were billed; the caller's cost accounting must see both.
    combined = Usage(
        input_tokens=usage.input_tokens + retry_usage.input_tokens,
        output_tokens=usage.output_tokens + retry_usage.output_tokens,
    )
    return _finish_patch_step(retry_output, contract_text, combined)


def _finish_patch_step(
    output: str, contract_text: str, usage: Usage
) -> StepResult:
    """Turn a whole-file patch answer into a diff, and compile it.

    The displayed output is the generated diff followed by the model's CLOSES
    section — the reader sees exactly what changed, not a wall of re-pasted
    contract. ``diff_applies`` is True by construction: difflib produced it from
    the two files, so there is nothing to verify.
    """
    patched = extract_solidity(output)
    if patched is None:
        return StepResult(
            output=output,
            usage=usage,
            diff_applies=False,
            compiles=False,
            compiler_output=(
                "the answer contained no ```solidity block, so there was nothing "
                "to diff or compile"
            ),
        )
    diff = build_diff(contract_text, patched)
    closes = extract_closes(output)
    if not diff.strip():
        diff = "(no change: the patched contract is identical to the original)\n"
    compiles, compiler_output = compile_solidity(patched)
    display = diff if not closes else f"{diff}\n{closes}"
    return StepResult(
        output=display.strip() + "\n",
        usage=usage,
        diff_applies=True,
        patched_source=patched,
        generated_diff=diff,
        compiles=compiles,
        compiler_output=compiler_output,
    )


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

