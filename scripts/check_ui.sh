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

BASE="${1:-http://127.0.0.1:8787}"
TMPDIR_RUN=$(mktemp -d)
PAGE="$TMPDIR_RUN/page.html"
STATUS="$TMPDIR_RUN/status.json"
trap 'rm -rf "$TMPDIR_RUN"' EXIT
FAILURES=0

ok()   { printf "  OK   %s\n" "$1"; }
bad()  { printf "  FAIL %s\n" "$1"; [ -n "${2:-}" ] && printf "       %s\n" "$2"; FAILURES=$((FAILURES+1)); }

has()  { # has <description> <fixed-string>
  if grep -qF -- "$2" "$PAGE"; then ok "$1"; else bad "$1" "not found in /: $2"; fi
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

# ---------------------------------------------------------------- scene wiring
echo
echo "three.js scene"
has "import map present"            '<script type="importmap"'
has "three.js pinned to the CDN build" 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js'
has "module script present"         'type="module"'
has "imports three"                 "from 'three'"
has "#scene canvas present"         'id="scene"'

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
has "reads document.body.dataset.state" "dataset.state"
has "listens for the scenestate event"  "scenestate"
has "honours the memory-missing class"  "memory-missing"

echo
echo "particles and forms"
has "COUNT is 5000"                   "COUNT = 5000"
has "wireframe material"              "wireframe: true"
has "additive blending"               "THREE.AdditiveBlending"
has "depthWrite disabled"             "depthWrite: false"
has "TetrahedronGeometry(1, 0)"       "TetrahedronGeometry(1, 0)"
has "inline noise function"           "function noise3("
has "inline smoothstep"               "function smoothstep("
has "ridged noise"                    "function ridged("
has "lateral outline point array"     "OUTLINE = ["
has "Catmull-Rom smoothing"           "function catmullRom("
has "point-in-polygon test"           "function insidePolygon("
has "grooves (Sylvian + cerebellum)"  "function grooveDist("
has "outline band sampler"            "function outlineBand("
has "streamline walker"               "function walkStreamline("
has "direction field"                 "function fieldDir("
has "minimum-spacing grid 0.055"      "SPACING = 0.055"
has "streamline step 0.035"           ", 38, 0.035, inCerebrum"
has "cerebellum step 0.022"           ", 16, 0.022, inCereb"
has "cerebellum sub-polygon"          "CEREBELLUM = ["
has "brainstem centreline"            "BRAINSTEM_AXIS = ["
has "brain opacity 0.7"               "BRAIN_OPACITY = 0.7"
has "shortfall parked, not interior"  "function parkInstance("
has "parked scale 0.004"              "PARKED_SCALE = 0.004"
has "parked count exposed"            "stats.parked++"
has "brain pose 0.12 / +0.35"         "RX = 0.12, RY = 0.35"
has "sceneStats.form exposed"         "sceneStats.form"
has "keyframes: coin 1, bulb 2, logo 4 and 5" "KEY_FORMS = ['brain', 'coin', 'bulb', 'scatter', 'logo', 'logo']"
has "keyframes measured per section"  "function measureKeys("
has "scroll anchor located"           "function locate("
has "three-point blend through scatter" "function blendTarget("
has "smoothstep on t"                 "smoothstep(0, 1, kT"
has "rate 9 ease"                     "EASE_RATE = 9"
has "per-keyframe base rotation"      "BASE_ROT = {"
has "one full turn through scatter"   "FULL_TURN = 2 * Math.PI"
has "arrives at B's base rotation"    "rb[1] + turn - ra[1]"
has "sceneStats.t exposed"            "sceneStats.t = kT"
has "sceneStats.formA / formB"        "sceneStats.formA"
has "sceneStats.scattered"            "sceneStats.scattered"
has "interval crossing logged once"   "turnstyl scene: "
has "logo form generator"             "function buildLogo("
has "logo diameter 2.6"               "LOGO_DIAMETER = 2.6"
has "logo stroke 1.4 / depth 0.7"     "LOGO_STROKE = 1.4, LOGO_DEPTH = 0.7"
has "logo fine size class only"       "sLogo[i]  = (i >= FORM_N) ? s : 0.014"
has "coin form generator"             "function buildCoin("
has "canvas glyph sampling"           "getImageData("
has "waits for document.fonts.ready"  "document.fonts.ready"
has "coin ring 1.35 / stroke 0.16 / depth 0.35" "COIN_R = 1.35, COIN_STROKE = 0.16, COIN_DEPTH = 0.35"
has "coin glyph 1.7 tall"             "COIN_GLYPH_H = 1.7"
has "coin fine size class only"       "sCoin[i]  = (i >= FORM_N) ? s : 0.014"
has "coin in sceneStats"              "coin: coinStats"
has "bulb form generator"             "function buildBulb("
has "bulb profile array"              "BULB_PROFILE = ["
has "surface of revolution sampler"   "function revolveProfile("
has "open Catmull-Rom for the profile" "function catmullRomOpen("
has "five thread ridges"              "BULB_RIDGES = [-0.70, -0.80, -0.90, -1.00, -1.10]"
has "filament coil, 6 turns"          "COIL_TURNS = 6, COIL_R = 0.09"
has "bulb 2.7 tall, tilt 0.10"        "BULB_HEIGHT = 2.7, BULB_TILT = 0.10"
has "bulb opacity 0.8"                "BULB_OPACITY = 0.8"
has "bulb faces the camera"           "bulb: [BULB_TILT, 0]"
has "bulb fine size class only"       "sBulb[i]  = (i >= FORM_N) ? s : 0.014"
has "bulb in sceneStats"              "bulb: bulbStats"
has "ambient field excluded from forms" "AMBIENT = 300"
for f in brain logo coin bulb scatter; do
  if grep -qE "[\"']${f}[\"']|\b${f}:" "$PAGE"; then
    ok "form present: $f"
  else
    bad "form present: $f" "no form named '${f}'"
  fi
done
has "teal accent"                     "0x5DCAA5"
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
has "mousemove listener on window"    "window.addEventListener('mousemove'"
has "click fires a pulse"             "hero.addEventListener('click'"
has "scroll parallax listener"        "window.addEventListener('scroll'"
has "pointer projected into the scene" "function pointerIntoScene("
has "bulge offset applied"            "bulge[j]"
has "bulge radius 0.9"                "BULGE_R = 0.9"

echo
echo "motion and hygiene"
has "pauses when the tab is hidden"     "document.hidden"
has "respects prefers-reduced-motion"   "prefers-reduced-motion"
has "caps pixel ratio"                  "setPixelRatio"
has "resizes with the window"           "resize"

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
has "page renders four step cards"     "function fourSteps("
has "unrun steps pill as not started"  'label:"not started"'
FIRST_JOB=$(curl -s "$BASE/api/jobs" 2>/dev/null | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);j=d.get('jobs') or [];print(j[0]['job_id'] if j else '')" 2>/dev/null)
if [ -n "$FIRST_JOB" ]; then
  NSTEPS=$(curl -s "$BASE/api/jobs/$FIRST_JOB" 2>/dev/null | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);s=d.get('steps') or [];print(len(s), sum(1 for x in s if x.get('status')=='not_started'), all(x.get('price_usdc') is not None for x in s))" 2>/dev/null)
  case "$NSTEPS" in
    "4 "*" True") ok "API returns all four steps with prices ($NSTEPS: count, not_started, priced)";;
    *) bad "API returns all four steps with prices" "got: $NSTEPS";;
  esac
