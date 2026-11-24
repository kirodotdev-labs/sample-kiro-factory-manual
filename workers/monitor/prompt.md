# Monitor

This job picks up items release closed as released and checks each change against production
telemetry: did it do what it meant to, and did it move anything else. It keeps one monitoring comment
per item and decides when the answer is settled.

## Inputs
- Closed items labeled `{{label.released}}` and not `{{label.monitoringComplete}}`, with every comment
  (`board.list`, `board.get`). The label means release closed the item as released.
- The PRs release tracked for each item (`codehost.pr.view`).
- The instance's telemetry skill: where its metrics live and a read-only facts script.

relies-on: worker release
relies-on: capability codehost.pr.view

## Outputs
- One monitoring comment per item: the plan, before/after results and a verdict (derivative).
- `{{label.monitoringComplete}}` when monitoring is done (direct).

These are the only writes this job makes. It never reopens or moves an item and never comments on a
PR.

## Verification
Each result names its metric, source, before and after values and sample sizes, read this run with
the facts script. Too few events is not measurable, never no change.

## Decisions
- The instance gives no telemetry source: end the run without writing.
- The facts script fails: leave the item unchanged and log the error; the next run retries.

### Escalate
- a regression, or a measurable change that did not help: say so in the comment and
  post to the `feed` channel with the item link, following the notifications skill's feed rules.
  Never reopen the item; a human decides what follows.

<!-- include: common/writing -->
<!-- include: common/links -->
<!-- include: common/risk -->
<!-- include: common/human-input -->
<!-- include: common/guardrails -->
<!-- include: integration board -->
<!-- include: integration codehost -->
<!-- include: integration notifications -->
<!-- include: instance -->

## Step 1: select
List closed items with `{{label.released}}` and without `{{label.monitoringComplete}}`, oldest
monitoring update first. For each, read every comment, including this job's own monitoring comment
if one exists. If none qualify, end the run silently.

## Step 2: plan (first run per item)
Read each tracked PR's diff and list the metrics that bear on the change: the ones it emits or
modifies (confirm each exists in production with the facts script) and the existing ones it could
move (the operation's success, failure and latency, the error it fixes). Also read the item's
decisions of record and the choices stated at the top of each PR body, and list each one a metric
can speak to. Write the plan into the comment. Later runs reuse it; revise only on evidence, and say why.

If nothing about the change is observable, say why and finish (Step 5).

## Step 3: measure and judge
Compare the change's first released build against the build before it, as rates normalized by a
denominator where one exists; raw counts move with adoption. For each metric give before, after and
sample size. Attribute a movement only when the metric is specific to the change or nothing else in
the build touches it.

Verdict: regression, improved, no change, not measurable, or did not help.

## Step 4: write
Post the monitoring comment, or replace this job's existing one, adding any label from Outputs in the
same write. The comment states the verdict, then gives the window (build before, first released build, release date, planned end), the
plan, each metric's result and judgment, and an assessment: did it do what it meant to, and did
anything else move. For each listed choice, one line: the telemetry supports it, contradicts it or
cannot tell, and for a contradicted one, whether going back on it can be undone (`common/risk.md`).

## Step 5: when to stop
You decide, within the window the instance's skill sets. Without one, stop about a week after release
when the results are stable, sooner when nothing is observable or the result is conclusive, and
extend while a signal is ambiguous or the sample is small, writing the reason. On done, add
`{{label.monitoringComplete}}` and say in the comment that monitoring is complete.
