# Overview

turnstyl is a metering and memory layer for agents that sell work.

An agent that charges per unit of work has to answer five questions no model
call can answer: what does this step cost, has this buyer paid, do they get
credit, have I already done this, and what did I promise and did I deliver it.
turnstyl answers all five out of one Sibyl Memory file, and writes every answer
back as a journal entry naming the facts it rested on. The meter is auditable
rather than asserted.

Two services are wired up on top of it — a Solidity security audit and a
Foundry test suite — but they are demonstrations of the layer, not the claim. A
service is a spec. The engine, the memory, the payments, the credit rules and
the on-chain verification underneath are shared, so adding a service is adding a
spec and not a code path.

## The shape of a job

A job is an ordered list of steps against one input. Each step has a name, a
base price in USDC, a system prompt, and an optional mechanical gate the answer
has to survive.

1. **Submit.** A buyer hands the agent a Solidity file and picks a service. The
   agent hashes the source, opens a job, runs step 1 free, and issues the
   invoice for step 2.
2. **Quote.** The price of the next step is the base price times three
   multipliers, each of which reads a row in memory. The invoice carries the
   sentence that produced it.
3. **Pay.** The buyer settles the invoice on one of two rails: x402, which is
   gasless, or the receipts contract on Base Sepolia.
4. **Run.** The agent sees the payment, runs the step, puts the answer through
   its gate, records the output and its sha256, and commits that hash on chain.
5. **Repeat** until the last step, at which point the job closes and its outputs
   are copied into the cache that prices the next job for the same contract.

Every one of those transitions is a journal event. The event names the memory
rows it read, what it did, and what it expects next — so a decision can be
replayed from the rows that produced it.

## What makes it different

**The price is read, not configured.** A step costs half as much when this
contract's output for it is already in the store, half as much again when the
recorded average token cost for that step has run hot, and a tenth less for a
buyer the reflection pass has watched settle promptly. Every multiplier names
the memory row behind it in the invoice text.

**Trust is a computed tier, not an opinion.** Credit is extended after three
jobs close with every paid step settled; a job that closes with delivered work
unpaid puts the buyer in arrears, and arrears that outlive a 24 hour grace
period become a default. Two defaults block the buyer, and a block is worked off
rather than permanent. Every one of those rules is a pure function of the ledger
in [`policy.py`](https://github.com/shrooms08/turnstyl/blob/master/src/turnstyl/policy.py),
with no clock, no network and no memory client of its own.

**The work is provable both ways.** Memory holds the output; the chain holds the
sha256 the agent published when it was paid. `verify` recomputes the hash and
compares. Neither half proves anything alone, which is exactly why deleting the
file cannot be undone from the chain.

**A program buys the same way a person does.** The MCP server signs in with its
own wallet, is quoted per step, pays in USDC, and sees only what that wallet is
entitled to see. The selling agent cannot tell the difference and does not try.

## The delete test

Delete `data/turnstyl.db` and the agent forgets it was ever paid: it invoices
the same buyer for a step that buyer already bought, and the buyer pays for it a
second time. Both payments are real, on Base Sepolia, from the same wallet, 224
seconds apart, under two different memos, because the second job has an id the
first job's memo cannot be recomputed from —
[0.50 USDC at block 46506401](https://sepolia.basescan.org/tx/0xff0ad9caa24bed8c591f5010e8ce85683f8c0486aecad7bf9e564962c6d491af)
and then
[0.50 USDC again](https://sepolia.basescan.org/tx/0x6c5aa73f0e8d40a1f87a3a67a53f7d40d2caa29df75a007846c72dbf1ec06e34).
The chain kept both receipts and neither of them tells the agent it had already
collected the first one.

## Where to go next

| If you want to | Read |
| --- | --- |
| run it in five minutes | [Quickstart](#quickstart) |
| understand the vocabulary and the rules | [Concepts](#concepts) |
| see exactly what memory holds and what each row changes | [Memory](#memory) |
| see the two services, or write a third | [Services](#services) |
| pay for a step | [Buying](#buying) |
| check the work against the chain | [Verify](#verify) |
| buy from an agent harness | [MCP](#mcp) |
| call the HTTP API | [API](#api) |
