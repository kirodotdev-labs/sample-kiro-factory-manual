#!/usr/bin/env python3
"""Verification signal: is CI green on a PR's current head?

    python3 <bundle>/scripts/signals/ci_green.py <pr number or URL>

Reads the PR's checks through the configured codehost client and prints the head sha, every check
that has not passed, and a last line `ci-green: PASS | FAIL | PENDING`. PASS means every check
passed or was skipped. A PR with no checks reported yet is PENDING, so an instance whose project
has no CI replaces this signal rather than relying on it. Always exits 0.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import runner  # noqa: E402

runner.bootstrap()
import factory_config  # noqa: E402


def reading(client, number: int) -> str:
    head = client.pr_detail(number).get("headRefOid", "?")
    checks = client.checks_read(number)
    failed = [c for c in checks if c.get("bucket") in ("fail", "cancel")]
    pending = [c for c in checks if c.get("bucket") == "pending"]
    lines = [f"head {head}: {len(checks)} checks"]
    lines += [f"  {c.get('bucket')}: {c.get('name')} ({c.get('state')})" for c in failed + pending]
    verdict = "FAIL" if failed else "PENDING" if pending or not checks else "PASS"
    if not checks:
        lines.append("  no checks reported yet")
    lines.append(f"ci-green: {verdict}")
    return "\n".join(lines)


def pr_number(arg: str) -> int | None:
    """The PR number from a number or a URL: its trailing digits, else the last path segment that is
    a number (a URL that goes on past the PR, such as one ending in /files). A URL's query and
    fragment are not part of it."""
    arg = re.split(r"[?#]", arg.strip(), maxsplit=1)[0] if "/" in arg else arg
    found = re.findall(r"(\d+)\s*$", arg) or re.findall(r"/(\d+)(?=/)", arg)
    return int(found[-1]) if found else None


def main(argv: list[str]) -> int:
    number = pr_number(argv[0]) if argv else None
    if number is None:
        print("usage: ci_green.py <pr number or URL>")
        return 0
    try:
        client = factory_config.integration_client(factory_config.load(), "codehost")
        print(reading(client, number))
    except Exception as exc:  # a signal reports; the agent decides
        print(f"ci-green: UNKNOWN ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
