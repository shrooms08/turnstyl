"""The orchestrator: what runs, what it costs, who gets credit.

Every decision this module makes is a function of what memory said, and every
decision it makes is written back to memory as one journal event. Delete the
database and the agent forgets it was ever paid — which is the point turnstyl
is making.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import policy
from . import schema as S
from .llm import Usage as LLMUsage
from .llm import mechanical_block as llm_mechanical_block
from .llm import run_step as llm_run_step
from .memory import TurnstylMemory, TurnstylStore
from .payments import PaymentBackend, get_backend


def _dedupe(keys: list[str]) -> list[str]:
    """Memory keys in first-read order, each named once."""
    seen: set[str] = set()
    ordered = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


@dataclass
class Outcome:
    """Everything the CLI needs to render one call. The engine never prints."""

    job_id: str
    status: str
    step: int | None = None
    step_name: str | None = None
    decision: str | None = None
    reason: str = ""
    memory_read: list[str] = field(default_factory=list)
    output: str | None = None
    cached: bool = False
    price_usdc: float = 0.0
    tokens: int = 0
    seconds: float = 0.0
    invoice: S.OpenInvoice | None = None
    price_reason: str = ""
    diff_applies: bool | None = None
    compiles: bool | None = None
    memory_hints: list[str] = field(default_factory=list)
    commit_tx: str | None = None
    commit_hash: str | None = None
    commit_error: str | None = None
    reconciled: list[dict] = field(default_factory=list)
    complete: bool = False
    resumed: bool = False
    note: str = ""


class Engine:
    """One audit job at a time, resumable from memory at any point."""

    def __init__(
        self,
        store: TurnstylStore | None = None,
        payments: PaymentBackend | None = None,
    ) -> None:
        self.store = store or TurnstylStore(TurnstylMemory())
        self.payments = payments or get_backend(self.store.memory)
        # Written once, on first run, so the prices the agent quotes are on the
        # record rather than only in the code.
        self.store.ensure_pricing_rules()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def new_job(self, contract_path: str | Path, buyer: str) -> Outcome:
        """Start an audit from a .sol file, or resume the one already open."""
        path = Path(contract_path)
        if not path.is_file():
            raise RuntimeError(
                f"turnstyl: contract file not found at {path}. "
                f"Pass a path to a .sol file, e.g. examples/Vault.sol"
            )
        contract_text = path.read_text(encoding="utf-8")
        if not contract_text.strip():
            raise RuntimeError(f"turnstyl: contract file {path} is empty.")
        return self.new_job_from_source(contract_text, buyer, filename=path.name)

    def new_job_from_source(
        self, contract_text: str, buyer: str, *, filename: str = "contract.sol"
    ) -> Outcome:
        """Start an audit from contract text, or resume the one already open.

        The path form above is a thin wrapper over this: the API hands in the
        text it was posted, the CLI hands in what it read from disk, and from
        here on nothing knows or cares which.
        """
        if not contract_text.strip():
            raise RuntimeError("turnstyl: the contract source is empty.")
        contract_hash = S.sha256_text(contract_text)
        buyer_key = self.store.buyer_key(buyer)

        reconciled = self._reconcile(buyer_key)
        hints = self._memory_hints(contract_text, contract_hash)
        read: list[str] = [S.STATE_ACTIVE_JOBS]
        if hints:
            read.append(f"fts5 findings/* for {filename} function names")
        existing = self._find_open_job(buyer_key, contract_hash, read)
        if existing is not None:
            self.store.journal(
                S.JournalEntry(
                    evaluated=[
                        f"{S.STATE_ACTIVE_JOBS} -> holds {existing.job_id}",
                        f"{S.job_state_key(existing.job_id)} -> buyer={existing.buyer}, "
                        f"contract_hash={existing.contract_hash[:12]}..., "
                        f"current_step={existing.current_step}, status={existing.status}",
                    ],
                    acted=[
                        f"resumed job {existing.job_id} instead of creating a new one"
                    ],
                    forward=[f"run step {existing.current_step} of {existing.job_id}"],
                    extra={
                        "job_id": existing.job_id,
                        "buyer": buyer_key,
                        "step": existing.current_step,
                        "decision": "RESUME_EXISTING",
                        "price": None,
                    },
                )
            )
            return Outcome(
                job_id=existing.job_id,
                status=existing.status,
                step=existing.current_step,
                step_name=S.STEP_NAMES.get(existing.current_step),
                decision="RESUME_EXISTING",
                reason=(
                    f"an open job for this buyer and contract_hash "
                    f"{contract_hash[:12]}... is already in {S.STATE_ACTIVE_JOBS} at "
                    f"step {existing.current_step} (status {existing.status}); "
                    f"no new job created and no work repeated"
                ),
                memory_read=_dedupe(read),
                invoice=existing.open_invoice,
                resumed=True,
                reconciled=reconciled,
                memory_hints=hints,
                note=f"Resumed job {existing.job_id}.",
            )

        job_id = uuid.uuid4().hex[:12]
        state = S.JobState(
            job_id=job_id,
            buyer=buyer_key,
            contract_hash=contract_hash,
            current_step=S.FIRST_STEP,
            status=S.STATUS_NEW,
        )
        self.store.put_job_state(state)
        self.store.put_job_entity(
            job_id, S.JobEntity(buyer=buyer_key, contract_hash=contract_hash)
        )
        self.store.add_active_job(job_id)
        # The source itself goes to memory, so any later process can run a step
        # of this job without being handed the file again.
        self.store.put_contract_source(contract_hash, contract_text)

        ledger = self.store.get_buyer(buyer_key)
        if job_id not in ledger.jobs:
            ledger.jobs.append(job_id)
        self.store.put_buyer(buyer_key, ledger)
        read.append(f"entity buyer/{buyer_key}")

        outcome = self._advance(state, contract_text, extra_reads=read)
        outcome.reconciled = reconciled
        outcome.memory_hints = hints
        return outcome

    def run(self, job_id: str) -> Outcome:
        """Execute the current step of a job, or say why it cannot."""
        read = [S.job_state_key(job_id)]
        state = self.store.get_job_state(job_id)
        if state is None:
            raise RuntimeError(
                f"turnstyl: no job {job_id!r} in memory at {self.store.db_path}.\n"
                f"  Run 'turnstyl status' to list the jobs this database knows about."
            )

        if state.status == S.STATUS_COMPLETE:
            self.store.journal(
                S.JournalEntry(
                    evaluated=[
                        f"{S.job_state_key(job_id)} -> status=complete, "
                        f"current_step={state.current_step}"
                    ],
                    acted=["did nothing; the job is already complete"],
                    forward=["no further steps for this job"],
                    extra={
                        "job_id": job_id,
                        "buyer": state.buyer,
                        "step": state.current_step,
                        "decision": "ALREADY_COMPLETE",
                        "price": None,
                    },
                )
            )
            return Outcome(
                job_id=job_id,
                status=state.status,
                step=state.current_step,
                decision="ALREADY_COMPLETE",
                reason=(
                    f"{S.job_state_key(job_id)} says status=complete; all "
                    f"{S.LAST_STEP} steps are recorded and the job entity is archived"
                ),
                memory_read=_dedupe(read),
                complete=True,
                note="Job is already complete. Nothing to run.",
            )

        reconciled = self._reconcile(state.buyer)
        contract_text = self._contract_text_for(state)
        outcome = self._advance(state, contract_text, extra_reads=read)
        outcome.reconciled = reconciled
        return outcome

    def peek(self, job_id: str) -> tuple[str, str, tuple | None, S.JobState] | None:
        """What ``run`` would decide right now, without writing a journal event.

        Syncs the open invoice against the payment backend (a real fact, and
        it is recorded in the state document) and asks the policy, but does
        not act and does not journal. The worker loop uses this every pass so
        a job that is simply waiting for payment does not leave one journal
        event per interval. Returns (decision, reason, invoice_signature,
        state), or None for a job that is complete or unknown.
        """
        state = self.store.get_job_state(job_id)
        if state is None or state.status == S.STATUS_COMPLETE:
            return None
        entity = self.store.get_job_entity(job_id)
        if entity is not None and str(state.current_step) in entity.steps:
            # already recorded: run() will skip or close it, which is an action
            return ("SKIP_ALREADY_DONE", "step already recorded", None, state)
        self._sync_invoice(state, [])
        ledger = self.store.get_buyer(state.buyer)
        decision, reason = policy.decide(state.current_step, ledger, state)
        inv = state.open_invoice
        signature = (inv.step, inv.paid, inv.amount_usdc) if inv is not None else None
        return decision, reason, signature, state

    def pay(self, job_id: str, step: int, tx_hash: str | None = None) -> str:
        """Settle one invoice out of band. Test backends only."""
        state = self.store.get_job_state(job_id)
        if state is None:
            raise RuntimeError(
                f"turnstyl: no job {job_id!r} in memory at {self.store.db_path}."
            )
        if step not in S.ALL_STEPS:
            raise RuntimeError(
                f"turnstyl: step must be one of {list(S.ALL_STEPS)}, got {step!r}."
            )
        resolved = self.payments.mark_paid(job_id, step, tx_hash)
        # Reflect it in the job state immediately so a reader of the state
        # document alone can see the invoice is settled.
        if state.open_invoice is not None and state.open_invoice.step == step:
            state.open_invoice.paid = True
            state.open_invoice.tx_hash = resolved
            self.store.put_job_state(state)
        return resolved

    def ledger(self, buyer: str) -> dict:
        """Everything memory knows about one buyer."""
        buyer_key = self.store.buyer_key(buyer)
        reconciled = self._reconcile(buyer_key)
        ledger = self.store.get_buyer(buyer_key)
        known = self.store.buyer_exists(buyer_key)
        jobs = []
        for job_id in ledger.jobs:
            state = self.store.get_job_state(job_id)
            if state is None:
                continue
            entity = self.store.get_job_entity(job_id)
            jobs.append(
                {
                    "job_id": job_id,
                    "status": state.status,
                    "current_step": state.current_step,
                    "contract_hash": state.contract_hash,
                    "open_invoice": state.open_invoice,
                    "steps_recorded": sorted(entity.steps) if entity else [],
                    "archived": entity is None,
                }
            )
        return {
            "buyer": buyer_key,
            "known": known,
            "ledger": ledger,
            "jobs": jobs,
            "reconciled": reconciled,
            "memory_read": [
                f"entity buyer/{buyer_key}",
                *[S.job_state_key(j["job_id"]) for j in jobs],
            ],
        }

    def status(self) -> dict:
        """The active jobs this database is carrying."""
        job_ids = self.store.get_active_jobs()
        jobs = []
        for job_id in job_ids:
            state = self.store.get_job_state(job_id)
            if state is None:
                continue
            jobs.append(state)
        return {
            "active_jobs": jobs,
            "orphans": [j for j in job_ids if self.store.get_job_state(j) is None],
            "db_path": str(self.store.db_path),
            "memory_read": [S.STATE_ACTIVE_JOBS, *[S.job_state_key(j) for j in job_ids]],
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    _FUNCTION_NAMES = re.compile(r"\bfunction\s+([A-Za-z_]\w*)")

    def _memory_hints(self, contract_text: str, contract_hash: str) -> list[str]:
        """Semantic search: has anything like this contract been audited before?

        FTS5 over the findings entities, queried with the contract's own function
        names. Cheap, and it is what lets the agent say "I have seen withdraw()
        before" on a contract whose bytes it has never seen.
        """
        names = self._FUNCTION_NAMES.findall(contract_text)[:5]
        if not names:
            return []
        try:
            hits = self.store.search_findings(" ".join(dict.fromkeys(names)))
        except Exception:
            return []  # a hint is never worth failing a job over
        hints = []
        for row in hits:
            body = row.get("body") or {}
            slots = [k for k in S.STEP_SLOTS.values() if body.get(k)]
            same = " (this contract)" if row.get("name") == contract_hash else ""
            hints.append(
                f"prior findings for contract {str(row.get('name'))[:12]}{same} "
                f"hold {', '.join(slots) or 'nothing yet'}"
            )
        return hints

    def _reconcile(self, buyer: str) -> list[dict]:
        """Ask the payment backend to settle anything the buyer has since paid.

        A no-op on the fake backend. On Base it walks the buyer's outstanding
        invoices against Paid logs, so a debt cleared on chain is cleared here
        before any decision is made about new work.
        """
        try:
            return self.payments.reconcile(buyer)
        except Exception as e:  # noqa: BLE001 - reconciliation must never block work
            self.store.journal(
                S.JournalEntry(
                    evaluated=[f"entity buyer/{buyer} -> reconciliation attempted"],
                    acted=[f"reconciliation failed: {type(e).__name__}: {e}"],
                    forward=["decisions below use the ledger as last written"],
                    extra={
                        "job_id": None,
                        "buyer": buyer,
                        "step": None,
                        "decision": "RECONCILE_FAILED",
                        "price": None,
                    },
                )
            )
            return []

    def _find_open_job(
        self, buyer_key: str, contract_hash: str, read: list[str]
    ) -> S.JobState | None:
        for job_id in self.store.get_active_jobs():
            state = self.store.get_job_state(job_id)
            read.append(S.job_state_key(job_id))
            if state is None:
                continue
            if (
                state.buyer == buyer_key
                and state.contract_hash == contract_hash
                and state.status != S.STATUS_COMPLETE
            ):
                return state
        return None

    def _contract_text_for(self, state: S.JobState) -> str:
        """Recover the contract for a resumed job from the REFERENCE tier.

        This is what lets `turnstyl job run <id>` finish a job in a fresh
        process with no .sol path on the command line. Returns "" if the source
        is missing; only a genuine model call needs it, and that path then
        fails loudly.
        """
        return self.store.get_contract_source(state.contract_hash) or ""

    def _advance(
        self,
        state: S.JobState,
        contract_text: str,
        extra_reads: list[str],
    ) -> Outcome:
        """Decide and act on exactly one step. Writes exactly one journal event."""
        job_id = state.job_id
        step = state.current_step
        read = list(extra_reads)

        entity = self.store.get_job_entity(job_id)
        read.append(f"entity job/{job_id}")
        if entity is None:
            raise RuntimeError(
                f"turnstyl: job {job_id} has a state document but no job entity in "
                f"memory at {self.store.db_path}. The database is inconsistent; "
                f"start a new job."
            )

        # Idempotent resume: a step whose output is already recorded is never
        # re-executed and never re-charged.
        if str(step) in entity.steps:
            done = entity.steps[str(step)]
            skip_acted = [f"skipped step {step}; it is already recorded"]
            if step >= S.LAST_STEP:
                # Crash between the last step's write and the completion write:
                # finish closing the job rather than advancing past step 4.
                state.status = S.STATUS_COMPLETE
                state.current_step = S.LAST_STEP
                ledger = self.store.get_buyer(state.buyer)
                self._complete(state, entity, ledger, skip_acted)
                ledger.trust_tier = policy.recompute_trust_tier(ledger)
                self.store.put_buyer(state.buyer, ledger)
            else:
                state.current_step = step + 1
            self.store.put_job_state(state)
            self.store.journal(
                S.JournalEntry(
                    evaluated=[
                        f"entity job/{job_id} -> step {step} already recorded, "
                        f"output_sha256={done.output_sha256[:12]}..., "
                        f"paid={done.paid}",
                    ],
                    acted=skip_acted,
                    forward=(
                        ["job closed on resume; no further steps"]
                        if state.status == S.STATUS_COMPLETE
                        else [f"run step {state.current_step}"]
                    ),
                    extra={
                        "job_id": job_id,
                        "buyer": state.buyer,
                        "step": step,
                        "decision": "SKIP_ALREADY_DONE",
                        "price": done.price_usdc,
                    },
                )
            )
            return Outcome(
                job_id=job_id,
                status=state.status,
                step=step,
                step_name=S.STEP_NAMES.get(step),
                decision="SKIP_ALREADY_DONE",
                reason=(
                    f"entity job/{job_id} already holds step {step} with "
                    f"output_sha256 {done.output_sha256[:12]}...; not re-run and "
                    f"not re-charged"
                ),
                memory_read=_dedupe(read),
                invoice=state.open_invoice,
                complete=state.status == S.STATUS_COMPLETE,
                note=(
                    f"Job {job_id} was already finished; closed it out."
                    if state.status == S.STATUS_COMPLETE
                    else f"Step {step} was already done. Run again for step {state.current_step}."
                ),
            )

        # Settle the open invoice from the payment backend before deciding.
        self._sync_invoice(state, read)

        buyer_key = state.buyer
        ledger = self.store.get_buyer(buyer_key)
        read.append(f"entity buyer/{buyer_key}")

        decision, reason = policy.decide(step, ledger, state)

        if decision in (S.WAIT_FOR_PAYMENT, S.REFUSE):
            self.store.journal(
                S.JournalEntry(
                    evaluated=[
                        f"entity buyer/{buyer_key} -> paid_steps={ledger.paid_steps}, "
                        f"open_invoices={ledger.open_invoices}, "
                        f"unpaid_from_prior_jobs={ledger.unpaid_from_prior_jobs}, "
                        f"trust_tier={ledger.trust_tier}",
                        f"{S.job_state_key(job_id)} -> open_invoice="
                        f"{state.open_invoice.model_dump() if state.open_invoice else None}",
                    ],
                    acted=[f"{decision} on step {step}: {reason}"],
                    forward=[
                        "wait for the invoice to be settled, then run again"
                        if decision == S.WAIT_FOR_PAYMENT
                        else "no further work for this buyer until prior jobs are settled"
                    ],
                    extra={
                        "job_id": job_id,
                        "buyer": buyer_key,
                        "step": step,
                        "decision": decision,
                        "price": (
                            state.open_invoice.amount_usdc if state.open_invoice else None
                        ),
                    },
                )
            )
            return Outcome(
                job_id=job_id,
                status=state.status,
                step=step,
                step_name=S.STEP_NAMES.get(step),
                decision=decision,
                reason=reason,
                memory_read=_dedupe(read),
                invoice=state.open_invoice,
                price_usdc=(
                    state.open_invoice.amount_usdc if state.open_invoice else 0.0
                ),
                note=(
                    "Settle the invoice above, then run the job again."
                    if decision == S.WAIT_FOR_PAYMENT
                    else "Refused. Nothing was executed and nothing was charged."
                ),
            )

        return self._execute(state, entity, ledger, step, decision, reason, contract_text, read)

    def _sync_invoice(self, state: S.JobState, read: list[str]) -> None:
        """Ask the backend whether the open invoice has been settled."""
        invoice = state.open_invoice
        if invoice is None or invoice.paid:
            return
        read.append(f"payments {self.payments.name}:{invoice.memo}")
        tx_hash = self.payments.check_paid(state.job_id, invoice.step)
        if tx_hash:
            invoice.paid = True
            invoice.tx_hash = tx_hash
            self.store.put_job_state(state)

    def _execute(
        self,
        state: S.JobState,
        entity: S.JobEntity,
        ledger: S.BuyerLedger,
        step: int,
        decision: str,
        reason: str,
        contract_text: str,
        read: list[str],
    ) -> Outcome:
        job_id = state.job_id
        evaluated = [
            f"entity buyer/{state.buyer} -> paid_steps={ledger.paid_steps}, "
            f"paid_usdc={ledger.paid_usdc:.2f}, open_invoices={ledger.open_invoices}, "
            f"unpaid_from_prior_jobs={ledger.unpaid_from_prior_jobs}, "
            f"trust_tier={ledger.trust_tier}",
            f"{S.job_state_key(job_id)} -> current_step={step}, status={state.status}",
        ]

        state.status = S.STATUS_RUNNING
        self.store.put_job_state(state)

        findings = self.store.get_findings(state.contract_hash)
        read.append(f"entity findings/{state.contract_hash[:12]}...")
        cached_output = findings.slot(step)
        evaluated.append(
            f"entity findings/{state.contract_hash[:12]}... -> step {step} "
            f"({S.STEP_SLOTS[step]}) {'is cached' if cached_output else 'is not cached'}"
        )

        started = time.monotonic()
        if cached_output is not None:
            output, usage, cached = cached_output, LLMUsage(), True
            result = None
            diff_applies = None
        else:
            text = contract_text or self._require_contract_text(state)
            prior = {int(k): v.output for k, v in entity.steps.items()}
            result = llm_run_step(
                step, text, prior, mechanical=self._mechanical_for(step, entity)
            )
            output, usage, diff_applies = (
                result.output,
                result.usage,
                result.diff_applies,
            )
            cached = False
        tokens = usage.total
        seconds = round(time.monotonic() - started, 3)

        invoice = state.open_invoice
        if invoice is not None and invoice.step == step:
            price_usdc = invoice.amount_usdc
        else:
            step_cost = self.store.get_step_cost(step)
            price_usdc, _ = policy.price(step, ledger, step_cost, cached)

        record = S.StepRecord(
            output=output,
            output_sha256=S.sha256_text(output),
            price_usdc=price_usdc,
            paid=(decision == S.RUN_PAID) or price_usdc == 0.0,
            tx_hash=invoice.tx_hash if invoice and invoice.step == step else None,
            tokens=tokens,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            seconds=seconds,
            cached=cached,
            diff_applies=diff_applies,
            patched_source=result.patched_source if result else None,
            generated_diff=result.generated_diff if result else None,
            compiles=result.compiles if result else None,
            compiler_output=result.compiler_output if result else None,
        )
        entity.steps[str(step)] = record
        self.store.put_job_entity(job_id, entity)

        # Commit the hash of what was delivered, for anything the buyer is billed
        # for. Free work is not committed: nothing was sold, so there is nothing
        # to prove. A failed commit is a warning, never a lost step — the work is
        # already recorded in memory by the time we get here.
        commit_tx: str | None = None
        commit_error: str | None = None
        if decision in (S.RUN_PAID, S.RUN_ON_CREDIT):
            try:
                commit_tx = self.payments.commit_output(
                    job_id, step, record.output_sha256
                )
            except Exception as e:  # noqa: BLE001 - reported, never fatal
                commit_error = f"{type(e).__name__}: {e}"
            if commit_tx:
                record.commit_tx = commit_tx
                entity.steps[str(step)] = record
                self.store.put_job_entity(job_id, entity)

        acted = [
            f"{decision} step {step} ({S.STEP_NAMES[step]}) "
            f"{'from memory (cached, no model call)' if cached else 'via the model'}; "
            f"output_sha256={record.output_sha256[:12]}..., tokens={tokens}, "
            f"seconds={seconds}"
        ]

        if record.compiles is not None:
            acted.append(
                f"mechanical check: the patched contract "
                f"{'compiles' if record.compiles else 'DOES NOT COMPILE'}"
            )
        if commit_tx:
            acted.append(
                f"committed output_sha256={record.output_sha256[:12]}... on chain "
                f"in tx {commit_tx}"
            )
        elif commit_error:
            acted.append(f"on-chain commit failed and was skipped: {commit_error}")

        # Rolling cost averages only reflect real executions; a cached serve
        # costs no tokens and must not drag the average that sets the price.
        if not cached:
            updated_cost = self.store.record_step_cost(step, tokens, seconds)
            acted.append(
                f"entity step_cost/{step} -> runs={updated_cost.runs}, "
                f"avg_tokens={updated_cost.avg_tokens:.0f}, "
                f"avg_seconds={updated_cost.avg_seconds:.2f}"
            )

        # Money.
        if decision == S.RUN_PAID and invoice is not None and invoice.step == step:
            ledger.paid_steps += 1
            ledger.consecutive_paid_since_default += 1
            ledger.paid_usdc = round(ledger.paid_usdc + invoice.amount_usdc, 2)
            acted.append(
                f"entity buyer/{state.buyer} -> paid_steps={ledger.paid_steps}, "
                f"paid_usdc={ledger.paid_usdc:.2f}, "
                f"consecutive_paid_since_default="
                f"{ledger.consecutive_paid_since_default} (settled {invoice.memo})"
            )
        elif decision == S.RUN_ON_CREDIT:
            ledger.open_invoices += 1
            ledger.outstanding.append(
                S.OutstandingItem(
                    job_id=job_id,
                    step=step,
                    amount_usdc=price_usdc,
                    memo=invoice.memo if invoice and invoice.step == step else "",
                    invoice_block=(
                        invoice.invoice_block
                        if invoice and invoice.step == step
                        else None
                    ),
                )
            )
            acted.append(
                f"entity buyer/{state.buyer} -> delivered step {step} on credit; "
                f"open_invoices={ledger.open_invoices} ({price_usdc:.2f} USDC owed)"
            )

        state.open_invoice = None
        state.current_step = step + 1

        next_invoice: S.OpenInvoice | None = None
        price_reason = ""
        complete = False
        if step >= S.LAST_STEP:
            complete = True
            state.current_step = S.LAST_STEP
            state.status = S.STATUS_COMPLETE
            self._complete(state, entity, ledger, acted)
        else:
            next_step = step + 1
            next_invoice, price_reason = self._issue_invoice(
                state, next_step, ledger, evaluated, read
            )
            state.status = S.STATUS_AWAITING_PAYMENT

        ledger.trust_tier = policy.recompute_trust_tier(ledger)
        self.store.put_buyer(state.buyer, ledger)
        acted.append(f"entity buyer/{state.buyer} -> trust_tier={ledger.trust_tier}")
        self.store.put_job_state(state)

        forward = (
            [f"job {job_id} is complete; findings cached under contract_hash "
             f"{state.contract_hash[:12]}..."]
            if complete
            else [
                f"await {next_invoice.amount_usdc:.2f} USDC for step "
                f"{next_invoice.step} ({next_invoice.memo}), then run again"
            ]
        )
        self.store.journal(
            S.JournalEntry(
                evaluated=evaluated,
                acted=acted,
                forward=forward,
                extra={
                    "job_id": job_id,
                    "buyer": state.buyer,
                    "step": step,
                    "decision": decision,
                    "price": price_usdc,
                },
            )
        )

        return Outcome(
            job_id=job_id,
            status=state.status,
            step=step,
            step_name=S.STEP_NAMES[step],
            decision=decision,
            reason=reason,
            memory_read=_dedupe(read),
            output=output,
            cached=cached,
            price_usdc=price_usdc,
            tokens=tokens,
            seconds=seconds,
            diff_applies=diff_applies,
            compiles=record.compiles,
            invoice=next_invoice,
            price_reason=price_reason,
            commit_tx=commit_tx,
            commit_hash=record.output_sha256 if commit_tx else None,
            commit_error=commit_error,
            complete=complete,
            note=(
                f"Job {job_id} complete. All {S.LAST_STEP} outputs cached under this "
                f"contract hash."
                if complete
                else ""
            ),
        )

    def _mechanical_for(self, step: int, entity: S.JobEntity) -> str:
        """What the verifier is told about the checks already run.

        Only step 4 gets one, and only from the patch step's recorded results —
        the verifier judges a patch turnstyl has already diffed and compiled.
        """
        if step != S.STEP_VERIFY:
            return ""
        patch_record = entity.steps.get(str(S.STEP_PATCH))
        if patch_record is None:
            return ""
        return llm_mechanical_block(patch_record.compiles, patch_record.compiler_output)

    def _require_contract_text(self, state: S.JobState) -> str:
        raise RuntimeError(
            f"turnstyl: job {state.job_id} needs the contract source to run step "
            f"{state.current_step}, but this process was not given it.\n"
            f"  The contract for hash {state.contract_hash[:12]}... is not cached in "
            f"memory for this step. Re-run with 'turnstyl job new <contract.sol> "
            f"--buyer {state.buyer}', which resumes this same job with the file."
        )

    def _issue_invoice(
        self,
        state: S.JobState,
        step: int,
        ledger: S.BuyerLedger,
        evaluated: list[str],
        read: list[str],
    ) -> tuple[S.OpenInvoice, str]:
        """Price the next step from memory and open an invoice for it."""
        step_cost = self.store.get_step_cost(step)
        read.append(f"entity step_cost/{step}")
        findings = self.store.get_findings(state.contract_hash)
        cached = findings.slot(step) is not None
        amount, price_reason = policy.price(step, ledger, step_cost, cached)
        evaluated.append(
            f"entity step_cost/{step} -> runs={step_cost.runs}, "
            f"avg_tokens={step_cost.avg_tokens:.0f}; priced step {step}: {price_reason}"
        )
        memo = self.payments.issue_invoice(state.job_id, step, amount, state.buyer)
        invoice = S.OpenInvoice(
            step=step,
            amount_usdc=amount,
            memo=memo,
            invoice_block=self.payments.current_block(),
            price_reason=price_reason,
        )
        state.open_invoice = invoice
        return invoice, price_reason

    def _complete(
        self,
        state: S.JobState,
        entity: S.JobEntity,
        ledger: S.BuyerLedger,
        acted: list[str],
    ) -> None:
        """Copy the outputs into the findings entity, archive the job, settle up."""
        findings = self.store.get_findings(state.contract_hash)
        for step_str, record in entity.steps.items():
            findings = findings.with_step(int(step_str), record.output)
        self.store.put_findings(state.contract_hash, findings)
        acted.append(
            f"entity findings/{state.contract_hash[:12]}... -> filled "
            f"{sorted(S.STEP_SLOTS[int(k)] for k in entity.steps)}"
        )

        # A delivered-but-unpaid step on a job that is now closed stops being a
        # live invoice and becomes a debt carried into the next job.
        carried = [o for o in ledger.outstanding if o.job_id == state.job_id]
        if carried:
            ledger.open_invoices = max(0, ledger.open_invoices - len(carried))
            ledger.unpaid_from_prior_jobs += len(carried)
            ledger.defaults += len(carried)
            # A fresh default restarts the earn-back clock from zero.
            ledger.consecutive_paid_since_default = 0
            acted.append(
                f"entity buyer/{state.buyer} -> {len(carried)} delivered step(s) "
                f"unpaid at close; unpaid_from_prior_jobs="
                f"{ledger.unpaid_from_prior_jobs}, defaults={ledger.defaults}, "
                f"consecutive_paid_since_default reset to 0"
            )

        if self.store.archive_job_entity(
            state.job_id, f"job complete, outputs cached under {state.contract_hash}"
        ):
            acted.append(f"archived entity job/{state.job_id}")
        self.store.remove_active_job(state.job_id)
        acted.append(f"{S.STATE_ACTIVE_JOBS} -> removed {state.job_id}")
