# turnstyl

turnstyl is a metered AI agent that does multi-step work, gets paid per step in USDC on Base, and keeps job state, findings, and a buyer ledger in Sibyl Memory so it can resume any job, price any step, and decide who gets credit.

Status: day 2: core engine, offline demo passing

## Try it offline

No API key and no chain needed — `MOCK_LLM=1` serves canned step outputs and
`PAYMENTS=fake` settles invoices by hand.

```bash
.venv/bin/python scripts/demo_offline.py          # the seven-beat acceptance test

export MOCK_LLM=1 PAYMENTS=fake
.venv/bin/turnstyl job new examples/Vault.sol --buyer 0xYourAddress
.venv/bin/turnstyl status
.venv/bin/turnstyl pay <job_id> 2
.venv/bin/turnstyl job run <job_id>
.venv/bin/turnstyl ledger 0xYourAddress
```

Step 1 (scope) is free. Steps 2 (findings), 3 (patch) and 4 (verify) are priced
per step from what memory already knows: a contract whose findings are cached
costs half, a step whose recorded average token cost is high costs more, and a
buyer who has paid twice with nothing outstanding gets the next step on credit.
