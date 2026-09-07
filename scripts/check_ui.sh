#!/usr/bin/env bash
# Check the served page and API without a browser.
#
#   scripts/check_ui.sh [base_url]
#
# Confirms the three.js scene is actually wired into the page the server sends:
# the import map, the module script, the #scene canvas, every state the scene
# must handle, and the "records" field the particle count is derived from.
# Prints OK or FAIL per check and exits non-zero if any check fails.

set -uo pipefail
cd "$(dirname "$0")/.."

# The report and verify endpoints are private to the buyer now, so the checks
# that read them do it as the operator. OPERATOR_TOKEN is generated into .env
# by the server itself and is never printed here.
[ -f .env ] && { set -a; . ./.env; set +a; }
OPTOK="${OPERATOR_TOKEN:-}"
OPH=(-H "Authorization: Bearer $OPTOK")

BASE="${1:-http://127.0.0.1:8787}"
TMPDIR_RUN=$(mktemp -d)
PAGE="$TMPDIR_RUN/page.html"      # index.html: the story and the scene
APP="$TMPDIR_RUN/app.html"        # app.html: the buyer and operator app
CSS="$TMPDIR_RUN/turnstyl.css"    # the stylesheet both pages load
ALL="$TMPDIR_RUN/all.txt"         # all three, for checks that do not care which
STATUS="$TMPDIR_RUN/status.json"
trap 'rm -rf "$TMPDIR_RUN"' EXIT
FAILURES=0

ok()   { printf "  OK   %s\n" "$1"; }
bad()  { printf "  FAIL %s\n" "$1"; [ -n "${2:-}" ] && printf "       %s\n" "$2"; FAILURES=$((FAILURES+1)); }

has()  { # has <description> <fixed-string> : anywhere in the UI
  if grep -qF -- "$2" "$ALL"; then ok "$1"; else bad "$1" "not found in the UI: $2"; fi
}
hasidx(){ # hasidx <description> <fixed-string> : must be on index.html
  if grep -qF -- "$2" "$PAGE"; then ok "$1"; else bad "$1" "not found in /: $2"; fi
}
hasapp(){ # hasapp <description> <fixed-string> : must be on app.html
  if grep -qF -- "$2" "$APP"; then ok "$1"; else bad "$1" "not found in /app.html: $2"; fi
}
noidx(){ # noidx <description> <fixed-string> : must NOT be on index.html
  if grep -qF -- "$2" "$PAGE"; then bad "$1" "still on the story page: $2"; else ok "$1"; fi
}

echo "turnstyl UI check against $BASE"
echo

# ---------------------------------------------------------------- server
CODE=$(curl -s -o "$PAGE" -w "%{http_code}" "$BASE/" 2>/dev/null)
if [ "$CODE" != "200" ]; then
  bad "GET / reachable" "HTTP ${CODE:-no response}. Start it with: .venv/bin/turnstyl serve --db ./data/ui.db"
  echo
  echo "RESULT: FAIL - the server is not answering, nothing else can be checked."
  exit 1
fi
ok "GET / returns 200 ($(wc -c < "$PAGE" | tr -d ' ') bytes)"

ACODE=$(curl -s -o "$APP" -w "%{http_code}" "$BASE/app.html" 2>/dev/null)
if [ "$ACODE" != "200" ]; then
  bad "GET /app.html reachable" "HTTP ${ACODE:-no response}"
  echo
  echo "RESULT: FAIL - the app page is not served, nothing else can be checked."
  exit 1
fi
ok "GET /app.html returns 200 ($(wc -c < "$APP" | tr -d ' ') bytes)"
CCODE0=$(curl -s -o "$CSS" -w "%{http_code}" "$BASE/static/turnstyl.css" 2>/dev/null)
[ "$CCODE0" = "200" ] && ok "GET /static/turnstyl.css returns 200 ($(wc -c < "$CSS" | tr -d ' ') bytes)" \
  || bad "GET /static/turnstyl.css returns 200" "HTTP ${CCODE0:-no response}"
cat "$PAGE" "$APP" "$CSS" > "$ALL"

# ---------------------------------------------------------------- scene wiring
echo
echo "three.js scene"
hasidx "import map present"            '<script type="importmap"'
hasidx "three.js pinned to the CDN build" 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js'
hasidx "module script present"         'type="module"'
hasidx "imports three"                 "from 'three'"
hasidx "#scene canvas present"         'id="scene"'

echo
echo "scene states"
# Quoted forms only: a bare grep for "ring" matches JSON.stringify.
for state in ring pulse reform scatter; do
  if grep -qE "[\"']${state}[\"']" "$PAGE"; then
    ok "handles state: $state"
  else
    bad "handles state: $state" "no quoted '${state}' in the page"
  fi
