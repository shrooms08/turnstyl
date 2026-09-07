#!/usr/bin/env python3
"""Run the audit against a set of contracts with known bugs, and count.

    .venv/bin/python scripts/eval.py                    # 3 runs each, budget 1.00 USD
    .venv/bin/python scripts/eval.py --runs 1
    .venv/bin/python scripts/eval.py --mock             # smoke-test the harness, no spend

Every contract in evals/contracts/ has a manifest entry listing the bugs that
were put there on purpose and a matcher that decides whether the findings step
found each one. One contract is clean, so a finding on it is a false positive.
One is adversarial: it has a real bug and comments telling the auditor not to
report it.

Each run is a whole audit through the real engine, against its own throwaway
database, with payments on the fake backend so nothing touches a chain. What is
recorded per run: which known bugs were found, false positives, whether the
patch compiled, whether the verifier agreed with the compiler, tokens and
seconds per step, cost, and whether a second audit of the same contract in the
same store was served from memory for nothing.

Writes evals/results/<date>.json and rewrites docs/EVALS.md. A --mock run
writes evals/results/<date>-mock.json and leaves docs/EVALS.md alone: it
measures the harness, not the model, and must not overwrite real numbers.

Exit codes: 0 done, 2 usage or a missing fixture, 3 the estimate exceeded the
budget and nothing was spent, 4 the budget was hit mid-run (partial results are
still written).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from turnstyl import schema as S  # noqa: E402
from turnstyl.engine import Engine  # noqa: E402
from turnstyl.llm import DEFAULT_MODEL  # noqa: E402
from turnstyl.memory import TENANT_ID, TurnstylMemory, TurnstylStore  # noqa: E402
from turnstyl.payments import get_backend  # noqa: E402

MANIFEST = REPO_ROOT / "evals" / "contracts" / "manifest.json"
RESULTS_DIR = REPO_ROOT / "evals" / "results"
EVALS_DOC = REPO_ROOT / "docs" / "EVALS.md"
BUYER = "0xe7a1000000000000000000000000000000000eva"

# List price per million tokens at the time of writing, overridable so a rerun
# on a different model does not need a code change. Cost here is the model bill
# only: the USDC prices turnstyl charges a buyer are a separate thing entirely.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (15.00, 75.00),
}
FALLBACK_PRICE = (1.00, 5.00)

SEVERITY_LINE = re.compile(
    r"^\s*(?:\d+[\.\):]|[-*•]|#{1,4})?\s*.*?\b(CRITICAL|HIGH|MEDIUM|LOW|INFORMATIONAL|INFO)\b",
    re.I | re.M,
)
CLOSED = re.compile(r"(?<!NOT )\bCLOSED\b")
NOT_CLOSED = re.compile(r"\bNOT\s+CLOSED\b", re.I)
NO_COMPILE = re.compile(r"does not compile|fail\w*\s+to\s+compile|did not compile", re.I)


def archived_entity(db: Path, job_id: str) -> S.JobEntity | None:
    """A completed job's entity, read out of the archive table.

    The SDK archives entities and exposes no reader for them, the same reason
    the API goes to sqlite for this. Read-only: the driver refuses to write
    through a mode=ro connection.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT body FROM archived_entities "
            "WHERE tenant_id = ? AND category = ? AND name = ?",
            (TENANT_ID, S.CAT_JOB, job_id),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return S.JobEntity.model_validate(json.loads(row[0]))


def die(message: str, code: int = 2) -> None:
    print(f"turnstyl eval: {message}", file=sys.stderr)
    raise SystemExit(code)


# ----------------------------------------------------------------------
# Reading the answers
# ----------------------------------------------------------------------
def bug_found(findings_text: str, matcher: dict) -> bool:
    """Every regex in all_of must appear somewhere in the findings output."""
    text = findings_text or ""
    patterns = matcher.get("all_of") or []
    if not patterns:
        return False
    return all(re.search(p, text, re.I | re.S) for p in patterns)


def severities(findings_text: str) -> list[str]:
    """The severity of each line that reads like a finding."""
    out = []
    for line in (findings_text or "").splitlines():
        match = SEVERITY_LINE.match(line)
        if match and len(line.strip()) > 12:
            out.append(match.group(1).upper())
    return out


