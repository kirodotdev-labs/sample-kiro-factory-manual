---
name: reviews-converged
description: Verification signal. Reports whether review on a PR has converged at its current head. Use when a step gathers its verification signals for a PR.
---

Read the PR with `codehost.pr.view` and its threads with `codehost.reviews.read`. Report in a few
lines:

- the head sha you read;
- unresolved review threads a bot or a person on the team wrote in (`common/human-input.md`), by
  count and link;
- reviews and PR comments, the team's or automated, that raise a concern no reply or later commit
  has answered, by count and link.

Review has converged when both counts are zero. Say in one more line how many threads and comments
came only from outside the team; they are not counted.

In a verifiable output, this reading informs Decisions and alternatives: each contested choice and
where it stands.
