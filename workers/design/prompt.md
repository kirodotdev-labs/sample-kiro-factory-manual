# Design

Promote every merged spec, merge every approved one and submit every converged one, then author,
revise, restart or sync at most one spec per run.

Board: `{{config.integrations.board.boardId}}`
Repo: `{{config.repo.slug}}`
Repo checkout: `{{host.checkout}}`
Worktrees: `{{host.worktreeRoot}}/spec-<shortId>`

## Inputs
- The routed item and every comment (`board.get`). Act only on items triage labelled `{{label.spec}}`;
  triage's Specification and Design axes shape what the docs cover (Step 2).
- The spec PR: its state, draft flag, review decision, mergeability, checks (`pr.view`,
  `checks.read`) and human input (`reviews.read`).
- The repo as it really is (the repo checkout). AUTHOR, RESTART, REVISE and SYNC abort before
  writing without one.

Spec state comes from the item's comments and the PR, never the column.

relies-on: worker triage

## Outputs
- Requirements, and a design when the approach needs review, in a draft spec PR (direct). The item
  links it as `Spec PR` (`common/links.md`).
- A spec review page at `items/<shortId>/spec.html`, linked from the item and the PR (derived).
- An approved spec PR queued to merge (direct).
- `{{label.fastTrack}}` on an item whose spec merged, which unblocks build (direct).
- At most one feed message per run, listing the specs it submitted, self-approved or promoted; an
  operator notification when the repo cannot be updated.

## Verification
<!-- include: verification -->

Gather the signals against the spec PR's current head before SUBMIT.

## Decisions
- **Approval is a human's.** A spec PR is approved only when `pr.view` reports the review decision
  as approved: a human's Approve review that satisfies the default branch's protection. An
  approving comment is not approval: reply once on the PR asking for an Approve review and leave the
  PR as it is. A formal approval merges even with minor threads open. Never push to an approved spec
  PR. An instance skill may let design approve a spec PR itself; it names the signals that decide
  it, and a PR it approves counts as approved for MERGE, and is a feed event (below).
- **Failure**: on any failure in AUTHOR, RESTART or REVISE, remove `{{label.claim}}`, post a
  failure comment, clean up the worktree, and stop. A failed `pr.ready` or feed post is retried by a
  later run and never fails the step.
- **Notifications**: the Step 1 operator notification, and at most one feed message per run (the
  notifications skill) listing this run's feed events: a spec PR SUBMIT marked ready, one an
  instance skill approved, and one PROMOTE handled, each as `<Spec ready for review | Spec approved
  by the factory | Spec merged, build next>: <spec title or item name>` with its PR and item links.
  Record each event on its item (SUBMIT's and the approval's in a one-line comment, PROMOTE's in the
  promotion comment) and skip any the item already records. A failed post never fails the step.

### Escalate
- a review comment cannot be safely addressed, or two designs disagree on approach:
  escalate per `common/review-loop.md`, leaving the thread open with the decision stated. Do not
  loop within one run.
- SYNC has made 3 sync merges on the PR (Step 3.7 step 5).

<!-- include: common/writing -->
<!-- include: common/links -->
<!-- include: common/human-input -->
<!-- include: common/review-loop -->
<!-- include: common/risk -->
<!-- include: common/claim -->
<!-- include: common/git -->
<!-- include: common/guardrails -->
<!-- include: integration board -->
<!-- include: integration codehost -->
<!-- include: integration publishing -->
<!-- include: integration notifications -->
<!-- include: instance -->

---

## Step 0: Pick the work (cheap; no repo yet)
`board.list` on `{{config.integrations.board.boardId}}`, open items only, every page. Select on
labels, not the column. Keep items labelled `{{label.spec}}` and not labelled `{{label.fastTrack}}` (build
owns it) or `{{label.claim}}`; apply these exclusions client-side. Read each candidate's comments
and its spec PR to place it in the first mode that fits. A spec PR whose head branch is in a fork
is not this step's to revise, sync or merge: say so on the item once and leave that PR alone.

1. **PROMOTE**: the spec PR merged. Handle EVERY such item via Step 4, then continue down this list.
2. **MERGE**: the spec PR is open and approved (Decisions) and not yet queued to merge. Handle
   EVERY such item via Step 3.8, then continue down this list.
3. **RESTART**: an approach change (implement Step 8, or a human naming the approach to restart
   with) was posted and no spec PR has opened since. Author fresh
   via Steps 1 to 3: any earlier PR is closed and describes the rejected approach. The latest
   approach change is authoritative: its chosen approach is your input, its rejected approach is
   what not to re-propose. New branch with the `-r<n>` suffix (`common/git.md` §2). Handle the oldest and stop.
4. **REVISE**: the spec PR is not approved and has actionable feedback not answered since the latest
   revision: an unanswered inline thread, changes-requested review, top-level review body or PR
   comment, human or bot (check reviews and PR comments, not only threads). A reply that only
   holds a bot finding "as a suggestion pending human assessment" does not answer it. Skip an
   item whose status comment carries an open escalation with no human reply since. Handle
   the oldest via Step 3.5 and stop.