done
hasidx "reads document.body.dataset.state" "dataset.state"
hasidx "listens for the scenestate event"  "scenestate"
hasidx "honours the memory-missing class"  "memory-missing"

echo
echo "particles and forms"
hasidx "COUNT is 5000"                   "COUNT = 5000"
hasidx "wireframe material"              "wireframe: true"
hasidx "additive blending"               "THREE.AdditiveBlending"
hasidx "depthWrite disabled"             "depthWrite: false"
hasidx "TetrahedronGeometry(1, 0)"       "TetrahedronGeometry(1, 0)"
hasidx "inline noise function"           "function noise3("
hasidx "inline smoothstep"               "function smoothstep("
hasidx "ridged noise"                    "function ridged("
hasidx "lateral outline point array"     "OUTLINE = ["
hasidx "Catmull-Rom smoothing"           "function catmullRom("
hasidx "point-in-polygon test"           "function insidePolygon("
hasidx "grooves (Sylvian + cerebellum)"  "function grooveDist("
hasidx "outline band sampler"            "function outlineBand("
hasidx "streamline walker"               "function walkStreamline("
hasidx "direction field"                 "function fieldDir("
hasidx "minimum-spacing grid 0.055"      "SPACING = 0.055"
hasidx "streamline step 0.035"           ", 38, 0.035, inCerebrum"
hasidx "cerebellum step 0.022"           ", 16, 0.022, inCereb"
hasidx "cerebellum sub-polygon"          "CEREBELLUM = ["
hasidx "brainstem centreline"            "BRAINSTEM_AXIS = ["
hasidx "brain opacity 0.7"               "BRAIN_OPACITY = 0.7"
hasidx "shortfall parked, not interior"  "function parkInstance("
hasidx "parked scale 0.004"              "PARKED_SCALE = 0.004"
hasidx "parked count exposed"            "stats.parked++"
hasidx "brain pose 0.12 / +0.35"         "RX = 0.12, RY = 0.35"
hasidx "sceneStats.form exposed"         "sceneStats.form"
hasidx "keyframes: coin 1, bulb 2, logo 4 and 5" "KEY_FORMS = ['brain', 'coin', 'bulb', 'scatter', 'logo', 'logo']"
hasidx "keyframes measured per section"  "function measureKeys("
hasidx "scroll anchor located"           "function locate("
hasidx "three-point blend through scatter" "function blendTarget("
hasidx "smoothstep on t"                 "smoothstep(0, 1, kT"
hasidx "rate 9 ease"                     "EASE_RATE = 9"
hasidx "per-keyframe base rotation"      "BASE_ROT = {"
hasidx "one full turn through scatter"   "FULL_TURN = 2 * Math.PI"
hasidx "arrives at B's base rotation"    "rb[1] + turn - ra[1]"
hasidx "sceneStats.t exposed"            "sceneStats.t = kT"
hasidx "sceneStats.formA / formB"        "sceneStats.formA"
hasidx "sceneStats.scattered"            "sceneStats.scattered"
hasidx "interval crossing logged once"   "turnstyl scene: "
hasidx "logo form generator"             "function buildLogo("
hasidx "logo diameter 2.6"               "LOGO_DIAMETER = 2.6"
hasidx "logo stroke 1.4 / depth 0.7"     "LOGO_STROKE = 1.4, LOGO_DEPTH = 0.7"
hasidx "logo fine size class only"       "sLogo[i]  = (i >= FORM_N) ? s : 0.014"
hasidx "coin form generator"             "function buildCoin("
hasidx "canvas glyph sampling"           "getImageData("
hasidx "waits for document.fonts.ready"  "document.fonts.ready"
hasidx "coin ring 1.35 / stroke 0.16 / depth 0.35" "COIN_R = 1.35, COIN_STROKE = 0.16, COIN_DEPTH = 0.35"
hasidx "coin glyph 1.7 tall"             "COIN_GLYPH_H = 1.7"
hasidx "coin fine size class only"       "sCoin[i]  = (i >= FORM_N) ? s : 0.014"
hasidx "coin in sceneStats"              "coin: coinStats"
hasidx "bulb form generator"             "function buildBulb("
hasidx "bulb profile array"              "BULB_PROFILE = ["
hasidx "surface of revolution sampler"   "function revolveProfile("
hasidx "open Catmull-Rom for the profile" "function catmullRomOpen("
hasidx "five thread ridges"              "BULB_RIDGES = [-0.70, -0.80, -0.90, -1.00, -1.10]"
hasidx "filament coil, 6 turns"          "COIL_TURNS = 6, COIL_R = 0.09"
hasidx "bulb 2.7 tall, tilt 0.10"        "BULB_HEIGHT = 2.7, BULB_TILT = 0.10"
hasidx "bulb opacity 0.8"                "BULB_OPACITY = 0.8"
hasidx "bulb faces the camera"           "bulb: [BULB_TILT, 0]"
hasidx "bulb fine size class only"       "sBulb[i]  = (i >= FORM_N) ? s : 0.014"
hasidx "bulb in sceneStats"              "bulb: bulbStats"
hasidx "ambient field excluded from forms" "AMBIENT = 300"
for f in brain logo coin bulb scatter; do
  if grep -qE "[\"']${f}[\"']|\b${f}:" "$PAGE"; then
    ok "form present: $f"
  else
    bad "form present: $f" "no form named '${f}'"
  fi
