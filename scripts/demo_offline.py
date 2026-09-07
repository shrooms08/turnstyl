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

from turnstyl import jobtypes  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from turnstyl import digest  # noqa: E402
from turnstyl import events  # noqa: E402
from turnstyl import injection  # noqa: E402
from turnstyl import policy  # noqa: E402
from turnstyl import schema as S  # noqa: E402
from turnstyl.engine import promote_arrears
from turnstyl.memory import TurnstylMemory, TurnstylStore  # noqa: E402

BUYER = "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8"
CONTRACT = REPO_ROOT / "examples" / "Vault.sol"
ADVERSARIAL = REPO_ROOT / "examples" / "Adversarial.sol"

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


def active_job_of_type(job_type: str) -> str:
    """The active job of one type. Beat g leaves an audit job open, so from
    there on "the only active job" is no longer a safe way to find one."""
    st = store()
    matches = [
        j for j in st.get_active_jobs()
        if (st.get_job_state(j) or None) and st.get_job_state(j).job_type == job_type
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"turnstyl demo: expected exactly 1 active {job_type} job, found "
            f"{len(matches)}: {matches}"
        )
    return matches[0]


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
AUDIT = jobtypes.get("audit")


def price_str(step: int, cached: bool) -> str:
    """The invoice amount the CLI prints for an audit step, from the spec."""
    base = AUDIT.step(step).base_price_usdc
    amount = round(base * (S.CACHED_MULTIPLIER if cached else 1.0), 2)
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
    findings = st.get_findings("audit", state.contract_hash)
    same = {
        step: S.sha256_text(findings.slot(AUDIT.step_name(step))) == prior_sha[step]
        for step in (1, 2, 3)
    }
    ledger = st.get_buyer(BUYER)
    r.append(check("d", "steps 1-3 sha256 unchanged", all(same.values()), f"{same}"))
    r.append(check("d", "job complete, entity archived, findings cached",
                   state.status == S.STATUS_COMPLETE and st.get_job_entity(job_id) is None
                   and all(findings.slot(AUDIT.step_name(n)) for n in AUDIT.all_steps)))
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


def age_arrears(job_id: str, hours: float | None = None) -> None:
    """Backdate this job's arrears so its grace period has run out.

    The demo cannot wait a day. Backdating closed_at states exactly the fact a
    day would have produced, and the promotion itself is the agent's own code
    path (engine.promote_arrears), not a shortcut around it.
    """
    hours = S.GRACE_HOURS + 1 if hours is None else hours
    st = store()
    ledger = st.get_buyer(BUYER)
    when = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    touched = 0
    for item in ledger.outstanding:
        if item.job_id == job_id and item.closed_at:
            item.closed_at = when
            touched += 1
    if not touched:
        raise SystemExit(
            f"turnstyl demo: job {job_id} has no arrears item to age; the close "
            f"did not record one"
        )
    st.put_buyer(BUYER, ledger)


def force_default(job_id: str) -> list[dict]:
    """Age the arrears and let the agent notice, which records the default."""
    age_arrears(job_id)
    return promote_arrears(store(), BUYER)


