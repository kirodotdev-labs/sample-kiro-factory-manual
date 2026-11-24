"""What scripts read from a factory post: the attribution header and the PR links. Stdlib only.

Every factory post opens with the attribution header (`common/writing.md`), whose `Run:` line names
the run; the run id names the worker. A post links the item's PRs with one `<Kind> PR: <URL>` line
each (`PR`, `Spec PR`, `Repro PR`, ...), which is also how a person links a PR on an item. Nothing
else in a post is read by a script; workers and people read the prose for meaning.
"""

from __future__ import annotations

import os
import re

_RUN_LINE = re.compile(r"^Run:\s*`?([^`\s]+)`?", re.M)
# A PR link line, also as a list item or in bold (`- PR: ...`, `**PR:** ...`).
_PR_LINE = re.compile(r"^\W{0,4}((?:[A-Z][\w-]* )?PR):\**\s*(.*)$", re.M)
_URL = re.compile(r"https?://[^\s)>\]]+")


def run_id() -> str:
    """This run's id, as the runner sets it; `unknown` when the host set none."""
    return os.environ.get("FACTORY_RUN_ID") or "unknown"


def worker_of(run: str | None) -> str | None:
    """The worker in a runner's `<instance>:<worker>:<started_at>` run id."""
    parts = (run or "").split(":")
    return parts[1] if len(parts) >= 3 else None


def run_of(body: str) -> str | None:
    """The run id in a post's attribution header."""
    m = _RUN_LINE.search((body or "")[:500])
    return m.group(1) if m else None


def header(run: str, provenance_header: str) -> str:
    return f"{provenance_header}\nRun: `{run}`"


def compose(body: str, run: str, provenance_header: str) -> str:
    """A full post for a script worker: the attribution header, then the prose."""
    return "\n\n".join(p for p in (header(run, provenance_header), (body or "").strip()) if p)


def links_in(body: str) -> list[tuple[str, str]]:
    """Every (kind, URL) PR link a post makes, in order: one per `<Kind> PR:` line anywhere in it, the
    first URL in its value, written bare, as `[text](url)` or as `<url>`. The kind is capitalised as
    written in `common/links.md`; a lowercase `pr:` is prose."""
    out = []
    for m in _PR_LINE.finditer(body or ""):
        url = _URL.search(m.group(2))
        if url:
            out.append((m.group(1), url.group(0)))
    return out
