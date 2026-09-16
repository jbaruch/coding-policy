#!/usr/bin/env python3
"""A CHANGELOG entry the publish step cannot stamp is refused on the PR.

The hazard is a branch cut before an intervening publish: its `### ` block
merges BELOW a version heading added since, the stamp step correctly finds
nothing un-headed, and the work ships filed under an already-published version
(#452). These cases pin the check that catches it while a rebase is still the
fix.
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_changelog_placement",
    _os.path.join(_ROOT, "check-changelog-placement.py"))
check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check)

HEADED = """# Changelog

## 0.3.9 — 2026-01-02

### Added

- **A published entry.** Already stamped.
"""

STAMPABLE = """# Changelog

### Added

- **A new entry.** Not yet stamped.

## 0.3.9 — 2026-01-02

### Added

- **A published entry.** Already stamped.
"""

MISFILED = """# Changelog

## 0.3.9 — 2026-01-02

### Added

- **A new entry.** Not yet stamped.

### Added

- **A published entry.** Already stamped.
"""


class MisfiledTest(unittest.TestCase):
    def test_an_entry_above_the_first_heading_is_stampable(self):
        # Line 3 of STAMPABLE, above its first `## ` at line 7.
        self.assertEqual(check.misfiled(STAMPABLE, [3]), [])

    def test_an_entry_under_a_version_heading_is_refused(self):
        # Line 5 of MISFILED, below its first `## ` at line 3.
        self.assertEqual(check.misfiled(MISFILED, [5]), ["### Added"])

    def test_a_published_entry_reading_the_same_is_not_confused_for_it(self):
        # Both blocks read `### Added`; only the added line number is judged.
        self.assertEqual(check.misfiled(STAMPABLE, [3]), [])

    def test_a_changelog_with_no_version_heading_yet_is_stampable(self):
        self.assertEqual(check.misfiled("# Changelog\n\n### Added\n", [3]), [])

    def test_an_unchanged_changelog_is_not_inspected(self):
        self.assertEqual(check.misfiled(MISFILED, []), [])


class RepositoryTest(unittest.TestCase):
    """End to end over a real repository, so the diff half is exercised too."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.changelog = self.root / "CHANGELOG.md"
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.changelog.write_text(HEADED, encoding="utf-8")
        self.git("add", "CHANGELOG.md")
        self.git("commit", "-q", "-m", "base")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True,
                       capture_output=True, text=True)

    def run_check(self):
        proc = subprocess.run(
            [_sys.executable, _os.path.join(_ROOT, "check-changelog-placement.py"),
             "--base", "main"],
            cwd=self.root, capture_output=True, text=True)
        return proc.returncode, proc.stderr

    def commit_changelog(self, text):
        self.git("checkout", "-q", "-b", "work")
        self.changelog.write_text(text, encoding="utf-8")
        self.git("add", "CHANGELOG.md")
        self.git("commit", "-q", "-m", "entry")

    def test_a_stampable_entry_passes(self):
        self.commit_changelog(STAMPABLE)
        code, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_an_entry_merged_under_a_heading_is_refused(self):
        self.commit_changelog(MISFILED)
        code, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("under a version heading", err)
        self.assertIn("Rebase onto main", err)

    def test_a_branch_touching_no_entries_passes(self):
        self.git("checkout", "-q", "-b", "work")
        (self.root / "other.txt").write_text("x", encoding="utf-8")
        self.git("add", "other.txt")
        self.git("commit", "-q", "-m", "unrelated")
        code, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_an_unknown_base_is_a_tool_error_not_a_verdict(self):
        self.commit_changelog(STAMPABLE)
        proc = subprocess.run(
            [_sys.executable, _os.path.join(_ROOT, "check-changelog-placement.py"),
             "--base", "no-such-ref"],
            cwd=self.root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("failed", proc.stderr)


if __name__ == "__main__":
    unittest.main()
