from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime" / "render"))
from render import Renderer, RenderError, step_dirs  # noqa: E402

ACME = ROOT / "examples" / "acme"
HOST = ACME / "hosts" / "local.json"


def render(instance=ACME, host=HOST):
    out = Path(tempfile.mkdtemp()) / "bundle"
    return Renderer(ROOT, instance, host).render(out), out


def make_module(spec: dict, steps: dict | None = None, skills=("mod-record",)) -> Path:
    """A module directory: module.json from `spec`, a script worker declared in it per `steps` entry, and
    skills."""
    d = Path(tempfile.mkdtemp()) / "mod"
    (d / "workers").mkdir(parents=True)
    spec = json.loads(json.dumps(spec))
    for name, defaults in (steps or {}).items():
        (d / "workers" / name).mkdir()
        (d / "workers" / name / "run.py").write_text("print('module step')\n")
        spec.setdefault("workers", {}).setdefault(name, {}).update(
            {"implementation": "script", "script": f"workers/{name}/run.py", "schedule": "0 * * * *", **defaults})
    (d / "module.json").write_text(json.dumps(spec))
    for sk in skills:
        (d / "skills" / sk).mkdir(parents=True)
        (d / "skills" / sk / "SKILL.md").write_text(f"---\nname: {sk}\ndescription: A module skill.\n---\n\nBody.\n")
    (d / "context").mkdir()
    (d / "context" / "record.md").write_text("End every post with the record for {{instance.name}}.\n")
    return d


def render_with_module(mod: Path, mutate=None):
    tmp = copy_acme()
    edit_config(tmp, lambda c: (c.update(modules=["mod"]), mutate and mutate(c)))
    out = Path(tempfile.mkdtemp()) / "bundle"
    return Renderer(ROOT, tmp, HOST, {"mod": mod}).render(out), out


def copy_acme() -> Path:
    tmp = Path(tempfile.mkdtemp()) / "acme"
    shutil.copytree(ACME, tmp)
    return tmp


def with_proxy_board(d: Path) -> Path:
    """An instance board integration that ships an MCP proxy, as a platform reached only through a
    server does."""
    b = d / "integrations" / "board" / "proxied"
    b.mkdir(parents=True)
    (b / "SKILL.md").write_text("---\nname: board-proxied\ndescription: A board behind an MCP server.\n---\n\nBody.\n")
    (b / "client.py").write_text("def from_config(cfg):\n    return None\n")
    (b / "mcp_proxy.py").write_text("print('proxy')\n")
    (b / "tool_catalog.json").write_text('{"tools": []}\n')
    edit_config(d, lambda c: c["integrations"].update(board={"use": "proxied", "boardId": "board-acme"}))
    return d


def edit_config(d: Path, mutate) -> None:
    cfg = json.loads((d / "factory-config.json").read_text())
    mutate(cfg)
    (d / "factory-config.json").write_text(json.dumps(cfg))


