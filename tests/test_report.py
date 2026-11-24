"""The report step, rendered from the Manual's acme example with the step on, and run against fake
board and code host clients, publishing to disk. Stdlib only."""

import datetime as dt
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

MANUAL = Path(__file__).resolve().parents[1]
ACME = MANUAL / "examples" / "acme"
HOST = ACME / "hosts" / "local.json"
NOW = dt.datetime(2026, 11, 12, 18, 0, tzinfo=dt.timezone.utc)
sys.path.insert(0, str(MANUAL / "runtime" / "render"))
from render import Renderer  # noqa: E402


def iso(days=0, hours=0):
    return (NOW - dt.timedelta(days=days, hours=hours)).isoformat().replace("+00:00", "Z")


def render(tmp: Path) -> Path:
    inst = tmp / "acme"
    shutil.copytree(ACME, inst)
    cfg = json.loads((inst / "factory-config.json").read_text())
    cfg["integrations"]["publishing"] = {"use": "disk", "dir": str(tmp / "pages"), "baseUrl": "file:///pages/"}
    (inst / "factory-config.json").write_text(json.dumps(cfg))
    out = tmp / "bundle"
    Renderer(MANUAL, inst, HOST).render(out)
    return out


class FakeBoard:
    def __init__(self, open_items, closed_items, full):
        self.open_items, self.closed_items, self.full = open_items, closed_items, full

    def list(self, *, status="Open", updated_after=None):
        return self.open_items if status == "Open" else self.closed_items

    def get(self, item, *, comment_limit=50):
        return self.full[item]