def beat_f() -> tuple[str, str]:
    print("BEAT f: CREDIT and GRACE. Work on credit, arrears, settled in time, then one left too long")
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

    # 1. The close records arrears, not a default. Not paying yet and not
    #    paying look the same at this moment; only the clock separates them.
    r.append(check("f", "the close records arrears, not a default",
                   ledger.defaults == 0 and ledger.unpaid_from_prior_jobs == 1,
                   f"defaults={ledger.defaults}, unpaid={ledger.unpaid_from_prior_jobs}"))
    late = policy.arrears(ledger)
    r.append(check("f", "the debt carries the moment its job closed",
                   len(late) == 1 and bool(late[0].closed_at),
                   str([(i.job_id, i.closed_at) for i in ledger.outstanding])))
    r.append(check("f", "credit is suspended while it is owed",
                   ledger.unpaid_from_prior_jobs == 1,
                   f"unpaid={ledger.unpaid_from_prior_jobs}"))
    arrears_reason = policy.arrears_line(ledger, datetime.now(timezone.utc))
    r.append(check("f", "and the refusal counts the grace period down",
                   arrears_reason.startswith("in arrears:")
                   and "before it counts as a default" in arrears_reason,
                   arrears_reason))
    cli("job", "new", str(CONTRACT), "--buyer", BUYER)
    _, refused = cli("job", "run", only_job_id())
    r.append(check("f", "paid work is refused while in arrears, in those words",
                   "DECISION: REFUSE" in refused and "in arrears:" in refused,
                   refused[:400]))
    r.append(check("f", "a job closed with a debt is not a completed paid job (still 3)",
                   ledger.completed_paid_jobs == 3, f"completed_paid_jobs={ledger.completed_paid_jobs}"))

    # 2. Settled inside the grace period: no default, nothing reset.
    csd_before = ledger.consecutive_paid_since_default
    cli("pay", job_id, "2")
    cli("ledger", BUYER)                      # reconciliation runs on the next read
    ledger = store().get_buyer(BUYER)
    r.append(check("f", "settling within grace records no default",
                   ledger.defaults == 0, f"defaults={ledger.defaults}"))
    r.append(check("f", "and clears the arrears entirely",
                   not policy.arrears(ledger) and ledger.unpaid_from_prior_jobs == 0,
                   f"arrears={len(policy.arrears(ledger))}, unpaid={ledger.unpaid_from_prior_jobs}"))
    r.append(check("f", "the earn-back clock was never reset",
                   ledger.consecutive_paid_since_default > csd_before,
                   f"{csd_before} -> {ledger.consecutive_paid_since_default}"))
    r.append(check("f", "so the buyer is trusted again",
                   ledger.trust_tier == S.TRUST_TRUSTED, f"trust_tier={ledger.trust_tier}"))

    # 3. A second credit close, left past its grace period: that is a default.
    cli("job", "new", str(CONTRACT), "--buyer", BUYER)
    job_id = only_job_id()
    _, credit_flat = cli("job", "run", job_id)
    r.append(check("f", "credit is extended again after the debt was settled",
                   "DECISION: RUN_ON_CREDIT" in credit_flat, credit_flat[:300]))
    for step in (3, 4):
        pay_and_run(job_id, step, "f", r)
    moved = force_default(job_id)
    ledger = store().get_buyer(BUYER)
    r.append(check("f", "left past grace, the arrears becomes a default",
                   len(moved) == 1 and ledger.defaults == 1,
                   f"moved={len(moved)}, defaults={ledger.defaults}"))
    r.append(check("f", "and the earn-back clock is reset by it",
                   ledger.consecutive_paid_since_default == 0,
                   f"consecutive={ledger.consecutive_paid_since_default}"))
    r.append(check("f", "the journal records the promotion",
                   any((e.get("extra") or {}).get("decision") == "ARREARS_DEFAULTED"
                       for e in store().read_journal(limit=40))))
    beat_result("f", "arrears, settled in time, then one left too long", r)
    notes.append(f"beat f RUN_ON_CREDIT line:\n    {line}")
    notes.append(f"beat f arrears line:\n    {arrears_reason}")
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