class RenderTest(unittest.TestCase):
    def test_example_renders_every_manual_step(self):
        _, out = render()
        workers = json.loads((out / "workers.json").read_text())
        self.assertEqual(set(workers), set(step_dirs(ROOT)) | {"flaky-tests"})
        for name, w in workers.items():
            if w["implementation"] == "agent":
                text = (out / w["prompt"]).read_text()
                self.assertNotIn("<!-- include:", text, name)
                self.assertNotRegex(text, r"\{\{\s*(config|label|host|worker|instance)\.", name)
            else:
                self.assertTrue((out / "scripts" / w["entry"]).is_file(), name)

    def test_render_is_deterministic(self):
        a, _ = render()
        b, _ = render()
        self.assertEqual(a["contentSha256"], b["contentSha256"])

    def test_instance_rules_are_fenced_into_every_prompt(self):
        _, out = render()
        for name in ("triage", "implement"):
            text = (out / "prompts" / f"{name}.md").read_text()
            self.assertIn("=== instance acme rules ===", text)
            self.assertIn("Never run the e2e suite against the production environment.", text)

    def test_a_goal_reaches_every_agent_prompt_with_the_instance_rules(self):
        tmp = Path(tempfile.mkdtemp()) / "acme"
        shutil.copytree(ACME, tmp)
        cfg = json.loads((tmp / "factory-config.json").read_text())
        cfg["goal"] = "Ship small fixes fast."
        (tmp / "factory-config.json").write_text(json.dumps(cfg))
        _, out = render(tmp)
        workers = json.loads((out / "workers.json").read_text())
        for name, w in workers.items():
            if w["implementation"] == "agent":
                self.assertIn("The factory's goal for acme: Ship small fixes fast.", (out / w["prompt"]).read_text(), name)
        _, plain = render()
        self.assertNotIn("The factory's goal for", (plain / "prompts" / "learn.md").read_text())
        cfg["goal"] = "   \n "
        (tmp / "factory-config.json").write_text(json.dumps(cfg))
        _, blank = render(tmp)
        self.assertNotIn("The factory's goal for", (blank / "prompts" / "learn.md").read_text())
        cfg["goal"] = "Ship {{config.repo.slug}} fast."
        (tmp / "factory-config.json").write_text(json.dumps(cfg))
        with self.assertRaises(RenderError):
            render(tmp)

    def test_instance_source_is_bundled_unexpanded(self):
        acme = copy_acme()
        skill = acme / "skills" / "widgets-testing" / "SKILL.md"
        skill.write_text(skill.read_text() + "\nRepo: {{config.repo.slug}}\n")
        (acme / ".notes.md").write_text("scratch")
        _, out = render(instance=acme)
        src = out / "instance-source"
        for f in ("factory-config.json", "rules.md", "skills/widgets-testing/SKILL.md"):
            self.assertEqual((src / f).read_bytes(), (acme / f).read_bytes(), f)
        self.assertNotIn("{{config.repo.slug}}", (out / "skills" / "widgets-testing" / "SKILL.md").read_text())
        self.assertFalse((src / ".notes.md").exists())

    def test_skills_are_bundled_and_listed_for_the_agent(self):
        manifest, out = render()
        self.assertIn("widgets-testing", manifest["skills"])
        self.assertIn("board-github", manifest["skills"])
        self.assertTrue((out / "skills" / "board-github" / "references" / "capabilities.md").exists())
        self.assertNotIn("{{", (out / "skills" / "widgets-testing" / "SKILL.md").read_text())
        bundle = json.loads(HOST.read_text())["paths"]["bundle"]
        skill = lambda s: f"skill://{bundle}/skills/{s}/SKILL.md"  # noqa: E731
        implement = json.loads((out / "agents" / "implement.json").read_text())
        self.assertEqual(implement["name"], "kiro-factory-implement")
        # Its own skills, then every integration's; widgets-testing is named by implement, so only it gets it.
        for s in ("implementation-verifiable-output", "widgets-testing", "agent-review", "risk", "board-github"):
            self.assertIn(skill(s), implement["resources"])
        triage = json.loads((out / "agents" / "triage.json").read_text())
        self.assertNotIn(skill("widgets-testing"), triage["resources"])
        self.assertIn(skill("board-github"), triage["resources"])
        text = (out / "prompts" / "triage.md").read_text()
        self.assertIn("follow the `board-github` skill", text)
        self.assertNotIn("This worker's skills", (out / "prompts" / "implement.md").read_text())

    def test_board_mcp_server_is_wired_into_the_agent(self):
        _, out = render(instance=with_proxy_board(copy_acme()))
        paths = json.loads(HOST.read_text())["paths"]
        agent = json.loads((out / "agents" / "triage.json").read_text())
        self.assertEqual(list(agent["mcpServers"]), ["board"])
        board = agent["mcpServers"]["board"]
        self.assertEqual(board["args"], [f"{paths['bundle']}/scripts/integrations/board/mcp_proxy.py"])
        self.assertEqual(board["env"], {"FACTORY_BUNDLE": paths["bundle"]})
        for f in ("mcp_proxy.py", "tool_catalog.json", "proxied.py"):
            self.assertTrue((out / "scripts" / "integrations" / "board" / f).is_file(), f)
    def test_agent_is_in_the_v3_format(self):
        _, out = render()
        agent = json.loads((out / "agents" / "triage.json").read_text())
        self.assertEqual(agent["permissions"], {"rules": [{"capability": "all", "effect": "allow"}]})

    def test_kiro_cli_is_the_default_harness_and_ships_no_skill(self):
        manifest, out = render()
        cfg = json.loads((out / "factory-config.json").read_text())
        self.assertEqual(cfg["integrations"]["harness"], {"use": "kiro-cli"})
        self.assertFalse([s for s in manifest["skills"] if s.startswith("harness-")])
        self.assertTrue((out / "scripts" / "integrations" / "harness" / "kiro_cli.py").is_file())

    def test_an_instance_harness_replaces_kiro_cli(self):
        d = with_proxy_board(copy_acme())
        h = d / "integrations" / "harness" / "echo"
        h.mkdir(parents=True)
        (h / "config.schema.json").write_text('{"type": "object", "additionalProperties": false, "properties": {}}')
        (h / "client.py").write_text(
            "import json\n"
            "def render(cfg, *, bundle, workers, mcp_servers):\n"
            "    return {'harness/echo.json': json.dumps({'workers': workers, 'mcp': sorted(mcp_servers)})}\n"
            "def install(cfg, bundle, worker):\n    pass\n"
            "def command(cfg, *, bundle, worker, instruction, model):\n    return ['echo', instruction]\n")
        edit_config(d, lambda c: c["integrations"].update(harness={"use": "echo"}))
        _, out = render(instance=d)
        self.assertFalse((out / "agents").exists())
        echo = json.loads((out / "harness" / "echo.json").read_text())
        self.assertEqual(echo["mcp"], ["board"])
        self.assertTrue(all(s.endswith("/SKILL.md") for w in echo["workers"].values() for s in w["skills"]))
        self.assertEqual(echo["workers"]["triage"]["context"], [])


    def test_a_setting_keeps_its_defaults_type(self):
        msg = self._broken(lambda d: edit_config(d, lambda c: c["workers"].update(janitor={"settings": {"maxBoardItems": "lots"}})))
        self.assertIn("workers.janitor.settings.maxBoardItems is a string, but the janitor step's default is a number", msg)
        msg = self._broken(lambda d: edit_config(d, lambda c: c["workers"].update(report={"settings": {"days": True}})))
        self.assertIn("is a boolean", msg)
        msg = self._broken(lambda d: edit_config(d, lambda c: c["workers"].update(report={"settings": {"days": float("nan")}})))
        self.assertIn("is not a finite number", msg)
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["workers"].update(janitor={"settings": {"maxBoardItems": 10 ** 400}}))
        render(tmp)
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["workers"].update(janitor={"settings": {"maxBoardItems": 500}}))
        _, out = render(tmp)
        self.assertEqual(json.loads((out / "workers.json").read_text())["janitor"]["settings"]["maxBoardItems"], 500)

    def test_an_agent_worker_needs_a_minute_and_a_script_worker_less(self):
        msg = self._broken(lambda d: edit_config(d, lambda c: c["workers"].update(triage={"timeoutSecs": 10})))
        self.assertIn("worker triage: timeoutSecs is 10; an agent worker needs at least 60", msg)
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["workers"].update(janitor={"timeoutSecs": 10}))
        _, out = render(tmp)
        self.assertEqual(json.loads((out / "workers.json").read_text())["janitor"]["runTimeoutSecs"], 10)

    def test_a_config_of_another_version_fails(self):
        self.assertIn("_schema", self._broken(lambda d: edit_config(d, lambda c: c.update(_schema=2))))
        self.assertIn("_schema", self._broken(lambda d: edit_config(d, lambda c: c.pop("_schema"))))

    def test_unknown_always_skill_fails(self):
        msg = self._broken(lambda d: edit_config(d, lambda c: c["workers"]["implement"].update(skills=["nope"])))
        self.assertIn("unknown skill", msg)

    def test_steps_off_by_default_stay_off_unless_enabled(self):
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["workers"].pop("monitor"))
        _, out = render(tmp)
        self.assertNotIn("monitor", json.loads((out / "workers.json").read_text()))

    def test_instance_can_replace_a_script_step_with_its_own_prompt(self):
        tmp = copy_acme()
        (tmp / "janitor.md").write_text("Summarise the board for {{instance.name}}.\n")
        edit_config(tmp, lambda c: c["workers"].update(janitor={"implementation": "agent", "agent": "janitor.md"}))
        _, out = render(tmp)
        w = json.loads((out / "workers.json").read_text())["janitor"]
        self.assertEqual(w["implementation"], "agent")
        self.assertIn("Summarise the board for acme.", (out / w["prompt"]).read_text())

    def _broken(self, mutate):
        tmp = copy_acme()
        mutate(tmp)
        with self.assertRaises(RenderError) as ctx:
            render(tmp)
        return str(ctx.exception)

    def test_agent_implementation_without_a_prompt_fails(self):
        msg = self._broken(lambda d: edit_config(d, lambda c: c["workers"].update(janitor={"implementation": "agent"})))
        self.assertIn("workers.janitor.agent", msg)

    def test_skill_name_must_match_its_directory(self):
        def mutate(d):
            p = d / "skills" / "widgets-testing" / "SKILL.md"
            p.write_text(p.read_text().replace("name: widgets-testing", "name: other"))
        self.assertIn("front matter name", self._broken(mutate))

    def test_a_skill_description_a_yaml_parser_rejects_fails(self):
        def mutate(d):
            p = d / "skills" / "widgets-testing" / "SKILL.md"
            text = p.read_text()
            start = text.index("description:")
            end = text.index("\n", start)
            p.write_text(text[:start] + "description: Build widgets: the gate and the tests." + text[end:])
        self.assertIn("not valid YAML unquoted", self._broken(mutate))

    def test_unknown_value_fails(self):
        self.assertIn("unknown value", self._broken(lambda d: (d / "rules.md").write_text("{{config.nope.missing}}\n")))

    def test_invalid_config_fails(self):
        self.assertIn("slug", self._broken(lambda d: edit_config(d, lambda c: c["repo"].pop("slug"))))

    def test_required_integration_missing_fails(self):
        self.assertIn("publishing", self._broken(lambda d: edit_config(d, lambda c: c["integrations"].pop("publishing"))))

    def test_claim_ttl_is_the_timeout_plus_margin(self):
        _, out = render()
        w = json.loads((out / "workers.json").read_text())
        self.assertEqual(w["implement"]["claimTtlSecs"], 3600 + 300)
        self.assertIn("`65` minutes", (out / "prompts" / "implement.md").read_text())

    def test_the_title_and_timezone_default_and_the_header_is_derived_from_the_title(self):
        _, out = render()
        inst = json.loads((out / "factory-config.json").read_text())["instance"]
        self.assertEqual(inst, {"name": "acme", "title": "Acme Factory", "timezone": "UTC",
                                "header": "🏭 **Acme Factory** · automated agent"})
        self.assertIn("🏭 **Acme Factory** · automated agent\nRun:", (out / "prompts" / "triage.md").read_text())
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["instance"].update(title="Widgets Bot", timezone="Europe/Berlin"))
        _, out = render(tmp)
        cfg = json.loads((out / "factory-config.json").read_text())
        self.assertEqual(cfg["instance"]["header"], "🏭 **Widgets Bot** · automated agent")
        self.assertEqual(json.loads((out / "workers.json").read_text())["triage"]["timezone"], "Europe/Berlin")
        for bad in ("**x**", "Acme\n", "   "):
            self.assertIn("title", self._broken(lambda d: edit_config(d, lambda c: c["instance"].update(title=bad))))

    def test_default_verification_signals_render_and_bundle(self):
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["workers"]["implement"].pop("verification"))
        _, out = render(tmp)
        text = (out / "prompts" / "implement.md").read_text()
        self.assertIn("scripts/signals/ci_green.py <subject>`", text)
        self.assertIn("- `reviews-converged`: follow the `reviews-converged` skill.", text)
        agent = json.loads((out / "agents" / "implement.json").read_text())
        self.assertTrue(any(r.endswith("/skills/reviews-converged/SKILL.md") for r in agent["resources"]))
        self.assertTrue((out / "scripts" / "signals" / "ci_green.py").is_file())
        w = json.loads((out / "workers.json").read_text())["implement"]
        self.assertEqual(w["verification"], [{"script": "signals/ci_green.py"}, {"skill": "reviews-converged"},
                                            {"skill": "agent-review"}, {"skill": "risk"}])

    def test_host_override_merges_values_and_replaces_on_a_new_use(self):
        tmp = copy_acme()
        host = json.loads(HOST.read_text())
        host["integrationOverrides"] = {"notifications": {
            "feed": {"webhookFile": "/run/secrets/feed"},
            "operator": {"use": "none"}}}
        (tmp / "hosts" / "merged.json").write_text(json.dumps(host))
        _, out = render(tmp, tmp / "hosts" / "merged.json")
        n = json.loads((out / "factory-config.json").read_text())["integrations"]["notifications"]
        self.assertEqual(n["feed"], {"use": "slack-workflow", "webhookFile": "/run/secrets/feed",
                                     "payloadVariable": "Content"})
        self.assertEqual(n["operator"], {"use": "none"})

    def test_instance_disables_one_default_and_adds_its_own(self):
        _, out = render()
        text = (out / "prompts" / "implement.md").read_text()
        self.assertIn("scripts/instance/run_tests.sh <subject>`", text)
        self.assertIn("- `reviews-converged`: follow the `reviews-converged` skill.", text)
        self.assertNotIn("ci_green", text)
        w = json.loads((out / "workers.json").read_text())["implement"]
        self.assertEqual(w["verification"], [{"skill": "reviews-converged"}, {"skill": "agent-review"},
                                            {"skill": "risk"}, {"script": "instance/run_tests.sh"}])

    def test_a_signal_no_worker_uses_is_not_bundled(self):
        tmp = copy_acme()

        def off(c):
            for w in ("design", "implement"):
                v = c["workers"].setdefault(w, {}).setdefault("verification", [])
                v.append({"skill": "agent-review", "enabled": False})
        edit_config(tmp, off)
        manifest, out = render(tmp)
        self.assertNotIn("agent-review", manifest["skills"])
        self.assertFalse((out / "skills" / "agent-review").exists())
        self.assertIn("reviews-converged", manifest["skills"])

    def test_a_step_skill_is_bundled_only_for_a_worker_that_names_it(self):
        manifest, out = render()
        self.assertTrue((out / "skills" / "implementation-verifiable-output" / "templates" / "output.html").is_file())
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["workers"].update(
            implement={"enabled": False}, release={"enabled": False}, monitor={"enabled": False}))
        manifest, out = render(tmp)
        self.assertNotIn("implementation-verifiable-output", manifest["skills"])
        self.assertFalse((out / "skills" / "implementation-walkthrough").exists())

    def test_release_gathers_the_risk_signal(self):
        _, out = render()
        text = (out / "prompts" / "release.md").read_text()
        self.assertIn("- `risk`: follow the `risk` skill.", text)
        self.assertIn("Risk: Very High", text)

    def test_instance_signal_is_added_after_the_defaults(self):
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["workers"]["implement"].update(
            verification=[{"script": "scripts/run_tests.sh"}]))
        _, out = render(tmp)
        w = json.loads((out / "workers.json").read_text())["implement"]
        self.assertEqual(w["verification"], [{"script": "signals/ci_green.py"},
                                             {"skill": "reviews-converged"},
                                             {"skill": "agent-review"},
                                             {"skill": "risk"},
                                             {"script": "instance/run_tests.sh"}])

    def test_disabling_a_signal_that_is_not_a_default_fails(self):
        msg = self._broken(lambda d: edit_config(d, lambda c: c["workers"]["implement"].update(
            verification=[{"script": "ci-green.py", "enabled": False}])))
        self.assertIn("not a default signal", msg)

    def test_verification_entry_needs_exactly_one_of_skill_or_script(self):
        bad = [{"skill": "reviews-converged", "script": "scripts/run_tests.sh"}]
        msg = self._broken(lambda d: edit_config(d, lambda c: c["workers"]["implement"].update(verification=bad)))
        self.assertIn("verification", msg)

    def test_verification_on_a_script_worker_fails(self):
        msg = self._broken(lambda d: edit_config(d, lambda c: c["workers"].update(
            janitor={"verification": [{"skill": "reviews-converged"}]})))
        self.assertIn("need an agent", msg)

    def test_unknown_verification_skill_fails(self):
        msg = self._broken(lambda d: edit_config(d, lambda c: c["workers"]["implement"].update(
            verification=[{"skill": "nope"}])))
        self.assertIn("unknown skill", msg)



