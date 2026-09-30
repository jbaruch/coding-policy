"""Outcome tests for check-rules-budget.sh, plus the live budget gate.

The live case runs the check against this repo's rules/, so a PR that grows
the always-loaded rules past the script-owned budget fails the test suite
that CI runs (#642). The fixture cases pin the script's contract against
throwaway rule trees, independent of the live rules' size.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "check-rules-budget.sh"
REPO_RULES = Path(__file__).resolve().parents[2] / "rules"


def run(*args, budget=None):
    env = {k: v for k, v in os.environ.items() if k != "RULES_BUDGET_BYTES"}
    if budget is not None:
        env["RULES_BUDGET_BYTES"] = budget
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False)


class LiveRulesBudget(unittest.TestCase):
    def test_repo_rules_fit_the_budget(self):
        result = run(str(REPO_RULES))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["within_budget"])
        self.assertLessEqual(report["total_bytes"], report["budget_bytes"])
        self.assertEqual(report["total_bytes"], sum(p.stat().st_size for p in REPO_RULES.glob("*.md")))


class FixtureRulesBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rules = Path(self.tmp.name) / "rules"
        self.rules.mkdir()
        (self.rules / "a.md").write_text("a" * 60)
        (self.rules / "b.md").write_text("b" * 40)
        (self.rules / "notes.txt").write_text("x" * 1000)

    def test_within_budget_reports_every_rule_largest_first(self):
        result = run(str(self.rules), budget="100")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["total_bytes"], 100)
        self.assertTrue(report["within_budget"])
        self.assertEqual([Path(f["path"]).name for f in report["files"]], ["a.md", "b.md"])

    def test_over_budget_fails_with_an_actionable_message(self):
        result = run(str(self.rules), budget="99")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["within_budget"])
        self.assertIn("over the 99-byte budget by 1", result.stderr)
        self.assertIn("references/", result.stderr)
        self.assertIn("a.md (60)", result.stderr)

    def test_multibyte_text_counts_bytes(self):
        (self.rules / "b.md").write_text("—" * 10)  # 10 chars, 30 bytes
        report = json.loads(run(str(self.rules), budget="1000").stdout)
        self.assertEqual(report["total_bytes"], 90)

    def test_setup_errors_exit_2_with_no_report(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        for args, budget, needle in (
            ((str(Path(self.tmp.name) / "absent"),), None, "not found"),
            ((str(empty),), None, "no *.md rule files"),
            ((str(self.rules),), "lots", "positive integer"),
        ):
            with self.subTest(needle=needle):
                result = run(*args, budget=budget)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(needle, result.stderr)


if __name__ == "__main__":
    unittest.main()