def beat_h(credit_job: str) -> str:
    """The second service. Same engine, same ledger, a different spec."""
    print("BEAT h: SECOND SERVICE. A Foundry test suite, priced and gated on its own terms")
    r: list[bool] = []
    # settle the credit step beat g left open, so the buyer is trusted again.
    # Reconciliation runs at the start of the next engine call; `ledger` is one.
    cli("pay", credit_job, "2")
    cli("ledger", BUYER)
    led = store().get_buyer(BUYER)
    r.append(check("h", "the previous job's credit step is settled and trust is back",
                   led.open_invoices == 0 and led.trust_tier == S.TRUST_TRUSTED,
                   f"open_invoices={led.open_invoices}, trust={led.trust_tier}, "
                   f"completed_paid_jobs={led.completed_paid_jobs}, defaults={led.defaults}, "
                   f"consecutive={led.consecutive_paid_since_default}, "
                   f"outstanding={[(o.job_id, o.step) for o in led.outstanding]}"))

    _, flat = cli("job", "new", str(CONTRACT), "--buyer", BUYER, "--type", "tests")
    job_id = active_job_of_type("tests")
    st = store()
    state = st.get_job_state(job_id)
    r.append(check("h", "the job records its type", state is not None and state.job_type == "tests",
                   f"job_type={state.job_type if state else None}"))
    r.append(check("h", "step 1 is the tests scope, not the audit scope", "STEP 1: scope" in flat and "TEST SCOPE" in flat, flat[:300]))
    r.append(check("h", "step 1 is NOT served from the audit's cached findings",
                   "from memory (cached)" not in flat, flat[:400]))
    r.append(check("h", "step 2 is invoiced at this type's own price, 0.40 USDC",
                   "step 2 (plan)" in flat and "amount 0.40 USDC" in flat, flat[:500]))

    # trust was earned on audits; it is spent here
    raw, flat = cli("job", "run", job_id)
    line = decision_line(raw)
    r.append(check("h", "the tests job runs on credit, on trust earned from audits",
                   "DECISION: RUN_ON_CREDIT" in flat, flat[:400]))
    r.append(check("h", "step 2 (plan) executed", "STEP 2: plan" in flat, flat[:300]))

    # step 3 goes through the forge_test gate
    cli("pay", job_id, "2")          # settle the credit step
    cli("pay", job_id, "3")
    _, flat = cli("job", "run", job_id)
    r.append(check("h", "step 3 ran as paid work", "DECISION: RUN_PAID" in flat, flat[:300]))
    r.append(check("h", "step 3 (tests) executed", "STEP 3: tests" in flat, flat[:300]))
    r.append(check("h", "the CLI panel reports the run", "TESTS:" in flat and "passed" in flat, flat[:600]))

    entity = store().get_job_entity(job_id)
    rec = entity.steps.get("3") if entity else None
    r.append(check("h", "the suite compiled and ran", rec is not None and rec.compiles is True,
                   f"compiles={getattr(rec, 'compiles', None)}: {getattr(rec, 'test_output', '')[:120]}"))
    r.append(check("h", "at least 3 tests ran", rec is not None and (rec.tests_total or 0) >= 3,
                   f"tests_total={getattr(rec, 'tests_total', None)}"))
    r.append(check("h", "at least one test fails, documenting a real defect",
                   rec is not None and (rec.tests_failed or 0) >= 1,
                   f"tests_failed={getattr(rec, 'tests_failed', None)}"))
    r.append(check("h", "a failing test is not a gate failure: the step was still delivered",
                   rec is not None and bool(rec.output)))

    cli("pay", job_id, "4")
    _, flat = cli("job", "run", job_id)
    r.append(check("h", "step 4 (report) executed and the job completed",
                   "STEP 4: report" in flat and "COMPLETE" in flat, flat[:400]))

    st = store()
    tests_findings = st.get_findings("tests", state.contract_hash)
    audit_findings = st.get_findings("audit", state.contract_hash)
    r.append(check("h", "the tests work is cached under its own type",
                   sorted(tests_findings.filled) == ["plan", "report", "scope", "tests"],
                   f"tests slots={tests_findings.filled}"))
    r.append(check("h", "the audit's cached work is untouched by it",
                   sorted(audit_findings.filled) == ["findings", "patch", "scope", "verify"],
                   f"audit slots={audit_findings.filled}"))
    r.append(check("h", "cost history is kept per type",
                   st.get_step_cost("tests", 3).runs >= 1 and st.get_step_cost("audit", 3).runs >= 1,
                   f"tests/3 runs={st.get_step_cost('tests',3).runs}, audit/3 runs={st.get_step_cost('audit',3).runs}"))
    ledger = st.get_buyer(BUYER)
    r.append(check("h", "one shared ledger across services: no new default",
                   ledger.defaults == 1 and not ledger.outstanding,
                   f"defaults={ledger.defaults}, outstanding={len(ledger.outstanding)}"))
    beat_result("h", "a second service on the same engine, memory and ledger", r)
    notes.append(f"beat h RUN_ON_CREDIT line (trust across services):\n    {line}")
    return job_id


