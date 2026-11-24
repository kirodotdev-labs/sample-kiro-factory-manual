"""Render a factory bundle from the Manual, one instance and one host profile.

    python3 runtime/render/render.py --instance examples/acme --host examples/acme/hosts/local.json --out <dir>

Stdlib only. Deterministic: the same inputs give byte-identical output.

Steps. Each directory under workers/ with a defaults.json is a step, and a worker of the same name
runs it. A step runs when its `enabled` is true (the default when defaults.json omits it), and the
instance's `workers.<name>.enabled` overrides that. defaults.json names the step's implementations as paths beside it: `agent` (a
prompt an agent follows) and `script` (an executable; `.py` runs with the host's Python), and
`implementation` picks one. The instance's `workers.<name>` overrides any key; its `agent` and
`script` paths are relative to the instance directory. An instance can add a worker the Manual does
not ship the same way.

Directives in a prompt and in workers/common/*.md:

    <!-- include: common/<name> -->      workers/common/<name>.md
    <!-- include: integration <kind> --> a pointer to the skill of each configured integration of that kind
    <!-- include: instance -->           the instance's rules.md, fenced
    <!-- include: verification -->       the worker's verification signals, one line each
    {{config.*}} {{label.*}} {{host.*}} {{worker.*}} {{instance.*}}
                                         values from the config, the vocabulary, the host paths, the
                                         worker (name, schedule, timezone, claimTtlMinutes, settings) and the instance
                                         (name; workers: the enabled worker names)

Skills. The bundle's skills/ holds the skill of each configured integration
(integrations/<kind>/<name>/SKILL.md, named <kind>-<name>, with the kind's KIND.md as
references/capabilities.md), every signal skill in workers/signals/ and step skill in workers/skills/
that an enabled worker names, and every skill in the instance's skills/, which wins over a Manual
skill of the same name. A step's defaults.json `skills` and the instance's `workers.<name>.skills`
add up. Values are expanded in each SKILL.md and
references/*.md. The configured agent harness (integrations/harness/, kiro-cli by default) renders one
agent per worker with the skills that worker names, every integration's and each instance skill no
worker names, plus the MCP servers below. The harness discloses skills its own way: the name and
description up front and the body when the agent needs it. An integration that ships an mcp_proxy.py becomes one stdio MCP
server, named after its kind. The harness kind ships no skill: the agent never calls it.

Modules. Each module given as `--module <name>=<dir>` and listed in the config's `modules` adds its
module.json `workers` block between the Manual's steps and the instance's. The README's Modules
section is the reference for what a module may hold.

The host profile (host.schema.json) names where the bundle and its working directories are on the
machine that runs it; those paths are written into the prompts and the harness config as given.
`--out` is where this render writes, which is the same directory only when the render runs on that
machine. The render replaces `--out` whole, so it refuses a directory that is neither empty nor a
bundle, and a render that fails leaves the previous bundle as it was.

The bundle's instance-source/ is a copy of the instance directory as written, with no value
expanded, so a worker can propose a change to the instance as a patch.

Verification. A worker's `verification` lists the signals its agent gathers before deciding to
proceed, iterate, wait or ask a human. Each entry is `{"skill": <name>}` (a skill the prompt tells
the agent to follow) or `{"script": <path>}` (an executable the agent runs with the subject as its only
argument). A step's default signals live in workers/signals/, shared by the steps that use them; an
instance's are relative to the instance. A signal returns plain text; the step's prompt makes the decision.
The instance's `workers.<name>.verification` adds to the step's list; an entry naming a default
signal with `"enabled": false` turns that one off.

A passthrough input check names what it trusts with `relies-on: capability <kind>.<cap>` or
`relies-on: worker <name>` lines in the implementation, and the render fails when that is not
configured. Write generically: name capabilities, not platforms, and leave project values to the
instance.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jsonschema_lite import validate  # noqa: E402

NAMESPACES = ("config", "label", "host", "worker", "instance")
VALUE_RE = re.compile(r"\{\{\s*((?:%s)(?:\.[A-Za-z0-9_\-]+)*)\s*\}\}" % "|".join(NAMESPACES))
INCLUDE_RE = re.compile(r"^<!--\s*include:\s*(common/[a-z0-9\-]+|integration\s+[a-z\-]+|instance|verification)\s*-->\s*$", re.M)
RELIES_RE = re.compile(r"^(?:#\s*)?relies-on:\s*(capability|worker)\s+([a-z0-9_.\-]+)\s*$", re.M)
FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CONTRACT_HEADINGS = ("## Inputs", "## Outputs", "## Verification", "## Decisions")
# A claim outlives the run that took it by this much: the runner kills a run at its timeout and holds
# the worker's lease for the same span, so after it no live run can still hold the claim.
AGENT_MIN_TIMEOUT_SECS = 60  # an agent run cannot do a step's work in less
CLAIM_MARGIN_SECS = 300
SCRIPT_ASSETS = ("*.py", "*.sh", "*.html", "*.css", "*.js", "*.png")
# An integration that ships this file gets a stdio MCP server in the harness config.
MCP_PROXY = "mcp_proxy.py"
MCP_PROXY_ASSETS = ("tool_catalog.json",)
DEFAULT_HARNESS = "kiro-cli"
# A value an example profile leaves for the adopter to fill in, such as "<factory home>/bundle".
PLACEHOLDER_RE = re.compile(r"<[^<>]+>")
# The host paths a run writes to. One inside the bundle is deleted by the next render.
WORKING_PATHS = ("checkout", "worktreeRoot", "stateDir")
CRON_FIELD_RE = re.compile(r"^[0-9A-Za-z*/,\-]+$")


class RenderError(Exception):
    pass


def step_dirs(manual: Path) -> dict[str, Path]:
    """Every Manual step by name: workers/<name>/ with a defaults.json."""
    return {d.parent.name: d.parent for d in sorted((manual / "workers").glob("*/defaults.json"))}


def skill_front(text: str) -> dict[str, str]:
    """The name and description from a SKILL.md's front matter (flat `key: value` lines)."""
    m = FRONT_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def _unquoted_yaml_value(text: str) -> str:
    """The first front matter key whose unquoted value a YAML parser rejects, else "".

    Harnesses parse front matter as YAML and silently skip a skill that fails, so `: ` or ` #` in an
    unquoted value drops the skill with no error at run time."""
    m = FRONT_RE.match(text)
    for line in (m.group(1).splitlines() if m else []):
        key, sep, value = line.partition(":")
        value = value.strip()
        if sep and value and value[0] not in "\"'" and (": " in value or " #" in value or value.endswith(":")):
            return key.strip()
    return ""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise RenderError(f"missing {path}")
    except ValueError as exc:
        raise RenderError(f"{path}: invalid JSON: {exc}")


