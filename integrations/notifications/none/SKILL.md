---
name: notifications-none
description: The notifications channel is configured to deliver nothing. Use whenever a step would post to that channel.
---

# Notifications on none

`post` is a no-op: write the channel and the message text to the run log and continue. Nothing is
delivered and the run never fails because of it.
