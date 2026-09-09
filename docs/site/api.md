# API

The HTTP API is what the browser, the CLI's `serve` view and the MCP server all
call. It is FastAPI; interactive docs are at `/docs` on any running instance.

**Every example on this page was produced by calling a real server**, not
written by hand: the read-only Base-backend examples come from the live agent
against `./data/turnstyl.db`, and the create/pay flow comes from a throwaway
server on the fake backend. Bodies are abridged where marked with `…` and
nowhere else.

## Base URL

`TURNSTYL_API` is an API **origin** — a tunnel URL, or `http://127.0.0.1:8787`
locally. The published GitHub Pages URL is a static page and answers no API
calls.

## Who is asking

Three identities, and nothing in between
([`auth.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/auth.py)):

| Identity | Credential | Sees |
| --- | --- | --- |
| **public** | none | that a job exists and how far it got, never what it says |
| **buyer** | `Authorization: Bearer <session token>` from signing a login message | everything about their own jobs, nothing more about anyone else's |
| **operator** | `Authorization: Bearer <OPERATOR_TOKEN>` from the agent's `.env` | everything |

Sessions are in-process and last 24 hours; a restart signs everyone out, which is
the right default for an agent whose whole state is one file the operator can
delete. See [Security](#security).

---

## Public endpoints

No credential. Nothing here names anyone.

## `GET /api/status`

What the agent is, which store it has open, and which rails are up. Auth: none.
Parameters: none.

```json
{
  "db_path": "data/turnstyl.db",
  "db_exists": true,
  "db_size_bytes": 2781184,
  "records": 193,
  "tenant": "turnstyl",
  "agent_address": "0x4463aC72Fa96E419c46185cbaA5313fB75C2FdA3",
  "receipts_address": "0xD2Bb3c9741D7c26A8B161895bb91471706B17477",
  "chain_id": 84532,
  "explorer": "https://sepolia.basescan.org",
  "payments_backend": "base",
  "usdc_address": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  "receipts_abi": [ "… Paid, Committed, pay, commit …" ],
  "usdc_abi": [ "… balanceOf, allowance, approve …" ],
  "max_jobs_per_day": 150,
  "remaining_today": 150,
  "job_types": [ "… the same array as GET /api/job_types …" ],
  "default_job_type": "audit",
  "x402": {
    "enabled": true,
    "network": "eip155:84532",
    "facilitator": "https://x402.org/facilitator",
    "reason": "facilitator https://x402.org/facilitator supports exact on eip155:84532"
  },
  "schema": { "ok": true, "problem": null, "skipped": false },
  "worker": {
    "running": true,
    "last_pass_at": "2026-09-09T08:07:33Z",
    "seconds_since_pass": 1.9,
    "passes": 390
  },
  "memory_missing": false
}
```

`schema.ok: false` means this build cannot read rows the store holds; the page
then says the agent needs a restart rather than showing an empty list. `worker`
is only meaningful when the process was started with `--with-worker`.

## `GET /api/stats`

The public meter: six figures, nobody named. Cached for 10 seconds. Auth: none.
Parameters: none.

```json
{
  "memory_missing": false,
  "jobs": 10,
  "jobs_completed": 10,
  "buyers": 2,
  "usdc_settled": 10.09,
  "decisions": 156,
  "served_from_memory": 24,
  "cache_seconds": 10,
  "computed_at": "2026-09-09T08:19:07Z",
  "source": "job states, each buyer's ledger, the job entities' step records, and a count of the journal table. No job id, address or output appears in this response."
}
```

The cache is deliberately bypassed when the memory file is missing, so the delete
beat shows the instant the file goes.

## `GET /api/job_types`

The services on offer, with steps, prices and gates. Auth: none. Parameters: none.

```json
{
  "memory_missing": false,
  "default": "audit",
  "job_types": [
    {
      "id": "audit",
      "name": "Security audit",
      "description": "A four-step Solidity security audit: scope, findings, patch, verify.",
      "input_kind": "solidity_source",
      "steps": [
        { "n": 1, "name": "scope",    "base_price_usdc": 0.0,  "gate": "none",    "cacheable": true },
        { "n": 2, "name": "findings", "base_price_usdc": 0.5,  "gate": "none",    "cacheable": true },
        { "n": 3, "name": "patch",    "base_price_usdc": 0.75, "gate": "compile", "cacheable": true },
        { "n": 4, "name": "verify",   "base_price_usdc": 0.25, "gate": "none",    "cacheable": true }
      ],
      "total_usdc": 1.5
    },
    {
      "id": "tests",
      "name": "Test suite",
      "description": "A Foundry test suite for your contract, run and reported.",
      "input_kind": "solidity_source",
      "steps": [
        { "n": 1, "name": "scope",  "base_price_usdc": 0.0,  "gate": "none",       "cacheable": true },
        { "n": 2, "name": "plan",   "base_price_usdc": 0.4,  "gate": "none",       "cacheable": true },
        { "n": 3, "name": "tests",  "base_price_usdc": 0.75, "gate": "forge_test", "cacheable": true },
        { "n": 4, "name": "report", "base_price_usdc": 0.25, "gate": "none",       "cacheable": true }
      ],
      "total_usdc": 1.4
    }
  ]
}
```

## `GET /api/auth/nonce`

A one-time nonce and the exact message to sign with it. Auth: none.

| Parameter | In | Required | Meaning |
| --- | --- | --- | --- |
| `address` | query | yes | the wallet that wants to sign in |

```json
{
  "address": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
  "nonce": "40b7fe9ef9ab9f3c96e11bf28939ce62",
  "issued": "2026-09-09T08:06:22Z",
  "message": "turnstyl login\n\naddress: 0x0964dc1e37aca77c6df395db7c0eec848b1ceff8\nnonce: 40b7fe9ef9ab9f3c96e11bf28939ce62\nissued: 2026-09-09T08:06:22Z",
  "expires_in_seconds": "300"
}
```

The message is built server-side and rebuilt server-side on verify, so a client
cannot sign one thing and present another.

## `POST /api/auth/verify`

Exchange a signature for a session token. Auth: none.

| Field | In | Required | Meaning |
| --- | --- | --- | --- |
| `address` | body | yes | `0x` + 40 hex |
| `signature` | body | yes | `personal_sign` over the message from `/api/auth/nonce` |
| `nonce` | body | no | the nonce that was signed, when the caller kept it |

```json
{
  "signed_in": true,
  "token": "cR7_U8rwiFmue4wLsuoyzo42d5kX2JqdwoOq_sG45E8",
  "address": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
  "expires_at": "2026-09-10T08:06:22Z",
  "expires_in_seconds": 86400
}
```

## `GET /api/auth/me`

Who the bearer token says you are. Auth: optional.

```json
{
  "kind": "buyer",
  "address": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
  "signed_in": true,
  "operator": false
}
```

## `POST /api/auth/logout`

Drop the session. Auth: bearer. Returns **204** with no body.

---

## Buyer endpoints

A session for the address in question, or the operator token.

## `POST /api/jobs`

Give the agent a contract. Runs step 1 free and issues the invoice for step 2.
Auth: **buyer session for `buyer`**, or operator.

| Field | In | Required | Meaning |
| --- | --- | --- | --- |
| `buyer` | body | yes | `0x` + 40 hex; must match the session |
| `source` | body | yes | 1 to 65536 bytes of Solidity |
| `job_type` | body | no | `audit` (default) or `tests` |
| `filename` | body | no | for display; basename only, 80 chars |

```json
{
  "memory_missing": false,
  "job_id": "d3098e24af3d",
  "buyer": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
  "contract_hash": "7c779e61ee00b90008a47686d43790be06677f501dd92b8ae51e0df56625cb33",
  "job_type": "audit",
  "job_type_name": "Security audit",
  "last_step": 4,
  "status": "awaiting_payment",
  "current_step": 2,
  "created_at": "2026-09-09T08:06:22.565Z",
  "archived": false,
  "open_invoice": {
    "step": 2,
    "amount_usdc": 0.5,
    "memo": "turnstyl:d3098e24af3d:step2",
    "invoice_block": null,
    "paid": false,
    "tx_hash": null,
    "price_reason": "base 0.50 for step 2 (findings); no discount (not cached), no surcharge (step_cost/2 avg_tokens=0 over 0 run(s)); buyer trust_tier=new = 0.50 USDC"
  },
  "steps": [ "… step 1, with its output …" ],
  "injection_flags": [],
  "source": "entity job/<id>",
  "resumed": false,
  "decision": "RUN_FREE"
}
```

`resumed: true` means an open job for this buyer and contract already existed and
was returned instead of a second one being created.

Refusals: **400** on a bad address, a source that is not Solidity, or an unknown
`job_type`; **401** with no session; **403** signed in as a different wallet;
**409** if the memory file is missing; **429** past 10 creations per minute or
150 per UTC day.

## `GET /api/jobs`

One buyer's jobs, or every job for the operator. Auth: buyer session for `buyer`,
or operator for the unfiltered list.

| Parameter | In | Required | Meaning |
| --- | --- | --- | --- |
| `buyer` | query | for a buyer | only this address's jobs |

```json
{
  "memory_missing": false,
  "buyer": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
  "viewer": "buyer",
  "jobs": [
    {
      "job_id": "d3098e24af3d",
      "buyer": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
      "contract_hash": "7c779e61ee00b90008a47686d43790be06677f501dd92b8ae51e0df56625cb33",
      "job_type": "audit",
      "current_step": 4,
      "status": "complete",
      "created_at": "2026-09-09T08:06:22.565Z",
      "updated_at": "2026-09-09T08:06:24.916Z",
      "archived": true,
      "source": "state + archived_entities (read-only)"
    }
  ],
  "source": "The SDK archives entities but exposes no reader for them, so completed jobs are recovered from the archived_entities table over a read-only connection, then from each buyer's jobs list. Every row names its own source."
}
```

Without `?buyer=` and without the operator token, **403**:

```json
{
  "detail": "the job list is an operator view. Ask for one buyer's jobs with ?buyer=<address> and a session for that address, or use GET /api/stats for the public figures."
}
```

## `GET /api/jobs/{job_id}`

One job. Auth: **none required** — a link to a job is a receipt a buyer may want
to show someone — but what comes back depends on who is asking.

To the buyer or the operator, every step carries its `output`, and
`contract_hash` is present:

```json
{
  "job_id": "d3098e24af3d",
  "buyer": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
  "contract_hash": "7c779e61ee00b90008a47686d43790be06677f501dd92b8ae51e0df56625cb33",
  "job_type": "audit",
  "status": "complete",
  "current_step": 4,
  "last_step": 4,
  "archived": true,
  "open_invoice": null,
  "steps": [
    {
      "step": 3,
      "name": "patch",
      "status": "done",
      "price_usdc": 0.75,
      "paid": true,
      "cached": false,
      "tokens_in": 943,
      "tokens_out": 472,
      "seconds": 0.163,
      "output_sha256": "9545b16f324e2a5507319a250bea56913f5cfc763a5d89272dcce4908a2896e3",
      "commit_tx": null,
      "pay_tx": "0xfaked3098e24af3d3",
      "pay_method": "receipts",
      "output": "--- a/Vault.sol\n+++ b/Vault.sol\n@@ -2,34 +2,43 @@ …",
      "compiles": true
    }
  ],
  "injection_flags": [],
  "source": "archived_entities (read-only)",
  "viewer": "buyer"
}
```

To anyone else, the same shape with every `output` **null**, the buyer truncated,
`contract_hash` dropped, and the redaction stated:

```json
{
  "job_id": "d3098e24af3d",
  "buyer": "0x0964…eff8",
  "contract_hash": null,
  "steps": [ { "step": 3, "name": "patch", "price_usdc": 0.75, "paid": true, "output": null, "compiles": true } ],
  "redacted": true,
  "private": "step outputs and the contract are visible to the buyer who paid for them, and to the operator",
  "viewer": "public"
}
```

## `GET /api/jobs/{job_id}/verify`

Each step's output against its on-chain commit. Auth: buyer or operator — it
recomputes hashes of the outputs, so it reads them. Parameters: none.

```json
{
  "job_id": "9f3dc77280a6",
  "job_type": "audit",
  "checked_at": "2026-09-09T08:07:25Z",
  "cache_ttl_seconds": 60,
  "steps": [
    {
      "step": 2,
      "name": "findings",
      "memo":         "0xa321a45b5629576f602133485b3ad434ecda94c33b290cb7e8dafb1dcde9db8c",
      "onchain_memo": "0xa321a45b5629576f602133485b3ad434ecda94c33b290cb7e8dafb1dcde9db8c",
      "cached": true,
      "output_sha256_stored":     "023ea28debaa9f07f9de262c74f7b7250ee2fa2cbd855cc00c1e3e79559bcc4c",
      "output_sha256_recomputed": "023ea28debaa9f07f9de262c74f7b7250ee2fa2cbd855cc00c1e3e79559bcc4c",
      "onchain_hash":           "0x023ea28debaa9f07f9de262c74f7b7250ee2fa2cbd855cc00c1e3e79559bcc4c",
      "tx": "0x8818015d3bc57910a384ea046bb911dada6958a2161b7ef5a1de197fbfae6b66",
      "tx_url": "https://sepolia.basescan.org/tx/0x8818015d3bc57910a384ea046bb911dada6958a2161b7ef5a1de197fbfae6b66",
      "block": 46516283,
      "matches": true,
      "reason": "matches on-chain commit"
    }
  ],
  "summary": { "checked": 4, "matches": 3, "mismatches": 0, "no_commit": 1 },
  "source": "archived_entities (read-only); Committed events decoded from each commit transaction's receipt"
}
```

See [Verify](#verify) for every `reason` and what each one means.

## `GET /api/jobs/{job_id}/report.json`

The job as a report document. Auth: buyer or operator.

| Parameter | In | Required | Meaning |
| --- | --- | --- | --- |
| `token` | query | no | session token, for a plain link that carries no header |

```json
{
  "memory_missing": false,
  "job_id": "d3098e24af3d",
  "contract_hash": "7c779e61ee00b90008a47686d43790be06677f501dd92b8ae51e0df56625cb33",
  "buyer": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
  "job_type": "audit",
  "job_type_name": "Security audit",
  "status": "complete",
  "generated_at": "2026-09-09T08:06:25Z",
  "archived": true,
  "chain_id": 84532,
  "explorer": "https://sepolia.basescan.org",
  "receipts_address": "0xD2Bb3c9741D7c26A8B161895bb91471706B17477",
  "receipts_url": "https://sepolia.basescan.org/address/0xD2Bb3c9741D7c26A8B161895bb91471706B17477",
  "steps": [ "… every step with its output …" ],
  "verification": [
    { "step": 1, "name": "scope",    "output_sha256": "b975491491f0…", "commit_tx": null, "commit_tx_url": null }
  ]
}
```

## `GET /api/jobs/{job_id}/report.md`

The same document as Markdown, as a download. Auth: buyer or operator, header or
`?token=`. A download is a navigation and carries no `Authorization` header,
which is the only reason the query parameter exists; nothing else accepts a token
that way.

```text
Content-Type: text/markdown; charset=utf-8
Content-Disposition: attachment; filename="turnstyl-audit-d3098e24af3d.md"
```

```markdown
# turnstyl security audit report: job d3098e24af3d

- service: Security audit (`audit`)
- contract sha256: `7c779e61ee00b90008a47686d43790be06677f501dd92b8ae51e0df56625cb33`
- buyer: `0x0964dc1e37aca77c6df395db7c0eec848b1ceff8`
- job status: complete (archived)
- opened: 2026-09-09T08:06:22.565Z
- generated: 2026-09-09T08:06:25Z
- chain: Base Sepolia (84532), receipts contract [0xD2Bb…7477](…)

## Step 1: scope

- price: 0.00 USDC, free
- output sha256: `b975491491f0022b30d7050bf7d6ed4e122a4ca209f97139c19ae04ac1323373`
…
```

## `GET /api/buyers/{address}`

The ledger, and what the agent would decide for this buyer's next paid step.
Auth: that buyer, or operator.

```json
{
  "memory_missing": false,
  "buyer": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
  "known": true,
  "ledger": {
    "paid_steps": 3,
    "paid_usdc": 1.5,
    "open_invoices": 0,
    "unpaid_from_prior_jobs": 0,
    "defaults": 0,
    "consecutive_paid_since_default": 3,
    "consecutive_paid_since_block": 0,
    "completed_paid_jobs_at_block": 0,
    "completed_paid_jobs": 1,
    "trust_tier": "new",
    "jobs": ["d3098e24af3d"],
    "outstanding": []
  },
  "trust": {
    "trust_tier": "new",
    "would_decide": "WAIT_FOR_PAYMENT",
    "explanation": "step 2 is unpaid at 0.50 USDC and the buyer has not earned credit; credit after 3 fully paid jobs, currently 1 (buyer completed_paid_jobs=1, paid_steps=3, paid_usdc=1.50, open_invoices=0, unpaid_from_prior_jobs=0, defaults=0, consecutive_paid_since_default=3, trust_tier=new)",
    "jobs_until_credit": 2,
    "steps_until_credit": 2,
    "arrears": null,
    "unblock": null,
    "completed_paid_jobs": 1,
    "earned_back": true
  },
  "outstanding": [],
  "jobs": ["d3098e24af3d"],
  "source": "entity buyer/<address>; trust.explanation is the reason string policy.decide produces for the next paid step; outstanding[].memo is keccak256(\"<job_id>:<step>\") computed here, the same bytes payments.memo_bytes32 puts on chain"
}
```

`trust.explanation` is not a rendering of the ledger — it is the literal reason
string `policy.decide` would return.

## `GET /api/journal`

The decision history, newest first. Auth: optional; each event is trimmed to what
its own job's buyer allows — the whole entry for them and the operator, one
sentence for everyone else.

| Parameter | In | Default | Meaning |
| --- | --- | --- | --- |
| `job` | query | none | filter to one job id |
| `limit` | query | 50 | 1–500 |

```json
{
  "memory_missing": false,
  "job": "d3098e24af3d",
  "limit": 3,
  "viewer": "buyer",
  "count": 3,
  "events": [
    {
      "ts": "2026-09-09T08:06:24.921Z",
      "decision": "RUN_PAID",
      "step": 4,
      "buyer": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
      "evaluated": [
        "entity buyer/0x0964…eff8 -> completed_paid_jobs=0, paid_steps=2, paid_usdc=1.25, open_invoices=0, unpaid_from_prior_jobs=0, trust_tier=new",
        "job:d3098e24af3d -> current_step=4, status=awaiting_payment, job_type=audit",
        "entity findings/audit/7c779e61ee00... -> step 4 (verify) is not cached"
      ],
      "acted": [
        "RUN_PAID step 4 (verify) via the model; output_sha256=9a1d5df65a4a..., tokens=1681, seconds=0.0",
        "entity step_cost/audit/4 -> runs=1, avg_tokens=1681, avg_seconds=0.00",
        "archived entity job/d3098e24af3d"
      ],
      "forward": [
        "job d3098e24af3d is complete; audit work cached under audit/7c779e61ee00b90008..."
      ],
      "extra": {
        "job_id": "d3098e24af3d",
        "step": 4,
        "decision": "RUN_PAID",
        "price": 0.25,
        "summary": "Ran step 4 (verify) because the 0.25 USDC invoice was paid. Job complete, all steps paid. The audit work is cached for this contract."
      }
    }
  ],
  "source": "journal (COLD tier), newest first"
}
```

## `POST /api/jobs/{job_id}/pay-x402/{step}`

Pay one open invoice over x402, gasless for the buyer. Auth: the x402 payment
itself.

Unpaid, it answers **402** with the requirements base64-encoded in the
`PAYMENT-REQUIRED` header — and, because v2 sends an empty body, turnstyl mirrors
them into the JSON body too so a reader has both. Captured from the live agent:

```json
{
  "x402Version": 2,
  "error": "Payment required",
  "resource": {
    "url": "http://127.0.0.1:8787/api/jobs/9f3dc77280a6/pay-x402/2",
    "description": "turnstyl: one metered step of an audit or test suite",
    "mimeType": ""
  },
  "accepts": [
    {
      "scheme": "exact",
      "network": "eip155:84532",
      "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
      "amount": "10000",
      "payTo": "0x4463aC72Fa96E419c46185cbaA5313fB75C2FdA3",
      "maxTimeoutSeconds": 600,
      "extra": { "name": "USDC", "version": "2" }
    }
  ]
}
```

`amount` is in the asset's smallest unit; USDC has 6 decimals, so `10000` is 0.01
USDC. That job is already complete and owes nothing, which is why this quotes the
nominal fallback rather than a real invoice; a step with an open invoice quotes
that invoice's amount, priced from memory at request time.

With a valid `PAYMENT-SIGNATURE` (or the v1 alias `X-PAYMENT`) header, the
middleware verifies, the handler checks the payer against the job's buyer, and
the middleware settles on the way out:

```json
{
  "x402": "verified",
  "job_id": "9f3dc77280a6",
  "step": 2,
  "amount_usdc": 0.5,
  "buyer": "0x0964dc1e37aca77c6df395db7c0eec848b1ceff8",
  "note": "settlement is recorded once the facilitator confirms it"
}
```

Under `PAYMENTS=fake` this route is not mounted at all — **404**:

```json
{ "detail": "x402 is not available: payments backend is 'fake'; x402 settles real USDC" }
```

## `POST /api/buyers/{address}/settle-x402/{job_id}/{step}`

The same, for an outstanding item on a job that has already closed. Same request
and response shapes.

## `POST /api/jobs/{job_id}/pay` — fake backend only

Mark the job's open invoice paid, the same thing `turnstyl pay` does, so the
browser flow can be exercised without a wallet. Auth: buyer or operator.
Parameters: none.

Returns the job as `GET /api/jobs/{job_id}` would, plus three fields:

```json
{ "paid_step": 2, "tx_hash": "0xfaked3098e24af3d2", "simulated": true }
```

Under `PAYMENTS=base` it answers **404** — on chain a payment is a `Paid` log and
nothing else counts.

## `POST /api/buyers/{address}/settle/{job_id}/{step}` — fake backend only

Settle one outstanding item on a closed job. Auth: that buyer, or operator.
**404** under `PAYMENTS=base`; **404** if the buyer is unknown or has no such
outstanding item.

---

## Operator endpoints

`Authorization: Bearer <OPERATOR_TOKEN>`. The token is generated into the agent's
`.env` at startup and is never printed by the server or by any script.

## `GET /api/jobs` with no `?buyer=`

Every job in the store. See above; the operator's response carries
`"viewer": "operator"` and nothing is redacted.

## `GET /api/digest`

What the agent did, counted from the journal and the entities. Auth: optional —
**the operator gets every figure, everyone else gets the counts only.**

| Parameter | In | Default | Meaning |
| --- | --- | --- | --- |
| `days` | query | 1 | 1–90, how many days back to count |

Computing it writes one consolidation entity, `digest/<date>`, so the same day
counted again is an entity read rather than a walk of the journal.

Operator:

```json
{
  "date": "2026-09-09",
  "days": 1,
  "viewer": "operator",
  "complete": true,
  "consolidated_as": "digest/2026-09-09",
  "figures": {
    "jobs_opened": 1,
    "jobs_completed": 1,
    "usdc_settled": 1.5,
    "steps_served_from_memory": 0,
    "steps_run": 4,
    "model_spend_usd_estimated": 0.0077,
    "model": "claude-haiku-4-5",
    "tokens_in": 3626,
    "tokens_out": 808,
    "new_buyers": 1,
    "trust_changes": 0,
    "buyers_above_new": 0,
    "defaults": 0,
    "refusals": 0,
    "injection_flags": 0,
    "median_seconds_payment_to_output": 0.2,
    "payment_to_output_observations": 3,
    "payment_to_output_minimum": 3,
    "top_contracts_by_repeat_audits": [
      { "contract_hash": "7c779e61ee00b900", "jobs": 1, "steps_from_memory": 0 }
    ],
    "buyers_active": 1
  }
}
```

Public — same call, no token. `complete: false`, and the four operator-only
figures are gone: the model spend is the operator's own bill and the contract
table is a list of what has been audited, so neither is public.

```json
{
  "date": "2026-09-09",
  "viewer": "public",
  "complete": false,
  "figures": {
    "jobs_opened": 1,
    "jobs_completed": 1,
    "usdc_settled": 1.5,
    "steps_served_from_memory": 0,
    "steps_run": 4,
    "new_buyers": 1,
    "trust_changes": 0,
    "buyers_above_new": 0,
    "defaults": 0,
    "refusals": 0,
    "injection_flags": 0,
    "median_seconds_payment_to_output": 0.2,
    "payment_to_output_observations": 3,
    "payment_to_output_minimum": 3
  }
}
```

---

## Static routes

Not part of the API and excluded from the OpenAPI schema.

| Path | What |
| --- | --- |
| `GET /` | the story page |
| `GET /app.html` | the buyer and operator app |
| `GET /docs.html` | this documentation site |
| `GET /docs/site/{slug}.md` | one documentation page's Markdown source |
| `GET /config.js` | the API origin the page should talk to, `no-store` |
| `GET /static/…` | the stylesheet, the brand marks, the screenshots |

## Errors, rate limits and CORS

| Code | When |
| --- | --- |
| 400 | a malformed address, a source that is not Solidity, an unknown `job_type` |
| 401 | an action that needs a session, without one |
| 403 | signed in, but not as the wallet that owns the thing asked for |
| 404 | no such job or buyer; or a fake-backend-only route under `PAYMENTS=base` |
| 409 | the memory file is missing and the agent cannot take jobs or payments |
| 429 | more than 10 job creations a minute, or past the 150-per-UTC-day cap |
| 503 | the store holds rows this build cannot read — the message names the entity and the field, and says to restart |

The rate and daily caps are in-process counters, so they reset when the server
restarts. CORS allows the GitHub Pages origin and localhost only, and exposes the
`PAYMENT-REQUIRED`, `PAYMENT-RESPONSE` and `X-PAYMENT-RESPONSE` headers so a
cross-origin page can read a 402.
