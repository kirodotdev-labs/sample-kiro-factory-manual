"""Factory report: one page of what waits on a person, the board over time and each worker's health.

Zero-token script worker, off by default. Everything it shows is read fresh each run from what the
Manual's interfaces already return, so it keeps no state and adds nothing to what workers write.

Inputs: open items, and items closed within `days` (board); the newest PR link on each item in review
and that PR's state (codehost); the host's run records and the enabled workers (bundle workers.json).
Outputs: one page at `pageKey` (publishing): items labelled needs-info, which wait on the requester,
and items in review whose newest PR link is an open PR out of draft, which wait on a reviewer; open
items per column now and per day over `days`; each worker's state, last run, last success and last
error. The page is template.html with this script's model inlined, and report.css and report.js
inlined or, where the publishing platform links assets, published beside it; report.js draws
the model.
Verification: none; the page is rebuilt from its sources on every run.
Decisions: none. A worker is stalled when its last success is older than three of its usual gaps
between runs, and failing when its last run failed. A failed board read fails the run.

Run: python3 scripts/workers/report/report.py [dry]. `dry` builds the page and publishes nothing.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
import runner  # noqa: E402

runner.bootstrap()

import factory_config  # noqa: E402
import records  # noqa: E402
from runner import Fail, Report  # noqa: E402

WORKER = "report"
MAX_REVIEW_READS = 50
HEALTH_RUNS = 11  # how many of a worker's latest runs set the gap that "stalled" is judged against
PR_NUMBER = re.compile(r"/(\d+)/?(?:[#?].*)?$")


def _ts(v) -> dt.datetime | None:
    if not v:
        return None
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


# ------------------------------------------------------------------ act on

def latest_prs(item: dict) -> dict[str, str]:
    """The newest URL per PR kind the item's description and comments link, oldest post first. Only
    the team's text links a PR: an outsider's `PR:` line is their own text (`common/links.md`)."""
    out: dict[str, str] = {}
    bodies = [item.get("description") or ""] if item.get("trusted") is not False else []
    bodies += [c.get("body") or "" for c in item.get("comments") or [] if c.get("trusted") is not False]
    for body in bodies:
        for kind, url in records.links_in(body):
            out[kind] = url
    return out


def _canon(url) -> str:
    """A PR URL without its fragment, query or trailing slash, lowercased, for comparing two links."""
    return re.split(r"[#?]", str(url or ""), maxsplit=1)[0].rstrip("/").lower()


def act_on(open_items: list[dict], needs_info: str, get_item, pr_detail) -> list[dict]:
    """Rows of {id, title, url, why, link}: what waits on a person now."""
    rows = []
    for it in open_items:
        if needs_info in (it.get("labels") or []):
            rows.append({"id": it["id"], "title": it.get("title") or "", "url": it.get("url") or "",
                         "why": "waiting on the requester's answer", "link": ""})
    reviewing = [it for it in open_items if it.get("column") == "review"][:MAX_REVIEW_READS]
    for it in reviewing if pr_detail else []:
        try:
            prs = latest_prs(get_item(it["id"]))
        except Exception as exc:  # noqa: BLE001 - one unreadable item is a note, not a failed page
            rows.append({"id": it["id"], "title": it.get("title") or "", "url": it.get("url") or "",
                         "why": f"could not read the item ({type(exc).__name__})", "link": ""})
            continue
        for kind, url in sorted(prs.items()):
            m = PR_NUMBER.search(url)
            if not m:
                continue
            try:
                pr = pr_detail(int(m.group(1)))
            except Exception:  # noqa: BLE001 - a code host error leaves the row out
                continue
            # The lookup is by number in the configured repo, so a link to another repo's PR resolves
            # to an unrelated PR; only the one whose URL is the link counts.
            if _canon(pr.get("url")) != _canon(url):
                continue
            if str(pr.get("state", "")).lower() == "open" and not pr.get("isDraft"):
                rows.append({"id": it["id"], "title": it.get("title") or "", "url": it.get("url") or "",
                             "why": f"{kind} ready for review", "link": url})
    return rows


# ------------------------------------------------------------------ board over time

def board_series(open_items: list[dict], closed_items: list[dict], now: dt.datetime, days: int) -> dict:
    """Open items at the end of each of the last `days` days, and how many opened and closed in the
    window. Items closed before the window never count, so a closed list limited to the window is
    enough."""
    items = ([(_ts(i.get("created")), None) for i in open_items]
             + [(_ts(i.get("created")), _ts(i.get("closed"))) for i in closed_items])
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
    days_ends = [end - dt.timedelta(days=days - 1 - k) for k in range(days)]
    counts = [sum(1 for c, x in items if c and c < d and (x is None or x >= d)) for d in days_ends]
    start = end - dt.timedelta(days=days)
    return {"days": [(d - dt.timedelta(days=1)).date().isoformat() for d in days_ends], "open": counts,
            "opened": sum(1 for c, _ in items if c and c >= start),
            "closed": sum(1 for _, x in items if x and x >= start)}


def by_column(open_items: list[dict]) -> list[tuple[str, int]]:
    cols = ["intake", "review", "release", None]
    return [(c or "no column", sum(1 for i in open_items if i.get("column") == c)) for c in cols]


# ------------------------------------------------------------------ health

def _last_line(text) -> str:
    """A failure's last non-empty line, capped: the exception or message, not the start of a traceback."""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    return lines[-1][:200] if lines else ""


