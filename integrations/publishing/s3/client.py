"""Publishing client for S3 (a pages bucket behind a distribution).

Used when `integrations.publishing.use` is "s3". Keys are relative to the configured prefix and map
1:1 to URLs under `baseUrl`. Conforms to `integrations/publishing/interface.py` (PublishingClient).

Needs boto3 1.36 or later (the conditional writes below), which the host installs; the import is lazy
so a worker that never publishes to S3 (or an offline unit test) does not need it. Credentials come
from the configured `profile`, otherwise the default chain (a cloud host's role). A profile must
refresh itself (for example with `credential_process`); this module does not refresh it.

The conditional write uses the object's ETag: `expected` replaces only the version that was read
(If-Match) and `create_only` writes only when the key is empty (If-None-Match), so a second writer
never silently overwrites the first.
"""

from __future__ import annotations

import factory_config


class Conflict(RuntimeError):
    """A conditional write lost: the page changed since it was read (see interface.Conflict)."""


# Every key is rewritten in place, so neither the CDN nor a browser may reuse a copy.
CACHE_CONTROL = "no-cache"


class S3Publishing:
    # Pages are served under a CSP with no 'unsafe-inline': CSS and JS go in files beside the page.
    packaging = "linked"

    def __init__(self, *, bucket: str, base_url: str, region: str, prefix: str = "",
                 profile: str | None = None):
        self.bucket = bucket
        self.base_url = base_url
        self.region = region
        self.prefix = (prefix or "").strip("/")
        self.profile = profile or None

    @classmethod
    def from_config(cls, cfg: dict) -> "S3Publishing":
        p = "integrations.publishing"
        return cls(
            bucket=factory_config.get(cfg, f"{p}.bucket"),
            base_url=factory_config.get(cfg, f"{p}.baseUrl"),
            region=factory_config.get(cfg, f"{p}.region"),
            prefix=factory_config.get(cfg, f"{p}.prefix", "", required=False),
            profile=factory_config.get(cfg, f"{p}.profile", "", required=False) or None,
        )

    def _client(self):
        import boto3

        return boto3.Session(profile_name=self.profile, region_name=self.region).client("s3")

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def url(self, key: str) -> str:
        return self.base_url.rstrip("/") + "/" + self._key(key)

    def get_page(self, key: str) -> tuple[bytes | None, str | None]:
        from botocore.exceptions import ClientError

        client = self._client()
        # A key written or deleted between a denied read and the list gets one more read; a key
        # that lists as present and is denied twice is a real denial.
        for _ in range(2):
            try:
                obj = client.get_object(Bucket=self.bucket, Key=self._key(key))
                return obj["Body"].read(), obj["ETag"]
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404"):
                    return None, None
                if code not in ("AccessDenied", "403"):
                    raise
                denied = exc
            if not self._exists(key):
                return None, None
        raise denied

    def _exists(self, key: str) -> bool:
        # Without s3:ListBucket on the whole bucket, S3 answers a read of a missing key with 403, so a
        # list of the key's own prefix tells missing from denied. A key sorts before every longer key
        # it prefixes, so one result is enough.
        k = self._key(key)
        page = self._client().list_objects_v2(Bucket=self.bucket, Prefix=k, MaxKeys=1)
        return any(obj["Key"] == k for obj in page.get("Contents") or [])

    def put_page(self, key: str, html: bytes, *, expected: str | None = None,
                 create_only: bool = False, title: str = "", summary: str = "",
                 tags: list[str] | None = None) -> str:
        return self._put(key, html, "text/html", expected=expected, create_only=create_only)

    def put_asset(self, key: str, data: bytes, content_type: str) -> str:
        self._put(key, data, content_type)
        return self.url(key)

    def _put(self, key: str, body: bytes, content_type: str, *, expected: str | None = None,
             create_only: bool = False) -> str:
        from botocore.exceptions import ClientError

        args = {"Bucket": self.bucket, "Key": self._key(key), "Body": body,
                "ContentType": content_type, "CacheControl": CACHE_CONTROL}
        if expected:
            args["IfMatch"] = expected
        elif create_only:
            args["IfNoneMatch"] = "*"
        try:
            return self._client().put_object(**args)["ETag"]
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in (409, 412):
                raise Conflict(f"{key} changed since it was read") from exc
            raise


def from_config(cfg: dict) -> S3Publishing:
    return S3Publishing.from_config(cfg)
