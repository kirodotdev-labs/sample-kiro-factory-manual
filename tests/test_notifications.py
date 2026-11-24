import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _client():
    spec = importlib.util.spec_from_file_location(
        "slack_workflow", ROOT / "integrations" / "notifications" / "slack-workflow" / "client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cfg(webhook_file: str, var: str = "text") -> dict:
    return {"integrations": {"notifications": {
        "feed": {"use": "slack-workflow", "webhookFile": webhook_file, "payloadVariable": var},
        "operator": {"use": "slack-workflow", "webhookFile": "/nonexistent", "payloadVariable": var},
    }}}


class SlackWorkflowTest(unittest.TestCase):
    def test_reads_the_named_channel(self):
        mod = _client()
        self.assertEqual(mod.from_config(_cfg("/a"), "operator").webhook_file, "/nonexistent")
        self.assertEqual(mod.from_config(_cfg("/a"), "feed").webhook_file, "/a")

    def test_missing_webhook_skips_without_failing(self):
        self.assertIn("feed: SKIPPED", _client().from_config(_cfg("/nonexistent"), "feed").post("hi"))

    def test_empty_inputs_skip(self):
        mod = _client()
        with tempfile.NamedTemporaryFile("w", suffix=".url", delete=False) as fh:
            fh.write("")
        self.assertIn("webhook file is empty", mod.from_config(_cfg(fh.name), "feed").post("hi"))
        self.assertIn("payloadVariable is empty", mod.from_config(_cfg(fh.name, ""), "feed").post("hi"))
        self.assertIn("no message", mod.from_config(_cfg(fh.name), "feed").post(""))

    def test_cli_always_exits_zero(self):
        self.assertEqual(_client().main(["feed"]), 0)

    def test_the_cli_takes_the_message_from_a_file(self):
        import contextlib
        import io
        mod = _client()
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("Spec ready: `$(reboot)`")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(mod.main(["feed", fh.name]), 0)
            self.assertEqual(mod.main(["feed", "/nonexistent/message.txt"]), 0)
        first, second = out.getvalue().splitlines()
        self.assertIn("feed: SKIPPED", first)  # read the file, then found no webhook configured
        self.assertIn("feed: FAILED (cannot read the message file", second)

    def test_the_message_file_is_read_as_utf8_whatever_the_locale(self):
        """A scheduler often runs with the C locale, where the default encoding is ASCII."""
        import subprocess
        import sys
        client = ROOT / "integrations" / "notifications" / "slack-workflow" / "client.py"
        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as fh:
            fh.write("Spec ready — café ✅".encode("utf-8"))
        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as bad:
            bad.write(b"\xff\xfe not utf-8")
        env = {"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PATH": "/usr/bin:/bin"}
        for path, status in ((fh.name, "feed: SKIPPED"), (bad.name, "feed: FAILED (cannot read the message file")):
            r = subprocess.run([sys.executable, str(client), "feed", path], capture_output=True, text=True,
                               env=env, timeout=30)
            self.assertEqual((r.returncode, r.stderr), (0, ""))
            self.assertIn(status, r.stdout)


if __name__ == "__main__":
    unittest.main()
