"""The `publishing` kind's client interface.

A publishing client lets a script worker put and read pages at stable keys without a model. An
integration that ships a `client.py` implements this protocol; the capabilities mirror
`integrations/publishing/kind.json`, and the key->URL stability contract and the conditional-write
rule are in `KIND.md`. The renderer copies this file and the configured integration's client into
the bundle at scripts/integrations/publishing/, so a script gets the client from
`factory_config.integration_client(cfg, "publishing")` and treats it as a `PublishingClient`.

A client is constructed by a module-level `from_config(cfg)`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class Conflict(RuntimeError):
    """A conditional write lost: the page changed since it was read. The caller re-reads and retries
    rather than overwriting a newer version.

    The shape only: every kind's interface module is named `interface` on the same path, so a client
    cannot import this one reliably. Each client module defines its own `Conflict`, and a caller
    catches the one from the client it holds (`sys.modules[type(client).__module__].Conflict`)."""


@runtime_checkable
class PublishingClient(Protocol):
    packaging: str  # "inline" (one self-contained file) or "linked" (CSS/JS as assets beside the page)

    def url(self, key: str) -> str:
        """The stable reader URL for a key. The same key always yields the same URL."""
        ...

    def get_page(self, key: str) -> tuple[bytes | None, str | None]:
        """(body, version-handle) of the current page, or (None, None) when the key is empty. The
        version handle is passed back to `put_page` as `expected` for a conditional replace."""
        ...

    def put_page(self, key: str, html: bytes, *, expected: str | None = None,
                 create_only: bool = False, title: str = "", summary: str = "",
                 tags: list[str] | None = None) -> str:
        """Create or replace the page at `key`; return its new version handle.

        `expected` replaces only the version that was read; `create_only` writes only when the key
        is empty. Either one losing raises `Conflict`, so a second writer never silently overwrites
        the first. `title`/`summary`/`tags` seed platform metadata on first create where the platform
        has it.
        """
        ...

    def put_asset(self, key: str, data: bytes, content_type: str) -> str:
        """Store a binary asset a page links to, returning its URL."""
        ...
