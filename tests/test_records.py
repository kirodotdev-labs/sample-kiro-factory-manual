"""What scripts read from a factory post (workers/lib/records.py): the attribution header and the PR
link lines. Stdlib only."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers" / "lib"))
import records  # noqa: E402

HEADER = "🏭 **Acme Factory** · automated agent"


class RecordsTest(unittest.TestCase):
    def test_a_post_names_its_run_and_worker(self):
        body = records.compose("Prose.", "acme:design:2026-11-02T18:00:00Z", HEADER)
        self.assertTrue(body.startswith(HEADER + "\nRun: `acme:design:2026-11-02T18:00:00Z`\n\nProse."))
        self.assertEqual(records.worker_of(records.run_of(body)), "design")
        self.assertIsNone(records.run_of("no header"))

    def test_links_come_from_pr_lines_wherever_they_are(self):
        self.assertEqual(records.links_in("Fixed in\nPR: https://h/o/r/pull/7"), [("PR", "https://h/o/r/pull/7")])
        self.assertEqual(records.links_in("prose naming https://h/o/r/pull/7"), [])
        two = "**Release tracking**\nPR: https://h/o/r/pull/1 · closed\nEdge: x\nPR: https://h/o/r/pull/2 · open"
        self.assertEqual([u for _, u in records.links_in(two)], ["https://h/o/r/pull/1", "https://h/o/r/pull/2"])
        # The same lines inside a list, as a module's record writes them, in any link form.
        listed = "x\n\n**Item record**\n- Spec PR: [#12](https://h/o/r/pull/12)\n- Repro PR: <https://h/o/r/pull/9>"
        self.assertEqual(records.links_in(listed), [("Spec PR", "https://h/o/r/pull/12"),
                                                    ("Repro PR", "https://h/o/r/pull/9")])
        self.assertEqual(records.links_in("- PR: https://h/o/r/pull/12 (merged)"), [("PR", "https://h/o/r/pull/12")])


if __name__ == "__main__":
    unittest.main()
