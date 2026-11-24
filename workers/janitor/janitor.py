"""Factory janitor: deterministic TTL reaps.

A script worker, so no model runs and it costs nothing. It owns the factory's two board-only
recoveries:

  A. Recover abandoned claims past the claiming step's TTL, so a crashed or killed run's item
     re-enters the queue.
  B. Auto-close needs-info items that triage asked about longer ago than the age limit.

Why a script. Every step is a label filter, a timestamp comparison, a lookup table or a fixed
template, with no judgement anywhere, so a model adds cost and the risk of acting on a misread
board. A script also keeps running when the model is unavailable.

Safety. Writes are destructive (label removal, item closure), so both reaps are capped per run,
the argument `dry` reports what it would do and writes nothing, and an item is only ever acted on
while its label is still present, which makes a double run a no-op.

Scope. This is advisory recovery, not a lock manager: it never resolves two live claimants racing
for one item. That is handled at claim time by the workers' own optimistic re-check (the claim
convention). All this job decides is whether a claim has been held long enough to be considered
dead.

Inputs: open items with the claim label and their last-updated time; needs-info items and triage's
newest post on each (the needs-info clock); each step's claim TTL and triage's needsInfoDays (bundle
workers.json). A failed board read fails the run; an item whose comments cannot be read is left
alone and fails the run after the rest is done.
Outputs: claim labels removed and needs-info items closed, each with a comment; one line per action.
Verification: none beyond the TTL and window checks.
Decisions: reap or leave; never asks a human. Anything it cannot decide mechanically it leaves alone.

Run: python3 scripts/workers/janitor/janitor.py [dry]
"""

from __future__ import annotations

import datetime as dt
import os
import sys

# Put the bundle's script dirs on sys.path (scripts/lib, scripts/runtime, scripts/integrations/*).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
import runner  # noqa: E402

runner.bootstrap()

import factory_config  # noqa: E402
import records  # noqa: E402
from runner import Fail, Report, Skip  # noqa: E402

CAP_PER_REAP = 25


# ------------------------------------------------------------------- utilities

def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_ts(v):
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _human_age(minutes: float) -> str:
    if minutes < 90:
        return f"{minutes:.0f} minutes"
    if minutes < 60 * 36:
        return f"{minutes / 60:.1f} hours"
    return f"{minutes / 1440:.1f} days"


