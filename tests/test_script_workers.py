"""Offline unit tests for the factory's script workers, their integration clients, the local host's
history module and the process runner. Stdlib only; no network, no board, no code host, no publishing.

Each test builds a throwaway bundle on disk (factory-config.json, vocabulary.json, workers.json) and
points FACTORY_BUNDLE at it, then imports the script and client modules from the source tree the way
a rendered bundle lays them out (scripts/lib, scripts/runtime, scripts/integrations/<kind>). The
janitor is tested through its pure functions and one dry run against a fake board.
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WORKERS = {
    "triage": {"claimTtlSecs": 2100, "settings": {"needsInfoDays": 5}},
    "design": {"claimTtlSecs": 2100},
    "implement": {"claimTtlSecs": 3900},
}

COLUMNS = {"intake": "Inbox", "review": "In Review", "release": "Shipping"}
VOCAB = json.loads((ROOT / "workers" / "common" / "vocabulary.json").read_text())

CONFIG = {
    "_schema": 3,
    "instance": {"name": "acme", "title": "Acme Factory", "timezone": "America/Los_Angeles",
                 "header": "🏭 **Acme Factory** · automated agent"},
    "repo": {"slug": "acme-corp/widgets", "defaultBranch": "main"},
    "integrations": {
        "board": {"use": "github", "boardId": "acme-corp/widgets", "scopeLabel": "factory",
                  "columns": {"intake": "Inbox", "review": "In Review", "release": "Shipping"}},
        "codehost": {"use": "github"},
        "publishing": {"use": "s3", "bucket": "b", "baseUrl": "https://pages.example.com/",
                       "region": "us-west-2", "prefix": ""},
        "notifications": {"feed": {"use": "slack-workflow"}},
    },
}


def _make_bundle(tmp: Path) -> Path:
    scripts = tmp / "scripts"
    (scripts / "lib").mkdir(parents=True)
    (scripts / "runtime").mkdir(parents=True)
    for kind in ("board", "publishing", "codehost", "notifications"):
        (scripts / "integrations" / kind).mkdir(parents=True)
    for p in (ROOT / "workers" / "lib").glob("*.py"):
        (scripts / "lib" / p.name).write_text(p.read_text())
    (scripts / "runtime" / "history.py").write_text(
        (ROOT / "hosting" / "local" / "history.py").read_text())
    for kind, name in (("board", "github"), ("publishing", "s3"), ("publishing", "disk"), ("codehost", "github")):
        (scripts / "integrations" / kind / f"{name}.py").write_text(
            (ROOT / "integrations" / kind / name / "client.py").read_text())
    for kind in ("board", "publishing", "codehost", "notifications"):
        (scripts / "integrations" / kind / "interface.py").write_text(
            (ROOT / "integrations" / kind / "interface.py").read_text())
    d = scripts / "workers" / "janitor"
    d.mkdir(parents=True)
    (d / "janitor.py").write_text((ROOT / "workers" / "janitor" / "janitor.py").read_text())
    (tmp / "factory-config.json").write_text(json.dumps(CONFIG))
    (tmp / "vocabulary.json").write_text(json.dumps(VOCAB))
    (tmp / "workers.json").write_text(json.dumps(WORKERS))
    return tmp


class _Fixture(unittest.TestCase):
    """Sets FACTORY_BUNDLE to a fresh bundle and puts its script dirs on sys.path, so the script
    and client modules import exactly as they do in a real bundle."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.bundle = _make_bundle(Path(self._tmp.name))
        self._saved_env = dict(os.environ)
        self._saved_path = list(sys.path)
        self._saved_mods = set(sys.modules)
        os.environ["FACTORY_BUNDLE"] = str(self.bundle)
        os.environ["FACTORY_STATE_DIR"] = str(self.bundle / "state")
        sys.path.insert(0, str(self.bundle / "scripts" / "lib"))
        import runner  # noqa: F401
        runner.bootstrap()

    def tearDown(self):
        sys.path[:] = self._saved_path
        for m in set(sys.modules) - self._saved_mods:
            del sys.modules[m]
        os.environ.clear()
        os.environ.update(self._saved_env)
        self._tmp.cleanup()

    def _load_op(self, rel: str, fn: str):
        import importlib.util
        p = self.bundle / "scripts" / rel
        spec = importlib.util.spec_from_file_location("t_" + fn, p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


# --------------------------------------------------------------------- config + clients

class ConfigTest(_Fixture):
    def test_labels_and_host_module(self):
        import factory_config
        self.assertEqual(factory_config.label("claim"), VOCAB["labels"]["claim"])
        self.assertTrue(factory_config.load()["instance"]["name"] == "acme")
        self.assertEqual(factory_config.load_host_module("history").__name__, "history")

    def test_get_missing_raises(self):
        import factory_config
        with self.assertRaises(factory_config.ConfigError):
            factory_config.get(factory_config.load(), "integrations.board.nope")
        self.assertIsNone(
            factory_config.get(factory_config.load(), "integrations.board.nope", required=False))

    def test_a_bundle_without_a_state_directory_says_so(self):
        import factory_config
        os.environ.pop("FACTORY_STATE_DIR", None)
        (self.bundle / "host.json").write_text(json.dumps({"name": "local", "paths": {}}))
        with self.assertRaises(factory_config.ConfigError) as ctx:
            factory_config.state_dir("janitor")
        self.assertIn("has no paths.stateDir", str(ctx.exception))
        (self.bundle / "host.json").write_text(json.dumps({"paths": {"stateDir": str(self.bundle / "st")}}))
        self.assertEqual(factory_config.state_dir("janitor"), self.bundle / "st" / "janitor")


class PublishingClientTest(_Fixture):
    def test_s3_key_and_url(self):
        import s3
        c = s3.S3Publishing(bucket="b", base_url="https://pages.example.com/", region="r",
                            prefix="fx")
        self.assertEqual(c._key("status/index.html"), "fx/status/index.html")
        self.assertEqual(c.url("status/index.html"),
                         "https://pages.example.com/fx/status/index.html")

    def test_s3_tells_a_missing_page_from_a_denied_read_without_bucket_wide_list(self):
        import types
        from unittest import mock
        import s3

        class ClientError(Exception):
            def __init__(self, response, operation):
                self.response = response
        exceptions = types.SimpleNamespace(ClientError=ClientError)
        botocore = types.SimpleNamespace(exceptions=exceptions)
        mock.patch.dict(sys.modules, {"botocore": botocore, "botocore.exceptions": exceptions}).start()
        self.addCleanup(mock.patch.stopall)

        keys = ["fx/status/index.html.bak"]

        class _S3:
            def get_object(self, **kw):
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

            def list_objects_v2(self, Bucket, Prefix, MaxKeys):
                return {"Contents": [{"Key": k} for k in sorted(keys) if k.startswith(Prefix)][:MaxKeys]}
        c = s3.S3Publishing(bucket="b", base_url="https://pages.example.com/", region="r", prefix="fx")
        c._client = lambda: _S3()
        self.assertEqual(c.get_page("status/index.html"), (None, None))
        keys.append("fx/status/index.html")
        with self.assertRaises(ClientError):
            c.get_page("status/index.html")

        class _Created(_S3):
            reads = 0

            def get_object(self, **kw):
                _Created.reads += 1
                if _Created.reads == 1:
                    return super().get_object(**kw)
                return {"Body": io.BytesIO(b"new"), "ETag": "e1"}
        c._client = lambda: _Created()
        # Written between the denied read and the list: read once more.
        self.assertEqual(c.get_page("status/index.html"), (b"new", "e1"))

        class _Gone(_S3):
            listings = iter([["fx/status/index.html"], []])

            def list_objects_v2(self, Bucket, Prefix, MaxKeys):
                return {"Contents": [{"Key": k} for k in next(_Gone.listings)]}
        c._client = lambda: _Gone()
        # Deleted again before the second read: missing, not an error.
        self.assertEqual(c.get_page("status/index.html"), (None, None))

    def test_disk_writes_conditionally_and_stays_in_its_directory(self):
        import disk
        root = Path(self._tmp.name) / "pages"
        c = disk.DiskPublishing(directory=str(root))
        v1 = c.put_page("status/index.html", b"one", create_only=True)
        with self.assertRaises(disk.Conflict):
            c.put_page("status/index.html", b"two", create_only=True)
        self.assertEqual(c.get_page("status/index.html"), (b"one", v1))
        v2 = c.put_page("status/index.html", b"two", expected=v1)
        with self.assertRaises(disk.Conflict):
            c.put_page("status/index.html", b"three", expected=v1)
        self.assertEqual(c.get_page("status/index.html"), (b"two", v2))
        self.assertEqual(c.get_page("missing.html"), (None, None))
        self.assertEqual(c.url("status/index.html"), root.resolve().as_uri() + "/status/index.html")
        with self.assertRaises(ValueError):
            c.put_asset("../escape.txt", b"x", "text/plain")



# --------------------------------------------------------------------- janitor

class _FakeBoard:
    """A board client double for the janitor: canned open items and comments, recorded writes."""

    def __init__(self, items, comments=None):
        self._items = items
        self._comments = comments or {}
        self.writes = []

    def list(self, status="Open", extra=None):
        return list(self._items)

    def get(self, item, comment_limit=50):
        return {"comments": self._comments.get(item, [])}

    def update(self, item_id, **kw):
        self.writes.append(("update", item_id, kw))
        return {}

    def close(self, item_id, reason_body, status="closed"):
        self.writes.append(("close", item_id, reason_body))


def _iso_ago(minutes=0, days=0):
    import datetime as dt
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes, days=days)
    return t.isoformat()


