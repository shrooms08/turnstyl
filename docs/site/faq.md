# FAQ

## Is this real, or a testnet?

Both, and the line is worth drawing precisely.

**Real:** the model writes the work, the Sibyl Memory file holds it and really
does lose everything when deleted, USDC moves on real wallet signatures,
`forge build` and `forge test` really run against the model's answer, and the
commits are real transactions on a real contract.

**Testnet:** the chain is **Base Sepolia**, so the USDC has no value. The
receipts contract is
[`0xD2Bb3c9741D7c26A8B161895bb91471706B17477`](https://sepolia.basescan.org/address/0xD2Bb3c9741D7c26A8B161895bb91471706B17477)
on chain 84532, and every transaction linked anywhere on this site can be opened
in an explorer.

**Simulated, and only when you ask for it:**

- `MOCK_LLM=1` serves canned step outputs so the offline demo needs no API key.
- `PAYMENTS=fake` settles invoices in memory. Its transaction hashes begin with
  `0xfake` and are never rendered as explorer links.
- The `simulate payment` and `settle` endpoints exist **only** on the fake
  backend and answer 404 under `PAYMENTS=base`, as do the x402 endpoints, which
  have nothing to settle when payments are fake.
- The tamper test in the live demo edits a **discarded copy** of the store and
  throws it away. The real store is never modified.
- The rate and daily caps are in-process counters, so they reset on restart.

Neither `MOCK_LLM` nor `PAYMENTS=fake` is on in anything served publicly.

## Who is it for?

Anyone building an agent that charges for work rather than for tokens.

The audit and the test suite are demonstrations. What is reusable is the layer
under them: a step is priced from what the store remembers about that step and
that buyer, an invoice carries the arithmetic that produced it, credit is a
computed tier rather than a config flag, and every delivered output is hashed on
chain so the buyer can prove what they were given. Adding your own service is
adding a spec — see [Services](#services) for a worked third one.

If you are looking specifically for a Solidity audit, you can also just buy one:
[Quickstart](#quickstart) offline, or the live page with a wallet.

## What happens if the operator's machine is off?

The page stays up and says so. It is served from GitHub Pages, which is static;
the API and the worker run on the operator's Mac behind a Cloudflare tunnel.

When that machine is off, the page still tells the story and shows the brand, the
console reads `agent offline: the operator's machine is not reachable right now`,
and submit and pay are **held rather than failing**. It does not show stale
numbers, because `/api/stats` is the only source of the figures and there is
nothing to read.

Nothing is lost. The store is a file on disk; when the machine comes back, every
job resumes at the step it stopped at, because that is the whole point of the
layer. See [Deploy](#deploy) for how the tunnel and the publish work, and what
goes wrong with them.

## Why not just call the model yourself?

You can, and for a one-off you should. turnstyl is not a wrapper around a model
call; it is what you end up building the third time you try to charge for one.
Five questions a model call cannot answer:

| Question | What you would otherwise build |
| --- | --- |
| What does this step cost? | a price that reads your own cost history and what you already hold, not a constant in a config file |
| Has this buyer paid? | a ledger keyed to settlements you verified on chain, not to what the client told you |
| Do they get credit? | a trust rule with an earn-back path, a grace period, and a way out of a block |
| Have I already done this? | a content-addressed cache, so the second identical request costs 0 tokens instead of full price |
| What did I promise, and did I deliver it? | a hash on chain the buyer can check against the bytes they hold |

And the one that ties them together: **after a crash, a restart or a deploy, does
the agent still know all of the above?** A process that keeps this in memory
re-invoices for work it already delivered. That is not hypothetical — it is
the [delete test](#overview), and both payments are on chain.

If you are calling a model once, none of this applies. If you are selling the
call, all of it does, and the parts are not independent: pricing needs the cost
history, credit needs the settlement record, the cache needs the content hash,
and every one of them has to survive the process exiting.

## What does turnstyl not do?

Stated plainly, because a list of what a thing does not do is more useful than
another paragraph about what it does.

- **It does not custody funds.** The receipts contract moves USDC from buyer to
  agent in one call and never takes a balance. It has no owner, no pause and no
  upgrade path.
- **It does not judge whether the work is good.** [Verify](#verify) proves the
  output is the one that was paid for. It says nothing about whether the audit is
  right — a confidently wrong audit verifies perfectly.
- **It does not summarise anything with a model.** Journal summaries are
  f-string templates. Nothing in `src/turnstyl/` calls a model to summarise, so
  that primitive is deliberately not claimed.
- **It does not run on mainnet.** Base Sepolia only, chain 84532.
- **It is not multi-tenant.** One operator, one store, one agent wallet. The
  tenant string exists to keep turnstyl's rows apart from another local
  consumer's, not to serve several operators.
- **It does not do dispute resolution or refunds.** A buyer who is unhappy has
  the output, its hash on chain, and the journal event that priced it. There is
  no arbitration and no reversal path.
- **It does not chase debts.** A closed job with unpaid work suspends credit,
  refuses further paid work, and becomes a default after 24 hours. It sends
  nothing to anyone, and there is no collections step.
- **It does not integrate Virtuals**, and nothing here should be read as claiming
  otherwise. Exactly one partner stack is claimed: Base.
- **It claims no product-market fit.** The buyers in the live store are the
  operator's own test wallets. See [Evals](#evals).
- **It has no queue, no autoscaling and no HA.** One process, one SQLite file,
  one laptop, one tunnel. When the laptop is off, the agent is off.
- **It does not keep your contract private from the operator.** Outputs and
  sources are private from the public and from other buyers, and the operator
  holds the machine and the file. See [Security](#security).

## Can an agent buy from it?

Yes, on exactly the terms a person gets. `turnstyl-mcp` signs in with its own
wallet, is quoted per step, pays in USDC, and sees only what that wallet is
entitled to see. A machine that pays three jobs in full earns credit on the
fourth the same way a human buyer does. See [MCP](#mcp).

## Why is the second audit of the same contract so cheap?

Because it costs the agent nothing. The first job's outputs were copied into
`findings/<type>/<contract_hash>` when it closed, so the step is served out of
the store with no model call, and the price carries the ×0.5 cache multiplier on
top. In the evals: 0 tokens in, 0 tokens out, $0.0000, in 18 of 18 second passes.

The saving is passed on rather than pocketed, and the invoice says which memory
row it came from. That is the whole argument for the layer, stated as a number.

## What breaks when memory is deleted?

The agent re-invoices a buyer for steps that buyer already paid for, re-runs work
it already did, and treats a proven payer as a stranger with no credit.

The payments are still on chain and the outputs are still hashed there, but
nothing on chain tells the agent which invoice it already collected — the memo is
`keccak256("<job_id>:<step>")`, an opaque hash that means something only if you
already hold the job id, which is precisely what a deleted database no longer has.
So it charges again. See [Memory](#memory).

## Where do I report a problem?

<https://github.com/shrooms08/turnstyl>. Every script in `scripts/` fails loudly
with a message an operator can act on, so the output of the one that broke is
usually the whole bug report.