5. **SUBMIT**: The spec PR is open, a draft, not approved, not a REVISE candidate. Check EVERY such
   item via Step 3.6, then continue down this list.
6. **SYNC**: only when no item fits 3 or 4. As SUBMIT, but a check fails (none pending) or the PR is
   settled conflicting. Skip it when its head commit is under 24h old, or when the item's status is
   blocked on a spec-CI escalation with no push since. Handle the oldest via Step 3.7 and stop.
7. **AUTHOR**: no spec PR yet. Handle the oldest via Steps 1 to 3 and stop.
8. **Closed spec PR**: the latest spec PR closed unmerged and no approach change came after it. If
   this worker has not commented since it closed, post one comment: it closed unmerged, and a human
   should name the approach to restart with, reopen the PR, or close the item. Otherwise
   skip it silently.

Nothing actionable: stop, no notification. Only RESTART, AUTHOR, REVISE and SYNC touch the repo.

## Step 1: Repo (RESTART, AUTHOR, REVISE, SYNC)
Ensure `{{host.checkout}}`, the host's clone, is fresh:
`git fetch --all --prune` and `git pull --ff-only` on `{{config.repo.defaultBranch}}`. Apply
`common/git.md` §1 (a REVISE run reads a stale branch without it), §2 and §4. If it fails, post one
operator notification (`notifications.post` channel `operator`): "Spec run aborted: could not update
the repo checkout — <git error>." Leave the item untouched and stop.

## Step 2: Author the spec doc(s), by Specification axis
Claim per `common/claim.md`. In the re-check, if a post linking a Spec PR arrived meanwhile, remove `{{label.claim}}` and stop.

Read the item (description and ALL comments, including triage's classification) and audit the
relevant code so the spec is grounded in the actual repo.

Author under the repo's spec directory, `<specDir>/<slug>/` (`<specDir>` is `specs` unless the
instance's rules or skills name another), in a worktree off the default branch per `common/git.md`
§2-3:
`git worktree add {{host.worktreeRoot}}/spec-<shortId> -b factory/<shortId>-<slug> origin/{{config.repo.defaultBranch}}`.
Do NOT use a `spec/` prefix.

Write requirements and a design, whatever triage's Specification and Design axes say. When
Specification is `product vetting needed`, the design follows the requirements as written and lists
which parts change if product changes them. An item that reached spec only for a vetting question
(Design `none`) gets a short design.

The instance's rules or skills may set the spec format. The default:

- **Requirements:** the problem, the desired outcome and the scope, then numbered requirements, each
  with acceptance criteria a test could check: one observable behavior each, covering the error and
  boundary cases. For a bug: current behavior, expected behavior, and what must stay unchanged.
- **Design:** the approach, the components and interfaces it changes, error handling, the testing
  strategy, and open questions.

The design MUST address every requirement. Ground every claim in real code: cite `path:Lstart-Lend`;
do not invent files. State unknowns explicitly. **Design-for-reuse check:** before the design
introduces any NEW mechanism (a state field, a mapping/alias table, a pattern-matching layer, an
eager/push-based process, a new metric), enumerate the EXISTING mechanism(s) that could serve the same
purpose and prefer reusing or extending them. If it still introduces something new, record the rejected
reuse option and why in the design. If the simplest approach suggests the
item's premise itself may be wrong (e.g. the behavior being specified shouldn't exist), raise that as
an explicit question in the spec PR rather than designing around it.

We do NOT author a tasks document. The build step turns the approved design into code directly.

## Step 3: Open the spec PR (draft) and hand off
- Commit `docs(spec): <slug> requirements + design` (no shortId prefix; `common/git.md` §4). Push with
  `git push -u origin factory/<shortId>-<slug>`.
- `pr.open`, head that branch, base the default branch, `draft=true`, title like
  `docs(spec): <item title>`. The body summarizes the item and names who should review (product for
  the requirements when Specification is `product vetting needed`, eng for the design, the domain
  owners when triage names a domain-owner case, a security reviewer when Security is `notable`), says it is a spec for
  human review authored by an automated agent, links the item, and requests review.
- Build the spec review page (below) and add its link to the PR body.
- In ONE board update, remove `{{label.claim}}` and comment on the item (do not move it) with the
  spec PR linked as `Spec PR`, the page link, who reviews, and that the item is labelled `{{label.fastTrack}}` once the spec PR is approved and
  merged.
- Remove the worktree (`common/git.md` §5; keep the branch). Stop.

## Step 3.5: Revise a spec PR from review feedback (REVISE)
1. Claim per `common/claim.md`. In the re-check, if another run revised the spec meanwhile, remove
   `{{label.claim}}` and stop. Run the review loop (`common/review-loop.md`), counting passes by the
   status comment's revision count. If it escalates, skip to step 6; if it settles a choice, revise
   the spec to it below.
