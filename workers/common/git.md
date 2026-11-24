# Git mechanics

Every worker that touches the repo uses these. They are mechanics, not policy: nothing here decides
what to build or whether to proceed. Read this file whenever your instructions point at it; do not
re-derive it from the repo or from habit. How the worker authenticates to the code host for
`git` and PR operations is the code host integration's concern (its skill),
not this file.

## 1. Guarantee the remote is fully tracked before any fetch

A factory checkout is often cloned with a narrow refspec (the default branch only), which tracks the
default branch and nothing else. Two different failures come out of that and look unrelated:

- `git fetch` silently never updates any feature branch, so a worktree on an EXISTING branch comes
  up stale and the push is rejected non-fast-forward.
- `--force-with-lease` REFUSES the push with `! [rejected] ... (stale info)` even when the remote is
  exactly where you think it is, because the lease has no refspec-covered tracking ref to compare
  against.

Repair it idempotently at the top of every run that fetches. It is a no-op once present:

```
git config --get-all remote.origin.fetch | grep -q 'refs/heads/\*' \
  || git config --add remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
```

Then `git fetch --all --prune` and confirm the default branch is current.

Diagnosing `stale info` as a blocked or forbidden force-push is WRONG. Force-push to a feature branch
is permitted and is required by the conflict-rebase path. See §4 before concluding anything about
permissions.

## 2. Branch naming

`factory/<shortId>-<slug-of-title>`, shared by every worker. `<shortId>` is the item's id as the
board shows it, without any URL or repo around it (an issue number, a ticket key). The slug is a
few words you choose to describe the title, in lowercase letters, digits and hyphens only, at most
40 characters: write it yourself, never copy characters from the title into a command. Design and implement
use the SAME prefix. The spec PR's branch is deleted when it merges, so the implementation reuses the
name on a fresh branch. When the instance's rules name a different prefix, use it everywhere this
Manual writes `factory/`.

A restart after an approach change appends `-r2` (`-r<n>` for a later one) so the fresh branch cannot collide with the
closed PR's branch.

## 3. Which branch does this item's work live on

Decide this before creating a worktree. Worktrees live under `{{host.worktreeRoot}}/<name>`; never
work in the main checkout.

| Situation | What to do |
|---|---|
| Spec PR **merged** (spec docs on the default branch) | Fresh branch: `git fetch --prune`, then `git worktree add <dir> -b factory/<shortId>-<slug> origin/{{config.repo.defaultBranch}}`. Read the merged spec docs as the spec. |
| **No spec PR at all** (plain fast-track item) | Fresh branch off the default branch, same command as above. |
| The item's PR closed for an approach change (implement Step 8) | The prior PR is CLOSED. Ignore any PR or Spec PR an older post links and do not reuse its branch. Fresh off the default branch with the `-r2` suffix from §2. A NEW draft PR opens from it. |
| An **open PR** already exists for this item (converge/review work), or its branch already exists on origin | `git worktree add <dir> <branch>` tracking the remote branch, and continue from it. |

Do ALL work inside the worktree dir.

## 4. Pushing

- **Name the remote and the branch as literals.** `git push origin factory/<shortId>-<slug>`.
- **Commit messages go in a file**: `git commit -F <file>`, never `-m` with text that came from an
  item or a review (`common/guardrails.md`).
- **First push of a new branch**: `git push -u origin factory/<shortId>-<slug>`.
- **Continuing an existing branch**: plain `git push origin <branch>`. Never force-push a branch you
  did not just rewrite yourself.
- **NEVER push to the default branch**, whatever it is named.
- **After your own rebase**: `git push --force-with-lease origin <branch>`. When the lease goes
  stale, fix the refspec (§1).

Route on the verbatim git error, never on a diagnosis:

| Error | Means | Do |
|---|---|---|
| `(stale info)` | narrow refspec, §1 | repair the refspec, retry the same command |
| `(non-fast-forward)` | your worktree is behind | re-fetch, rebase, retry |
| `Permission denied (publickey)` | credentials | report it; not fixable in-run |

Never report any of these as a permission ban, and never invent a side-branch-plus-manual-
instructions workaround that leaves a human to finish a mechanical step.

## 5. Cleanup (every terminal, including failures)

`git worktree remove --force <dir>` the worktree you created, and release the claim label
(`common/claim.md`). A run that dies holding a worktree leaves disk and a stale claim for the
janitor; a run that dies holding the claim blocks the item until the TTL expires.

Leave the branch and the PR alone on cleanup: they are the run's output, not scratch state.
