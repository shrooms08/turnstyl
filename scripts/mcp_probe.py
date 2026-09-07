#!/usr/bin/env python3
"""Drive the turnstyl MCP server over stdio and check every tool's shape.

Called by scripts/test_mcp.sh, which starts a throwaway turnstyl for it to buy
from. Prints one PASS or FAIL line per check and exits non-zero if any failed.

    .venv/bin/python scripts/mcp_probe.py <api_base>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER = str(REPO_ROOT / ".venv" / "bin" / "turnstyl-mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, detail: str = "") -> None:
    print(f"  PASS {label}" + (f" ({detail})" if detail else ""))


def bad(label: str, detail: str = "") -> None:
    print(f"  FAIL {label}")
    if detail:
        print(f"       {detail}")
    FAILURES.append(label)


def check(label: str, condition: bool, detail: str = "") -> bool:
    (ok if condition else bad)(label, detail if not condition else "")
    return bool(condition)


async def call(session: ClientSession, name: str, args: dict | None = None) -> dict:
    """Call a tool and return its structured content."""
    result = await session.call_tool(name, args or {})
    if result.structuredContent is not None:
        return result.structuredContent
    text = "".join(c.text for c in result.content if getattr(c, "text", None))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"summary": text, "ok": False}


def params(api: str, *, wallet: bool, cwd: str | None = None) -> StdioServerParameters:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TURNSTYL_API": api,
        "HOME": os.environ.get("HOME", ""),
    }
    if wallet:
        key = os.environ.get("BUYER_PRIVATE_KEY")
        if not key:
            sys.exit(
                "turnstyl mcp probe: BUYER_PRIVATE_KEY is not set; source .env "
                "before running this."
            )
        env["BUYER_PRIVATE_KEY"] = key
    return StdioServerParameters(command=SERVER, args=[], env=env, cwd=cwd)


async def with_wallet(api: str) -> None:
    async with stdio_client(params(api, wallet=True)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name: t for t in (await session.list_tools()).tools}
            check(
                "the server offers all eight tools with a wallet",
                len(tools) == 8,
                f"got {sorted(tools)}",
            )
            check(
                "the paying tool is registered when a key is present",
                "turnstyl_pay_and_run" in tools,
            )
            pay = tools.get("turnstyl_pay_and_run")
            check(
                "the paying tool says it spends money in its first line",
                bool(pay) and (pay.description or "").splitlines()[0].startswith(
                    "SPENDS REAL MONEY"
                ),
                (pay.description or "").splitlines()[0] if pay else "missing",
            )
            check(
                "the paying tool is annotated destructive",
                bool(pay) and pay.annotations and pay.annotations.destructiveHint is True,
            )
            check(
                "max_usdc is required, with no default",
                bool(pay)
                and "max_usdc" in (pay.inputSchema.get("required") or [])
                and "default" not in (pay.inputSchema["properties"]["max_usdc"]),
                json.dumps(pay.inputSchema)[:200] if pay else "",
            )
            check(
                "every read-only tool is annotated read-only",
                all(
                    tools[n].annotations.readOnlyHint is True
                    for n in tools
                    if n not in ("turnstyl_pay_and_run", "turnstyl_submit")
                ),
            )
            check(
                "every tool has structured output declared",
                all(t.outputSchema for t in tools.values()),
            )

            # ---- services -------------------------------------------------
            services = await call(session, "turnstyl_services")
            check("turnstyl_services returns ok", services.get("ok") is True, str(services)[:200])
            check(
                "turnstyl_services lists both services with prices",
                len(services.get("services") or []) == 2
                and all("steps" in s and "total_usdc" in s for s in services["services"]),
                json.dumps(services.get("services"))[:200],
            )
            check(
                "turnstyl_services names the default",
                services.get("default") == "audit",
                str(services.get("default")),
            )
            check(
                "turnstyl_services has a one-line summary",
                bool(services.get("summary")) and "\n" not in services["summary"],
                str(services.get("summary"))[:120],
            )

            # ---- status ---------------------------------------------------
            status = await call(session, "turnstyl_status")
            check("turnstyl_status returns ok", status.get("ok") is True, str(status)[:200])
            check(
                "turnstyl_status reports the fake backend",
                status.get("payments_backend") == "fake",
                str(status.get("payments_backend")),
            )
            check(
                "turnstyl_status says x402 is unavailable and why",
                status.get("x402", {}).get("enabled") is False
                and "fake" in (status["x402"].get("reason") or ""),
                json.dumps(status.get("x402")),
            )
            check(
                "turnstyl_status carries the public stats",
                isinstance(status.get("stats"), dict)
                and {"jobs", "buyers", "decisions"} <= set(status["stats"]),
                json.dumps(status.get("stats"))[:160],
            )
            check(
                "turnstyl_status reports the buyer address, never the key",
                bool(status.get("buyer_address"))
                and status["buyer_address"].startswith("0x")
                and os.environ["BUYER_PRIVATE_KEY"].lower().lstrip("0x")
                not in json.dumps(status).lower(),
            )

            # ---- submit ---------------------------------------------------
            source = (REPO_ROOT / "examples" / "Vault.sol").read_text(encoding="utf-8")
            submitted = await call(
                session, "turnstyl_submit", {"source": source, "job_type": "audit"}
            )
            check("turnstyl_submit returns ok", submitted.get("ok") is True, str(submitted)[:240])
            job_id = submitted.get("job_id") or ""
            check("turnstyl_submit returns a job id", bool(job_id), job_id)
            check(
                "turnstyl_submit returns the free scope output",
                bool(submitted.get("scope")) and "SCOPE" in (submitted["scope"] or ""),
                str(submitted.get("scope"))[:120],
            )
            check(
                "turnstyl_submit returns the next invoice at 0.50 USDC",
                (submitted.get("next_invoice") or {}).get("step") == 2
                and abs(float(submitted["next_invoice"]["amount_usdc"]) - 0.50) < 1e-9,
                json.dumps(submitted.get("next_invoice")),
            )
            check(
                "turnstyl_submit says whether credit applies",
                isinstance(submitted.get("credit"), dict)
                and "applies" in submitted["credit"]
                and submitted["credit"].get("trust_tier") is not None,
                json.dumps(submitted.get("credit")),
            )

            # ---- quote ----------------------------------------------------
            quote = await call(session, "turnstyl_quote", {"job_id": job_id})
            check("turnstyl_quote returns ok", quote.get("ok") is True, str(quote)[:200])
            check(
                "turnstyl_quote prices the open invoice",
                (quote.get("invoice") or {}).get("step") == 2
                and abs(float(quote["invoice"]["amount_usdc"]) - 0.50) < 1e-9,
                json.dumps(quote.get("invoice"))[:200],
            )
            check(
                "turnstyl_quote carries the price reason",
                bool((quote.get("invoice") or {}).get("price_reason"))
                and "base 0.50" in quote["invoice"]["price_reason"],
                str((quote.get("invoice") or {}).get("price_reason"))[:140],
            )
            check(
                "turnstyl_quote reports trust and jobs until credit",
                (quote.get("trust") or {}).get("tier") is not None
                and quote["trust"].get("jobs_until_credit") is not None,
                json.dumps(quote.get("trust"))[:160],
            )

            # ---- pay: the limit is real ------------------------------------
            refused = await call(
                session,
                "turnstyl_pay_and_run",
                {"job_id": job_id, "max_usdc": 0.10},
            )
            check(
                "turnstyl_pay_and_run refuses an invoice above max_usdc",
                refused.get("ok") is False and "refusing to pay" in refused.get("error", ""),
                str(refused)[:240],
            )
            check(
                "the refusal says nothing was spent and what limit to raise",
                "Nothing was spent" in refused.get("error", "")
                and "0.50" in refused.get("error", ""),
                refused.get("error", "")[:200],
            )
            still = await call(session, "turnstyl_quote", {"job_id": job_id})
            check(
                "the invoice is still open after the refusal",
                (still.get("invoice") or {}).get("step") == 2,
                json.dumps(still.get("invoice"))[:160],
            )

            # ---- pay: through the simulate path ----------------------------
            paid = await call(
                session,
                "turnstyl_pay_and_run",
                {"job_id": job_id, "max_usdc": 1.00},
            )
            check("turnstyl_pay_and_run settles the invoice", paid.get("paid") is True, str(paid)[:240])
            check("the agent ran the paid step", paid.get("ran") is True, str(paid.get("summary"))[:200])
            check(
                "the paid step's output comes back",
                bool(paid.get("output")) and "FINDINGS" in (paid.get("output") or ""),
                str(paid.get("output"))[:120],
            )
            check(
                "the settlement says it was simulated, not x402",
                (paid.get("settlement") or {}).get("method") == "simulated"
                and "x402 is unavailable" in (paid["settlement"].get("note") or ""),
                json.dumps(paid.get("settlement"))[:240],
            )
            check(
                "the next invoice comes back with the work",
                (paid.get("next_invoice") or {}).get("step") == 3,
                json.dumps(paid.get("next_invoice")),
            )

            # ---- job ------------------------------------------------------
            job = await call(session, "turnstyl_job", {"job_id": job_id})
            check("turnstyl_job returns ok", job.get("ok") is True, str(job)[:200])
            check(
                "turnstyl_job returns every step",
                len(job.get("steps") or []) == 4,
                str(len(job.get("steps") or [])),
            )
            check(
                "turnstyl_job returns the outputs this wallet paid for",
                job.get("readable") is True
                and any(s.get("output") for s in job["steps"]),
            )
            check(
                "turnstyl_job returns decisions as summary sentences",
                bool(job.get("decisions"))
                and all(d.get("summary") for d in job["decisions"]),
                json.dumps((job.get("decisions") or [])[:1])[:200],
            )

            # ---- verify ---------------------------------------------------
            verified = await call(session, "turnstyl_verify", {"job_id": job_id})
            check("turnstyl_verify returns ok", verified.get("ok") is True, str(verified)[:200])
            check(
                "turnstyl_verify reports per-step results",
                bool(verified.get("steps"))
                and all("matches" in s for s in verified["steps"]),
                json.dumps(verified.get("steps"))[:200],
            )
            check(
                "turnstyl_verify counts what it checked",
                {"checked", "matches", "mismatches", "no_commit"}
                <= set(verified.get("summary_counts") or {}),
                json.dumps(verified.get("summary_counts")),
            )

            # ---- report ---------------------------------------------------
            report = await call(session, "turnstyl_report", {"job_id": job_id})
            check("turnstyl_report returns ok", report.get("ok") is True, str(report)[:200])
            check(
                "turnstyl_report returns the Markdown document",
                (report.get("markdown") or "").startswith("# turnstyl")
                and "## Verification" in report.get("markdown", ""),
                (report.get("markdown") or "")[:80],
            )

            # ---- errors are sentences, never tracebacks --------------------
            missing = await call(session, "turnstyl_job", {"job_id": "0000nosuchjob"})
            check(
                "an unknown job is a clear sentence, not a traceback",
                missing.get("ok") is False
                and "Traceback" not in json.dumps(missing)
                and "0000nosuchjob" in missing.get("error", ""),
                str(missing)[:200],
            )


async def without_wallet(api: str) -> None:
    """A server with no BUYER_PRIVATE_KEY must still be useful, and must not pay."""
    with tempfile.TemporaryDirectory() as empty:
        async with stdio_client(params(api, wallet=False, cwd=empty)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name: t for t in (await session.list_tools()).tools}
                check(
                    "without a key the paying tool is not registered at all",
                    "turnstyl_pay_and_run" not in tools,
                    sorted(tools),
                )
                check(
                    "the other seven tools are still offered",
                    len(tools) == 7,
                    f"got {len(tools)}: {sorted(tools)}",
                )
                services = await call(session, "turnstyl_services")
                check(
                    "turnstyl_services works with no wallet",
                    services.get("ok") is True and bool(services.get("services")),
                    str(services)[:160],
                )
                status = await call(session, "turnstyl_status")
                check(
                    "turnstyl_status works with no wallet and says it cannot pay",
                    status.get("ok") is True and status.get("can_pay") is False,
                    str(status.get("can_pay")),
                )
                submitted = await call(
                    session, "turnstyl_submit", {"source": "contract X {}"}
                )
                check(
                    "submitting with no wallet is refused in one sentence",
                    submitted.get("ok") is False
                    and "BUYER_PRIVATE_KEY" in submitted.get("error", ""),
                    str(submitted)[:200],
                )


async def offline() -> None:
    """Nothing listening: every tool must say so, and say where it looked."""
    async with stdio_client(params("http://127.0.0.1:9", wallet=True)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            status = await call(session, "turnstyl_status")
            check(
                "an unreachable agent reads as a sentence naming the URL",
                status.get("ok") is False
                and "not answering" in status.get("error", "")
                and "127.0.0.1:9" in status.get("error", ""),
                str(status)[:200],
            )


async def main() -> int:
    api = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8794"
    print(f"turnstyl MCP server check against {api}\n")
    print("with a wallet")
    await with_wallet(api)
    print("\nwithout a wallet")
    await without_wallet(api)
    print("\nagent offline")
    await offline()

    print()
    if FAILURES:
        print(f"RESULT: FAIL - {len(FAILURES)} check(s) failed")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS - every tool answers in the shape it promises")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