done
hasidx "teal accent"                     "0x5DCAA5"
if grep -qF "RIM_DOT" "$PAGE"; then bad "per-frame rim recolour removed" "RIM_DOT still present"; else ok "per-frame rim recolour removed"; fi
MODULE="$TMPDIR_RUN/module.js"
sed -n '/<script type="module">/,/<\/script>/p' "$PAGE" > "$MODULE"
if grep -qE "IntersectionObserver|SCATTER_TRANSIT_MS|HERO_SCROLL_TRIGGER|consoleActive|SCROLL_TURN|scrollY \* 0" "$MODULE"; then
  bad "no observer-driven or scrollY-based rotation in the scene" "old machinery still in the module"
else
  ok "no observer-driven or scrollY-based rotation in the scene"
fi

echo
echo "interaction"
hasidx "mousemove listener on window"    "window.addEventListener('mousemove'"
hasidx "click fires a pulse"             "hero.addEventListener('click'"
hasidx "scroll parallax listener"        "window.addEventListener('scroll'"
hasidx "pointer projected into the scene" "function pointerIntoScene("
hasidx "bulge offset applied"            "bulge[j]"
hasidx "bulge radius 0.9"                "BULGE_R = 0.9"

echo
echo "motion and hygiene"
hasidx "pauses when the tab is hidden"     "document.hidden"
hasidx "respects prefers-reduced-motion"   "prefers-reduced-motion"
hasidx "caps pixel ratio"                  "setPixelRatio"
hasidx "resizes with the window"           "resize"

# ---------------------------------------------------------------- brand
echo
echo "brand"
has "favicon links the mark"          'rel="icon" type="image/svg+xml" href="static/brand/mark-light.svg"'
has "lockup image in the top bar"     'src="static/brand/lockup-a.svg" alt="turnstyl"'
has "hero headline bounded"           ".hero h1{max-width:56vw}"
BCODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/static/brand/lockup-a.svg" 2>/dev/null)
[ "$BCODE" = "200" ] && ok "GET /static/brand/lockup-a.svg returns 200" \
  || bad "GET /static/brand/lockup-a.svg returns 200" "HTTP ${BCODE:-no response}"

# ---------------------------------------------------------------- api
echo
echo "API"
SCODE=$(curl -s -o "$STATUS" -w "%{http_code}" "$BASE/api/status" 2>/dev/null)
if [ "$SCODE" != "200" ]; then
  bad "GET /api/status returns 200" "HTTP ${SCODE:-no response}"
else
  ok "GET /api/status returns 200"
  if grep -q '"records"' "$STATUS"; then
    ok "status carries records ($(sed -n 's/.*"records": *\([0-9]*\).*/\1/p' "$STATUS" | head -1))"
  else
    bad "status carries records" "no \"records\" key in /api/status"
  fi
  if grep -q '"memory_missing"' "$STATUS"; then
    ok "status carries memory_missing"
  else
    bad "status carries memory_missing"
  fi
fi

# four step cards always: the page merges, and the API fills the ladder in
hasapp "page renders four step cards"     "function fourSteps("
hasapp "unrun steps pill as not started"  'label:"not started"'
FIRST_JOB=$(curl -s "${OPH[@]}" "$BASE/api/jobs" 2>/dev/null | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);j=d.get('jobs') or [];print(j[0]['job_id'] if j else '')" 2>/dev/null)
if [ -n "$FIRST_JOB" ]; then
  NSTEPS=$(curl -s "${OPH[@]}" "$BASE/api/jobs/$FIRST_JOB" 2>/dev/null | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);s=d.get('steps') or [];print(len(s), sum(1 for x in s if x.get('status')=='not_started'), all(x.get('price_usdc') is not None for x in s))" 2>/dev/null)
  case "$NSTEPS" in
    "4 "*" True") ok "API returns all four steps with prices ($NSTEPS: count, not_started, priced)";;
    *) bad "API returns all four steps with prices" "got: $NSTEPS";;
  esac
else
  bad "API returns all four steps with prices" "no job in the store to test against"
