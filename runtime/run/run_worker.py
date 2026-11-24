#!/usr/bin/env python3
"""Run one factory worker once, the same way on every host.

    python3 <bundle>/scripts/runtime/run_worker.py <worker> [--item <id>] [--dry-run]

This is the only entry point a host schedules. It reads the bundle, takes the worker's single-flight
lease, runs the worker, writes a run record, and releases the lease. `--item` starts a run for one
board item (a person's "run now" on it): an agent is told to act on that item only, a script gets
FACTORY_ITEM, and the record carries `item`.

- An agent worker runs one headless turn of the configured agent harness (`integrations.harness`,
  kiro-cli by default) on a short instruction that points at the worker's rendered prompt. The
  harness module in scripts/integrations/harness/ installs its rendered config, builds the argv and
  reads the run's stdout back into the record's trace and credits.
- A script worker runs its entry file: a `.py` with the host's Python, anything else as an
  executable (`lib/runner.py` maps a Python script's signals to an exit code).

Host-specific storage (run records and leases) comes from the host module the renderer copies next
to this file as `history.py`: `write_record(record)`, `acquire_lease(worker, ttl_secs, run_id)`,
`release_lease(worker, run_id)`. Paths come from the bundle's `host.json`.

The environment each run gets:

    FACTORY_BUNDLE           the bundle root
    FACTORY_CONFIG           the bundle's factory-config.json
    FACTORY_RUN_ID           <instance>:<worker>:<started_at>, stamped on every post
    FACTORY_STATE_DIR        writable, kept between runs of this worker where the host can
    FACTORY_WORKTREE_ROOT    where the worker creates git worktrees
    FACTORY_ITEM             the board item this run was started for, only with --item

and the runner's own environment: all of it, or, when the host profile has `env`, only BASE_ENV, the
locale's LC_*, every FACTORY_*, the variables the harness module lists in its ENV and the variables
`env` names.

Stdlib only.

Exit status, which a host reads to decide whether it must record the run itself:

    0          the worker succeeded; its record is written
    1          the worker failed or timed out; its record is written
    LEASE_HELD another run holds the worker's lease, so nothing ran and nothing is recorded
    NO_RECORD  no record could be written (the bundle, the host module or the record store failed)
    other      a usage error (argparse exits 2, an unknown worker returns 2); no record
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
import traceback
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = Path(os.environ.get("FACTORY_BUNDLE") or HERE.parents[1])
sys.path[:0] = [str(HERE), str(BUNDLE / "scripts" / "lib")]

TAIL_CHARS = 4000
NO_RECORD = 3
LEASE_HELD = 4
KILL_GRACE_SECS = 10  # how long a timed-out worker gets to stop after each signal
# An item id goes into the agent's instruction, so it is one token: an id, a number or a URL.
ITEM_RE = re.compile(r"[\w.:/#@-]{1,300}")
# What a shell and the programs on PATH need to start. A host profile with `env` keeps these and the
# variables it names; a token is kept only by name.
BASE_ENV = frozenset({"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TMPDIR", "TZ"})
# A record is published (the report shows a failed run's last line, learn reads summaries), so its trace,
# summary and error are masked: the values of the secrets the Manual's integrations read (KNOWN_SECRET_ENV),
# the contents of integration files under a `*File` key, the token shapes a run commonly holds, and the
# password in a URL. This guards against an accidental print of the factory's own secrets; anything else a
# run prints is the run's to keep out of its output.
KNOWN_SECRET_ENV = ("KIRO_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|ksk_[A-Za-z0-9_-]{16,}"
                      r"|(?:AKIA|ASIA)[0-9A-Z]{16})"
                      r"|(?<=://)[^/\s:@\"]*:[^/\s@\"]+(?=@)")
MIN_SECRET_CHARS = 8  # shorter values are flags and names, and masking them would garble the trace
MAX_SECRET_FILE_BYTES = 64 * 1024


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(name: str) -> dict:
    return json.loads((BUNDLE / name).read_text())


def instruction(prompt_path: Path, item: str | None = None) -> str:
    focus = (f" This run was started for {item}: act on that item only, and stop if this step has nothing to do "
             "on it.") if item else ""
    return (
        f"Read {prompt_path} in full and follow it exactly. It is this run's complete procedure. "
        "If the file is missing or unreadable, stop and do nothing." + focus
    )


def command(name: str, worker: dict, host: dict, harness_cfg: dict | None = None, item: str | None = None) -> list[str]:
    """The argv this worker runs. Pure, so it can be tested without running anything."""
    if worker["implementation"] == "agent":
        cfg = harness_cfg or _harness_cfg()
        return harness(cfg).command(cfg, bundle=BUNDLE, worker=name,
                                    instruction=instruction(BUNDLE / worker["prompt"], item), model=worker.get("model"))
    path = BUNDLE / "scripts" / worker["entry"]
    if path.suffix == ".py":
        return [host.get("paths", {}).get("python") or sys.executable, str(path)]
    return [str(path)]


def _harness_cfg() -> dict:
    return _read("factory-config.json")["integrations"]["harness"]


def harness(cfg: dict):
    """The configured harness module, copied into the bundle by the renderer."""
    path = BUNDLE / "scripts" / "integrations" / "harness" / f"{cfg['use'].replace('-', '_')}.py"
    spec = importlib.util.spec_from_file_location(f"harness_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tail(text: str, secrets: tuple[str, ...] = ()) -> str:
    """The end of `text`, masked first so a secret cut at the boundary leaves no fragment."""
    text = mask(text or "", secrets)
    return text if len(text) <= TAIL_CHARS else "..." + text[-TAIL_CHARS:]


def mask(text: str, secrets: tuple[str, ...]) -> str:
    """`text` with each secret masked as written, JSON-escaped and URL-encoded, and every token shape."""
    forms = {f for secret in secrets
             for f in (secret, json.dumps(secret)[1:-1], secret.replace("/", "\\/"), urllib.parse.quote(secret, safe=""))}
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, "***")
    return TOKEN_RE.sub("***", text)


def _secret_file(path: str) -> str:
    """A secret file's text, or "" for anything but a readable regular file (a FIFO would block)."""
    try:
        p = Path(os.path.expanduser(path))
        if not p.is_file():
            return ""
        with p.open("rb") as f:
            return f.read(MAX_SECRET_FILE_BYTES).decode(errors="replace").strip()
    except (OSError, ValueError):  # unreadable, or a path with a NUL in it
        return ""


