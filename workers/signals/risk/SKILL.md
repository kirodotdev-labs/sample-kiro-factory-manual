---
name: risk
description: Verification signal. Re-judges an item's risk level from the change as built and the other signals' readings. Use when a step gathers its verification signals for an item's PR.
---

Run it after the step's other signals: it weighs their readings from this run.

Read the item's risk label, the factory's level of record and whether a person set the label
(`common/risk.md`), triage's assessment, and the PR's diff (`codehost.pr.view` and the files it
changes). Judge the level with the rubric in `common/risk.md` and the instance's `risk-levels`
section, against the change as it now is rather than as triage expected it:
- **Raise** when the diff touches a surface triage did not name, a security surface or a public
  contract, grew well past triage's scope, or another signal's reading names a risk (a review
  finding on a trust boundary, a failed eval).
- **Lower** only on evidence that the change is narrower than triage judged: fewer surfaces, no
  contract touched, tests that cover what triage was concerned about. Lower one level at most per
  head.

Report in a few lines:
- the head sha you read;
- the label, the level of record, and whether a person set the label;
- one of `risk: keep`, `risk: raise to <level>: <why>` or `risk: lower to <level>: <why>`, citing
  `path:line` or a link for the evidence. When a person set the label, report `risk: keep (set by a
  person)` and any disagreement in one line. An item with no risk label gets `risk: keep (unlabeled)`: only triage sets a first level.

The reading passes once it is reported for the current head, whatever level it names: the step
records the level and goes on as it would without one. An instance adds evidence for this signal by
listing more signals or by naming it under `risk-levels`.