class FakeCodehost:
    def __init__(self, prs):
        self.prs = prs

    def pr_detail(self, number):
        return self.prs[number]


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.bundle = render(cls.tmp)
        os.environ["FACTORY_BUNDLE"] = str(cls.bundle)
        os.environ["FACTORY_STATE_DIR"] = str(cls.tmp / "state")
        spec = importlib.util.spec_from_file_location(
            "report_worker", cls.bundle / "scripts" / "workers" / "report" / "report.py")
        cls.r = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.r)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("FACTORY_BUNDLE", None)
        os.environ.pop("FACTORY_STATE_DIR", None)
        cls._tmp.cleanup()

    def test_the_render_declares_the_worker_with_its_settings(self):
        w = json.loads((self.bundle / "workers.json").read_text())["report"]
        self.assertEqual(w["implementation"], "script")
        self.assertEqual(w["settings"], {"days": 30, "pageKey": "report/index.html"})
        self.assertTrue((self.bundle / "scripts" / "workers" / "report" / "report.py").is_file())

    def test_act_on_lists_needs_info_and_prs_out_of_draft(self):
        needs = self.r.factory_config.label("needsInfo")
        items = [
            {"id": "1", "title": "Ask", "url": "u1", "labels": [needs], "column": "intake"},
            {"id": "2", "title": "Ready", "url": "u2", "labels": [], "column": "review"},
            {"id": "3", "title": "Draft", "url": "u3", "labels": [], "column": "review"},
            {"id": "4", "title": "Building", "url": "u4", "labels": [], "column": None},
        ]
        full = {
            "2": {"description": "", "comments": [{"body": "PR: https://gh/acme/w/pull/7"},
                                                   {"body": "PR: https://gh/acme/w/pull/9"}]},
            "3": {"description": "Spec PR: https://gh/acme/w/pull/8", "comments": []},
        }
        prs = {9: {"state": "OPEN", "isDraft": False, "url": "https://gh/acme/w/pull/9"},
               8: {"state": "OPEN", "isDraft": True, "url": "https://gh/acme/w/pull/8"},
               7: {"state": "CLOSED", "isDraft": False, "url": "https://gh/acme/w/pull/7"},
               12: {"state": "OPEN", "isDraft": False, "url": "https://gh/acme/w/pull/12"}}
        full["4"] = {"description": "PR: https://gh/other-org/lib/pull/12", "comments": []}
        items[3]["column"] = "review"
        rows = self.r.act_on(items, needs, lambda i: full[i], FakeCodehost(prs).pr_detail)
        self.assertEqual([(r["id"], r["why"]) for r in rows],
                         [("1", "waiting on the requester's answer"), ("2", "PR ready for review")])
        self.assertEqual(rows[1]["link"], "https://gh/acme/w/pull/9")

    def test_a_link_matches_its_pr_whatever_its_fragment_query_or_case(self):
        pr = {"state": "OPEN", "isDraft": False, "url": "https://github.com/acme-corp/widgets/pull/9"}
        for link in ("https://github.com/acme-corp/widgets/pull/9#issuecomment-123",
                     "https://github.com/acme-corp/widgets/pull/9?w=1",
                     "https://github.com/Acme-Corp/Widgets/pull/9/"):
            items = [{"id": "2", "title": "T", "url": "u", "labels": [], "column": "review"}]
            rows = self.r.act_on(items, "Needs Info", lambda i: {"description": f"PR: {link}", "comments": []},
                                 lambda n: pr)
            self.assertEqual([r["id"] for r in rows], ["2"], link)

    def test_without_a_code_host_client_the_page_says_so_and_still_publishes(self):
        board = FakeBoard([], [], {})
        fc = self.r.factory_config
        orig = fc.integration_client

        def client(cfg, kind):
            if kind == "codehost":
                raise ModuleNotFoundError("No module named 'github'", name="github")
            return board if kind == "board" else orig(cfg, kind)
        fc.integration_client = client
        try:
            with self.assertRaises(self.r.Report):
                self.r.run(type("C", (), {"message": ""})())
        finally:
            fc.integration_client = orig
        data = self.embedded((self.tmp / "pages" / "report" / "index.html").read_text())
        self.assertIn("The code host has no script client: PRs awaiting review are not shown.", data["notes"])

    def test_a_code_host_client_that_cannot_import_fails_the_run(self):
        fc = self.r.factory_config
        orig = fc.integration_client

        def client(cfg, kind):
            if kind == "codehost":
                raise ModuleNotFoundError("No module named 'some_dep'", name="some_dep")
            return FakeBoard([], [], {}) if kind == "board" else orig(cfg, kind)
        fc.integration_client = client
        try:
            with self.assertRaises(ModuleNotFoundError):
                self.r.run(type("C", (), {"message": "dry"})())
        finally:
            fc.integration_client = orig

    def test_days_below_one_fails_the_run(self):
        fc = self.r.factory_config
        orig = fc.worker_setting
        fc.worker_setting = lambda w, k, d=None: 0 if k == "days" else orig(w, k, d)
        try:
            with self.assertRaises(self.r.Fail) as ctx:
                self.r.run(type("C", (), {"message": "dry"})())
        finally:
            fc.worker_setting = orig
        self.assertIn("days is 0; it must be at least 1", ctx.exception.message)

    def test_board_series_counts_open_items_per_day(self):
        opened = [{"created": iso(days=10)}, {"created": iso(hours=2)}]
        closed = [{"created": iso(days=40), "closed": iso(days=5)}, {"created": iso(days=6), "closed": iso(days=1)}]
        s = self.r.board_series(opened, closed, NOW, 30)
        self.assertEqual(len(s["open"]), 30)
        self.assertEqual(s["days"][-1], "2026-11-12")
        self.assertEqual(s["open"][0], 1)
        self.assertEqual(s["open"][-1], 2)
        self.assertEqual(max(s["open"]), 3)
        self.assertEqual((s["opened"], s["closed"]), (3, 2))

    def test_health_says_ok_failing_stalled_and_no_runs(self):
        runs = [{"worker": "triage", "started_at": iso(hours=h), "status": "succeeded"} for h in (2, 1.5, 1, 0.5)]
        runs += [{"worker": "design", "started_at": iso(hours=1), "status": "succeeded"},
                 {"worker": "design", "started_at": iso(hours=0.5), "status": "failed",
                  "error": "Traceback (most recent call last):\n  File \"x.py\", line 1\nRuntimeError: boom\n"}]
        runs += [{"worker": "implement", "started_at": iso(hours=h), "status": "succeeded"} for h in (12, 11.5, 11)]
        runs += [{"worker": "implement", "started_at": iso(hours=0.2), "status": "succeeded"}]
        runs += [{"worker": "learn", "started_at": iso(days=14), "status": "succeeded"},
                 {"worker": "learn", "started_at": iso(days=7), "status": "succeeded"}]
        workers = {n: {} for n in ("triage", "design", "implement", "learn", "janitor")}
        got = {r["worker"]: r for r in self.r.health(workers, runs, NOW)}
        self.assertEqual(got["triage"]["state"], "ok")
        self.assertEqual((got["design"]["state"], got["design"]["error"]), ("failing", "RuntimeError: boom"))
        self.assertEqual(got["implement"]["state"], "ok")
        self.assertEqual(got["learn"]["state"], "ok")
        self.assertEqual(got["janitor"]["state"], "no runs")
        stalled = [{"worker": "triage", "started_at": iso(hours=h), "status": "succeeded"} for h in (12, 11.5, 11)]
        self.assertEqual(self.r.health({"triage": {}}, stalled, NOW)[0]["state"], "stalled")

    def embedded(self, doc):
        start = doc.index('<script type="application/json" id="model">') + len('<script type="application/json" id="model">')
        return json.loads(doc[start:doc.index("</script>", start)])

    def test_the_page_is_the_template_with_its_assets_and_a_model_no_board_text_can_break(self):
        rows = [{"id": "1", "title": "</script><script>alert(1)</script>", "url": "u", "why": "w", "link": ""}]
        data = self.r.model("acme", NOW, rows, self.r.board_series([], [], NOW, 7), [("intake", 0)], [], [])
        doc = self.r.page(data)
        self.assertNotIn("{{", doc)
        self.assertEqual(doc.count("</script>"), 2)
        self.assertIn("<title>acme factory</title>", doc)
        self.assertIn(".pill.bad", doc)
        self.assertIn("function actOn()", doc)
        self.assertEqual(self.embedded(doc), json.loads(json.dumps(data)))
        for f in ("template.html", "report.css", "report.js"):
            self.assertTrue((self.bundle / "scripts" / "workers" / "report" / f).is_file(), f)

    def test_linked_packaging_links_the_assets_and_publishes_them_beside_the_page(self):
        data = self.r.model("acme", NOW, [], self.r.board_series([], [], NOW, 7), [("intake", 0)], [], [])
        doc = self.r.page(data, linked=True)
        self.assertIn('<link rel="stylesheet" href="report.css">', doc)
        self.assertIn('<script src="report.js"></script>', doc)
        self.assertNotIn("function actOn()", doc)
        self.assertNotIn("{{", doc)

        class LinkedPub:
            packaging = "linked"

            def __init__(self):
                self.assets, self.pages = {}, {}

            def put_asset(self, key, data, content_type):
                self.assets[key] = content_type

            def put_page(self, key, body, **kw):
                self.pages[key] = body

            def url(self, key):
                return "https://pages/" + key
        pub = LinkedPub()
        board = FakeBoard([], [], {})
        fc = self.r.factory_config
        orig = fc.integration_client
        fc.integration_client = lambda cfg, kind: {"board": board, "codehost": FakeCodehost({}), "publishing": pub}[kind]
        try:
            with self.assertRaises(self.r.Report):
                self.r.run(type("C", (), {"message": ""})())
        finally:
            fc.integration_client = orig
        self.assertEqual(pub.assets, {"report/report.css": "text/css", "report/report.js": "text/javascript"})
        self.assertIn(b'href="report.css"', pub.pages["report/index.html"])

    def test_a_worker_whose_runs_are_all_older_than_the_window_still_shows_its_last_failure(self):
        asked = {}

        class History:
            @staticmethod
            def run_records(since=None, older=0):
                asked.update(since=since, older=older)
                return [{"worker": "learn", "started_at": iso(days=45), "status": "failed",
                         "error": "boom: token expired"}]
        board = FakeBoard([], [], {})
        doc, _rows = self.r.build({"instance": {"name": "acme"}}, board, None, History, {"learn": {}}, NOW, 30)
        row = self.embedded(doc)["health"][0]
        self.assertEqual((row["state"], row["error"]), ("failing", "boom: token expired"))
        self.assertEqual((asked["since"], asked["older"]), (NOW - dt.timedelta(days=30), self.r.HEALTH_RUNS))

    def test_only_the_teams_text_links_a_pr(self):
        item = {"description": "PR: https://git.example.com/acme/widgets/pull/1", "trusted": False,
                "comments": [{"body": "PR: https://git.example.com/acme/widgets/pull/2", "trusted": False},
                             {"body": "Spec PR: https://git.example.com/acme/widgets/pull/3", "trusted": True},
                             {"body": "PR: https://git.example.com/acme/widgets/pull/4"}]}
        self.assertEqual(self.r.latest_prs(item), {"Spec PR": "https://git.example.com/acme/widgets/pull/3",
                                                   "PR": "https://git.example.com/acme/widgets/pull/4"})

    def test_a_failed_board_read_fails_the_run_in_one_line(self):
        class Down:
            def list(self, **kw):
                raise RuntimeError("no login")
        with self.assertRaises(self.r.Fail) as ctx:
            self.r.build({"instance": {"name": "acme"}}, Down(), None, None, {}, NOW, 30)
        self.assertEqual(ctx.exception.message, "Report NOT RUN: board read failed: RuntimeError: no login")

    def test_a_run_publishes_the_page_and_dry_publishes_nothing(self):
        board = FakeBoard([{"id": "1", "title": "T", "url": "u", "labels": [], "column": "intake",
                            "created": iso(days=2)}], [], {})
        fc = self.r.factory_config
        orig = fc.integration_client
        fc.integration_client = lambda cfg, kind: {"board": board, "codehost": FakeCodehost({})}.get(kind) \
            or orig(cfg, kind)
        try:
            page = self.tmp / "pages" / "report" / "index.html"
            page.unlink(missing_ok=True)  # another test may have published it
            with self.assertRaises(self.r.Report) as ctx:
                self.r.run(type("C", (), {"message": "dry"})())
            self.assertIn("DRY RUN", ctx.exception.message)
            self.assertFalse((self.tmp / "pages" / "report" / "index.html").exists())
            with self.assertRaises(self.r.Report) as ctx:
                self.r.run(type("C", (), {"message": ""})())
            self.assertIn("published", ctx.exception.message)
            data = self.embedded((self.tmp / "pages" / "report" / "index.html").read_text())
            self.assertEqual((data["name"], data["actOn"]), ("acme", []))
            self.assertEqual([c["count"] for c in data["board"]["columns"]], [1, 0, 0, 0])
            self.assertTrue(all(w["state"] == "no runs" for w in data["health"]))
            self.assertIn("This host has no run records yet: health shows no runs.", data["notes"])
        finally:
            fc.integration_client = orig


if __name__ == "__main__":
    unittest.main()