class _Cfg:
    """One run's resolved config: labels, limits and the attribution header."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.claim = factory_config.label("claim")
        self.groomed = factory_config.label("groomed")
        self.fast_track = factory_config.label("fastTrack")
        self.spec = factory_config.label("spec")
        self.needs_info = factory_config.label("needsInfo")
        # Each step's claim TTL in minutes, which the renderer derives from the step's timeout, so the
        # janitor never reaps a claim a live run can still hold. A step that is not enabled takes
        # the longest TTL of the enabled ones.
        ttls = {step: factory_config.claim_ttl_minutes(step) for step in ("triage", "design", "implement")}
        longest = max((t for t in ttls.values() if t), default=35)
        self.ttl_min = {step: t or longest for step, t in ttls.items()}
        self.needs_info_days = int(factory_config.worker_setting("triage", "needsInfoDays", 5))
        self.header = factory_config.get(cfg, "instance.header")

    def post(self, body: str, run_key: str) -> str:
        return records.compose(body, run_key, self.header)

    def stage(self, item: dict) -> str:
        """Which step holds this claim, from the item's labels (`common/claim.md`): claiming posts no
        comment, and workers select on labels and write the column only for human readers."""
        labels = set(item.get("labels") or [])
        if self.fast_track in labels:
            return "implement"
        if self.spec in labels:
            return "design"
        if self.groomed not in labels:
            return "triage"       # untriaged: triage is the only step that takes it
        return "implement"        # groomed only: awaiting a person, so the longest wait is the safe one


# ------------------------------------------------------------ data acquisition

def _comments(board, item: dict) -> list[dict]:
    """Comments for one item, preferring the inline list from the board read and falling back to a
    direct fetch so a truncated inline list cannot hold an item open forever."""
    return item.get("comments") or board.get(item["id"], comment_limit=50).get("comments") or []


# --------------------------------------------------------------- derived state

def stale_claims(rc: _Cfg, items: list[dict]) -> list[dict]:
    """Claimed items whose age exceeds their stage TTL, oldest first.

    Age comes from the item's last-updated time: adding the claim stamps it, and a healthy run does
    not touch the item again until it finishes. Any other write resets it and makes the claim look
    younger, which delays recovery rather than yanking live work -- the safe direction."""
    now = _now()
    out = []
    for t in items:
        if rc.claim not in (t.get("labels") or []):
            continue
        updated = _parse_ts(t.get("updated"))
        if updated is None:
            continue              # no clock, cannot judge: leave alone
        age_min = (now - updated).total_seconds() / 60.0
        stage = rc.stage(t)
        ttl = rc.ttl_min[stage]
        if age_min >= ttl:
            out.append({"item": t, "stage": stage, "age_min": age_min, "ttl": ttl})
    out.sort(key=lambda r: -r["age_min"])
    return out


def asked_at(rc: _Cfg, comments: list[dict]):
    """When the needs-info clock started: triage's newest post on the item. A factory post whose run
    id names no worker counts when no post names triage, because a run started without a run id
    posts `unknown` (`common/writing.md`). None when neither is on the item."""
    triage, unnamed = None, None
    for c in comments:
        body = c.get("body") or ""
        if not body.lstrip().startswith(rc.header) or c.get("trusted") is False:
            continue
        when = _parse_ts(c.get("created") or c.get("updated"))
        if not when:
            continue
        worker = records.worker_of(records.run_of(body))
        if worker == "triage":
            triage = max(triage or when, when)
        elif worker is None:
            unnamed = max(unnamed or when, when)
    return triage or unnamed


def stale_needs_info(rc: _Cfg, board, items: list[dict]) -> tuple[list[dict], list[str]]:
    """(needs-info items triage asked about longer ago than the age limit, oldest first; items whose
    comments could not be read, each as "<id>: <error>"). An item with the label but no post that
    starts the clock is left alone: there is no basis to close it."""
    now = _now()
    due, unread = [], []
    for t in items:
        if rc.needs_info not in (t.get("labels") or []):
            continue
        try:
            since = asked_at(rc, _comments(board, t))
        except Exception as exc:  # noqa: BLE001 - one unreadable item must not stop the others
            unread.append(f"{t.get('id')}: {type(exc).__name__}: {exc}")
            continue
        if since is None:
            continue
        age_days = (now - since).total_seconds() / 86400.0
        if age_days >= rc.needs_info_days:
            due.append({"item": t, "age_days": age_days, "since": since})
    due.sort(key=lambda r: r["since"])
    return due, unread


# ------------------------------------------------------------------- the reaps

def unclaim(rc: _Cfg, board, row: dict, run_key: str, dry: bool) -> str:
    t = row["item"]
    body = rc.post(
        "**Janitor: reclaimed an abandoned claim**\n"
        f"The {row['stage']} step's claim (the step is inferred from the item's labels) showed no activity for "
        f"{_human_age(row['age_min'])} (TTL {row['ttl']} minutes) and the run posted no completion, so "
        "it appears to have failed or been killed. Removed the claim label so the item re-enters the "
        f"{row['stage']} queue. The step reconciles its own branch or worktree when it "
        "picks the item up again.",
        run_key,
    )
    sid = t["id"]
    if dry:
        return f"WOULD unclaim {sid} stage={row['stage']} age={_human_age(row['age_min'])}"
    board.update(sid, remove_labels=[rc.claim], comment=body)
    return f"unclaimed {sid} stage={row['stage']} age={_human_age(row['age_min'])}"


def close_needs_info(rc: _Cfg, board, row: dict, run_key: str, dry: bool) -> str:
    t = row["item"]
    body = rc.post(
        "**Auto-closed: needs info not addressed**\n"
        f"Triage asked for more detail {row['age_days']:.0f} days ago and the needs-info label is "
        "still on this item. Closing to keep the board clear. To revive it: add the requested detail, "
        "remove the needs-info label or ask a maintainer to, and reopen it; it will be triaged again.",
        run_key,
    )
    sid = t["id"]
    if dry:
        return f"WOULD close {sid} waiting={row['age_days']:.1f}d"
    board.close(sid, body)
    return f"closed {sid} waiting={row['age_days']:.1f}d"


# ------------------------------------------------------------------ entrypoint

def reap(ctx):
    try:
        cfg = factory_config.load()
        rc = _Cfg(cfg)
    except factory_config.ConfigError as exc:
        raise Fail(f"Janitor NOT RUN: config error: {exc}")

    dry = (getattr(ctx, "message", "") or "").strip().lower().startswith("dry")
    run_key = records.run_id()
    board = factory_config.integration_client(cfg, "board")

    try:
        items = board.list(status="Open")
    except Exception as exc:  # noqa: BLE001 - a failed board read fails the run, in one line
        raise Fail(f"Janitor NOT RUN: board read failed: {type(exc).__name__}: {exc}")
    # Board-scope guard: a read far larger than the board usually holds means the scope filter
    # stopped applying, and acting on it would touch items that are not ours.
    max_items = int(factory_config.worker_setting("janitor", "maxBoardItems", 200))
    if max_items < 1:
        raise Fail(f"Janitor NOT RUN: workers.janitor.settings.maxBoardItems is {max_items}; it must be at least 1")
    if len(items) > max_items:
        raise Fail(
            f"Janitor ABORTED: board read returned {len(items)} open items, more than maxBoardItems "
            f"({max_items}), so the board scope may not have applied. No items were touched; raise "
            "workers.janitor.settings.maxBoardItems if the board really is that large."
        )

    # Claims first: they need no comment read, so an unreadable needs-info item cannot hold them.
    claims = stale_claims(rc, items)[:CAP_PER_REAP]
    actions = [unclaim(rc, board, r, run_key, dry) for r in claims]
    needs_info, unread = stale_needs_info(rc, board, items)
    needs_info = needs_info[:CAP_PER_REAP]
    actions += [close_needs_info(rc, board, r, run_key, dry) for r in needs_info]

    by_stage: dict = {}
    for r in claims:
        by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + 1
    parts = []
    if claims:
        shape = ", ".join(f"{n} {s}" for s, n in sorted(by_stage.items()))
        parts.append(f"reclaimed {len(claims)} abandoned claim(s) ({shape})")
    if needs_info:
        parts.append(f"auto-closed {len(needs_info)} stale needs-info")
    done = "; ".join(parts) or "nothing due"

    if unread:
        raise Fail(f"Janitor{' DRY RUN' if dry else ''}: {done}; could not read the comments of "
                   f"{len(unread)} needs-info item(s), left as they are: {'; '.join(unread[:5])}")
    if dry:
        detail = "\n".join(f"  {a}" for a in actions) or "  (nothing due)"
        raise Report(
            f"Janitor DRY RUN over {len(items)} open board items, nothing written.\n"
            f"stale claims due: {len(claims)}   stale needs-info due: {len(needs_info)}\n{detail}"
        )
    if not actions:
        raise Skip()              # idle: the common case, stay silent
    raise Report(f"Janitor: {done}.")


if __name__ == "__main__":
    sys.exit(runner.main(reap))
