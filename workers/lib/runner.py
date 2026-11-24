"""Run a factory script worker as a plain process.

A script worker's entry function takes one argument, `ctx`, whose `message` is the script's first
command-line argument: the word `dry` asks a script to report what it would do and write nothing.

    python3 scripts/workers/janitor/janitor.py [dry]      # -> runner.main(reap)

The entry ends its run by returning or by raising one of the signals below, which `main` maps to
what the run prints and its exit status:

    return, Skip   -> exit 0, nothing printed
    Report         -> exit 0, the message on stdout
    Fail           -> exit 1, the message on stderr as one line
    anything else  -> exit 1 with the traceback

`bootstrap()` puts the bundle's script directories on `sys.path`, so a script imports
`factory_config`, the host's `history` module and an integration client by bare name whichever
directory it is in.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _scripts_root() -> Path | None:
    """The bundle's scripts/ directory, found by walking up from this file.

    In a rendered bundle this file is at scripts/lib/runner.py, so scripts/ is its parent's parent.
    FACTORY_BUNDLE/scripts wins when the env names a bundle (the host sets it).
    """
    env = os.environ.get("FACTORY_BUNDLE")
    if env:
        cand = Path(env) / "scripts"
        if cand.is_dir():
            return cand
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "scripts":
            return parent
        if (parent / "scripts").is_dir() and (parent / "scripts" / "lib").is_dir():
            return parent / "scripts"
    # Source tree (not a bundle): nothing to bootstrap; the integration clients live elsewhere.
    return None


def bootstrap() -> None:
    """Put the bundle's script dirs on sys.path so scripts import by bare name.

    Adds scripts/lib, scripts/runtime, and every scripts/integrations/<kind>. Idempotent.
    """
    root = _scripts_root()
    if root is None:
        return
    candidates = [root / "lib", root / "runtime"]
    integrations = root / "integrations"
    if integrations.is_dir():
        candidates += [p for p in integrations.iterdir() if p.is_dir()]
    for p in candidates + [root]:
        s = str(p)
        if p.is_dir() and s not in sys.path:
            sys.path.insert(0, s)


# Control-flow signals a script raises to end its run.
class Skip(Exception):
    """Nothing to do this run: it prints nothing and exits 0."""


class Fail(Exception):
    """The run failed for the reason the message gives: printed as one line, exit 1, no traceback."""
    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


class Report(Exception):
    """The run did its work: the message says what, on stdout, and the exit is 0."""
    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


class ProcessCtx:
    """What an entry function is given: `message`, the script's first argument."""

    def __init__(self, message: str = ""):
        self.message = message


def main(entry) -> int:
    """Run `entry(ctx)` as a process and translate its control-flow signal to an exit code.

    Returns the exit code; the caller is expected to `sys.exit` it.
    """
    bootstrap()
    ctx = ProcessCtx(message=sys.argv[1] if len(sys.argv) > 1 else "")
    try:
        entry(ctx)
    except Skip:
        return 0
    except Fail as sig:
        print(sig.message, file=sys.stderr)
        return 1
    except Report as sig:
        if sig.message:
            print(sig.message)
        return 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    return 0
