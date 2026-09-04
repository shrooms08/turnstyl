#!/usr/bin/env python3
"""Offline acceptance test for the turnstyl engine.

Runs the seven beats of the demo against a throwaway database, driving the real
CLI as separate subprocesses so every beat crosses a process boundary — which is
the only way to prove the agent's memory, and not its RAM, is carrying the job.

    .venv/bin/python scripts/demo_offline.py

MOCK_LLM=1 and PAYMENTS=fake are forced, so no network call and no chain are
involved. Prints PASS or FAIL per beat and exits non-zero if any beat fails.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from turnstyl import schema as S  # noqa: E402
from turnstyl.memory import TurnstylMemory, TurnstylStore  # noqa: E402

BUYER = "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8"
CONTRACT = REPO_ROOT / "examples" / "Vault.sol"

BOX_CHARS = "│┃|╭╮╰╯─━┌┐└┘═║╔╗╚╝┏┓┗┛┡┩╇┳┻╋┠┨"
_BOX_RE = re.compile(f"[{re.escape(BOX_CHARS)}]")

DB_PATH = Path(tempfile.mkdtemp(prefix="turnstyl-demo-")) / "demo.db"

failures: list[str] = []
notes: list[str] = []


# ----------------------------------------------------------------------
# Harness
# ----------------------------------------------------------------------
def cli(*args: str, expect_ok: bool = True) -> tuple[str, str]:
    """Run the turnstyl CLI in a fresh process. Returns (raw, flat)."""
    env = {
        **os.environ,
        "TURNSTYL_DB": str(DB_PATH),
        "MOCK_LLM": "1",
        "PAYMENTS": "fake",
        "COLUMNS": "200",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "NO_COLOR": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "turnstyl.cli", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if expect_ok and proc.returncode != 0:
        raise SystemExit(
            f"turnstyl demo: CLI command {' '.join(args)!r} exited "
            f"{proc.returncode}.\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    raw = proc.stdout + proc.stderr
    # Rich draws panels; strip the borders and collapse the wrapping so a
    # substring assertion sees the text the operator sees.
    flat = re.sub(r"\s+", " ", _BOX_RE.sub(" ", raw)).strip()
    return raw, flat


def store() -> TurnstylStore:
    """A fresh store handle. Never reused across beats, on purpose."""
    return TurnstylStore(TurnstylMemory(DB_PATH))


def check(beat: str, label: str, ok: bool, detail: str = "") -> bool:
    if ok:
        print(f"  ok   {label}")
        return True
    failures.append(f"{beat}: {label}")
    print(f"  FAIL {label}" + (f"\n       {detail}" if detail else ""))
    return False


def beat_result(beat: str, title: str, results: list[bool]) -> None:
    verdict = "PASS" if all(results) else "FAIL"
    print(f"{verdict} {beat}: {title}\n")


def decision_line(raw: str) -> str:
    """The DECISION line as printed, rejoined across rich's soft wrapping."""
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("DECISION:"):
            collected = [line.strip()]
            for cont in lines[i + 1 :]:
                stripped = cont.strip()
                if not stripped or stripped.startswith(("memory read:", "╭", "│")):
                    break
                collected.append(stripped)
            return " ".join(collected)
    return ""


def only_job_id() -> str:
    active = store().get_active_jobs()
    if len(active) != 1:
        raise SystemExit(
            f"turnstyl demo: expected exactly 1 active job in {DB_PATH}, "
            f"found {len(active)}: {active}"
        )
    return active[0]


# ----------------------------------------------------------------------
# Beats
# ----------------------------------------------------------------------
def beat_a() -> str:
    print("BEAT a: new job, free step 1, invoice for step 2, then WAIT_FOR_PAYMENT")
    _, flat = cli("job", "new", str(CONTRACT), "--buyer", BUYER)
    job_id = only_job_id()
    r = [
        check("a", "step 1 output printed", "STEP 1: scope" in flat and "SCOPE (contract" in flat, flat[:300]),
        check("a", "invoice for step 2 at 0.50 USDC", "step 2 (findings)" in flat and "amount 0.50 USDC" in flat, flat[:400]),
        check("a", "decision is RUN_FREE", "DECISION: RUN_FREE" in flat),
    ]
    state = store().get_job_state(job_id)
    r.append(check("a", "state is awaiting_payment at step 2",
                   state is not None and state.status == S.STATUS_AWAITING_PAYMENT and state.current_step == 2,
                   f"state={state}"))
    raw2, flat2 = cli("job", "run", job_id)
    r.append(check("a", "a following run waits for payment", "DECISION: WAIT_FOR_PAYMENT" in flat2, flat2[:300]))
    r.append(check("a", "nothing was executed for step 2", "STEP 2: findings" not in flat2))
    beat_result("a", "free step 1, invoiced, then blocked on payment", r)
    return job_id


