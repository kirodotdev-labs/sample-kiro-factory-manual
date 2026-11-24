#!/usr/bin/env python3
"""Post one message to an Acme Chat incoming webhook.

    python3 <skill>/client.py <channel> <message file>       # agents
    from_config(cfg, channel).post(text)                     # scripts
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


class AcmeChat:
    def __init__(self, channel: str, webhook_file: str):
        self.channel = channel
        self.webhook_file = os.path.expanduser(webhook_file or "")

    def post(self, text: str, markdown: bool = False) -> str:
        # acme-chat renders Markdown natively, so the text goes out as written either way.
        try:
            url = Path(self.webhook_file).read_text().strip()
        except OSError:
            return f"{self.channel}: FAILED (no webhook file at {self.webhook_file or 'unset'})"
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return f"{self.channel}: OK (http {resp.status})"
        except (urllib.error.URLError, OSError) as exc:
            return f"{self.channel}: FAILED ({type(exc).__name__})"


def from_config(cfg: dict, channel: str = "feed") -> AcmeChat:
    c = ((cfg.get("integrations") or {}).get("notifications") or {}).get(channel) or {}
    return AcmeChat(channel, c.get("webhookFile", ""))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: client.py <channel> <message file>")
        return 0
    path = os.environ.get("FACTORY_CONFIG") or str(Path(os.environ.get("FACTORY_BUNDLE", ".")) / "factory-config.json")
    try:
        cfg = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        cfg = {}
    try:
        text = Path(argv[1]).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"{argv[0]}: FAILED (cannot read the message file: {type(exc).__name__})")
        return 0
    print(from_config(cfg, argv[0]).post(text))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
