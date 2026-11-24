---
name: implementation-walkthrough
description: Optional, for a large or complex change. Builds a Visual Walkthrough tab on the implementation verifiable output, an outline and before/after inspector leading to exact code diffs. Use when the scope readings show a change too large to explain in one section.
---

# Implementation walkthrough

The representation must be more understandable, and almost always much smaller, than the
implementation itself. Represent the change, not a point-in-time model of the system or a review of
its merits. Include unchanged structure only where it explains the transition, including unchanged
code that the changed data or events now pass through, and mark it unchanged.

## Build from the diff upward

Group related hunks into meaningful changes, then compose those groups until the top level explains
how the overall goal is achieved. Name nodes for changes, not directories. Files can span concepts;
shared code may appear under several nodes. Every hunk must be reachable through a node. Do not
append a full diff or a per-file inventory; readers who want the whole diff read it on the PR. When
regenerating for a new head, note mechanisms the previous version described that the new head
removed.

Let source changes drive the hierarchy. Attach tests, snapshot updates and mechanical boilerplate to
the changes they support rather than giving them their own nodes: higher-level tests to the behavior
they exercise, unit tests to the interface or diff they cover. Attach review threads to the nodes they
concern.

A useful shape is overall change, core conceptual changes, interfaces and calls, exact diffs: usually
two to five levels. Collapse levels that say nothing new.

Every explanatory node shows before, after and a short description of what changed at that level.
Higher levels draw the transition as a visual model suited to the change, such as a flow, a state
diagram or an ownership map; prose in two boxes is not a visual model. At most two sentences per node,
plus a short excerpt where code says it better. At the top level, give each view the change alters its
own before/after (ownership, data and events, calls, or the user's path) and skip the rest. Where it
helps, walk a concrete example through a flow in user-facing terms.

Immediately above the diffs, show the relevant interfaces, types, signatures and calls before and
after, without bodies. Use actual symbol names, tell calls from imports, and link each to its hunks;
mark dispatch you cannot resolve instead of guessing. Include events and callbacks that cross an
execution boundary. Prefer analysis to hand-drawn edges where the instance's signals provide it, and
keep its findings distinct from your interpretation.

Check each behavior rule you state against every entry path that reaches the changed code. The code's
own comments are not evidence that every path follows the rule.

## Present an outline and inspector

The page gets two tabs: Report, the verifiable output as before, and Visual Walkthrough. The report's
walkthrough section keeps one sentence on the overall change and a link to the tab. Keep the outline
visible, and show the selected node's before, after and description in the inspector, with its diffs
open there rather than inside collapsed sections; collapse only long secondary attachments such as
tests. Build the tabs, outline and inspector with HTML and CSS only: the page carries no scripts.
