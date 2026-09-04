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
has "hero 40% scroll trigger"         "HERO_SCROLL_TRIGGER = 0.4"
has "sceneStats.form exposed"         "sceneStats.form"
has "sceneStats.target exposed"       "sceneStats.target"
has "form change logged once"         "turnstyl scene: "
has "logo form generator"             "function buildLogo("
has "transition through scatter"      "SCATTER_TRANSIT_MS = 700"
has "console section chooses the logo" "consoleActive()"
has "ambient field excluded from forms" "AMBIENT = 300"
for f in brain logo scatter; do
  if grep -qE "[\"']${f}[\"']|\b${f}:" "$PAGE"; then
    ok "form present: $f"
  else
    bad "form present: $f" "no form named '${f}'"
  fi
done
has "state to form mapping"           "function formFor("
has "teal accent"                     "0x5DCAA5"
if grep -qF "RIM_DOT" "$PAGE"; then bad "per-frame rim recolour removed" "RIM_DOT still present"; else ok "per-frame rim recolour removed"; fi

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
has "favicon links the mark"          'rel="icon" type="image/svg+xml" href="/static/brand/mark-light.svg"'
has "lockup image in the top bar"     'src="/static/brand/lockup-a.svg" alt="turnstyl"'
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

JCODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/journal?limit=1" 2>/dev/null)
[ "$JCODE" = "200" ] && ok "GET /api/journal?limit=1 returns 200 (pulse source)" \
  || bad "GET /api/journal?limit=1 returns 200" "HTTP ${JCODE:-no response}"

echo
if [ "$FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL - $FAILURES check(s) failed"
  exit 1
fi
echo "RESULT: PASS - page and API are wired"
