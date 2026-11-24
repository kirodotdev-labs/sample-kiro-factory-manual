"""The Manual must stay generic: no project names anywhere, and no platform names in the prompts,
workers/common/ or the worker scripts.

The shipped term lists hold no project names: the project-name check covers the names a factory
adds in tests/denylist-*.txt or FACTORY_DENYLIST, plus two shapes no Manual file should hold, a
UUID and a 12-digit account id."""

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = {".md", ".py", ".json", ".sh", ".html", ".js", ".ts", ".css", ".txt"}
SKIP_DIRS = {".git", "examples", "tests", "build", "public", "__pycache__", "node_modules", "cdk.out"}


def files(under: Path):
    for path in sorted(under.rglob("*")):
        text = path.suffix in TEXT or path.name == "Dockerfile"
        if path.is_file() and text and not SKIP_DIRS & set(path.relative_to(ROOT).parts):
            yield path


def _read_terms(path: Path) -> list[str]:
    return [t.strip() for t in path.read_text().splitlines() if t.strip() and not t.startswith("#")]


def terms(name: str) -> list[str]:
    """The terms in tests/<name>.txt and any tests/<name>-*.txt beside it."""
    return [t for path in sorted((ROOT / "tests").glob(f"{name}*.txt")) for t in _read_terms(path)]


def extra_denylist() -> list[str]:
    """Terms from the files FACTORY_DENYLIST names, for a factory that keeps its own names outside the Manual."""
    return [t for f in filter(None, os.environ.get("FACTORY_DENYLIST", "").split(os.pathsep)) for t in _read_terms(Path(f))]


UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
ACCOUNT = re.compile(r"(?<![\d.])\d{12}(?![\d.])")


def term_pattern(term: str) -> re.Pattern:
    """The term as a whole word, in any case."""
    return re.compile(rf"(?<![\w-]){re.escape(term)}(?![\w-])", re.I)


class GenericityTest(unittest.TestCase):
    def test_no_project_terms_anywhere(self):
        pats = []
        for entry in terms("denylist") + extra_denylist():
            t, _, allowed = entry.partition(" @")
            pats.append((t, term_pattern(t), allowed.strip()))
        hits = []
        for path in files(ROOT):
            text = path.read_text(errors="replace")
            top = path.relative_to(ROOT).parts[0]
            for t, pat, allowed in pats:
                if allowed and top == allowed:
                    continue
                for m in pat.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    hits.append(f"{path.relative_to(ROOT)}:{line}: {t}")
            for pat, what in ((UUID, "uuid"), (ACCOUNT, "12-digit account id")):
                for m in pat.finditer(text):
                    hits.append(f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, m.start()) + 1}: {what} {m.group(0)}")
        self.assertFalse(hits, "project-specific text in the Manual:\n" + "\n".join(hits[:60]))

    def test_no_platform_terms_in_worker_rules(self):
        """The shared rules, every prompt and every worker script name capabilities, not platforms.
        Platform code lives in integrations/ and hosting/."""
        pats = [(t, term_pattern(t)) for t in terms("platforms")]
        paths = list(files(ROOT / "workers" / "common"))
        paths += [p for p in files(ROOT / "workers")
                  if p.name == "prompt.md" or p.suffix in {".py", ".js", ".html"}]
        hits = []
        for path in paths:
            text = path.read_text(errors="replace")
            for t, pat in pats:
                for m in pat.finditer(text):
                    hits.append(f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, m.start()) + 1}: {t}")
        self.assertFalse(hits, "platform names in worker rules (name a capability instead):\n" + "\n".join(hits[:60]))

    def test_a_term_matches_as_a_whole_word_and_a_shape_matches_anywhere(self):
        """The checks above cannot pass by matching nothing: the patterns they build find what they
        are for."""
        pat = term_pattern("Acme")
        self.assertTrue(pat.search("built for acme."))
        self.assertFalse(pat.search("acme-corp") or pat.search("acmes"))
        self.assertTrue(UUID.search("id 123e4567-e89b-42d3-a456-426614174000"))
        self.assertTrue(ACCOUNT.search("account 123456789012 in"))
        self.assertFalse(ACCOUNT.search("1234567890123") or ACCOUNT.search("12345678901.2"))


if __name__ == "__main__":
    unittest.main()