def beat_i() -> None:
    """An untrusted source. The scan runs before any model does."""
    print("BEAT i: UNTRUSTED SOURCE. A contract whose comments try to instruct the auditor")
    r: list[bool] = []
    raw, flat = cli("job", "new", str(ADVERSARIAL), "--buyer", BUYER)
    # Other jobs are still open by now, so this one is found by its source hash
    # rather than by being the only one.
    adv_hash = S.sha256_text(ADVERSARIAL.read_text(encoding="utf-8"))
    st = store()
    job_id = next(
        (j for j in st.get_active_jobs()
         if (st.get_job_state(j) or None) and st.get_job_state(j).contract_hash == adv_hash),
        "",
    )
    if not job_id:
        raise SystemExit("turnstyl demo: the adversarial job was not created")
    state = st.get_job_state(job_id)
    flags = list(state.injection_flags) if state else []
    rules = sorted({f.rule for f in flags})

    r.append(check("i", "the flags are recorded on the job state", len(flags) >= 6,
                   f"{len(flags)} flag(s): {rules}"))
    r.append(check("i", "every rule class fires on this contract",
                   set(rules) >= {"ignore-instructions", "suppress-findings", "role-assertion",
                                  "chat-role-marker", "addresses-the-model"},
                   f"rules={rules}"))
    r.append(check("i", "the flags carry line numbers and the matched text",
                   all(f.line > 0 and f.matched for f in flags),
                   str([(f.line, f.matched) for f in flags[:3]])))
    r.append(check("i", "the CLI shows the warning", "UNTRUSTED SOURCE" in flat and
                   "text that tries to instruct the auditor" in flat, flat[:300]))
    r.append(check("i", "the journal records one line for the scan",
                   any((e.get("extra") or {}).get("decision") == "FLAGGED_UNTRUSTED_SOURCE"
                       for e in store().read_journal(limit=40))))
    r.append(check("i", "step 1 still ran and the job is priced as usual",
                   "STEP 1: scope" in flat and "amount 0.50 USDC" in flat, flat[:400]))

    # a clean contract of the same shape produces no flags at all
    r.append(check("i", "the scan is quiet on the ordinary sample contract",
                   not injection.scan(CONTRACT.read_text(encoding="utf-8")),
                   str(injection.scan(CONTRACT.read_text(encoding="utf-8"))[:2])))
    beat_result("i", "instruction-like text found, recorded and shown, never followed", r)
    notes.append(
        f"beat i: {len(flags)} flagged passage(s) on lines "
        f"{sorted({f.line for f in flags})}, rules {rules}"
    )


