# Flaky tests

An instance-only worker: once a week, file a board item for each new flaky e2e test.

## Inputs

The last 7 days of `main` CI runs, and the `widgets-testing` skill's `references/flaky-tests.md`.

If the CI history cannot be read, stop and report that. Do not treat it as no failures.

## Outputs

At most 3 new board items, one per test that failed and then passed on retry at least twice and is
not already in the table or on the board.

## Verification

None beyond the selection rule above.

## Decisions

File or skip each test. Never asks a human; the items are the report.

<!-- include: common/writing -->
<!-- include: common/links -->
<!-- include: integration board -->
<!-- include: instance -->
