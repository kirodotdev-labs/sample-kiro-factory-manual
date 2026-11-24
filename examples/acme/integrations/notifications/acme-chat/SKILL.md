---
name: notifications-acme-chat
description: Post a feed or operator notification to an Acme Chat incoming webhook. Use whenever a step posts to a notifications channel.
---

# Notifications on Acme Chat

An integration the instance ships itself, because the Manual has none for Acme Chat.

Write the message to a file first, so no shell quoting touches text that came from an item:

```
{{host.python}} {{host.bundle}}/skills/notifications-acme-chat/client.py <channel> <message file>
```

The client prints `<channel>: OK` or `<channel>: FAILED (...)` and always exits 0. The webhook URL is
a credential, kept only in the channel's `webhookFile`.