def _dump(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _json_kind(value) -> str:
    """A setting's JSON type, so an override keeps its default's: a boolean is not a number."""
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return "a number"
    if isinstance(value, float):
        return "a number" if math.isfinite(value) else "not a finite number"
    return {str: "a string", list: "a list", dict: "an object"}.get(type(value), "null")


def _within(path: str, parent: str) -> bool:
    """Whether `path` is `parent` or inside it, comparing the paths as written (`~` expanded)."""
    path, parent = (os.path.normpath(os.path.expanduser(p)) for p in (path, parent))
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def _is_bundle(path: Path) -> bool:
    """Whether a directory is a render's output: its manifest.json carries what a render writes."""
    try:
        manifest = json.loads((path / "manifest.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and {"manualCommit", "contentSha256"} <= set(manifest)


@functools.lru_cache(maxsize=1)
def _timezones() -> set[str]:
    """The IANA zone names this machine knows; empty when it has no zone data to check against."""
    try:
        import zoneinfo
        return zoneinfo.available_timezones()
    except Exception:  # noqa: BLE001 - no zone data is not a reason to refuse a render
        return set()


def _merge_override(slot: dict, over: dict) -> None:
    for key, value in over.items():
        cur = slot.get(key)
        if isinstance(value, dict) and isinstance(cur, dict) and value.get("use", cur.get("use")) == cur.get("use"):
            _merge_override(cur, value)
        else:
            slot[key] = value

class Renderer:
    def __init__(self, manual: Path, instance: Path, host: Path, modules: dict[str, Path] | None = None):
        self.manual, self.instance = manual, instance
        self.host, self.host_path = _read_json(host), host
        self.check_host()
        self.config = _read_json(instance / "factory-config.json")
        self.check_schema()
        supplied = dict(modules or {})
        missing = [m for m in self.config.get("modules", []) if m not in supplied]
        if missing:
            raise RenderError(f"modules: {', '.join(missing)} listed in the config but not given to the render "
                              "(--module <name>=<dir>)")
        # The modules the instance lists, in its order: (name, directory, module.json).
        self.modules = [(m, supplied[m], _read_json(supplied[m] / "module.json"))
                        for m in self.config.get("modules", [])]
        integrations = self.config.setdefault("integrations", {})
        integrations.setdefault("harness", {"use": DEFAULT_HARNESS})
        # A host fills in or swaps integration values: a path only that host has (a secret file), or a
        # whole integration it cannot serve (for example a dashboard-only notifier). Values merge into
        # the instance's; an override naming a different `use` replaces that integration.
        _merge_override(integrations, self.host.get("integrationOverrides", {}))
        self.commit = _git_commit(manual)
        self.kinds = {p.parent.name: _read_json(p) for p in sorted((manual / "integrations").glob("*/kind.json"))}
        # A channel the config leaves out posts nowhere, so a prompt that names it still has a skill to follow.
        for kind, spec in self.kinds.items():
            if (manual / "integrations" / kind / "none").is_dir():
                for channel in spec.get("channels", []):
                    integrations.setdefault(kind, {}).setdefault(channel, {"use": "none"})
        self.steps = step_dirs(manual)

    # ---------- config ----------
    def check_host(self) -> None:
        here = Path(__file__).resolve().parent
        errors = validate(self.host, _read_json(here / "host.schema.json"))
        if not isinstance(self.host, dict):
            raise RenderError(f"invalid host profile {self.host_path}:\n  " + "\n  ".join(errors))
        overrides = self.host.get("workerOverrides")
        for name, entry in sorted(overrides.items()) if isinstance(overrides, dict) else []:
            errors += validate(entry, {"$ref": "#/$defs/worker"}, _read_json(here / "config.schema.json"),
                               f"$.workerOverrides.{name}")
        paths = self.host.get("paths")
        paths = paths if isinstance(paths, dict) else {}
        bundle = paths.get("bundle")
        for key, value in sorted(paths.items()):
            if not isinstance(value, str):
                continue
            if PLACEHOLDER_RE.search(value):
                errors.append(f"$.paths.{key}: {value!r} is the example's placeholder; write the real path")
            elif key == "bundle" and not value.startswith("/"):
                errors.append(f"$.paths.bundle: {value!r} must be an absolute path: the prompts and the "
                              "harness config carry it as written")
            elif key in WORKING_PATHS and isinstance(bundle, str) and _within(value, bundle):
                errors.append(f"$.paths.{key}: {value!r} is inside the bundle, which every render replaces; "
                              "keep it beside the bundle")
        if errors:
            raise RenderError(f"invalid host profile {self.host_path}:\n  " + "\n  ".join(errors))
        paths.setdefault("python", "python3")

    def check_schema(self) -> None:
        errors = validate(self.config, _read_json(Path(__file__).resolve().parent / "config.schema.json"))
        if errors:
            raise RenderError("invalid factory-config.json:\n  " + "\n  ".join(errors))

    def check_config(self) -> None:
        self.check_schema()  # again, now with the host's overrides merged in
        errors = []
        runtime = str(self.host.get("runtime", self.host.get("name", "")))
        needs = self.manual / "hosting" / runtime / "config.schema.json"
        if needs.exists():
            schema = _read_json(needs)
            unmet = validate(self.config, schema)
            if unmet:
                raise RenderError(f"host {runtime}: {schema.get('description', 'the config lacks what this host needs')}\n  "
                                  + "\n  ".join(unmet))
        self.check_module_integrations()
        for kind, slot, cfg in self.integration_slots():
            name = cfg.get("use")
            d = self.integration_dir(kind, name)
            if d is None:
                errors.append(f"$.integrations.{slot}: no integration {kind}/{name} in the Manual, a module or "
                              "the instance")
                continue
            sch = d / "config.schema.json"
            if sch.exists():
                body = {k: v for k, v in cfg.items() if k != "use"}
                schema = _read_json(sch)
                errors += validate(body, schema, path=f"$.integrations.{slot}")
                for key, prop in schema.get("properties", {}).items():
                    if "default" in prop:
                        cfg.setdefault(key, prop["default"])
        if errors:
            raise RenderError("invalid factory-config.json:\n  " + "\n  ".join(errors))

    def integration_slots(self):
        """Yield (kind, slot, cfg) for each configured integration. A kind with channels has one slot per channel."""
        for kind, cfg in sorted(self.config.get("integrations", {}).items()):
            if self.kinds.get(kind, {}).get("channels"):
                for channel, ccfg in sorted(cfg.items()):
                    yield kind, f"{kind}.{channel}", ccfg
            else:
                yield kind, kind, cfg

    def check_module_integrations(self) -> None:
        """A module's integrations implement a Manual kind under a name no Manual or other module integration has."""
        owner: dict[str, str] = {}
        for m, mdir, _spec in self.modules:
            dirs = (mdir / "integrations").glob("*/*") if (mdir / "integrations").is_dir() else []
            for d in sorted(p for p in dirs if p.is_dir()):
                kind, name = d.parent.name, d.name
                if kind not in self.kinds:
                    raise RenderError(f"module {m}: integrations/{kind} is not a Manual integration kind")
                if (self.manual / "integrations" / kind / name).is_dir():
                    raise RenderError(f"module {m}: integration {kind}/{name} is already the Manual's; rename it")
                if f"{kind}/{name}" in owner:
                    raise RenderError(f"module {m}: integration {kind}/{name} is already module "
                                      f"{owner[f'{kind}/{name}']}'s")
                owner[f"{kind}/{name}"] = m

    def integration_dir(self, kind: str, name: str | None) -> Path | None:
        """The instance's integration, else a module's, else the Manual's."""
        if not name:
            return None
        marker = "SKILL.md" if self.agent_facing(kind) else "client.py"
        modules = (mdir / "integrations" for _m, mdir, _spec in self.modules)
        for base in (self.instance / "integrations", *modules, self.manual / "integrations"):
            d = base / kind / name
            if (d / marker).exists():
                return d
        return None

    def agent_facing(self, kind: str) -> bool:
        return self.kinds.get(kind, {}).get("agentFacing", True)

    def vocabulary(self) -> dict:
        vocab = _read_json(self.manual / "workers" / "common" / "vocabulary.json")
        over = self.config.get("vocabulary", {})
        for key, value in over.get("labels", {}).items():
            if key not in vocab["labels"]:
                raise RenderError(f"vocabulary.labels.{key} is not a factory label")
            vocab["labels"][key] = value
        return vocab

    # ---------- workers ----------
    # What a module's worker entry may hold: a worker it declares in full, or what it adds to one.
    MODULE_ADDS = {"skills", "context", "settings"}
    MODULE_DECLARES = {"description", "enabled", "implementation", "agent", "script", "model", "requires", "schedule",
                       "timezone", "timeoutSecs"} | MODULE_ADDS

    def check_modules(self, overrides: dict) -> None:
        """A module declares workers the Manual does not ship, each in one module, and adds to workers
        that exist; it sets no verification signals (the instance does)."""
        owner: dict[str, str] = {}
        for m, _mdir, spec in self.modules:
            extra = sorted(set(spec) - {"_README", "workers"})
            if extra:
                raise RenderError(f"module {m}: module.json has unknown key(s) {extra}")
            for name, entry in spec.get("workers", {}).items():
                if "implementation" in entry:
                    if name in self.steps:
                        raise RenderError(f"module {m}: workers.{name} is a Manual step; a module adds to it and "
                                          "does not declare it again")
                    if name in owner:
                        raise RenderError(f"module {m}: workers.{name} is already declared by module {owner[name]}")
                    owner[name] = m
        for m, _mdir, spec in self.modules:
            for name, entry in spec.get("workers", {}).items():
                allowed = self.MODULE_DECLARES if "implementation" in entry else self.MODULE_ADDS
                bad = sorted(set(entry) - allowed)
                if bad:
                    raise RenderError(f"module {m}: workers.{name}: {', '.join(bad)} not something a module sets"
                                      + (" (the instance sets verification)" if "verification" in bad else
                                         "; a module adds only context, skills and settings to a worker it does "
                                         "not declare, and the instance config sets the rest"
                                         if "implementation" not in entry else ""))
                if "implementation" in entry:
                    continue
                if name in owner:
                    raise RenderError(f"module {m}: workers.{name} is module {owner[name]}'s; a module adds only "
                                      "to Manual steps and the instance's workers, so modules do not depend on "
                                      "each other")
                if name not in self.steps and name not in owner and name not in overrides:
                    raise RenderError(f"module {m}: workers.{name} names no worker: not a Manual step, nor declared "
                                      "by a module or the instance")

    def workers(self) -> dict[str, dict]:
        """The enabled workers, each resolved to one implementation file."""
        tz = self.config["instance"].get("timezone", "UTC")
        overrides = {k: dict(v) for k, v in self.config.get("workers", {}).items()}
        module_workers = {n for _m, _d, spec in self.modules for n in spec.get("workers", {})}
        for name, entry in overrides.items():
            if (name not in self.steps and name not in module_workers and "implementation" not in entry
                    and entry.get("enabled", True)):
                raise RenderError(f"workers.{name} names no worker: not a Manual step, nor declared by a "
                                  "module, and the entry has no implementation")
        # A host may disable or retune a worker it cannot serve, e.g. one that needs a desktop. A host
        # is shared by instances, so it skips a worker this instance does not have.
        for k, v in self.host.get("workerOverrides", {}).items():
            if k in self.steps or k in module_workers or k in overrides:
                overrides.setdefault(k, {}).update(v)
        # Layers over each step's defaults: each module's workers block, then the instance's.
        layers = [(f"module {m}", mdir, spec.get("workers", {})) for m, mdir, spec in self.modules]
        layers.append(("workers", self.instance, overrides))
        self.check_modules(overrides)
        zones = _timezones()
        out: dict[str, dict] = {}
        for name in sorted(set(self.steps).union(*(set(ws) for _, _, ws in layers))):
            d = self.steps.get(name)
            # A module worker is one a module's entry declares in full, with its implementation.
            declared = next(((base, ws[name]) for _, base, ws in layers[:-1]
                             if "implementation" in ws.get(name, {})), None)
            w = dict(_read_json(d / "defaults.json")) if d else {}
            for key in ("agent", "script"):
                if key in w:
                    w[key] = d / w[key]
            # The settings a worker takes: its Manual step's, or what the module declaring it lists.
            known = (set(w.get("settings", {})) if d else
                     set(declared[1].get("settings", {})) if declared else None)
            for where, base, ws in layers:
                over = dict(ws.get(name, {}))
                for key in ("agent", "script"):
                    if key in over:
                        over[key] = base / over[key]
                if base is self.instance and ("verification" in w or "verification" in over):
                    w["verification"] = self.merge_signals(name, d, w.get("verification", []),
                                                           over.pop("verification", []))
                if "skills" in over:
                    w["skills"] = list(dict.fromkeys([*w.get("skills", []), *over.pop("skills")]))
                if "context" in over:  # a module's; the schema keeps it out of an instance's config
                    w["context"] = [*w.get("context", []), *(base / c for c in over.pop("context"))]
                settings = over.pop("settings", {})
                unknown = sorted(set(settings) - known) if known is not None else []
                if unknown:
                    raise RenderError(f"{where}: workers.{name}.settings: {', '.join(unknown)} not a setting "
                                      f"of the {name} step (its defaults.json lists {sorted(known) or 'none'})")
                for key, value in settings.items():
                    default = w.get("settings", {}).get(key)
                    if default is not None and _json_kind(value) != _json_kind(default):
                        raise RenderError(f"{where}: workers.{name}.settings.{key} is {_json_kind(value)}, "
                                          f"but the {name} step's default is {_json_kind(default)}")
                if settings:
                    w["settings"] = dict(w.get("settings", {}), **settings)
                w.update(over)
            if not w.get("enabled", True):
                continue
            impl = w.get("implementation")
            if impl not in ("agent", "script"):
                raise RenderError(f"worker {name}: implementation must be agent or script")
            if impl == "agent" and int(w.get("timeoutSecs") or 1800) < AGENT_MIN_TIMEOUT_SECS:
                raise RenderError(f"worker {name}: timeoutSecs is {w['timeoutSecs']}; an agent worker needs at least "
                                  f"{AGENT_MIN_TIMEOUT_SECS}")
            source = w.get(impl)
            if source is None:
                raise RenderError(f"worker {name}: implementation {impl} needs workers.{name}.{impl}, "
                                  "because the Manual ships none for this step")
            if not Path(source).is_file():
                raise RenderError(f"worker {name}: {impl} {source} does not exist")
            if "schedule" not in w:
                raise RenderError(f"worker {name}: no schedule")
            fields = str(w["schedule"]).split()
            if len(fields) != 5 or not all(CRON_FIELD_RE.match(f) for f in fields):
                raise RenderError(f"worker {name}: schedule {w['schedule']!r} is not a five-field cron expression "
                                  "(minute hour day-of-month month day-of-week)")
            if zones and w.get("timezone", tz) not in zones:
                raise RenderError(f"worker {name}: timezone {w.get('timezone', tz)!r} is not an IANA zone name "
                                  "such as America/Los_Angeles")
            if impl == "script" and w.get("context"):
                raise RenderError(f"worker {name}: context is for an agent, and this worker runs a script")
            if impl == "script" and w.get("verification"):
                raise RenderError(f"worker {name}: verification signals need an agent to weigh them, "
                                  "and this worker runs a script")
            w.pop("script" if impl == "agent" else "agent", None)
            w.update(enabled=True, source=Path(source))
            w.setdefault("timezone", tz)
            w.setdefault("requires", [])
            w["runTimeoutSecs"] = int(w.get("timeoutSecs") or 1800)
            w["claimTtlSecs"] = w["runTimeoutSecs"] + CLAIM_MARGIN_SECS
            out[name] = w
        return out

    def check_requires(self, workers: dict[str, dict]) -> None:
        slots = {slot for _, slot, _ in self.integration_slots()} | {k for k, _, _ in self.integration_slots()}
        for name, w in workers.items():
            for req in w["requires"]:
                if req not in slots:
                    raise RenderError(f"worker {name} requires integration {req!r}, which the config does not set: "
                                      f"set integrations.{req}, or turn the worker off with "
                                      f'"workers": {{"{name}": {{"enabled": false}}}}')

    def check_relies_on(self, name: str, text: str, workers: dict[str, dict]) -> None:
        for kind_of, target in RELIES_RE.findall(text):
            if kind_of == "worker":
                if target not in workers:
                    raise RenderError(f"worker {name} relies on worker {target!r}, which is not enabled")
            else:
                kind, _, cap = target.partition(".")
                if kind not in self.config.get("integrations", {}):
                    raise RenderError(f"worker {name} relies on {target}, but no {kind} integration is configured")
                if cap not in self.kinds.get(kind, {}).get("capabilities", []):
                    raise RenderError(f"worker {name} relies on unknown capability {target}")

    def merge_signals(self, worker: str, step_dir: Path | None, defaults, extra) -> list[dict]:
        """The step's default signals, minus those the instance disables, then the instance's own."""
        where = f"workers.{worker}.verification"
        defaults = self.check_signals(defaults, f"{step_dir}/defaults.json", allow_enabled=False)
        extra = self.check_signals(extra, where, allow_enabled=True)
        known = {self.signal_key(e) for e in defaults}
        disabled = set()
        added = []
        for e in extra:
            key = self.signal_key(e)
            if key in known:
                if e.get("enabled", True) is False:
                    disabled.add(key)
            elif "enabled" in e:
                raise RenderError(f"{where}: {key[0]} {key[1]!r} is not a default signal of {worker}, "
                                  "so it has nothing to enable or disable")
            else:
                added.append(e)
        out = [self.resolve_signal(e, self.manual / "workers" / "signals", f"{step_dir}/defaults.json")
               for e in defaults if self.signal_key(e) not in disabled]
        return out + [self.resolve_signal(e, self.instance, where) for e in added]

    @staticmethod
    def check_signals(entries, where: str, allow_enabled: bool) -> list[dict]:
        if not isinstance(entries, list):
            raise RenderError(f"{where}: verification must be a list")
        for e in entries:
            kinds = [k for k in ("skill", "script") if k in e] if isinstance(e, dict) else []
            rest = set(e) - set(kinds) if isinstance(e, dict) else set()
            ok = (len(kinds) == 1 and isinstance(e[kinds[0]], str)
                  and (not rest or (allow_enabled and rest == {"enabled"} and isinstance(e["enabled"], bool))))
            if not ok:
                shape = '{"skill": <name>} or {"script": <path>}'
                if allow_enabled:
                    shape += ', optionally with "enabled": false to turn a default off'
                raise RenderError(f"{where}: a verification entry is {shape}, not {e!r}")
        return entries

    @staticmethod
    def signal_key(e: dict) -> tuple[str, str]:
        if "skill" in e:
            return "skill", e["skill"]
        return "script", Path(e["script"]).name

    @staticmethod
    def resolve_signal(e: dict, base: Path, where: str) -> dict:
        """{"skill": name} or {"script": absolute path}."""
        if "skill" in e:
            return {"skill": e["skill"]}
        path = base / e["script"]
        if not path.is_file():
            raise RenderError(f"{where}: verification script {path} does not exist")
        return {"script": path}

    def signal_entry(self, worker: str, path: Path) -> str:
        """A verification script's path under the bundle's scripts/."""
        if self.instance in path.parents:
            rel = path.relative_to(self.instance)
            if rel.parts[0] != "scripts":
                raise RenderError(f"worker {worker}: an instance verification script must be under scripts/, not {rel}")
            return str(Path("instance", *rel.parts[1:]))
        return str(Path("signals", path.name))

    def verification_text(self, worker: str, signals: list[dict]) -> str:
        if not signals:
            return ""
        lines = ["Verification signals. Gather every one against the subject's current revision, then weigh them together:"]
        for s in signals:
            if "skill" in s:
                lines.append(f"- `{s['skill']}`: follow the `{s['skill']}` skill.")
            else:
                entry = "{{host.bundle}}/scripts/" + self.signal_entry(worker, s["script"])
                cmd = f"{{{{host.python}}}} {entry}" if s["script"].suffix == ".py" else entry
                lines.append(f"- `{s['script'].stem}`: run `{cmd} <subject>` and read its output.")
        return "\n" + "\n".join(lines) + "\n"

    # ---------- text expansion ----------
    def expand_includes(self, text: str, seen: tuple = (), verification: str = "") -> str:
        def repl(m: re.Match) -> str:
            target = m.group(1)
            if target == "verification":
                return verification
            if target == "instance":
                name = self.config["instance"]["name"]
                parts = []
                goal = (self.config.get("goal") or "").strip()
                if "{{" in goal:
                    raise RenderError("goal: write it as plain text; it takes no {{...}} values")
                if goal:
                    parts.append(f"The factory's goal for {name}: {goal}\n"
                                 "Where this step leaves a choice open, prefer what serves the goal. It does not "
                                 "override a guardrail, a person's decision or this step's rules.")
                path = self.instance / "rules.md"
                if path.exists():
                    parts.append(f"=== instance {name} rules ===\n{path.read_text().strip()}\n=== end instance rules ===")
                return "\n" + "\n\n".join(parts) + "\n" if parts else ""
            if target.startswith("common/"):
                cname = target.split("/", 1)[1]
                if cname in seen:
                    raise RenderError(f"include cycle through common/{cname}")
                path = self.manual / "workers" / "common" / f"{cname}.md"
                if not path.exists():
                    raise RenderError(f"include of missing common/{cname}")
                return self.expand_includes(path.read_text(), seen + (cname,), verification).rstrip("\n")
            kind = target.split()[1]
            cfg = self.config.get("integrations", {}).get(kind)
            if cfg is None:
                raise RenderError(f"include of integration {kind}, which the config does not set")
            channels = self.kinds.get(kind, {}).get("channels")
            items = sorted(cfg.items()) if channels else [(None, cfg)]
            lines = []
            for channel, ccfg in items:
                where = f"`{kind}` channel `{channel}`" if channel else f"each `{kind}` capability"
                lines.append(f"For {where}, follow the `{kind}-{ccfg['use']}` skill.")
            return "\n".join(lines)

        return INCLUDE_RE.sub(repl, text)

    def expand_values(self, text: str, ctx: dict, where: str) -> str:
        def repl(m: re.Match) -> str:
            value = ctx
            for part in m.group(1).split("."):
                if not isinstance(value, dict) or part not in value:
                    raise RenderError(f"{where}: unknown value {{{{{m.group(1)}}}}}")
                value = value[part]
            return value if isinstance(value, str) else json.dumps(value, sort_keys=True)

        return VALUE_RE.sub(repl, text)

    # ---------- skills ----------
    def skill_sources(self) -> dict[str, tuple[Path, Path | None]]:
        """name -> (skill directory, kind's KIND.md or None). Instance skills win over Manual ones."""
        out: dict[str, tuple[Path, Path | None]] = {}
        for kind, slot, cfg in self.integration_slots():
            if not self.agent_facing(kind):
                continue
            d = self.integration_dir(kind, cfg["use"])
            kind_md = self.manual / "integrations" / kind / "KIND.md"
            out[f"{kind}-{cfg['use']}"] = (d, kind_md if kind_md.exists() else None)
        modules = {mdir / "skills": m for m, mdir, _spec in self.modules}
        origin = {name: "the Manual" for name in out}
        for base in (*self.manual_skill_dirs(), *modules, self.instance / "skills"):
            for d in sorted(base.glob("*/SKILL.md")) if base.is_dir() else []:
                name = d.parent.name
                if base in modules and name in origin:
                    raise RenderError(f"module {modules[base]}: skill {name} is already {origin[name]}'s; "
                                      "rename it, because a module's skill names differ from the Manual's "
                                      "and other modules'")
                origin[name] = f"module {modules[base]}" if base in modules else "the Manual"
                out[name] = (d.parent, None)
        return out

    def manual_skill_dirs(self) -> tuple[Path, Path]:
        """The Manual's signal skills and step skills."""
        return self.manual / "workers" / "signals", self.manual / "workers" / "skills"

    def on_demand_skill_dirs(self) -> tuple[Path, ...]:
        """Skill directories whose skills ship only when an enabled worker names them: the Manual's
        signals and step skills, then each module's skills."""
        return (*self.manual_skill_dirs(), *(mdir / "skills" for _m, mdir, _spec in self.modules))

    def render_skills(self, out: Path, ctx: dict, used: set[str]) -> list[str]:
        names = []
        on_demand = self.on_demand_skill_dirs()
        for name, (src, kind_md) in sorted(self.skill_sources().items()):
            # A Manual signal or step skill no enabled worker names stays out: its description alone could load it.
            if src.parent in on_demand and name not in used:
                continue
            if not SKILL_NAME_RE.match(name):
                raise RenderError(f"skill name {name!r} must be lowercase letters, digits and hyphens")
            dest = out / "skills" / name
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "config.schema.json"))
            if kind_md is not None:
                (dest / "references").mkdir(exist_ok=True)
                shutil.copy2(kind_md, dest / "references" / "capabilities.md")
            skill_md = dest / "SKILL.md"
            front = skill_front(skill_md.read_text())
            if front.get("name") != name:
                raise RenderError(f"{src}/SKILL.md: front matter name must be {name!r}")
            if not front.get("description"):
                raise RenderError(f"{src}/SKILL.md has no description")
            bad = _unquoted_yaml_value(skill_md.read_text())
            if bad:
                raise RenderError(f"{src}/SKILL.md: front matter {bad!r} is not valid YAML unquoted; quote the value")
            for md in [skill_md] + sorted((dest / "references").glob("*.md")):
                md.write_text(self.expand_values(md.read_text(), ctx, f"skills/{name}/{md.name}"))
            names.append(name)
        return names

    def mcp_servers(self) -> dict[str, dict]:
        """One stdio MCP server per integration that ships an mcp_proxy.py, named after its kind."""
        paths = self.host.get("paths", {})
        bundle = paths.get("bundle", "")
        servers = {}
        for kind, _slot, icfg in self.integration_slots():
            d = self.integration_dir(kind, icfg.get("use"))
            if d and (d / MCP_PROXY).exists():
                servers[kind] = {
                    "command": paths.get("python") or "python3",
                    "args": [f"{bundle}/scripts/integrations/{kind}/{MCP_PROXY}"],
                    "env": {"FACTORY_BUNDLE": bundle},
                }
        return servers

    def worker_loads(self, out: Path, skills: list[str], named: dict[str, tuple], workers: dict[str, dict],
                     ctx: dict) -> dict[str, dict]:
        """What each agent worker's harness agent loads. Context: the files its modules give it, copied
        to modules/<module>/ in the bundle, in the model's context from the start. Skills, disclosed
        progressively: the ones it names (its step's, its config's and its signals'), every
        integration's, and each instance skill no worker names."""
        bundle = self.host.get("paths", {}).get("bundle", "")
        sources = self.skill_sources()
        integrations = [s for s in skills if sources[s][1] is not None]
        instance = {d.parent.name for d in (self.instance / "skills").glob("*/SKILL.md")} \
            if (self.instance / "skills").is_dir() else set()
        shared = sorted(instance - {s for names in named.values() for s in names})
        loads = {}
        for name, names in named.items():
            context = []
            for path in workers[name].pop("context", []):
                mod = next(((m, mdir) for m, mdir, _spec in self.modules if mdir in path.parents), None)
                if mod is None or not path.is_file():
                    raise RenderError(f"worker {name}: context {path} is not a file in a module")
                rel = Path("modules", mod[0], path.relative_to(mod[1]))
                text = self.expand_values(path.read_text(), ctx, str(path))
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                (out / rel).write_text(text)
                context.append({"path": f"{bundle}/{rel}", "chars": len(text)})
            loads[name] = {"context": context,
                           "skills": [f"{bundle}/skills/{s}/SKILL.md"
                                      for s in dict.fromkeys([*names, *integrations, *shared]) if s in skills]}
        return loads

    def harness_files(self, loads: dict[str, dict]) -> dict[str, str]:
        cfg = self.config["integrations"]["harness"]
        client = self.integration_dir("harness", cfg["use"]) / "client.py"
        spec = importlib.util.spec_from_file_location(f"harness_{cfg['use'].replace('-', '_')}", client)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        bundle = self.host.get("paths", {}).get("bundle", "")
        try:
            files = mod.render(cfg, bundle=bundle, workers=loads, mcp_servers=self.mcp_servers())
        except ValueError as exc:
            raise RenderError(f"harness {cfg['use']}: {exc}")
        for rel in files:
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                raise RenderError(f"harness {cfg['use']}: render returned a path outside the bundle: {rel}")
        return files

    # ---------- render ----------
    def render(self, dest: Path) -> dict:
        """Write the bundle to `dest`, replacing a previous bundle there only once the new one is whole."""
        dest = dest.resolve()
        if dest.exists() and (not dest.is_dir() or (any(dest.iterdir()) and not _is_bundle(dest))):
            raise RenderError(f"--out {dest} exists and is not a bundle (it has no manifest.json of a "
                              "render's). The render replaces the whole directory, so name a new or empty one")
        if _within(str(dest), str(self.instance.resolve())):
            raise RenderError(f"--out {dest} is inside the instance directory, which the render copies into "
                              "the bundle: render to a directory outside it")
        stage = dest.parent / f".{dest.name}.rendering"
        shutil.rmtree(stage, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            manifest = self.render_into(stage)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        # Two renames, so the bundle's path is missing only for the instant between them: a run that
        # starts during a render finds the old bundle or the new one.
        old = dest.parent / f".{dest.name}.replaced"
        shutil.rmtree(old, ignore_errors=True)
        if dest.exists():
            dest.rename(old)
        try:
            stage.rename(dest)
        except BaseException:
            if old.exists():
                old.rename(dest)
            shutil.rmtree(stage, ignore_errors=True)
            raise
        shutil.rmtree(old, ignore_errors=True)
        return manifest

    def render_into(self, out: Path) -> dict:
        self.check_config()
        # Defaults filled in, and the post header derived from the title, for scripts and prompts to read
        # from the bundle's config.
        inst = self.config["instance"]
        inst["title"] = (inst.get("title") or inst["name"].replace("-", " ").title() + " Factory").strip()
        inst.setdefault("timezone", "UTC")
        inst["header"] = f"🏭 **{inst['title']}** · automated agent"
        vocab = self.vocabulary()
        workers = self.workers()
        self.check_requires(workers)
        (out / "prompts").mkdir(parents=True)
        base_ctx = {
            "config": self.config,
            "label": vocab["labels"],
            "host": self.host.get("paths", {}),
            "instance": {"name": self.config["instance"]["name"], "workers": ", ".join(sorted(workers))},
        }
        agent_dirs = {}
        used: set[str] = set()
        named: dict[str, tuple] = {}
        for name, w in workers.items():
            source = w.pop("source")
            if w["implementation"] == "agent" and self.instance not in source.parents:
                agent_dirs[name] = source.parent
            self.check_relies_on(name, source.read_text(errors="replace"), workers)
            if w["implementation"] != "agent":
                continue
            w.pop("agent")
            signals = w.get("verification", [])
            always = tuple(w.get("skills", []))
            always += tuple(s["skill"] for s in signals if "skill" in s and s["skill"] not in always)
            known = set(self.skill_sources())
            missing = [s for s in always if s not in known]
            if missing:
                raise RenderError(f"worker {name}: workers.{name}.skills or verification names unknown skill(s) {missing}")
            used.update(always)
            text = self.expand_includes(source.read_text(), verification=self.verification_text(name, signals))
            named[name] = always
            ctx = dict(base_ctx, worker={"name": name, "schedule": w["schedule"], "timezone": w["timezone"],
                                         "claimTtlMinutes": w["claimTtlSecs"] // 60,
                                         "settings": w.get("settings", {})})
            text = self.expand_values(text, ctx, f"{source} (or the instance rules or a file it includes)")
            header = f"<!-- Rendered by the Kiro Factory Manual for instance {self.config['instance']['name']}. Do not edit. -->\n\n"
            text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
            (out / "prompts" / f"{name}.md").write_text(header + text + "\n")
            w["prompt"] = f"prompts/{name}.md"
        self.copy_scripts(out, workers, agent_dirs)
        # The instance as written, before any value is expanded: learn proposes its changes as
        # patches against these files.
        shutil.copytree(self.instance, out / "instance-source",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".*"))
        skills = self.render_skills(out, dict(base_ctx, worker={}), used)
        loads = self.worker_loads(out, skills, named, workers, dict(base_ctx, worker={}))
        (out / "factory-config.json").write_text(_dump(self.config))
        (out / "vocabulary.json").write_text(_dump(vocab))
        (out / "host.json").write_text(_dump({k: v for k, v in self.host.items() if not k.startswith("_")}))
        (out / "workers.json").write_text(_dump(workers))
        for rel, content in sorted(self.harness_files(loads).items()):
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / rel).write_text(content)
        manifest = {
            "manualCommit": self.commit,
            "instance": self.config["instance"]["name"],
            "host": self.host.get("name"),
            "skills": skills,
            "instanceRules": (self.instance / "rules.md").exists(),
            "modules": {m: _git_commit(mdir) for m, mdir, _spec in self.modules},
            "contentSha256": _tree_hash(out),
        }
        (out / "manifest.json").write_text(_dump(manifest))
        return manifest

    def copy_scripts(self, out: Path, workers: dict[str, dict], agent_dirs: dict[str, Path]) -> None:
        scripts = out / "scripts"
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.md")
        shutil.copytree(self.manual / "workers" / "lib", scripts / "lib", ignore=ignore)
        for kind, slot, cfg in self.integration_slots():
            d = self.integration_dir(kind, cfg["use"])
            dest = scripts / "integrations" / kind
            dest.mkdir(parents=True, exist_ok=True)
            iface = self.manual / "integrations" / kind / "interface.py"
            if iface.exists():
                shutil.copy2(iface, dest / "interface.py")
            if (d / "client.py").exists():
                shutil.copy2(d / "client.py", dest / f"{cfg['use'].replace('-', '_')}.py")
            if (d / MCP_PROXY).exists():
                for f in [MCP_PROXY, *MCP_PROXY_ASSETS]:
                    if not (d / f).is_file():
                        raise RenderError(f"integration {kind}/{cfg['use']} ships {MCP_PROXY} without the {f} "
                                          "it serves")
                    shutil.copy2(d / f, dest / f)
        # Every host runs workers with the same runner; the host directory adds its storage
        # (history.py: run records and leases).
        run = scripts / "runtime"
        run.mkdir(parents=True, exist_ok=True)
        for f in sorted((self.manual / "runtime" / "run").glob("*.py")):
            shutil.copy2(f, run / f.name)
        hist = self.manual / "hosting" / str(self.host.get("runtime", self.host.get("name", ""))) / "history.py"
        if not hist.exists():
            raise RenderError(f"host {self.host.get('name')!r} has no hosting/<runtime>/history.py in the Manual")
        shutil.copy2(hist, run / "history.py")
        if (self.instance / "scripts").is_dir():
            shutil.copytree(self.instance / "scripts", scripts / "instance", ignore=ignore)
        for name, w in workers.items():
            for s in w.get("verification", []):
                if "script" not in s:
                    continue
                path = s["script"]
                s["script"] = self.signal_entry(name, path)
                if self.instance not in path.parents:
                    dest = scripts / s["script"]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest)
            if w["implementation"] != "script":
                # An agent step may ship helper scripts its prompt runs from scripts/workers/<name>/.
                if name in agent_dirs:
                    self.copy_step_assets(agent_dirs[name], scripts / "workers" / name)
                continue
            source = Path(w.pop("script"))
            if self.instance in source.parents:
                rel = source.relative_to(self.instance)
                if rel.parts[0] != "scripts":
                    raise RenderError(f"worker {name}: an instance script must be under scripts/, not {rel}")
                entry = Path("instance", *rel.parts[1:])
            else:
                self.copy_step_assets(source.parent, scripts / "workers" / name)
                entry = Path("workers", name, source.name)
            w["entry"] = str(entry)

    @staticmethod
    def copy_step_assets(step: Path, dest: Path) -> None:
        files = [f for pattern in SCRIPT_ASSETS for f in sorted(step.glob(pattern))]
        if files:
            dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest / f.name)


