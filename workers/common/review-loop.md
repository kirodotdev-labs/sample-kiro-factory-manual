# Review loop: decisions of record and convergence

A step that answers review on its own PR runs this before changing anything for review. Read every
comment on the item and the PR as one timeline (`common/human-input.md`).

**Decisions of record.** When a human rules on a contested approach, or this job settles one itself
(below), this job records the ruling as its own board comment: what was ruled, what it forbids or
requires from here on, who decided it and where, and the pass it was made at. These comments are
append-only: a revision is a new comment naming what it supersedes, never an edit. The newest one
nothing supersedes is the plan of record, and it stands until a human revisits it.

**Rounds in the current episode** = this job's pass count (on its status comment) minus the pass of
the latest decision of record (or the pass count itself when there is none).

**Sort each new finding against the plan of record:**
- *Restates what the plan of record settled, with no new evidence*: not actionable. Reply naming the
  decision and its pass, resolve the thread, and do not count it as churn.
- *Names a consequence the decision did not account for* (a reachable failure, a failing test, a new
  constraint): do not patch it and do not overturn the decision. Escalate it for re-litigation,
  quoting the decision, the new evidence and what the decision missed. The bar is new evidence, not
  renewed disagreement.
- *Adjacent or unrelated*: a real finding. Fix it, saying in the reply how it sits alongside any
  decision it touches.

**At 4 or more rounds, judge convergence** (below 4, just do the work: a narrow follow-up finding
on a fresh revision is normal and gets fixed) from the thread history and commit list before fixing
anything. Converging: findings are independent, in different areas, each fixed once and stayed
fixed. Not converging: you can point to threads showing fix-induced findings, one concern re-raised,
findings piling into one area, drift off the item's scope, or an unanswered human question under the
automated rounds. When not converging, settle the choice behind it if it can be undone later by an
ordinary change: take the option you would recommend, or the narrowest one that still meets the
item's done-when, record it as a decision of record, state it and the alternative at the top of the
PR body, and carry on to the step's human gate. Escalate instead when the choice is hard to undo
(`common/risk.md`, Reversibility), or when the loop still does not converge after a decision this
job made.

**Escalation.** Every escalation is a PR comment (an item comment when there is no PR yet) naming the
cause, the evidence, the decision needed and the option you recommend, with whether it can be undone;
a reply on each thread left open; and the status comment's `Status:` line reading `escalated`, with
the comment naming who and what. While that line reads `escalated` with no human reply since, later passes post no new escalation comment, push nothing
against the contested seam, and do not mark the PR ready. They may still fix CI and answer new human
comments. A later escalation (a new episode or a different cause) is a new fact that escalates again.

**Closing an episode.** A human reply to an open escalation ends it. Move the `Status:` line off `escalated`,
acknowledge it on the thread with your one-sentence reading and what you will change, record it as a
decision of record, and point to it from the status comment. If you cannot state it in one sentence,
ask one question naming the two readings and stay blocked. An escalation over a mechanical cause (a
check to re-run, a conflict) also ends when the cause clears: move the `Status:` line off `escalated` and say what cleared it.