fi
# buyer flow
hasapp "ethers v6 loaded from cdnjs"     "ethers/6.13.2/ethers.umd.min.js"
hasapp "connect wallet button"           'id="connectBtn"'
hasapp "switches to Base Sepolia"        "wallet_switchEthereumChain"
hasapp "adds Base Sepolia on 4902"       "wallet_addEthereumChain"
hasapp "Base Sepolia chain id 0x14a34"   'BASE_CHAIN = "0x14a34"'
hasapp "silent reconnect via eth_accounts" 'eth("eth_accounts")'
hasapp "nothing requested on load"       'sessionStorage.getItem("wasConnected") === "1"'
hasapp "new audit panel"                 'id="auditPanel"'
hasapp "POST /api/jobs from the page"    'postJson("/api/jobs"'
hasapp "pay: allowance first"            ".allowance(W.addr, st.receipts_address)"
hasapp "pay: approve when short"         ".approve(st.receipts_address"
hasapp "pay: then pay(memo, units)"      ".pay(memo, units)"
hasapp "user rejection reads cancelled"  'return "cancelled"'
hasapp "Simulate payment on fake backend" "Simulate payment"
hasapp "simulate calls POST /api/jobs/{id}/pay" '"/pay"'
hasapp "your jobs and all jobs are separate reads" "S.mine = d;"
hasapp "this is you pill"                "this is you"
hasidx "story: credit after three paid jobs" "three fully paid jobs"
hasapp "ledger: jobs until credit"       '"jobs until credit"'
hasapp "ledger: completed paid jobs"     '"completed paid jobs"'
hasapp "memory-missing disables submit"  "cannot take jobs while memory is missing"
has "config.js loaded, cache-busted"  "config.js?v="
has "API origin from config"          "window.TURNSTYL_API"
has "every API fetch is prefixed"     "function apiUrl("
has "tunnel header on fetches"        "ngrok-skip-browser-warning"
has "agent offline banner"            "agent offline: the operator"
has "offline after two failed polls"  "S.statusFails >= 2"
hasapp "offline disables submit"         "submissions are held until"
hasapp "faucet link"                     "https://faucet.circle.com"
hasapp "receipts contract link"          'id="receiptsLink"'
has "assets are relative for Pages"   'src="static/brand/lockup-a.svg"'
CCODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/config.js" 2>/dev/null)
[ "$CCODE" = "200" ] && ok "GET /config.js returns 200" || bad "GET /config.js returns 200" "HTTP ${CCODE:-no response}"
hasapp "wallet chip element"             'class="chip" id="walletChip"'
hasapp "chip: green dot on Base Sepolia" '<span class="dot on"></span><span class="addr">'
hasapp "chip: amber dot on another chain" '<span class="dot warn"></span>'
hasapp "chip: gray dot disconnected"     '<span class="dot"></span><span class="addr">'
hasapp "chip: switch label on wrong chain" "switch to Base Sepolia"
hasapp "chip: copy flash"                'addr.textContent = "copied"'
has "chip: same row at every width"  ".topbar{position:fixed;top:var(--banner-h)"
noidx "top gradient removed (solid header)" 'id="topfade"'
has "fixed solid header"              "height:var(--header-h);z-index:30"
has "header background solid black"  "background:#000;border-bottom:1px solid rgba(255,255,255,.08)"
has "header 64px desktop"            "--header-h:64px"
has "header 56px mobile"             ":root{--header-h:56px}"
has "banner height drives the header" "--banner-h"
has "script sets the banner height"  'setProperty("--banner-h"'
has "page pads for header + banner"  "padding-top:calc(var(--header-h) + var(--banner-h))"
hasapp "status row is the console's first row" '<div class="statusrow" id="statusCluster">'
has "700px media query"              "@media (max-width:700px)"
has "mobile h1 clamp"                "clamp(38px,11vw,56px)"
has "mobile h2 clamp"                "clamp(30px,8.5vw,42px)"
has "mobile chip 11px, 60vw"         "max-width:60vw"
if grep -qF "linear-gradient(to right,rgba(0,0,0,.7)" "$ALL"; then bad "mobile story gradient removed (panel replaces it)" "gradient still present"; else ok "mobile story gradient removed (panel replaces it)"; fi
has "backing panel rule"              ".panel{position:relative;background:rgba(0,0,0,.62)"
has "panel blur with prefix"          "-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)"
has "panel radius, border, padding"   "border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:28px 32px;margin:-28px -32px"
has "panel hugs the story text"       ".story .inner.panel{max-width:calc(760px + 64px)}"
has "panel hugs the hero text"        ".hero .panel{align-self:flex-start;width:fit-content;max-width:calc(56vw + 64px)}"
has "danger panel border tinted red"  ".story.danger .panel{border-color:rgba(229,72,77,.25)}"
has "mobile panel padding"            ".panel{padding:18px 20px;margin:-18px -20px}"
N_STORY=$(grep -c 'class="inner reveal panel"' "$PAGE"); N_HERO=$(grep -c 'class="reveal in panel"' "$PAGE")
[ "$N_STORY" = "4" ] && [ "$N_HERO" = "1" ] && ok "panel class on all five text blocks (4 story + 1 hero)" || bad "panel class on all five text blocks" "story=$N_STORY hero=$N_HERO"
has "tables as label/value on mobile" "content:attr(data-label)"
if grep -qF "(i === 0 && !mobile)" "$ALL"; then bad "newest event no longer auto-expanded" "old auto-expand still present"; else ok "newest event no longer auto-expanded"; fi
hasapp "outstanding item pay button"     'data-settle="1"'
hasapp "ledger memo comes from the API"  "o.memo"
hasapp "timeline: summary sentence"      '<p class="sum">'
hasapp "timeline: summary from extra"    "e.extra.summary"
hasapp "timeline: fallback to first acted line" "e.acted[0]"
hasapp "timeline: details collapsed by default" "S.memTouched[key] || false"
hasapp "timeline: details disclosure"    "<summary>details<span"
hasapp "job type picker"                 'id="typePicker"'
hasapp "picker reads the API's job types" "function jobTypes("
hasapp "per-type step prices in the picker" "sp.base_price_usdc"
hasapp "new job panel, not new audit"    "<h3>new job</h3>"
hasapp "submit sends the chosen type"    "job_type:S.jobType"
hasapp "job page shows the service"      'class="k">service</div>'
hasapp "step card shows the test run"    "TESTS "
hasapp "step card: tests compile label"  "TESTS COMPILE "
JT=$(curl -s "$BASE/api/job_types" 2>/dev/null | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print(len(d.get('job_types') or []), d.get('default'))" 2>/dev/null)
[ "$JT" = "2 audit" ] && ok "GET /api/job_types returns both services" || bad "GET /api/job_types" "got: $JT"
hasapp "x402 gasless pay button"         'id="payX402Btn"'
hasapp "x402 fallback: pay on chain"     ">Pay on chain<"
hasapp "x402 no-gas note"                'class="nogas">no gas needed<'
hasapp "x402 typed-data signing"         "signTypedData(domain, types, message)"
hasapp "x402 EIP-3009 type"              "TransferWithAuthorization: ["
hasapp "x402 domain read from USDC"      "usdcC.name()"
hasapp "x402 header construction"        '"PAYMENT-SIGNATURE": b64('
hasapp "x402 payload carries accepted"   "accepted: signed.want"
hasapp "x402 endpoints"                  "/pay-x402/"
hasapp "x402 settle endpoint"            "/settle-x402/"
hasapp "x402 rail shown on the step"     'class="nogas">x402<'
X4=$(curl -s "$BASE/api/status" 2>/dev/null | .venv/bin/python -c "import json,sys;x=(json.load(sys.stdin).get('x402') or {});print('enabled' if x.get('enabled') else 'disabled', x.get('network'))" 2>/dev/null)
case "$X4" in
  "enabled eip155:84532"|"disabled eip155:84532") ok "/api/status reports x402 ($X4)";;
  *) bad "/api/status reports x402" "got: $X4";;
