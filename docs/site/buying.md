# Buying

There are two rails, and the agent treats them as one. Both move real USDC on
Base Sepolia and both end in the same two places: the step is marked paid in the
agent's memory with its settlement transaction, and the agent commits the sha256
of what it delivered to the receipts contract.

## The two rails

| | x402, gasless | receipts contract |
| --- | --- | --- |
| The buyer needs | USDC only | USDC **and a little ETH** for gas |
| The buyer signs | an EIP-3009 transfer authorisation (EIP-712 typed data) | an `approve` once, then a `pay` transaction |
| Who submits it | a facilitator, which pays the gas | the buyer |
| What lands on chain | a USDC `Transfer`, `from` the facilitator's address | a `Paid` log on the receipts contract |
| How the agent sees it | the `PAYMENT-RESPONSE` header, recorded in the `x402_payments` state document | `check_paid` reads `Paid` logs from the chain |
| On the page | `Pay 0.50 USDC, no gas` | `Pay on chain` |

x402 is the default when the facilitator is reachable; the receipts contract is
always there as the fallback. `GET /api/status` reports which:

```json
"x402": {
  "enabled": true,
  "network": "eip155:84532",
  "facilitator": "https://x402.org/facilitator",
  "reason": "facilitator https://x402.org/facilitator supports exact on eip155:84532"
}
```

From the command line:

```bash
.venv/bin/python scripts/buyer_pay_x402.py <job_id> <step>     # gasless
.venv/bin/python scripts/buyer_pay.py      <job_id> <step>     # on chain
```

