"""Board client for GitHub Issues, through the `gh` CLI.

Used by script workers when `integrations.board.use` is "github". The board is one repo's issues
that carry the scope label (`boardId`, `scopeLabel`); pull requests, which the issues API also
returns, are dropped. `gh` runs with the caller's environment, so it uses `GH_TOKEN` when the host
sets one and the default `gh` login otherwise.

GitHub has no write that carries a comment and label changes together, so `update` makes several
writes in a fixed order: comment, added labels, removed labels, then the state. A crash part way
leaves the record posted and the item still labelled as before, which the janitor recovers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from urllib.parse import quote

import factory_config

_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", os.path.expanduser("~/.local/bin"))
# Who a script counts as the team: the repo's owner, members of its organization and its invited
# collaborators (the board skill has the same rule for agents). MEMBER is organization membership,
# not write access to the repo.
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
CLOSE_REASONS = {"closed": "completed", "not_planned": "not_planned"}


class BoardError(RuntimeError):
    pass


def _gh_path() -> str:
    found = shutil.which("gh")
    if found:
        return found
    for d in _BIN_DIRS:
        cand = os.path.join(d, "gh")
        if os.access(cand, os.X_OK):
            return cand
    raise FileNotFoundError("gh not found on PATH or in " + ", ".join(_BIN_DIRS))


def _run_gh(args: list[str], stdin: str | None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(dict.fromkeys(list(_BIN_DIRS) + env.get("PATH", "").split(os.pathsep)))
    return subprocess.run([_gh_path(), *args], input=stdin, capture_output=True, text=True,
                          timeout=120, env=env)


def _person(user: dict | None) -> dict | None:
    login = (user or {}).get("login")
    return {"login": login, "name": login} if login else None


class GitHubBoard:
    """BoardClient for GitHub Issues. Construct with `from_config(cfg)`."""

    def __init__(self, *, repo: str, scope_label: str, columns: dict[str, str],
                 host: str = "github.com", bot_logins=(), team_logins=(), gh=None):
        self.repo = repo
        self.bot_logins = frozenset(l.lower() for l in bot_logins)   # logins are case-insensitive
        self.team_logins = frozenset(l.lower() for l in team_logins)
        self.scope_label = scope_label
        self.columns = columns     # factory column -> label; an unmapped one is not recorded
        self.host = host
        self._gh = gh or _run_gh   # injectable for tests: (args, stdin) -> CompletedProcess

    @classmethod
    def from_config(cls, cfg: dict) -> "GitHubBoard":
        b = "integrations.board"
        return cls(repo=factory_config.get(cfg, f"{b}.boardId"),
                   scope_label=factory_config.get(cfg, f"{b}.scopeLabel"),
                   columns=factory_config.get(cfg, f"{b}.columns", {}, required=False),
                   host=factory_config.get(cfg, f"{b}.host", "github.com", required=False),
                   bot_logins=factory_config.get(cfg, f"{b}.botLogins", [], required=False),
                   team_logins=factory_config.get(cfg, f"{b}.teamLogins", [], required=False))

    def _api(self, method: str, path: str, body: dict | None = None, *, paginate: bool = False,
             missing_ok: bool = False):
        args = ["api", "--hostname", self.host, "-X", method, path]
        if paginate:
            args += ["--paginate", "--jq", ".[]"]
        if body is not None:
            args += ["--input", "-"]
        r = self._gh(args, json.dumps(body) if body is not None else None)
        if r.returncode != 0:
            if missing_ok and "404" in (r.stderr or ""):
                return None
            raise BoardError(f"gh api {method} {path}: {(r.stderr or '').strip()[:300]}")
        out = (r.stdout or "").strip()
        if paginate:
            return [json.loads(line) for line in out.splitlines() if line.strip()]
        return json.loads(out) if out else {}

    def _trusted(self, association: str | None, user: dict | None) -> bool:
        """On the team: one of the instance's team logins when it names any, otherwise an association
        in TRUSTED; or one of the factory's own logins (an app posts with association NONE). Other
        bots are not: some relay outsiders' text."""
        login = ((user or {}).get("login") or "").lower()
        if login in self.bot_logins:
            return True
        return login in self.team_logins if self.team_logins else association in TRUSTED

    def _normalize(self, issue: dict, comments: list[dict] | None = None) -> dict:
        """The interface's item record (`interface.py`) from a GitHub issue."""
        labels = [l.get("name") if isinstance(l, dict) else str(l) for l in issue.get("labels") or []]
        return {
            "id": str(issue.get("number")),
            "url": issue.get("html_url") or "",
            "title": issue.get("title") or "",
            "description": issue.get("body") or "",
            "labels": labels,
            "column": next((k for k, v in self.columns.items() if v in labels), None),
            "status": "closed" if issue.get("state") == "closed" else "open",
            "created": issue.get("created_at"),
            "updated": issue.get("updated_at"),
            "closed": issue.get("closed_at"),
            "assignee": _person(issue.get("assignee")),
            "requester": _person(issue.get("user")),
            "type": (issue.get("type") or {}).get("name") or "",
            "trusted": self._trusted(issue.get("author_association"), issue.get("user")),
            "comments": [{"author": _person(c.get("user")), "body": c.get("body") or "",
                          "created": c.get("created_at"), "updated": c.get("updated_at"),
                          "trusted": self._trusted(c.get("author_association"), c.get("user"))}
                         for c in comments or []],
        }

    def _issue_path(self, item_id: str) -> str:
        return f"repos/{self.repo}/issues/{int(item_id)}"

    # ---- BoardClient capabilities ----

    def list(self, *, status: str = "Open", updated_after: str | None = None) -> list[dict]:
        query = f"state={status.lower()}&labels={quote(self.scope_label)}&per_page=100"
        if updated_after:
            query += f"&since={quote(updated_after)}"
        issues = self._api("GET", f"repos/{self.repo}/issues?{query}", paginate=True)
        return [self._normalize(i) for i in issues if "pull_request" not in i]

    def get(self, item: str, *, comment_limit: int = 50) -> dict:
        """The item with every comment: GitHub pages them, so the list is complete whatever the
        limit."""
        issue = self._api("GET", self._issue_path(item))
        comments = self._api("GET", f"{self._issue_path(item)}/comments?per_page=100", paginate=True)
        return self._normalize(issue, comments)

    def comment(self, item_id: str, body: str) -> None:
        self._api("POST", f"{self._issue_path(item_id)}/comments", {"body": body})

    def update_comment(self, item_id: str, comment_id: str, body: str) -> None:
        self._api("PATCH", f"repos/{self.repo}/issues/comments/{int(comment_id)}", {"body": body})

    def add_labels(self, item_id: str, labels: list[str]) -> None:
        if labels:
            self._api("POST", f"{self._issue_path(item_id)}/labels", {"labels": list(labels)})

    def remove_labels(self, item_id: str, labels: list[str]) -> None:
        for label in labels:
            self._api("DELETE", f"{self._issue_path(item_id)}/labels/{quote(label, safe='')}",
                      missing_ok=True)

    def move(self, item_id: str, column: str) -> None:
        self.update(item_id, column=column)

    def close(self, item_id: str, reason_body: str, *, status: str = "closed") -> None:
        self.update(item_id, status=status, comment=reason_body)

    def create(self, title: str, body: str, labels: list[str]) -> dict:
        names = [self.scope_label, *labels]
        if "intake" in self.columns:
            names.append(self.columns["intake"])
        issue = self._api("POST", f"repos/{self.repo}/issues",
                          {"title": title, "body": body, "labels": list(dict.fromkeys(names))})
        return self._normalize(issue)

    def update(self, item_id: str, *, add_labels: list[str] | None = None,
               remove_labels: list[str] | None = None, column: str | None = None,
               status: str | None = None, comment: str | None = None) -> dict:
        change = None
        if status is not None:
            s = status.strip().lower().replace(" ", "_")
            if s != "open" and s not in CLOSE_REASONS:
                raise BoardError(f"unknown status {status!r}: use open, closed or not planned")
            change = {"state": "open"} if s == "open" else {"state": "closed", "state_reason": CLOSE_REASONS[s]}
        adds, removes = list(add_labels or []), []
        if column in self.columns:
            adds.append(self.columns[column])
            removes = [v for k, v in self.columns.items() if k != column]
        removes += list(remove_labels or [])   # the caller's, which carry the claim, come off last
        if comment is not None:
            self.comment(item_id, comment)
        self.add_labels(item_id, list(dict.fromkeys(adds)))
        self.remove_labels(item_id, [l for l in dict.fromkeys(removes) if l not in adds])
        if change is not None:
            return self._normalize(self._api("PATCH", self._issue_path(item_id), change))
        return {"id": str(item_id)}


def from_config(cfg: dict) -> GitHubBoard:
    return GitHubBoard.from_config(cfg)