esac
hasapp "download report button"          'id="downloadReportBtn"'
hasapp "report link hits report.md"      '/report.md'
hasapp "verify all button"               'id="verifyAllBtn"'
hasapp "per-step verify control"         'data-verify="'
hasapp "verify result: match"            "matches on-chain commit"
hasapp "verify result: mismatch"         "does not match the on-chain commit"
hasapp "verify result: no commit"        "no commit for this step"
hasapp "verify calls the API, not the chain" '"/verify"'
FIRST_JOB2=$(curl -s "${OPH[@]}" "$BASE/api/jobs" 2>/dev/null | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);j=d.get('jobs') or [];print(j[0]['job_id'] if j else '')" 2>/dev/null)
if [ -n "$FIRST_JOB2" ]; then
  RCODE=$(curl -s "${OPH[@]}" -D "$TMPDIR_RUN/rh" -o /dev/null -w "%{http_code}" "$BASE/api/jobs/$FIRST_JOB2/report.md" 2>/dev/null)
  [ "$RCODE" = "200" ] && grep -qi "content-disposition: attachment" "$TMPDIR_RUN/rh" && ok "GET report.md returns an attachment" || bad "GET report.md returns an attachment" "HTTP $RCODE"
  VCODE=$(curl -s "${OPH[@]}" -o /dev/null -w "%{http_code}" "$BASE/api/jobs/$FIRST_JOB2/verify" 2>/dev/null)
  [ "$VCODE" = "200" ] && ok "GET verify returns 200" || bad "GET verify returns 200" "HTTP $VCODE"