def beat_j() -> None:
    """Reflection: the agent reads its own journal and prices what it learned."""
    print("BEAT j: REFLECTION. Three fast payments in the journal earn a discount")
    r: list[bool] = []
    raw, flat = cli("reflect")
    st = store()
    pattern = st.get_pattern(BUYER)

    r.append(check("j", "reflection wrote a pattern entity for this buyer",
                   pattern is not None, f"pattern={pattern}"))
    if pattern is None:
        beat_result("j", "reflection", r)
        return
    r.append(check("j", "it counted at least three settled invoices",
                   pattern.payments_observed >= S.PROMPT_PAYER_MIN_PAYMENTS,
                   f"payments_observed={pattern.payments_observed}"))
    r.append(check("j", "the median is under the prompt-payer threshold",
                   pattern.median_seconds_invoice_to_payment is not None
                   and pattern.median_seconds_invoice_to_payment < S.PROMPT_PAYER_MAX_SECONDS,
                   f"median={pattern.median_seconds_invoice_to_payment}"))
    r.append(check("j", "so pays_promptly is true", pattern.pays_promptly is True,
                   f"pays_promptly={pattern.pays_promptly}"))
    r.append(check("j", "the median is measured from PAYMENT_SEEN, not inferred",
                   pattern.basis == "payment_seen", f"basis={pattern.basis!r}"))
    seen = [e for e in store().read_journal(limit=200)
            if (e.get("extra") or {}).get("decision") == events.PAYMENT_SEEN]
    r.append(check("j", "every settled invoice left one PAYMENT_SEEN event",
                   len(seen) >= pattern.payments_observed,
                   f"{len(seen)} event(s) for {pattern.payments_observed} payment(s)"))
    r.append(check("j", "each payment event names the amount, step, rail and tx",
                   all(all(k in (e.get("extra") or {}) for k in
                           ("amount", "step", "rail", "tx", "issued_at")) for e in seen),
                   str((seen[0].get("extra") if seen else {}))[:200]))
    r.append(check("j", "and reads as one sentence",
                   bool(seen) and " USDC for step " in (seen[0]["extra"]["summary"])
                   and " seen on " in seen[0]["extra"]["summary"],
                   seen[0]["extra"]["summary"] if seen else "no event"))
    r.append(check("j", "and how much of a job this buyer buys",
                   pattern.steps_per_job_median is not None,
                   f"steps_per_job_median={pattern.steps_per_job_median}"))

    # The discount is not inferred from one payment. Same buyer, same ledger,
    # a pattern with two observations: the price must be untouched.
    spec = jobtypes.get("audit").step(2)
    ledger = st.get_buyer(BUYER)
    cost = st.get_step_cost("audit", 2)
    plain, plain_reason = policy.price(spec, ledger, cost, False)
    two = S.BuyerPattern(address=BUYER, payments_observed=2,
                         median_seconds_invoice_to_payment=1.0, pays_promptly=None)
    under, under_reason = policy.price(spec, ledger, cost, False, two)
    over, over_reason = policy.price(spec, ledger, cost, False, pattern)
    r.append(check("j", "below three observations the price is unchanged",
                   under == plain and "x0.9" not in under_reason,
                   f"{under} vs {plain}: {under_reason}"))
    r.append(check("j", "at three the price drops a tenth",
                   abs(over - round(plain * 0.9, 2)) < 1e-9 and "x0.9" in over_reason,
                   f"{over} vs {plain}: {over_reason}"))
    shown = (f"{pattern.median_seconds_invoice_to_payment:.1f}"
             if pattern.median_seconds_invoice_to_payment < 10
             else f"{pattern.median_seconds_invoice_to_payment:.0f}")
    r.append(check("j", "and the reason names the median and the count",
                   f"median of {shown}s" in over_reason
                   and f"over {pattern.payments_observed} payments" in over_reason,
                   over_reason))

    # Now a real invoice, priced through the engine on a contract this buyer
    # has not audited, so the discount is the only multiplier in play.
    variant = CONTRACT.read_text(encoding="utf-8") + "\n// reflection variant\n"
    variant_path = DB_PATH.parent / "Variant.sol"
    variant_path.write_text(variant, encoding="utf-8")
    _, flat2 = cli("job", "new", str(variant_path), "--buyer", BUYER)
    r.append(check("j", "a live invoice carries the discount and says why",
                   "x0.9 because this buyer has paid within a median of" in flat2,
                   flat2[flat2.find("priced step"):][:200] if "priced step" in flat2 else flat2[:300]))
    r.append(check("j", "the invoice is 0.45 USDC, not 0.50",
                   "amount 0.45 USDC" in flat2, flat2[:400]))
    notes.append(f"beat j discount reason:\n    {over_reason}")
    notes.append(
        f"beat j payment event:\n    {seen[-1]['extra']['summary'] if seen else 'none'}"
    )
    notes.append(
        f"beat j median: {pattern.median_seconds_invoice_to_payment}s over "
        f"{pattern.payments_observed} payments, basis {pattern.basis!r}"
    )
    beat_result("j", "the agent learned a buyer pays fast, and charged less for it", r)


