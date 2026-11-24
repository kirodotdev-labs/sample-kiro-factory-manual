import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci_green", ROOT / "workers" / "signals" / "ci_green.py")
ci_green = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_green)


class FakeCodehost:
    def __init__(self, checks):
        self.checks = checks

    def pr_detail(self, number):
        return {"headRefOid": "abc123"}

    def checks_read(self, number):
        return self.checks


def verdict(checks):
    return ci_green.reading(FakeCodehost(checks), 7).splitlines()[-1]


class CiGreenTest(unittest.TestCase):
    def test_pass_when_every_check_passed_or_skipped(self):
        self.assertEqual(verdict([{"name": "a", "bucket": "pass"}, {"name": "b", "bucket": "skipping"}]), "ci-green: PASS")

    def test_fail_beats_pending(self):
        self.assertEqual(verdict([{"name": "a", "bucket": "pending"}, {"name": "b", "bucket": "fail"}]), "ci-green: FAIL")

    def test_pending_while_running(self):
        self.assertEqual(verdict([{"name": "a", "bucket": "pending"}]), "ci-green: PENDING")

    def test_no_checks_is_pending(self):
        self.assertEqual(verdict([]), "ci-green: PENDING")

    def test_reading_names_the_head(self):
        self.assertIn("head abc123", ci_green.reading(FakeCodehost([]), 7))

    def test_the_pr_number_comes_from_a_number_or_any_url_of_the_pr(self):
        for arg, number in (("7", 7), ("https://git.example.com/acme/widgets/pull/7", 7),
                            ("https://git.example.com/acme/widgets/pull/7/files", 7),
                            ("https://git.example.com/acme/widgets/pull/7#issuecomment-123", 7),
                            ("https://git.example.com/acme/widgets/pull/7/files?w=1", 7),
                            ("https://reviews.example.com/R-4521", 4521)):
            self.assertEqual(ci_green.pr_number(arg), number, arg)
        self.assertIsNone(ci_green.pr_number("the PR"))


if __name__ == "__main__":
    unittest.main()


gh_spec = importlib.util.spec_from_file_location("github_client", ROOT / "integrations" / "codehost" / "github" / "client.py")
github_client = importlib.util.module_from_spec(gh_spec)
gh_spec.loader.exec_module(github_client)


class GitHubChecksReadTest(unittest.TestCase):
    def test_reads_go_to_the_configured_host(self):
        calls = []

        class Done:
            returncode, stdout, stderr = 0, '{"headRefOid": "abc123"}', ""
        client = github_client.from_config({"repo": {"slug": "acme/widgets"},
                                            "integrations": {"codehost": {"use": "github", "host": "git.example.com"}}})
        client._gh = lambda *args, **kw: calls.append(args) or Done()
        client.pr_detail(7)
        client._api_lines("repos/acme/widgets/commits/abc123/status", ".statuses[]")
        self.assertIn("git.example.com/acme/widgets", calls[0])
        self.assertEqual(calls[1][:3], ("api", "--hostname", "git.example.com"))
        default = github_client.from_config({"repo": {"slug": "acme/widgets"},
                                             "integrations": {"codehost": {"use": "github"}}})
        self.assertEqual(default.host, "github.com")

    def test_check_runs_and_statuses_map_to_gh_buckets(self):
        client = github_client.GitHub("acme/widgets")
        client.pr_detail = lambda n: {"headRefOid": "abc123"}
        pages = {
            "check-runs": [{"name": "build", "status": "completed", "conclusion": "success"},
                           {"name": "lint", "status": "completed", "conclusion": "skipped"},
                           {"name": "e2e", "status": "in_progress", "conclusion": None},
                           {"name": "unit", "status": "completed", "conclusion": "timed_out"}],
            "status": [{"context": "gates", "state": "pending"}, {"context": "cla", "state": "success"}],
        }
        client._api_lines = lambda path, jq: pages["check-runs" if "check-runs" in path else "status"]
        got = {c["name"]: c["bucket"] for c in client.checks_read(7)}
        self.assertEqual(got, {"build": "pass", "lint": "skipping", "e2e": "pending", "unit": "fail",
                               "gates": "pending", "cla": "pass"})
        self.assertEqual(verdict(client.checks_read(7)), "ci-green: FAIL")
