"""Local host storage: run records and leases in a directory on this machine.

The renderer copies this file to the bundle's scripts/runtime/history.py when the host's runtime is
`local`. Every host module provides the same functions:

    run_records(since=None, older=0) -> list[dict]
                                            the run records, normalised. With `since` (an aware
                                            datetime), only those started at or after it, plus each
                                            worker's newest `older` records from before it, so a
                                            worker that stopped running is still seen
    write_record(record) -> None            one JSON file per run
    acquire_lease(worker, ttl_secs, run_id) -> bool
    release_lease(worker, run_id) -> None

Records live at <stateDir>/runs/<worker>/<start>.json and leases at <stateDir>/leases/<worker>.json,
where stateDir is the host profile's `paths.stateDir` (FACTORY_STATE_DIR when set). Nothing here
deletes a record: prune old ones with the host's own tools (hosting-local.md).
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path


def _root() -> Path:
    env = os.environ.get("FACTORY_STATE_DIR")
    if env:
        return Path(env)
    bundle = Path(os.environ.get("FACTORY_BUNDLE") or Path(__file__).resolve().parents[2])
    state = (json.loads((bundle / "host.json").read_text()).get("paths") or {}).get("stateDir")
    if not state:
        raise RuntimeError(f"{bundle / 'host.json'} has no paths.stateDir; render the bundle again")
    return Path(os.path.expanduser(state))


def _normalise(worker: str, rec: dict) -> dict:
    return {
        "worker": rec.get("worker") or worker,
        "run_id": rec.get("runId") or rec.get("run_id") or "",
        "started_at": rec.get("start") or rec.get("started_at"),
        "finished_at": rec.get("end") or rec.get("finished_at"),
        "status": rec.get("status") or "",
        "implementation": rec.get("implementation") or "",
        "duration_ms": int(rec.get("duration_ms") or 0),
        "summary": rec.get("summary") or "",
        "trace": rec.get("trace") or "",
        "item": rec.get("item") or "",
        "error": rec.get("error") or "",
        "credits": None if rec.get("credits") is None else float(rec["credits"]),
    }


def _file_name(start: str) -> str:
    return start.replace(":", "-") + ".json"


def run_records(since: dt.datetime | None = None, older: int = 0) -> list[dict]:
    # A record's file is named after its start time, so an older one is skipped without being read.
    floor = _file_name(since.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")) if since else ""
    out = []
    base = _root() / "runs"
    for worker in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
        paths = sorted(worker.glob("*.json"))
        before = [p for p in paths if p.name < floor]
        for path in (before[-older:] if older else []) + paths[len(before):]:
            try:
                out.append(_normalise(worker.name, json.loads(path.read_text())))
            except (OSError, ValueError):
                continue
    return out


def write_record(record: dict) -> None:
    d = _root() / "runs" / record["worker"]
    d.mkdir(parents=True, exist_ok=True)
    name = _file_name(record["start"])
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, d / name)


def _lease_path(worker: str) -> Path:
    d = _root() / "leases"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{worker}.json"


def acquire_lease(worker: str, ttl_secs: int, run_id: str) -> bool:
    """Take the worker's lease when nobody holds it or the holder's has expired.

    The check and the write happen under an exclusive lock on the lease file, so two runs that find
    the same expired lease cannot both take it."""
    path = _lease_path(worker)
    now = dt.datetime.now(dt.timezone.utc)
    with open(path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        try:
            held = dt.datetime.fromisoformat(json.loads(fh.read())["expires"]) > now
        except (ValueError, KeyError, TypeError):
            held = False
        if held:
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"runId": run_id, "expires": (now + dt.timedelta(seconds=ttl_secs)).isoformat()}))
        return True


def release_lease(worker: str, run_id: str) -> None:
    """Give the lease up when this run still holds it. The file stays, emptied, so every run locks
    the same one."""
    with open(_lease_path(worker), "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        try:
            mine = json.loads(fh.read()).get("runId") == run_id
        except (ValueError, AttributeError):
            mine = False
        if mine:
            fh.seek(0)
            fh.truncate()
