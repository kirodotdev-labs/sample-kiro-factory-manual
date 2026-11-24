---
name: publishing-disk
description: Publish factory pages and assets as files in a local directory. Use when a step publishes or reads back a page.
---

# Integration: publishing on disk

Pages are files under `{{config.integrations.publishing.dir}}`; a key is a path relative to it.

## The client

Publish and read through the bundle's client, from the host's Python. Write the page to a file
first, then:

```
{{host.python}} - <<'EOF'
import sys
sys.path.insert(0, "{{host.bundle}}/scripts/lib")
import runner
runner.bootstrap()
import factory_config
pub = factory_config.integration_client(factory_config.load(), "publishing")
print(pub.put_page("<key>", open("<page file>", "rb").read(), create_only=True), pub.url("<key>"))
EOF
```

The sections below name the calls to make on `pub`.

## Page packaging

A page opens straight from disk, so it is one self-contained file: CSS in a `<style>` block, script
inline, images as `data:` URIs or assets beside the page linked relatively.

## `put_page`

`pub.put_page(key, html, ...)`, with `html` as bytes: `create_only=True` for the
first write of a key, `expected=<the version get_page returned>` to replace it. Either losing raises
`Conflict`, which ends the script with that name in its traceback; re-read and retry. The page URL
is `pub.url(key)`.

## `put_asset`

`pub.put_asset(key, data, content_type)` writes unconditionally and returns the asset's URL.

## `get_page`

`pub.get_page(key)` returns `(body, version)`, or `(None, None)` when the key does not exist.