fi
hasapp "one pay sequence for both"       "function payMemo("
hasapp "settle endpoint on fake backend" '"/settle/"'
# the API computes the memo for every outstanding item
FIRST_BUYER=$(curl -s "${OPH[@]}" "$BASE/api/jobs" 2>/dev/null | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);j=d.get('jobs') or [];print(j[0]['buyer'] if j else '')" 2>/dev/null)
if [ -n "$FIRST_BUYER" ]; then
  OUTS=$(curl -s "${OPH[@]}" "$BASE/api/buyers/$FIRST_BUYER" 2>/dev/null | .venv/bin/python -c "
import json,sys,re
d=json.load(sys.stdin); o=d.get('outstanding') or []
ok=all(re.match(r'^0x[0-9a-f]{64}$', x.get('memo','')) and 'amount_units' in x for x in o)
print(len(o), 'ok' if ok else 'bad')" 2>/dev/null)
  case "$OUTS" in
    "0 ok") ok "GET /api/buyers outstanding carries memo (none outstanding right now)";;
    *" ok") ok "GET /api/buyers outstanding carries a keccak memo ($OUTS items)";;
    *) bad "GET /api/buyers outstanding carries a keccak memo" "got: $OUTS";;
  esac
else
  bad "GET /api/buyers outstanding carries a keccak memo" "no job in the store to find a buyer"
fi
for k in usdc_address receipts_abi usdc_abi; do
  if grep -q "\"$k\"" "$STATUS"; then ok "status carries $k"; else bad "status carries $k" "no \"$k\" in /api/status"; fi
done
JCODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/journal?limit=1" 2>/dev/null)
[ "$JCODE" = "200" ] && ok "GET /api/journal?limit=1 returns 200 (pulse source)" \
  || bad "GET /api/journal?limit=1 returns 200" "HTTP ${JCODE:-no response}"

# ---------------------------------------------------------------- two pages, one look
echo
echo "pages"
hasidx "index links the app in the header" '<a class="navlink" href="app.html">App</a>'
hasidx "index has the operator strip"      'class="opstrip wrap"'
hasidx "strip: agent online or offline"    'id="agentState"'
hasidx "strip: memory path"                'id="dbPath"'
hasidx "strip: the public figures"         'id="statsLine"'
hasidx "strip: latest decision sentence"   'id="opSummary"'
hasidx "strip: one button into the app"    '<a class="btn" href="app.html">Open the app</a>'
noidx  "index no longer renders the console" 'id="consoleBody"'
noidx  "index no longer builds the job panel" 'id="auditPanel"'
noidx  "index no longer pays"              "function payMemo("
noidx  "index carries no wallet"           "eth_requestAccounts"
hasidx "index keeps the scene canvas"      'id="scene"'
if grep -qF '<script type="importmap"' "$APP"; then bad "app carries no three.js" "an import map is present on app.html"; else ok "app carries no three.js"; fi
if grep -qiF "three.module.js" "$APP"; then bad "app loads no three.js module" "three.module.js on app.html"; else ok "app loads no three.js module"; fi
grep -qF '<link rel="stylesheet" href="static/turnstyl.css">' "$PAGE" && \
  grep -qF '<link rel="stylesheet" href="static/turnstyl.css">' "$APP" && \
  ok "both pages load the shared stylesheet" || bad "both pages load the shared stylesheet"
grep -qF "<style" "$PAGE" && bad "index has no inline stylesheet left" "a <style> block is still inline" || ok "index has no inline stylesheet left"
grep -qF ".panel{position:relative;background:rgba(0,0,0,.62)" "$CSS" && ok "the shared stylesheet carries the story rules" || bad "shared stylesheet carries the story rules"
grep -qF ".steps{display:grid" "$CSS" && ok "the shared stylesheet carries the console rules" || bad "shared stylesheet carries the console rules"
hasapp "app routes by ?job="               "function jobParam("
hasapp "job links point at the app"        'return "app.html?job=" + encodeURIComponent(id)'
hasapp "back link returns to the app list" '<a class="back" href="app.html">'
noidx  "no ?job=...#console links remain"  '#console"'

