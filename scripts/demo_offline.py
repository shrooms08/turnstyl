#!/usr/bin/env python3
"""Offline acceptance test for the turnstyl engine.

Runs the eight beats of the demo against a throwaway database, driving the real
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
def price_str(step: int, cached: bool) -> str:
    """The invoice amount the CLI prints for a step, from the pricing rules."""
    amount = round(S.BASE_PRICES[step] * (S.CACHED_MULTIPLIER if cached else 1.0), 2)
    return f"amount {amount:.2f} USDC"


def pay_and_run(job_id: str, step: int, beat: str, r: list[bool], cached: bool | None = None) -> str:
    """Settle one step on the fake backend and run it; assert RUN_PAID."""
    cli("pay", job_id, str(step))
    raw, flat = cli("job", "run", job_id)
    r.append(check(beat, f"step {step} ran as paid work", "DECISION: RUN_PAID" in flat, flat[:300]))
    if cached is True:
        r.append(check(beat, f"step {step} was served from memory", "from memory (cached)" in flat, flat[:300]))
    return raw


def complete_paid_job(beat: str, r: list[bool], expect_cached: bool) -> str:
    """One whole job, every paid step settled: what credit is earned on."""
    _, flat = cli("job", "new", str(CONTRACT), "--buyer", BUYER)
    job_id = only_job_id()
    r.append(check(beat, f"job {job_id}: step 2 invoiced at {price_str(2, expect_cached)[7:]}",
                   price_str(2, expect_cached) in flat, flat[:500]))
    if expect_cached:
        r.append(check(beat, f"job {job_id}: step 1 served from memory", "from memory (cached)" in flat, flat[:400]))
    for step in (2, 3, 4):
        pay_and_run(job_id, step, beat, r, cached=expect_cached if step > 1 else None)
    state = store().get_job_state(job_id)
    r.append(check(beat, f"job {job_id} complete", state is not None and state.status == S.STATUS_COMPLETE))
    return job_id


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
    line = decision_line(raw2)
    r.append(check("a", "a following run waits for payment", "DECISION: WAIT_FOR_PAYMENT" in flat2, flat2[:300]))
    r.append(check("a", "the reason says credit comes after 3 fully paid jobs, currently 0",
                   "credit after 3 fully paid jobs, currently 0" in line, line))
    r.append(check("a", "nothing was executed for step 2", "STEP 2: findings" not in flat2))
    beat_result("a", "free step 1, invoiced, then blocked on payment", r)
    notes.append(f"beat a WAIT_FOR_PAYMENT line:\n    {line}")
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
    print("BEAT c: pay step 3, run; two paid steps do NOT earn credit any more")
    cli("pay", job_id, "3")
    _, flat = cli("job", "run", job_id)
    ledger = store().get_buyer(BUYER)
    r = [
        check("c", "decision is RUN_PAID", "DECISION: RUN_PAID" in flat, flat[:300]),
        check("c", "step 3 executed", "STEP 3: patch" in flat),
        check("c", "buyer has paid_steps == 2", ledger.paid_steps == 2, f"paid_steps={ledger.paid_steps}"),
        check("c", "buyer is still 'new' (no completed paid job yet)", ledger.trust_tier == S.TRUST_NEW,
              f"trust_tier={ledger.trust_tier}, completed_paid_jobs={ledger.completed_paid_jobs}"),
        check("c", "paid 1.25 USDC so far", ledger.paid_usdc == 1.25, f"paid_usdc={ledger.paid_usdc}"),
    ]
    _, flat4 = cli("job", "run", job_id)
    r.append(check("c", "step 4 waits for payment (no credit from step counts)",
                   "DECISION: WAIT_FOR_PAYMENT" in flat4, flat4[:300]))
    beat_result("c", "two paid steps, still no credit", r)


def beat_d(job_id: str) -> None:
    print("BEAT d: RESUME. Pay step 4; a fresh process finishes the job, steps 1-3 untouched")
    before = store().get_job_entity(job_id)
    if before is None:
        raise SystemExit(f"turnstyl demo: job entity for {job_id} vanished before beat d")
    prior_sha = {int(k): v.output_sha256 for k, v in before.steps.items()}
    if sorted(prior_sha) != [1, 2, 3]:
        raise SystemExit(f"turnstyl demo: expected steps 1-3 recorded, found {sorted(prior_sha)}")
    cli("pay", job_id, "4")
    _, flat = cli("job", "run", job_id)
    r = [
        check("d", "step 4 ran as paid work in a fresh process", "DECISION: RUN_PAID" in flat, flat[:300]),
        check("d", "step 4 executed", "STEP 4: verify" in flat),
        check("d", "steps 1-3 were not re-run", not any(f"STEP {n}:" in flat for n in (1, 2, 3)), flat[:300]),
    ]
    st = store()
    state = st.get_job_state(job_id)
    findings = st.get_findings(state.contract_hash)
    same = {step: S.sha256_text(findings.slot(step)) == prior_sha[step] for step in (1, 2, 3)}
    ledger = st.get_buyer(BUYER)
    r.append(check("d", "steps 1-3 sha256 unchanged", all(same.values()), f"{same}"))
    r.append(check("d", "job complete, entity archived, findings cached",
                   state.status == S.STATUS_COMPLETE and st.get_job_entity(job_id) is None
                   and all(findings.slot(n) for n in S.ALL_STEPS)))
    r.append(check("d", "first fully paid job counted: completed_paid_jobs == 1",
                   ledger.completed_paid_jobs == 1, f"completed_paid_jobs={ledger.completed_paid_jobs}"))
    r.append(check("d", "nothing outstanding, trust still new",
                   not ledger.outstanding and ledger.trust_tier == S.TRUST_NEW,
                   f"outstanding={len(ledger.outstanding)}, trust_tier={ledger.trust_tier}"))
    beat_result("d", "resumed and finished as paid work, one job on the record", r)


def beat_e() -> list[str]:
    print("BEAT e: HISTORY. Two more fully paid jobs, served from memory at half price")
    r: list[bool] = []
    jobs = []
    for n in (2, 3):
        jobs.append(complete_paid_job("e", r, expect_cached=True))
        ledger = store().get_buyer(BUYER)
        r.append(check("e", f"after job {n}: completed_paid_jobs == {n}", ledger.completed_paid_jobs == n,
                       f"completed_paid_jobs={ledger.completed_paid_jobs}"))
        want = S.TRUST_TRUSTED if n >= 3 else S.TRUST_NEW
        r.append(check("e", f"after job {n}: trust_tier {want}", ledger.trust_tier == want,
                       f"trust_tier={ledger.trust_tier}"))
    beat_result("e", "three fully paid jobs, buyer now trusted", r)
    return jobs


def beat_f() -> tuple[str, str]:
    print("BEAT f: CREDIT. Fourth job: step 2 runs before its invoice clears")
    _, flat = cli("job", "new", str(CONTRACT), "--buyer", BUYER)
    job_id = only_job_id()
    raw, flat2 = cli("job", "run", job_id)
    line = decision_line(raw)
    r = [
        check("f", "step 1 served from memory", "from memory (cached)" in flat, flat[:400]),
        check("f", "step 2 runs on credit", "DECISION: RUN_ON_CREDIT" in flat2, flat2[:400]),
        check("f", "the reason names completed_paid_jobs=3 >= 3", "completed_paid_jobs=3 >= 3" in line, line),
        check("f", "step 2 executed", "STEP 2: findings" in flat2),
    ]
    ledger = store().get_buyer(BUYER)
    r.append(check("f", "the credit step is carried as open_invoices == 1", ledger.open_invoices == 1,
                   f"open_invoices={ledger.open_invoices}"))
    _, flat3 = cli("job", "run", job_id)
    r.append(check("f", "step 3 waits: nothing more on credit while one step is owed",
                   "DECISION: WAIT_FOR_PAYMENT" in flat3, flat3[:300]))
    for step in (3, 4):
        pay_and_run(job_id, step, "f", r)
    ledger = store().get_buyer(BUYER)
    r.append(check("f", "job closed with step 2 unpaid: defaults == 1, unpaid_from_prior_jobs == 1",
                   ledger.defaults == 1 and ledger.unpaid_from_prior_jobs == 1,
                   f"defaults={ledger.defaults}, unpaid={ledger.unpaid_from_prior_jobs}"))
    r.append(check("f", "a job closed with a debt is not a completed paid job (still 3)",
                   ledger.completed_paid_jobs == 3, f"completed_paid_jobs={ledger.completed_paid_jobs}"))
    beat_result("f", "credit extended on three paid jobs, then one default", r)
    notes.append(f"beat f RUN_ON_CREDIT line:\n    {line}")
    return job_id, line


def beat_g(credit_job: str) -> str:
    """Refuse, then a buyer with a default pays its way back to credit."""
    print("BEAT g: REFUSE and EARN-BACK. Debt refused, settled, four clean paid steps, credit returns")
    r: list[bool] = []
    cli("job", "new", str(CONTRACT), "--buyer", BUYER)
    fifth = only_job_id()
    _, flat = cli("job", "run", fifth)
    r.append(check("g", "step 2 of the next job is refused", "DECISION: REFUSE" in flat, flat[:300]))
    r.append(check("g", "the reason names the unpaid prior step", "unpaid on a completed job" in flat, flat[:400]))

    cli("pay", credit_job, "2")                       # settle the debt on the closed job
    raw, flat = cli("job", "run", fifth)
    line_wait = decision_line(raw)
    ledger = store().get_buyer(BUYER)
    r.append(check("g", "settling the old debt clears unpaid_from_prior_jobs", ledger.unpaid_from_prior_jobs == 0,
                   f"unpaid_from_prior_jobs={ledger.unpaid_from_prior_jobs}"))
    r.append(check("g", "the debt payment counts as clean paid step 1 of 4", ledger.consecutive_paid_since_default == 1,
                   f"consecutive={ledger.consecutive_paid_since_default}"))
    r.append(check("g", "credit has not returned yet", "DECISION: WAIT_FOR_PAYMENT" in flat, flat[:300]))
    r.append(check("g", "the reason says how many clean steps are still needed",
                   "credit returns after 4 consecutive paid steps, currently 1" in line_wait, line_wait))
    for step in (2, 3, 4):
        pay_and_run(fifth, step, "g", r, cached=True if step == 2 else None)
    ledger = store().get_buyer(BUYER)
    r.append(check("g", "four consecutive paid steps on record", ledger.consecutive_paid_since_default == 4,
                   f"consecutive={ledger.consecutive_paid_since_default}"))
    r.append(check("g", "trusted again despite the default", ledger.trust_tier == S.TRUST_TRUSTED,
                   f"trust_tier={ledger.trust_tier}, defaults={ledger.defaults}, completed_paid_jobs={ledger.completed_paid_jobs}"))
    cli("job", "new", str(CONTRACT), "--buyer", BUYER)
    sixth = only_job_id()
    raw, flat = cli("job", "run", sixth)
    line = decision_line(raw)
    r.append(check("g", "the next unpaid step runs on credit", "DECISION: RUN_ON_CREDIT" in flat, flat[:400]))
    r.append(check("g", "the reason names consecutive_paid_since_default=4", "consecutive_paid_since_default=4" in line, line))
    beat_result("g", "refused, settled, earned back", r)
    notes.append(f"beat g WAIT_FOR_PAYMENT (after default) line:\n    {line_wait}")
    notes.append(f"beat g RUN_ON_CREDIT (earned back) line:\n    {line}")
    return sixth


def beat_h(prior_jobs: set[str]) -> str:
    print("BEAT h: DELETE TEST. Wipe the database and watch the agent forget")
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
        check("h", "a brand new job id was issued", new_job not in prior_jobs, f"job={new_job}"),
        check("h", "step 1 ran again", "STEP 1: scope" in flat and "SCOPE (contract" in flat),
        check("h", "step 1 was NOT served from memory", "from memory (cached)" not in flat, flat[:400]),
        check("h", "step 2 is invoiced at 0.50 USDC again",
              "step 2 (findings)" in flat and "amount 0.50 USDC" in flat, flat[:500]),
        check("h", "buyer trust_tier is back to new", ledger.trust_tier == S.TRUST_NEW,
              f"trust_tier={ledger.trust_tier}"),
        check("h", "buyer paid history is gone", ledger.paid_steps == 0 and ledger.completed_paid_jobs == 0,
              f"paid_steps={ledger.paid_steps}, completed_paid_jobs={ledger.completed_paid_jobs}"),
    ]
    beat_result("h", "memory deleted, buyer treated as a stranger", r)
    if all(r):
        print("DOUBLE CHARGE REPRODUCED: memory deleted, buyer re-invoiced 0.50 for paid work\n")
    notes.append(f"beat h DECISION line:\n    {line}")
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
    seen = {job_a}
    seen.update(beat_e())
    credit_job, _ = beat_f()
    seen.add(credit_job)
    seen.add(beat_g(credit_job))
    seen.update(store().get_active_jobs())
    beat_h(seen)

    print("-" * 72)
    for note in notes:
        print(note)
    print("-" * 72)
    if failures:
        print(f"RESULT: FAIL — {len(failures)} check(s) failed")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — all 8 beats passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
