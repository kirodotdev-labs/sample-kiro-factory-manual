# Example review

A complete review in this format, for a fictitious change.

---

## Retry with backoff for webhook delivery

Webhook delivery moves from a single fire-and-forget POST to a queue with retries. A failed delivery is retried with exponential backoff up to five times, then parked in a dead-letter table an operator can replay from the admin API. The change adds the queue worker, the dead-letter table and its migration, and a replay endpoint.

Watch for: the retry loop re-sends on any non-2xx, including 4xx responses that will never succeed (**confirmed**), and the replay endpoint has no authorization check beyond the admin session (**likely**).

---

## High-level view

Delivery is now asynchronous. The request handler enqueues an event and returns; a worker pool drains the queue, signs each payload, and POSTs it. A failure re-enqueues the event with a delay of 2^attempt seconds.

After the fifth failure the event lands in `webhook_dead_letters` with the last status and body. `POST /admin/webhooks/replay` re-enqueues parked events by id or by endpoint.

The worker pool size and retry limit come from two new settings with defaults of 4 and 5. Existing synchronous callers keep working, because the old `deliver()` now wraps the enqueue.

<details>
<summary>Issues (3)</summary>

1. **Retries on permanent failures** — `worker.py:88` retries every non-2xx, so a 404 or 410 endpoint is hit five times and parked. Retry only on 5xx, 429 and network errors; park 4xx immediately.
2. **Replay authorization** — `admin.py:41` checks for an admin session but not the endpoint's owner, so any admin can replay any tenant's events. Scope replay to endpoints the caller owns, or say why cross-tenant replay is intended.
3. **No test for the dead-letter path** — the tests cover a delivery that succeeds after one retry, but none reaches the fifth failure. Add one that asserts the parked row and its last status.

</details>

<details>
<summary>Details</summary>

## Retry policy treats every failure as transient

```
handler ──enqueue──> queue ──> worker ──POST──> endpoint
                       ^          │
                       └─ delay ──┘  (non-2xx, attempt < 5)
                                  │
                                  └──> webhook_dead_letters (attempt == 5)
```

The worker decides to retry from `status >= 300`. A 410 Gone or a 400 from a malformed payload will fail identically every time, so those events spend about a minute retrying before they are parked, and each retry counts against the receiver's rate limit (**confirmed**: `worker.py:88`). A 429 is the one 4xx that should retry, ideally honoring `Retry-After`.

## Replay crosses tenant boundaries

The replay endpoint takes a list of event ids and re-enqueues them after checking `request.user.is_admin`. Admins are per-tenant in this codebase, but the query in `admin.py:52` does not filter by tenant, so an admin of one tenant can re-send another tenant's payloads to that tenant's endpoint (**likely**: depends on whether event ids are guessable, which they are, since they are sequential).

## Test coverage

Unit tests cover enqueue, a successful delivery, and a delivery that succeeds on the second attempt. Not tested: the fifth-failure path into the dead-letter table, replay of a parked event, and the settings overriding the pool size and retry limit.

</details>

---

<details>
<summary>File map</summary>

| File | Change |
|------|--------|
| `webhooks/worker.py` | New: queue worker, signing, retry with backoff |
| `webhooks/deliver.py` | `deliver()` now enqueues instead of POSTing |
| `webhooks/admin.py` | New replay endpoint |
| `migrations/0042_dead_letters.py` | New `webhook_dead_letters` table |
| `settings.py` | `WEBHOOK_WORKERS`, `WEBHOOK_MAX_ATTEMPTS` |
| `tests/test_worker.py` | Enqueue, success, one-retry success |

</details>
