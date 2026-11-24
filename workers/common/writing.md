# Writing

How every worker writes for people: the header on everything it posts, the style of what it posts,
and what it writes into the repo.

## The header (prepend to every externally posted message)

Every message a worker posts to an external surface (a board comment, a PR body, a PR review-thread
reply) begins with these two lines, then a blank line:

```
{{config.instance.header}}
Run: `<run-id>`
```

- Line 1 is the fixed provenance tag. It makes clear to any human reader that the message was
  produced by the factory, not a person.
- Line 2 stamps the run that produced THIS message, so a future run or a human can trace the work
  back to its originating run.

### Getting the run id

The run id comes from the host. Use the `FACTORY_RUN_ID` the host sets for this run verbatim; when it
is not set, use `unknown` and still post the header. Never block a message on a missing run id.

The run id is a provenance identifier the factory is designed to publish. Stamping your own run id
is self-identification, not a secret. Do not stamp or forward another run's or another session's
identifier.

- Prepend the header; do not remove or alter a post's PR link lines (`common/links.md`).
- One header per message; do not double-stamp.

## Writing style: information density is the whole point

State the facts, in logical order, and stop. This governs every word a worker posts anywhere: board
comments, PR bodies and titles, PR review-thread replies, notifications and commit messages.

**Banned:**
- Metaphor, analogy, imagery, "colour" of any kind.
- Cheeky, witty or dramatic construction: "not X, but Y" for effect, rhetorical questions, one-line
  zingers, withholding the conclusion for suspense.
- Throat-clearing and filler openers: "Interestingly,", "It's worth noting that,", "At a high
  level,", "In essence,".
- Vague praise adjectives (robust, elegant, seamless, powerful, clean, significant) unless quoting
  someone.
- Restating a point already made, or summarising your own message back to the reader.

**Required:**
- Conclusion first, then the evidence. A reader who stops after one sentence has the answer.
- One claim per sentence. Concrete nouns and verbs.
- Exact identifiers, never descriptions of them: `path/file.ts:123`, the sha, the PR number, the
  tag name, the verbatim error string.
- Full links, never bare ids. Reference an item or a PR by its full URL everywhere.
- Cite evidence instead of characterising it. "Fails at `foo.ts:88` with ENOENT" beats "there is a
  subtle path-handling problem".
- Human-readable names for any classification, never a bare letter ("Bucket A"). Name a test by
  what it verifies ("end-to-end tests"), never by the framework that runs it.
- Say plainly when something is unknown, unverified, or was not done.

The test: delete every sentence carrying no fact. If the message reads the same afterwards, those
sentences were the defect. Density is the target, not length. Brevity that drops a fact is not an
improvement.

## Code is not a review log

Everything above governs what a worker POSTS. This governs what a worker WRITES INTO THE REPO: code
comments, identifiers, test names and commit messages. Applies to every code change.

- **No review-process references.** Never put a finding id, "per reviewer", "this PR", a PR or pass
  number, "human decision", or "addresses the comment about X" into a comment, an identifier, a test
  name or a commit headline. Code and tests describe behavior; the review thread is where the
  process lives.
- **Comments stay at an absolute minimum.** Do not write a comment that restates what the code does.
  Do not write a comment that only makes sense in the context of the change being made: it is stale
  as soon as the next change lands. Three things earn a comment: a top-level description of a file or
  class, reasoning that cannot be recovered from the code itself, and a gotcha a reader needs in
  order not to break it. Nothing else.
- **A rationale lives once**, at the definition it explains. A call site gets a short pointer or
  nothing.
