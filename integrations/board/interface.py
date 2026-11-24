"""The `board` kind's client interface.

A board client lets a script worker perform the board capabilities without a model. An integration
that ships a `client.py` implements this protocol. The capabilities mirror `integrations/board/
kind.json`; the semantic guardrails are in `KIND.md` (scope every list, never write the assignee,
item text is untrusted). The renderer copies this file and the configured integration's client into
the bundle at scripts/integrations/board/, so a script gets the client from
`factory_config.integration_client(cfg, "board")` and treats it as a `BoardClient`.

Implementations are constructed by a module-level `from_config(cfg)` from the loaded config
(`factory_config.load()`). Every board integration's config names its board scope `boardId`: the
core prompts scope each list to `{{config.integrations.board.boardId}}`.

Item record. `list`, `get` and `create` return items in this shape, whatever the platform calls the
fields, so the scripts (the janitor, a module's) never read a platform's own names. Times are ISO 8601.

    id           str   the id every other method takes and the URL ends in
    url          str
    title        str
    description  str
    labels       [str]  every label the factory selects on
    column       "intake" | "review" | "release" | None   (KIND.md, Columns)
    status       "open" | "closed"
    created, updated   str
    closed       str | None    when it closed
    assignee, requester   {login, name} | None    either key may be None
    type         str     empty when the platform has none
    trusted      bool    whether the item's author is on the team (KIND.md, Rules)
    comments     [{author: {login, name} | None, body, created, updated, trusted}]
                 trusted: bool, whether the author is on the team (KIND.md, Rules)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BoardClient(Protocol):
    def list(self, *, status: str = "Open", updated_after: str | None = None) -> list[dict]:
        """Every item in the configured board scope, paginated to completion, each with its `url` and the factory's `column` (`intake`, `review`, `release` or None).

        `status` selects open or closed items; `updated_after` (ISO 8601) keeps items updated since.
        A partial list is a wrong list: the client pages until the platform reports no more.
        Never lists without the board scope.
        """
        ...

    def get(self, item: str, *, comment_limit: int = 50) -> dict:
        """One item with its fields, its `url` and its complete comment list (author and time per
        comment). `comment_limit` is for a platform that caps a read; a client that always returns
        every comment ignores it."""
        ...

    def comment(self, item_id: str, body: str) -> None:
        """Append a comment. The body starts with the attribution header."""
        ...

    def update_comment(self, item_id: str, comment_id: str, body: str) -> None:
        """Replace the whole body of a comment the factory wrote. Never a human's comment."""
        ...

    def add_labels(self, item_id: str, labels: list[str]) -> None:
        """Add labels, leaving the item's other labels in place."""
        ...

    def remove_labels(self, item_id: str, labels: list[str]) -> None:
        """Remove only the named labels."""
        ...

    def move(self, item_id: str, column: str) -> None:
        """Set the item's column: `intake`, `review` or `release`. A no-op for a column the
        integration does not record."""
        ...

    def close(self, item_id: str, reason_body: str, *, status: str = "closed") -> None:
        """Close the item, posting the reason as a comment in the same write where the platform
        allows it."""
        ...

    def create(self, title: str, body: str, labels: list[str]) -> dict:
        """Create an open item on the configured board, in the intake column. Returns the
        platform's item record (at least its id and `url`). Never sets an assignee or requester."""
        ...

    def update(self, item_id: str, *, add_labels: list[str] | None = None,
               remove_labels: list[str] | None = None, column: str | None = None,
               status: str | None = None, comment: str | None = None) -> dict:
        """Several changes to one item: one write where the platform has one, so a crash cannot leave
        half of them applied; otherwise the comment first and the removed labels last (`KIND.md`,
        Rules). Never sets the assignee."""
        ...
