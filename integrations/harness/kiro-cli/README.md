# Harness: kiro-cli

The built-in harness, used when the config sets no `integrations.harness`. It covers each harness
capability this way:

- `render` writes `agents/<worker>.json` for each agent worker, a kiro-cli agent in the V3 format
  (from `agent.template.json`) named `<agent>-<worker>`. The worker's context are `file://` resources, which
  kiro-cli loads when the run starts; its skills are `skill://` resources, which it discloses
  progressively. The render fails when a worker's context passes what kiro-cli loads (32 files,
  100,000 characters, 50,000 from one file) rather than let kiro-cli truncate it.
- `install` copies the worker's agent to `~/.kiro/agents/<agent>-<worker>.json`, or under
  `$KIRO_HOME/agents/` when that is set.
- `command` runs `kiro-cli chat --v3 --no-interactive --trust-all-tools --agent <agent>-<worker>
  --output-format stream-json` on the instruction.
- `result` reads that stream: the agent's messages and tool-call titles become the text, and the
  credits are the sum of every `turn_completion` event's `promptTurnSummaries` entries in `credit`.
  `extraArgs` must not set another `--output-format`. Those events are kiro-cli's own (`_meta.kiro`
  on a `session_info_update`), not a published format: when a kiro-cli release changes them, run
  records show no credits rather than a wrong number, and `result` is the one place to fix.

kiro-cli also loads the running user's `~/.kiro/steering` into every run, whatever the agent
lists. A host keeps personal steering out by running the factory as its own OS user, or with `HOME`
or `KIRO_HOME` set to a directory of the factory's own (`factory-setup/references/hosting-local.md`).

The agent runs with every tool allowed and no approval prompt, which a headless run needs. What
bounds it is the host: the user it runs as, the credentials in its environment and the network it
can reach (README, Security).

The worker host needs a kiro-cli on `PATH` (or `binary` set) whose `chat` takes the flags above
(`kiro-cli chat --v3 --no-interactive "say ok"` answers), and a credential kiro-cli reads headless,
such as `KIRO_API_KEY`.

```json
"integrations": { "harness": { "use": "kiro-cli", "binary": "kiro-cli", "agent": "kiro-factory" } }
```

A host whose binary lives elsewhere sets it in its `integrationOverrides.harness`.
