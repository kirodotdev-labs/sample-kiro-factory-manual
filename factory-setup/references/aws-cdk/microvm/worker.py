"""The run server inside each worker's Lambda MicroVM.

Lambda starts this server while building the image and snapshots it after /ready answers, so nothing
here may read a credential or a secret before /run: whatever exists at snapshot time is shared by every
MicroVM started from the image. /run starts one worker run in the background and returns at once (its
timeout is at most 60 seconds). The run writes a record to runs/<worker>/<started_at>.json in the pages
bucket and then has its MicroVM terminated.

Every AWS call in a run, here and in everything it starts, uses a session from the token vending
machine (run_credentials.py) scoped to the run's worker; the MicroVM's execution role can do nothing else.

The launcher sends the run hook payload `{"worker": "<name>", "token": "<run token>"}`, plus `item` to
run for one board item (run_worker.py --item). `smoke` checks that kiro-cli answers and gh reads the
repo; any other name is a worker in the bundle at /opt/factory, run once by run_worker.py, which writes
that worker's run record and holds its lease.
"""

import json
import os
import re
import subprocess
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import run_credentials

PORT = 8080
HOOKS = "/aws/lambda-microvms/runtime/v1/"
OUTPUT_TAIL = 4000
SMOKE_PROMPT = "Reply with the single word ok and nothing else."
SMOKE_TIMEOUT_SECONDS = 900
BUNDLE = Path("/opt/factory")
# The host profile names this file as the webhook channels' webhookFile.
WEBHOOK_FILE = Path("/tmp/factory/secrets/feed-webhook")
# The host profile's paths.stateDir. A MicroVM's disk is gone after the run, so the run's own worker
# directory comes down before the run and goes back up after it. A run may list only its own state.
STATE_DIR = Path("/tmp/factory/state")
STATE_PREFIX = "state/"
# From /run, below the launcher's MAX_RUN_SECONDS: the clone, state restore and run_worker.py share it, and
# what is left after it is for the state upload, the record and termination. lib/schedule.ts's
# checkWorkerTimeouts keeps a worker's own timeout inside it.
RUN_DEADLINE_SECONDS = 2 * 60 * 60 - 300
# gh as git's credential helper; the image's gh wrapper supplies the App token.
GIT_HELPER = "!gh auth git-credential"
# The gh wrapper fetches installation tokens during a run, so they are masked by shape.
GITHUB_TOKEN_PATTERN = re.compile(r"ghs_[A-Za-z0-9]{20,}")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tail(text: str, secrets: tuple) -> str:
    # Masked before truncating, so a secret cut at the boundary leaves no fragment.
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return GITHUB_TOKEN_PATTERN.sub("***", text)[-OUTPUT_TAIL:]


def _command(argv: list, env: dict, timeout: int = 60, secrets: tuple = ()) -> dict:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
        return {"exit": r.returncode, "stdout": _tail(r.stdout, secrets), "stderr": _tail(r.stderr, secrets)}
    except subprocess.TimeoutExpired:
        return {"exit": None, "error": f"timed out after {timeout}s"}
    except OSError as e:
        return {"exit": None, "error": str(e)}


def _secret(env_name: str) -> str:
    import boto3

    secret_id = os.environ.get(env_name)
    if not secret_id:
        return ""
    client = boto3.client("secretsmanager", region_name=os.environ["FACTORY_REGION"])
    return client.get_secret_value(SecretId=secret_id)["SecretString"].strip()


def _write_webhook() -> str:
    """Write the webhook where the host profile points the notification channels. Until someone stores the
    URL, no file is written, and the notifier skips each post without failing the run."""
    try:
        url = _secret("WEBHOOK_SECRET")
    except Exception:
        traceback.print_exc()
        return ""
    if not url.startswith("https://"):
        return ""
    WEBHOOK_FILE.parent.mkdir(parents=True, exist_ok=True)
    WEBHOOK_FILE.touch(mode=0o600)
    WEBHOOK_FILE.write_text(url)
    return url


def _s3():
    import boto3

    return boto3.client("s3", region_name=os.environ["FACTORY_REGION"])