class ModuleRenderTest(unittest.TestCase):
    def test_a_module_adds_a_step_context_and_a_skill_through_the_harness(self):
        mod = make_module({"workers": {"triage": {"context": ["context/record.md"], "skills": ["mod-record"]}}},
                          {"mod-report": {"settings": {"depth": 2}}})
        manifest, out = render_with_module(mod, lambda c: c["workers"].update({"mod-report": {"settings": {"depth": 3}}}))
        workers = json.loads((out / "workers.json").read_text())
        self.assertEqual(workers["mod-report"]["entry"], "workers/mod-report/run.py")
        self.assertEqual(workers["mod-report"]["settings"], {"depth": 3})
        self.assertTrue((out / "scripts" / "workers" / "mod-report" / "run.py").exists())
        bundle = json.loads(HOST.read_text())["paths"]["bundle"]
        triage = json.loads((out / "agents" / "triage.json").read_text())["resources"]
        self.assertEqual(triage[0], f"file://{bundle}/modules/mod/context/record.md")
        self.assertIn(f"skill://{bundle}/skills/mod-record/SKILL.md", triage)
        self.assertEqual((out / "modules" / "mod" / "context" / "record.md").read_text(),
                         "End every post with the record for acme.\n")
        design = json.loads((out / "agents" / "design.json").read_text())["resources"]
        self.assertFalse([r for r in design if "mod" in r])
        self.assertNotIn("record.md", (out / "prompts" / "triage.md").read_text())
        self.assertIn("mod-record", manifest["skills"])
        self.assertIn("mod", manifest["modules"])

    def test_a_listed_module_the_render_was_not_given_fails(self):
        tmp = copy_acme()
        edit_config(tmp, lambda c: c.update(modules=["mod"]))
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, tmp, HOST).render(Path(tempfile.mkdtemp()) / "b")
        self.assertIn("--module", str(ctx.exception))

    def test_a_module_listed_twice_fails(self):
        tmp = copy_acme()
        edit_config(tmp, lambda c: c.update(modules=["mod", "mod"]))
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, tmp, HOST, {"mod": make_module({})}).render(Path(tempfile.mkdtemp()) / "b")
        self.assertIn("$.modules: items are not unique", str(ctx.exception))

    def test_the_instance_turning_a_worker_off_wins_over_a_module_adding_to_it(self):
        mod = make_module({"workers": {"discover": {"skills": ["mod-record"], "context": ["context/record.md"]}}})
        _, out = render_with_module(mod, lambda c: c["workers"].update(discover={"enabled": False}))
        self.assertNotIn("discover", json.loads((out / "workers.json").read_text()))

    def _fails(self, mod, mutate=None) -> str:
        with self.assertRaises(RenderError) as ctx:
            render_with_module(mod, mutate)
        return str(ctx.exception)

    def test_a_module_cannot_redeclare_a_manual_step_or_name_a_worker_nobody_has(self):
        self.assertIn("is a Manual step", self._fails(make_module({}, {"janitor": {}})))
        self.assertIn("names no worker", self._fails(make_module({"workers": {"triag": {"skills": ["mod-record"]}}})))
        self.assertIn("unknown key", self._fails(make_module({"hooks": {}})))
        self.assertIn("not something a module sets",
                      self._fails(make_module({"workers": {"triage": {"schedule": "0 * * * *"}}})))

    def test_two_modules_never_declare_the_same_worker_or_skill(self):
        a = make_module({}, {"mod-report": {}})
        b = make_module({}, {"mod-report": {}}, skills=("other",))
        tmp = copy_acme()
        edit_config(tmp, lambda c: c.update(modules=["a", "b"]))
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, tmp, HOST, {"a": a, "b": b}).render(Path(tempfile.mkdtemp()) / "x")
        self.assertIn("already declared by module a", str(ctx.exception))
        b = make_module({}, {"mod-other": {}})
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, tmp, HOST, {"a": a, "b": b}).render(Path(tempfile.mkdtemp()) / "x")
        self.assertIn("skill mod-record is already module a's", str(ctx.exception))
        self.assertIn("already the Manual's", self._fails(make_module({}, skills=("risk",))))
        self.assertIn("already the Manual's", self._fails(make_module({}, skills=("board-github",))))

    def test_a_module_adds_nothing_to_another_modules_worker(self):
        a = make_module({}, {"mod-report": {}})
        b = make_module({"workers": {"mod-report": {"settings": {"depth": 1}}}}, skills=())
        tmp = copy_acme()
        edit_config(tmp, lambda c: c.update(modules=["a", "b"]))
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, tmp, HOST, {"a": a, "b": b}).render(Path(tempfile.mkdtemp()) / "x")
        self.assertIn("is module a's", str(ctx.exception))

    def test_a_host_retunes_only_workers_the_instance_has(self):
        host = Path(tempfile.mkdtemp()) / "host.json"
        host.write_text(json.dumps(dict(json.loads(HOST.read_text()),
                                        workerOverrides={"mod-report": {"schedule": "5 * * * *"}})))
        _, out = render(copy_acme(), host)
        self.assertNotIn("mod-report", json.loads((out / "workers.json").read_text()))
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["workers"].update({"mod-report": {"enabled": False}}))
        render(tmp, host)
        edit_config(tmp, lambda c: c["workers"].update({"mod-report": {"schedule": "5 * * * *"}}))
        with self.assertRaises(RenderError) as ctx:
            render(tmp, host)
        self.assertIn("workers.mod-report names no worker", str(ctx.exception))

    def test_context_is_for_agent_workers(self):
        mod = make_module({"workers": {"mod-report": {"context": ["context/record.md"]}}}, {"mod-report": {}})
        self.assertIn("context is for an agent", self._fails(mod))

    def test_settings_must_be_ones_the_step_lists(self):
        mod = make_module({"workers": {"triage": {"settings": {"nope": 1}}}})
        with self.assertRaises(RenderError) as ctx:
            render_with_module(mod)
        self.assertIn("nope", str(ctx.exception))
        with self.assertRaises(RenderError):
            render_with_module(make_module({}), lambda c: c["workers"].update(triage={"settings": {"nope": 1}}))

    def test_context_comes_only_from_a_module_and_must_fit_the_harness(self):
        with self.assertRaises(RenderError) as ctx:
            render_with_module(make_module({}), lambda c: c["workers"].update(triage={"context": ["x.md"]}))
        self.assertIn("unknown key 'context'", str(ctx.exception))
        mod = make_module({"workers": {"triage": {"context": ["context/record.md"]}}})
        (mod / "context" / "record.md").write_text("x" * 50_001)
        with self.assertRaises(RenderError) as ctx:
            render_with_module(mod)
        self.assertIn("kiro-cli loads at most", str(ctx.exception))

    def test_only_the_instance_sets_verification(self):
        mod = make_module({"workers": {"implement": {"verification": [{"skill": "mod-record"}]}}})
        with self.assertRaises(RenderError) as ctx:
            render_with_module(mod)
        self.assertIn("the instance sets verification", str(ctx.exception))

    def _with_integration(self, mod: Path, kind: str = "codehost", name: str = "mod-host") -> Path:
        d = mod / "integrations" / kind / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {kind}-{name}\ndescription: A module code host.\n---\n\n"
                                    "Repo {{config.repo.slug}}.\n")
        (d / "client.py").write_text("def from_config(cfg):\n    return None\n")
        (d / "mcp_proxy.py").write_text("print('proxy')\n")
        (d / "tool_catalog.json").write_text('{"tools": []}\n')
        (d / "config.schema.json").write_text(json.dumps(
            {"type": "object", "additionalProperties": False,
             "properties": {"_README": {"type": "string"}, "packages": {"type": "array", "default": ["Pkg"]}}}))
        return mod

    def test_a_module_ships_an_integration_the_config_picks(self):
        mod = self._with_integration(make_module({}))
        manifest, out = render_with_module(mod, lambda c: c["integrations"].update(codehost={"use": "mod-host"}))
        self.assertIn("codehost-mod-host", manifest["skills"])
        self.assertNotIn("codehost-github", manifest["skills"])
        self.assertTrue((out / "skills" / "codehost-mod-host" / "references" / "capabilities.md").exists())
        self.assertTrue((out / "scripts" / "integrations" / "codehost" / "mod_host.py").exists())
        self.assertTrue((out / "scripts" / "integrations" / "codehost" / "tool_catalog.json").exists())
        self.assertEqual(json.loads((out / "factory-config.json").read_text())["integrations"]["codehost"]["packages"],
                         ["Pkg"])
        self.assertIn("codehost", json.loads((out / "agents" / "implement.json").read_text())["mcpServers"])
        bad = {"use": "mod-host", "nope": 1}
        self.assertIn("nope", self._fails(mod, lambda c: c["integrations"].update(codehost=bad)))

    def test_a_module_integration_takes_no_manual_or_other_modules_name_or_unknown_kind(self):
        self.assertIn("already the Manual's", self._fails(self._with_integration(make_module({}), name="github")))
        self.assertIn("not a Manual integration kind",
                      self._fails(self._with_integration(make_module({}), kind="pager")))
        a = self._with_integration(make_module({}))
        b = self._with_integration(make_module({}, skills=("other",)))
        tmp = copy_acme()
        edit_config(tmp, lambda c: c.update(modules=["a", "b"]))
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, tmp, HOST, {"a": a, "b": b}).render(Path(tempfile.mkdtemp()) / "x")
        self.assertIn("already module a's", str(ctx.exception))


