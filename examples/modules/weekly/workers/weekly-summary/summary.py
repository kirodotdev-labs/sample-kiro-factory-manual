#!/usr/bin/env python3
"""Example module worker: how many board items opened and closed in the last seven days.

Inputs: the board's open items, and the items closed in the window.
Outputs: one line and a short list on stdout, which the run record keeps. It writes nothing.
Verification: none.
Decisions: none.

Run: python3 scripts/workers/weekly-summary/summary.py
"""

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
import runner  # noqa: E402

runner.bootstrap()

import factory_config  # noqa: E402
from runner import Fail, Report  # noqa: E402

DAYS = 7


def summary(open_items: list[dict], closed: list[dict], since: str, limit: int) -> str:
    opened = [i for i in open_items + closed if (i.get("created") or "") >= since]
    lines = [f"- {i.get('title') or ''} ({i.get('url') or ''})" for i in opened[:limit]]
    return "\n".join([f"Last {DAYS} days: {len(opened)} opened, {len(closed)} closed."] + lines)


def run(ctx):
    cfg = factory_config.load()
    limit = int(factory_config.worker_setting("weekly-summary", "maxItems", 20))
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    board = factory_config.integration_client(cfg, "board")
    try:
        open_items = board.list(status="Open")
        closed = [i for i in board.list(status="Closed", updated_after=since) if (i.get("closed") or "") >= since]
    except Exception as exc:  # noqa: BLE001 - nothing to count without the board
        raise Fail(f"Weekly summary NOT RUN: board read failed: {type(exc).__name__}: {exc}")
    raise Report(summary(open_items, closed, since, limit))


if __name__ == "__main__":
    sys.exit(runner.main(run))
