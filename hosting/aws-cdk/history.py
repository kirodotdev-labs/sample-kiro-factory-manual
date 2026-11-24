"""AWS host storage: run records and leases in the pages bucket.

The renderer copies this file to the bundle's scripts/runtime/history.py when the host's runtime is
aws-cdk. It provides the functions every host module does (`run_records`, `write_record`,
`acquire_lease`, `release_lease`; see hosting/local/history.py), with the same shapes over a
different store.

Each worker run writes a record to the pages bucket at `runs/<worker>/<started_at>.json`: {worker,
start, end, status, summary, and, when the harness reports it, credits}. `credits` is None when the
record omits it (not reported, which is not zero). Nothing here deletes a record: the bucket's
lifecycle rule does (hosting-aws.md).

Config. The bucket, prefix and region come from `integrations.publishing` (the pages bucket is the
run-record store on this host); `FACTORY_HOST_RUNS_PREFIX` overrides the `runs/` prefix. boto3 is
imported lazily so an offline unit test needs neither it nor the bucket.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import factory_config


def _settings() -> dict:
    cfg = factory_config.load()
    p = "integrations.publishing"
    prefix = factory_config.get(cfg, f"{p}.prefix", "", required=False).strip("/")
    runs = os.environ.get("FACTORY_HOST_RUNS_PREFIX", "runs")
    return {
        "bucket": factory_config.get(cfg, f"{p}.bucket"),
        "region": factory_config.get(cfg, f"{p}.region"),
        "profile": factory_config.get(cfg, f"{p}.profile", "", required=False) or None,
        "runs_prefix": f"{prefix}/{runs}" if prefix else runs,
    }


def _client(s: dict):
    import boto3

    return boto3.Session(profile_name=s["profile"], region_name=s["region"]).client("s3")


def _normalise(worker: str, rec: dict) -> dict:
    # The worker is the one in the key, which only that worker's runs may write; a body naming another
    # worker does not move the record there.
    return {
        "worker": worker,
        "run_id": rec.get("runId") or rec.get("run_id") or "",
        "started_at": rec.get("start") or rec.get("started_at"),
        "finished_at": rec.get("end") or rec.get("finished_at"),
        "status": rec.get("status") or "",
        "implementation": rec.get("implementation") or "",
        "duration_ms": int(rec.get("duration_ms") or 0),
        "summary": rec.get("summary") or "",
        "trace": rec.get("trace") or "",
        "item": rec.get("item") or "",
        "error": rec.get("error") or "",
        "credits": None if rec.get("credits") is None else float(rec["credits"]),
    }


def _iter_objects(client, bucket: str, prefix: str):
    token = None
    while True:
        args = {"Bucket": bucket, "Prefix": prefix.rstrip("/") + "/"}
        if token:
            args["ContinuationToken"] = token
        page = client.list_objects_v2(**args)
        for obj in page.get("Contents") or []:
            yield obj["Key"]
        token = page.get("NextContinuationToken")
        if not page.get("IsTruncated"):
            break


def run_records(since: dt.datetime | None = None, older: int = 0) -> list[dict]:
    # A record's key ends in its start time, so an older one is skipped without being fetched.
    floor = since.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + ".json" if since else ""
    s = _settings()
    client = _client(s)
    keys: dict[str, list[str]] = {}  # worker -> its record keys, which the listing returns oldest first
    for key in _iter_objects(client, s["bucket"], s["runs_prefix"]):
        # runs/<worker>/<started_at>.json
        parts = key[len(s["runs_prefix"]):].strip("/").split("/")
        if len(parts) == 2 and parts[1].endswith(".json"):
            keys.setdefault(parts[0], []).append(key)
    out: list[dict] = []
    for worker, mine in sorted(keys.items()):
        before = [k for k in mine if k.rsplit("/", 1)[-1] < floor]
        for key in (before[-older:] if older else []) + mine[len(before):]:
            try:
                out.append(_normalise(worker, json.loads(client.get_object(Bucket=s["bucket"], Key=key)["Body"].read())))
            except Exception:  # noqa: BLE001
                continue
    return out


def _get_or_none(client, bucket: str, key: str):
    """The object, or None when the key is missing. Without s3:ListBucket on the whole bucket, S3
    answers a read of a missing key with 403, so a 403 lists the key: absent is missing, and present
    means it was written between the two calls, so it is read once more. A key that lists as present
    after two denied reads is a real denial."""
    for _ in range(2):
        try:
            return client.get_object(Bucket=bucket, Key=key)
        except client.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                return None
            if code not in ("AccessDenied", "403"):
                raise
            denied = exc
        listed = client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1).get("Contents") or []
        if not any(o["Key"] == key for o in listed):
            return None
    raise denied


def write_record(record: dict) -> None:
    s = _settings()
    key = f"{s['runs_prefix']}/{record['worker']}/{record['start']}.json"
    _client(s).put_object(Bucket=s["bucket"], Key=key, ContentType="application/json",
                          Body=json.dumps(record, indent=2, sort_keys=True).encode())


def _lease_key(s: dict, worker: str) -> str:
    return f"{s['runs_prefix']}/{worker}/lock"


def acquire_lease(worker: str, ttl_secs: int, run_id: str) -> bool:
    """S3 conditional create (If-None-Match: *); an expired lease is replaced only if its ETag is
    unchanged since it was read, so two runs cannot both take it."""
    s = _settings()
    client, key = _client(s), _lease_key(s, worker)
    now = dt.datetime.now(dt.timezone.utc)
    body = json.dumps({"runId": run_id, "expires": (now + dt.timedelta(seconds=ttl_secs)).isoformat()}).encode()
    try:
        client.put_object(Bucket=s["bucket"], Key=key, Body=body, IfNoneMatch="*")
        return True
    except client.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("PreconditionFailed", "ConditionalRequestConflict"):
            raise
    held = _get_or_none(client, s["bucket"], key)
    if held is None:
        return False  # the holder released it between the two calls; the next scheduled run takes it
    # A run can write its own worker's lease, so an expiry past what any run claims, or one that does not
    # parse, counts as expired rather than holding the worker off.
    try:
        expires = dt.datetime.fromisoformat(json.loads(held["Body"].read())["expires"])
        expired = not now < expires <= now + dt.timedelta(seconds=ttl_secs)
    except (ValueError, KeyError, TypeError):
        expired = True
    if not expired:
        return False
    try:
        client.put_object(Bucket=s["bucket"], Key=key, Body=body, IfMatch=held["ETag"])
        return True
    except client.exceptions.ClientError:
        return False


def release_lease(worker: str, run_id: str) -> None:
    s = _settings()
    client, key = _client(s), _lease_key(s, worker)
    try:
        held = json.loads(client.get_object(Bucket=s["bucket"], Key=key)["Body"].read())
        if held.get("runId") == run_id:
            client.delete_object(Bucket=s["bucket"], Key=key)
    except Exception:  # noqa: BLE001
        pass
