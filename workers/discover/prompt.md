# Discover

Turn what the factory itself has observed into new items on the board, so the loop closes: a
released change that regressed, did not help or proved a choice wrong, and the product findings
learn reports. Discover is
the only step that creates items. Triage takes it from there like any other item.

Board: `{{config.integrations.board.boardId}}`
Repo: `{{config.repo.slug}}`

## Inputs
- Closed items labelled `{{label.released}}`, updated in the last 30 days, whose monitoring
  comment gives the verdict regression or did not help, or says the telemetry contradicts a choice (`board.list`, `board.get`).
- Learn's latest findings: `learn/index.json`, then the newest run's JSON it lists, its `findings`
  list (`publishing.get_page`). Absent until learn has run; skip it then.
- Existing `{{label.discovered}}` items, open and closed, and the open board, to dedupe.

Monitor's verdict and learn's findings are taken as given; discover does not re-measure.

relies-on: capability board.create

## Outputs
- One new item per candidate that passes verification, labelled `{{label.discovered}}`, in the
  intake column (`board.create`) (direct).
- `discover/<YYYY-MM-DD>/<run>.html`, every run, including runs that file nothing: each candidate,
  what it was checked against, and whether it was filed or skipped and why (derivative).

## Verification
Each candidate passes both checks before it is filed:
- **Not already tracked:** no `{{label.discovered}}` item, open or closed, links the same source,
  and no open item asks for the same thing or links the same evidence, however it was filed. A
  discovered item a human closed for this source stays closed; do not file it again.
- **Actionable as written:** someone reading only the new item could say what should change and how
  to tell it is done.

## Decisions
- A candidate that fails a check is not filed; the run page gives the reason.
- Triage is the gate for what discover files. A candidate that is a direction choice rather than a
  task (several sources pointing at one theme with more than one way to address it) is still filed;
  its body gives the evidence and two or three options, and triage or a person picks.

<!-- include: common/writing -->
<!-- include: common/links -->
<!-- include: common/guardrails -->
<!-- include: integration board -->
<!-- include: integration publishing -->
<!-- include: instance -->

## Step 1: gather candidates
A candidate is one ask: a follow-up item's regression, unmet intent or contradicted choice, or one
learn finding. Several
sources that point at the same cause are one candidate (a theme). Check each as Verification says.

## Step 2: file
`board.create` with `labels=["{{label.discovered}}"]`, a short title stating the ask, and this
body after the attribution header and a blank line:

```
## What & why
<the ask, restated in one to three sentences, and what it is meant to fix>

## Type
<Bug | Feature / enhancement>

## Acceptance criteria
- <one testable statement per line>

## Evidence
<what was observed, with numbers where the source has them>
Source: <link>
Source: <link>
```

A theme adds `## Options` with two or three ways to address it and the tradeoff of each. A
contradicted choice adds `## Options` with two: go back on it, or keep it.

## Step 3: publish the run page
Write `discover/<YYYY-MM-DD>/<run>.html` with `publishing.put_page`, using the run id from the
attribution header. List every candidate with its sources, the dedupe match it found (or none), and
the outcome: the new item's link, or why it was skipped.
