#!/usr/bin/env python3
"""Post one message to a Slack Workflow Builder trigger, for a notifications channel.

    python3 <skill>/client.py <channel> <message file>       # agents
    from_config(cfg, channel).post(text)                     # scripts

An agent passes the message as a file, never as an argument: the text derives from items, and a
shell runs what it finds inside backticks or `$(...)`.

The webhook URL is a credential: it lives only in the file named by
integrations.notifications.<channel>.webhookFile and is never printed. Every outcome is a one-line
status (`<channel>: OK|SKIPPED|FAILED (...)`) and the exit code is always 0, so a notification never
fails the run that produced it. HTTP 200 {"ok":true} means the trigger received the payload, not
that the workflow rendered it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# The Markdown subset the factory writes (KIND.md): **bold**, "- " bullets and [text](url) links.
_LINK = re.compile(r"\[((?:\\.|[^\\\]])*)\]\((\S+?)\)")
_BOLD = re.compile(r"(?<!\\)\*\*(.+?)(?<!\\)\*\*")
_ESCAPED = re.compile(r"\\(.)")


def _text(s: str) -> str:
    return _ESCAPED.sub(r"\1", _BOLD.sub(r"\1", s))


def to_plain(text: str) -> str:
    """Plain text for the factory's Markdown. A Workflow Builder variable is inserted as plain
    text, so Slack shows mrkdwn in it literally; a link becomes "text · url" and Slack links the
    bare URL itself. A backslash-escaped character is kept literal."""
    out = []
    for line in text.split("\n"):
        if line.startswith("- "):
            line = "• " + line[2:]
        parts, last = [], 0
        for m in _LINK.finditer(line):
            parts.append(_text(line[last:m.start()]))
            label = _ESCAPED.sub(r"\1", m.group(1))
            parts.append(f"{label} · {m.group(2)}")
            last = m.end()
        parts.append(_text(line[last:]))
        out.append("".join(parts))
    return "\n".join(out)


class SlackWorkflow:
    def __init__(self, channel: str, webhook_file: str, payload_variable: str):
        self.channel = channel
        self.webhook_file = os.path.expanduser(webhook_file or "")
        self.payload_variable = payload_variable or ""

    def post(self, text: str, markdown: bool = False) -> str:
        tag = self.channel
        if not self.payload_variable:
            return f"{tag}: SKIPPED (payloadVariable is empty)"
        if not text:
            return f"{tag}: SKIPPED (no message given)"
        try:
            url = Path(self.webhook_file).read_text().strip()
        except OSError:
            return f"{tag}: SKIPPED (no webhook file at {self.webhook_file or 'unset'})"
        if not url:
            return f"{tag}: SKIPPED (webhook file is empty)"
        body = json.dumps({self.payload_variable: to_plain(text) if markdown else text}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                reply = resp.read(300).decode(errors="replace")
                code = resp.status
        except urllib.error.HTTPError as exc:
            return f"{tag}: FAILED (http {exc.code})"
        except (urllib.error.URLError, OSError) as exc:
            return f"{tag}: FAILED (no response: {type(exc).__name__})"
        if '"ok":false' in reply.replace(" ", ""):
            return f"{tag}: FAILED (http {code}, body: {reply})"
        return f"{tag}: OK (http {code})"


def from_config(cfg: dict, channel: str = "feed") -> SlackWorkflow:
    c = ((cfg.get("integrations") or {}).get("notifications") or {}).get(channel) or {}
    return SlackWorkflow(channel, c.get("webhookFile", ""), c.get("payloadVariable", ""))


def _config() -> dict:
    path = os.environ.get("FACTORY_CONFIG") or str(Path(os.environ.get("FACTORY_BUNDLE", ".")) / "factory-config.json")
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: client.py <channel> <message file>")
        return 0
    try:
        text = Path(argv[1]).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"{argv[0]}: FAILED (cannot read the message file: {type(exc).__name__})")
        return 0
    print(from_config(_config(), argv[0]).post(text))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