class OutputDirectoryTest(unittest.TestCase):
    """The render replaces --out whole, so it only replaces what it wrote."""

    def test_a_directory_that_is_not_a_bundle_is_refused_and_left_alone(self):
        out = Path(tempfile.mkdtemp())
        (out / "notes.txt").write_text("mine")
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, ACME, HOST).render(out)
        self.assertIn("not a bundle", str(ctx.exception))
        self.assertEqual((out / "notes.txt").read_text(), "mine")

    def test_another_tools_manifest_does_not_make_a_directory_a_bundle(self):
        out = Path(tempfile.mkdtemp())
        (out / "manifest.json").write_text('{"name": "a web app", "icons": []}')
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, ACME, HOST).render(out)
        self.assertIn("not a bundle", str(ctx.exception))
        self.assertTrue((out / "manifest.json").exists())

    def test_a_bundle_directory_that_stops_being_one_is_refused_in_the_same_process(self):
        out = Path(tempfile.mkdtemp()) / "bundle"
        Renderer(ROOT, ACME, HOST).render(out)
        Renderer(ROOT, ACME, HOST).render(out)
        shutil.rmtree(out)
        out.mkdir()
        (out / "notes.txt").write_text("mine")
        with self.assertRaises(RenderError):
            Renderer(ROOT, ACME, HOST).render(out)
        self.assertEqual((out / "notes.txt").read_text(), "mine")

    def test_a_bundle_is_not_rendered_inside_the_instance_it_copies(self):
        tmp = copy_acme()
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, tmp, HOST).render(tmp / "bundle")
        self.assertIn("inside the instance directory", str(ctx.exception))

    def test_an_empty_directory_and_a_previous_bundle_are_replaced(self):
        out = Path(tempfile.mkdtemp())
        first = Renderer(ROOT, ACME, HOST).render(out)
        (out / "stale.txt").write_text("from the last render")
        second = Renderer(ROOT, ACME, HOST).render(out)
        self.assertEqual(first["contentSha256"], second["contentSha256"])
        self.assertFalse((out / "stale.txt").exists())

    def test_a_failed_render_keeps_the_previous_bundle(self):
        _manifest, out = render()
        before = (out / "manifest.json").read_text()
        tmp = copy_acme()
        (tmp / "rules.md").write_text("Use {{config.no.such.value}}.\n")
        with self.assertRaises(RenderError):
            Renderer(ROOT, tmp, HOST).render(out)
        self.assertEqual((out / "manifest.json").read_text(), before)
        self.assertEqual([p.name for p in out.parent.iterdir()], ["bundle"])


