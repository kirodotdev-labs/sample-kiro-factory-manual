# Linking PRs

Later runs, and the scripts that read the board, find an item's PRs from its posts. Link each PR the
item has on its own line, in the post that introduces it and in each status comment you replace:

```
PR: https://code.example.com/acme/app/pull/12
```

Use `PR` for the implementation, `Spec PR` for the spec, and `<Kind> PR` for another PR tied to the
item (`Repro PR`). A person on the team links a PR the same way. A PR only mentioned in prose is
not the item's, and neither is one that someone outside the team linked.
