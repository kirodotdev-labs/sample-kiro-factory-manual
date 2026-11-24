# Claim protocol

Claiming an item is a first-class, uniform step so parallel or duplicate runs do not pick up the
same item, and so an abandoned claim (a crash, a reap, a timeout) can be recovered. Every agent
worker that takes an item follows this. The `janitor` worker is the sole recoverer of abandoned
claims.

## The claim signal: a single claim label

An item being actively worked carries the label `{{label.claim}}`. It is ADDED the moment a worker
picks the item, before any real work, and REMOVED when the worker reaches a terminal state for its
stage (success or failure), so the label is present only for the active window.

Claiming is a label-only write. Do NOT post a "started" or "claiming" comment: a run is silent until
it has something to report. The claim's clock is the item's own last-updated time (adding the label
IS an update, so it stamps the claim time), and the stage is inferred from the item's labels.

## Claim (at pick time, before any real work)

1. `add_labels {{label.claim}}`, and nothing else. **Claiming does not move the item.** A worker
   leaves the column where it found it, so the column keeps meaning "where this item is in the
   cycle" rather than "who is touching it right now". Only a terminal moves an item.
2. **Optimistic re-check** (mitigates the read→claim race): immediately re-read the item (`board.get`)
   and confirm it is still yours to work: the claim label is present, and no terminal comment for
   this stage arrived between your read and your write. If one did, `remove_labels {{label.claim}}`
   and pick a different item or stop.

   This is advisory, not mutual exclusion: two runs that add the label in the same instant are
   indistinguishable and both proceed. What protects you is that queue reads exclude the claim label,
   workers are staggered on the schedule so a stage does not overlap itself, and runs are minutes
   long against a much longer cadence. Claim promptly to shrink the residual window.

## Complete (terminal state for the stage)

When the worker finishes the item's stage, in the SAME write: `remove_labels {{label.claim}}`, and
add or keep the stage's terminal labels and any `move` it performs. The claim label is present only
for the active window, never on a settled item.

## Queue reads exclude the claim label

Every worker's selection query excludes items carrying `{{label.claim}}`. An item another run is
actively holding is not eligible to be picked.

## Recovery is the janitor's job

Per-worker timeout recovery is not baked into workers: the `janitor` worker is the single owner of
un-claiming abandoned items, by each step's TTL. This keeps recovery in one auditable place.

### Deriving stage and claim age

Because claiming writes no comment, a recoverer derives both from the item itself.

**Claim age** = now − the item's last-updated time. Adding the claim label stamps that field, and a
healthy run does not touch the item again until it finishes, so for a still-claimed item the field
is the claim time. Any other write in the window (a human comment, another worker's edit) resets it
and makes the claim look younger, which delays recovery rather than yanking live work: it fails in
the safe direction.

**Stage** is inferred from the item's labels, not its column (workers select on labels and write the
column only for human readers):
- `{{label.fastTrack}}` → **implement** (also covers a review-phase worker's claim).
- `{{label.spec}}` without `{{label.fastTrack}}` → **design**.
- not `{{label.groomed}}` → **triage** (untriaged; triage is the only worker that takes it).
- anything else → treat as **implement**.

### TTL

A claim is abandoned once it is older than its stage's worker's timeout plus 5 minutes. The runner
kills a run at its timeout, so no live run can hold a claim past that. Your claim TTL is
`{{worker.claimTtlMinutes}}` minutes.

## Scaling one role past one instance

The optimistic re-check is advisory. If a role is scaled to N concurrent instances, prefer
partitioning over tighter locking: instance `i` of `N` picks only items where `hash(itemId) % N ==
i`, so instances never contend. Partitioning composes with the janitor, which is still needed for
crash recovery.