def _restore_state(worker: str) -> None:
    """Best effort: a run that cannot restore starts with a cold cache."""
    try:
        s3, bucket = _s3(), os.environ["PAGES_BUCKET"]
        prefix = f"{STATE_PREFIX}{worker}/"
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                rel = obj["Key"][len(STATE_PREFIX) :]
                if not rel or rel.endswith("/") or ".." in Path(rel).parts:
                    continue
                dest = STATE_DIR / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(bucket, obj["Key"], str(dest))
    except Exception:
        traceback.print_exc()


def _save_state(worker: str) -> None:
    """Best effort, and only adds or overwrites keys: a file a run deletes comes back on the next restore."""
    root = STATE_DIR / worker
    if not root.is_dir():
        return
    try:
        s3, bucket = _s3(), os.environ["PAGES_BUCKET"]
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            s3.upload_file(str(path), bucket, f"{STATE_PREFIX}{worker}/{path.relative_to(root).as_posix()}")
    except Exception:
        traceback.print_exc()


def _repo_slug() -> str:
    return json.loads((BUNDLE / "factory-config.json").read_text())["repo"]["slug"]


def _prepare_checkout(env: dict, deadline: float) -> None:
    """Give the run the checkout the host profile names, as a workstation has before any run: a blobless
    clone with gh as its credential helper and the factory account's identity as local config. Best effort:
    a run without it fails on its first read, which its record shows."""
    try:
        checkout = Path(json.loads((BUNDLE / "host.json").read_text())["paths"]["checkout"])
        if not (checkout / ".git").exists():
            checkout.parent.mkdir(parents=True, exist_ok=True)
            clone = subprocess.run(
                ["git", "-c", "credential.helper=", "-c", f"credential.helper={GIT_HELPER}",
                 "clone", "--filter=blob:none", "--quiet", f"https://github.com/{_repo_slug()}.git", str(checkout)],
                capture_output=True, text=True, timeout=max(1, int(deadline - time.monotonic())),
                env={**env, "GIT_TERMINAL_PROMPT": "0"})
            if clone.returncode != 0:
                print(f"clone failed: {_tail(clone.stderr, ())}", flush=True)
                return
        for key, value in (("credential.helper", GIT_HELPER),
                           ("user.name", os.environ["GIT_USER_NAME"]),
                           ("user.email", os.environ["GIT_USER_EMAIL"])):
            subprocess.run(["git", "-C", str(checkout), "config", key, value], check=True,
                           timeout=max(1, min(30, int(deadline - time.monotonic()))))
    except Exception:
        traceback.print_exc()


def _smoke(record: dict, env: dict, secrets: tuple) -> None:
    record["tools"] = {
        name: _command(argv, env)
        for name, argv in {
            "kiro-cli": ["kiro-cli", "--version"],
            "git": ["git", "--version"],
            "gh": ["gh", "--version"],
            "python": ["python3.12", "--version"],
        }.items()
    }
    record["chat"] = _command(["kiro-cli", "chat", "--v3", "--no-interactive", SMOKE_PROMPT],
                              env, SMOKE_TIMEOUT_SECONDS, secrets)
    slug = _repo_slug()
    record["gh"] = _command(["gh", "api", f"repos/{slug}", "--jq", ".full_name"], env, secrets=secrets)
    chat_ok = record["chat"].get("exit") == 0 and re.search(r"\bok\b", record["chat"].get("stdout", ""), re.I) is not None
    gh_ok = record["gh"].get("exit") == 0 and record["gh"].get("stdout", "").strip() == slug
    record["status"] = "ok" if chat_ok and gh_ok else "failed"
    record["summary"] = f"kiro-cli {'replied' if chat_ok else 'did not reply ok'}; gh {'read' if gh_ok else 'could not read'} {slug}"


def _bundle(record: dict, env: dict, secrets: tuple, deadline: float) -> None:
    """One run of a bundle worker. run_worker.py writes the run's record and holds its lease."""
    env = {**env, "FACTORY_BUNDLE": str(BUNDLE), "FACTORY_PYTHON": "python3.12"}
    _prepare_checkout(env, deadline)
    _restore_state(record["worker"])
    argv = ["python3.12", str(BUNDLE / "scripts" / "runtime" / "run_worker.py"), record["worker"]]
    if "item" in record:
        argv += ["--item", record["item"]]
    result = _command(argv, env, max(1, int(deadline - time.monotonic())), secrets)
    # Only a run that held the lease (exit 0 or 1) saves, so a skipped run's stale copy never lands last.
    if result.get("exit") in (0, 1):
        _save_state(record["worker"])
    record["run_worker"] = result
    record["status"] = {0: "ok", 4: "skipped"}.get(result.get("exit"), "failed")
    record["summary"] = (result.get("stdout", "").strip().splitlines() or [result.get("error", "")])[-1]


