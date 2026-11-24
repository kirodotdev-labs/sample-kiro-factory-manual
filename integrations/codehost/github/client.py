"""The `codehost` client on GitHub, for scripts: the read side through the `gh` CLI.

    from_config(cfg).checks_read(123)

`gh` runs with the caller's environment, so it uses `GH_TOKEN` when the host sets one and the
default `gh` login otherwise. It implements what the shipped scripts read (`pr_detail` and
`checks_read`); the other capabilities raise NotImplementedError,
and agents perform them through the `codehost-github` skill.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", os.path.expanduser("~/.local/bin"))

# The buckets `gh pr checks` uses, from REST check-run conclusions and commit-status states.
_CONCLUSION_BUCKET = {"success": "pass", "neutral": "pass", "skipped": "skipping", "cancelled": "cancel",
                      "failure": "fail", "timed_out": "fail", "action_required": "fail", "startup_failure": "fail",
                      "stale": "pending"}
_STATUS_BUCKET = {"success": "pass", "failure": "fail", "error": "fail", "pending": "pending"}


def _run_bucket(run: dict) -> str:
    if run.get("status") != "completed":
        return "pending"
    return _CONCLUSION_BUCKET.get(run.get("conclusion") or "", "pending")


def _gh_path() -> str:
    found = shutil.which("gh")
    if found:
        return found
    for d in _BIN_DIRS:
        cand = os.path.join(d, "gh")
        if os.access(cand, os.X_OK):
            return cand
    raise FileNotFoundError("gh not found on PATH or in " + ", ".join(_BIN_DIRS))


class GitHub:
    def __init__(self, slug: str, host: str = "github.com"):
        self.slug, self.host = slug, host

    def _gh(self, *args: str, timeout: int = 60):
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(dict.fromkeys(list(_BIN_DIRS) + env.get("PATH", "").split(os.pathsep)))
        return subprocess.run([_gh_path(), *args], capture_output=True, text=True, timeout=timeout, env=env)

    def pr_detail(self, number: int) -> dict:
        r = self._gh("pr", "view", str(number), "--repo", f"{self.host}/{self.slug}", "--json",
                     "number,state,isDraft,headRefOid,headRefName,baseRefName,mergeable,url")
        if r.returncode != 0:
            raise RuntimeError(f"gh pr view {number}: {r.stderr.strip()}")
        return json.loads(r.stdout)

    def checks_read(self, number: int) -> list[dict]:
        """Each check on the PR head as {name, state, bucket}; bucket is pass, fail, pending, skipping
        or cancel. No checks reported yet is an empty list.

        Read through REST (the head's check runs, latest per name, and its commit statuses, latest per
        context) rather than `gh pr checks`: that GraphQL query also asks for each check's workflow run,
        which an App installation without Actions read access is refused."""
        sha = self.pr_detail(number)["headRefOid"]
        runs = self._api_lines(f"repos/{self.slug}/commits/{sha}/check-runs?per_page=100",
                               ".check_runs[] | {name, status, conclusion}")
        statuses = self._api_lines(f"repos/{self.slug}/commits/{sha}/status?per_page=100",
                                   ".statuses[] | {context, state}")
        out = [{"name": c["name"], "state": (c.get("conclusion") or c.get("status") or "").upper(),
                "bucket": _run_bucket(c)} for c in runs]
        out += [{"name": s["context"], "state": (s.get("state") or "").upper(),
                 "bucket": _STATUS_BUCKET.get(s.get("state"), "pending")} for s in statuses]
        return out

    def _api_lines(self, path: str, jq: str) -> list[dict]:
        r = self._gh("api", "--hostname", self.host, "--paginate", path, "--jq", jq)
        if r.returncode != 0:
            raise RuntimeError(f"gh api {path.split('?')[0]}: {r.stderr.strip()}")
        return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]

    def _unsupported(self, *_a, **_k):
        raise NotImplementedError("not in the script client; agents use the codehost-github skill")

    pr_open = pr_update = pr_ready = pr_comment = pr_reply = pr_resolve_thread = _unsupported
    checks_rerun = reviews_read = branch_push = _unsupported


def from_config(cfg: dict) -> GitHub:
    return GitHub(cfg["repo"]["slug"], cfg["integrations"]["codehost"].get("host") or "github.com")
