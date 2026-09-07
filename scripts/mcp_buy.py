#!/usr/bin/env python3
"""Buy one metered step from turnstyl through the MCP server, as an agent would.

    TURNSTYL_API=http://127.0.0.1:8803 .venv/bin/python scripts/mcp_buy.py <contract.sol>

Used by the MCP beat in scripts/demo_live.sh. Everything here goes over stdio
to `turnstyl-mcp`, which goes over HTTP to the agent: no shortcuts, and no
import of turnstyl itself. Prints the markers the beat greps for.

Exit codes: 0 bought, 2 usage, 3 nothing to buy or the purchase did not
complete (the caller treats that as SKIP rather than FAIL).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER = str(REPO_ROOT / ".venv" / "bin" / "turnstyl-mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SKIP = 3


class Skip(Exception):
    """A reason this beat cannot buy anything, to report rather than to crash on."""


def die(message: str) -> None:
    """Stop the purchase with a sentence. Raised inside anyio's task group, so
    main unwraps it with except* rather than letting it surface as a traceback."""
    raise Skip(message)


def skips(error: BaseException) -> list[BaseException]:
    """The Skip leaves of a nested exception group.

    stdio_client and ClientSession each open their own task group, so a Skip
    raised inside the session arrives wrapped twice and the reason is only
    readable at the leaves.
    """
    if isinstance(error, BaseExceptionGroup):
        found: list[BaseException] = []
        for inner in error.exceptions:
            found.extend(skips(inner))
        return found
    return [error] if isinstance(error, Skip) else []


async def call(session: ClientSession, name: str, args: dict | None = None) -> dict:
    result = await session.call_tool(name, args or {})
    if result.structuredContent is not None:
        return result.structuredContent
    text = "".join(c.text for c in result.content if getattr(c, "text", None))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"summary": text, "ok": False, "error": text}


async def buy(contract: Path) -> int:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "TURNSTYL_API": os.environ.get("TURNSTYL_API", "http://127.0.0.1:8787"),
        "BUYER_PRIVATE_KEY": os.environ.get("BUYER_PRIVATE_KEY", ""),
    }
    if not env["BUYER_PRIVATE_KEY"]:
        die("BUYER_PRIVATE_KEY is not set, so the MCP server has no wallet")

    params = StdioServerParameters(command=SERVER, args=[], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = [t.name for t in (await session.list_tools()).tools]
            if "turnstyl_pay_and_run" not in tools:
                die("the MCP server did not register its paying tool")

            status = await call(session, "turnstyl_status")
            if not status.get("ok"):
                die(f"the agent is not answering: {status.get('error')}")
            if not (status.get("x402") or {}).get("enabled"):
                die(f"x402 is unavailable: {(status.get('x402') or {}).get('reason')}")
            print(f"signed in as {status.get('buyer_address')}")

            services = await call(session, "turnstyl_services")
            if not services.get("ok"):
                die(f"could not read the services: {services.get('error')}")
            print(
                "services: "
                + ", ".join(
                    f"{s['id']} {s['total_usdc']:.2f} USDC" for s in services["services"]
                )
            )

            submitted = await call(
                session,
                "turnstyl_submit",
                {"source": contract.read_text(encoding="utf-8"), "job_type": "audit"},
            )
            if not submitted.get("ok"):
                die(f"the agent would not take the job: {submitted.get('error')}")
            job_id = submitted["job_id"]
            if not submitted.get("scope"):
                die(f"job {job_id} came back without its free scope step")
            print(f"scope ok  job {job_id}, {len(submitted['scope'])} chars")

            quote = await call(session, "turnstyl_quote", {"job_id": job_id})
            invoice = quote.get("invoice")
            if not invoice:
                die(
                    f"job {job_id} has nothing to pay for "
                    f"(status {quote.get('status')}); nothing to buy in this beat"
                )
            amount = float(invoice["amount_usdc"])
            print(
                f"quoted   step {invoice['step']} ({invoice['step_name']}) at "
                f"{amount:.2f} USDC: {invoice.get('price_reason', '')[:90]}"
            )

            # The limit is the point of the tool: prove it refuses below the price
            # before proving it pays at or above it.
            refused = await call(
                session,
                "turnstyl_pay_and_run",
                {"job_id": job_id, "max_usdc": round(max(amount - 0.01, 0.01), 2)},
            )
            if refused.get("ok") is not False or "refusing to pay" not in refused.get("error", ""):
                die(f"the paying tool did not refuse a too-small budget: {refused}")
            print(f"refused over budget: {refused['error'][:100]}")

            paid = await call(
                session,
                "turnstyl_pay_and_run",
                {"job_id": job_id, "max_usdc": round(amount + 0.01, 2)},
            )
            if not paid.get("paid"):
                die(f"the payment did not go through: {paid.get('error') or paid.get('summary')}")
            settlement = paid.get("settlement") or {}
            print(f"PAID     {amount:.2f} USDC over {settlement.get('method')}")
            print(f"settlement {settlement.get('tx')}")
            if not paid.get("ran"):
                die(
                    f"paid, but the agent had not finished step {paid.get('step')} in "
                    f"time: {paid.get('summary')}"
                )
            print(
                f"ran step {paid['step']} ({paid.get('step_name')}), "
                f"{len(paid.get('output') or '')} chars, commit {paid.get('commit_tx')}"
            )

            job = await call(session, "turnstyl_job", {"job_id": job_id})
            print(
                f"job      {job.get('status')}, "
                f"{sum(1 for s in job.get('steps') or [] if s.get('status') == 'done')} "
                f"step(s) done, {len(job.get('decisions') or [])} decision(s)"
            )
            return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: mcp_buy.py <contract.sol>", file=sys.stderr)
        return 2
    contract = Path(argv[1])
    if not contract.is_file():
        print(f"turnstyl mcp buy: contract {contract} is missing", file=sys.stderr)
        return 2
    # anyio wraps whatever escapes its task group, so the skip is unwrapped
    # here; `return` is not allowed inside an except* block, hence the variable.
    code = SKIP
    try:
        code = asyncio.run(buy(contract))
    except* Skip as group:
        for reason in skips(group):
            print(f"turnstyl mcp buy: {reason}", file=sys.stderr)
        code = SKIP
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
