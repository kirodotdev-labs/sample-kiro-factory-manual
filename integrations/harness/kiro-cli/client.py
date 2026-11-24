"""kiro-cli as the factory's agent harness, the built-in default. See integrations/harness/KIND.md."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_BINARY = "kiro-cli"
DEFAULT_AGENT = "kiro-factory"
ENV = ("KIRO_HOME",)  # install writes the agent under it, so the run must look there too
# What kiro-cli loads from an agent's file:// resources (its configuration reference): at most 32
# files and 100,000 characters in all, 50,000 from one file. Past that it truncates or drops files.
MAX_CONTEXT_FILES, MAX_CONTEXT_CHARS, MAX_FILE_CHARS = 32, 100_000, 50_000


def _agent(cfg: dict, worker: str) -> str:
    return f"{cfg.get('agent') or DEFAULT_AGENT}-{worker}"


def _agent_file(worker: str) -> str:
    return f"agents/{worker}.json"


def render(cfg: dict, *, bundle: str, workers: dict[str, dict], mcp_servers: dict[str, dict]) -> dict[str, str]:
    """One kiro-cli agent per worker: its context as file:// resources, which kiro-cli loads when the
    run starts, and its skills as skill:// resources, which it discloses progressively."""
    template = json.loads((Path(__file__).resolve().parent / "agent.template.json").read_text())
    base = {k: v for k, v in template.items() if not k.startswith("_")}
    files = {}
    for worker, load in sorted(workers.items()):
        context = load.get("context", [])
        total = sum(c["chars"] for c in context)
        big = [c["path"] for c in context if c["chars"] > MAX_FILE_CHARS]
        if len(context) > MAX_CONTEXT_FILES or total > MAX_CONTEXT_CHARS or big:
            raise ValueError(f"worker {worker}: {len(context)} context files, {total} characters"
                             f"{', too long: ' + ', '.join(big) if big else ''}; kiro-cli loads at most "
                             f"{MAX_CONTEXT_FILES} files, {MAX_CONTEXT_CHARS} characters, {MAX_FILE_CHARS} per file")
        agent = dict(base, name=_agent(cfg, worker), mcpServers=mcp_servers)
        agent["resources"] = ([f"file://{c['path']}" for c in context]
                              + [f"skill://{s}" for s in load.get("skills", [])])
        files[_agent_file(worker)] = json.dumps(agent, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return files


def install(cfg: dict, bundle: Path, worker: str) -> None:
    """Copy the worker's agent to kiro-cli's agents directory, where `--agent` looks it up:
    ~/.kiro/agents, or $KIRO_HOME/agents when the host relocates ~/.kiro. The name is the factory's
    own, so this never replaces another agent."""
    src = bundle / _agent_file(worker)
    home = Path(os.environ.get("KIRO_HOME") or Path.home() / ".kiro")
    dest = home / "agents" / f"{_agent(cfg, worker)}.json"
    if not dest.exists() or dest.read_bytes() != src.read_bytes():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())


def command(cfg: dict, *, bundle: Path, worker: str, instruction: str, model: str | None) -> list[str]:
    argv = [cfg.get("binary") or DEFAULT_BINARY, "chat", "--v3", "--no-interactive", "--trust-all-tools",
            "--agent", _agent(cfg, worker), "--output-format", "stream-json"]
    model = model or cfg.get("model")
    if model:
        argv += ["--model", model]
    return argv + list(cfg.get("extraArgs", [])) + [instruction]


def result(cfg: dict, stdout: str) -> dict:
    """Read stream-json: one ACP event per line. Text is the agent's messages, with each tool call's
    title on its own line; credits sum every turn_completion's credit entries, retried attempts
    included. A run cut off before its last turn completed counts only the turns that did."""
    lines: list[str] = []
    message: list[str] = []
    final, credits, turns = "", 0.0, 0
    for raw in (stdout or "").splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        data = event.get("data") or {}
        if event.get("type") == "runFinished":
            final = data.get("finalText") or ""
            continue
        update = data.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text":
                message.append(content.get("text") or "")
            continue
        if message:
            lines.append("".join(message))
            message = []
        if kind == "tool_call" and update.get("title"):
            lines.append(f"[tool] {update['title']}")
        meta = ((update.get("_meta") or {}).get("kiro") or {}) if kind == "session_info_update" else {}
        if meta.get("kind") == "turn_completion":
            turns += 1
            credits += sum(float(s.get("usage") or 0) for s in meta.get("promptTurnSummaries") or []
                           if s.get("unit") == "credit")
    if message:
        lines.append("".join(message))
    text = "\n".join(lines).strip() or final
    return {"text": text, "credits": round(credits, 4) if turns else None}


