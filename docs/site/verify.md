# Verify

Every job page has a **Verify** button, and every job has
`GET /api/jobs/{job_id}/verify` behind it. This page is what that proves, what it
does not, and what it says when someone has been at the store.

## What verify proves

For each step, that **the output the buyer is holding right now is the same
output whose hash the agent published on chain when it was paid**.

It needs both halves. The chain holds the hash; memory holds the output. Either
alone proves nothing:

- the hash on chain, with no output, is 32 bytes that could stand for anything;
- the output in memory, with no hash on chain, is a file the agent could have
  written at any time.

That is also why the [delete test](#overview) cannot be undone from the chain,
and why the memo — `keccak256("<job_id>:<step>")` — is only meaningful to
someone who still holds the job id.

## What verify does not prove

- **Not that the work is correct.** A confidently wrong audit verifies perfectly.
  Verify is about integrity of delivery, not quality of judgement; for quality,
  see the mechanical gates and the [Evals](#evals).
- **Not that the agent has not lost the output.** A step whose output is gone
  from memory cannot be verified at all — there is nothing left to hash. Verify
  reports the absence rather than passing quietly.
- **Not that the payment was fair.** The price and its reason are in memory and
  in the journal; the chain records the amount, not whether the amount was right.
- **Not anything about a free step.** Step 1 costs nothing, is never invoiced and
  is never committed, so it has no commit to compare against. Verify says
  `no commit for this step` and returns `matches: null`, not `true`.
- **Nothing at all under `PAYMENTS=fake`.** A fake payment produces no commit
  transaction. Every step reports `no commit for this step (free step, or the
  fake payment backend)`.

## The three hashes it compares

`verify_step` in
[`api.py:1151`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/api.py#L1151)
puts three values side by side, plus the memo.

| Field | Where it comes from |
| --- | --- |
| `output_sha256_stored` | the sha256 the agent **recorded on the step** at the moment it delivered the work |
| `output_sha256_recomputed` | sha256 of the output text **as it is in memory right now**, computed on this request |
| `onchain_hash` | the `outputHash` decoded out of the `Committed(memo, outputHash)` log in that step's commit transaction receipt, fetched live from Base Sepolia |

Plus `onchain_memo`, the indexed memo topic from that same log, checked against
`keccak256("<job_id>:<step>")` recomputed locally — so a commit for a *different*
job or step cannot be passed off as this one's.

The verdict is `matches = (onchain_hash == "0x" + recomputed) and (onchain_memo == memo)`,
and the failure is reported with the reason that distinguishes it:

| `reason` | What happened |
| --- | --- |
| `matches on-chain commit` | all three agree |
| `the stored output no longer hashes to its recorded sha256; the on-chain hash matches the original` | **the store has been altered**: recomputed ≠ stored, and the chain agrees with stored |
| `the on-chain hash differs from the output the buyer holds` | recomputed = stored, but the chain says something else |
| `the committed memo is not this job and step` | the transaction commits a real hash, for something else |
| `no Committed event from the receipts contract in this transaction` | the recorded commit tx carries no such log |
| `no commit for this step (free step, or the fake payment backend)` | nothing was ever committed |
| `transaction not found on Base Sepolia` / `rpc unavailable: …` | the chain could not be read; **not** reported as a mismatch |

An RPC that will not answer is never a pass and never a failure. It is its own
outcome, which is why `matches` is a three-valued field.

Results are cached per `(job_id, step, commit_tx)` for `cache_ttl_seconds`, which
is 60 — a commit transaction's receipt is immutable, so re-reading it on every
page refresh would buy nothing.

## A real result

`GET /api/jobs/9f3dc77280a6/verify` against the live store, one step abridged:

```json
{
  "job_id": "9f3dc77280a6",
  "job_type": "audit",
  "checked_at": "2026-09-09T08:07:25Z",
  "cache_ttl_seconds": 60,
  "steps": [
    {
      "step": 1, "name": "scope", "cached": true,
      "output_sha256_stored":     "b0a8d30213fbfb176e875882e93456709a3fa03a025a736275addc92f994a5aa",
      "output_sha256_recomputed": "b0a8d30213fbfb176e875882e93456709a3fa03a025a736275addc92f994a5aa",
      "onchain_hash": null, "tx": null, "block": null,
      "matches": null,
      "reason": "no commit for this step (free step, or the fake payment backend)"
    },
    {
      "step": 2, "name": "findings", "cached": true,
      "memo":         "0xa321a45b5629576f602133485b3ad434ecda94c33b290cb7e8dafb1dcde9db8c",
      "onchain_memo": "0xa321a45b5629576f602133485b3ad434ecda94c33b290cb7e8dafb1dcde9db8c",
      "output_sha256_stored":     "023ea28debaa9f07f9de262c74f7b7250ee2fa2cbd855cc00c1e3e79559bcc4c",
      "output_sha256_recomputed": "023ea28debaa9f07f9de262c74f7b7250ee2fa2cbd855cc00c1e3e79559bcc4c",
      "onchain_hash":           "0x023ea28debaa9f07f9de262c74f7b7250ee2fa2cbd855cc00c1e3e79559bcc4c",
      "tx": "0x8818015d3bc57910a384ea046bb911dada6958a2161b7ef5a1de197fbfae6b66",
      "block": 46516283,
      "matches": true,
      "reason": "matches on-chain commit"
    }
  ],
  "summary": { "checked": 4, "matches": 3, "mismatches": 0, "no_commit": 1 },
  "source": "archived_entities (read-only); Committed events decoded from each commit transaction's receipt"
}
```

Note `"cached": true` on both steps. **A step served out of memory is verified
exactly like any other.** Serving from the cache is not serving something
unproved: the cached output carries its own commit, and the same three hashes are
compared.

## The tamper test

Beat 3b of `scripts/demo_live.sh` does not argue that verify would catch an
altered store. It alters one and checks.

The store is copied. One byte in the middle of step 2's output is changed through
the SDK — a real write through `put_job_entity`, not a hex edit — and the step's
**recorded** `output_sha256` is deliberately left as it was, which is what a
tamperer would want. A second server is started against the copy, and the same
`verify` endpoint is called against the same chain.

The result, asserted by the script:

| Step | `matches` | Why |
| --- | --- | --- |
| 2 | **false** | `output_sha256_recomputed` ≠ `output_sha256_stored`, and `onchain_hash` equals `"0x" + output_sha256_stored` — the chain still holds the original |
| 3 | true | untouched |
| 4 | true | untouched |

with the reason on step 2 reading:

```text
the stored output no longer hashes to its recorded sha256; the on-chain hash
matches the original
```

That sentence is the whole point. The failure is not vague — it says which of the
two halves moved. The chain and the recorded hash agree with each other and
disagree with the bytes in the store, which localises the tampering to the store
and to that one step. The other three steps of the same job still verify, so the
answer is not "this job is bad" but "this step's output is no longer the one that
was paid for."

The tamper test edits a discarded copy and throws it away. The real store is
never modified.

## Verifying from elsewhere

| From | How |
| --- | --- |
| the app | the **Verify** button on any job page |
| HTTP | `GET /api/jobs/{job_id}/verify` — see [API](#api) |
| an agent | the `turnstyl_verify` MCP tool — see [MCP](#mcp) |
| the report | every Markdown report ends with a verification table of sha256 against the `Committed` event |
| by hand | recompute `keccak256("<job_id>:<step>")`, open the commit transaction on [basescan](https://sepolia.basescan.org/address/0xD2Bb3c9741D7c26A8B161895bb91471706B17477), read `outputHash` out of the `Committed` log, and `sha256sum` the output you were given |

The last row is the one that matters: none of the first four require trusting
turnstyl's own code.

Verification reads the outputs, so it is visible to the wallet that paid for them
and to the operator, and not to the public. See [Security](#security).
