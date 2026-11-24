"""The `notifications` kind's client interface.

A notifications client posts one message to one channel without a model. The channels are `feed`
(the team) and `operator` (whoever runs the factory), per `integrations/notifications/kind.json`.
A script worker posts through a client of this shape. An integration MAY
ship a `client.py`; the semantics (one message, final result only, delivery may be unconfirmable)
are in `KIND.md`.

`post` returns a one-line status string rather than raising, because a notification failure must not
fail the run that produced the content: the script reports the status and keeps its own output.

A client is constructed by a module-level `from_config(cfg, channel)`, one per channel.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class NotificationsClient(Protocol):
    def post(self, text: str, markdown: bool = False) -> str:
        """Deliver one message to this channel. With markdown=True the text uses the Markdown
        subset in KIND.md and the client renders it for its platform; otherwise it is sent as
        given. Returns a one-line status (delivered / not delivered and why). Never raises on a
        delivery failure; reports it."""
        ...
