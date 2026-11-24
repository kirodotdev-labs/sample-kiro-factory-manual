#!/usr/bin/env python3
"""Learn's input: the factory's run records for a window, as JSON.

    python3 <bundle>/scripts/workers/learn/run_records.py [days]

Prints one JSON object: `window_days`, then `workers`, a per-worker summary (runs, by status,
minutes, credits), then `runs`, one entry per run in the window (worker, start, status, minutes,
credits, summary). Traces are left out: they are long, and the summary says what the run did. Reads
the host's records through its `history` module and writes nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
import runner  # noqa: E402

runner.bootstrap()
import factory_config  # noqa: E402


def _ts(v):
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def window(records: list[dict], days: int, now: dt.datetime) -> dict:
    since = now - dt.timedelta(days=days)
    runs, workers = [], {}
    for r in records:
        start = _ts(r.get("started_at"))
        if start is None or start < since:
            continue
        minutes = round((r.get("duration_ms") or 0) / 60000, 1)
        runs.append({"worker": r["worker"], "start": start.isoformat(), "status": r.get("status", ""),
                     "minutes": minutes, "credits": r.get("credits", 0),
                     "summary": (r.get("summary") or r.get("error") or "")[:300]})
        w = workers.setdefault(r["worker"], {"runs": 0, "status": {}, "minutes": 0.0, "credits": 0.0})
        w["runs"] += 1
        w["status"][r.get("status", "")] = w["status"].get(r.get("status", ""), 0) + 1
        w["minutes"] = round(w["minutes"] + minutes, 1)
        w["credits"] = round(w["credits"] + (r.get("credits") or 0), 2)
    runs.sort(key=lambda r: r["start"])
    return {"window_days": days, "workers": workers, "runs": runs}


def main(argv: list[str]) -> int:
    days = int(argv[0]) if argv and argv[0].isdigit() else 7
    history = factory_config.load_host_module("history")
    now = dt.datetime.now(dt.timezone.utc)
    print(json.dumps(window(history.run_records(since=now - dt.timedelta(days=days)), days, now), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
