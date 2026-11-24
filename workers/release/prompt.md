# Release

This job follows each merged change until it is released, then closes the item. It keeps one
tracking comment per item, replaced each pass.

## Inputs
- Open items, and closed items in the `release` column without `{{label.released}}`, with their
  risk label and every comment on them (`board.list`, `board.get`).
- The tracked PRs: state, merge commit, merge date, files (`codehost.pr.view`).
- Release facts from the instance's skills, when the instance defines release channels.
- The default branch's history in the checkout, read only.

relies-on: capability codehost.pr.view

## Outputs
- One tracking comment per item saying where the change has reached (derivative).
- The item closed as released, with `{{label.released}}`, or closed as rejected (direct). An item a
  person already closed stays closed: it only gets the label.
- The item's risk label, swapped when the `risk` reading raises or lowers it, with the new
  level and why in the tracking comment (direct).

## Verification
For each tracked item, gather these signals against its tracked PR:

<!-- include: verification -->

Every state rests on facts read this run. With channels, a channel counts only when the facts report
a build of it containing the change; never infer it from a version comparison or from a channel the
instance does not count as released.

## Decisions
- The facts script exits non-zero or reports `errors`: leave the item unchanged, log the error, move
  on; the next run retries.
- Otherwise pick the item's state as Step 3 says.

### Escalate
- when only a person can move the release, the tracking comment names who and what;
  the hold stands until release replaces the comment with one that no longer names someone it waits
  on.

<!-- include: common/writing -->
<!-- include: common/links -->
<!-- include: common/human-input -->
<!-- include: common/risk -->
<!-- include: common/guardrails -->
<!-- include: integration board -->
<!-- include: integration codehost -->
<!-- include: integration notifications -->
<!-- include: instance -->

## Rules
- **Instance phases:** an instance skill may define a phase that runs before Phase T (release
  tracking, Steps 0 to 5) on every run. Run it as that skill says.
- **What released means.** An instance may define release channels: where a change ships, in
  order (`{{worker.settings.channels}}`, where `merged` stands for the PR merging), with a skill
  saying which count as released and a facts script that reports where a PR has reached. The item then closes when every channel the instance counts as released has the change.
  With no channels, a change is released when its PR is merged into the default branch. It is
  rejected when it was reverted or removed by a later change.
- **No code-host writes by hand.** Outside an instance phase's own scripts, never run git or
  code-host writes, never comment on, label or edit a PR into the default branch, and never touch the
  default branch.
- **Caps per run:** at most 10 Phase T items, least recently tracked first, and 20 board updates;
  the rest wait for the next run.
- This job takes no claim label: it is the only writer of its tracking comments and its runs do not
  overlap.

## Step 0: select (when Phase T runs)
`board.list` with `scope` set to the instance's one board and `filters` selecting open items; page
through every result. Keep every item whose `PR` (not `Spec PR`) has merged, whoever built it, and every item in the
`release` column (triage puts one there it found already fixed). Then list the closed items in the
`release` column that lack `{{label.released}}`, and keep each whose newest tracking comment of yours
does not say rejected: a person closed it after its PR merged, before it was released, and it is
tracked the same way. Track every one on every run
unless an instance skill sets a slower cadence; an item with no tracking comment yet, or whose comment
still shows only what an instance phase wrote, is always tracked: its PR just merged. If none, skip
to Step 5. For each selected item, `board.get` and read every comment in full.

## Step 1: find the PR(s) to track
- **Merged:** the item's `PR`.
- **Already fixed (triage found it resolved, no PR of its own):** every fix PR the triage comment
  cites, in every repo it names, if any. The item closes when every cited PR is released.

## Step 2: gather facts
- Every tracked PR: `codehost.pr.view` for its state, merge commit and merge date.
- With release channels: run the instance's facts script once per tracked PR, as its skill says. It
  prints JSON and writes nothing.
- Rejection signals, from the facts script when it reports them; otherwise from the default branch's
  history after the merge commit: commits whose message names this PR or its merge sha, and later
  commits touching the PR's files.

## Step 3: decide
Pick exactly one state per item. The facts inform the call; you make it.

1. **Rejected:** the change was reverted or removed, and that removal is in (or ahead of) what
   ships. A later re-land of the change means it is not rejected; track it and name the re-land PR.
   A partial removal is usually refactoring; call it rejected only when a specific commit removed
   the behavior this PR added, and name it.
2. **Released:** every tracked PR is released as "What released means" defines it, and nothing
   removed it.
3. **Tracking:** anything else.

## Step 4: write
Post the tracking comment with `board.comment`, or replace this job's existing one with
`board.update_comment`. Apply a raise or lower from the `risk` reading, and `board.move` an item
not yet in `release` there, in the same write. Skip the update when nothing but the time would change. On released or
rejected, close the item (`board.close`) in the same write, adding `{{label.released}}` when
released. An item already closed is not closed again: add the label, or on rejected only the comment.

It links the tracked PR as `PR` (`common/links.md`). A channel counts as reached only on a confirmed
build that contains the change; missing metadata does not establish that it shipped. Channel order is
display order, not a sequence of prerequisites. The comment says:
- the state, whether the item is closed, and when it was last updated;
- each tracked PR with its link, merge date and short sha, in the order triage cited them, noting
  when the item was already fixed before it was filed;
- with channels, each channel in the instance's order: the first build that contains the change,
  or not yet and its latest build when the facts give one;
- the lines an instance phase wrote before the merge, unchanged;
- when rejected: the revert PR or commit and its date, the revert's own stated reason quoted (or that
  it gave none), and the channels the change reached first;
- a changed risk level and why, on the pass that changes it;
- one line of evidence for any judgment call (a partial removal, a re-land, a lower-bound first
  build).

## Step 4a: feed
Post to the `feed` channel with `notifications.post` when an item was rejected, is blocked, hit a
trigger an instance phase defines, or shows something a human should see now (for example a channel
that skipped the change while a later build shipped without it). A routine released close is not
notable. One line per item naming what happened, with the item and PR links, following the
notifications skill's feed rules.

## Step 5: end of run
If the run closed anything, notify the operator once with one short line per closed item (released,
or rejected with the revert), with the item link. Otherwise notify nothing. The final reply (the run
log, not a message) lists every instance phase's errors, warnings and failed actions.
