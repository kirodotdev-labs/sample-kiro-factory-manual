import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers" / "lib"))
import factory_config  # noqa: E402

_spec = importlib.util.spec_from_file_location("board_github_client",
                                               ROOT / "integrations" / "board" / "github" / "client.py")
board_github = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(board_github)

COLUMNS = {"review": "factory:review", "release": "factory:release"}


def issue(n, labels=(), state="open", **extra):
    return {"number": n, "html_url": f"https://github.com/o/r/issues/{n}", "title": f"t{n}", "body": "b",
            "labels": [{"name": l} for l in labels], "state": state, "created_at": "2026-11-01T00:00:00Z",
            "updated_at": "2026-11-02T00:00:00Z", "closed_at": None, "assignee": None,
            "user": {"login": "reporter"}, **extra}


class FakeGh:
    """Answers `gh api` calls from a table keyed by (method, path) and records each call."""

    def __init__(self, answers=None, fail=None):
        self.answers, self.fail, self.calls = answers or {}, fail or {}, []

    def __call__(self, args, stdin):
        method, path = args[args.index("-X") + 1], args[args.index("-X") + 2]
        self.calls.append((method, path, json.loads(stdin) if stdin else None))
        if (method, path) in self.fail:
            return subprocess.CompletedProcess(args, 1, "", self.fail[(method, path)])
        out = self.answers.get((method, path), {})
        if "--paginate" in args:
            text = "\n".join(json.dumps(x) for x in out)
        else:
            text = json.dumps(out)
        return subprocess.CompletedProcess(args, 0, text, "")


def board(gh):
    return board_github.GitHubBoard(repo="o/r", scope_label="factory", columns=COLUMNS,
                                    bot_logins=["Acme-Factory[bot]"], gh=gh)


