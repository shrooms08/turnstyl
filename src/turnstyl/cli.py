"""turnstyl command line.

    turnstyl job new <contract.sol> --buyer <addr>
    turnstyl job run <job_id>
    turnstyl pay <job_id> <step> [--tx <hash>]
    turnstyl ledger <buyer>
    turnstyl status
"""
from __future__ import annotations

import os
import sys

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import schema as S
from .engine import Engine, Outcome

load_dotenv()

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="turnstyl - a metered audit agent that gets paid per step.",
)
job_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Job commands.")
app.add_typer(job_app, name="job")

DECISION_STYLE = {
    S.RUN_FREE: "cyan",
    S.RUN_PAID: "green",
    S.RUN_ON_CREDIT: "yellow",
    S.WAIT_FOR_PAYMENT: "magenta",
    S.REFUSE: "red",
    "RESUME_EXISTING": "cyan",
    "SKIP_ALREADY_DONE": "cyan",
    "ALREADY_COMPLETE": "cyan",
}


def agent_address() -> str:
    """The address a buyer pays. Public by definition; no secret is ever read."""
    return os.environ.get("AGENT_ADDRESS") or "(AGENT_ADDRESS not set in .env)"


def render_decision(outcome: Outcome) -> None:
    if not outcome.decision:
        return
    style = DECISION_STYLE.get(outcome.decision, "white")
    line = Text("DECISION: ", style="bold")
    line.append(outcome.decision, style=f"bold {style}")
    line.append(", because ")
    line.append(outcome.reason)
    console.print(line)
    if outcome.memory_read:
        console.print(
            Text(f"memory read: {', '.join(outcome.memory_read)}", style="dim")
        )


def render_step(outcome: Outcome) -> None:
    if outcome.output is None or outcome.step is None:
        return
    served = "from memory (cached)" if outcome.cached else "from the model"
    subtitle = (
        f"{served} | {outcome.tokens} tokens | {outcome.seconds:.2f}s | "
        f"{outcome.price_usdc:.2f} USDC | sha256 {S.sha256_text(outcome.output)[:12]}"
    )
    console.print(
        Panel(
            # Text(), not a bare str: step output is Solidity, and rich would
            # read `balances[msg.sender]` as console markup and eat the index.
            Text(outcome.output),
            title=f"STEP {outcome.step}: {S.STEP_NAMES[outcome.step]}",
            subtitle=subtitle,
            border_style="cyan",
            padding=(1, 2),
        )
    )


def render_invoice(invoice: S.OpenInvoice | None, price_reason: str = "") -> None:
    if invoice is None:
        return
    body = Table.grid(padding=(0, 2))
    body.add_column(style="dim", justify="right")
    body.add_column()
    body.add_row("step", f"{invoice.step} ({S.STEP_NAMES[invoice.step]})")
    body.add_row("amount", f"{invoice.amount_usdc:.2f} USDC")
    body.add_row("pay to", agent_address())
    body.add_row("memo", invoice.memo)
    body.add_row("status", "settled" if invoice.paid else "unpaid")
    if price_reason:
        body.add_row("priced", Text(price_reason))
    console.print(
        Panel(body, title="INVOICE", border_style="yellow", padding=(1, 2))
    )


def render_outcome(outcome: Outcome) -> None:
    render_step(outcome)
    render_decision(outcome)
    render_invoice(outcome.invoice, outcome.price_reason)
    if outcome.complete:
        console.print(
            Panel(
                f"Job {outcome.job_id} is complete. All {S.LAST_STEP} outputs are "
                f"cached in memory under this contract hash; a repeat audit of the "
                f"same contract is served from memory at half price.",
                title="COMPLETE",
                border_style="green",
                padding=(1, 2),
            )
        )
    elif outcome.note:
        console.print(Text(outcome.note, style="dim"))


def fail(message: str) -> None:
    err_console.print(Text(message, style="bold red"))
    raise typer.Exit(code=1)


def build_engine() -> Engine:
    try:
        return Engine()
    except Exception as e:  # surfaced with the path so an operator can act
        fail(f"turnstyl: could not start the engine: {type(e).__name__}: {e}")
        raise


@job_app.command("new")
def job_new(
    contract: str = typer.Argument(..., help="Path to the Solidity contract."),
    buyer: str = typer.Option(..., "--buyer", help="Buyer wallet address."),
) -> None:
    """Start a 4-step audit, or resume the open one for this contract."""
    engine = build_engine()
    try:
        outcome = engine.new_job(contract, buyer)
    except Exception as e:
        fail(f"turnstyl: {e}")
        raise
    console.print(
        Text(f"job {outcome.job_id}  buyer {engine.store.buyer_key(buyer)}", style="dim")
    )
    render_outcome(outcome)