def beat_k() -> None:
    """The digest: one consolidation entity, and its numbers match the store."""
    print("BEAT k: DIGEST. A day counted from the journal, consolidated once")
    r: list[bool] = []
    raw, flat = cli("digest")
    st = store()
    today = digest.today()
    entity = st.get_digest(today)

    r.append(check("k", "the digest wrote one consolidation entity",
                   entity is not None, f"digest/{today}"))
    if entity is None:
        beat_result("k", "digest", r)
        return
    f = entity.figures
    r.append(check("k", "the CLI printed the day's figures",
                   "turnstyl digest for today" in flat and "consolidated as entity" in flat,
                   flat[:200]))

    # Every figure is checked against the store it was counted from.
    job_ids = digest.all_job_ids(st)
    complete = sum(1 for j in job_ids if (st.get_job_state(j) or S.JobState(
        job_id="x", buyer="x", contract_hash="x")).status == S.STATUS_COMPLETE)
    ledger = st.get_buyer(BUYER)
    r.append(check("k", "jobs opened matches the jobs in the store",
                   f["jobs_opened"] == len(job_ids),
                   f"digest={f['jobs_opened']} store={len(job_ids)}"))
    r.append(check("k", "jobs completed matches the completed job states",
                   f["jobs_completed"] == complete,
                   f"digest={f['jobs_completed']} store={complete}"))
    r.append(check("k", "defaults matches the buyer ledger",
                   f["defaults"] == ledger.defaults,
                   f"digest={f['defaults']} ledger={ledger.defaults}"))
    r.append(check("k", "USDC settled is positive and no more than the ledger",
                   0 < f["usdc_settled"] <= ledger.paid_usdc + 0.01,
                   f"digest={f['usdc_settled']} ledger={ledger.paid_usdc}"))
    r.append(check("k", "steps served from memory is counted",
                   f["steps_served_from_memory"] > 0,
                   str(f["steps_served_from_memory"])))
    r.append(check("k", "model spend is estimated from the recorded tokens",
                   f["model_spend_usd_estimated"] > 0 and f["tokens_in"] > 0,
                   f"${f['model_spend_usd_estimated']} over {f['tokens_in']} in"))
    r.append(check("k", "injection flags raised are counted",
                   f["injection_flags"] > 0, str(f["injection_flags"])))
    r.append(check("k", "refusals are counted", f["refusals"] > 0, str(f["refusals"])))
    changes = [e for e in st.read_journal(limit=200)
               if (e.get("extra") or {}).get("decision") == events.TRUST_CHANGED]
    r.append(check("k", "a tier that moved left a TRUST_CHANGED event",
                   len(changes) > 0, f"{len(changes)} event(s)"))
    r.append(check("k", "the digest counts those events, not a snapshot",
                   f["trust_changes"] == len(changes),
                   f"digest={f['trust_changes']} journal={len(changes)}"))
    r.append(check("k", "and reports the standing snapshot beside it",
                   f["buyers_above_new"] >= 0, str(f.get("buyers_above_new"))))
    r.append(check("k", "payment to output is a median over at least three payments",
                   (f["payment_to_output_observations"] >= digest.MIN_OBSERVATIONS)
                   == (f["median_seconds_payment_to_output"] is not None),
                   f"n={f['payment_to_output_observations']} "
                   f"median={f['median_seconds_payment_to_output']}"))
    r.append(check("k", "the top contracts are named, most audited first",
                   len(f["top_contracts_by_repeat_audits"]) > 0
                   and f["top_contracts_by_repeat_audits"][0]["jobs"] >= 1,
                   str(f["top_contracts_by_repeat_audits"])[:200]))

    # The second read is the point of consolidating: same day, same numbers.
    again = digest.build(st, days=1)
    r.append(check("k", "counting the same day again gives the same figures",
                   again.figures["jobs_opened"] == f["jobs_opened"]
                   and again.figures["usdc_settled"] == f["usdc_settled"],
                   f"{again.figures['jobs_opened']} vs {f['jobs_opened']}"))
    notes.append(
        "beat k digest: "
        + ", ".join(
            f"{k}={f[k]}" for k in
            ("jobs_opened", "jobs_completed", "usdc_settled", "steps_run",
             "steps_served_from_memory", "defaults", "refusals", "injection_flags",
             "trust_changes", "median_seconds_payment_to_output",
             "payment_to_output_observations")
        )
    )
    beat_result("k", "the day counted once, from what the agent wrote down", r)


def variant(name: str, note: str) -> Path:
    """A contract this buyer has not audited, so a beat buys work rather than
    reading a cache."""
    path = DB_PATH.parent / name
    path.write_text(CONTRACT.read_text(encoding="utf-8") + f"\n// {note}\n", encoding="utf-8")
    return path


def job_for(path: Path) -> str:
    """The active job for one source file, found by its hash rather than by
    being the only one open."""
    wanted = S.sha256_text(path.read_text(encoding="utf-8"))
    st = store()
    for job_id in st.get_active_jobs():
        state = st.get_job_state(job_id)
        if state is not None and state.contract_hash == wanted:
            return job_id
    raise SystemExit(f"turnstyl demo: no active job for {path}")