class JanitorTest(_Fixture):
    def setUp(self):
        super().setUp()
        self.jan = self._load_op("workers/janitor/janitor.py", "reap")
        import factory_config
        self.rc = self.jan._Cfg(factory_config.load())

    def test_stage_from_labels(self):
        rc = self.rc
        self.assertEqual(rc.stage({"labels": ["Fast Track"]}), "implement")
        self.assertEqual(rc.stage({"labels": ["Spec"]}), "design")
        self.assertEqual(rc.stage({"labels": []}), "triage")
        self.assertEqual(rc.stage({"labels": ["Groomed"]}), "implement")

    def test_ttls_come_from_each_step(self):
        self.assertEqual(self.rc.ttl_min, {"triage": 35, "design": 35, "implement": 65})
        self.assertEqual(self.rc.needs_info_days, 5)

    def test_a_disabled_step_takes_the_longest_ttl(self):
        import factory_config
        (self.bundle / "workers.json").write_text(json.dumps({"implement": {"claimTtlSecs": 3900}}))
        rc = self.jan._Cfg(factory_config.load())
        self.assertEqual(rc.ttl_min, {"triage": 65, "design": 65, "implement": 65})

    def test_stale_claims_respects_ttl(self):
        items = [
            {"id": "A", "labels": ["Working", "Spec"], "updated": _iso_ago(minutes=50)},  # design ttl 35 -> stale
            {"id": "B", "labels": ["Working", "Spec"], "updated": _iso_ago(minutes=20)},  # fresh
            {"id": "C", "labels": ["Working", "Fast Track"], "updated": _iso_ago(minutes=50)},  # implement ttl 65
        ]
        due = self.jan.stale_claims(self.rc, items)
        self.assertEqual([r["item"]["id"] for r in due], ["A"])

    def test_the_needs_info_clock_starts_at_triages_newest_post(self):
        h = self.rc.header
        items = [
            {"id": "N", "labels": ["Needs Info"],
             "comments": [{"body": f"{h}\nRun: `acme:triage:a`\n\nUnder-specified.", "created": _iso_ago(days=6)},
                          {"body": "I'll add detail soon", "created": _iso_ago(days=1)}]},
            {"id": "M", "labels": ["Needs Info"],
             "comments": [{"body": "a person's comment", "created": _iso_ago(days=30)}]},
            {"id": "T", "labels": ["Needs Info"],
             "comments": [{"body": f"{h}\nRun: `acme:triage:a`\n\nUnder-specified.", "created": _iso_ago(days=9)},
                          {"body": f"{h}\nRun: `acme:triage:b`\n\nStill missing the steps.", "created": _iso_ago(days=2)}]},
            {"id": "O", "labels": ["Needs Info"],
             "comments": [{"body": f"{h}\nRun: `acme:design:a`\n\nThe spec is ready.", "created": _iso_ago(days=8)}]},
        ]
        due, unread = self.jan.stale_needs_info(self.rc, _FakeBoard(items), items)
        self.assertEqual(([r["item"]["id"] for r in due], unread), (["N"], []))

    def test_a_post_whose_run_id_names_no_worker_starts_the_clock_when_none_names_triage(self):
        h = self.rc.header
        items = [{"id": id_, "labels": ["Needs Info"], "comments": [{"body": body, "created": _iso_ago(days=days)}]}
                 for id_, days, body in (("unknown", 9, f"{h}\nRun: `unknown`\n\nUnder-specified."),
                                         ("old-id", 8, f"{h}\nRun: `run:job-7`\n\nUnder-specified."),
                                         ("no-run", 7, f"{h}\n\nUnder-specified."))]
        due, _unread = self.jan.stale_needs_info(self.rc, _FakeBoard(items), items)
        self.assertEqual([r["item"]["id"] for r in due], ["unknown", "old-id", "no-run"])

    def _reap(self, board, message=""):
        import factory_config
        orig = factory_config.integration_client
        factory_config.integration_client = lambda cfg, kind: board if kind == "board" else orig(cfg, kind)
        try:
            self.jan.reap(type("Ctx", (), {"message": message})())
        finally:
            factory_config.integration_client = orig

    def test_an_unreadable_needs_info_item_fails_the_run_after_the_claims_are_released(self):
        class _Down(_FakeBoard):
            def get(self, item, comment_limit=50):
                raise RuntimeError("HTTP 502")
        board = _Down([{"id": "A", "labels": ["Working", "Spec"], "updated": _iso_ago(minutes=300)},
                       {"id": "N", "labels": ["Needs Info"]}])
        with self.assertRaises(self.jan.Fail) as cm:
            self._reap(board)
        self.assertEqual([(w[0], w[1]) for w in board.writes], [("update", "A")])
        self.assertIn("reclaimed 1 abandoned claim(s) (1 design); could not read the comments of 1 needs-info "
                      "item(s), left as they are: N: RuntimeError: HTTP 502", cm.exception.message)

    def test_a_run_that_did_not_do_its_job_fails(self):
        """A config error and a board read past the scope guard end the run as failed, so its record
        and the report's health do not show the janitor as working."""
        board = _FakeBoard([{"id": str(n), "labels": []} for n in range(201)])
        with self.assertRaises(self.jan.Fail) as cm:
            self._reap(board)
        self.assertIn("Janitor ABORTED: board read returned 201 open items", cm.exception.message)
        self.assertEqual(board.writes, [])
        (self.bundle / "vocabulary.json").unlink()
        with self.assertRaises(self.jan.Fail) as cm:
            self._reap(board)
        self.assertIn("Janitor NOT RUN: config error", cm.exception.message)

    def test_an_outsiders_copy_of_the_header_does_not_start_the_clock(self):
        h = self.rc.header
        spoof = [{"body": f"{h}\nRun: `acme:triage:a`\n\nx", "created": _iso_ago(days=30), "trusted": False}]
        self.assertIsNone(self.jan.asked_at(self.rc, spoof))

    def test_dry_run_writes_nothing(self):
        import factory_config
        items = [{"id": "A", "labels": ["Working", "Spec"], "updated": _iso_ago(minutes=300)}]
        board = _FakeBoard(items)

        # Patch the janitor's seams: config and the board client factory.
        self.jan.factory_config = factory_config
        orig_client = factory_config.integration_client
        factory_config.integration_client = lambda cfg, kind: board if kind == "board" else orig_client(cfg, kind)
        try:
            with self.assertRaises(self.jan.Report) as cm:
                self.jan.reap(type("Ctx", (), {"message": "dry"})())
            self.assertIn("DRY RUN", str(cm.exception))
            self.assertIn("WOULD unclaim A stage=design", str(cm.exception))
            self.assertEqual(board.writes, [])
        finally:
            factory_config.integration_client = orig_client

    def test_a_failed_board_read_fails_the_run_in_one_line(self):
        import factory_config

        class _Down:
            def list(self, **kw):
                raise RuntimeError("gh: To get started with GitHub CLI, please run: gh auth login")
        orig_client = factory_config.integration_client
        factory_config.integration_client = lambda cfg, kind: _Down() if kind == "board" else orig_client(cfg, kind)
        try:
            with self.assertRaises(self.jan.Fail) as cm:
                self.jan.reap(type("C", (), {"message": "dry"})())
            self.assertIn("Janitor NOT RUN: board read failed: RuntimeError: gh: To get started", cm.exception.message)
        finally:
            factory_config.integration_client = orig_client


