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

from .jobtypes import GATE_COMPILE, GATE_FORGE_TEST, GATE_NONE, JobType
from .schema import sha256_text

# Loaded once, at import. load_dotenv never overwrites an already-set variable,
# so an explicit `MOCK_LLM=1 turnstyl ...` always wins over the file.
load_dotenv()

DEFAULT_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1200          # default cap; a step spec may raise it
MAX_CONTRACT_CHARS = 12000

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
    tests_total: int | None = None
    tests_passed: int | None = None
    tests_failed: int | None = None
    test_output: str | None = None


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
    # An answer cut off by the output cap has an opening fence and no closing
    # one. Take what there is: the gate then reports a compile error naming the
    # truncation, which is a fact the retry can act on, rather than "no block".
    opened = re.search(r"```(?:solidity|sol)?[ \t]*\n", output, re.IGNORECASE)
    if opened:
        body = output[opened.end():].strip()
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


REPO_ROOT = Path(__file__).resolve().parents[2]
FORGE_STD = REPO_ROOT / "contracts" / "lib" / "forge-std"
TEST_OUTPUT_LINES = 40


def run_forge_tests(
    contract_source: str, test_source: str
) -> tuple[bool, int, int, int, str]:
    """Compile and RUN a Foundry suite against the contract under test.

    Returns (ran, total, passed, failed, first lines of output).

    ``ran`` is the gate: it is True whenever forge compiled the suite and
    reported results, even when tests failed. A failing test is not a gate
    failure — it may be the suite correctly documenting a defect in the
    contract, which is the whole product. The gate fails only when the file
    does not compile or forge cannot run it.
    """
    if not shutil.which("forge"):
        return False, 0, 0, 0, "forge is not installed on this machine; cannot run tests"
    if not FORGE_STD.is_dir():
        return False, 0, 0, 0, (
            f"forge-std is not vendored at {FORGE_STD}; run 'forge install' in contracts/"
        )
    name = _contract_name(contract_source)
    with tempfile.TemporaryDirectory(prefix="turnstyl-forge-") as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "test").mkdir()
        (root / "lib").mkdir()
        (root / "src" / f"{name}.sol").write_text(contract_source, encoding="utf-8")
        (root / "test" / f"{name}Test.t.sol").write_text(test_source, encoding="utf-8")
        # symlink rather than copy: forge-std is 1.3 MB and this runs per step
        try:
            (root / "lib" / "forge-std").symlink_to(FORGE_STD, target_is_directory=True)
        except OSError:
            shutil.copytree(FORGE_STD, root / "lib" / "forge-std")
        (root / "foundry.toml").write_text(
            '[profile.default]\nsrc = "src"\nout = "out"\ntest = "test"\nlibs = ["lib"]\n',
            encoding="utf-8",
        )
        try:
            proc = subprocess.run(
                ["forge", "test", "--root", str(root), "--json"],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            return False, 0, 0, 0, "forge test timed out after 600s"
        raw = (proc.stdout or "").strip()
        errors = (proc.stderr or "").strip()

    # forge exits non-zero when tests FAIL, so the exit code cannot be the gate.
    # The gate is whether it produced parseable results at all.
    suites = _parse_forge_json(raw)
    if suites is None:
        detail = (errors or raw or f"forge test exited {proc.returncode}").strip()
        head = "\n".join(detail.splitlines()[:TEST_OUTPUT_LINES])
        return False, 0, 0, 0, head
    total = passed = failed = 0
    lines: list[str] = []
    for suite, results in suites:
        for test_name, status, reason in results:
            total += 1
            if status == "Success":
                passed += 1
                lines.append(f"PASS {test_name}")
            else:
                failed += 1
                lines.append(f"FAIL {test_name}" + (f"  <- {reason}" if reason else ""))
    lines.sort(key=lambda l: (l.startswith("PASS"), l))
    summary = f"{passed} passed, {failed} failed, {total} total"
    body = "\n".join([summary, *lines][:TEST_OUTPUT_LINES])
    return True, total, passed, failed, body


def _parse_forge_json(raw: str):
    """[(suite, [(test, status, reason), ...]), ...] or None if it did not run."""
    if not raw:
        return None
    import json

    # forge may print a line of chatter before the JSON object
    start = raw.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(raw[start:])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    out = []
    for suite, payload in data.items():
        if not isinstance(payload, dict):
            return None
        results = payload.get("test_results")
        if not isinstance(results, dict):
            return None
        out.append(
            [
                suite,
                [
                    (
                        name,
                        (r or {}).get("status"),
                        ((r or {}).get("reason") or "").strip() or None,
                    )
                    for name, r in results.items()
                ],
            ]
        )
    return out


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


def test_mechanical_block(record) -> str:
    """What the reporter is told about the run turnstyl already did.

    The run results are ground truth for step 4; the test file's own comments
    are not, and the prompt says so.
    """
    if record is None or record.tests_total is None:
        return ""
    return (
        "RUN RESULTS (forge test, ground truth): "
        f"compiled and ran: {'yes' if record.compiles else 'no'}; "
        f"{record.tests_passed} passed, {record.tests_failed} failed, "
        f"{record.tests_total} total.\n"
        f"{record.test_output or ''}"
    )


def _build_user_message(
    job_type: JobType,
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
            f"{job_type.step_name(prior_step).upper()} (from step {prior_step}):\n"
            f"{prior_outputs[prior_step]}"
        )
    if mechanical:
        parts.append(mechanical)
    parts.append(f"Now produce the {job_type.step_name(step)} for step {step}.")
    return "\n\n".join(parts)


def run_step(
    job_type: JobType,
    step: int,
    contract_text: str,
    prior_outputs: dict[int, str] | None = None,
    mechanical: str = "",
) -> StepResult:
    """Run one step of one job type.

    The prompt, the step's name and its gate all come from the type's spec;
    everything below is the same for every service. A gated step gets one retry
    with the gate's own errors handed back, and the second answer is accepted as
    it is.

    MOCK_LLM=1 short-circuits to the type's canned output with no network call.
    """
    spec = job_type.step(step)                     # raises on an unknown step
    prior_outputs = prior_outputs or {}

    if os.environ.get("MOCK_LLM") == "1":
        if job_type.mock is None:
            raise RuntimeError(
                f"turnstyl: job type {job_type.id!r} has no mock outputs, so it "
                f"cannot run with MOCK_LLM=1."
            )
        output = job_type.mock(step, contract_text, prior_outputs)
        prompt = spec.system_prompt + _build_user_message(
            job_type, step, contract_text, prior_outputs, mechanical
        )
        usage = Usage(
            input_tokens=_estimate_tokens(prompt),
            output_tokens=_estimate_tokens(output),
        )
        # The mock answers are whole files too, so the offline path runs the same
        # gates the real one does. No retry: a second call returns the identical
        # fixture.
        return _apply_gate(spec, output, contract_text, usage)

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
    user_message = _build_user_message(
        job_type, step, contract_text, prior_outputs, mechanical
    )
    output, usage = _call_model(client, anthropic, spec, model, user_message)

    result = _apply_gate(spec, output, contract_text, usage)
    if spec.gate == GATE_NONE or result.compiles:
        return result

    # A gated answer that does not compile (or does not run) is not a deliverable,
    # however convincing its prose. One retry, with the tool's own errors handed back.
    retry_message = (
        f"{user_message}\n\n"
        f"{_retry_note(spec, result)}\n\n"
        f"Return the complete file again, fixing these errors. Keep the same "
        f"output format, the same pragma, and no imports beyond the ones the "
        f"instructions allow."
    )
    retry_output, retry_usage = _call_model(
        client, anthropic, spec, model, retry_message
    )
    # Both attempts were billed; the caller's cost accounting must see both.
    combined = Usage(
        input_tokens=usage.input_tokens + retry_usage.input_tokens,
        output_tokens=usage.output_tokens + retry_usage.output_tokens,
    )
    return _apply_gate(spec, retry_output, contract_text, combined)


def _retry_note(spec, result: StepResult) -> str:
    if spec.gate == GATE_FORGE_TEST:
        return (
            f"Your previous test file did not compile or could not be run. "
            f"`forge test` reported:\n{result.test_output or result.compiler_output}"
        )
    return (
        f"Your previous patched contract did not compile. `forge build` "
        f"reported:\n{result.compiler_output}"
    )


def _apply_gate(
    spec, output: str, contract_text: str, usage: Usage
) -> StepResult:
    """Put a step's answer through its gate, if it has one."""
    if spec.gate == GATE_COMPILE:
        return _finish_compile_step(output, contract_text, usage)
    if spec.gate == GATE_FORGE_TEST:
        return _finish_forge_test_step(output, contract_text, usage)
    return StepResult(output=output, usage=usage)


def _finish_compile_step(
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


def _finish_forge_test_step(
    output: str, contract_text: str, usage: Usage
) -> StepResult:
    """Extract the suite, run it against the contract, and record what happened.

    The displayed output is the test file followed by the run results, so the
    buyer sees both what was written and what it did.
    """
    suite = extract_solidity(output)
    if suite is None:
        return StepResult(
            output=output,
            usage=usage,
            compiles=False,
            test_output=(
                "the answer contained no ```solidity block, so there was no test "
                "file to compile or run"
            ),
        )
    ran, total, passed, failed, detail = run_forge_tests(contract_text, suite)
    if not ran:
        return StepResult(
            output=suite,
            usage=usage,
            compiles=False,
            tests_total=None,
            test_output=detail,
        )
    display = (
        f"{suite.rstrip()}\n\n"
        f"--- forge test ---\n"
        f"{detail}\n"
    )
    return StepResult(
        output=display,
        usage=usage,
        compiles=True,
        tests_total=total,
        tests_passed=passed,
        tests_failed=failed,
        test_output=detail,
    )


def _call_model(client, anthropic, spec, model: str, user_message: str):
    """One Messages API call. Returns (output_text, Usage)."""
    step = spec.n
    try:
        response = client.messages.create(
            model=model,
            max_tokens=spec.max_tokens or MAX_TOKENS,
            system=spec.system_prompt,
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
            f"({spec.name}); stop_details={response.stop_details}"
        )
    output = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not output:
        raise RuntimeError(
            f"turnstyl: step {step} ({spec.name}) returned no text content "
            f"(stop_reason={response.stop_reason!r}, model={model!r})."
        )
    return output, Usage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
