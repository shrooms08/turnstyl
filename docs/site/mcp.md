# MCP

turnstyl ships a Model Context Protocol server, `turnstyl-mcp`, so any agent
harness can buy from a turnstyl instance the way a person does in the browser:
read what is on offer, submit a contract, get quoted, pay the invoice in USDC,
collect the work, and verify it against the chain.

The buyer here is a program with its own wallet. It holds a private key, signs a
login message with it, is invoiced per step, signs a USDC transfer authorisation,
and gets exactly what it paid for and nothing more. The selling agent cannot tell
the difference and does not try to: same metered steps, same receipts contract,
same memory, same trust rules. **A machine that pays three jobs in full earns
credit on the fourth on exactly the terms a human buyer does.**

## It is a client, not a second door

`turnstyl-mcp` never opens the agent's memory file, never imports `turnstyl`, and
knows nothing about Sibyl Memory. Everything goes over the same HTTP API the
browser uses, with the same wallet signatures and the same visibility rules: it
sees exactly what the wallet in `BUYER_PRIVATE_KEY` is entitled to see, which is
its own jobs and nobody else's.

Its dependencies are `mcp`, `httpx`, `eth-account` and `x402`. None of the
agent's own stack.

## The eight tools

| Tool | Signature | Spends | Annotations |
| --- | --- | --- | --- |
| `turnstyl_services` | `()` | no | readOnly |
| `turnstyl_status` | `()` | no | readOnly |
| `turnstyl_submit` | `(source: str, job_type: str = "audit")` | no | not readOnly, not destructive |
| `turnstyl_quote` | `(job_id: str)` | no | readOnly |
| `turnstyl_job` | `(job_id: str)` | no | readOnly |
| `turnstyl_verify` | `(job_id: str)` | no | readOnly |
| `turnstyl_report` | `(job_id: str)` | no | readOnly |
| `turnstyl_pay_and_run` | `(job_id: str, max_usdc: float)` | **yes** | **destructive**, not idempotent |

Every tool returns a compact object whose first key is `summary`: one line that
reads on its own. Failures come back in the same shape, with `ok: false` and a
sentence saying what to do, never a traceback.

### `turnstyl_services()`

What this agent sells, with the price of every step. No wallet needed.

Returns `summary`, `default` (the default service id), and `services[]` with
`id`, `name`, `description`, `total_usdc`, and `steps[]` of `n`, `name`,
`base_price_usdc`, `gate`.

### `turnstyl_status()`

Is the agent up, what chain it settles on, and the public figures. No wallet
needed. **Call this first when anything else fails** — it says whether the agent
is reachable at all, whether its memory file is present, and whether the gasless
x402 rail is available.

Returns `summary`, `online`, `api`, `network`, `chain_id`, `receipts_address`,
`explorer`, `payments_backend`, `x402 {enabled, network, reason}`,
`memory_missing`, `buyer_address`, `can_pay`, and
`stats {jobs, jobs_completed, buyers, usdc_settled, decisions, served_from_memory}`.

### `turnstyl_submit(source, job_type="audit")`

Opens a job, runs step 1 free, returns its output and the next invoice. Spends
nothing. `source` is the complete Solidity file as text, 1 to 65536 bytes.

Signs in with the configured wallet first: one signature over a message the agent
issues, no transaction and no spend. Submitting the same source twice **resumes**
the open job rather than creating a second one, so nothing is charged twice.

Returns `summary`, `job_id`, `job_type`, `resumed`, `decision`, `scope` (the step
1 output), `next_invoice {step, step_name, amount_usdc, memo, price_reason}`, and
`credit {applies, trust_tier, jobs_until_credit}`.

### `turnstyl_quote(job_id)`

What the next step costs and why. Spends nothing. The `price_reason` is the
pricing rules' own words — the base price, whether the work is already in memory,
any surcharge, and this buyer's standing. **Read it before calling
`turnstyl_pay_and_run`.**

Returns `summary`, `job_id`, `status`, `current_step`, `last_step`, `invoice`,
and `trust {tier, jobs_until_credit, would_decide, explanation}`.

### `turnstyl_job(job_id)`

Everything this wallet is entitled to see about one job. Outputs are visible only
to the wallet that paid for them; another wallet gets the public shape, with
prices and transactions but no text, and `readable: false`.

