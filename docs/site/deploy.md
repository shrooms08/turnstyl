# Deploy

The published page is always up. The agent behind it is not: the API and the
worker run on the operator's own Mac, reached through a Cloudflare quick tunnel
whose URL the page reads from `config.js`. When that machine is off, the page
still tells the story and shows the brand, the console reads `agent offline: the
operator's machine is not reachable right now`, and submit and pay are held
rather than failing.

That split — a static page anyone can open, an agent that is only live while a
laptop is on — is the shape of this deployment, and every failure mode below
falls out of it.

## Running the agent

`.env` is gitignored and never printed. For a live run it must define:

| Variable | What |
| --- | --- |
| `BASE_SEPOLIA_RPC` | the RPC the agent reads `Paid` logs and commit receipts from |
| `USDC_ADDRESS` | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` on Base Sepolia |
| `RECEIPTS_ADDRESS` | `0xD2Bb3c9741D7c26A8B161895bb91471706B17477` |
| `RECEIPTS_DEPLOY_BLOCK` | where `check_paid` starts scanning from |
| `AGENT_ADDRESS`, `AGENT_PRIVATE_KEY` | the wallet that is paid and that sends commits |
| `BUYER_ADDRESS`, `BUYER_PRIVATE_KEY` | the test buyer the scripts and the MCP server use |
| `ANTHROPIC_API_KEY` | for a real model run |
| `OPERATOR_TOKEN` | generated at startup if absent; the operator view's credential |

The agent itself is one command:

```bash
.venv/bin/turnstyl serve --with-worker --db ./data/turnstyl.db
```

`--with-worker` runs the worker loop in a background thread of the same process,
so a paid step runs itself. `--interval` sets the pass interval (default 3
seconds). Without it you have to run `turnstyl job run <id>` by hand, or
`turnstyl worker` as a second process.

The worker's pass does four things: settles anything the payment backend has
since seen, promotes overdue arrears to defaults, advances every active job, and
— hourly — runs the reflection pass that writes `pattern/<address>`. Its heartbeat
appears in `GET /api/status` under `worker`, which is how a watchdog outside the
process can see a thread inside it.

## The tunnel

```bash
scripts/tunnel.sh              # foreground; Ctrl-C takes it down
scripts/tunnel.sh --daemon     # detached; survives the terminal closing
scripts/tunnel.sh --status     # running, and is the page pointing at it
scripts/tunnel.sh --stop       # stop it and publish an empty config.js
scripts/tunnel_check.sh        # from anywhere: is the published page reachable
```

`--daemon` starts three processes: `caffeinate` so the Mac does not sleep,
`turnstyl serve --with-worker` with `PAYMENTS=base` and the real model, and
`cloudflared tunnel --url`. It writes the tunnel URL into `web/config.js` and
pushes only that file to `gh-pages`. On the way down it stops all three and
publishes an **empty** `config.js`, so the page says "agent offline" rather than
pointing at a hostname that is gone.

**Publishing is never assumed.** Every write of `web/config.js` is read back,
every push is checked against what `gh-pages` actually serves, and `--status`
reports *running but unpublished* as its own outcome with exit code 2 rather than
as success. `--daemon` waits for the URL to be *published*, not merely printed:
`cloudflared` announces a hostname within a second or two, but that hostname still
has to answer a request and then reach `gh-pages`, and only that last step makes
the page live.

Once running, a watchdog checks every pass and repairs what it can: `serve` down
→ restart it; the worker stalled → restart `serve` to revive it; three
consecutive public failures → rebuild the tunnel, wait for the new hostname to
answer, and republish. It also re-proves the published URL against `gh-pages`
periodically, because a healthy tunnel nobody can reach from the page is still an
outage.

## GitHub Pages

```bash
scripts/pages.sh                 # publish the whole page
scripts/pages.sh --config-only   # publish only web/config.js
```

No build step. `index.html`, `app.html`, `docs.html`, `docs/site/*.md`,
`config.js` and `static/` are copied as they are into a `gh-pages` git worktree,
committed and pushed. A `.nojekyll` file is dropped in so Pages serves the tree
verbatim.

Two safeguards worth knowing about:

- **A full publish never knocks a live session offline.** The local `config.js`
  is the empty default unless `tunnel.sh` wrote it, so when `gh-pages` already
  publishes a URL and the local file is empty, the published one is kept.
- **Pages is enabled through the API on the first run** if the `gh` CLI is
  available, and otherwise the script prints the two clicks to do it by hand.

`web/config.js` is marked `skip-worktree` in this checkout, so git will not report
changes to it and `git add web/config.js` would ignore them. That is deliberate —
`pages.sh` copies the file with `cp`, never with `git add` — but it does mean a
write that did not land would be invisible, which is why it is read back before
anything is published.

The live page: <https://shrooms08.github.io/turnstyl/>

## Known failure modes

Each of these has been hit for real. The fix for each is different, which is why
`tunnel_check.sh` keeps them apart instead of reporting one "offline".

### 1. `no URL published` — the publish never happened

`gh-pages` serves the empty default. The page says "agent offline". **Nothing is
wrong with the tunnel.**

```text
OFFLINE: config.js publishes no API URL (the operator has not run scripts/tunnel.sh)
```

or, when a tunnel *is* running here:

```text
UNPUBLISHED: config.js publishes no API URL, but a tunnel is running here at https://….trycloudflare.com
```

**Fix:** `scripts/tunnel.sh --status` says the same thing with the log to read.
Republish by hand if needed:

```bash
printf 'window.TURNSTYL_API = "%s";\n' "<the running url>" > web/config.js
scripts/pages.sh --config-only
```

Usual cause: a detached process with no git credentials, or a rejected push. The
daemon log at `./data/tunnel.log` carries a `PUBLISH FAILED` line when that
happens.

### 2. `published != running` — a stale publish

A tunnel is running on this Mac at one hostname and the page points at another.
Visitors reach a dead hostname while the operator sees a healthy agent. This can
only be told apart from the operator's own machine, so `tunnel_check.sh` checks
it only when `./data/tunnel.pid` says a supervisor is alive here.

```text
MISMATCH: the page publishes https://a….trycloudflare.com but the tunnel
running here is https://b….trycloudflare.com
```

**Fix:** the watchdog republishes on its next pass. To force it,
`scripts/tunnel.sh --stop` then `--daemon`.

### 3. `published but down` — DNS

`curl` exit code 6 is "could not resolve host", and for a hostname `cloudflared`
minted seconds ago that usually means **this machine's resolver has not caught up
yet**, not that the tunnel is dead. Visitors resolve it with their own DNS, so
refusing to publish over a local lookup would take the page down for everyone to
fix a problem only this Mac has.

`public_ok` therefore treats exit 6 specially: it asks a resolver directly with
`dig +short`, and retries against the edge with `curl --resolve host:443:<ip>`
before believing the hostname is unreachable. Any other curl failure is a real
one and is returned as such.

**Fix:** usually none — wait a few seconds. If `dig +short <host>` returns
nothing after a minute, the tunnel really is gone; the watchdog rebuilds it after
three consecutive failures.

### 4. VPN

A VPN or a split-DNS resolver on the operator's Mac is the most common cause of a
persistent case 3: `cloudflared` connects fine outbound, the hostname is live for
the rest of the world, and this machine cannot resolve or reach it. The symptom
is `tunnel_check.sh` reporting `OFFLINE` from the operator's own machine while
the page works for everyone else — or the watchdog rebuilding a perfectly healthy
tunnel every few minutes because its own health check keeps failing.

**Fix:** run the check from a phone on cellular, or from any machine that is not
on the VPN, to see which side is actually broken:

```bash
curl -s https://shrooms08.github.io/turnstyl/config.js
curl -s "<the URL in that file>/api/status"
```

If those work from off-VPN, the tunnel is fine and only the local health check is
lying. Drop the VPN before running `--daemon`, or the watchdog will keep tearing
down a working tunnel.

### 5. Stale schema — the agent is running code that cannot read its own store

A schema change lands, the running server is older (or newer) than the code that
wrote the store, and every read of the affected entity raises `extra_forbidden`.
This used to answer 500, and the page — which treats a failed fetch as "no data" —
showed a store full of jobs as **empty**.

Now the server refuses to start, naming the entity and the field:

```text
entity buyer/0x0964…eff8 has an unknown field 'tier_v2', which this build's
buyer model does not accept (extra_forbidden). Restart the agent on the code
that wrote this store, or run the migration that brings the store up to this
code.
```

At runtime the same condition is a **503** with that sentence, `GET /api/status`
reports `schema.ok: false`, and the page shows a restart banner instead of an
empty list.

**Fix:** restart the agent on the code that wrote the store. If you must serve
anyway, `--skip-schema-guard` starts it with a loud warning; the affected reads
still fail as 503s. See [Security](#security).

### 6. The store is gone

`turnstyl reset` was run, or the file was deleted. `GET /api/status` reports
`memory_missing: true`, `/api/stats` bypasses its cache so the change shows
instantly, and the page raises the red banner. The API and the worker **refuse to
recreate** a file that goes missing while they are running — only `serve` and
`worker` bootstrap one at startup — which is what keeps the delete beat honest.

**Fix:** there is none, and that is the point. See [Overview](#overview).

## Checking a deployment

```bash
scripts/check_ui.sh                      # the served page and API, no browser
scripts/check_ui.sh https://example.com  # against any base URL
scripts/test_api.sh                      # the API's shapes and guards
scripts/test_mcp.sh                      # every MCP tool over stdio
scripts/tunnel_check.sh                  # from anywhere: is the page reachable
```

`check_ui.sh` confirms the scene is wired into the page the server actually
sends, that both pages carry their headers and panels, that the docs site is
served and every documentation page fetches, and that the public stats endpoint
names nobody. It prints OK or FAIL per check and exits non-zero if any fail.
