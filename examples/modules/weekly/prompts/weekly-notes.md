# Weekly notes

An example module worker: once a week, post one board item that lists what triage routed.

## Inputs

The board's items triaged in the last 7 days (`board.list`, then `board.get` for each), and the
`Summary:` line each triage comment ends with (`context/summary-lines.md` asks triage for it).

If the board cannot be read, stop and say so. Do not treat it as nothing triaged.

## Outputs

One new board item titled "Weekly notes, <date>", with one line per triaged item: its `Summary:`
line and its link. No item when nothing was triaged.

## Verification

None beyond the selection rule above.

## Decisions

Post or skip. Never asks a human.

<!-- include: common/writing -->
<!-- include: common/guardrails -->
<!-- include: integration board -->
<!-- include: instance -->