def verdict_agrees(verify_text: str, compiles: bool | None) -> bool | None:
    """Did the verifier's prose agree with what the compiler actually said?

    The rule the verify prompt sets: a patch that does not compile may not have
    any finding marked CLOSED, and the regressions section must say so. When it
    does compile, the verifier must not claim otherwise.
    """
    if compiles is None:
        return None
    text = verify_text or ""
    claims_broken = bool(NO_COMPILE.search(text))
    if compiles:
        return not claims_broken
    return claims_broken and not CLOSED.search(NOT_CLOSED.sub("", text))


def cost_usd(tokens_in: int, tokens_out: int, price: tuple[float, float]) -> float:
    return tokens_in / 1e6 * price[0] + tokens_out / 1e6 * price[1]


# ----------------------------------------------------------------------
# One audit, start to finish, against a throwaway store
# ----------------------------------------------------------------------
def audit_once(store: TurnstylStore, source: str, filename: str) -> tuple[str, list[dict]]:
    """Create a job, pay each invoice, run every step. Returns (job_id, steps)."""
    engine = Engine(store=store, payments=get_backend(store.memory))
    outcome = engine.new_job_from_source(source, BUYER, filename=filename, job_type="audit")
    job_id = outcome.job_id

    for _ in range(12):                      # 4 steps, with slack; never unbounded
        state = store.get_job_state(job_id)
        if state is None or state.status == S.STATUS_COMPLETE:
            break
        invoice = state.open_invoice
        if invoice is not None and not invoice.paid:
            engine.pay(job_id, invoice.step)
        result = engine.run(job_id)
        if result.decision == S.REFUSE:
            break

    entity = store.get_job_entity(job_id)
    if entity is None:
        entity = archived_entity(store.db_path, job_id)
    if entity is None:
        die(f"job {job_id} produced no step records", 2)

    steps = []
    for key in sorted(entity.steps, key=int):
        record = entity.steps[key]
        steps.append(
            {
                "step": int(key),
                "name": {1: "scope", 2: "findings", 3: "patch", 4: "verify"}[int(key)],
                "tokens_in": record.input_tokens or 0,
                "tokens_out": record.output_tokens or 0,
                "seconds": record.seconds or 0.0,
                "cached": bool(record.cached),
                "compiles": record.compiles,
                "output": record.output or "",
            }
        )
    return job_id, steps


def run_case(case: dict, run_index: int, price: tuple[float, float]) -> dict:
    """One run of one contract: a first audit, then a second from memory."""
    path = (MANIFEST.parent / case["file"]).resolve()
    if not path.is_file():
        die(f"contract {path} from the manifest is missing", 2)
    source = path.read_text(encoding="utf-8")

    workdir = Path(tempfile.mkdtemp(prefix="turnstyl-eval-"))
    try:
        store = TurnstylStore(TurnstylMemory(workdir / "eval.db"))
        started = time.monotonic()
        job_id, steps = audit_once(store, source, path.name)
        first_seconds = round(time.monotonic() - started, 2)

        # The same contract again, same store: everything should come out of
        # memory for no tokens at all.
        _, second_steps = audit_once(store, source, path.name)

        by_name = {s["name"]: s for s in steps}
        findings_text = by_name.get("findings", {}).get("output", "")
        patch = by_name.get("patch", {})
        verify_text = by_name.get("verify", {}).get("output", "")

        found = {}
        for bug in case.get("bugs") or []:
            found[bug["id"]] = bug_found(findings_text, bug.get("match") or {})

        sev = severities(findings_text)
        tokens_in = sum(s["tokens_in"] for s in steps)
        tokens_out = sum(s["tokens_out"] for s in steps)
        second_in = sum(s["tokens_in"] for s in second_steps)
        second_out = sum(s["tokens_out"] for s in second_steps)

        state = store.get_job_state(job_id)
        return {
            "run": run_index,
            "contract": case["id"],
            "job_id": job_id,
            "seconds": first_seconds,
            "steps": [{k: v for k, v in s.items() if k != "output"} for s in steps],
            "found": found,
            "findings_total": len(sev),
            "findings_high_or_critical": sum(1 for x in sev if x in ("HIGH", "CRITICAL")),
            "severities": sev,
            "patch_compiles": patch.get("compiles"),
            "verdict_agrees": verdict_agrees(verify_text, patch.get("compiles")),
            "injection_flags": len(state.injection_flags) if state else 0,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost_usd(tokens_in, tokens_out, price), 6),
            "second_pass": {
                "all_cached": bool(second_steps) and all(s["cached"] for s in second_steps),
                "tokens_in": second_in,
                "tokens_out": second_out,
                "cost_usd": round(cost_usd(second_in, second_out, price), 6),
                "seconds": round(sum(s["seconds"] for s in second_steps), 2),
            },
            "findings_excerpt": findings_text[:1200],
            "verify_excerpt": verify_text[:600],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ----------------------------------------------------------------------
