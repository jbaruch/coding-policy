#!/usr/bin/env python3
"""The source checkout's instruction index agrees with the published manifest.

`AGENTS.md` sends an agent working in this checkout to `.claude/CLAUDE.md`,
which @-imports the rule files. A rule declared in `.tessl-plugin/plugin.json`
but missing from that index ships to consumers while staying invisible to
anyone working on the repo itself, and nothing caught the drift (#368). This
suite is that check: the two lists must name the same files, and every named
file must exist.
"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = ROOT / ".tessl-plugin" / "plugin.json"
INDEX = ROOT / ".claude" / "CLAUDE.md"

#: An `@`-import line in the index, relative to `.claude/`.
IMPORT = re.compile(r"^@\.\./(rules/[^\s]+\.md)\s*$", re.MULTILINE)


def declared_rules():
    """Rule paths the plugin manifest publishes, repo-relative."""
    rules = json.loads(MANIFEST.read_text())["rules"]
    if isinstance(rules, str):
        base = ROOT / rules
        return sorted(str(p.relative_to(ROOT)) for p in base.glob("*.md"))
    return sorted(rules)


def indexed_rules():
    """Rule paths the source instruction index imports, repo-relative."""
    return sorted(IMPORT.findall(INDEX.read_text()))


class SourceIndex(unittest.TestCase):
    def test_index_matches_manifest(self):
        declared, indexed = declared_rules(), indexed_rules()
        self.assertEqual(
            sorted(set(declared) - set(indexed)), [],
            "declared in .tessl-plugin/plugin.json but not imported by .claude/CLAUDE.md",
        )
        self.assertEqual(
            sorted(set(indexed) - set(declared)), [],
            "imported by .claude/CLAUDE.md but not declared in .tessl-plugin/plugin.json",
        )

    def test_every_indexed_rule_exists(self):
        missing = [r for r in indexed_rules() if not (ROOT / r).is_file()]
        self.assertEqual(missing, [], "index imports a rule file that does not exist")

    def test_index_covers_every_rule_file(self):
        on_disk = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "rules").glob("*.md"))
        self.assertEqual(
            sorted(set(on_disk) - set(indexed_rules())), [],
            "rules/*.md file missing from .claude/CLAUDE.md",
        )


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
