# Kind: publishing

Where the factory publishes pages a human reads: spec and implementation review
pages, learn's proposals and the report.

## Capabilities

| Capability | Required parameters | Semantics every platform must honour |
|---|---|---|
| `put_page` | `key`, `html` | Creates or replaces the page at a stable key and returns its URL. The same key always yields the same URL, so a link posted once stays valid. Concurrent writers must not silently overwrite each other: use a conditional write where the platform has one. |
| `put_asset` | `key`, `bytes`, `contentType` | Stores an image, video or other file a page links to, and returns its URL. |
| `get_page` | `key` | Returns the current page, or nothing. Workers that keep history inside their page read it back here. |

## Keys

| Key | Written by |
|---|---|
| `items/<item>/spec.html` | design |
| `items/<item>/implementation.html` | implement |
| `learn/<date>.html` | learn |
| `learn/<date>.json`, `learn/index.json` | learn (read by discover) |
| `discover/<date>/<run>.html` | discover |

A module may publish under its own keys.

Readers must be authenticated when the content is not public. That is the integration's job, not
the step's.