Protocol details — read from the installed `x402` package and confirmed on the
wire against the live public facilitator — are in
[docs/X402.md](https://github.com/shrooms08/turnstyl/blob/master/docs/X402.md).

### What each rail needs from the buyer

**x402.** A wallet with USDC on Base Sepolia and nothing else. The buyer signs
EIP-712 `TransferWithAuthorization` against the USDC contract:

- **domain** `{name: "USDC", version: "2", chainId: 84532, verifyingContract: <USDC>}`
- **message** `{from, to, value, validAfter: "0", validBefore: now + 600, nonce: <32 random bytes>}`

No approval, no ETH, no transaction of the buyer's own. The facilitator submits
the transfer and pays the gas — which is what makes
[this settlement](https://sepolia.basescan.org/tx/0x70d44a1431e3dd3614bb32965e6e5447b5b97bbe5064aa958b45b749f8b3394e)
gasless: its `from` is the facilitator `0xd407e409…f1bf`, not the buyer.

**Receipts contract.** A wallet with USDC *and* a little ETH. The buyer approves
the receipts contract once, then calls `pay(bytes32 memo, uint256 amount)`, which
moves USDC from the buyer to the agent in one call and emits `Paid`. The buyer
pays the gas for that transaction.

## The memo

Both rails are bound to a specific invoice, but differently.

On the receipts rail the binding is the **memo**: `keccak256("<job_id>:<step>")`,
computed from a bare string anyone can recompute. `payments.invoice_memo_raw`
builds it deliberately without any turnstyl convention in it, so a buyer, an
explorer or an auditor can reproduce it. A payment counts when a `Paid` log
carries that memo, a payer matching the invoiced buyer, and at least the invoiced
amount. **The agent trusts the log, not the buyer.**

The memo is not part of x402, so there the binding is the **endpoint path**:
`POST /api/jobs/{job_id}/pay-x402/{step}` and
`POST /api/buyers/{address}/settle-x402/{job_id}/{step}`. The middleware prices
each request from memory at request time through a `DynamicPrice` callable, so
the 402 quotes this invoice and no other.

## The credit rules

Not every step is paid for before it runs. The full precedence is in
[Concepts](#concepts); what matters to a buyer is:

- **A new buyer pays up front.** Step 1 is free; steps 2 onward are invoiced and
  run once settled.
- **Three fully paid jobs earn credit.** Once three jobs have closed with every
  paid step settled, no open invoices and nothing owed, the tier becomes
  `trusted` and the agent runs steps on credit — `RUN_ON_CREDIT` — invoicing
  afterward.
- **Credit is a whole-job record, not a step count.** Twenty paid steps spread
  over jobs you never finished earn nothing.
- **Trust crosses services.** The ledger is not namespaced by job type, so
  audits earn credit on test suites.
- **A closed job with unpaid work suspends credit immediately.** Not after the
  grace period — immediately. The grace period only decides whether it is
  recorded as a default.
- **Two defaults block you, and a block is worked off.** Settle everything, then
  six paid steps, and you are `new` again — a stranger with a history, earning
  credit back by the ordinary rule.

The exact constants, and the one sentence a blocked buyer is told, are in
[Concepts](#concepts).

## From invoice to settled payment to commit

What actually happens, in order, once the buyer signs.

1. **Invoice.** `Engine._issue_invoice` prices the step from memory and writes
   the open invoice onto the job's HOT state document, with the memo, the amount
   and the reason sentence.
2. **The buyer pays.** Either rail. Nothing in the agent's memory changes yet.
3. **The agent sees it.**
   - *Receipts rail:* `BasePayments.check_paid` reads `Paid` logs for that memo
     from `RECEIPTS_DEPLOY_BLOCK` forward and matches payer and amount. The
     worker calls this on every pass, so no callback and no webhook is involved
     — the chain is the source of truth and the agent polls it.
   - *x402:* the middleware verifies the signature before the handler runs and
     settles on the way out. Because it settles *after* the handler returns, the
     handler records the step from a settlement it has not yet seen; turnstyl
     reads the `PAYMENT-RESPONSE` header in the same request and patches the
     record with the real hash before responding.
4. **`PAYMENT_SEEN`.** The moment any rail first sees the invoice settled,
   [`events.payment_seen`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/events.py#L42)
   writes one COLD journal event carrying the invoice's own `issued_at`. That is
   the single event the prompt-payer median is later read off.
5. **The decision.** On the next pass `policy.decide` returns `RUN_PAID`, naming
   the invoice, the amount and the settlement transaction in its reason.
6. **The step runs.** The model answers, the gate runs, the output and its
   sha256 go into the `job/<job_id>` entity, and `step_cost/<type>/<n>` folds in
   the tokens and seconds this run took.
7. **The commit.** `commit_output` sends `commit(bytes32 memo, bytes32 outputHash)`
   to the receipts contract — agent only — publishing the sha256 of exactly what
   the buyer received, under the same memo the payment carried. The transaction
   hash is written onto the step record.
8. **The ledger.** `paid_steps`, `paid_usdc` and the earn-back counters advance,
   the trust tier is recomputed from the counters, and the whole thing is
   journalled with the rows it read.
9. **The next invoice** is issued, or the job closes and its outputs are copied
   into `findings/<type>/<hash>`.

Steps 7 and 3 are the pair worth opening in an explorer: the same memo appears on
a payment and on a commit, so anyone can see what was bought and the hash of what
was delivered for it, without trusting the agent's account of either.

- payment: [`0xff0ad9ca…491af`](https://sepolia.basescan.org/tx/0xff0ad9caa24bed8c591f5010e8ce85683f8c0486aecad7bf9e564962c6d491af)
  — a `Paid` log under memo `0xb206842b…ba74a`
- commit: [`0xebce4ec0…58629`](https://sepolia.basescan.org/tx/0xebce4ec085ce3d2c6ecbcfa1c877a25ead0a4c77bccb3dc1a815b627d6558629)
  — a `Committed` log under the *same* memo, sent by the agent `0x4463aC72…FdA3`

## The contract

[`TurnstylReceipts.sol`](https://github.com/shrooms08/turnstyl/blob/master/contracts/src/TurnstylReceipts.sol),
Base Sepolia (chain 84532):
[`0xD2Bb3c9741D7c26A8B161895bb91471706B17477`](https://sepolia.basescan.org/address/0xD2Bb3c9741D7c26A8B161895bb91471706B17477)

- `pay(bytes32 memo, uint256 amount)` moves USDC from the buyer to the agent in
  one call and emits `Paid(memo, payer, amount)`.
- `commit(bytes32 memo, bytes32 outputHash)` publishes the sha256 of a delivered
  step and emits `Committed(memo, outputHash)`. Agent only.
- The contract **holds no custody**, never takes a token balance, and has no
  owner, no pause and no upgrade path.

Its own tests: `cd contracts && forge test`.

## Paying offline

`PAYMENTS=fake` settles invoices in memory instead of on chain, for running the
demo without a wallet. Its transaction hashes begin with `0xfake` and are never
rendered as explorer links. The `simulate payment` and `settle` endpoints exist
only on that backend and answer 404 under `PAYMENTS=base`, as do the x402
endpoints, which have nothing to settle when payments are fake.

```bash
export MOCK_LLM=1 PAYMENTS=fake
.venv/bin/turnstyl pay <job_id> <step>
```

A fake payment produces no commit transaction, so [verify](#verify) reports
`no commit for this step` rather than a match. That is the honest answer: there
is nothing on chain to check against.