def _git_commit(path: Path) -> str:
    """The Manual's commit, so a bundle records what it was rendered from: the COMMIT file a
    published Manual carries (build-tools/build), else the checkout's HEAD, else empty."""
    stamped = path / "COMMIT"
    if stamped.is_file():
        return stamped.read_text().strip()
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        h.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manual", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--instance", type=Path, required=True)
    ap.add_argument("--host", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--module", action="append", default=[], metavar="NAME=DIR",
                    help="a module the instance lists in its config's `modules`, and where it is")
    args = ap.parse_args(argv)
    modules = {}
    for m in args.module:
        name, sep, d = m.partition("=")
        if not sep or not name or not d:
            print(f"render failed: --module {m!r}: write it as <name>=<dir>", file=sys.stderr)
            return 1
        modules[name] = Path(d).resolve()
    try:
        manifest = Renderer(args.manual.resolve(), args.instance.resolve(), args.host.resolve(),
                            modules).render(args.out)
    except RenderError as exc:
        print(f"render failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2, sort_keys=True))
    unlisted = sorted(set(modules) - set(manifest["modules"]))
    if unlisted:
        print(f"note: --module {', '.join(unlisted)} given but not listed in the config's `modules`, so not "
              "used", file=sys.stderr)
    bundle = _read_json(args.host.resolve())["paths"]["bundle"]
    if Path(bundle).resolve() != args.out.resolve():
        print(f"note: rendered to {args.out.resolve()}; workers run this bundle from {bundle} "
              "(the host profile's paths.bundle), so install it there", file=sys.stderr)
    return 0


if __name__ == "__main__":
    os.umask(0o022)
    sys.exit(main())
