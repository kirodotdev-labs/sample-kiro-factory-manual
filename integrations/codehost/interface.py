"""The `codehost` kind's client interface.

A codehost client lets a script worker read and drive pull requests without a model. The factory's
default agent workers perform these capabilities through the host's `gh`/`git` tools and the prompt,
not through a client; this protocol exists for the script workers that read code-host state
deterministically (the ci-green signal reads a PR's checks). An integration MAY ship a
`client.py` implementing it. The capabilities mirror `integrations/codehost/kind.json`; what each
must honour is in `KIND.md`.

A client that implements only the read side (what the script workers need) raises NotImplementedError for the
write capabilities rather than pretending to perform them. The methods the shipped scripts call are
`pr_detail` and `checks_read` (the ci-green signal); a client implements at least those. A module may
read more through its own reader around the client. It is constructed by a module-level `from_config(cfg)`. Times are ISO 8601; a login is
the platform's user name.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CodehostClient(Protocol):
    def pr_detail(self, number: int) -> dict:
        """{number, state, isDraft, headRefOid, headRefName, baseRefName, mergeable, url}. Raises on
        failure."""
        ...

    def checks_read(self, number: int) -> list[dict]:
        """Each check on the PR head as {name, state, bucket}; bucket is pass, fail, pending,
        skipping or cancel. No checks yet is an empty list."""
        ...

    def pr_open(self, *, head: str, base: str, title: str, body: str, draft: bool = True) -> dict:
        ...

    def pr_update(self, number: int, **fields) -> dict:
        ...

    def pr_ready(self, number: int) -> dict:
        ...

    def pr_comment(self, number: int, body: str) -> None:
        ...

    def pr_reply(self, number: int, thread_id: str, body: str) -> None:
        ...

    def pr_resolve_thread(self, number: int, thread_id: str) -> None:
        ...

    def checks_rerun(self, check: str) -> None:
        ...

    def reviews_read(self, number: int) -> list[dict]:
        ...

    def branch_push(self, branch: str) -> None:
        ...
