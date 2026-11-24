---
name: widgets-testing
description: How to build and test the widgets repo. Use before opening or updating a PR, and when choosing which tests a change needs.
---

# Testing in widgets

`make test` runs the unit tests. `make e2e` runs the end-to-end suite against a local server.

A change to a renderer under `src/render/` also needs `make e2e`. When an e2e test fails on CI but
passes locally, read `references/flaky-tests.md` before changing the test.
