import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime" / "render"))
from render import CONTRACT_HEADINGS, INCLUDE_RE, RELIES_RE, skill_front, step_dirs  # noqa: E402

STEP_DIRS = step_dirs(ROOT)
KINDS = {p.parent.name: json.loads(p.read_text()) for p in (ROOT / "integrations").glob("*/kind.json")}


def defaults(step: Path) -> dict:
    return json.loads((step / "defaults.json").read_text())


class StepContractTest(unittest.TestCase):
    def test_every_step_ships_its_default_implementation(self):
        for name, step in STEP_DIRS.items():
            spec = defaults(step)
            self.assertIn(spec["implementation"], ("agent", "script"), name)
            self.assertIn(spec["implementation"], spec, f"{name}: no {spec['implementation']} path")
            for key in ("agent", "script"):
                if key in spec:
                    self.assertTrue((step / spec[key]).is_file(), f"{name}: {key} {spec[key]} missing")
            self.assertIn("schedule", spec, name)
            self.assertIsInstance(spec.get("requires"), list, name)

    def test_every_prompt_states_its_contract(self):
        for name, step in STEP_DIRS.items():
            spec = defaults(step)
            if "agent" not in spec:
                continue
            text = (step / spec["agent"]).read_text()
            for heading in CONTRACT_HEADINGS:
                self.assertRegex(text, rf"(?m)^{re.escape(heading)}$", f"{name}: missing {heading}")

    def test_every_script_states_its_contract(self):
        for name, step in STEP_DIRS.items():
            spec = defaults(step)
            if "script" not in spec:
                continue
            text = (step / spec["script"]).read_text()
            for label in ("Inputs:", "Outputs:", "Verification:", "Decisions:"):
                self.assertIn(label, text, f"{name}: script docstring has no {label}")

    def test_includes_name_real_files(self):
        paths = [step / defaults(step)["agent"] for step in STEP_DIRS.values() if "agent" in defaults(step)]
        for path in paths + list(ROOT.glob("workers/common/*.md")):
            for target in INCLUDE_RE.findall(path.read_text()):
                if target.startswith("common/"):
                    self.assertTrue((ROOT / "workers" / f"{target}.md").exists(), f"{path}: {target}")
                elif target not in ("instance", "verification"):
                    self.assertIn(target.split()[1], KINDS, f"{path}: {target}")

    def test_relies_on_targets_exist(self):
        for name, step in STEP_DIRS.items():
            spec = defaults(step)
            text = (step / spec[spec["implementation"]]).read_text()
            for kind_of, target in RELIES_RE.findall(text):
                if kind_of == "worker":
                    self.assertIn(target, STEP_DIRS, f"{name}: relies on worker {target}")
                else:
                    kind, _, cap = target.partition(".")
                    self.assertIn(cap, KINDS.get(kind, {}).get("capabilities", []), f"{name}: {target}")


class IntegrationContractTest(unittest.TestCase):
    def integrations(self):
        for kind in KINDS:
            for d in sorted((ROOT / "integrations" / kind).iterdir()):
                if d.is_dir():
                    yield kind, d

    def test_every_integration_is_a_named_skill(self):
        for kind, d in self.integrations():
            json.loads((d / "config.schema.json").read_text())
            if not KINDS[kind].get("agentFacing", True):
                continue
            front = skill_front((d / "SKILL.md").read_text())
            self.assertEqual(front.get("name"), f"{kind}-{d.name}", d)
            self.assertTrue(front.get("description"), d)

    def test_every_harness_implements_the_interface(self):
        for kind, d in self.integrations():
            if kind != "harness":
                continue
            spec = importlib.util.spec_from_file_location(f"harness_{d.name}", d / "client.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for cap in KINDS[kind]["capabilities"]:
                self.assertTrue(callable(getattr(mod, cap, None)), f"{d} has no {cap}()")

    def test_every_integration_covers_its_kind(self):
        for kind, d in self.integrations():
            if d.name == "none" or KINDS[kind].get("channels"):
                continue
            text = (d / ("SKILL.md" if KINDS[kind].get("agentFacing", True) else "README.md")).read_text()
            missing = [c for c in KINDS[kind]["capabilities"] if f"`{c}`" not in text]
            self.assertFalse(missing, f"{kind}/{d.name} does not document {missing}")

    def test_every_shipped_client_has_the_methods_its_kinds_interface_names(self):
        """A script calls a client through its kind's interface, so the two cannot drift apart."""
        for kind, d in self.integrations():
            iface, client = ROOT / "integrations" / kind / "interface.py", d / "client.py"
            if kind == "harness" or not (iface.exists() and client.exists()):
                continue
            protocol = next(m.group(1) for m in re.finditer(r"^class (\w+)\(Protocol\):", iface.read_text(), re.M))
            body = iface.read_text().split(f"class {protocol}(Protocol):", 1)[1]
            wanted = re.findall(r"^    def (\w+)\(", body, re.M)
            have = client.read_text()
            missing = [m for m in wanted if not re.search(rf"^\s+(def {m}\(|.*\b{m}\b.* = )", have, re.M)]
            self.assertTrue(wanted, iface)
            self.assertFalse(missing, f"{kind}/{d.name} client lacks {missing}")

    def test_every_code_host_skill_says_who_is_on_the_team(self):
        for kind, d in self.integrations():
            if kind == "codehost":
                self.assertIn("on the team", (d / "SKILL.md").read_text(),
                              f"codehost/{d.name}: KIND.md has every code host skill say who directs the factory")

    def test_no_skill_pastes_item_or_review_text_into_a_command(self):
        """A title, body or comment reaches a platform's CLI from a file: pasted into the command, a
        backtick or $(...) in it runs."""
        pasted = re.compile(r"--(?:body|comment|title) (?:<|\"<)|\"<message")
        for kind, d in self.integrations():
            skill = d / "SKILL.md"
            if skill.exists():
                hits = pasted.findall(skill.read_text())
                self.assertFalse(hits, f"{kind}/{d.name}: {hits}")


if __name__ == "__main__":
    unittest.main()
