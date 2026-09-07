# turnstyl over MCP

`turnstyl-mcp` is a Model Context Protocol server that lets any agent harness
buy from a turnstyl instance the way a person does in the browser: read what is
on offer, submit a contract, get quoted, pay the invoice in USDC, collect the
work, and verify it against the chain.

## Agent-to-agent commerce

The buyer here is a program with its own wallet. It is not a person clicking a
button, and it is not an operator with special access. It holds a private key,
signs a login message with it, is invoiced per step, signs a USDC transfer
authorisation, and gets exactly what it paid for and nothing more. The selling
agent cannot tell the difference and does not try to: same four metered steps,
same receipts contract on Base Sepolia, same memory, same trust rules. A machine
that pays three jobs in full earns credit on the fourth on exactly the terms a
human buyer does.

That is the whole point of the meter. A price per step and a receipt per payment
are what let two programs transact without either one being trusted in advance.

## What it is not

It is a **client**. It never opens the agent's memory file, never imports
`turnstyl`, and knows nothing about Sibyl Memory. Everything goes over the same
HTTP API the browser uses, with the same wallet signatures and the same
visibility rules: this server sees exactly what the wallet in
`BUYER_PRIVATE_KEY` is entitled to see, which is its own jobs and nobody else's.

Its dependencies are `mcp`, `httpx`, `eth-account` and `x402`. None of the
agent's own stack.

## Install and configure

```bash
pip install turnstyl-mcp        # or, from this repo: uv pip install -e .
```

Two environment variables:

| variable | what it is | default |
| --- | --- | --- |
| `TURNSTYL_API` | the operator's **API** origin | `http://127.0.0.1:8787` |
| `BUYER_PRIVATE_KEY` | the wallet that pays | none |

`TURNSTYL_API` is the API, not the published page. `https://<user>.github.io/...`
is static and answers no API calls; the server refuses that value with a message
saying so. Point it at the tunnel URL the operator publishes, or at
`http://127.0.0.1:8787` when the agent runs on the same machine.

`BUYER_PRIVATE_KEY` is read once at startup, held in a module-private, and never
logged, returned, or put in an error message. The address it derives is public
and appears freely. **If it is unset, the paying tool is not registered at all**
and the other seven still work: an agent with no wallet can read the services,
the status, and any job it is shown, and can do nothing that costs money.

A `.env` in the working directory the harness starts the server in is read too,
for variables the environment did not already set. Deliberately not dotenv's
default upward search, which would find the `.env` of whatever checkout the
package happened to be installed from.

## Tools

| tool | what it does | spends |
| --- | --- | --- |
| `turnstyl_services` | the services on offer, their steps and prices | no |
| `turnstyl_status` | agent up, chain, receipts contract, x402 availability, public stats | no |
| `turnstyl_submit` | opens a job, runs the free scope step, returns it and the next invoice | no |
| `turnstyl_quote` | the open invoice, its price and the reason for that price, and the buyer's standing | no |
| `turnstyl_pay_and_run` | **pays** the open invoice in USDC and returns the work | **yes** |
| `turnstyl_job` | the whole job: steps, outputs, decisions as sentences | no |
| `turnstyl_verify` | each paid output against its on-chain commit | no |
| `turnstyl_report` | the job as one Markdown document | no |

Every tool returns a compact object whose first key is `summary`: one line that
reads on its own. Failures come back in the same shape, with `ok: false` and a
sentence saying what to do, never a traceback.

### The one that spends money

`turnstyl_pay_and_run(job_id, max_usdc)` is the only tool that moves value, and
its description says so in its first line. It is annotated `destructiveHint:
true`.

`max_usdc` is **required and has no default**. The tool compares it against the
invoice before doing anything: if the invoice is larger it refuses, spends
nothing, and says which limit to raise and to what. There is no way to call this
tool without stating a ceiling.

Payment is EIP-3009 `transferWithAuthorization` over x402: the buyer signs, a
facilitator submits, and the buyer needs no ETH and grants no approval. See
[X402.md](X402.md) for the wire formats. When the agent runs the fake payments
backend the invoice is settled without a chain transaction and the response says
so; when the agent is on Base but x402 is unavailable, the tool refuses rather
than paying some other way.

After settling it waits up to 90 seconds for the selling agent to run the paid
step, then returns the output, the settlement transaction, the commit
transaction and the next invoice. If the step has not finished in time it says
the payment stands and the work is owed, rather than pretending nothing
happened.

## Testing it

```bash
scripts/test_mcp.sh
```

Starts a throwaway turnstyl on a private port with `PAYMENTS=fake`, drives the
MCP server over stdio, and checks every tool's shape: the annotations, the
required `max_usdc`, the refusal path, the simulate path, and that a server with
no key does not register the paying tool. The real gasless purchase, end to end
on Base Sepolia, is beat 10 of `scripts/demo_live.sh`.