def health(workers: dict, runs: list[dict], now: dt.datetime) -> list[dict]:
    """One row per enabled worker: state (ok, failing, stalled, no runs), last run, last success and the
    last failure's error."""
    rows = []
    for name in sorted(workers):
        mine = sorted((r for r in runs if r.get("worker") == name and _ts(r.get("started_at"))),
                      key=lambda r: _ts(r["started_at"]))
        if not mine:
            rows.append({"worker": name, "state": "no runs", "last": None, "lastOk": None, "error": ""})
            continue
        last = mine[-1]
        ok = [r for r in mine if r.get("status") == "succeeded"]
        last_ok = _ts(ok[-1]["started_at"]) if ok else None
        starts = [_ts(r["started_at"]) for r in mine[-HEALTH_RUNS:]]
        gaps = [(b - a).total_seconds() for a, b in zip(starts, starts[1:]) if b > a]
        stalled = last_ok is None or (len(gaps) >= 2 and (now - last_ok).total_seconds() > 3 * statistics.median(gaps))
        failed = [r for r in mine if r.get("status") != "succeeded"]
        state = "failing" if last.get("status") != "succeeded" else "stalled" if stalled else "ok"
        rows.append({"worker": name, "state": state, "last": _ts(last["started_at"]), "lastOk": last_ok,
                     "error": _last_line(failed[-1].get("error") or failed[-1].get("summary"))
                     if failed and state != "ok" else ""})
    return rows


# ------------------------------------------------------------------ page

HERE = Path(__file__).resolve().parent


def _iso(t: dt.datetime | None) -> str | None:
    return t.isoformat(timespec="seconds").replace("+00:00", "Z") if t else None


def model(name: str, now: dt.datetime, rows: list[dict], series: dict, cols: list[tuple[str, int]],
          workers: list[dict], notes: list[str]) -> dict:
    """Everything the page shows, as plain data; report.js draws it."""
    return {"name": name, "generatedAt": _iso(now), "actOn": rows,
            "board": {**series, "columns": [{"name": c, "count": n} for c, n in cols]},
            "health": [{**w, "last": _iso(w["last"]), "lastOk": _iso(w["lastOk"])} for w in workers],
            "notes": notes}


ASSETS = (("report.css", "text/css"), ("report.js", "text/javascript"))