class HostProfileTest(unittest.TestCase):
    def profile(self, mutate) -> Path:
        host = json.loads(HOST.read_text())
        mutate(host)
        path = Path(tempfile.mkdtemp()) / "host.json"
        path.write_text(json.dumps(host))
        return path

    def refused(self, mutate) -> str:
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, ACME, self.profile(mutate))
        return str(ctx.exception)

    def test_a_placeholder_left_in_an_example_profile_is_refused(self):
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, ACME, ROOT / "hosting" / "local" / "host.example.json")
        message = str(ctx.exception)
        self.assertIn("$.paths.bundle", message)
        self.assertIn("placeholder", message)
        Renderer(ROOT, ACME, ROOT / "hosting" / "aws-cdk" / "host.example.json")

    def test_a_profile_needs_a_name_and_an_absolute_bundle_path(self):
        self.assertIn("missing required 'name'", self.refused(lambda h: h.pop("name")))
        self.assertIn("missing required 'bundle'", self.refused(lambda h: h["paths"].pop("bundle")))
        self.assertIn("must be an absolute path", self.refused(lambda h: h["paths"].update(bundle="factory/bundle")))

    def test_the_env_list_reaches_the_bundle_and_a_name_that_is_no_variable_is_refused(self):
        out = Path(tempfile.mkdtemp()) / "b"
        Renderer(ROOT, ACME, self.profile(lambda h: h.update(env=["GH_TOKEN"]))).render(out)
        self.assertEqual(json.loads((out / "host.json").read_text())["env"], ["GH_TOKEN"])
        for bad in ("GH TOKEN", "GH_TOKEN\n", "1X", "AWS_*"):
            self.assertIn("$.env[0]", self.refused(lambda h: h.update(env=[bad])))

    def test_what_a_run_writes_is_named_and_kept_outside_the_bundle(self):
        """A render replaces the bundle, and with it anything a run wrote inside."""
        for key in ("checkout", "worktreeRoot", "stateDir"):
            self.assertIn(f"missing required {key!r}", self.refused(lambda h: h["paths"].pop(key)))
            inside = self.refused(lambda h: h["paths"].update({key: h["paths"]["bundle"] + "/work"}))
            self.assertIn(f"$.paths.{key}", inside)
            self.assertIn("inside the bundle", inside)
        beside = self.profile(lambda h: h["paths"].update(stateDir=h["paths"]["bundle"] + "-state"))
        Renderer(ROOT, ACME, beside)

    def test_a_malformed_profile_gets_a_line_not_a_traceback(self):
        path = Path(tempfile.mkdtemp()) / "host.json"
        path.write_text("[]")
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, ACME, path)
        self.assertIn("expected object", str(ctx.exception))
        self.assertIn("$.workerOverrides.triage.timeoutSecs: expected integer",
                      self.refused(lambda h: h.update(workerOverrides={"triage": {"timeoutSecs": "soon"}})))
        self.assertIn("$.workerOverrides.triage: unknown key 'scheduel'",
                      self.refused(lambda h: h.update(workerOverrides={"triage": {"scheduel": "0 * * * *"}})))

    def test_python_defaults_to_python3(self):
        r = Renderer(ROOT, ACME, self.profile(lambda h: h["paths"].pop("python")))
        self.assertEqual(r.host["paths"]["python"], "python3")

    def test_the_aws_host_needs_a_publishing_bucket_for_its_run_records(self):
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["integrations"].update(publishing={"use": "disk", "dir": "/srv/pages"}))
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, tmp, ACME / "hosts" / "aws-cdk.json").render(Path(tempfile.mkdtemp()) / "b")
        self.assertIn("host aws-cdk: this host keeps run records and leases in the publishing bucket",
                      str(ctx.exception))
        self.assertIn("missing required 'bucket'", str(ctx.exception))

    def test_an_unknown_key_is_refused_and_a_note_is_not(self):
        self.assertIn("unknown key 'workerOverride'", self.refused(lambda h: h.update(workerOverride={})))
        Renderer(ROOT, ACME, self.profile(lambda h: h.update(_README="a note")))