# ---------------------------------------------------------------- sign-in and the operator token
echo
echo "auth"
hasapp "asks the API for a login nonce"    '"/api/auth/nonce?address="'
hasapp "signs the message the API issued"  "signer.signMessage(n.message)"
hasapp "exchanges the signature for a session" '"/api/auth/verify"'
hasapp "sends the session as a bearer token" 'h["Authorization"] = "Bearer " + b'
hasapp "every API call goes through headers()" "function headers(extra)"
hasapp "the operator token outranks the session" "return A.operator || A.token || null"
hasapp "chip reads signed in"              '"signed in"'
hasapp "chip offers sign in before it is"  '"sign in"'
hasapp "connecting signs in straight away" "if(W.addr && !buyerSignedIn()) return signIn();"
hasapp "settings drawer"                   'id="settingsDrawer"'
hasapp "settings holds the operator token" 'id="opToken"'
hasapp "operator token kept in sessionStorage only" 'sessionStorage.setItem("turnstylOperator"'
hasapp "the drawer says sessionStorage only" "<b>sessionStorage</b>"
hasapp "report link carries the session"   '"?token=" + encodeURIComponent(b)'
hasapp "private outputs say whose they are" "This output belongs to"
hasapp "the app says who bought what is not the meter" "Who bought what is not part of the meter"
# the login message, exactly as auth.py builds it
NONCE=$(curl -s "$BASE/api/auth/nonce?address=0x0000000000000000000000000000000000000001" 2>/dev/null)
MSGOK=$(printf '%s' "$NONCE" | .venv/bin/python -c "
import json,sys
d=json.load(sys.stdin)
want='turnstyl login\n\naddress: %s\nnonce: %s\nissued: %s' % (d['address'], d['nonce'], d['issued'])
print('ok' if d['message'] == want else 'bad')" 2>/dev/null)
[ "$MSGOK" = "ok" ] && ok "GET /api/auth/nonce returns the exact message to sign" || bad "login message format" "got: $(printf '%s' "$NONCE" | head -c 200)"
ME=$(curl -s "$BASE/api/auth/me" 2>/dev/null | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print(d['kind'], d['signed_in'])" 2>/dev/null)
[ "$ME" = "public False" ] && ok "GET /api/auth/me without a token reads as public" || bad "auth/me public" "got: $ME"
if [ -n "$OPTOK" ]; then
  MEO=$(curl -s "${OPH[@]}" "$BASE/api/auth/me" 2>/dev/null | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print(d['kind'], d['operator'])" 2>/dev/null)
  [ "$MEO" = "operator True" ] && ok "GET /api/auth/me with OPERATOR_TOKEN reads as the operator" || bad "auth/me operator" "got: $MEO"
else
  bad "GET /api/auth/me with OPERATOR_TOKEN" "OPERATOR_TOKEN is not in .env; start the server once to have it written"
fi
LPUB=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/jobs" 2>/dev/null)
LOP=$(curl -s "${OPH[@]}" -o /dev/null -w "%{http_code}" "$BASE/api/jobs" 2>/dev/null)
[ "$LPUB" = "403" ] && [ "$LOP" = "200" ] && ok "GET /api/jobs: 403 without the operator token, 200 with it" \
  || bad "the job list is an operator view" "public=$LPUB operator=$LOP"
curl -s "$BASE/api/jobs" 2>/dev/null | grep -q "the job list is an operator view" \
  && ok "the 403 says the job list is an operator view" || bad "job list 403 detail"

# ---------------------------------------------------------------- sign out and disconnect
echo
echo "sign out"
hasapp "the signed-in chip opens a menu"    'id="walletMenu"'
hasapp "menu item: copy address"            '>Copy address</button>'
hasapp "menu item: sign out"                'id="signOutBtn">Sign out</button>'
hasapp "menu item: disconnect"              'id="disconnectBtn">Disconnect</button>'
hasapp "copy stays the first item"          'class="mi" data-copy='
hasapp "the chip toggles its own menu"      'if(hit("walletChip")){ W.menu = !W.menu;'
hasapp "sign out calls the logout endpoint" '"/api/auth/logout"'
hasapp "logout carries the old session token" 'Authorization: "Bearer " + token'
hasapp "sign out keeps the wallet connected" "A.token = null; A.address = null; A.buyerOk = false;"
hasapp "disconnect signs out first"         "return signOut().then(function(){"
hasapp "disconnect clears the reconnect flag" 'sessionStorage.removeItem("wasConnected")'
hasapp "disconnect asks the wallet to revoke" 'eth("wallet_revokePermissions", [{ eth_accounts: {} }])'
hasapp "an unsupported wallet is not an error" "/* not supported here */"
hasapp "an account change signs the old session out" 'window.ethereum.on("accountsChanged"'
hasapp "and drops to the connect state"     "W.addr = null; W.balance = null; W.menu = false;"
hasapp "clicking outside closes the menu"   'e.target.closest("#walletMenu")'
has   "the menu is styled"                  ".menu{position:absolute"
hasapp "settings: clear operator token"     'id="clearOp">Clear operator token</button>'
hasapp "settings says signing out does not clear it" "Signing out of a wallet does not clear it"
LOCODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/auth/logout" 2>/dev/null)
[ "$LOCODE" = "401" ] && ok "POST /api/auth/logout without a token -> 401" || bad "logout without a token -> 401" "HTTP $LOCODE"

# ---------------------------------------------------------------- digest card
echo
echo "digest"
hasapp "the operator view has a digest card"   'class="card digest"'
hasapp "the card is labelled operator view"    'digest <span class="pill gold">operator view</span>'
hasapp "the card is drawn only for the operator" "if(!isOperator()) return \"\";"
hasapp "it asks for today and for all time"    '"/api/digest?days=1"'
hasapp "it shows today against all time"       "left: today. right: all time"
hasapp "it names the consolidation entity"     "consolidated as"
hasapp "it shows USDC settled"                 ">USDC settled<"
hasapp "it shows the estimated model spend"    "model spend (est.)"
hasapp "it shows payment to output"            "payment to output:"
hasapp "it shows the top contracts"            "top contracts by repeat audits"
has   "the digest card is styled"              ".digest .cols{display:grid"
DGCODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/digest" 2>/dev/null)
[ "$DGCODE" = "200" ] && ok "GET /api/digest answers without a credential" || bad "GET /api/digest" "HTTP $DGCODE"
DGPUB=$(curl -s "$BASE/api/digest" 2>/dev/null | .venv/bin/python -c "
import json,sys
d=json.load(sys.stdin)
print('ok' if d['complete'] is False and 'model_spend_usd_estimated' not in d['figures'] else 'bad')" 2>/dev/null)
[ "$DGPUB" = "ok" ] && ok "the public digest is counts only" || bad "public digest is counts only" "got: $DGPUB"

# ---------------------------------------------------------------- untrusted source
echo
echo "untrusted source"
hasapp "the job page has an untrusted-source panel" 'class="card warn"'
hasapp "the panel says what the text tried to do" "text that tries to instruct the auditor"
hasapp "the panel says the scan ran before any model" "before any model saw the file"
hasapp "the panel lists line, rule and the matched text" 'class="ln">line '
has   "the warning panel is styled"    ".warn{background:#0C0705"
hasapp "a redacted flag says whose the passage is" "the passage is private to this job"

# ---------------------------------------------------------------- public stats
echo
echo "public stats"
ST="$TMPDIR_RUN/stats.json"
TCODE=$(curl -s -o "$ST" -w "%{http_code}" "$BASE/api/stats" 2>/dev/null)
[ "$TCODE" = "200" ] && ok "GET /api/stats returns 200 without a credential" || bad "GET /api/stats" "HTTP ${TCODE:-no response}"
SHAPE=$(.venv/bin/python -c "
import json
d=json.load(open('$ST'))
want={'jobs','jobs_completed','buyers','usdc_settled','decisions','served_from_memory'}
print('ok' if want <= set(d) else 'missing ' + ','.join(sorted(want - set(d))))" 2>/dev/null)
[ "$SHAPE" = "ok" ] && ok "/api/stats carries the six public figures" || bad "/api/stats shape" "$SHAPE"
if grep -qiE '0x[0-9a-f]{40}' "$ST"; then bad "/api/stats names nobody" "an address is in the body"; else ok "/api/stats carries no address"; fi
[ "$(.venv/bin/python -c "import json;print(json.load(open('$ST'))['cache_seconds'])" 2>/dev/null)" = "10" ] && ok "/api/stats is cached for 10 seconds" || bad "/api/stats cache_seconds"
hasidx "the story strip shows the stats line" 'id="statsLine"'
hasapp "the app shows the stats line under the header" 'id="statsLine"'
has   "stats line: jobs in memory"      "jobs in memory</span>"
has   "stats line: completed"           "completed</span>"
has   "stats line: buyers"              "buyers</span>"
has   "stats line: USDC settled"        "USDC settled</span>"
has   "stats line: decisions logged"    "decisions logged</span>"
has   "stats line: served from memory"  "served from memory</span>"
hasidx "the story page reads /api/stats, not the job list" 'api("/api/stats")'
noidx  "the story page never asks for the job list" '/api/jobs'

# ---------------------------------------------------------------- who sees which table
echo
echo "job tables"
hasapp "your jobs asks for one address"   '"/api/jobs?buyer=" + encodeURIComponent(addr)'
hasapp "all jobs is fetched only for the operator" "isOperator()"
hasapp "the all-jobs section is labelled operator view" '<span class="pill gold">operator view</span>'
hasapp "no table at all without a session" "Connect a wallet and sign in to see the jobs you paid for"
hasapp "and it says there is no list of everyone else's" "There is no list of everyone"
hasapp "the public receipt line on someone else's job" "you are viewing the public receipt for this job"
if grep -qF 'every job</h3>' "$APP"; then bad "the every-job section is gone" "an 'every job' section is still rendered"; else ok "the every-job section is gone"; fi

echo
if [ "$FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL - $FAILURES check(s) failed"
  exit 1
fi
echo "RESULT: PASS - page and API are wired"
