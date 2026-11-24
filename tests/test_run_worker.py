import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime" / "render"))
from render import Renderer  # noqa: E402

ACME = ROOT / "examples" / "acme"


def _bundle(host: str) -> Path:
    out = Path(tempfile.mkdtemp()) / "bundle"
    Renderer(ROOT, ACME, ACME / "hosts" / f"{host}.json").render(out)
    return out


def _load_runner(bundle: Path):
    import os
    os.environ["FACTORY_BUNDLE"] = str(bundle)
    spec = importlib.util.spec_from_file_location("run_worker", bundle / "scripts" / "runtime" / "run_worker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RunWorkerTest(unittest.TestCase):
    def test_every_host_bundle_has_the_runner_and_a_history_module(self):
        for host in ("local", "aws-cdk"):
            b = _bundle(host)
            self.assertTrue((b / "scripts" / "runtime" / "run_worker.py").exists(), host)
            self.assertTrue((b / "scripts" / "runtime" / "history.py").exists(), host)
            self.assertEqual(json.loads((b / "host.json").read_text())["runtime"], host)

    def test_agent_worker_runs_kiro_cli_headless_on_its_prompt(self):
        b = _bundle("local")
        rw = _load_runner(b)
        workers = json.loads((b / "workers.json").read_text())
        host = json.loads((b / "host.json").read_text())
        argv = rw.command("triage", workers["triage"], host)
        self.assertEqual(argv[:7], ["kiro-cli", "chat", "--v3", "--no-interactive", "--trust-all-tools",
                                    "--agent", "kiro-factory-triage"])
        self.assertIn(str(b / "prompts" / "triage.md"), argv[-1])
        self.assertNotIn("started for", argv[-1])
        focused = rw.command("triage", workers["triage"], host, item="P-7")
        self.assertIn("started for P-7: act on that item only", focused[-1])

    def test_the_kiro_cli_harness_installs_its_agent_under_home(self):
        import os
        b = _bundle("local")
        rw = _load_runner(b)
        home, old = tempfile.mkdtemp(), os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            rw.harness({"use": "kiro-cli"}).install({"use": "kiro-cli"}, b, "triage")
        finally:
            os.environ["HOME"] = old
        self.assertEqual((Path(home) / ".kiro" / "agents" / "kiro-factory-triage.json").read_bytes(),
                         (b / "agents" / "triage.json").read_bytes())

    def test_the_kiro_cli_harness_installs_under_kiro_home_when_the_host_sets_it(self):
        b = _bundle("local")
        rw = _load_runner(b)
        kiro_home = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"KIRO_HOME": kiro_home, "HOME": tempfile.mkdtemp()}):
            rw.harness({"use": "kiro-cli"}).install({"use": "kiro-cli"}, b, "triage")
            self.assertFalse((Path(os.environ["HOME"]) / ".kiro").exists())
        self.assertTrue((Path(kiro_home) / "agents" / "kiro-factory-triage.json").is_file())

    def test_script_worker_runs_its_entry_file(self):
        b = _bundle("local")
        rw = _load_runner(b)
        workers = json.loads((b / "workers.json").read_text())
        host = json.loads((b / "host.json").read_text())
        argv = rw.command("janitor", workers["janitor"], host)
        self.assertEqual(argv[-1], str(b / "scripts" / "workers" / "janitor" / "janitor.py"))
        self.assertTrue(Path(argv[-1]).exists())

    def test_non_python_entry_runs_as_an_executable(self):
        b = _bundle("local")
        rw = _load_runner(b)
        host = json.loads((b / "host.json").read_text())
        self.assertEqual(rw.command("x", {"implementation": "script", "entry": "instance/x.sh"}, host),
                         [str(b / "scripts" / "instance" / "x.sh")])

    def test_dry_run_runs_nothing_and_says_when_the_bundle_is_not_where_it_was_rendered_for(self):
        b = _bundle("local")
        rw = _load_runner(b)
        host = json.loads((b / "host.json").read_text())
        host["paths"]["stateDir"] = str(b.parent / "state")
        (b / "host.json").write_text(json.dumps(host))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rw.run("design", dry_run=True), 0)
        self.assertFalse((b.parent / "state").exists())
        self.assertIn("was rendered to run from /srv/acme-factory/bundle", json.loads(out.getvalue())["cannotRun"])


