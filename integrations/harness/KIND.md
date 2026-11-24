# Kind: harness

The agent harness that runs an agent worker: the program that takes one instruction, gives the model
the worker's context, skills and MCP servers, and runs the turn headless with every tool allowed. The rest of
the Manual never names a harness. `kiro-cli` is the built-in one and the default when the config sets
no `integrations.harness`.

A harness is not agent-facing: it ships no skill, because the agent never calls it. Its directory has
a `README.md`, a `config.schema.json` and a `client.py` implementing `interface.py`.

## Capabilities

| Capability | Called by | Semantics every harness must honour |
|---|---|---|
| `render` | the renderer | Returns the files the harness reads, as bundle-relative paths and contents, per worker. Each worker's context is in the model's context from the start of its run; each of its skills is disclosed the harness's own way, its name and description up front and its body when the agent needs it. They load exactly the context, skills and MCP servers given and nothing from the user's own setup (personal steering, rules or agents), so a run behaves the same on every host. A harness that cannot keep the user's setup out by itself says so in its README, with what the host does to keep it out. A context the harness cannot load whole fails the render. |
| `install` | the runner, before each agent run | Puts the worker's rendered files where the harness looks them up. Writes only under names the factory owns. Never prompts and never logs in: it runs headless, often under a fresh `HOME`. |
| `command` | the runner | Returns the argv for one headless, non-interactive turn of the worker on the instruction, with every tool allowed, exiting non-zero when the turn fails. Pure: it runs nothing. |
| `result` | the runner, after each agent run (also on a timeout, with the partial output) | Reads the run's stdout into `{"text", "credits"}`. `text` is readable output, which the run record keeps as its trace and summary. `credits` is what the run spent, or `None` when the harness cannot tell; never a guessed number. Pure. |

## Rules

- The run record treats a non-zero exit as a failure and reads stderr only then, so a harness may log
  to stderr freely.
- Model ids are the harness's: `workers.<name>.model` is passed through unchanged.
