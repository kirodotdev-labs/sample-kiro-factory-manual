#!/usr/bin/env bash
# The image's `gh`, and through `gh auth git-credential` git's: runs the real gh with a GitHub App
# installation token from the token vending machine. A token lasts an hour and a run can last longer,
# so one is fetched on demand and reused for 45 minutes. A GH_TOKEN the caller sets wins. If fetching
# fails, gh runs with the token it has, or none, and reports the auth error.
set -uo pipefail
if [ -z "${GH_TOKEN:-}" ]; then
  cache=/tmp/factory/secrets/gh-token
  if [ ! -s "$cache" ] || [ $(( $(date +%s) - $(stat -c %Y "$cache") )) -ge 2700 ]; then
    mkdir -p "${cache%/*}"
    if (umask 077; python3.12 /opt/worker/run_credentials.py github > "$cache.$$") && [ -s "$cache.$$" ]; then
      mv "$cache.$$" "$cache"
    else
      rm -f "$cache.$$"
    fi
  fi
  [ -s "$cache" ] && GH_TOKEN="$(cat "$cache")" && export GH_TOKEN
fi
exec /usr/local/libexec/gh "$@"
