#!/bin/sh
# Verification signal for acme: run the widgets test suite in the current worktree. Acme's code host
# has no CI, so this replaces the Manual's ci_green. Usage: run_tests.sh <pr number>
if out=$(npm test --silent 2>&1); then
  echo "tests: PASS"
else
  printf '%s\n' "$out" | tail -n 40
  echo "tests: FAIL"
fi