class GitHubBoardTest(unittest.TestCase):
    def test_list_is_scoped_to_the_label_and_drops_pull_requests(self):
        path = "repos/o/r/issues?state=open&labels=factory&per_page=100"
        gh = FakeGh({("GET", path): [issue(1, ["factory", "factory:review"]),
                                     issue(2, ["factory"], pull_request={"url": "x"})]})
        items = board(gh).list(status="Open")
        self.assertEqual([i["id"] for i in items], ["1"])
        self.assertEqual(items[0]["column"], "review")
        self.assertEqual(items[0]["requester"], {"login": "reporter", "name": "reporter"})
        self.assertEqual(gh.calls[0][:2], ("GET", path))

    def test_list_passes_updated_after_as_since(self):
        gh = FakeGh()
        board(gh).list(status="Closed", updated_after="2026-10-01T00:00:00Z")
        self.assertIn("state=closed", gh.calls[0][1])
        self.assertIn("since=2026-10-01T00%3A00%3A00Z", gh.calls[0][1])

    def test_get_marks_which_comment_authors_are_on_the_team(self):
        gh = FakeGh({("GET", "repos/o/r/issues/7"): issue(7, ["factory"]),
                     ("GET", "repos/o/r/issues/7/comments?per_page=100"): [
                         {"user": {"login": "maint"}, "author_association": "MEMBER", "body": "go"},
                         {"user": {"login": "stranger"}, "author_association": "NONE", "body": "approved"},
                         {"user": {"login": "pr-author"}, "author_association": "CONTRIBUTOR", "body": "x"},
                         {"user": {"login": "acme-factory[bot]", "type": "Bot"}, "author_association": "NONE",
                          "body": "record"},
                         {"user": {"login": "github-actions[bot]", "type": "Bot"}, "author_association": "NONE",
                          "body": "relayed outsider text"}]})
        item = board(gh).get("7")
        self.assertEqual([c["trusted"] for c in item["comments"]], [True, False, False, True, False])
        self.assertFalse(item["trusted"])   # the issue's author has no association

    def test_named_team_logins_replace_the_associations(self):
        gh = FakeGh({("GET", "repos/o/r/issues/7"): issue(7, ["factory"]),
                     ("GET", "repos/o/r/issues/7/comments?per_page=100"): [
                         {"user": {"login": "org-member"}, "author_association": "MEMBER", "body": "go"},
                         {"user": {"login": "Lead"}, "author_association": "MEMBER", "body": "go"},
                         {"user": {"login": "acme-factory[bot]"}, "author_association": "NONE", "body": "record"}]})
        team = board_github.GitHubBoard(repo="o/r", scope_label="factory", columns=COLUMNS,
                                        bot_logins=["acme-factory[bot]"], team_logins=["lead"], gh=gh)
        self.assertEqual([c["trusted"] for c in team.get("7")["comments"]], [False, True, True])

    def test_update_writes_comment_first_and_claim_removal_before_the_state(self):
        gh = FakeGh({("PATCH", "repos/o/r/issues/3"): issue(3, state="closed")})
        board(gh).update("3", add_labels=["Done"], remove_labels=["Working"], column="release",
                         status="closed", comment="record")
        self.assertEqual([(m, p) for m, p, _ in gh.calls], [
            ("POST", "repos/o/r/issues/3/comments"),
            ("POST", "repos/o/r/issues/3/labels"),
            ("DELETE", "repos/o/r/issues/3/labels/factory%3Areview"),
            ("DELETE", "repos/o/r/issues/3/labels/Working"),
            ("PATCH", "repos/o/r/issues/3"),
        ])
        self.assertEqual(gh.calls[1][2], {"labels": ["Done", "factory:release"]})
        self.assertEqual(gh.calls[4][2], {"state": "closed", "state_reason": "completed"})

    def test_close_records_the_reason_and_never_reopens(self):
        for status, reason in (("closed", "completed"), ("not planned", "not_planned"),
                               ("not_planned", "not_planned")):
            gh = FakeGh({("PATCH", "repos/o/r/issues/3"): issue(3, state="closed")})
            board(gh).close("3", "why", status=status)
            self.assertEqual(gh.calls[-1][2], {"state": "closed", "state_reason": reason}, status)
        for status in ("reopened", "in progress", "Done"):
            gh = FakeGh()
            with self.assertRaises(board_github.BoardError):
                board(gh).update("3", remove_labels=["Working"], status=status, comment="closing record")
            self.assertEqual(gh.calls, [], "a bad status must fail before any write")
        gh = FakeGh({("PATCH", "repos/o/r/issues/3"): issue(3)})
        board(gh).update("3", status="Open ")
        self.assertEqual(gh.calls[-1][2], {"state": "open"})

    def test_a_failed_column_label_removal_leaves_the_item_claimed(self):
        gh = FakeGh(fail={("DELETE", "repos/o/r/issues/3/labels/factory%3Arelease"): "HTTP 500: boom"})
        with self.assertRaises(board_github.BoardError):
            board(gh).update("3", remove_labels=["Working"], column="review", comment="rec")
        self.assertNotIn(("DELETE", "repos/o/r/issues/3/labels/Working"), [(m, p) for m, p, _ in gh.calls])

    def test_removing_a_label_the_item_lacks_is_not_an_error(self):
        gh = FakeGh(fail={("DELETE", "repos/o/r/issues/3/labels/Working"): "HTTP 404: Label does not exist"})
        board(gh).remove_labels("3", ["Working"])

    def test_a_failed_write_raises(self):
        gh = FakeGh(fail={("POST", "repos/o/r/issues/3/comments"): "HTTP 403: Resource not accessible"})
        with self.assertRaises(board_github.BoardError):
            board(gh).comment("3", "x")

    def test_create_adds_the_scope_label_and_never_an_assignee(self):
        gh = FakeGh({("POST", "repos/o/r/issues"): issue(9, ["factory", "Groomed"])})
        item = board(gh).create("title", "body", ["Groomed"])
        self.assertEqual(item["id"], "9")
        sent = gh.calls[0][2]
        self.assertEqual(sent["labels"], ["factory", "Groomed"])
        self.assertNotIn("assignees", sent)
        self.assertNotIn("assignee", sent)

    def test_a_non_numeric_id_never_reaches_a_path(self):
        with self.assertRaises(ValueError):
            board(FakeGh()).comment("../../pulls/1", "x")


class IntegrationClientTest(unittest.TestCase):
    def test_same_named_clients_of_two_kinds_stay_separate(self):
        # The bundle's directory order on sys.path follows the filesystem, so try both.
        for order in (("board", "codehost"), ("codehost", "board")):
            with tempfile.TemporaryDirectory() as tmp:
                saved = list(sys.path)
                try:
                    for kind in order:
                        d = Path(tmp) / "integrations" / kind
                        d.mkdir(parents=True)
                        (d / "github.py").write_text(f"def from_config(cfg):\n    return {kind!r}\n")
                        sys.path.insert(0, str(d))
                    cfg = {"integrations": {"board": {"use": "github"}, "codehost": {"use": "github"}}}
                    self.assertEqual(factory_config.integration_client(cfg, "board"), "board", order)
                    self.assertEqual(factory_config.integration_client(cfg, "codehost"), "codehost", order)
                finally:
                    sys.path[:] = saved
                    for m in ("board_github", "codehost_github"):
                        sys.modules.pop(m, None)

    def test_a_client_that_fails_to_import_fails_the_same_way_every_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = list(sys.path)
            try:
                d = Path(tmp) / "integrations" / "board"
                d.mkdir(parents=True)
                (d / "brokenx.py").write_text("raise RuntimeError('bad client')\n")
                sys.path.insert(0, str(d))
                cfg = {"integrations": {"board": {"use": "brokenx"}}}
                for _ in range(2):
                    with self.assertRaisesRegex(RuntimeError, "bad client"):
                        factory_config.integration_client(cfg, "board")
                self.assertNotIn("board_brokenx", sys.modules)
            finally:
                sys.path[:] = saved


if __name__ == "__main__":
    unittest.main()