@job_app.command("run")
def job_run(job_id: str = typer.Argument(..., help="Job id from 'job new'.")) -> None:
    """Run the current step of a job."""
    engine = build_engine()
    try:
        outcome = engine.run(job_id)
    except Exception as e:
        fail(f"turnstyl: {e}")
        raise
    console.print(Text(f"job {outcome.job_id}  status {outcome.status}", style="dim"))
    render_outcome(outcome)


@app.command("pay")
def pay(
    job_id: str = typer.Argument(..., help="Job id."),
    step: int = typer.Argument(..., help="Step number to settle (2, 3 or 4)."),
    tx: str = typer.Option(None, "--tx", help="Transaction hash to record."),
) -> None:
    """Settle an invoice. Fake payment backend only."""
    engine = build_engine()
    try:
        tx_hash = engine.pay(job_id, step, tx)
    except NotImplementedError as e:
        fail(f"turnstyl: {e}")
        raise
    except Exception as e:
        fail(f"turnstyl: {e}")
        raise
    console.print(
        Panel(
            f"step {step} of job {job_id} marked paid\ntx {tx_hash}",
            title="PAYMENT RECORDED",
            border_style="green",
            padding=(1, 2),
        )
    )


@app.command("ledger")
def ledger(buyer: str = typer.Argument(..., help="Buyer wallet address.")) -> None:
    """Print what memory knows about one buyer."""
    engine = build_engine()
    data = engine.ledger(buyer)
    book = data["ledger"]
    if not data["known"]:
        console.print(
            Text(
                f"No ledger for {data['buyer']} in {engine.store.db_path}. "
                f"This buyer is unknown to memory.",
                style="yellow",
            )
        )
        return

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    grid.add_row("buyer", data["buyer"])
    grid.add_row("paid steps", str(book.paid_steps))
    grid.add_row("paid", f"{book.paid_usdc:.2f} USDC")
    grid.add_row("open invoices", str(book.open_invoices))
    grid.add_row("unpaid from prior jobs", str(book.unpaid_from_prior_jobs))
    grid.add_row("trust tier", book.trust_tier)
    grid.add_row("jobs", str(len(book.jobs)))
    console.print(Panel(grid, title="LEDGER", border_style="cyan", padding=(1, 2)))

    if book.outstanding:
        table = Table(title="OUTSTANDING", border_style="yellow")
        table.add_column("job")
        table.add_column("step")
        table.add_column("amount USDC", justify="right")
        for item in book.outstanding:
            table.add_row(
                item.job_id,
                f"{item.step} ({S.STEP_NAMES[item.step]})",
                f"{item.amount_usdc:.2f}",
            )
        console.print(table)

    if data["jobs"]:
        table = Table(title="JOBS", border_style="cyan")
        table.add_column("job")
        table.add_column("status")
        table.add_column("step", justify="right")
        table.add_column("steps recorded")
        table.add_column("contract")
        for job in data["jobs"]:
            table.add_row(
                job["job_id"],
                job["status"],
                str(job["current_step"]),
                ",".join(job["steps_recorded"]) or ("archived" if job["archived"] else "-"),
                job["contract_hash"][:12],
            )
        console.print(table)
    console.print(Text(f"memory read: {', '.join(data['memory_read'])}", style="dim"))


@app.command("status")
def status() -> None:
    """List the active jobs held in memory."""
    engine = build_engine()
    data = engine.status()
    if not data["active_jobs"]:
        console.print(
            Text(f"No active jobs in {data['db_path']}.", style="yellow")
        )
        return
    table = Table(title="ACTIVE JOBS", border_style="cyan")
    table.add_column("job")
    table.add_column("buyer")
    table.add_column("status")
    table.add_column("step", justify="right")
    table.add_column("open invoice")
    table.add_column("contract")
    for state in data["active_jobs"]:
        invoice = state.open_invoice
        table.add_row(
            state.job_id,
            state.buyer,
            state.status,
            f"{state.current_step}/{S.LAST_STEP}",
            f"{invoice.amount_usdc:.2f} USDC step {invoice.step}" if invoice else "-",
            state.contract_hash[:12],
        )
    console.print(table)
    if data["orphans"]:
        console.print(
            Text(
                f"warning: {len(data['orphans'])} active job id(s) have no state "
                f"document: {', '.join(data['orphans'])}",
                style="red",
            )
        )
    console.print(Text(f"memory read: {', '.join(data['memory_read'])}", style="dim"))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
