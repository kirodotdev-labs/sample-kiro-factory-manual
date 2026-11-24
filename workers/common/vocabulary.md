# Vocabulary

The factory's labels, and where the state scripts read lives. A prompt writes `{{label.<key>}}`; the
key is fixed, the value is whatever the board already uses. An instance renames a value in its config
under `vocabulary.labels`, never the key. A label exists only because a script reads it or a worker
needs it to bound a board query; any other state is read from the item's comments and its PRs.
Columns belong to the board integration (`references/capabilities.md` in the board skill).

## Labels

| Key | Default value | Meaning |
|---|---|---|
| `claim` | `Working` | An item is being actively worked right now. Added at pick time, removed at every terminal (`common/claim.md`). Queue reads exclude it. |
| `groomed` | `Groomed` | Triage has routed the item. It is no longer untriaged. |
| `fastTrack` | `Fast Track` | The item may be built without a spec. Implement selects on it. |
| `spec` | `Spec` | The item needs a spec authored and approved before implementation. Design selects on it. |
| `needsInfo` | `Needs Info` | Only the requester can supply what triage needs. The janitor closes the item after the needs-info window, counted from triage's newest post. |
| `released` | `Released` | Release closed the item as released. Monitor and discover select on it. |
| `monitoringComplete` | `Monitoring Complete` | Monitor finished assessing the released change. |
| `discovered` | `Discovered` | Discover created the item. The factory is its requester. |
| `riskVeryLow`, `riskLow`, `riskMedium`, `riskHigh`, `riskVeryHigh` | `Risk: Very Low` ... `Risk: Very High` | The item's risk level (`common/risk.md`). An item carries at most one. Triage sets it; the `risk` signal in implement and release may change it. |

## What scripts read

Workers and people read comments for meaning, whatever their wording. Scripts read only these:

- **The attribution header** (`common/writing.md`) on every factory post: it marks the post as the
  factory's, and its `Run:` line names the run, whose id names the worker.
- **PR link lines** (`common/links.md`): `<Kind> PR: <URL>`, how a worker or a person links a PR on
  an item.
- **The labels above.**

A module the factory runs may add what its scripts read, through the context and skills it gives workers.