def page(data: dict, linked: bool = False) -> str:
    """template.html with the model inlined, and the stylesheet and script inlined too, or linked as
    assets beside the page for a platform whose content policy blocks inline code (`packaging` is
    "linked"). The model is JSON in a non-executable script element: every `<` is escaped so no board
    text can close it."""
    css, js = ((HERE / name).read_text() for name, _type in ASSETS)
    style = '<link rel="stylesheet" href="report.css">' if linked else f"<style>{css}</style>"
    script = '<script src="report.js"></script>' if linked else f"<script>{js}</script>"
    doc = (HERE / "template.html").read_text()
    for slot, value in (("{{title}}", html.escape(f"{data['name']} factory")), ("{{style}}", style),
                        ("{{script}}", script),
                        ("{{model}}", json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))):
        doc = doc.replace(slot, value)
    return doc


# ------------------------------------------------------------------ entry

def build(cfg: dict, board, codehost, history, workers: dict, now: dt.datetime, days: int,
          linked: bool = False) -> tuple[str, list[dict]]:
    notes = []
    since = (now - dt.timedelta(days=days)).isoformat()
    try:
        open_items = board.list(status="Open")
        closed = board.list(status="Closed", updated_after=since)
    except Exception as exc:  # noqa: BLE001 - nothing to show without the board: fail in one line
        raise Fail(f"Report NOT RUN: board read failed: {type(exc).__name__}: {exc}")
    closed_items = [i for i in closed if (_ts(i.get("closed")) or now) >= now - dt.timedelta(days=days)]
    if codehost is None:
        notes.append("The code host has no script client: PRs awaiting review are not shown.")
    rows = act_on(open_items, factory_config.label("needsInfo"), board.get,
                  codehost.pr_detail if codehost else None)
    # The window's runs, and before it as many per worker as health reads, so a worker whose last
    # run is older than the window still shows that run and its error.
    runs = history.run_records(since=now - dt.timedelta(days=days), older=HEALTH_RUNS)
    if not runs:
        notes.append("This host has no run records yet: health shows no runs.")
    worker_rows = health(workers, runs, now)
    return page(model(cfg["instance"]["name"], now, rows, board_series(open_items, closed_items, now, days),
                      by_column(open_items), worker_rows, notes), linked), rows


def run(ctx):
    cfg = factory_config.load()
    days = int(factory_config.worker_setting(WORKER, "days", 30))
    if days < 1:
        raise Fail(f"Report NOT RUN: workers.report.settings.days is {days}; it must be at least 1")
    key = factory_config.worker_setting(WORKER, "pageKey", "report/index.html")
    board = factory_config.integration_client(cfg, "board")
    module = factory_config.integration_name(cfg, "codehost").replace("-", "_")
    try:
        codehost = factory_config.integration_client(cfg, "codehost")
    except ModuleNotFoundError as exc:
        if exc.name not in (module, f"codehost_{module}"):
            raise  # the client is there but cannot import: a real failure, not a missing client
        codehost = None  # a code host that ships only its skill: the rest of the page still holds
    history = factory_config.load_host_module("history")
    pub = factory_config.integration_client(cfg, "publishing")
    linked = getattr(pub, "packaging", "inline") == "linked"
    now = dt.datetime.now(dt.timezone.utc)
    doc, rows = build(cfg, board, codehost, history, factory_config.workers(), now, days, linked)
    if (getattr(ctx, "message", "") or "").strip().lower().startswith("dry"):
        raise Report(f"Report DRY RUN: {len(rows)} to act on, {len(doc)} bytes, nothing published.")
    if linked:
        base = key.rsplit("/", 1)[0] + "/" if "/" in key else ""
        for name, content_type in ASSETS:
            pub.put_asset(base + name, (HERE / name).read_bytes(), content_type)
    pub.put_page(key, doc.encode(), title=f"{cfg['instance']['name']} factory")
    raise Report(f"Report published: {pub.url(key)} ({len(rows)} to act on).")


if __name__ == "__main__":
    sys.exit(runner.main(run))