class ConfigMistakeTest(unittest.TestCase):
    """A plausible mistake in the config gets a line that names it, not a traceback."""

    def refused(self, mutate) -> str:
        tmp = copy_acme()
        edit_config(tmp, mutate)
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, tmp, HOST).render(Path(tempfile.mkdtemp()) / "b")
        return str(ctx.exception)

    def test_a_team_login_that_is_not_a_github_user_name_is_refused(self):
        for kind in ("board", "codehost"):
            for login in ("@lead", "lead ", "lead\n"):
                msg = self.refused(lambda c: c["integrations"][kind].update(teamLogins=[login]))
                self.assertIn("teamLogins", msg)

    def test_a_managed_user_login_with_an_underscore_is_accepted(self):
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["integrations"]["board"].update(teamLogins=["jdoe_acme"]))
        Renderer(ROOT, tmp, HOST).render(Path(tempfile.mkdtemp()) / "b")

    def test_an_integration_written_as_a_name_is_refused_by_the_schema(self):
        self.assertIn("$.integrations.board: expected object",
                      self.refused(lambda c: c["integrations"].update(board="github")))
        self.assertIn("$.integrations.notifications.feed: expected object",
                      self.refused(lambda c: c["integrations"]["notifications"].update(feed="none")))

    def test_a_schedule_is_five_cron_fields_in_a_real_timezone(self):
        self.assertIn("worker triage: schedule 'every tuesday' is not a five-field cron expression",
                      self.refused(lambda c: c["workers"]["triage"].update(schedule="every tuesday")))
        self.assertIn("is not a five-field cron expression",
                      self.refused(lambda c: c["workers"]["triage"].update(schedule="0 * * * * *")))
        self.assertIn("worker triage: timezone 'Mars/Olympus' is not an IANA zone name",
                      self.refused(lambda c: c["workers"]["triage"].update(timezone="Mars/Olympus")))
        tmp = copy_acme()
        edit_config(tmp, lambda c: c["workers"]["triage"].update(schedule="*/15 9-17 * JAN-JUN MON-FRI",
                                                                 timezone="Europe/Berlin"))
        Renderer(ROOT, tmp, HOST).render(Path(tempfile.mkdtemp()) / "b")

    def test_a_missing_integration_says_how_to_set_it_or_turn_the_worker_off(self):
        message = self.refused(lambda c: c["integrations"].pop("publishing"))
        self.assertIn("requires integration 'publishing'", message)
        self.assertIn("set integrations.publishing, or turn the worker off", message)

    def test_a_proxy_without_its_catalog_is_refused(self):
        tmp = with_proxy_board(copy_acme())
        (tmp / "integrations" / "board" / "proxied" / "tool_catalog.json").unlink()
        with self.assertRaises(RenderError) as ctx:
            Renderer(ROOT, tmp, HOST).render(Path(tempfile.mkdtemp()) / "b")
        self.assertIn("ships mcp_proxy.py without the tool_catalog.json it serves", str(ctx.exception))