class FakeHistory:
    def __init__(self, lease=True, lease_error=None, write_error=None):
        self.lease, self.lease_error, self.write_error = lease, lease_error, write_error
        self.records, self.released = [], []

    def acquire_lease(self, worker, ttl, run_id):
        if self.lease_error:
            raise self.lease_error
        return self.lease

    def write_record(self, record):
        if self.write_error:
            raise self.write_error
        self.records.append(record)

    def release_lease(self, worker, run_id):
        self.released.append(worker)


class RunRecordTest(unittest.TestCase):
    """Every run past the lease gets exactly one record, and the exit status says whether it did."""

    def setUp(self):
        import os
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = tempfile.mkdtemp()
        self.bundle = _bundle("local")
        self.set_checkout(tempfile.mkdtemp())
        self.rw = _load_runner(self.bundle)

    def set_checkout(self, path: str) -> None:
        """Point the bundle's host at a checkout, and at state and worktree directories of its own."""
        host = json.loads((self.bundle / "host.json").read_text())
        host["paths"].update(bundle=str(self.bundle), checkout=path, stateDir=str(self.bundle.parent / "state"),
                             worktreeRoot=str(self.bundle.parent / "worktrees"))
        (self.bundle / "host.json").write_text(json.dumps(host))

    def tearDown(self):
        import os
        os.environ["HOME"] = self._home
        sys.modules.pop("history", None)

    def _run(self, hist, argv=None):
        sys.modules["history"] = hist
        self.rw.command = lambda *_: argv or [sys.executable, "-c", "print('done')"]
        return self.rw.main(["janitor"])

    def test_success_writes_one_record_and_releases_the_lease(self):
        hist = FakeHistory()
        self.assertEqual(self._run(hist), 0)
        self.assertEqual([r["status"] for r in hist.records], ["succeeded"])
        self.assertEqual(hist.released, ["janitor"])

    def test_an_item_run_passes_the_item_and_records_it(self):
        hist = FakeHistory()
        sys.modules["history"] = hist
        self.rw.command = lambda *_: [sys.executable, "-c", "import os; print(os.environ['FACTORY_ITEM'])"]
        self.assertEqual(self.rw.main(["janitor", "--item", "P-7"]), 0)
        self.assertEqual(hist.records[0]["item"], "P-7")
        self.assertEqual(hist.records[0]["summary"], "P-7")

    def _variables_a_run_sees(self, **host_fields) -> str:
        host = json.loads((self.bundle / "host.json").read_text())
        host.update(host_fields)
        (self.bundle / "host.json").write_text(json.dumps(host))
        names = ("KEPT_TOKEN", "OTHER_SECRET", "LC_ALL", "PATH", "FACTORY_RUN_ID", "FACTORY_HOST_X", "KIRO_HOME")
        show = f"import os; print(','.join(k for k in {names!r} if k in os.environ))"
        hist = FakeHistory()
        with mock.patch.dict(os.environ, {"KEPT_TOKEN": "a", "OTHER_SECRET": "b", "LC_ALL": "C",
                                          "FACTORY_HOST_X": "c", "KIRO_HOME": tempfile.mkdtemp()}):
            self.assertEqual(self._run(hist, [sys.executable, "-c", show]), 0)
        return hist.records[0]["summary"]

    def test_a_run_keeps_the_whole_environment_when_the_host_lists_none(self):
        self.assertEqual(self._variables_a_run_sees(),
                         "KEPT_TOKEN,OTHER_SECRET,LC_ALL,PATH,FACTORY_RUN_ID,FACTORY_HOST_X,KIRO_HOME")

    def test_a_host_that_lists_env_keeps_only_those_variables_and_what_a_run_needs(self):
        """What a shell needs, the FACTORY_* the runner and host module read, and the harness's ENV."""
        self.assertEqual(self._variables_a_run_sees(env=["KEPT_TOKEN"]),
                         "KEPT_TOKEN,LC_ALL,PATH,FACTORY_RUN_ID,FACTORY_HOST_X,KIRO_HOME")
        self.assertEqual(self._variables_a_run_sees(env=[]), "LC_ALL,PATH,FACTORY_RUN_ID,FACTORY_HOST_X,KIRO_HOME")

    def _failing_run_printing(self, text: str, env: dict) -> dict:
        prog = f"import sys; print({text!r}); sys.stderr.write({text!r}); raise SystemExit(3)"
        hist = FakeHistory()
        with mock.patch.dict(os.environ, env):
            self.assertEqual(self._run(hist, [sys.executable, "-c", prog]), 1)
        return hist.records[0]

    def test_a_record_masks_secret_variables_and_token_shapes_in_every_field(self):
        token = "ghs_" + "A" * 36
        rec = self._failing_run_printing(f"key s3cr3t-kiro-value gh {token} author bot-person",
                                         {"KIRO_API_KEY": "s3cr3t-kiro-value", "GIT_AUTHOR_NAME": "bot-person"})
        for field in ("trace", "summary", "error"):
            self.assertNotIn("s3cr3t-kiro-value", rec[field])
            self.assertNotIn(token, rec[field])
            self.assertIn("author bot-person", rec[field])

    def test_a_secret_cut_by_the_tail_leaves_no_fragment(self):
        secret = "Zq" * 20
        text = secret + "x" * (self.rw.TAIL_CHARS - len(secret) // 2)
        rec = self._failing_run_printing(text, {"KIRO_API_KEY": secret})
        self.assertNotIn(secret[len(secret) // 2:], rec["error"])

    def _with_secret_file(self, content: bytes) -> None:
        hook = Path(tempfile.mkdtemp()) / "hook"
        hook.write_bytes(content)
        cfg_path = self.bundle / "factory-config.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["integrations"]["notifications"] = {"feed": {"use": "slack-workflow", "webhookFile": str(hook)}}
        cfg_path.write_text(json.dumps(cfg))

    def test_a_record_masks_what_a_configured_secret_file_holds_as_written_escaped_or_encoded(self):
        self._with_secret_file(b"https://hooks.example.com/trigger/abc123\n")
        rec = self._failing_run_printing(
            'a https://hooks.example.com/trigger/abc123 b {"url": "https:\\/\\/hooks.example.com\\/trigger\\/abc123"} '
            "c https%3A%2F%2Fhooks.example.com%2Ftrigger%2Fabc123", {})
        self.assertEqual(rec["summary"], 'a *** b {"url": "***"} c ***')

    def test_a_token_after_an_underscore_is_masked(self):
        rec = self._failing_run_printing("tok_ghp_" + "B" * 36, {})
        self.assertEqual(rec["summary"], "tok_***")

    def test_the_password_in_a_url_is_masked(self):
        text = ("postgres://app:s3cretpw@db.example.com/app redis://:r3dispw@cache ftp://u:pa,ss'0123@host "
                'https://example.com/x {"url":"https://h:443","owner":"a@b.example"}')
        rec = self._failing_run_printing(text, {})
        self.assertEqual(rec["summary"], "postgres://***@db.example.com/app redis://***@cache ftp://***@host "
                                         'https://example.com/x {"url":"https://h:443","owner":"a@b.example"}')

    def test_a_secret_file_in_a_list_is_masked_and_a_fifo_is_not_read(self):
        hook = Path(tempfile.mkdtemp()) / "hook"
        hook.write_text("list-secret-0123456789")
        fifo = Path(tempfile.mkdtemp()) / "fifo"
        os.mkfifo(fifo)
        cfg = {"integrations": {"notifications": {"feeds": [{"webhookFile": str(hook)}, {"webhookFile": str(fifo)}]}}}
        self.assertEqual(self.rw.run_secrets({}, cfg), ("list-secret-0123456789",))

    def test_an_unreadable_secret_file_does_not_stop_the_run(self):
        self._with_secret_file(b"\xff\xfe\x00\x81binary")
        self.assertEqual(self._run(FakeHistory()), 0)

    def test_a_held_lease_writes_nothing(self):
        hist = FakeHistory(lease=False)
        self.assertEqual(self._run(hist), self.rw.LEASE_HELD)
        self.assertEqual(hist.records, [])
        self.assertEqual(hist.released, [])

    def test_an_agent_with_a_missing_checkout_is_recorded_as_failed_and_runs_nothing(self):
        missing = str(Path(tempfile.mkdtemp()) / "no-clone")
        self.set_checkout(missing)
        hist = FakeHistory()
        sys.modules["history"] = hist
        marker = Path(tempfile.mkdtemp()) / "ran"
        self.rw.command = lambda *_: [sys.executable, "-c", f"open({str(marker)!r}, 'w')"]
        self.assertEqual(self.rw.main(["triage"]), 1)
        self.assertFalse(marker.exists())
        self.assertEqual(hist.records[0]["status"], "failed")
        self.assertIn(f"the checkout {missing}", hist.records[0]["error"])
        self.assertEqual(hist.released, ["triage"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.rw.main(["triage", "--dry-run"]), 0)
        self.assertIn("does not exist", json.loads(out.getvalue())["cannotRun"])

    def test_a_missing_checkout_does_not_hide_another_failure(self):
        self.set_checkout(str(Path(tempfile.mkdtemp()) / "no-clone"))
        hist = FakeHistory(lease_error=ConnectionError("state store down"))
        sys.modules["history"] = hist
        self.assertEqual(self.rw.main(["triage"]), 1)
        self.assertIn("ConnectionError: state store down", hist.records[0]["error"])

    def test_a_script_worker_ignores_the_checkout(self):
        self.set_checkout(str(Path(tempfile.mkdtemp()) / "no-clone"))
        self.assertEqual(self._run(FakeHistory()), 0)

    def test_a_failing_worker_is_recorded_as_failed(self):
        hist = FakeHistory()
        self.assertEqual(self._run(hist, [sys.executable, "-c", "raise SystemExit(5)"]), 1)
        self.assertEqual([(r["status"], r["exitCode"]) for r in hist.records], [("failed", 5)])

    def test_stderr_is_an_error_only_when_the_worker_fails(self):
        logs = "import sys; sys.stderr.write('startup log\\n')"
        ok, bad = FakeHistory(), FakeHistory()
        self._run(ok, [sys.executable, "-c", logs])
        self._run(bad, [sys.executable, "-c", logs + "; raise SystemExit(5)"])
        self.assertEqual(ok.records[0]["error"], "")
        self.assertIn("startup log", bad.records[0]["error"])

    def test_a_timed_out_worker_keeps_its_partial_output(self):
        workers_json = self.rw.BUNDLE / "workers.json"
        workers = json.loads(workers_json.read_text())
        workers["janitor"]["runTimeoutSecs"] = 2
        workers_json.write_text(json.dumps(workers))
        hist = FakeHistory()
        slow = "import sys, time; print('picked an item', flush=True); sys.stderr.write('warn\\n'); sys.stderr.flush(); time.sleep(30)"
        self.assertEqual(self._run(hist, [sys.executable, "-c", slow]), 1)
        rec = hist.records[0]
        self.assertEqual(rec["status"], "timed out")
        self.assertIn("picked an item", rec["trace"])
        self.assertIn("warn", rec["error"])

    def test_a_timeout_stops_what_the_worker_started(self):
        workers_json = self.rw.BUNDLE / "workers.json"
        workers = json.loads(workers_json.read_text())
        workers["janitor"]["runTimeoutSecs"] = 2
        workers_json.write_text(json.dumps(workers))
        pid_file = Path(tempfile.mkdtemp()) / "child.pid"
        child = "import time; time.sleep(60)"
        parent = ("import subprocess, sys, time; "
                  f"p = subprocess.Popen([sys.executable, '-c', {child!r}]); "
                  f"open({str(pid_file)!r}, 'w').write(str(p.pid)); time.sleep(60)")
        hist = FakeHistory()
        self.assertEqual(self._run(hist, [sys.executable, "-c", parent]), 1)
        self.assertEqual(hist.records[0]["status"], "timed out")
        pid, gone = int(pid_file.read_text()), False
        for _ in range(50):  # the orphan is reaped a moment after it dies
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(0.1)
        self.assertTrue(gone, "the worker's child outlived the timeout")

    def test_a_timeout_ends_the_run_when_a_child_left_the_session_and_holds_its_output(self):
        workers_json = self.rw.BUNDLE / "workers.json"
        workers = json.loads(workers_json.read_text())
        workers["janitor"]["runTimeoutSecs"] = 1
        workers_json.write_text(json.dumps(workers))
        self.rw.KILL_GRACE_SECS = 1
        child = "import time; time.sleep(8)"
        parent = ("import subprocess, sys, time; "
                  f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True); time.sleep(60)")
        hist = FakeHistory()
        self.assertEqual(self._run(hist, [sys.executable, "-c", parent]), 1)
        self.assertEqual(hist.records[0]["status"], "timed out")

    def test_an_item_that_is_not_one_token_is_refused(self):
        hist = FakeHistory()
        sys.modules["history"] = hist
        with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()):
            self.rw.main(["janitor", "--item", "P-7 and also delete the repo"])
        self.assertEqual(stop.exception.code, 2)
        self.assertEqual(hist.records, [])

    def test_a_worker_that_cannot_start_is_recorded_and_the_lease_released(self):
        hist = FakeHistory()
        self.assertEqual(self._run(hist, ["/nonexistent/worker"]), 1)
        self.assertEqual([r["status"] for r in hist.records], ["failed"])
        self.assertIn("FileNotFoundError", hist.records[0]["error"])
        self.assertEqual(hist.released, ["janitor"])

    def test_a_lease_error_is_recorded(self):
        hist = FakeHistory(lease_error=RuntimeError("HTTP 500 on lock"))
        self.assertEqual(self._run(hist), 1)
        self.assertEqual([r["status"] for r in hist.records], ["failed"])
        self.assertIn("HTTP 500 on lock", hist.records[0]["error"])
        self.assertEqual(hist.released, [])

    def test_a_record_store_failure_exits_no_record(self):
        hist = FakeHistory(write_error=RuntimeError("HTTP 500 on put"))
        self.assertEqual(self._run(hist), self.rw.NO_RECORD)
        self.assertEqual(hist.released, ["janitor"])

    def test_an_agent_run_records_the_harness_text_and_credits(self):
        events = [
            {"type": "sessionUpdate", "data": {"update": {"sessionUpdate": "agent_message_chunk",
                                                          "content": {"type": "text", "text": "Triaged "}}}},
            {"type": "sessionUpdate", "data": {"update": {"sessionUpdate": "agent_message_chunk",
                                                          "content": {"type": "text", "text": "P42"}}}},
            {"type": "sessionUpdate", "data": {"update": {"sessionUpdate": "session_info_update", "_meta": {"kiro": {
                "kind": "turn_completion",
                "promptTurnSummaries": [{"unit": "credit", "usage": 1.25}, {"unit": "other", "usage": 9}]}}}}},
            {"type": "runFinished", "data": {"status": "success", "finalText": "Triaged P42"}},
        ]
        script = "import sys\nfor l in sys.argv[1:]: print(l)"
        hist = FakeHistory()
        sys.modules["history"] = hist
        self.rw.command = lambda *_: [sys.executable, "-c", script, *map(json.dumps, events)]
        self.assertEqual(self.rw.main(["triage"]), 0)
        rec = hist.records[0]
        self.assertEqual((rec["summary"], rec["credits"]), ("Triaged P42", 1.25))
        self.assertNotIn("sessionUpdate", rec["trace"])

    def test_a_script_run_records_no_credits(self):
        hist = FakeHistory()
        self._run(hist)
        self.assertNotIn("credits", hist.records[0])


class KiroCliResultTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "kiro_cli_harness", ROOT / "integrations" / "harness" / "kiro-cli" / "client.py")
        self.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.h)

    def _update(self, **u):
        return json.dumps({"type": "sessionUpdate", "data": {"update": u}})

    def test_text_joins_messages_and_tool_titles(self):
        out = "\n".join([
            self._update(sessionUpdate="agent_message_chunk", content={"type": "text", "text": "Reading "}),
            self._update(sessionUpdate="agent_message_chunk", content={"type": "text", "text": "the board"}),
            self._update(sessionUpdate="tool_call", title="get_task P7"),
            self._update(sessionUpdate="agent_message_chunk", content={"type": "text", "text": "Done"}),
            "not json",
        ])
        self.assertEqual(self.h.result({}, out)["text"], "Reading the board\n[tool] get_task P7\nDone")

    def test_credits_sum_every_turn_and_are_none_without_one(self):
        turn = self._update(sessionUpdate="session_info_update", _meta={"kiro": {
            "kind": "turn_completion", "promptTurnSummaries": [{"unit": "credit", "usage": 0.5}]}})
        self.assertEqual(self.h.result({}, f"{turn}\n{turn}")["credits"], 1.0)
        self.assertIsNone(self.h.result({}, "plain text")["credits"])

    def test_final_text_is_the_fallback(self):
        done = json.dumps({"type": "runFinished", "data": {"finalText": "ok"}})
        self.assertEqual(self.h.result({}, done)["text"], "ok")


if __name__ == "__main__":
    unittest.main()
