#!/bin/bash

#
# This script will calculate the next version depending on the current
# branch and the paramter (minor|major), create a tag and push it.
#
# This will trigger the build & deploy pipeline.
#

B_PROD="main"
B_TEST="test"

#
# Prechecks
#

[ "$1" = "minor" -o "$1" = "major" ] || { echo >&2 "Usage: $0 <minor|major>"; exit 1; }
git rev-parse --is-inside-work-tree &>/dev/null || { echo >&2 "Error: Not in a git repository!"; exit 1; }
git diff --quiet || { echo >&2 "Error: Your working tree has uncommitted changes."; exit 2; }
git diff --cached --quiet || { echo >&2 "Error: Your working tree has uncommitted changes."; exit 2; }

B="$(git rev-parse --abbrev-ref HEAD)"
[ "$B" = "$B_PROD" -o "$B" = "$B_TEST" ] || { echo >&2 "Error: For releases, you must be on the '$_PROD' or '$B_TEST' branch!"; exit 2; }
git show-ref --quiet "refs/remotes/origin/$B" || { echo >&2 "Branch '$B' does not exist in remote refs!"; exit 2; }

L="$(git rev-parse HEAD)"
R="$(git rev-parse "origin/$B")"
[ "$L" != "$R" ] && git merge-base --is-ancestor "$L" "$R" &>/dev/null && { echo >&2 "Your local branch is behind the remote, please pull/rebase first!"; exit 2; }

#
# Fetch and calculate tags
#

git fetch --prune --tags --unshallow &>/dev/null || true
git fetch --all --tags &>/dev/null

LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
[ -z "$LAST_TAG" ] && { echo >&2 "Error: No existing tags found."; exit 3; }

MAJOR=""
MINOR=""
RC=""

if [[ "$LAST_TAG" =~ ^v([0-9]+)\.([0-9]+)rc([0-9]+)$ ]]; then
  MAJOR="${BASH_REMATCH[1]}"
  MINOR="${BASH_REMATCH[2]}"
  RC="${BASH_REMATCH[3]}"
elif [[ "$LAST_TAG" =~ ^v([0-9]+)\.([0-9]+)$ ]]; then
  MAJOR="${BASH_REMATCH[1]}"
  MINOR="${BASH_REMATCH[2]}"
fi

NEXT_TAG=""
case "$B/$1" in
  $B_TEST/minor)
    [ -n "$MAJOR" -a -n "$MINOR" -a -n "$RC" ] && NEXT_TAG="v${MAJOR}.${MINOR}rc$((RC+1))"
    [ -n "$MAJOR" -a -n "$MINOR" -a -z "$RC" ] && NEXT_TAG="v${MAJOR}.$((MINOR+1))rc1"
    ;;
  $B_TEST/major)
    [ -n "$MAJOR" -a -n "$MINOR" -a -n "$RC" ] && NEXT_TAG="v${MAJOR}.${MINOR}rc$((RC+1))"
    [ -n "$MAJOR" -a -n "$MINOR" -a -z "$RC" ] && NEXT_TAG="v$((MAJOR+1)).0rc1"
    ;;
  $B_PROD/minor)
    [ -n "$MAJOR" -a -n "$MINOR" -a -n "$RC" ] && NEXT_TAG="v${MAJOR}.${MINOR}"
    [ -n "$MAJOR" -a -n "$MINOR" -a -z "$RC" ] && NEXT_TAG="v${MAJOR}.$((MINOR+1))"
    ;;
  $B_PROD/major)
    [ -n "$MAJOR" -a -n "$MINOR" -a -n "$RC" ] && NEXT_TAG="v${MAJOR}.0"
    [ -n "$MAJOR" -a -n "$MINOR" -a -z "$RC" ] && NEXT_TAG="v$((MAJOR+1)).0"
    ;;
esac

[ -z "$NEXT_TAG" ] && { echo >&2 "Error: The previous tag '$LAST_TAG' is in an unexpected form for an $1 release."; exit 3; }

#
# Confirm, tag and push
#

read -rp "Do you want to create and push tag '$NEXT_TAG' (previous tag was '$LAST_TAG', current branch is '$B')? [y/N] " answer
if [ "$answer" = "y" -o "$answer" = "Y" ]; then
  git tag "$NEXT_TAG"
  git push origin "$NEXT_TAG"
  echo "Tag '$NEXT_TAG' created and pushed."
else
  echo &>2 "Aborted."
  exit 4
fi
