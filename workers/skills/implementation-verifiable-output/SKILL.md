---
name: implementation-verifiable-output
description: How implement fuses its verification signals into the implementation verifiable output, the page a human reads instead of the diff. Use in implement's fusion step, on every pass that pushed code or got new readings.
---

# Implementation verifiable output

The change itself is implement's direct output. This page is its derivative: an account of what was
done, made from the verification readings, so a person can judge the change without reading all of
it. It is a representation of what was done, not a review of it.

Implement produces it when it fuses its signals, after gathering every signal on the
current head. It comes out on every pass that pushed code or got new readings, whether or not the bar
was met: a page for a PR that stays draft says what is still pending or failing. A reviewer, when the
domain asks for one, reads this page.

## Inputs

- The diff from the PR's base to its current head, and enough surrounding code to explain it.
- The item, the spec when there is one, and triage's assessment.
- This run's own reasoning: the options you actually weighed, why you chose one, and the areas of
  note. It exists only in the run that built the change, so write it down here.
- Every verification reading from this run, each with the head sha it read.

Keep three kinds of evidence apart and say which is which: what the code shows, what a reading
measured on the current head, and what the run reports it did. A reading of an older head is
pending, not passing. A signal that did not run is not run, never passed.

## Which readings feed which section

A signal's instructions, or an instance skill, may name the section its reading informs. Put
the reading there, as evidence beside your prose, with its head sha and result. A reading that names
no section goes under Testing and confidence. Every reading also appears in that section's readings
table, so the page shows the whole bar in one place.

## Sections

Default order: At a glance, Why this is needed, Decisions and alternatives, Invariants, Complexity
and scope, Implementation walkthrough, Testing and confidence. Leave out a section that does not
apply, and its navigation entry. Leaving one out is not a claim that its subject was checked.

- **At a glance.** What changes, before and after, in behavior a user or caller sees. What is
  unchanged at the boundary. The areas of note a reviewer should look at first.
- **Why this is needed.** The problem and who it affects, from the item and the spec. Attribute
  evidence accurately: a tracking item alone does not show that anyone approved the approach.
- **Decisions and alternatives.** The choices made, the alternatives you actually weighed, and the
  costs you accepted. Never reconstruct alternatives you did not weigh. Contested choices from review
  threads go here with where they stand.
- **Invariants.** The instance's invariants, each read for this change: holds, at risk, or does not
  apply, with the evidence. Include one when it might bear on the change. The Manual ships none; an
  instance adds them in its skills.
- **Complexity and scope.** What the change touches and how far it reaches: files, lines, and
  whatever the instance's scope signals measure, with their limits visible. Counts and heuristic flags
  do not establish quality. Scope beyond what the item or spec asked for is a finding here.
- **Implementation walkthrough.** How the change works, before and after, smaller than the diff.
  Diagrams, prose or short excerpts, whichever explains it. For a large or complex change, follow the
  `implementation-walkthrough` skill instead; this section then holds one sentence and a link to its
  tab.
- **Testing and confidence.** What was verified and what that establishes: the verification level
  triage asked for against what actually ran, each test or check reading, and what was not run.
  Confidence is stated plainly, with its blind spots, never inflated.

## Make the page

Start from `templates/output.html` and its stylesheet `templates/output.css`. They supply a layout,
not a form: remove unused placeholders.
Write plainly and briefly, keep material caveats visible, and put supporting detail in disclosures.
The page carries no scripts, and its styles stay in the stylesheet, packaged the way the publishing
integration's skill says. Escape source text, include no secrets, give diagrams a text equivalent,
and open external links in a new tab.

## Publish and link

- Publish with `publishing.put_page` at `items/<item>/implementation.html`, the same key on every
  pass, so the link never changes, and the stylesheet with `publishing.put_asset` at
  `items/<item>/implementation.css`. Replace the page whenever the change or its readings make it
  inaccurate. A rebase or a moved line alone is no reason.
- Link it from the PR body and from the item's status comment.
- If the page cannot be published, finish the run and say so in the status comment. Never fail the
  run over this page.
