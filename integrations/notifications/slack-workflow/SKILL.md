---
name: notifications-slack-workflow
description: Post a feed or operator notification through a Slack Workflow Builder trigger. Use whenever a step posts to a notifications channel.
---

# Notifications on a Slack Workflow Builder trigger

Post one message. Write it to a file first, so no shell quoting touches text that came from an
item:

```
{{host.python}} {{host.bundle}}/skills/notifications-slack-workflow/client.py <channel> <message file>
```

The channel is `feed` or `operator`. Read `references/capabilities.md` before posting: it holds what
a message carries, when a feed message is warranted, and how to dedupe one on the item.

- The webhook URL is a credential, kept only in the channel's `webhookFile`. Never put it in a
  prompt, a comment or a published file.
- The client always exits 0 and prints `<channel>: OK`, `SKIPPED (...)` or `FAILED (...)`. `OK`
  means the trigger received it, not that Slack rendered it; a payload variable mismatch shows only
  in Slack's workflow run history. An unpublished workflow fails every post with
  `workflow_not_published`.