Returns `summary`, `job_id`, `job_type`, `status`, `current_step`, `last_step`,
`readable`, `injection_flags`, `steps[]` with `output`, `open_invoice`, and
`decisions[]` of `{at, decision, step, summary}`.

### `turnstyl_verify(job_id)`

Each paid output against its on-chain commit. Needs both halves: the chain holds
the sha256 the agent published when it was paid, memory holds the output. A step
with no commit says so rather than passing quietly. See [Verify](#verify).

Returns `summary`, `job_id`,
`summary_counts {checked, matches, mismatches, no_commit}`, and `steps[]` of
`{step, name, matches, reason, onchain_hash, commit_tx, block}`.

### `turnstyl_report(job_id)`

The whole job as one Markdown document: every step's price, how it was paid, its
output in full, and a verification table of sha256 against the `Committed` event.

Returns `summary`, `job_id`, `markdown`, `bytes`.

### `turnstyl_pay_and_run(job_id, max_usdc)`

**The only tool that moves value**, and its description says so in its first
line. It is annotated `destructiveHint: true`.

`max_usdc` is **required and has no default**. The tool compares it against the
invoice before doing anything: if the invoice is larger it refuses, spends
nothing, and says which limit to raise and to what. There is no way to call this
tool without stating a ceiling.

Payment is EIP-3009 `transferWithAuthorization` over x402: the buyer signs, a
facilitator submits, and the buyer needs no ETH and grants no approval. When the
agent runs the fake payments backend the invoice is settled without a chain
transaction and the response says `simulated`; when the agent is on Base but
x402 is unavailable, the tool **refuses rather than paying some other way**.

After settling it waits up to 90 seconds (polling every 2) for the selling agent
to run the paid step, then returns the output, the settlement transaction, the
commit transaction and the next invoice. If the step has not finished in time it
says the payment stands and the work is owed, rather than pretending nothing
happened.

Returns `summary`, `paid`, `ran`, `step`, `step_name`, `output`,
`output_sha256`, `compiles`, `cached`,
`settlement {method, amount_usdc, tx, payer}`, `commit_tx`, `job_status`, and
`next_invoice`. On refusal: `ok: false` and a reason, with nothing spent.

## Install

`turnstyl-mcp` is **not published to PyPI yet.** Install it from the repo:

```bash
git clone https://github.com/shrooms08/turnstyl
cd turnstyl
uv venv --python 3.12
uv pip install -e ".[mcp]"
```

That puts the `turnstyl-mcp` executable at `.venv/bin/turnstyl-mcp`. Use its
absolute path in the configs below unless that virtualenv is on your `PATH`.

Two environment variables:

| Variable | What it is | Default |
| --- | --- | --- |
| `TURNSTYL_API` | the operator's **API** origin | `http://127.0.0.1:8787` |
| `BUYER_PRIVATE_KEY` | the wallet that pays | none |

`TURNSTYL_API` is the API, not the published page. `https://<user>.github.io/…`
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

### Claude Code

```bash
claude mcp add turnstyl \
  --env TURNSTYL_API=http://127.0.0.1:8787 \
  --env BUYER_PRIVATE_KEY=0x... \
  -- /abs/path/to/turnstyl/.venv/bin/turnstyl-mcp
```

### Codex

```bash
codex mcp add turnstyl \
  --env TURNSTYL_API=http://127.0.0.1:8787 \
  --env BUYER_PRIVATE_KEY=0x... \
  -- /abs/path/to/turnstyl/.venv/bin/turnstyl-mcp
```

or in `~/.codex/config.toml`:

```toml
[mcp_servers.turnstyl]
command = "/abs/path/to/turnstyl/.venv/bin/turnstyl-mcp"
args = []
env = { TURNSTYL_API = "http://127.0.0.1:8787", BUYER_PRIVATE_KEY = "0x..." }
```

### Cursor

`~/.cursor/mcp.json` for every project, or `.cursor/mcp.json` for one:

```json
{
  "mcpServers": {
    "turnstyl": {
      "command": "/abs/path/to/turnstyl/.venv/bin/turnstyl-mcp",
      "args": [],
      "env": {
        "TURNSTYL_API": "http://127.0.0.1:8787",
        "BUYER_PRIVATE_KEY": "0x..."
      }
    }
  }
}
```

A key in a config file is a key on disk. If that is not acceptable, leave
`BUYER_PRIVATE_KEY` out entirely — the seven read-only tools still register — or
export it into the environment the harness is launched from.

## A worked example: an agent buying an audit

Verbatim `summary` lines from an actual stdio session against a turnstyl running
on the fake backend. Nothing here is composed; the transcript is what the tools
returned.

**1. Check the agent is there.**

```text
turnstyl_status()
→ turnstyl is up at http://127.0.0.1:8798 on Base Sepolia (payments=fake);
  x402 is unavailable (payments backend is 'fake'; x402 settles real USDC);
  memory present; 1 jobs and 1.50 USDC settled so far.
```

**2. See what is on offer.**

```text
turnstyl_services()
→ 2 service(s) on offer: audit (1.50 USDC), tests (1.40 USDC).
  Step 1 is free on each.
```

**3. Submit the contract.** Step 1 runs free and comes back with the invoice for
step 2.

```text
turnstyl_submit(source=<Vault.sol>, job_type="audit")
→ job 6084b88f2512 open on audit; step 1 ran free; step 2 (findings) is
  invoiced at 0.25 USDC; this buyer is new and needs 2 more fully paid job(s)
  for credit
```

Note the price: 0.25, not the 0.50 base. This store had already audited that
contract, so `findings/audit/<hash>` holds step 2 and the ×0.5 cache multiplier
applied. The agent will serve it out of memory with no model call.

**4. Read the quote before spending.**

```text
turnstyl_quote(job_id="6084b88f2512")
→ step 2 (findings) of job 6084b88f2512 costs 0.25 USDC; buyer is new
```

**5. Set a ceiling that is too low, on purpose.** This is what a careful agent
does first.

```text
turnstyl_pay_and_run(job_id="6084b88f2512", max_usdc=0.10)
→ refusing to pay: step 2 of job 6084b88f2512 costs 0.25 USDC, above the 0.10
  USDC limit you set. Nothing was spent. Raise max_usdc to at least 0.25 to go
  ahead.
```

`ok: false`, and nothing was spent.

**6. Buy the three paid steps.**

```text
turnstyl_pay_and_run(job_id="6084b88f2512", max_usdc=1.00)
→ paid 0.25 USDC for step 2 (findings) of job 6084b88f2512 over simulated,
  and the agent ran it; next up is step 3 (patch) at 0.38 USDC

turnstyl_pay_and_run(job_id="6084b88f2512", max_usdc=1.00)
→ paid 0.38 USDC for step 3 (patch) of job 6084b88f2512 over simulated,
  and the agent ran it; next up is step 4 (verify) at 0.12 USDC

turnstyl_pay_and_run(job_id="6084b88f2512", max_usdc=1.00)
→ paid 0.12 USDC for step 4 (verify) of job 6084b88f2512 over simulated,
  and the agent ran it; job complete
```

0.75 USDC for the whole audit instead of 1.50, every step halved because the
store had already done this contract. `settlement.method` reads `simulated` here
because the agent is on the fake backend; against a live agent it reads `x402`
and carries a real transaction hash.

**7. Collect and check.**

```text
turnstyl_job(job_id="6084b88f2512")
→ job 6084b88f2512 (audit) is complete: 4 of 4 steps done,
  10 decision(s) recorded.

turnstyl_verify(job_id="6084b88f2512")
→ job 6084b88f2512: 0 of 4 step(s) match their on-chain commit, 0 differ,
  4 have no commit.

turnstyl_report(job_id="6084b88f2512")
→ report for job 6084b88f2512, 6,195 bytes of Markdown
```

`4 have no commit` is correct and is the honest answer here: this agent was on
the fake backend, so no commit transaction was ever sent and there is nothing on
chain to compare against. Against a live agent the same call reports 3 of 4
matching, with step 1 — the free step, never invoiced and never committed — as
the one with no commit. See [Verify](#verify).

## Testing it

```bash
scripts/test_mcp.sh
```

Starts a throwaway turnstyl on a private port with `PAYMENTS=fake`, drives the
MCP server over stdio, and checks every tool's shape: the annotations, the
required `max_usdc`, the refusal path, the simulate path, and that a server with
no key does not register the paying tool. The real gasless purchase, end to end
on Base Sepolia, is beat 10 of `scripts/demo_live.sh`.

Fuller protocol notes:
[docs/MCP.md](https://github.com/shrooms08/turnstyl/blob/master/docs/MCP.md).