else
  bad "API returns all four steps with prices" "no job in the store to test against"
fi
# buyer flow
has "ethers v6 loaded from cdnjs"     "ethers/6.13.2/ethers.umd.min.js"
has "connect wallet button"           'id="connectBtn"'
has "switches to Base Sepolia"        "wallet_switchEthereumChain"
has "adds Base Sepolia on 4902"       "wallet_addEthereumChain"
has "Base Sepolia chain id 0x14a34"   'BASE_CHAIN = "0x14a34"'
has "silent reconnect via eth_accounts" 'eth("eth_accounts")'
has "nothing requested on load"       'sessionStorage.getItem("wasConnected") === "1"'
has "new audit panel"                 'id="auditPanel"'
has "POST /api/jobs from the page"    'fetch(apiUrl("/api/jobs"), { method:"POST"'
has "pay: allowance first"            ".allowance(W.addr, st.receipts_address)"
has "pay: approve when short"         ".approve(st.receipts_address"
has "pay: then pay(memo, units)"      ".pay(memo, units)"
has "user rejection reads cancelled"  'return "cancelled"'
has "Simulate payment on fake backend" "Simulate payment"
has "simulate calls POST /api/jobs/{id}/pay" '"/pay"'
has "your jobs filter"                '"/api/jobs?buyer=" + encodeURIComponent(W.addr)'
has "this is you pill"                "this is you"
has "story: credit after three paid jobs" "three fully paid jobs"
has "ledger: jobs until credit"       '"jobs until credit"'
has "ledger: completed paid jobs"     '"completed paid jobs"'
has "memory-missing disables submit"  "cannot take jobs while memory is missing"
has "config.js loaded, cache-busted"  "config.js?v="
has "API origin from config"          "window.TURNSTYL_API"
has "every API fetch is prefixed"     "function apiUrl("
has "tunnel header on fetches"        "ngrok-skip-browser-warning"
has "agent offline banner"            "agent offline: the operator"
has "offline after two failed polls"  "S.statusFails >= 2"
has "offline disables submit"         "submissions are held until"
has "faucet link"                     "https://faucet.circle.com"
has "receipts contract link"          'id="receiptsLink"'
has "assets are relative for Pages"   'src="static/brand/lockup-a.svg"'
CCODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/config.js" 2>/dev/null)
[ "$CCODE" = "200" ] && ok "GET /config.js returns 200" || bad "GET /config.js returns 200" "HTTP ${CCODE:-no response}"
has "wallet chip element"             'class="chip" id="walletChip"'
has "chip: green dot on Base Sepolia" '<span class="dot on"></span><span class="addr">'
has "chip: amber dot on another chain" '<span class="dot warn"></span>'
has "chip: gray dot disconnected"     '<span class="dot"></span><span class="addr">'
has "chip: switch label on wrong chain" "switch to Base Sepolia"
has "chip: copy flash"                'addr.textContent = "copied"'
has "chip: same row at every width"  ".topbar{position:fixed;top:var(--banner-h)"
if grep -qF 'id="topfade"' "$PAGE"; then bad "top gradient removed (solid header)" "topfade still present"; else ok "top gradient removed (solid header)"; fi
has "fixed solid header"              "height:var(--header-h);z-index:30"
has "header background solid black"  "background:#000;border-bottom:1px solid rgba(255,255,255,.08)"
has "header 64px desktop"            "--header-h:64px"
has "header 56px mobile"             ":root{--header-h:56px}"
has "banner height drives the header" "--banner-h"
has "script sets the banner height"  'setProperty("--banner-h"'
has "page pads for header + banner"  "padding-top:calc(var(--header-h) + var(--banner-h))"
has "status row is the console's first row" '<div class="statusrow" id="statusCluster">'
has "700px media query"              "@media (max-width:700px)"
has "mobile h1 clamp"                "clamp(38px,11vw,56px)"
has "mobile h2 clamp"                "clamp(30px,8.5vw,42px)"
has "mobile chip 11px, 60vw"         "max-width:60vw"
has "story copy gradient on mobile"  "linear-gradient(to right,rgba(0,0,0,.7)"
has "tables as label/value on mobile" "content:attr(data-label)"
has "memory read closed on mobile"   "i === 0 && !mobile"
has "outstanding item pay button"     'data-settle="1"'
has "ledger memo comes from the API"  "o.memo"
has "one pay sequence for both"       "function payMemo("
has "settle endpoint on fake backend" '"/settle/"'
# the API computes the memo for every outstanding item
FIRST_BUYER=$(curl -s "$BASE/api/jobs" 2>/dev/null | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);j=d.get('jobs') or [];print(j[0]['buyer'] if j else '')" 2>/dev/null)
if [ -n "$FIRST_BUYER" ]; then
  OUTS=$(curl -s "$BASE/api/buyers/$FIRST_BUYER" 2>/dev/null | .venv/bin/python -c "
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

echo
if [ "$FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL - $FAILURES check(s) failed"
  exit 1
fi
echo "RESULT: PASS - page and API are wired"