class HistoryTest(_Fixture):
    def setUp(self):
        super().setUp()
        self.state = self.bundle / "state"
        os.environ["FACTORY_STATE_DIR"] = str(self.state)

    def tearDown(self):
        os.environ.pop("FACTORY_STATE_DIR", None)
        super().tearDown()

    def test_a_bundle_whose_host_names_no_state_directory_says_so(self):
        import history
        os.environ.pop("FACTORY_STATE_DIR", None)
        (self.bundle / "host.json").write_text(json.dumps({"name": "local", "paths": {}}))
        with self.assertRaises(RuntimeError) as ctx:
            history.run_records()
        self.assertIn("has no paths.stateDir; render the bundle again", str(ctx.exception))

    def test_records_round_trip(self):
        import history
        self.assertEqual(history.run_records(), [])
        history.write_record({"worker": "design", "start": "2026-10-01T10:00:00Z", "end": "2026-10-01T10:05:00Z",
                              "status": "succeeded", "duration_ms": 300000, "summary": "worked P123", "credits": 7})
        recs = history.run_records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["worker"], "design")
        self.assertEqual(recs[0]["credits"], 7)
        history.write_record({"worker": "learn", "start": "2026-10-01T11:00:00Z", "status": "timed out",
                              "implementation": "agent"})
        unreported = next(r for r in history.run_records() if r["worker"] == "learn")
        self.assertEqual((unreported["credits"], unreported["implementation"]), (None, "agent"))

    def test_records_older_than_since_are_not_read(self):
        import datetime as dt
        import history
        for start in ("2026-10-01T10:00:00Z", "2026-10-03T10:00:00Z"):
            history.write_record({"worker": "design", "start": start, "status": "succeeded"})
        old = self.state / "runs" / "design" / "2026-10-01T10-00-00Z.json"
        old.write_text(json.dumps({"worker": "design", "start": "read-me", "status": "succeeded"}))
        since = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)
        self.assertEqual([r["started_at"] for r in history.run_records(since=since)], ["2026-10-03T10:00:00Z"])
        self.assertEqual([r["started_at"] for r in history.run_records()], ["read-me", "2026-10-03T10:00:00Z"])

    def test_a_workers_newest_older_records_come_with_the_window_when_asked(self):
        import datetime as dt
        import history
        for worker, start in (("design", "2026-09-01T10:00:00Z"), ("design", "2026-09-02T10:00:00Z"),
                              ("design", "2026-10-03T10:00:00Z"), ("learn", "2026-08-01T10:00:00Z")):
            history.write_record({"worker": worker, "start": start, "status": "succeeded"})
        since = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)
        got = [(r["worker"], r["started_at"]) for r in history.run_records(since=since, older=1)]
        self.assertEqual(got, [("design", "2026-09-02T10:00:00Z"), ("design", "2026-10-03T10:00:00Z"),
                               ("learn", "2026-08-01T10:00:00Z")])

    def test_lease_is_single_flight_and_expires(self):
        import history
        self.assertTrue(history.acquire_lease("triage", 60, "run-a"))
        self.assertFalse(history.acquire_lease("triage", 60, "run-b"))
        history.release_lease("triage", "run-b")  # not the holder: no effect
        self.assertFalse(history.acquire_lease("triage", 60, "run-b"))
        history.release_lease("triage", "run-a")
        self.assertTrue(history.acquire_lease("triage", -1, "run-c"))  # already expired
        self.assertTrue(history.acquire_lease("triage", 60, "run-d"))  # takes over the expired lease

    def test_two_runs_racing_for_an_expired_lease_do_not_both_take_it(self):
        import threading
        import history
        self.assertTrue(history.acquire_lease("triage", -1, "dead"))
        start, won = threading.Barrier(8), []

        def contend(i):
            start.wait()
            if history.acquire_lease("triage", 60, f"run-{i}"):
                won.append(i)
        threads = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(won), 1)



