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
from turnstyl import digest  # noqa: E402
from turnstyl import injection  # noqa: E402
from turnstyl import policy  # noqa: E402
from turnstyl import schema as S  # noqa: E402
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
    r.append(check("j", "it records how the median was measured", bool(pattern.basis),
                   pattern.basis))
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
    r.append(check("j", "and the reason names the median and the count",
                   f"median of {pattern.median_seconds_invoice_to_payment:.0f}s" in over_reason
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
    notes.append(
        f"beat j discount reason:\n    {over_reason}"
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
             "steps_served_from_memory", "defaults", "refusals", "injection_flags")
        )
    )
    beat_result("k", "the day counted once, from what the agent wrote down", r)


def beat_l(prior_jobs: set[str]) -> str:
    print("BEAT l: DELETE TEST. Wipe the database and watch the agent forget")
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
        check("l", "a brand new job id was issued", new_job not in prior_jobs, f"job={new_job}"),
        check("l", "step 1 ran again", "STEP 1: scope" in flat and "SCOPE (contract" in flat),
        check("l", "step 1 was NOT served from memory", "from memory (cached)" not in flat, flat[:400]),
        check("l", "step 2 is invoiced at 0.50 USDC again",
              "step 2 (findings)" in flat and "amount 0.50 USDC" in flat, flat[:500]),
        check("l", "buyer trust_tier is back to new", ledger.trust_tier == S.TRUST_NEW,
              f"trust_tier={ledger.trust_tier}"),
        check("l", "buyer paid history is gone", ledger.paid_steps == 0 and ledger.completed_paid_jobs == 0,
              f"paid_steps={ledger.paid_steps}, completed_paid_jobs={ledger.completed_paid_jobs}"),
    ]
    beat_result("l", "memory deleted, buyer treated as a stranger", r)
    if all(r):
        print("DOUBLE CHARGE REPRODUCED: memory deleted, buyer re-invoiced 0.50 for paid work\n")
    notes.append(f"beat l DECISION line:\n    {line}")
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
    seen.update(store().get_active_jobs())
    beat_l(seen)

    print("-" * 72)
    for note in notes:
        print(note)
    print("-" * 72)
    if failures:
        print(f"RESULT: FAIL — {len(failures)} check(s) failed")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — all 12 beats passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
