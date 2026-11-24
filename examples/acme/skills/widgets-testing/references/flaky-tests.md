# Known flaky tests

| Test | Cause | What to do |
|---|---|---|
| `e2e/drag_test.py::test_snap` | animation timing on slow runners | retry the job once |
| `e2e/export_test.py` | shares a temp dir with `import_test.py` | run it alone with `make e2e ONLY=export` |

A failure not in this table is real until shown otherwise.