def beat_l() -> None:
    """A second default stops the relationship. Paying ends the stop."""
    print("BEAT l: BLOCKED and BACK. A second default blocks the buyer, and six paid steps unblock them")
    r: list[bool] = []

    # A trusted buyer takes work on credit and lets the job close without
    # settling it: the second default, and the block.
    first = variant("Blocked1.sol", "block beat, first")
    cli("job", "new", str(first), "--buyer", BUYER)
    defaulting = job_for(first)
    _, flat = cli("job", "run", defaulting)
    r.append(check("l", "a trusted buyer takes step 2 on credit",
                   "DECISION: RUN_ON_CREDIT" in flat, flat[:300]))
    for step in (3, 4):
        pay_and_run(defaulting, step, "l", r)
    ledger = store().get_buyer(BUYER)
    r.append(check("l", "the close records arrears, not a default yet",
                   ledger.defaults == 1 and len(policy.arrears(ledger)) == 1,
                   f"defaults={ledger.defaults}, arrears={len(policy.arrears(ledger))}"))
    force_default(defaulting)                 # left past its grace period
    ledger = store().get_buyer(BUYER)
    r.append(check("l", "left past grace it becomes a second default",
                   ledger.defaults == 2, f"defaults={ledger.defaults}"))
    r.append(check("l", "which blocks the buyer",
                   ledger.trust_tier == S.TRUST_BLOCKED, f"trust_tier={ledger.trust_tier}"))
    r.append(check("l", "and resets the clock that lifts a block",
                   ledger.consecutive_paid_since_block == 0,
                   f"consecutive_paid_since_block={ledger.consecutive_paid_since_block}"))

    # Blocked: the free step is still served, the paid one is not.
    second = variant("Blocked2.sol", "block beat, second")
    _, new_flat = cli("job", "new", str(second), "--buyer", BUYER)
    blocked_job = job_for(second)
    r.append(check("l", "a blocked buyer can still submit a job",
                   bool(blocked_job), blocked_job))
    r.append(check("l", "and the free scope step still runs for them",
                   "STEP 1: scope" in new_flat and "DECISION: RUN_FREE" in new_flat,
                   new_flat[:400]))
    raw, flat = cli("job", "run", blocked_job)
    refuse_line = decision_line(raw)
    r.append(check("l", "but a paid step is refused", "DECISION: REFUSE" in flat, flat[:300]))
    owed = policy.outstanding_usdc(ledger)
    r.append(check("l", "and the refusal says exactly what is required",
                   f"blocked after 2 defaults: settle {owed:.2f} USDC outstanding, then "
                   f"{S.UNBLOCK_PAID_STEPS} more consecutive paid steps to be served again"
                   in refuse_line, refuse_line))

    # Settle the debt. That is a settled paid step too, so it is the first of six.
    cli("pay", defaulting, "2")
    cli("ledger", BUYER)                       # reconciliation runs on the next read
    ledger = store().get_buyer(BUYER)
    r.append(check("l", "settling the debt clears unpaid_from_prior_jobs",
                   ledger.unpaid_from_prior_jobs == 0,
                   f"unpaid_from_prior_jobs={ledger.unpaid_from_prior_jobs}"))
    r.append(check("l", "and counts as the first of six paid steps",
                   ledger.consecutive_paid_since_block == 1,
                   f"consecutive_paid_since_block={ledger.consecutive_paid_since_block}"))
    r.append(check("l", "the buyer is still blocked until the six are done",
                   ledger.trust_tier == S.TRUST_BLOCKED, f"trust_tier={ledger.trust_tier}"))
    # With the debt gone the refusal must stop talking about a debt: saying
    # "settle 0.00 USDC outstanding" would contradict the buyer's own ledger.
    clear_terms = policy.unblock_terms(ledger)
    r.append(check("l", "with nothing outstanding the terms change wording",
                   clear_terms == (f"blocked after 2 defaults: this step must be paid "
                                   f"up front, {S.UNBLOCK_PAID_STEPS - 1} more paid "
                                   f"steps to be served normally"),
                   clear_terms))
    r.append(check("l", "and never claim a debt that is settled",
                   "outstanding" not in clear_terms and "0.00 USDC" not in clear_terms,
                   clear_terms))
    fresh = variant("Blocked1b.sol", "block beat, wording")
    _, wording_flat = cli("job", "new", str(fresh), "--buyer", BUYER)
    wording_raw, _ = cli("job", "run", job_for(fresh))
    no_debt_line = decision_line(wording_raw)
    r.append(check("l", "the live refusal uses that wording",
                   "this step must be paid up front" in no_debt_line
                   and "settle 0.00" not in no_debt_line, no_debt_line))
    notes.append(f"beat l blocked REFUSE line (no debt):\n    {no_debt_line}")

    # Five more paid steps, bought up front. A step already paid for is served
    # while blocked: that is what the count is earned from.
    for step in (2, 3, 4):
        pay_and_run(blocked_job, step, "l", r)
    ledger = store().get_buyer(BUYER)
    r.append(check("l", "four of six after one job bought up front",
                   ledger.consecutive_paid_since_block == 4,
                   f"consecutive_paid_since_block={ledger.consecutive_paid_since_block}"))
    r.append(check("l", "still blocked at four of six",
                   ledger.trust_tier == S.TRUST_BLOCKED, f"trust_tier={ledger.trust_tier}"))

    third = variant("Blocked3.sol", "block beat, third")
    cli("job", "new", str(third), "--buyer", BUYER)
    last_job = job_for(third)
    for step in (2, 3):
        pay_and_run(last_job, step, "l", r)
    ledger = store().get_buyer(BUYER)
    r.append(check("l", "six consecutive paid steps since the block",
                   ledger.consecutive_paid_since_block == S.UNBLOCK_PAID_STEPS,
                   f"consecutive_paid_since_block={ledger.consecutive_paid_since_block}"))
    r.append(check("l", "so the tier returns to new, not straight to trusted",
                   ledger.trust_tier == S.TRUST_NEW,
                   f"trust_tier={ledger.trust_tier}, defaults={ledger.defaults}"))

    # The seventh paid step runs with no block language at all.
    raw = pay_and_run(last_job, 4, "l", r)
    line = decision_line(raw)
    r.append(check("l", "and the seventh paid step runs normally",
                   "blocked" not in line, line))
    ledger = store().get_buyer(BUYER)
    r.append(check("l", "a blocked buyer who paid their way back can earn credit again",
                   policy.jobs_until_credit(ledger) >= 0
                   and ledger.trust_tier in (S.TRUST_NEW, S.TRUST_TRUSTED),
                   f"trust_tier={ledger.trust_tier}, "
                   f"completed_paid_jobs={ledger.completed_paid_jobs}, "
                   f"jobs_until_credit={policy.jobs_until_credit(ledger)}"))
    notes.append(f"beat l blocked REFUSE line:\n    {refuse_line}")
    beat_result("l", "blocked, then paid back to new", r)