class S3HistoryTest(_Fixture):
    def _history(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("s3_history", ROOT / "hosting" / "aws-cdk" / "history.py")
        history = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(history)
        return history

    def test_a_record_counts_for_the_worker_in_its_key_and_a_bad_one_is_skipped(self):
        history = self._history()
        objects = {"runs/triage/2026-10-01T10:00:00Z.json": b'{"worker": "implement", "start": "2026-10-01T10:00:00Z"}',
                   "runs/triage/2026-10-02T10:00:00Z.json": b'{"duration_ms": "lots"}',
                   "runs/triage/2026-10-03T10:00:00Z.json": b'["not", "a", "record"]'}

        class _S3:
            def list_objects_v2(self, **kw):
                return {"Contents": [{"Key": k} for k in sorted(objects)], "IsTruncated": False}

            def get_object(self, Bucket, Key):
                return {"Body": io.BytesIO(objects[Key])}
        history._client = lambda settings: _S3()
        self.assertEqual([(r["worker"], r["started_at"]) for r in history.run_records()],
                         [("triage", "2026-10-01T10:00:00Z")])

    def test_a_lease_that_could_not_come_from_a_run_does_not_hold_the_worker(self):
        history = self._history()
        for expires in ("9999-01-01T00:00:00+00:00", "2026-01-01T00:00:00", 42, None):
            bodies = {"held": json.dumps({"runId": "x", "expires": expires})}
            taken = []

            class _S3:
                exceptions = type("E", (), {"ClientError": type("ClientError", (Exception,), {})})

                def put_object(self, **kw):
                    if "IfNoneMatch" in kw:
                        err = self.exceptions.ClientError()
                        err.response = {"Error": {"Code": "PreconditionFailed"}}
                        raise err
                    taken.append(kw["IfMatch"])

                def get_object(self, **kw):
                    return {"Body": io.BytesIO(bodies["held"].encode()), "ETag": "e"}
            history._client = lambda settings: _S3()
            self.assertTrue(history.acquire_lease("triage", 3600, "run-a"), expires)
            self.assertEqual(taken, ["e"], expires)

    def test_records_older_than_since_are_not_fetched(self):
        import datetime as dt
        import importlib.util
        import io
        spec = importlib.util.spec_from_file_location("s3_history", ROOT / "hosting" / "aws-cdk" / "history.py")
        history = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(history)
        objects = {f"runs/design/{start}.json": {"start": start}
                   for start in ("2026-09-30T10:00:00Z", "2026-10-01T10:00:00Z", "2026-10-03T10:00:00Z")}
        objects["runs/design/lock"] = {}
        fetched = []

        class _S3:
            def list_objects_v2(self, **kw):
                return {"Contents": [{"Key": k} for k in sorted(objects)], "IsTruncated": False}

            def get_object(self, Bucket, Key):
                fetched.append(Key)
                return {"Body": io.BytesIO(json.dumps(objects[Key]).encode())}
        history._client = lambda settings: _S3()
        since = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)
        self.assertEqual([r["started_at"] for r in history.run_records(since=since)], ["2026-10-03T10:00:00Z"])
        self.assertEqual(fetched, ["runs/design/2026-10-03T10:00:00Z.json"])
        self.assertEqual([r["started_at"] for r in history.run_records(since=since, older=1)],
                         ["2026-10-01T10:00:00Z", "2026-10-03T10:00:00Z"])
        self.assertEqual(len(history.run_records()), 3)

    def test_a_lease_released_between_the_refused_create_and_the_read_is_a_skip_not_a_failure(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("s3_history", ROOT / "hosting" / "aws-cdk" / "history.py")
        history = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(history)

        class ClientError(Exception):
            def __init__(self, code):
                self.response = {"Error": {"Code": code}}

        class _S3:
            exceptions = type("E", (), {"ClientError": ClientError})

            def put_object(self, **kw):
                raise ClientError("PreconditionFailed")

            def get_object(self, **kw):
                raise ClientError(self.code)

            def list_objects_v2(self, **kw):
                return {"Contents": self.listed}
        s3 = _S3()
        history._client = lambda settings: s3
        for code in ("NoSuchKey", "AccessDenied"):
            s3.code, s3.listed = code, []
            self.assertFalse(history.acquire_lease("triage", 60, "run-a"), code)
        # A denied read of a lease that does exist is a real error.
        s3.listed = [{"Key": "runs/triage/lock"}]
        with self.assertRaises(ClientError):
            history.acquire_lease("triage", 60, "run-a")
        # Taken by another run between the denied read and the list: read it once more, and it is held.
        reads = []

        def get_object(**kw):
            reads.append(kw)
            if len(reads) == 1:
                raise ClientError("AccessDenied")
            return {"Body": io.BytesIO(json.dumps({"runId": "run-b", "expires": "2999-01-01T00:00:00+00:00"}).encode()),
                    "ETag": "e"}
        s3.get_object = get_object
        self.assertFalse(history.acquire_lease("triage", 60, "run-a"))
        self.assertEqual(len(reads), 2)
        # Released again before the second read: missing, not an error.
        lists = iter([[{"Key": "runs/triage/lock"}], []])

        def denied(**kw):
            raise ClientError("AccessDenied")
        s3.get_object = denied
        s3.list_objects_v2 = lambda **kw: {"Contents": next(lists)}
        self.assertFalse(history.acquire_lease("triage", 60, "run-a"))


# --------------------------------------------------------------------- runner

class RunnerTest(_Fixture):
    def test_process_ctx_signals_map_to_exit_codes(self):
        import contextlib
        import io
        import runner

        def skip(ctx):
            raise runner.Skip()

        def report(ctx):
            raise runner.Report("done")

        def boom(ctx):
            raise ValueError("x")

        def fail(ctx):
            raise runner.Fail("board read failed: no login")
        self.assertEqual(runner.main(skip), 0)
        self.assertEqual(runner.main(report), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(boom), 1)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(runner.main(fail), 1)
        self.assertEqual(err.getvalue(), "board read failed: no login\n")

    def test_the_first_argument_is_the_entrys_message(self):
        import runner
        seen = []
        with mock.patch.object(sys, "argv", ["janitor.py", "dry"]):
            self.assertEqual(runner.main(lambda ctx: seen.append(ctx.message)), 0)
        self.assertEqual(seen, ["dry"])


if __name__ == "__main__":
    unittest.main()