def beat_b(job_id: str) -> None:
    print("BEAT b: pay step 2, run, expect RUN_PAID and an invoice for step 3")
    cli("pay", job_id, "2")
    _, flat = cli("job", "run", job_id)
    r = [
        check("b", "decision is RUN_PAID", "DECISION: RUN_PAID" in flat, flat[:300]),
        check("b", "step 2 executed", "STEP 2: findings" in flat and "Reentrancy in withdraw()" in flat),
        check("b", "invoice for step 3 at 0.75 USDC", "step 3 (patch)" in flat and "amount 0.75 USDC" in flat, flat[:400]),
    ]
    ledger = store().get_buyer(BUYER)
    r.append(check("b", "buyer credited one paid step", ledger.paid_steps == 1 and ledger.paid_usdc == 0.50,
                   f"paid_steps={ledger.paid_steps}, paid_usdc={ledger.paid_usdc}"))
    beat_result("b", "paid step 2 executed, step 3 invoiced", r)


def beat_c(job_id: str) -> None:
    print("BEAT c: pay step 3, run, buyer reaches paid_steps=2 and trust_tier trusted")
    cli("pay", job_id, "3")
    _, flat = cli("job", "run", job_id)
    ledger = store().get_buyer(BUYER)
    r = [
        check("c", "decision is RUN_PAID", "DECISION: RUN_PAID" in flat, flat[:300]),
        check("c", "step 3 executed", "STEP 3: patch" in flat),
        check("c", "buyer has paid_steps == 2", ledger.paid_steps == 2, f"paid_steps={ledger.paid_steps}"),
        check("c", "buyer trust_tier == trusted", ledger.trust_tier == S.TRUST_TRUSTED,
              f"trust_tier={ledger.trust_tier}, open_invoices={ledger.open_invoices}"),
        check("c", "paid 1.25 USDC so far", ledger.paid_usdc == 1.25, f"paid_usdc={ledger.paid_usdc}"),
    ]
    beat_result("c", "two paid steps, buyer now trusted", r)


def beat_d(job_id: str) -> tuple[str, dict[int, str]]:
    print("BEAT d: RESUME. Fresh process runs step 4 on credit, steps 1-3 untouched")
    before = store().get_job_entity(job_id)
    if before is None:
        raise SystemExit(f"turnstyl demo: job entity for {job_id} vanished before beat d")
    prior_sha = {int(k): v.output_sha256 for k, v in before.steps.items()}
    if sorted(prior_sha) != [1, 2, 3]:
        raise SystemExit(f"turnstyl demo: expected steps 1-3 recorded, found {sorted(prior_sha)}")

    raw, flat = cli("job", "run", job_id)
    line = decision_line(raw)
    r = [
        check("d", "decision is RUN_ON_CREDIT", "DECISION: RUN_ON_CREDIT" in flat, flat[:300]),
        check("d", "reason names paid_steps=2", "paid_steps=2" in line, line),
        check("d", "step 4 executed immediately", "STEP 4: verify" in flat),
        check("d", "steps 1-3 were not re-run", not any(f"STEP {n}:" in flat for n in (1, 2, 3)), flat[:300]),
    ]
    # The job entity is archived on completion, so verify the preserved outputs
    # through the findings entity the completion wrote.
    state = store().get_job_state(job_id)
    findings = store().get_findings(state.contract_hash)
    same = {
        step: S.sha256_text(findings.slot(step)) == prior_sha[step]
        for step in (1, 2, 3)
    }
    r.append(check("d", "steps 1-3 sha256 unchanged", all(same.values()), f"{same}"))
    beat_result("d", "resumed on credit without redoing paid work", r)
    notes.append(f"beat d DECISION line:\n    {line}")
    return line, prior_sha


