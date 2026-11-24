"""The `harness` kind's interface.

A harness's `client.py` is a module with these four functions; the semantics are in `KIND.md`. The
renderer imports the configured harness from the Manual or the instance to call `render`, and copies
its `client.py` into the bundle at scripts/integrations/harness/<name>.py, where the runner imports it
for `install`, `command` and `result`. `cfg` is the `integrations.harness` block, `use` included.

`workers` maps each agent worker to what its agent loads: `context`, files the harness puts in the
model's context when the run starts, each `{"path": bundle path, "chars": int}`; and `skills`, SKILL.md
bundle paths the harness discloses progressively (name and description up front, the body when the
agent needs it). `mcp_servers` maps a server name to a stdio server in the MCP client shape:
`{"command": str, "args": [str], "env": {str: str}}`.
"""

from __future__ import annotations

from pathlib import Path

# Optional: environment variables the harness reads in `install` and in the run it starts. When a
# host profile lists `env`, the runner keeps these in each run as well.
ENV: tuple[str, ...] = ()


def render(cfg: dict, *, bundle: str, workers: dict[str, dict], mcp_servers: dict[str, dict]) -> dict[str, str]:
    """The files this harness reads, as {bundle-relative path: content}. Raises ValueError when a
    worker's context does not fit what the harness loads."""
    raise NotImplementedError


def install(cfg: dict, bundle: Path, worker: str) -> None:
    """Put this worker's rendered files where the harness looks them up. Headless; never prompts."""
    raise NotImplementedError


def command(cfg: dict, *, bundle: Path, worker: str, instruction: str, model: str | None) -> list[str]:
    """The argv for one headless turn of `worker` on `instruction` with every tool allowed."""
    raise NotImplementedError


def result(cfg: dict, stdout: str) -> dict:
    """What the run's stdout says: {"text": readable output, "credits": float or None}."""
    raise NotImplementedError