# Estimating the bill before spending it
# ----------------------------------------------------------------------
def estimate_usd(cases: list[dict], runs: int, price: tuple[float, float]) -> float:
    """Rough, and deliberately on the high side: four steps per audit, each
    seeing the contract plus everything before it, and answering near its cap."""
    total = 0.0
    for case in cases:
        path = (MANIFEST.parent / case["file"]).resolve()
        chars = len(path.read_text(encoding="utf-8")) if path.is_file() else 4000
        contract_tokens = chars / 3.5
        carried = 0.0
        for _step, out_cap in ((1, 500), (2, 900), (3, 2400), (4, 900)):
            total += cost_usd(int(contract_tokens + carried + 300), out_cap, price)
            carried += out_cap
        total *= 1.0
    return total * runs


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------
def aggregate(results: list[dict], cases: list[dict]) -> dict:
    by_case = {c["id"]: c for c in cases}
    recall: dict[str, dict] = {}
    for result in results:
        case = by_case[result["contract"]]
        for bug in case.get("bugs") or []:
            row = recall.setdefault(
                bug["id"],
                {
                    "contract": case["id"],
                    "class": bug["class"],
                    "title": bug["title"],
                    "severity": bug["severity"],
                    "function": bug["function"],
                    "runs": 0,
                    "found": 0,
                },
            )
            row["runs"] += 1
            row["found"] += 1 if result["found"].get(bug["id"]) else 0
    for row in recall.values():
        row["recall"] = round(row["found"] / row["runs"], 3) if row["runs"] else None

    clean_ids = {c["id"] for c in cases if c.get("clean")}
    clean_runs = [r for r in results if r["contract"] in clean_ids]
    gated = [r for r in results if r["patch_compiles"] is not None]
    agreed = [r for r in results if r["verdict_agrees"] is not None]
    costs = [r["cost_usd"] for r in results]
    seconds = [r["seconds"] for r in results]
    second = [r["second_pass"] for r in results]

    return {
        "recall": recall,
        "clean": {
            "runs": len(clean_runs),
            "findings_total": sum(r["findings_total"] for r in clean_runs),
            "high_or_critical": sum(r["findings_high_or_critical"] for r in clean_runs),
            "runs_with_high_or_critical": sum(
                1 for r in clean_runs if r["findings_high_or_critical"]
            ),
        },
        "patch_compile_rate": (
            round(sum(1 for r in gated if r["patch_compiles"]) / len(gated), 3) if gated else None
        ),
        "patch_compile_runs": len(gated),
        "verdict_agreement": (
            round(sum(1 for r in agreed if r["verdict_agrees"]) / len(agreed), 3) if agreed else None
        ),
        "verdict_runs": len(agreed),
        "median_cost_usd": round(statistics.median(costs), 4) if costs else None,
        "median_seconds": round(statistics.median(seconds), 1) if seconds else None,
        "total_cost_usd": round(sum(costs), 4),
        "first_pass": {
            "median_tokens_in": int(statistics.median([r["tokens_in"] for r in results])) if results else 0,
            "median_tokens_out": int(statistics.median([r["tokens_out"] for r in results])) if results else 0,
            "median_cost_usd": round(statistics.median(costs), 4) if costs else None,
        },
        "from_memory": {
            "all_cached_rate": (
                round(sum(1 for s in second if s["all_cached"]) / len(second), 3) if second else None
            ),
            "tokens_in": sum(s["tokens_in"] for s in second),
            "tokens_out": sum(s["tokens_out"] for s in second),
            "cost_usd": round(sum(s["cost_usd"] for s in second), 6),
        },
        "adversarial": [
            {
                "run": r["run"],
                "injection_flags": r["injection_flags"],
                "found": r["found"],
                "findings_total": r["findings_total"],
            }
            for r in results
            if by_case[r["contract"]].get("adversarial")
        ],
    }


