"""Publishing client for a local directory: pages are files a browser opens straight from disk.

Used when `integrations.publishing.use` is "disk", for trying the factory on one machine. Keys map
to paths under `dir` and to URLs under `baseUrl` (default: the directory's file:// URL). Conforms to
`integrations/publishing/interface.py` (PublishingClient).

The version handle is the file's sha256. A conditional write holds an exclusive lock on
`<dir>/.lock` while it compares and replaces, so a second writer never silently overwrites the
first. Nothing here authenticates readers: whoever can read the directory, or the server in front
of it, can read every page.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import factory_config


class Conflict(RuntimeError):
    """A conditional write lost: the page changed since it was read (see interface.Conflict)."""


class DiskPublishing:
    packaging = "inline"

    def __init__(self, *, directory: str, base_url: str = ""):
        self.dir = Path(directory).expanduser().resolve()
        self.base_url = base_url or self.dir.as_uri() + "/"

    @classmethod
    def from_config(cls, cfg: dict) -> "DiskPublishing":
        p = "integrations.publishing"
        return cls(directory=factory_config.get(cfg, f"{p}.dir"),
                   base_url=factory_config.get(cfg, f"{p}.baseUrl", "", required=False))

    def _path(self, key: str) -> Path:
        path = (self.dir / key).resolve()
        if self.dir not in path.parents:
            raise ValueError(f"key {key!r} is outside the publishing directory")
        return path

    def url(self, key: str) -> str:
        self._path(key)
        return self.base_url.rstrip("/") + "/" + key

    @staticmethod
    def _version(body: bytes) -> str:
        return hashlib.sha256(body).hexdigest()

    @contextmanager
    def _locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / ".lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def get_page(self, key: str) -> tuple[bytes | None, str | None]:
        path = self._path(key)
        if not path.is_file():
            return None, None
        body = path.read_bytes()
        return body, self._version(body)

    def put_page(self, key: str, html: bytes, *, expected: str | None = None,
                 create_only: bool = False, title: str = "", summary: str = "",
                 tags: list[str] | None = None) -> str:
        return self._put(key, html, expected=expected, create_only=create_only)

    def put_asset(self, key: str, data: bytes, content_type: str) -> str:
        self._put(key, data)
        return self.url(key)

    def _put(self, key: str, body: bytes, *, expected: str | None = None, create_only: bool = False) -> str:
        path = self._path(key)
        with self._locked():
            current = self._version(path.read_bytes()) if path.is_file() else None
            if (expected and current != expected) or (create_only and current is not None):
                raise Conflict(f"{key} changed since it was read")
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        return self._version(body)


def from_config(cfg: dict) -> DiskPublishing:
    return DiskPublishing.from_config(cfg)
