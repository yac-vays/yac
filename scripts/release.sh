#!/usr/bin/env bash
set -euo pipefail

b=$(git branch --show-current)
r=$(git config "branch.$b.remote")
git fetch --tags "$r"

[ -z "$(git status --porcelain)" ] || { echo "uncomitted changes in worktree: commit your changes before release"; exit 1; }
[ "$(git rev-parse HEAD)" = "$(git rev-parse @{u})" ] || { echo "local & remote branch differ: push your changes before release"; exit 1; }

case "$b" in
  test) re='^v[0-9]+\.[0-9]+rc[0-9]+$' ;;
  main) re='^v[0-9]+\.[0-9]+$' ;;
  *) echo "release only from main or test branch"; exit 1 ;;
esac

t=$(git tag --points-at HEAD | grep -E "$re" | xargs || true)
[ -z "$t" ] || { echo "this commit is already released as: $t"; exit 1; }

inc(){ [[ $1 =~ ^v([0-9]+)\.([0-9]+)$ ]]; echo "v${BASH_REMATCH[1]}.$((BASH_REMATCH[2]+1))"; }

s=$(git tag -l 'v[0-9]*.[0-9]*' | grep -Ev 'rc' | sort -V | tail -1 || true)
rc=$(git tag -l 'v[0-9]*.[0-9]*rc[0-9]*' | sort -V | tail -1 || true)

case "$b" in
  main) v=$(inc "${s:-v0.0}") ;;
  test)
    if [ -z "$rc" ]; then v="$(inc "${s:-v0.0}")rc1"
    elif [ -n "$s" ] && [ "$(printf '%s\n%s\n' "${rc%%rc*}" "$s" | sort -V | tail -1)" = "$s" ]; then v="$(inc "$s")rc1"
    else [[ $rc =~ ^(v[0-9]+\.[0-9]+rc)([0-9]+)$ ]]; v="${BASH_REMATCH[1]}$((BASH_REMATCH[2]+1))"
    fi ;;
esac

git tag "$v"
git push "$r" "$v"

echo "previous latest releases: ${s:-none} (main), ${rc:-none} (test)"
echo "successfully released $v ($b) now"