2. Worktree on the existing branch (`common/git.md` §3):
   `git worktree add {{host.worktreeRoot}}/spec-<shortId> factory/<shortId>-<slug>`.
3. Collect every unanswered thread, top-level review body and PR comment, human's before bots'
   (`reviews.read` marks bots). If legitimate, revise the docs; if not, prepare a brief rebuttal.
   Reply to each (`pr.reply`; a top-level body has no thread, so `pr.comment` quoting it) with what
   changed ("Revised in <sha>: ...") or why not, and resolve the threads you addressed. Never finish
   having replied only to bots while a human comment is unanswered. A human question you cannot
   resolve gets a reply saying it is flagged for a human.
4. Commit `docs(spec): revise <slug> per review` and push (`common/git.md` §4). Leave the draft
   state alone.
5. Refresh the spec review page at the same key.
6. In the same board update that removes `{{label.claim}}`, replace this job's own status comment on the item (never the authoring comment). It says the spec PR
   link, the revision count with the latest short sha and date, the status (revised and submitting
   for human review once every signal passes while the PR is a draft, awaiting human review once it
   is not, or escalated, naming who and what), at most one sentence of
   outstanding contention, and any feed record the item already carries.
7. Remove the worktree (keep the branch). Stop.

## Step 3.6: Submit a converged spec PR (SUBMIT)
For each selected PR, re-read it with `pr.view` and gather the signals. Run `pr.ready` when the PR is open, a draft, settled mergeable, and every verification signal passes on
the current head. An unknown mergeable state is
not settled: re-read up to 3 times a few seconds apart, then skip the PR this run. Red CI or a
conflict is SYNC's. A successful flip is a feed event, and its status comment is SUBMIT's only board write.

## Step 3.7: Sync a red or conflicting spec draft (SYNC)
No claim. The push is the record; the only board write is the step 5 escalation.
1. Repo per Step 1, then a worktree on the PR's branch. Confirm
   `git diff --name-only origin/{{config.repo.defaultBranch}}...HEAD` lists only paths under
   `<specDir>/`; otherwise stop, SYNC does not apply.
2. `git merge -m "Merge origin/{{config.repo.defaultBranch}} into <branch> (spec sync)" origin/{{config.repo.defaultBranch}}`.
   Merge, never rebase. On a conflict keep the spec's own text and the default branch's version of
   anything else. If the default branch rewrote the same spec, abort the merge and stop without
   pushing; that needs REVISE with a human's direction.
3. If a failing check reads the spec files (markdown, docs, spelling, format lint), read its output
   and fix the spec in a new commit. Any other check runs code the spec does not touch; the merge is
   the fix.
4. `git push origin <branch>`, never a force-push. Remove the worktree. Leave the draft state alone.
5. Cap: count earlier sync merges with
   `git log --merges --format=%s origin/{{config.repo.defaultBranch}}..HEAD | grep -c '(spec sync)$'`.
   When there are already 3 and CI is still red, do not merge again. Post or replace this job's
   status comment on the item (never the authoring comment that links the Spec PR), escalated to a
   human because spec CI is still red after 3 syncs, naming the failing checks, with the spec PR
   link, unless the item already says so for this head. Stop.

## Spec review page
A single HTML page at key `items/<shortId>/spec.html` (`publishing.put_page`), built in AUTHOR and
refreshed at the same key in REVISE, so its URL never changes. It is a review aid; the docs under
`<specDir>/<slug>/` are canonical.
- Package it as the publishing integration's "Page packaging" says; nothing loaded from other
  origins.
- The whole spec on one dense, skimmable page: every requirement with its acceptance criteria
  verbatim, the design's sections, an anchored TOC, and a header linking the spec
  PR and the item. Invent nothing.
- Each write follows the publishing skill for concurrent writes and who may read a non-public page.
- If the item has no comment linking the page, add the link in this run's comment.
- If publishing is unavailable or a publish fails, finish the run and say the page was skipped in
  the item comment.

The project's own page-styling rules and any extra publishing mechanics are the instance's.

## Step 3.8: Queue an approved spec PR to merge (MERGE)
For each selected item, re-read the PR; skip it unless still open and approved. `pr.ready` it if a
draft, then `pr.merge`; the code host lands it once its checks pass. Unless the item already says
so, comment: the spec PR approved by @<handle> is queued to merge, and the item is labelled
`{{label.fastTrack}}` for a new implementation PR after it lands. On a settled conflict or a failed
`pr.merge`, do not push; say so once with the verbatim error, and that a human must resolve it and
re-approve. Do not add `{{label.fastTrack}}` here.

## Step 4: Promote a merged spec (PROMOTE)
In ONE board update add `{{label.fastTrack}}` and keep `{{label.spec}}` (provenance), and comment that
the spec PR (linked as `Spec PR`) was approved and merged and the build step will implement it on a
fresh branch and open a new PR.
Do not move the column; build moves it when it opens a PR.

A promotion this run made is a feed event (Decisions); note the post, or its failure, in the promotion
comment.
