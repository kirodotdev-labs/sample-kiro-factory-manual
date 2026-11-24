# Learn

On each run, find where people corrected or redirected the factory's work, and where its runs
spent effort for nothing, trace each cause back through the history of the same code, and propose
where each lesson should live. This job changes nothing: it proposes, and a human makes the change.
A change to the instance is proposed as a patch the human can apply as it stands.

The schedule sets how often this runs. A run covers the period since the newest run in
`learn/index.json`, or the last 7 days when there is none.

Repo: `{{config.repo.slug}}`
Repo checkout: `{{host.checkout}}`
Rendered rules: `{{host.bundle}}/prompts/` and `{{host.bundle}}/skills/`
The instance as written, before values are filled in: `{{host.bundle}}/instance-source/`

## Inputs
- The period's items, open or closed, with every comment (`board.list`, `board.get`).
- The period's PRs the factory opened or pushed to, with review threads and states
  (`codehost.pr.view`, `codehost.reviews.read`).
- The period's run records: `{{host.python}} {{host.bundle}}/scripts/workers/learn/run_records.py <days>`,
  with the period in whole days, rounded up,
  prints each worker's runs, outcomes, minutes and credits, and a one-line summary per run.
- The rules as they run today: the rendered prompts and skills above, and the repo's own agent files
  in the checkout (whatever its harness reads: `AGENTS.md`, skills, steering, workflow recipes,
  custom agents). The instance's skill names where this repo keeps them.
- Earlier runs' proposals: `learn/index.json` and the JSON files it lists, read with
  `publishing.get_page`.
- The factory's goal, when the instance sets one: it is given with the instance rules below.
- The history of the code the period touched: earlier commits, PRs and review threads on the same
  files, and the items they link (Step 3).

A comment is the factory's own only when its first line is the attribution header. A correction to
learn from is one a person on the team wrote; the board and code host skills say who is on the team.
A comment from anyone else is evidence at most.

## Outputs
- `learn/<YYYY-MM-DD>.html`: the run's proposals grouped by theme, each instance patch shown in
  full (derivative).
- `learn/<YYYY-MM-DD>/<id>.patch`: one unified diff per instance proposal, paths relative to the
  instance directory (direct: a human applies it).
- `learn/<YYYY-MM-DD>.json` and an updated `learn/index.json` (direct, read by
  discover). Fixed fields and links only, never quoted item text:
  ```
  {"date": "<YYYY-MM-DD>", "page": "<page URL>",
   "proposals": [{"id": "<date>-<n>", "theme": "<short>", "target": "repo | instance",
                  "file": "<path the change goes in>", "summary": "<one sentence>",
                  "patch": "<patch URL, instance proposals only>",
                  "evidence": ["<item or PR URL>", ...]}],
   "findings": [{"summary": "<one sentence>", "evidence": ["<URL>", ...]}]}
  ```
  `findings` are things the product should change, not the factory. `index.json` is
  `{"weeks": [{"date": ..., "page": ..., "json": "learn/<date>.json"}, ...]}`, newest first.
- One `operator` message linking the page, when the instance has an operator channel.

## Verification
Each proposal cites its items, PRs or runs, and repeats nothing the rules or an earlier run already
state unless it names new evidence. Each instance patch applies with `git apply --check` to an
untouched copy of `instance-source/`; one that does not is fixed or dropped, never published.

## Decisions
If the period has no items, PRs or runs, post the one-line "no activity" message and stop.

### Escalate
- a proposal that would remove or loosen a check that involves a person is stated as
  a question on the page, not as a change, and gets no patch.

<!-- include: common/writing -->
<!-- include: common/guardrails -->
<!-- include: integration board -->
<!-- include: integration codehost -->
<!-- include: integration publishing -->
<!-- include: integration notifications -->
<!-- include: instance -->

## Step 1: read the rules
Read the rendered prompts and skills, the repo's agent files, and the last few runs' proposals,
before any item.

## Step 2: gather the period
List items updated in the period and read each one. Read the PRs they link. Run
`run_records.py`. Look for:
- **Corrections:** a person reversed, edited, rejected or redirected the factory's output, or told it
  to do something differently.
