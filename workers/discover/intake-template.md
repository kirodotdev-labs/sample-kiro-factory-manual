<!--
This is the contribution INTAKE TEMPLATE for a factory's board.
It has two layers:
  1. CORE TEMPLATE (top) — what a human copies into a new item. Light and approachable.
  2. AGENT FILL GUIDE (bottom) — detailed instructions for an agent helping a requester
     turn rough notes into a high-quality intake.
It is the intake-side mirror of the triage rubric: every field here maps to something the
triage worker evaluates, so a well-filled intake gets classified accurately and fast instead
of bouncing back as "needs info".

An instance that names its fast-track label can hand the requester that name; this generic template
leaves it open. The template is a
shipped asset, not a rendered rubric, so it carries no render directives.
-->

# 🎯 Contribution Intake

> Copy the block below into a new item. Fill what you can — the clearer the acceptance
> criteria, the faster this moves. Rough idea? Ask an agent to help you fill it in (see the
> agent guide at the bottom).

---

## CORE TEMPLATE (copy this)

```
## What & why
<1–3 sentences: what should change, and the problem/outcome it drives.>

## Type
<Bug | Feature / enhancement>

## Acceptance criteria — how we'll know it's done
<One clear, testable statement per line — something you could check pass/fail. One behavior
 per line. Say WHAT the system should do, not HOW to build it. Try to include the error /
 empty / edge case, not just the happy path. Plain language is fine; no special format needed.>
-
-


## If this is a bug
- Steps to reproduce:
- Expected vs. actual:
- Where seen (version / client / environment):

## Pointers (optional, but speeds triage a lot)
- Relevant files / modules / PR / branch / commit:
- Out of scope / non-goals:
- Known dependencies or related items:
```

---

## Why these fields (the short version)

An automated triage worker reads every new item and classifies it on several axes. This
template exists so you supply the answers up front instead of the worker having to guess:

| You write… | Triage reads it as… |
|---|---|
| **Acceptance criteria** (clear, testable) | **Specification** — clear, testable criteria let it be picked up directly; less-defined ones route to product or eng vetting to sort out first (not a rejection). Only a request nobody can act on yet comes back to you for more info. |
| **Type + repro (for bugs)** | **Type=Bug**, and whether it can be reproduced |
| **Pointers / files** | grounds the engineering assessment in real code |
| **Scope / non-goals** | catches oversized change sets that should be split |

An item that is unambiguous, testable, and needs no design can be
fast-tracked straight to implementation. Clearer, testable acceptance criteria are the
biggest lever you control — they move an item toward that fast path rather than a slower
product/eng vetting round. Less-defined items are still welcome; they just take the vetting
route first.

===================================================================================
# AGENT FILL GUIDE — for an agent helping a requester write this intake
===================================================================================

You are helping a human turn a rough request into a high-quality intake. Your job is
NOT to fill a form mechanically — it is to raise the quality of the request so it
survives triage. Interview the requester where a field is thin. Ground everything in
the actual repo where you can; do not invent behavior.

## The one thing that matters most: testable acceptance criteria (use EARS internally)

The triage worker will try to author each acceptance criterion as an EARS statement and
check that it is TESTABLE. Do NOT force the requester to write EARS — keep the
human-facing criteria in plain language. Use EARS yourself as an internal lens to
pressure-test each criterion, and rewrite the plain-language criteria until they'd pass.
Clear, testable criteria let an item be picked up directly; if they can't be made testable,
the item routes to product or eng vetting (not a rejection) — only a request nobody can act
on yet comes back to the requester for more info. EARS patterns (for your own use):

- Ubiquitous:   `THE <system> SHALL <response>.`
- Event-driven: `WHEN <trigger>, THE <system> SHALL <response>.`
- State-driven: `WHILE <state>, THE <system> SHALL <response>.`
- Conditional:  `IF <condition>, THEN THE <system> SHALL <response>.`
- Optional:     `WHERE <feature>, THE <system> MAY <response>.`

Every criterion must be:
- **Testable** — you could write a pass/fail test for it. Golden rule: if you can't write
  a test for it, rewrite it. Reject vague predicates: "works well", "is fast",
  "user-friendly", "handles X gracefully".
- **Atomic** — one behavior per line. If a line joins two checks with "AND", split it.
- **Outcome-focused** — state WHAT (observable behavior), not HOW. Do NOT prescribe class
  names, API paths, or specific services — that is the implementer's choice, and putting
  it here causes reviewers to enforce implementation instead of intent.
- **Boundary-aware** — cover the error / empty / boundary / idempotency case, not only the
  happy path. A happy-path-only spec is an incomplete spec.

If the intent itself is still fuzzy (not just the criteria), get the missing detail from the
requester — that is the case that genuinely can't move forward without them.

## Fill each field with triage in mind

- **What & why** — the problem and desired outcome, not a solution. Keep it to a few
  sentences; detail belongs in the acceptance criteria.
- **Type** — Bug if it's a defect in EXISTING behavior (something broken/incorrect/
  regressed); otherwise Feature/enhancement.
- **Bug fields** — for a bug, concrete repro steps are essential: the triage worker attempts
  a bounded reproduction, and a bug it cannot reproduce is sent back for exact steps /
  environment / version. Give expected-vs-actual and where it was seen. If you can point at
  the offending code path, do.
- **Pointers / files** — cite real paths, PRs, branches, or commits you have verified exist
  in the repo. Never invent a path. Concrete pointers let triage ground its assessment in
  actual code instead of guessing.
- **Scope / non-goals** — if the work is large or cross-cutting, say so and suggest how it
  could be broken up; triage flags oversized change sets for splitting. State explicit
  non-goals to keep the change scoped.
- **Dependencies** — link related items / prerequisite work so triage and implementation
  see the ordering.

## What NOT to do

- Don't force EARS/SHALL syntax on the human-facing criteria — keep those plain; EARS is your
  internal check only.
- Don't prescribe the implementation (services, class names, file layout) in acceptance
  criteria — that's HOW, not WHAT.
- Don't pad with "nice to have someday" items; keep the item to one coherent change.
- Don't assert behavior about the codebase you haven't verified — if unsure, say what is
  unknown so triage can flag it rather than trusting a fabricated claim.
- Don't add priority/SLA language — SLAs are a review-phase concern, not intake.