def _bundle_workers() -> dict:
    try:
        return json.loads((BUNDLE / "workers.json").read_text())
    except OSError:
        return {}


def _run(microvm_id: str, payload: str) -> None:
    started = time.monotonic()
    deadline = started + RUN_DEADLINE_SECONDS
    record = {"microvm_id": microvm_id, "started_at": _now(), "status": "error", "summary": ""}
    bundled, secrets = False, ()
    try:
        request = json.loads(payload or "{}")
        run_credentials.setup(request.get("token") or "")
        record["worker"] = request.get("worker")
        item = request.get("item")
        if item is not None:
            if not isinstance(item, str):
                raise ValueError(f"invalid item id {item!r}")
            record["item"] = item
        key = _secret("KIRO_API_KEY_SECRET")
        webhook = _write_webhook()
        env = {**os.environ, "KIRO_API_KEY": key}
        secrets = (key, webhook, run_credentials.token())
        if record["worker"] == "smoke":
            _smoke(record, env, secrets)
        elif record["worker"] in _bundle_workers():
            bundled = True
            _bundle(record, env, secrets, deadline)
        else:
            record["summary"] = f"unknown worker {record['worker']!r}"
    except Exception:
        record["summary"] = _tail(traceback.format_exc(), secrets)
    # start, end and duration_ms are the fields hosting/aws-cdk/history.py reads from a record.
    record.update(start=record["started_at"], ended_at=_now(), duration_ms=int((time.monotonic() - started) * 1000))
    record["end"] = record["ended_at"]
    print(json.dumps(record), flush=True)
    # run_worker.py exits 0 (succeeded) or 1 (failed) once its record is written, and 4 when another run
    # held the lease and there is nothing to record; any other status, or none, means no record exists.
    recorded = record.get("run_worker", {}).get("exit") in (0, 1, 4)
    _finish(microvm_id, record, write_record=not (bundled and recorded))


def _finish(microvm_id: str, record: dict, write_record: bool) -> None:
    # The run's session may write records only under the worker its token names, which is the payload's worker.
    name = record.get("worker") or "unknown"
    if write_record:
        try:
            _s3().put_object(
                Bucket=os.environ["PAGES_BUCKET"],
                Key=f"runs/{name}/{record['started_at']}.json",
                Body=json.dumps(record, indent=2).encode(),
                ContentType="application/json",
            )
        except Exception:
            traceback.print_exc()
    # Without this the MicroVM runs until the launcher's maximum duration.
    try:
        run_credentials.terminate(microvm_id)
    except Exception:
        traceback.print_exc()


# Lambda calls /run once per MicroVM; a run's own processes can reach the port too.
_RUN_ONCE = threading.Lock()


class Hooks(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == HOOKS + "ready":
            self._reply(200)
        elif self.path == HOOKS + "run":
            try:
                request = json.loads(body or b"{}")
            except ValueError:
                request = None
            if not isinstance(request, dict) or not request.get("microvmId"):
                self._reply(400)
                return
            if not _RUN_ONCE.acquire(blocking=False):
                self._reply(409)
                return
            threading.Thread(
                target=_run, args=(request.get("microvmId", ""), request.get("runHookPayload", "")), daemon=True
            ).start()
            self._reply(200)
        else:
            self._reply(404)

    def _reply(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        print(f"hook {self.command} {self.path} {args[1] if len(args) > 1 else ''}", flush=True)


if __name__ == "__main__":
    print(f"worker listening on {PORT}", flush=True)
    # Lambda calls the hooks from outside the VM; the launcher's NO_INGRESS connector admits nothing else.
    ThreadingHTTPServer(("0.0.0.0", PORT), Hooks).serve_forever()
