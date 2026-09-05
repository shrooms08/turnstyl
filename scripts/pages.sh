#!/usr/bin/env bash
# Publish web/ to the gh-pages branch of origin with a git worktree. No build:
# index.html, config.js and static/ are copied as they are.
#
#   scripts/pages.sh                 publish the whole page
#   scripts/pages.sh --config-only   publish only web/config.js (tunnel.sh uses this)
#
# Prints the Pages URL. On the first run it enables Pages for the branch through
# the GitHub API if it can, and otherwise prints the two clicks to do it by hand.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-full}"
REPO_URL=$(git remote get-url origin)
SLUG=$(echo "$REPO_URL" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
OWNER=${SLUG%%/*}; NAME=${SLUG##*/}
PAGES_URL="https://${OWNER}.github.io/${NAME}/"
WT=.gh-pages-worktree

for f in web/index.html web/config.js; do
  [ -f "$f" ] || { echo "turnstyl pages: $f is missing; nothing to publish" >&2; exit 1; }
done

git fetch origin gh-pages >/dev/null 2>&1 || true
rm -rf "$WT"; git worktree prune
if git show-ref --verify --quiet refs/remotes/origin/gh-pages; then
  git worktree add -q -B gh-pages "$WT" origin/gh-pages
else
  git worktree add -q --detach "$WT"
  ( cd "$WT" && git checkout -q --orphan gh-pages && git rm -rfq . >/dev/null 2>&1 || true )
fi

if [ "$MODE" = "--config-only" ]; then
  cp web/config.js "$WT/config.js"
else
  cp web/index.html "$WT/index.html"
  cp web/config.js "$WT/config.js"
  rm -rf "$WT/static"; mkdir -p "$WT/static"
  cp -R web/static/. "$WT/static/"
  find "$WT/static" \( -name .DS_Store -o -name .gitkeep \) -delete
  touch "$WT/.nojekyll"
fi

(
  cd "$WT"
  git add -A
  if git diff --cached --quiet; then
    echo "turnstyl pages: nothing changed on gh-pages"
  else
    git -c user.name="turnstyl" -c user.email="turnstyl@users.noreply.github.com" \
      commit -q -m "pages: $( [ "$MODE" = "--config-only" ] && echo "config.js -> $(tr -d '\n' < config.js)" || echo "publish web/" )"
    git push -q origin gh-pages
    echo "turnstyl pages: pushed gh-pages ($(git rev-parse --short HEAD))"
  fi
)
git worktree remove --force "$WT"

# Pages enabled for this branch? Try the API first, then say what to click.
if command -v gh >/dev/null 2>&1; then
  if ! gh api "repos/$SLUG/pages" >/dev/null 2>&1; then
    if gh api -X POST "repos/$SLUG/pages" -f "source[branch]=gh-pages" -f "source[path]=/" >/dev/null 2>&1; then
      echo "turnstyl pages: enabled GitHub Pages for gh-pages via the API (first deploy takes about a minute)"
    else
      echo "turnstyl pages: GitHub Pages is not enabled yet. Two clicks:"
      echo "  1. https://github.com/$SLUG/settings/pages -> Build and deployment -> Source: 'Deploy from a branch'"
      echo "  2. Branch: gh-pages, folder: / (root) -> Save"
    fi
  fi
fi
echo "Pages URL: $PAGES_URL"