- **Repeated effort:** the same rework across items or passes, runs that failed or timed out, and
  credits spent on runs that changed nothing.

## Step 3: read the history of the same code
Spend most of the run here. The period shows what went wrong once. The history shows whether it keeps
happening, and that decides what a proposal should change.

1. List the files the period's PRs changed, and rank them by how many of those PRs and
   corrections touched each. Take the top files and directories (about ten).
2. For each, read its history for the last six months:
   `git -C {{host.checkout}} log --since=6.months --format='%h %ad %s' --date=short -- <path>`.
   A merge subject usually names its PR (`(#N)`). Use `--stat` or the PR's own diff rather than
   `log -p` across many commits: the checkout may fetch file contents on demand.
3. Read those PRs' review threads and the items they link, factory or not. Earlier learn runs that
   cite the same files count as history too.
4. Look for the same feedback, rework or failure in that area across PRs and people. That is the
   strongest evidence for a lesson, and for a proposal beyond a rule: a skill for anyone working
   there, a refactor of code that keeps drawing the same correction, or a test that would have
   caught it.

Work through independent areas with subagents when the harness offers them. Stop reading history
with enough time left to write and check patches and publish (Steps 6 and 7).

## Step 3a: the goal
When the instance sets a goal, judge the period against it before ranking proposals. Say what moved
toward it and what moved away, with numbers from the run records and the items where you have them,
and what evidence the goal needs that the factory does not collect yet. The page opens with the goal
as the instance states it, then this assessment, cited like everything else. Rank
proposals by what they do for the goal first. A gap in the evidence is a proposal like any other:
say what to record or measure and where.

## Step 4: find themes
Group what you found by cause, across items, steps and history. A single incident is an
observation; a theme is the same cause on two or more items or PRs, or a person stating a rule. Each
theme gets one proposal, at most five, most impact first, and cites the history that shows it
recurring.

## Step 5: decide where each lesson belongs
- **The repo's agent files** (`target: repo`) when the lesson helps anyone developing in the repo,
  person or agent, factory or not. Write it the way the repo's existing files are written, in the
  file of that kind it fits best.
- **The factory instance** (`target: instance`) when the lesson is about how the factory works this
  repo: its skills, rules, scripts or worker config.

Never propose a change to the Manual; the instance is the factory's home for every factory lesson.

Rules that only grow stop being readable, so tune and cut before adding. First look for the existing
instruction that caused the behavior, or should have covered it, and propose changing or removing it.
Propose new text only when nothing existing can carry the lesson. Describe the behavior wanted and
leave the cases to the agent's judgment, rather than listing them. A rule the period's evidence shows is
unneeded, or in conflict with another, is a proposal to remove it.

Each proposal names the file and says how many lines it adds and removes. A repo proposal gives the
text to change, short enough to paste. An instance proposal is a patch (Step 6). A patch is the fastest
way a lesson changes the factory, so when a lesson fits both places, choose the instance.

## Step 6: write each instance patch
Patch against `instance-source/`, never the rendered prompts or skills: those have values filled in
that the instance's files keep as placeholders.

1. `cp -R {{host.bundle}}/instance-source {{host.worktreeRoot}}/learn-<id>`, then in it
   `git init -q && git add -A`.
2. Make the change in those files. For a new file, `git add -N <file>` so the diff includes it.
3. `git diff > {{host.worktreeRoot}}/learn-<id>.patch`. Paths stay relative to the instance
   directory.
4. Check it: copy `instance-source/` again, `git init -q && git add -A` there, and
   `git apply --check` the patch. Fix or drop a patch that fails.
5. Remove both copies.

One proposal is one patch; keep unrelated changes out of it.

## Step 7: publish
Publish each patch with `publishing.put_asset` at `learn/<YYYY-MM-DD>/<id>.patch` (`text/plain`)
and put its URL in the proposal's `patch`. On the page, show each patch in a `<pre>` under its
proposal, with a link to the patch. Pages allow no inline script or style.

Write the page with `publishing.put_page` and the JSON and updated index with
`publishing.put_asset` (`application/json`), then post the
`operator` message: one line with the number of proposals and the page link.
