#!/usr/bin/env python3
"""Undo one default recorded before the grace period existed.

    .venv/bin/python scripts/repair_arrears.py <address>            # show the diff
    .venv/bin/python scripts/repair_arrears.py <address> --apply    # write it

A job that closed with delivered work unpaid used to record a default on the
spot. It now records an arrears item, and that only becomes a default after
GRACE_HOURS unsettled. A default taken under the old rule, on a debt that is
still inside what the grace window would have been, is a punishment the current
rule would not impose.

This converts exactly one such default back: the most recent close that is
still within grace. It reads the journal to find that close and to recover the
counters as they were immediately before it, so nothing is guessed. It refuses
to touch a default whose grace window has already passed, because that one
would have been recorded anyway.

Prints a before and after diff and changes nothing without --apply.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from turnstyl import policy  # noqa: E402
from turnstyl import schema as S  # noqa: E402
from turnstyl.memory import TurnstylMemory, TurnstylStore, default_db_path  # noqa: E402

CLOSE_MARKER = "delivered step(s) unpaid at close"
PAID_MARKER = "consecutive_paid_since_default="


def die(message: str, code: int = 2) -> None:
    print(f"turnstyl repair: {message}", file=sys.stderr)
    raise SystemExit(code)


def parse_ts(value):
    return policy.parse_iso(value)


def find_close(store: TurnstylStore, buyer: str) -> dict:
    """The most recent close that recorded a default for this buyer.

    Returns the event plus the counters as they stood immediately before the
    close, read out of the same event's acted lines: the money branch of that
    step runs before the close does, so its line is the last state before it.
    """
    def counter(line: str, key: str) -> int | None:
        token = f"{key}="
        if token not in line:
            return None
        digits = ""
        for ch in line.split(token, 1)[1]:
            if ch.isdigit():
                digits += ch
            else:
                break
        return int(digits) if digits else None

    events = sorted(store.read_journal(limit=1000), key=lambda e: e.get("ts") or "")
    # Walk forward keeping the last value each counter was reported at. A close
    # event does not restate a counter it did not change, so the answer for
    # "what was it just before this close" is the most recent report of it,
    # which may be several events earlier.
    seen = {"consecutive_paid_since_default": None, "consecutive_paid_since_block": None}
    found = None
    for event in events:
        if buyer not in json.dumps(event):
            continue
        acted = [str(a) for a in (event.get("acted") or [])]
        for line in acted:
            for key in seen:
                value = counter(line, key)
                if value is not None:
                    seen[key] = value
        if any(CLOSE_MARKER in a for a in acted):
            # The money branch of this same step ran before the close reset the
            # counters, so `seen` now holds the state immediately before it.
            found = {"event": event, "before": dict(seen), "acted": acted}
            seen = {k: 0 for k in seen}     # the close reset them
    if found is None:
        die(f"no close recording a default for {buyer} in the journal window")
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Convert one premature default back to arrears.")
    parser.add_argument("address", help="Buyer wallet address.")
    parser.add_argument("--apply", action="store_true", help="Write the change. Without it, nothing is written.")
    parser.add_argument("--db", default=None, help="Store to repair. Defaults to $TURNSTYL_DB.")
    args = parser.parse_args(argv[1:])

    path = Path(args.db) if args.db else default_db_path()
    if not path.is_file():
        die(f"no memory at {path}")
    store = TurnstylStore(TurnstylMemory(path))
    buyer = store.buyer_key(args.address)
    if not store.buyer_exists(buyer):
        die(f"buyer {buyer} is unknown to {path}")

    ledger = store.get_buyer(buyer)
    before_json = ledger.model_dump()

    if ledger.defaults <= 0:
        die(f"buyer {buyer} has no defaults to convert")
    open_debts = [o for o in ledger.outstanding if not o.closed_at]
    if not open_debts:
        die(
            f"buyer {buyer} carries no unsettled item without a closed_at, so "
            f"there is no default here to convert back to arrears"
        )

    close = find_close(store, buyer)
    closed_at = close["event"].get("ts")
    when = parse_ts(closed_at)
    if when is None:
        die(f"the close event for {buyer} has an unreadable timestamp: {closed_at!r}")
    now = datetime.now(timezone.utc)
    deadline = when + timedelta(hours=S.GRACE_HOURS)
    if now >= deadline:
        die(
            f"the default for {buyer} was recorded at {closed_at}, which is more "
            f"than {S.GRACE_HOURS:g}h ago. The grace period would have expired, so "
            f"this default would have been recorded under the new rule too and is "
            f"not repaired.",
            3,
        )

    # The counters as they stood immediately before that close.
    csd = close["before"]["consecutive_paid_since_default"]
    csb = close["before"]["consecutive_paid_since_block"]
    if csd is None:
        die("could not read consecutive_paid_since_default from the close event")

    after = ledger.model_copy(deep=True)
    after.defaults = ledger.defaults - 1
    after.consecutive_paid_since_default = csd
    if csb is not None:
        after.consecutive_paid_since_block = csb
    # completed_paid_jobs_at_block is snapshotted by a close that takes a buyer
    # to two or more defaults. If this close is the one that wrote the value it
    # holds now, undoing the close means undoing the snapshot; the field did not
    # exist before it, so it goes back to 0. If some earlier close wrote it, it
    # is left alone.
    at_block_note = "left as it is: an earlier close wrote it"
    if ledger.completed_paid_jobs_at_block == ledger.completed_paid_jobs:
        after.completed_paid_jobs_at_block = 0
        at_block_note = "this close wrote it, so it is undone with the close"
    for item in after.outstanding:
        if not item.closed_at:
            item.closed_at = closed_at
    after.trust_tier = policy.recompute_trust_tier(after)

    print(f"turnstyl repair: buyer {buyer}")
    print(f"  store        {path}")
    print(f"  close        {closed_at} (job {close['event'].get('extra', {}).get('job_id')})")
    print(f"  grace        {S.GRACE_HOURS:g}h, due {deadline.strftime('%Y-%m-%dT%H:%M:%SZ')}, "
          f"{(deadline - now).total_seconds() / 3600:.1f}h left")
    print()
    print("  field                            before -> after")
    fields = [
        "defaults",
        "unpaid_from_prior_jobs",
        "consecutive_paid_since_default",
        "consecutive_paid_since_block",
        "completed_paid_jobs",
        "completed_paid_jobs_at_block",
        "trust_tier",
    ]
    notes = {"completed_paid_jobs_at_block": at_block_note}
    for field in fields:
        was, now_value = getattr(ledger, field), getattr(after, field)
        mark = " *" if was != now_value else "  "
        note = notes.get(field, "")
        print(f"{mark} {field:32s} {str(was):>8} -> {str(now_value):<8}"
              + (f"  ({note})" if note and was != now_value else ""))
    print()
    print("  outstanding")
    for old_item, new_item in zip(ledger.outstanding, after.outstanding):
        print(f"    job {old_item.job_id} step {old_item.step} {old_item.amount_usdc:.2f} USDC")
        print(f"      closed_at {str(old_item.closed_at):>26} -> {new_item.closed_at}")
    print()
    print(f"  after this the debt is arrears, not a default: settle it before "
          f"{deadline.strftime('%Y-%m-%dT%H:%MZ')} and no default is recorded.")
    print(f"  {policy.arrears_line(after, now)}")
    print()

    if not args.apply:
        print("  DRY RUN. Nothing was written. Re-run with --apply to write it.")
        return 0

    store.put_buyer(buyer, after)
    store.journal(
        S.JournalEntry(
            evaluated=[
                f"entity buyer/{buyer} -> defaults={ledger.defaults}, recorded at "
                f"{closed_at} under the pre-grace rule",
                f"the debt is {(now - when).total_seconds() / 3600:.1f}h old, inside "
                f"the {S.GRACE_HOURS:g}h grace period",
            ],
            acted=[
                f"converted one default back to an arrears item for {buyer}: "
                f"defaults {ledger.defaults} -> {after.defaults}, "
                f"consecutive_paid_since_default {ledger.consecutive_paid_since_default} "
                f"-> {after.consecutive_paid_since_default}, "
                f"consecutive_paid_since_block {ledger.consecutive_paid_since_block} "
                f"-> {after.consecutive_paid_since_block}"
            ],
            forward=[
                f"the debt defaults at {deadline.strftime('%Y-%m-%dT%H:%M:%SZ')} if "
                f"it is not settled"
            ],
            extra={
                "buyer": buyer,
                "decision": "ARREARS_REPAIRED",
                "defaults": after.defaults,
                "summary": (
                    f"A default recorded before the grace period existed was "
                    f"converted back to arrears; the debt is due "
                    f"{deadline.strftime('%Y-%m-%dT%H:%MZ')}."
                ),
            },
        )
    )
    print("  APPLIED. The buyer entity and one journal line were written.")
    verify = store.get_buyer(buyer)
    if verify.defaults != after.defaults:
        die(
            f"wrote defaults={after.defaults} but read back {verify.defaults}; "
            f"the store did not take the change",
            4,
        )
    print(f"  verified: defaults={verify.defaults}, tier={verify.trust_tier}, "
          f"arrears={len(policy.arrears(verify))}")
    print(json.dumps({"before": before_json, "after": verify.model_dump()}, indent=2)[:0] or "", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
