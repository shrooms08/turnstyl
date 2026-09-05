"""The worker loop: jobs run themselves once the buyer has paid.

Every ``interval`` seconds it reads ``active_jobs`` from memory and, for each
job that is not complete, reconciles the buyer, asks the engine what it would
decide, and acts only when that means work: a RUN_* decision runs the step
through the very same ``Engine.run`` the CLI uses, so a paid step can never run
twice (the engine skips a step whose output is already recorded) and every
action lands in the journal the way it always has.

A job that is merely waiting is looked at without being written about. The
engine's ``peek`` returns the decision without journaling, and the worker keeps
a small in-memory map of what it last saw per job; only a change in decision or
invoice state is journaled, by letting ``run`` speak once.

The worker never creates a database. If the file is gone (the delete beat), it
says so once and idles until it comes back.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from . import schema as S
from .engine import Engine
from .memory import TurnstylMemory, TurnstylStore, default_db_path
from .payments import get_backend

RUNNING_GRACE_SECONDS = 120.0   # a job mid-step in another process is left alone


def _log(line: str) -> None:
    print(line, flush=True)


class Worker:
    def __init__(self, db: str | Path | None = None, interval: float = 3.0) -> None:
        self.db = Path(db) if db else None
        self.interval = max(0.5, float(interval))
        # last (decision, invoice signature) per job. In memory on purpose:
        # this is the worker's own short-term attention, not a fact about the
        # world, and it must not survive the process or land in the store.
        self.last_seen: dict[str, tuple] = {}
        self.said_missing = False
        self.stop = threading.Event()

    def path(self) -> Path:
        return self.db if self.db is not None else default_db_path()

    # ------------------------------------------------------------------
    def pass_once(self) -> int:
        """One sweep over the active jobs. Returns the number of actions taken."""
        path = self.path()
        if not path.is_file():
            if not self.said_missing:
                _log(f"worker: memory file missing at {path}; idle until it returns")
                self.said_missing = True
            self.last_seen.clear()
            return 0
        if self.said_missing:
            _log(f"worker: memory file is back at {path}")
            self.said_missing = False

        # A fresh store per pass, closed after it: if the file is deleted under
        # a held connection SQLite keeps writing to the unlinked inode, and the
        # worker would carry on running jobs into a file nobody can see.
        store = TurnstylStore(TurnstylMemory(path))
        try:
            engine = Engine(store=store, payments=get_backend(store.memory))
            actions = 0
            for job_id in list(store.get_active_jobs()):
                actions += self._tick(engine, store, job_id)
            # A debt on a closed job belongs to a buyer who may have no open
            # job at all, so the per-job sweep above would never reconcile it.
            # Walk every buyer carrying outstanding items as well.
            actions += self._reconcile_outstanding(engine, store)
            # forget jobs that are no longer active
            active = set(store.get_active_jobs())
            for job_id in list(self.last_seen):
                if job_id not in active:
                    del self.last_seen[job_id]
            return actions
        finally:
            closer = getattr(getattr(store.memory, "client", None), "storage", None)
            if closer is not None and hasattr(closer, "close"):
                closer.close()

    def _reconcile_outstanding(self, engine: Engine, store: TurnstylStore) -> int:
        cleared_total = 0
        try:
            rows = store.memory.list_entities(S.CAT_BUYER, limit=200)
        except Exception as e:  # noqa: BLE001
            _log(f"worker: could not list buyers: {type(e).__name__}: {e}")
            return 0
        for row in rows:
            try:
                ledger = S.BuyerLedger.model_validate(row["body"])
            except Exception:  # noqa: BLE001 - a malformed row is not this loop's problem
                continue
            if not ledger.outstanding:
                continue
            buyer = row["name"]
            try:
                cleared = engine.payments.reconcile(buyer)
            except Exception as e:  # noqa: BLE001 - say it once, keep sweeping
                key = ("reconcile", str(e)[:80])
                if self.last_seen.get(f"{buyer}:reconcile") != key:
                    _log(f"worker: buyer {buyer} reconcile failed: {type(e).__name__}: {e}")
                    self.last_seen[f"{buyer}:reconcile"] = key
                continue
            for item in cleared:
                _log(
                    f"worker: reconciled step {item['step']} of job {item['job_id']} "
                    f"for {buyer} ({item['amount_usdc']:.2f} USDC, {item['tx_hash']})"
                )
            cleared_total += len(cleared)
        return cleared_total

    def _tick(self, engine: Engine, store: TurnstylStore, job_id: str) -> int:
        state = store.get_job_state(job_id)
        if state is None or state.status == S.STATUS_COMPLETE:
            return 0
        if state.status == S.STATUS_RUNNING and _age_seconds(state.updated_at) < RUNNING_GRACE_SECONDS:
            return 0  # another process is on it right now

        try:
            engine.payments.reconcile(state.buyer)
        except Exception as e:  # noqa: BLE001 - say it once, keep sweeping
            key = ("reconcile", str(e)[:80])
            if self.last_seen.get(f"{job_id}:reconcile") != key:
                _log(f"worker: job {job_id} reconcile failed: {type(e).__name__}: {e}")
                self.last_seen[f"{job_id}:reconcile"] = key

        peeked = engine.peek(job_id)
        if peeked is None:
            return 0
        decision, _reason, signature, _state = peeked
        seen = (decision, signature)
        step = _state.current_step

        acts = decision in (S.RUN_FREE, S.RUN_PAID, S.RUN_ON_CREDIT, "SKIP_ALREADY_DONE")
        changed = self.last_seen.get(job_id) != seen
        if not acts and not changed:
            return 0            # still waiting, nothing new: no log, no journal

        # Either work to do, or a state worth one journal line. run() writes
        # exactly one event either way, the same one the CLI would.
        outcome = engine.run(job_id)
        self.last_seen[job_id] = seen
        _log(f"worker: job {job_id} step {step} {outcome.decision}")
        if outcome.commit_tx:
            _log(f"worker: job {job_id} committed {outcome.commit_tx}")
        if outcome.commit_error:
            _log(f"worker: job {job_id} commit failed: {outcome.commit_error}")
        if outcome.complete:
            _log(f"worker: job {job_id} complete")
            self.last_seen.pop(job_id, None)
        elif outcome.invoice is not None and acts:
            _log(
                f"worker: job {job_id} invoiced step {outcome.invoice.step} "
                f"{outcome.invoice.amount_usdc:.2f} USDC"
            )
        return 1 if acts else 0

    # ------------------------------------------------------------------
    def run_forever(self) -> None:
        _log(
            f"worker: watching {self.path()} every {self.interval:g}s "
            f"(payments={os.environ.get('PAYMENTS') or 'fake'}, "
            f"model={'mock' if os.environ.get('MOCK_LLM') == '1' else 'real'})"
        )
        while not self.stop.is_set():
            try:
                self.pass_once()
            except Exception as e:  # noqa: BLE001 - one bad pass must not kill the loop
                _log(f"worker: pass failed: {type(e).__name__}: {e}")
            self.stop.wait(self.interval)


def _age_seconds(iso: str) -> float:
    from datetime import datetime, timezone

    try:
        then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return 1e9
    return (datetime.now(timezone.utc) - then).total_seconds()


def start_in_thread(db: str | Path | None, interval: float) -> Worker:
    """The worker as a daemon thread inside another process (``serve``).

    It builds its own store on every pass, inside the thread, so it never
    shares a SQLite connection with the request handlers.
    """
    worker = Worker(db, interval)
    thread = threading.Thread(target=worker.run_forever, name="turnstyl-worker", daemon=True)
    thread.start()
    return worker


def main(db: str | None, interval: float, once: bool) -> int:
    worker = Worker(db, interval)
    if once:
        n = worker.pass_once()
        _log(f"worker: one pass, {n} action(s)")
        return 0
    # Install the handlers ourselves. A process started in the background by a
    # POSIX shell inherits SIGINT as ignored, and Python then never raises
    # KeyboardInterrupt; a process manager sends SIGTERM. Both must stop the
    # loop cleanly, and both are only installable from the main thread.
    import signal

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        pass
    _log("worker: stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover - `turnstyl worker` is the entry point
    sys.exit(main(None, 3.0, "--once" in sys.argv))
