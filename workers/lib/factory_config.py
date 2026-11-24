"""Config, vocabulary and state for the factory's script workers. Stdlib only.

A script runs from a rendered bundle:

  bundle/factory-config.json   the instance config
  bundle/vocabulary.json       labels after the instance's renames
  bundle/workers.json          the resolved workers
  bundle/scripts/lib/          this file

Every path is found relative to this file, so a script needs no environment to find its config.
FACTORY_BUNDLE overrides the bundle root, and FACTORY_STATE_DIR names where a worker may keep state
between runs (the runner sets it to the host profile's `paths.stateDir`).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


class ConfigError(RuntimeError):
    """The config is missing, unreadable, or lacks a field a script needs."""


def bundle_root() -> Path:
    env = os.environ.get("FACTORY_BUNDLE")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"{path} not found; is this script running from a rendered bundle?")
    except (OSError, ValueError) as exc:
        raise ConfigError(f"could not read {path}: {exc}")


def load() -> dict:
    return _read(bundle_root() / "factory-config.json")


def vocabulary() -> dict:
    return _read(bundle_root() / "vocabulary.json")


def label(key: str) -> str:
    return vocabulary()["labels"][key]


def workers() -> dict:
    return _read(bundle_root() / "workers.json")


def claim_ttl_minutes(worker: str) -> int | None:
    """How long the named worker's claim may be held, from the bundle's workers.json. None when the
    worker is not enabled."""
    w = workers().get(worker)
    return int(w["claimTtlSecs"]) // 60 if w else None


def worker_setting(worker: str, key: str, default=None):
    """A resolved worker setting (the step's default merged with the instance's), from workers.json."""
    return (workers().get(worker) or {}).get("settings", {}).get(key, default)


def get(cfg: dict, dotted: str, default=None, *, required: bool = True):
    """Look up a dotted path such as 'integrations.board.boardId'.

    A missing value raises ConfigError when required. Failing loudly is the point: a script that
    quietly falls back to a default reads the wrong board or the wrong repo.
    """
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            if required:
                raise ConfigError(f"factory-config.json is missing {dotted}")
            return default
        node = node[part]
    return os.path.expanduser(node) if isinstance(node, str) and node.startswith("~") else node


def integration_name(cfg: dict, kind: str, channel: str | None = None) -> str | None:
    """Which integration implements a kind (or a notifications channel), or None when unset."""
    dotted = f"integrations.{kind}" + (f".{channel}" if channel else "") + ".use"
    return get(cfg, dotted, None, required=False)


def integration_client(cfg: dict, kind: str):
    """The configured integration's script client for a kind (the renderer copies it into the bundle
    under the integration's name)."""
    return integration_module(cfg, kind).from_config(cfg)


def integration_module(cfg: dict, kind: str, channel: str | None = None):
    """The configured integration's client module for a kind (or a notifications channel), for a
    caller whose `from_config` takes more than the config. Raises ImportError when it is missing."""
    name = integration_name(cfg, kind, channel)
    if not name:
        raise ConfigError(f"integrations.{kind}.use is not set")
    return _integration_module(kind, name.replace("-", "_"))


def _integration_module(kind: str, module: str):
    """The client module in scripts/integrations/<kind>/, loaded under a kind-qualified name.

    Two kinds can use integrations with the same name (one platform as both board and code host), and
    a bare import would hand the second caller the first kind's module."""
    for entry in sys.path:
        path = os.path.join(entry, f"{module}.py")
        if os.path.basename(entry.rstrip(os.sep)) == kind and os.path.isfile(path):
            bare = sys.modules.get(module)
            if bare is not None and os.path.realpath(getattr(bare, "__file__", "") or "") == os.path.realpath(path):
                return bare
            qualified = f"{kind}_{module}"
            if qualified not in sys.modules:
                spec = importlib.util.spec_from_file_location(qualified, path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[qualified] = mod
                try:
                    spec.loader.exec_module(mod)
                except BaseException:
                    sys.modules.pop(qualified, None)
                    raise
            return sys.modules[qualified]
    return __import__(module)


def load_host_module(name: str = "history"):
    """Import a hosting module the renderer copied into scripts/runtime/.

    The renderer places the active host's hosting/<host>/history.py under the bundle's
    scripts/runtime/, so a script reads run records by one bare name without knowing which host it
    runs on. Raises ConfigError when the module is absent, which means the bundle was rendered
    without a host that provides it.
    """
    try:
        return __import__(name)
    except ImportError as exc:
        raise ConfigError(
            f"host module {name!r} not importable: the bundle was rendered without a host that "
            f"provides hosting/<host>/{name}.py, or scripts/runtime is not on sys.path "
            "(call runner.bootstrap() first)."
        ) from exc


def state_dir(worker: str) -> Path:
    """Where a worker keeps files between runs, created on first use."""
    base = os.environ.get("FACTORY_STATE_DIR")
    if not base:
        host = bundle_root() / "host.json"
        base = (_read(host).get("paths") or {}).get("stateDir")
        if not base:
            raise ConfigError(f"{host} has no paths.stateDir; render the bundle again with a host profile that names it")
    path = Path(os.path.expanduser(base)) / worker
    path.mkdir(parents=True, exist_ok=True)
    return path
