"""turnstyl as MCP tools, so an agent can buy the way a person does.

The buyer here is a program with its own wallet. It reads what is on offer,
submits a contract, is quoted a price, pays that price in USDC, and gets the
work: the same four metered steps, the same receipts on Base Sepolia, the same
memory behind them. Nothing about the agent is special-cased for machines.

Every tool returns a compact object whose first key is ``summary``: one line a
model can read without parsing anything. Failures come back the same way, with
``ok: false`` and a sentence saying what to do about it, never a traceback.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from typing_extensions import Annotated

from . import client
from .client import TurnstylError

# stdio is the protocol channel and stderr is the log channel; a harness that
# shows stderr should not be flooded with one INFO line per request.
mcp = FastMCP("turnstyl_mcp", log_level="WARNING")

# How long to wait for the worker to run a step once its invoice is settled.
RUN_TIMEOUT_SECONDS = 90
POLL_SECONDS = 2.0


def fail(summary: str) -> dict[str, Any]:
    """Every failure, in the shape every success has."""
    return {"summary": summary, "ok": False, "error": summary}


def usdc(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "?"


def invoice_view(job: dict[str, Any]) -> dict[str, Any] | None:
    invoice = job.get("open_invoice")
    if not invoice or invoice.get("paid"):
        return None
    return {
        "step": invoice.get("step"),
        "step_name": next(
            (s.get("name") for s in job.get("steps") or []
             if s.get("step") == invoice.get("step")),
            None,
        ),
        "amount_usdc": invoice.get("amount_usdc"),
        "memo": invoice.get("memo"),
        "price_reason": invoice.get("price_reason"),
    }


def step_views(job: dict[str, Any], with_output: bool = True) -> list[dict[str, Any]]:
    out = []
    for step in job.get("steps") or []:
        view = {
            "step": step.get("step"),
            "name": step.get("name"),
            "status": step.get("status"),
            "price_usdc": step.get("price_usdc"),
            "paid": step.get("paid"),
            "cached": step.get("cached"),
            "pay_method": step.get("pay_method"),
            "pay_tx": step.get("pay_tx"),
            "commit_tx": step.get("commit_tx"),
            "output_sha256": step.get("output_sha256"),
        }
        if step.get("compiles") is not None:
            view["compiles"] = step["compiles"]
        if with_output:
            view["output"] = step.get("output")
        out.append(view)
    return out


async def job_detail(job_id: str) -> dict[str, Any]:
    return await client.with_session(
        lambda: client.get_json(f"/api/jobs/{job_id}", auth=True)
    )


# ----------------------------------------------------------------------
# Read-only tools. No wallet needed for the first two.
# ----------------------------------------------------------------------
@mcp.tool(
    name="turnstyl_services",
    annotations={
        "title": "List turnstyl services and prices",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def turnstyl_services() -> dict[str, Any]:
    """What this agent sells, with the price of every step. Free, no wallet needed.

    Each service is an ordered list of steps. Step 1 is free on every service;
    the rest are invoiced one at a time, and a step is only run once its
    invoice is settled. Call this before submitting anything to see what the
    work will cost in total.

    Returns:
        dict: summary, default service id, and services[] with id, name,
        description, total_usdc, and steps[] of n, name, base_price_usdc, gate.
    """
    try:
        data = await client.get_json("/api/job_types")
    except TurnstylError as exc:
        return fail(str(exc))
    services = data.get("job_types") or []
    names = ", ".join(
        f"{s['id']} ({s['total_usdc']:.2f} USDC)" for s in services
    ) or "none"
    return {
        "summary": f"{len(services)} service(s) on offer: {names}. Step 1 is free on each.",
        "ok": True,
        "default": data.get("default"),
        "services": services,
    }


@mcp.tool(
    name="turnstyl_status",
    annotations={
        "title": "Check the turnstyl agent and its chain",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def turnstyl_status() -> dict[str, Any]:
    """Is the agent up, what chain it settles on, and the public figures. No wallet needed.

    Use this first when anything else fails: it says whether the agent is
    reachable at all, whether its memory file is present, and whether the
    gasless x402 rail is available for payment.

    Returns:
        dict: summary, online, api, network, chain_id, receipts_address,
        payments_backend, x402 {enabled, network, reason}, memory_missing, and
        stats {jobs, jobs_completed, buyers, usdc_settled, decisions,
        served_from_memory}.
    """
    try:
        status = await client.get_json("/api/status")
    except TurnstylError as exc:
        return fail(str(exc))
    try:
        stats = await client.get_json("/api/stats")
    except TurnstylError:
        stats = {}

    x402 = status.get("x402") or {}
    rail = "x402 gasless payment is available" if x402.get("enabled") else (
        f"x402 is unavailable ({x402.get('reason') or 'no reason given'})"
    )
    memory = "memory missing" if status.get("memory_missing") else "memory present"
    return {
        "summary": (
            f"turnstyl is up at {client.api_base()} on Base Sepolia "
            f"(payments={status.get('payments_backend')}); {rail}; {memory}; "
            f"{stats.get('jobs', '?')} jobs and "
            f"{usdc(stats.get('usdc_settled'))} USDC settled so far."
        ),
        "ok": True,
        "online": True,
        "api": client.api_base(),
        "network": "base-sepolia",
        "chain_id": status.get("chain_id"),
        "receipts_address": status.get("receipts_address"),
        "explorer": status.get("explorer"),
        "payments_backend": status.get("payments_backend"),
        "x402": {
            "enabled": bool(x402.get("enabled")),
            "network": x402.get("network"),
            "reason": x402.get("reason"),
        },
        "memory_missing": bool(status.get("memory_missing")),
        "buyer_address": client.buyer_address(),
        "can_pay": client.have_key(),
        "stats": {
            k: stats.get(k)
            for k in (
                "jobs",
                "jobs_completed",
                "buyers",
                "usdc_settled",
                "decisions",
                "served_from_memory",
            )
        },
    }


@mcp.tool(
    name="turnstyl_submit",
    annotations={
        "title": "Submit a contract and get the free scope step",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def turnstyl_submit(
    source: Annotated[
        str,
        Field(
            description="The complete Solidity source to work on, 1 to 65536 bytes.",
            min_length=1,
        ),
    ],
    job_type: Annotated[
        str,
        Field(description="Service id from turnstyl_services; 'audit' or 'tests'."),
    ] = "audit",
) -> dict[str, Any]:
    """Open a job. Runs step 1 free and returns its output and the next invoice. Spends nothing.

    Signs in with the configured wallet first: one signature over a message the
    agent issues, no transaction and no spend. Submitting the same source twice
    resumes the open job rather than creating a second one, so nothing is
    charged twice.

    Args:
        source: the Solidity file, as text.
        job_type: which service to buy; defaults to 'audit'.

    Returns:
        dict: summary, job_id, job_type, resumed, decision, scope (the step 1
        output), next_invoice {step, amount_usdc, price_reason}, and
        credit {applies, trust_tier, jobs_until_credit}.
    """
    address = client.buyer_address()
    if not address:
        return fail(
            "submitting a job needs a wallet to open it in the name of: set "
            "BUYER_PRIVATE_KEY in the environment and restart the MCP server."
        )
    try:
        async def create():
            response = await client.request(
                "POST",
                "/api/jobs",
                auth=True,
                json_body={
                    "buyer": address,
                    "source": source,
                    "filename": "submitted.sol",
                    "job_type": job_type,
                },
            )
            if response.status_code != 200:
                raise TurnstylError(
                    f"the agent would not take the job ({response.status_code}): "
                    f"{client._detail(response)}"
                )
            return response.json()

        job = await client.with_session(create)
        buyer = await client.with_session(
            lambda: client.get_json(f"/api/buyers/{address}", auth=True)
        )
    except TurnstylError as exc:
        return fail(str(exc))

    trust = buyer.get("trust") or {}
    scope = next(
        (s.get("output") for s in job.get("steps") or [] if s.get("step") == 1), None
    )
    invoice = invoice_view(job)
    on_credit = job.get("decision") == "RUN_ON_CREDIT"
    line = (
        f"job {job.get('job_id')} open on {job.get('job_type')}; step 1 ran free"
        + (
            f"; step {invoice['step']} ({invoice['step_name']}) is invoiced at "
            f"{usdc(invoice['amount_usdc'])} USDC"
            if invoice
            else "; nothing outstanding"
        )
        + (
            f"; this buyer is {trust.get('trust_tier')} and "
            + (
                "gets work before paying"
                if on_credit
                else f"needs {trust.get('jobs_until_credit')} more fully paid job(s) for credit"
            )
        )
    )
    return {
        "summary": line,
        "ok": True,
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "resumed": bool(job.get("resumed")),
        "decision": job.get("decision"),
        "scope": scope,
        "next_invoice": invoice,
        "credit": {
            "applies": on_credit,
            "trust_tier": trust.get("trust_tier"),
            "jobs_until_credit": trust.get("jobs_until_credit"),
        },
    }


@mcp.tool(
    name="turnstyl_quote",
    annotations={
        "title": "Price the next step of a job",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def turnstyl_quote(
    job_id: Annotated[str, Field(description="Job id from turnstyl_submit.")],
) -> dict[str, Any]:
    """What the next step of this job costs and why. Spends nothing.

    The price reason is the pricing rules' own words: the base price, whether
    the work is already in memory (half price), any surcharge, and this buyer's
    standing. Read it before calling turnstyl_pay_and_run.

    Args:
        job_id: the job to quote.

    Returns:
        dict: summary, job_id, status, current_step, invoice {step, step_name,
        amount_usdc, memo, price_reason}, and trust {tier, jobs_until_credit,
        would_decide, explanation}.
    """
    try:
        job = await job_detail(job_id)
        buyer = await client.with_session(
            lambda: client.get_json(f"/api/buyers/{job['buyer']}", auth=True)
        )
    except TurnstylError as exc:
        return fail(str(exc))

    invoice = invoice_view(job)
    trust = buyer.get("trust") or {}
    if invoice is None:
        line = (
            f"job {job_id} has nothing to pay right now (status "
            f"{job.get('status')}, step {job.get('current_step')})"
        )
    else:
        line = (
            f"step {invoice['step']} ({invoice['step_name']}) of job {job_id} costs "
            f"{usdc(invoice['amount_usdc'])} USDC; buyer is {trust.get('trust_tier')}"
        )
    return {
        "summary": line,
        "ok": True,
        "job_id": job_id,
        "status": job.get("status"),
        "current_step": job.get("current_step"),
        "last_step": job.get("last_step"),
        "invoice": invoice,
        "trust": {
            "tier": trust.get("trust_tier"),
            "jobs_until_credit": trust.get("jobs_until_credit"),
            "would_decide": trust.get("would_decide"),
            "explanation": trust.get("explanation"),
        },
    }


@mcp.tool(
    name="turnstyl_job",
    annotations={
        "title": "Read a whole job: steps, outputs and decisions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def turnstyl_job(
    job_id: Annotated[str, Field(description="Job id from turnstyl_submit.")],
) -> dict[str, Any]:
    """Everything this wallet is entitled to see about one job. Spends nothing.

    Outputs are visible only to the wallet that paid for them; another wallet
    gets the public shape, with prices and transactions but no text.

    Args:
        job_id: the job to read.

    Returns:
        dict: summary, job_id, job_type, status, current_step, steps[] with
        output, open_invoice, and decisions[] of {at, decision, step, summary}.
    """
    try:
        job = await job_detail(job_id)
        journal = await client.with_session(
            lambda: client.get_json(f"/api/journal?job={job_id}&limit=50", auth=True)
        )
    except TurnstylError as exc:
        return fail(str(exc))

    done = [s for s in job.get("steps") or [] if s.get("status") == "done"]
    decisions = [
        {
            "at": event.get("ts"),
            "decision": event.get("decision"),
            "step": event.get("step"),
            "summary": (event.get("extra") or {}).get("summary"),
        }
        for event in journal.get("events") or []
    ]
    flags = job.get("injection_flags") or []
    warning = (
        f" The submitted source contains {len(flags)} passage(s) that try to "
        f"instruct the auditor; they were recorded, not followed."
        if flags
        else ""
    )
    return {
        "summary": (
            f"job {job_id} ({job.get('job_type')}) is {job.get('status')}: "
            f"{len(done)} of {job.get('last_step')} steps done, "
            f"{len(decisions)} decision(s) recorded.{warning}"
        ),
        "ok": True,
        "job_id": job_id,
        "job_type": job.get("job_type"),
        "status": job.get("status"),
        "current_step": job.get("current_step"),
        "last_step": job.get("last_step"),
        "readable": job.get("redacted") is not True,
        "injection_flags": flags,
        "steps": step_views(job),
        "open_invoice": invoice_view(job),
        "decisions": decisions,
    }


@mcp.tool(
    name="turnstyl_verify",
    annotations={
        "title": "Check each output against its on-chain commit",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def turnstyl_verify(
    job_id: Annotated[str, Field(description="Job id to verify.")],
) -> dict[str, Any]:
    """Prove each paid output is the one committed on chain. Spends nothing.

    Needs both halves: the chain holds the sha256 the agent published when it
    was paid, memory holds the output. This recomputes the hash and compares.
    A step with no commit says so rather than passing quietly.

    Args:
        job_id: the job to verify.

    Returns:
        dict: summary, job_id, summary_counts {checked, matches, mismatches,
        no_commit}, and steps[] of {step, name, matches, reason, onchain_hash,
        commit_tx, block}.
    """
    try:
        data = await client.with_session(
            lambda: client.get_json(f"/api/jobs/{job_id}/verify", auth=True)
        )
    except TurnstylError as exc:
        return fail(str(exc))

    counts = data.get("summary") or {}
    steps = [
        {
            "step": s.get("step"),
            "name": s.get("name"),
            "matches": s.get("matches"),
            "reason": s.get("reason"),
            "onchain_hash": s.get("onchain_hash"),
            "commit_tx": s.get("tx"),
            "block": s.get("block"),
        }
        for s in data.get("steps") or []
    ]
    return {
        "summary": (
            f"job {job_id}: {counts.get('matches', 0)} of {counts.get('checked', 0)} "
            f"step(s) match their on-chain commit, "
            f"{counts.get('mismatches', 0)} differ, "
            f"{counts.get('no_commit', 0)} have no commit."
        ),
        "ok": True,
        "job_id": job_id,
        "summary_counts": counts,
        "steps": steps,
    }


@mcp.tool(
    name="turnstyl_report",
    annotations={
        "title": "Download the job's Markdown report",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def turnstyl_report(
    job_id: Annotated[str, Field(description="Job id to report on.")],
) -> dict[str, Any]:
    """The whole job as one Markdown document the buyer can keep. Spends nothing.

    Every step's price, how it was paid, its output in full, and a verification
    table of sha256 against the Committed event on Base Sepolia.

    Args:
        job_id: the job to report on.

    Returns:
        dict: summary, job_id, markdown (the full document), bytes.
    """
    try:
        async def fetch():
            response = await client.request(
                "GET", f"/api/jobs/{job_id}/report.md", auth=True
            )
            if response.status_code == 401:
                raise TurnstylError(
                    f"the agent refused the request to the report as "
                    f"unauthenticated: {client._detail(response)}"
                )
            if response.status_code == 403:
                raise TurnstylError(
                    f"this report belongs to another wallet: {client._detail(response)}"
                )
            if response.status_code != 200:
                raise TurnstylError(
                    f"the agent answered {response.status_code} for the report: "
                    f"{client._detail(response)}"
                )
            return response.text

        text = await client.with_session(fetch)
    except TurnstylError as exc:
        return fail(str(exc))

    return {
        "summary": f"report for job {job_id}, {len(text):,} bytes of Markdown",
        "ok": True,
        "job_id": job_id,
        "markdown": text,
        "bytes": len(text),
    }


# ----------------------------------------------------------------------
# The one tool that spends money. Registered only when there is a wallet.
# ----------------------------------------------------------------------
async def _pay_and_run(job_id: str, max_usdc: float) -> dict[str, Any]:
    try:
        job = await job_detail(job_id)
    except TurnstylError as exc:
        return fail(str(exc))

    invoice = invoice_view(job)
    if invoice is None:
        return {
            "summary": (
                f"job {job_id} has no open invoice, so nothing was paid "
                f"(status {job.get('status')})."
            ),
            "ok": True,
            "paid": False,
            "job_id": job_id,
            "status": job.get("status"),
        }

    amount = float(invoice.get("amount_usdc") or 0)
    if amount > max_usdc:
        return fail(
            f"refusing to pay: step {invoice['step']} of job {job_id} costs "
            f"{usdc(amount)} USDC, above the {usdc(max_usdc)} USDC limit you set. "
            f"Nothing was spent. Raise max_usdc to at least {usdc(amount)} to "
            f"go ahead."
        )

    step = int(invoice["step"])
    try:
        status = await client.get_json("/api/status")
    except TurnstylError as exc:
        return fail(str(exc))
    backend = (status.get("payments_backend") or "").lower()
    x402_on = bool((status.get("x402") or {}).get("enabled"))

    try:
        if backend == "fake":
            settlement = await client.with_session(lambda: client.simulate_pay(job_id))
            settlement["note"] = (
                "this agent runs the fake payments backend, so x402 is "
                f"unavailable ({(status.get('x402') or {}).get('reason')}) and the "
                "invoice was marked paid without a chain transaction. No USDC moved."
            )
        elif not x402_on:
            return fail(
                "x402 gasless payment is not available on this agent right now "
                f"({(status.get('x402') or {}).get('reason') or 'no reason given'}), "
                "and this tool pays no other way. Nothing was spent. Pay through "
                "the receipts contract in the browser app instead."
            )
        else:
            settlement = await client.with_session(
                lambda: client.pay_x402(f"/api/jobs/{job_id}/pay-x402/{step}")
            )
    except TurnstylError as exc:
        return fail(str(exc))

    # The worker runs the step once the invoice is settled. Poll for it rather
    # than returning a payment with nothing to show for it.
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    ran: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            job = await job_detail(job_id)
        except TurnstylError:
            break
        current = next(
            (s for s in job.get("steps") or [] if s.get("step") == step), None
        )
        if current and current.get("status") == "done":
            ran = current
            break
        await asyncio.sleep(POLL_SECONDS)

    next_invoice = invoice_view(job)
    if ran is None:
        return {
            "summary": (
                f"paid {usdc(amount)} USDC for step {step} of job {job_id}, but the "
                f"agent had not finished the step after {RUN_TIMEOUT_SECONDS}s. The "
                f"payment stands and the work is owed: call turnstyl_job in a "
                f"moment to collect it."
            ),
            "ok": True,
            "paid": True,
            "ran": False,
            "job_id": job_id,
            "step": step,
            "settlement": settlement,
            "next_invoice": next_invoice,
        }

    return {
        "summary": (
            f"paid {usdc(amount)} USDC for step {step} ({ran.get('name')}) of job "
            f"{job_id} over {settlement['method']}, and the agent ran it"
            + (
                f"; next up is step {next_invoice['step']} "
                f"({next_invoice['step_name']}) at "
                f"{usdc(next_invoice['amount_usdc'])} USDC"
                if next_invoice
                else f"; job {job.get('status')}"
            )
        ),
        "ok": True,
        "paid": True,
        "ran": True,
        "job_id": job_id,
        "step": step,
        "step_name": ran.get("name"),
        "output": ran.get("output"),
        "output_sha256": ran.get("output_sha256"),
        "compiles": ran.get("compiles"),
        "cached": ran.get("cached"),
        "settlement": settlement,
        "commit_tx": ran.get("commit_tx"),
        "job_status": job.get("status"),
        "next_invoice": next_invoice,
    }


def register_paying_tool() -> None:
    """Register the spending tool. Only called when a wallet is configured."""

    @mcp.tool(
        name="turnstyl_pay_and_run",
        annotations={
            "title": "Pay for the next step and collect the work",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def turnstyl_pay_and_run(
        job_id: Annotated[str, Field(description="Job id from turnstyl_submit.")],
        max_usdc: Annotated[
            float,
            Field(
                description=(
                    "Hard limit in USDC for this one step. Required, no default. "
                    "The tool refuses and spends nothing if the invoice is above it."
                ),
                gt=0,
            ),
        ],
    ) -> dict[str, Any]:
        """SPENDS REAL MONEY: pays this job's open invoice in USDC from the configured wallet.

        The buyer signs an EIP-3009 transfer authorisation and a facilitator
        submits it, so the payment is gasless and needs no approval and no ETH.
        Then it waits up to 90 seconds for the agent to run the paid step and
        returns the work.

        Call turnstyl_quote first and pass a max_usdc you are willing to spend.
        The tool refuses without spending anything if the invoice is above it.

        Args:
            job_id: the job whose open invoice to settle.
            max_usdc: hard limit for this step, in USDC. Required.

        Returns:
            dict: summary, paid, ran, step, output, output_sha256, settlement
            {method, amount_usdc, tx, payer}, commit_tx, job_status, and
            next_invoice. On refusal: ok false and a reason, with nothing spent.
        """
        return await _pay_and_run(job_id, float(max_usdc))


def main() -> None:
    """Run the server over stdio. The entry point for `turnstyl-mcp`."""
    if client.have_key():
        register_paying_tool()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
