---
name: widgets-triage
description: What triage and the risk signal need to know about the widgets repo. Use when classifying an item or judging a change's risk.
---

# Triage in widgets

## verification-levels

Lowest first. The fast-track ceiling is `integration`.

- **unit**: `make test` covers it.
- **integration**: the change crosses the API and a renderer; `make test` covers it.
- **e2e**: a renderer under `src/render/` changes what a user sees; `make e2e`.

## axis-taxonomy

- Public contracts: the REST API under `api/` and the widget file format. The API owners review a
  spec that changes either.
- Config surfaces: `widgets.toml` keys a customer can set.
- Security surfaces: the upload endpoint, and anything under `auth/`.

## risk-levels

- A change to the widget file format is at least **high**: stored files outlive the change.
- Verification at `e2e` alone does not raise the level above **medium**.
