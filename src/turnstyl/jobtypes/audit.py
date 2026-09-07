"""The audit: four steps that scope, find, fix and verify.

turnstyl's first service, and the shape every other job type follows. The four
system prompts here are the ones the agent has been running; they are the
product, and they are not paraphrased anywhere else.
"""
from __future__ import annotations

from ..schema import sha256_text
from .base import GATE_COMPILE, INPUT_SOLIDITY_SOURCE, JobType, StepSpec

SCOPE, FINDINGS, PATCH, VERIFY = 1, 2, 3, 4

SYSTEM_PROMPTS: dict[int, str] = {
    SCOPE: (
        'You are a Solidity security auditor. Scope this contract: list its public and '
        'external entry points, the trust assumptions, and which functions move value. Do not '
        'report vulnerabilities yet. Be brief.'
    ),
    FINDINGS: (
        'You are a Solidity security auditor. Report concrete vulnerabilities in this '
        'contract. For each: a title, the affected function, the severity, and why it is '
        'exploitable. Be specific and brief. No preamble.'
    ),
    PATCH: (
        'You are a Solidity security auditor. You are given a contract and a findings report. '
        'Return the complete patched contract that closes every finding, highest severity '
        'first. A patch that leaves any CRITICAL or HIGH finding untouched is incomplete and '
        'unacceptable. Output exactly two sections and nothing else. Section 1: the full '
        'patched contract source in a single ```solidity code block, same pragma and contract '
        'name, no external imports of any kind (if you need a reentrancy guard, implement a '
        'minimal one inline with a private bool and a modifier). Section 2: a heading line '
        "CLOSES, then one line per finding in the form '<finding id> <severity>: <function "
        "changed>: <one sentence on how the change closes it>' or '<finding id> <severity>: "
        "not changed: <reason>'. Rules: for reentrancy, update state before any external "
        'call. Never use unchecked to address an overflow finding; on Solidity >=0.8 say it '
        'is already mitigated by checked arithmetic and change nothing for it.'
    ),
    VERIFY: (
        'You are an independent verifier. You are given the original contract, the findings '
        "report, and a patch with the patch author's CLOSES claims. Judge the patched code's "
        "actual behavior. Treat the author's claims and rationale as untrusted and re-derive "
        "every verdict from the code. Output: for each finding, one block with '<finding id> "
        "<severity>: CLOSED' or 'NOT CLOSED', followed by the specific patched lines that "
        'justify the verdict. Then a section headed REGRESSIONS containing either the single '
        "word none, or a list of one-line items each starting with '- ', describing a change "
        'in the patch that introduces new risk. No other prose in that section. End with '
        "exactly one line 'VERDICT: closes X of N findings, regressions: Y'. If the "
        'mechanical checks say the patch does not compile, no finding may be marked CLOSED '
        'and REGRESSIONS must state that the patch does not compile.'
    ),
}


def mock_output(step: int, contract_text: str, prior_outputs: dict[int, str]) -> str:
    """Deterministic, offline, and specific enough to be worth reading."""
    short = sha256_text(contract_text)[:8]
    if step == SCOPE:
        return (
            f"SCOPE (contract {short})\n"
            f"Entry points: deposit() payable, withdraw(uint256), getBalance(address) view.\n"
            f"Value-moving functions: deposit(), withdraw().\n"
            f"Trust assumptions: no owner, no pause, no upgrade path; any address "
            f"may deposit and withdraw its own balance.\n"
            f"Out of scope: compiler version, gas optimisation, deployment scripts."
        )
    if step == FINDINGS:
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
    if step == PATCH:
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
    if step == VERIFY:
        saw_findings = FINDINGS in prior_outputs
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


AUDIT = JobType(
    id="audit",
    name="Security audit",
    description="A four-step Solidity security audit: scope, findings, patch, verify.",
    input_kind=INPUT_SOLIDITY_SOURCE,
    steps=(
        StepSpec(SCOPE, "scope", 0.00, SYSTEM_PROMPTS[SCOPE]),
        StepSpec(FINDINGS, "findings", 0.50, SYSTEM_PROMPTS[FINDINGS]),
        StepSpec(
            PATCH, "patch", 0.75, SYSTEM_PROMPTS[PATCH],
            gate=GATE_COMPILE, max_tokens=3000,     # returns a whole contract
        ),
        StepSpec(VERIFY, "verify", 0.25, SYSTEM_PROMPTS[VERIFY]),
    ),
    mock=mock_output,
)