def run_secrets(env: dict, cfg: dict) -> tuple[str, ...]:
    """The values a record must not carry (the comment on KNOWN_SECRET_ENV)."""
    values = [env.get(name, "") for name in KNOWN_SECRET_ENV]

    def files(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and k.endswith("File"):
                    yield v
                else:
                    yield from files(v)
        elif isinstance(node, list):
            for v in node:
                yield from files(v)

    values += [_secret_file(path) for path in files(cfg.get("integrations", {}))]
    return tuple(v.strip() for v in values if len(v.strip()) >= MIN_SECRET_CHARS)


def _run(argv: list[str], timeout: int, env: dict, cwd: Path) -> tuple[int | None, str, str]:
    """(exit status, stdout, stderr) of the worker; the status is None when it timed out.

    The worker runs in its own session, and a timeout stops the whole session: an agent starts test
    suites and pushes of its own, and one left running after the lease is released would work on
    an item the next run may claim."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                            cwd=str(cwd), start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        pass
    for sig, grace in ((signal.SIGTERM, KILL_GRACE_SECS), (signal.SIGKILL, KILL_GRACE_SECS)):
        try:
            os.killpg(proc.pid, sig)
        except OSError:  # the group is gone, or (macOS) holds only a process already dead
            break
        try:
            out, err = proc.communicate(timeout=grace)
            return None, out, err
        except subprocess.TimeoutExpired:
            continue
    # A process that left the session still holds the pipes: stop waiting, without its output.
    proc.kill()
    return None, "", ""


def _output(worker: dict, hcfg: dict, stdout: str, secrets: tuple[str, ...] = ()) -> dict:
    """The record fields a run's stdout gives: trace, summary and, when the harness reports it,
    credits. An agent's stdout is the harness's own format, read by the harness."""
    text, credits = stdout, None
    if worker["implementation"] == "agent":
        out = harness(hcfg).result(hcfg, stdout)
        text, credits = out.get("text") or "", out.get("credits")
    text = mask(text, secrets)
    fields = {"trace": _tail(text), "summary": text.strip().splitlines()[-1] if text.strip() else ""}
    if credits is not None:
        fields["credits"] = credits
    return fields


class CannotRun(RuntimeError):
    """The host is not set up for this worker; the message says what to fix."""


def workdir(worker: dict, paths: dict) -> tuple[Path, str | None]:
    """(directory the worker runs in, why it cannot run). An agent works in the host's checkout when the
    host names one, and a checkout that is named but missing stops the run rather than letting the
    agent work in the bundle; anything else runs in the bundle. An agent's config names its skills
    under the host profile's `paths.bundle`, so a bundle anywhere else gives the agent none."""
    if worker["implementation"] != "agent":
        return BUNDLE, None
    home = paths.get("bundle")
    if home and Path(os.path.expanduser(home)).resolve() != BUNDLE.resolve():
        return BUNDLE, (f"this bundle is at {BUNDLE.resolve()} but was rendered to run from {home} "
                        "(host.json paths.bundle): install it there, or render with that path")
    if not paths.get("checkout"):
        return BUNDLE, None
    checkout = Path(os.path.expanduser(paths["checkout"]))
    if not checkout.is_dir():
        return checkout, f"the checkout {checkout} (host.json paths.checkout) does not exist: clone the repo there"
    return checkout, None


def inherited_env(host: dict, hcfg: dict) -> dict:
    """What a run keeps of the runner's environment (the module docstring). The runner and the host
    module read FACTORY_* and the harness reads its ENV here, so a run keeps those whatever `env` says."""
    keep = host.get("env")
    if keep is None:
        return dict(os.environ)
    keep = set(keep) | set(getattr(harness(hcfg), "ENV", ()))
    return {k: v for k, v in os.environ.items()
            if k in BASE_ENV or k.startswith(("LC_", "FACTORY_")) or k in keep}


def run(name: str, dry_run: bool = False, item: str | None = None) -> int:
    workers = _read("workers.json")
    if name not in workers:
        print(f"no worker {name!r} in this bundle; have {sorted(workers)}", file=sys.stderr)
        return 2
    worker, host = workers[name], _read("host.json")
    cfg = _read("factory-config.json")
    paths = host.get("paths", {})
    started = _now()
    run_id = f"{cfg['instance']['name']}:{name}:{_iso(started)}"
    hcfg = cfg["integrations"]["harness"]
    argv = command(name, worker, host, hcfg, item)
    timeout = int(worker["runTimeoutSecs"])
    cwd, problem = workdir(worker, paths)
    if dry_run:
        out = {"worker": name, "runId": run_id, "timeoutSecs": timeout, "cwd": str(cwd), "argv": argv}
        print(json.dumps({**out, "cannotRun": problem} if problem else out, indent=2))
        return 0

    import history  # the host module, copied beside this file by the renderer

    record = {"worker": name, "runId": run_id, "implementation": worker["implementation"], "start": _iso(started)}
    if item:
        record["item"] = item
    leased, code = False, 1
    secrets: tuple[str, ...] = ()
    t0 = time.monotonic()
    try:
        leased = history.acquire_lease(name, worker["claimTtlSecs"], run_id)
        if not leased:
            print(f"{name}: another run holds the lease; skipping")
            return LEASE_HELD
        # From the runner's whole environment, which holds every value a run's can.
        secrets = run_secrets(dict(os.environ), cfg)
        env = inherited_env(host, hcfg)
        state = Path(os.path.expanduser(paths["stateDir"])) / name
        worktrees = Path(os.path.expanduser(paths["worktreeRoot"]))
        state.mkdir(parents=True, exist_ok=True)
        worktrees.mkdir(parents=True, exist_ok=True)
        env.update({
            "FACTORY_BUNDLE": str(BUNDLE),
            "FACTORY_CONFIG": str(BUNDLE / "factory-config.json"),
            "FACTORY_RUN_ID": run_id,
            "FACTORY_STATE_DIR": str(state.parent),
            "FACTORY_WORKTREE_ROOT": str(worktrees),
            "TZ": worker.get("timezone") or env.get("TZ", "UTC"),
        })
        if item:
            env["FACTORY_ITEM"] = item
        if problem:
            raise CannotRun(problem)
        if worker["implementation"] == "agent":
            harness(hcfg).install(hcfg, BUNDLE, name)
        returncode, out, err = _run(argv, timeout, env, cwd)
        if returncode is None:
            record.update(_output(worker, hcfg, out, secrets), status="timed out",
                          summary=f"timed out after {timeout}s", error=_tail(err, secrets))
            code = 124
        else:
            # A worker's stderr is only an error when the worker failed: a harness may log there on
            # every run, and readers count any non-empty error as a failure.
            record.update(status="succeeded" if returncode == 0 else "failed", exitCode=returncode,
                          error=_tail(err, secrets) if returncode else "", **_output(worker, hcfg, out, secrets))
            code = returncode
    except CannotRun as exc:
        record.update(status="failed", summary=mask(str(exc), secrets), error=mask(str(exc), secrets))
        code = 1
    except Exception:  # noqa: BLE001 - recorded as a failed run, not lost
        record.update(status="failed", summary="the runner failed before the worker finished",
                      error=_tail(traceback.format_exc(), secrets))
        code = 1
    finally:
        # Every run that got past the lease, or failed taking it, gets exactly one record.
        if "status" in record:
            record.update(end=_iso(_now()), duration_ms=int((time.monotonic() - t0) * 1000))
            try:
                history.write_record(record)
            finally:
                if leased:
                    history.release_lease(name, run_id)
            print(f"{name}: {record['status']} ({record['duration_ms']} ms)")
    return 0 if code == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run one factory worker once.")
    ap.add_argument("worker")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the command and where it would run, and run nothing (a script worker's own "
                         "`dry` argument is its no-writes mode)")
    ap.add_argument("--item", help="start the run for this one board item")
    args = ap.parse_args(argv)
    if args.item and not ITEM_RE.fullmatch(args.item):
        ap.error(f"--item {args.item!r} is not an item id: letters, digits and . : / # @ - only")
    try:
        return run(args.worker, args.dry_run, args.item)
    except Exception:  # noqa: BLE001 - see NO_RECORD
        traceback.print_exc()
        return NO_RECORD


if __name__ == "__main__":
    sys.exit(main())