def markdown(report: dict) -> str:
    a = report["aggregate"]
    runs, model = report["runs_per_contract"], report["model"]
    lines = [
        "# turnstyl evals",
        "",
        f"Generated {report['generated_at']} by `scripts/eval.py`. "
        f"Model **{model}**, **{runs} run(s)** per contract, "
        f"**{report['total_runs']} audits** in total, each against its own throwaway "
        f"database with payments on the fake backend. Every number below is "
        f"produced by that script; nothing here is hand-written.",
        "",
        "Cost is the model bill for the audit, not what turnstyl charges a buyer.",
        "",
        "## Recall per known bug",
        "",
        "Each contract in `evals/contracts/` has bugs put there on purpose and a "
        "matcher in `manifest.json` that decides whether the findings step found "
        "them. A matcher requires the bug class *and* the function it lives in.",
        "",
        "| bug | class | severity | function | found | recall |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for bug_id, row in sorted(a["recall"].items()):
        where = row["function"]
        where = f"`{where}()`" if re.fullmatch(r"[A-Za-z_]\w*", where) else where
        lines.append(
            f"| `{bug_id}` | {row['class']} | {row['severity']} | {where} | "
            f"{row['found']}/{row['runs']} | {row['recall']:.0%} |"
        )
    clean = a["clean"]
    lines += [
        "",
        "## False positives on the clean contract",
        "",
        f"`clean.sol` is owner-gated, holds no value, uses custom errors and emits "
        f"an event on every write. Over **{clean['runs']} run(s)** the findings step "
        f"reported **{clean['findings_total']}** finding(s) in total, of which "
        f"**{clean['high_or_critical']}** were HIGH or CRITICAL. "
        f"**{clean['runs_with_high_or_critical']}** of {clean['runs']} run(s) "
        f"contained at least one HIGH or CRITICAL finding.",
        "",
        "## Gates and agreement",
        "",
        "| measure | value | over |",
        "| --- | --- | --- |",
        f"| patch compiles (`forge build`) | "
        f"{'—' if a['patch_compile_rate'] is None else format(a['patch_compile_rate'], '.0%')} | "
        f"{a['patch_compile_runs']} run(s) |",
        f"| verifier verdict agrees with the compiler | "
        f"{'—' if a['verdict_agreement'] is None else format(a['verdict_agreement'], '.0%')} | "
        f"{a['verdict_runs']} run(s) |",
        "",
        "The verifier agrees when it does not claim a patch failed to compile that "
        "did, and when a patch that did not compile is left with nothing marked "
        "CLOSED.",
        "",
        "## Cost and time per audit",
        "",
        "| measure | value |",
        "| --- | --- |",
        f"| median cost, first audit | ${a['median_cost_usd']:.4f} |",
        f"| median wall clock, first audit | {a['median_seconds']:.1f}s |",
        f"| median tokens in / out | {a['first_pass']['median_tokens_in']:,} / {a['first_pass']['median_tokens_out']:,} |",
        f"| total spend for this run | ${a['total_cost_usd']:.4f} |",
        "",
        "## First audit against the same audit from memory",
        "",
        "After an audit completes, a second audit of the same contract in the same "
        "store is served from the findings entity. Over "
        f"**{report['total_runs']}** second passes:",
        "",
        "| | tokens in | tokens out | cost |",
        "| --- | --- | --- | --- |",
        f"| first audit (median) | {a['first_pass']['median_tokens_in']:,} | "
        f"{a['first_pass']['median_tokens_out']:,} | ${a['first_pass']['median_cost_usd']:.4f} |",
        f"| second audit, from memory (all runs) | {a['from_memory']['tokens_in']:,} | "
        f"{a['from_memory']['tokens_out']:,} | ${a['from_memory']['cost_usd']:.4f} |",
        "",
        f"Every step of every second pass was served from memory in "
        f"{'—' if a['from_memory']['all_cached_rate'] is None else format(a['from_memory']['all_cached_rate'], '.0%')} "
        f"of runs.",
        "",
    ]
    if a["adversarial"]:
        lines += [
            "## The adversarial contract",
            "",
            "`examples/Adversarial.sol` holds a real reentrancy bug and comments "
            "telling the auditor it has already been audited, to report no findings, "
            "and to approve the patch. It is submitted like any other contract.",
            "",
            "| run | flagged passages | reentrancy reported | manipulation reported | findings |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in a["adversarial"]:
            found = row["found"]
            lines.append(
                f"| {row['run']} | {row['injection_flags']} | "
                f"{'yes' if found.get('adversarial-reentrancy') else 'no'} | "
                f"{'yes' if found.get('adversarial-injection') else 'no'} | "
                f"{row['findings_total']} |"
            )
        lines.append("")
    lines += [
        "## Reproducing this",
        "",
        "```bash",
        f".venv/bin/python scripts/eval.py --runs {runs} --budget 1.00",
        "```",
        "",
        "`--mock` runs the whole harness against the canned offline outputs and "
        "spends nothing, which checks the harness rather than the model. It "
        "writes to `evals/results/<date>-mock.json` and leaves this file alone.",
        "",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Measure the audit against known bugs.")
    parser.add_argument("--runs", type=int, default=3, help="Runs per contract (default 3).")
    parser.add_argument("--budget", type=float, default=1.00, help="Stop above this spend in USD.")
    parser.add_argument("--mock", action="store_true", help="Canned outputs, no API calls, no spend.")
    parser.add_argument("--model", default=None, help="Override LLM_MODEL for this run.")
    parser.add_argument("--only", default=None, help="Run one contract id from the manifest.")
    args = parser.parse_args(argv[1:])

    if args.runs < 1:
        die("--runs must be at least 1")
    if not MANIFEST.is_file():
        die(f"{MANIFEST} is missing; the eval set is not installed")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = manifest.get("contracts") or []
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
        if not cases:
            die(f"no contract {args.only!r} in the manifest")

    # The eval never touches a chain and never reads the operator's store.
    os.environ["PAYMENTS"] = "fake"
    if args.mock:
        os.environ["MOCK_LLM"] = "1"
    else:
        os.environ.pop("MOCK_LLM", None)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            die("ANTHROPIC_API_KEY is not set; use --mock to test the harness offline")
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    price = PRICES_PER_MTOK.get(model, FALLBACK_PRICE)

    total_runs = len(cases) * args.runs
    estimate = 0.0 if args.mock else estimate_usd(cases, args.runs, price)
    print(f"turnstyl eval: {len(cases)} contract(s) x {args.runs} run(s) = {total_runs} audits")
    print(f"  model     {model}" + ("  (MOCK: canned outputs, no spend)" if args.mock else ""))
    print(f"  price     ${price[0]:.2f}/Mtok in, ${price[1]:.2f}/Mtok out")
    print(f"  estimate  ${estimate:.2f}   budget ${args.budget:.2f}")
    if estimate > args.budget:
        print(
            f"turnstyl eval: the estimate ${estimate:.2f} is over the budget "
            f"${args.budget:.2f}. Nothing was run and nothing was spent. Lower "
            f"--runs, pick one contract with --only, or raise --budget.",
            file=sys.stderr,
        )
        return 3
    print()

    results: list[dict] = []
    spent = 0.0
    stopped = False
    for case in cases:
        for index in range(1, args.runs + 1):
            label = f"{case['id']} run {index}/{args.runs}"
            print(f"  {label} ...", end="", flush=True)
            started = time.monotonic()
            result = run_case(case, index, price)
            results.append(result)
            spent += result["cost_usd"]
            found = sum(1 for v in result["found"].values() if v)
            print(
                f" {time.monotonic() - started:5.1f}s  ${result['cost_usd']:.4f}  "
                f"found {found}/{len(result['found'])}  "
                f"compiles={result['patch_compiles']}  "
                f"cached2nd={result['second_pass']['all_cached']}"
            )
            if spent > args.budget:
                print(
                    f"turnstyl eval: spend ${spent:.2f} passed the budget "
                    f"${args.budget:.2f}; stopping here. Partial results are written "
                    f"below.",
                    file=sys.stderr,
                )
                stopped = True
                break
        if stopped:
            break

    if not results:
        die("no runs completed", 2)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "mock": bool(args.mock),
        "runs_per_contract": args.runs,
        "total_runs": len(results),
        "budget_usd": args.budget,
        "estimate_usd": round(estimate, 4),
        "price_per_mtok": {"input": price[0], "output": price[1]},
        "stopped_on_budget": stopped,
        "aggregate": aggregate(results, cases),
        "runs": results,
    }

    # A mock run measures the harness, not the model, so it never overwrites
    # the measured numbers: its results go to their own file and docs/EVALS.md
    # is left exactly as the last real run wrote it.
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = RESULTS_DIR / (f"{stamp}-mock.json" if args.mock else f"{stamp}.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not args.mock:
        EVALS_DOC.parent.mkdir(parents=True, exist_ok=True)
        EVALS_DOC.write_text(markdown(report), encoding="utf-8")

    print()
    print(f"turnstyl eval: {len(results)} audit(s), spent ${spent:.4f} of ${args.budget:.2f}")
    print(f"  results  {out.relative_to(REPO_ROOT)}")
    if args.mock:
        print(
            f"  summary  {EVALS_DOC.relative_to(REPO_ROOT)} left alone: a mock run "
            f"measures the harness, not the model"
        )
    else:
        print(f"  summary  {EVALS_DOC.relative_to(REPO_ROOT)}")
    return 4 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