def beat_e(job_id: str) -> None:
    print("BEAT e: COMPLETE. findings cached, job entity archived, ledger settled")
    st = store()
    state = st.get_job_state(job_id)
    findings = st.get_findings(state.contract_hash)
    ledger = st.get_buyer(BUYER)
    outstanding = [o for o in ledger.outstanding if o.job_id == job_id]
    r = [
        check("e", "job status is complete", state.status == S.STATUS_COMPLETE, f"status={state.status}"),
        check("e", "findings entity exists for the contract hash", st.findings_exist(state.contract_hash)),
        check("e", "findings holds all four outputs",
              all(findings.slot(n) for n in S.ALL_STEPS),
              f"{[n for n in S.ALL_STEPS if not findings.slot(n)]} missing"),
        check("e", "job entity archived", st.get_job_entity(job_id) is None),
        check("e", "job removed from active_jobs", job_id not in st.get_active_jobs()),
        # Defined: the buyer has 2 PAID steps plus 1 OPEN invoice for step 4,
        # because step 4 was delivered on credit and never settled.
        check("e", "ledger shows 2 paid steps", ledger.paid_steps == 2, f"paid_steps={ledger.paid_steps}"),
        check("e", "ledger shows exactly 1 outstanding invoice, for step 4 at 0.25 USDC",
              len(outstanding) == 1 and outstanding[0].step == 4 and outstanding[0].amount_usdc == 0.25,
              f"outstanding={[o.model_dump() for o in outstanding]}"),
        check("e", "the unpaid step 4 is carried as prior-job debt",
              ledger.unpaid_from_prior_jobs == 1, f"unpaid_from_prior_jobs={ledger.unpaid_from_prior_jobs}"),
    ]
    beat_result("e", "job complete, findings cached, one invoice outstanding", r)


def beat_f(first_job: str) -> None:
    print("BEAT f: REPEAT CONTRACT. Same contract, same buyer, step 2 priced from memory")
    _, flat = cli("job", "new", str(CONTRACT), "--buyer", BUYER)
    new_job = only_job_id()
    r = [
        check("f", "a new job was created", new_job != first_job, f"new={new_job} first={first_job}"),
        check("f", "step 2 is priced at 0.25 USDC (50% of 0.50)",
              "step 2 (findings)" in flat and "amount 0.25 USDC" in flat, flat[:500]),
        check("f", "the price reason says the findings are cached",
              "findings cached for this contract hash" in flat, flat[:600]),
        check("f", "step 1 was served from memory", "from memory (cached)" in flat, flat[:400]),
    ]
    beat_result("f", "repeat contract served from memory at half price", r)


def beat_g(prior_jobs: set[str]) -> str:
    print("BEAT g: DELETE TEST. Wipe the database and watch the agent forget")
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(DB_PATH) + suffix)
        if target.exists():
            target.unlink()
    if DB_PATH.exists():
        raise SystemExit(f"turnstyl demo: failed to delete {DB_PATH}")

    raw, flat = cli("job", "new", str(CONTRACT), "--buyer", BUYER)
    line = decision_line(raw)
    new_job = only_job_id()
    ledger = store().get_buyer(BUYER)
    r = [
        check("g", "a brand new job id was issued", new_job not in prior_jobs, f"job={new_job}"),
        check("g", "step 1 ran again", "STEP 1: scope" in flat and "SCOPE (contract" in flat),
        check("g", "step 1 was NOT served from memory", "from memory (cached)" not in flat, flat[:400]),
        check("g", "step 2 is invoiced at 0.50 USDC again",
              "step 2 (findings)" in flat and "amount 0.50 USDC" in flat, flat[:500]),
        check("g", "buyer trust_tier is back to new", ledger.trust_tier == S.TRUST_NEW,
              f"trust_tier={ledger.trust_tier}"),
        check("g", "buyer paid history is gone", ledger.paid_steps == 0 and ledger.paid_usdc == 0.0,
              f"paid_steps={ledger.paid_steps}, paid_usdc={ledger.paid_usdc}"),
    ]
    beat_result("g", "memory deleted, buyer treated as a stranger", r)
    if all(r):
        print("DOUBLE CHARGE REPRODUCED: memory deleted, buyer re-invoiced 0.50 for paid work\n")
    notes.append(f"beat g DECISION line:\n    {line}")
    return line


# ----------------------------------------------------------------------
def main() -> int:
    print(f"turnstyl offline demo\ndatabase: {DB_PATH}\ncontract: {CONTRACT}\n")
    if not CONTRACT.is_file():
        raise SystemExit(f"turnstyl demo: sample contract missing at {CONTRACT}")

    job_a = beat_a()
    beat_b(job_a)
    beat_c(job_a)
    beat_d(job_a)
    beat_e(job_a)
    seen = {job_a}
    beat_f(job_a)
    seen.update(store().get_active_jobs())
    beat_g(seen)

    print("-" * 72)
    for note in notes:
        print(note)
    print("-" * 72)
    if failures:
        print(f"RESULT: FAIL — {len(failures)} check(s) failed")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — all 7 beats passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