def beat_m(prior_jobs: set[str]) -> str:
    print("BEAT m: DELETE TEST. Wipe the database and watch the agent forget")
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
        check("m", "a brand new job id was issued", new_job not in prior_jobs, f"job={new_job}"),
        check("m", "step 1 ran again", "STEP 1: scope" in flat and "SCOPE (contract" in flat),
        check("m", "step 1 was NOT served from memory", "from memory (cached)" not in flat, flat[:400]),
        check("m", "step 2 is invoiced at 0.50 USDC again",
              "step 2 (findings)" in flat and "amount 0.50 USDC" in flat, flat[:500]),
        check("m", "buyer trust_tier is back to new", ledger.trust_tier == S.TRUST_NEW,
              f"trust_tier={ledger.trust_tier}"),
        check("m", "buyer paid history is gone", ledger.paid_steps == 0 and ledger.completed_paid_jobs == 0,
              f"paid_steps={ledger.paid_steps}, completed_paid_jobs={ledger.completed_paid_jobs}"),
    ]
    beat_result("m", "memory deleted, buyer treated as a stranger", r)
    if all(r):
        print("DOUBLE CHARGE REPRODUCED: memory deleted, buyer re-invoiced 0.50 for paid work\n")
    notes.append(f"beat m DECISION line:\n    {line}")
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
    sixth = beat_g(credit_job)
    seen.add(sixth)
    seen.add(beat_h(sixth))
    seen.update(store().get_active_jobs())
    beat_i()
    beat_j()
    beat_k()
    beat_l()
    seen.update(store().get_active_jobs())
    beat_m(seen)

    print("-" * 72)
    for note in notes:
        print(note)
    print("-" * 72)
    if failures:
        print(f"RESULT: FAIL — {len(failures)} check(s) failed")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — all 13 beats passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
