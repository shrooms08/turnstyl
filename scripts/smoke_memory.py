#!/usr/bin/env python3
"""Cross-process persistence smoke test for turnstyl's Sibyl Memory wrapper.

Run the two modes as SEPARATE processes — that is the whole point. The write
mode never verifies its own reads; a fresh process proves the data landed on
disk rather than in an in-memory cache.

    .venv/bin/python scripts/smoke_memory.py write
    .venv/bin/python scripts/smoke_memory.py read

Every failure raises with the expected and actual values printed.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Run from a checkout without needing the package installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from turnstyl.memory import TurnstylMemory  # noqa: E402

STATE_KEY = "job:demo"
STATE_BODY = {"step": 2, "status": "paid"}

ENTITY_CATEGORY = "buyer"
ENTITY_NAME = "0xabc"
ENTITY_BODY = {"paid_steps": 2}

EVENT_ACTED = ["smoke test wrote job:demo"]
EVENT_EXTRA = {"marker": "turnstyl-smoke", "job": "demo"}


def _fail(message: str) -> None:
    """Raise with a message an operator can act on without reading the code."""
    raise AssertionError(f"turnstyl smoke test FAILED: {message}")


def do_write() -> None:
    mem = TurnstylMemory()
    print(f"opening memory at {mem.db_path}")
    mem.set_state(STATE_KEY, STATE_BODY)
    mem.set_entity(ENTITY_CATEGORY, ENTITY_NAME, ENTITY_BODY)
    event_id = mem.write_event(acted=EVENT_ACTED, extra=EVENT_EXTRA)
    print(f"wrote state {STATE_KEY!r}, entity {ENTITY_CATEGORY}/{ENTITY_NAME}, event {event_id}")
    print("WRITE OK")


def do_read() -> None:
    mem = TurnstylMemory()  # fresh client, fresh process
    print(f"opening memory at {mem.db_path}")

    state = mem.get_state(STATE_KEY)
    if state is None:
        _fail(
            f"state key {STATE_KEY!r} is missing from {mem.db_path}. "
            f"Run 'write' first, and confirm both modes use the same database path."
        )
    if state["body"] != STATE_BODY:
        _fail(
            f"state {STATE_KEY!r} body mismatch. "
            f"expected {STATE_BODY!r}, got {state['body']!r}"
        )

    entity = mem.get_entity(ENTITY_CATEGORY, ENTITY_NAME)
    if entity is None:
        _fail(
            f"entity {ENTITY_CATEGORY}/{ENTITY_NAME} is missing from {mem.db_path}. "
            f"Run 'write' first, and confirm the tenant_id matches "
            f"(this process used {mem.tenant_id!r})."
        )
    if entity["body"] != ENTITY_BODY:
        _fail(
            f"entity {ENTITY_CATEGORY}/{ENTITY_NAME} body mismatch. "
            f"expected {ENTITY_BODY!r}, got {entity['body']!r}"
        )

    events = mem.read_events(limit=50)
    if not events:
        _fail(
            f"the journal at {mem.db_path} is empty; the 'write' mode should have "
            f"appended one event."
        )
    matching = [e for e in events if e.get("acted") == EVENT_ACTED]
    if not matching:
        _fail(
            f"no journal event with acted == {EVENT_ACTED!r} among the "
            f"{len(events)} most recent events. Most recent acted values: "
            f"{[e.get('acted') for e in events[:5]]!r}"
        )
    newest = matching[0]
    if newest.get("extra") != EVENT_EXTRA:
        _fail(
            f"journal event {newest['id']} extra mismatch. "
            f"expected {EVENT_EXTRA!r}, got {newest.get('extra')!r}"
        )

    print(f"state {STATE_KEY!r} -> {state['body']!r}")
    print(f"entity {ENTITY_CATEGORY}/{ENTITY_NAME} -> {entity['body']!r}")
    print(f"journal -> {len(events)} event(s), newest match {newest['id']} at {newest['ts']}")
    print("READ OK: cross-process persistence confirmed")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"write", "read"}:
        print(
            "usage: .venv/bin/python scripts/smoke_memory.py {write|read}\n"
            "  write  seed state, entity and one journal event, then print WRITE OK\n"
            "  read   open a fresh client, verify all three, then print READ OK",
            file=sys.stderr,
        )
        return 2
    if argv[1] == "write":
        do_write()
    else:
        do_read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