class ExamplesTest(unittest.TestCase):
    def test_the_minimal_example_renders_with_no_notifications_configured(self):
        minimal = ROOT / "examples" / "minimal"
        out = Path(tempfile.mkdtemp()) / "bundle"
        manifest = Renderer(ROOT, minimal, minimal / "hosts" / "local.json").render(out)
        self.assertIn("notifications-none", manifest["skills"])
        cfg = json.loads((out / "factory-config.json").read_text())
        self.assertEqual(cfg["integrations"]["notifications"],
                         {"feed": {"use": "none"}, "operator": {"use": "none"}})
        self.assertIn("follow the `notifications-none` skill", (out / "prompts" / "design.md").read_text())

    def test_the_factory_account_counts_as_the_factory_on_the_code_host(self):
        _manifest, out = render()
        self.assertIn('the factory\'s own logins, `["acme-factory"]`',
                      (out / "skills" / "codehost-github" / "SKILL.md").read_text())

    def test_the_example_module_adds_its_workers_and_what_triage_gets(self):
        tmp = copy_acme()
        edit_config(tmp, lambda c: c.update(modules=["weekly"]))
        out = Path(tempfile.mkdtemp()) / "bundle"
        Renderer(ROOT, tmp, HOST, {"weekly": ROOT / "examples" / "modules" / "weekly"}).render(out)
        workers = json.loads((out / "workers.json").read_text())
        self.assertEqual(workers["weekly-summary"]["entry"], "workers/weekly-summary/summary.py")
        self.assertEqual(workers["weekly-notes"]["prompt"], "prompts/weekly-notes.md")
        triage = json.loads((out / "agents" / "triage.json").read_text())["resources"]
        self.assertTrue(any(r.endswith("modules/weekly/context/summary-lines.md") for r in triage))
        self.assertTrue(any(r.endswith("skills/summary-tags/SKILL.md") for r in triage))
        self.assertIn("notes for\nacme quote", (out / "modules" / "weekly" / "context" / "summary-lines.md").read_text())

    def test_the_acme_triage_skill_has_the_three_headings_steps_read_by_name(self):
        text = (ACME / "skills" / "widgets-triage" / "SKILL.md").read_text()
        for heading in ("verification-levels", "axis-taxonomy", "risk-levels"):
            self.assertIn(f"## {heading}\n", text)
            self.assertIn(f"`{heading}`", (ROOT / "README.md").read_text())


class PublishingSkillTest(unittest.TestCase):
    def test_the_disk_skills_snippet_publishes_from_a_rendered_bundle(self):
        import re
        import subprocess
        home = Path(tempfile.mkdtemp()).resolve()
        out, pages = home / "bundle", home / "pages"
        instance = copy_acme()
        edit_config(instance, lambda c: c["integrations"].update(
            publishing={"use": "disk", "dir": str(pages)}))
        host = json.loads(HOST.read_text())
        host["paths"].update(bundle=str(out), python=sys.executable)
        (home / "host.json").write_text(json.dumps(host))
        Renderer(ROOT, instance, home / "host.json").render(out)
        skill = (out / "skills" / "publishing-disk" / "SKILL.md").read_text()
        snippet = re.search(r"```\n(.*?)```", skill, re.S).group(1)
        (home / "page.html").write_text("<p>spec</p>")
        snippet = snippet.replace("<key>", "items/7/spec.html").replace("<page file>", str(home / "page.html"))
        r = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((pages / "items" / "7" / "spec.html").read_text(), "<p>spec</p>")
        self.assertIn((pages / "items" / "7" / "spec.html").as_uri(), r.stdout)


if __name__ == "__main__":
    unittest.main()
