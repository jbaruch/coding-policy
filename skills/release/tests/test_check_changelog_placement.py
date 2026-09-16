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

_SCRIPT = _os.path.join(_ROOT, "check-changelog-placement.py")
_SPEC = importlib.util.spec_from_file_location("check_changelog_placement", _SCRIPT)
# Narrowed for the type checker the same way its sibling suite narrows, since
# `spec_from_file_location` is Optional at the type level.
assert _SPEC and _SPEC.loader, f"cannot load check-changelog-placement.py at {_SCRIPT}"
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


class ParkedIdentityTest(unittest.TestCase):
    def test_an_entry_above_the_first_heading_is_not_parked(self):
        # The published entry below the heading is parked, as it should be;
        # the new one above it is not, which is what makes it stampable.
        parked = check.parked(STAMPABLE)
        self.assertEqual(len(parked), 1)
        self.assertIn("A published entry.", parked[0])
        self.assertNotIn("A new entry.", "\n".join(parked))

    def test_an_entry_under_a_version_heading_is_parked(self):
        self.assertEqual(len(check.parked(MISFILED)), 2)

    def test_a_changelog_with_no_version_heading_yet_parks_nothing(self):
        self.assertEqual(check.parked("# Changelog\n\n### Added\n"), [])

    def test_a_moved_block_is_known_to_the_base(self):
        self.assertEqual(check.newly_parked(HEADED, HEADED), [])

    def test_a_new_block_parked_under_a_heading_is_flagged(self):
        flagged = check.newly_parked(HEADED, MISFILED)
        self.assertEqual(len(flagged), 1)
        self.assertIn("A new entry.", flagged[0])

    def test_a_swap_that_keeps_the_count_is_still_flagged(self):
        # The count rule this replaced was satisfied by adding one misfiled
        # block while dropping another parked one.
        swapped = """# Changelog

## 0.3.9 — 2026-01-02

### Added

- **A new entry.** Not yet stamped.
"""
        self.assertEqual(len(check.parked(swapped)), len(check.parked(HEADED)))
        self.assertEqual(len(check.newly_parked(HEADED, swapped)), 1)


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
        self.assertEqual(code, 1, err)
        self.assertIn("whose content is new to", err)
        self.assertIn("Rebase onto main", err)

    def test_a_swap_that_keeps_the_count_is_refused(self):
        # Adds a misfiled block and drops a parked one: the net count is
        # unchanged, and identity still catches it.
        swapped = HEADED.replace(
            "- **A published entry.** Already stamped.\n",
            "- **A new entry.** Not yet stamped.\n")
        self.commit_changelog(swapped)
        code, err = self.run_check()
        self.assertEqual(code, 1, err)
        self.assertIn("whose content is new to", err)

    def test_moving_an_entry_between_headings_is_allowed(self):
        # An archive repair -- filing a past entry under the version that
        # published it -- appears in the diff as an addition below a heading,
        # exactly like the hazard. The count tells them apart (#452).
        moved = HEADED.replace(
            "## 0.3.9 — 2026-01-02\n\n### Added\n\n- **A published entry.** Already stamped.\n",
            "## 0.3.10 — 2026-01-03\n\n### Added\n\n- **A published entry.** Already stamped.\n")
        self.commit_changelog(moved)
        code, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_branch_touching_no_entries_passes(self):
        self.git("checkout", "-q", "-b", "work")
        (self.root / "other.txt").write_text("x", encoding="utf-8")
        self.git("add", "other.txt")
        self.git("commit", "-q", "-m", "unrelated")
        code, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_absent_git_is_a_tool_error_not_a_verdict(self):
        # `subprocess.run` raises FileNotFoundError, and an uncaught exception
        # exits 1 — the misfiling verdict. A missing tool is the absence of an
        # answer, so it must exit 2.
        self.commit_changelog(MISFILED)
        env = dict(_os.environ, PATH=str(self.root / "no-tools"))
        proc = subprocess.run(
            [_sys.executable, _os.path.join(_ROOT, "check-changelog-placement.py"),
             "--base", "main"],
            cwd=self.root, capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("cannot run `git`", proc.stderr)

    def test_an_undecodable_changelog_is_a_tool_error_not_a_verdict(self):
        # UnicodeDecodeError is not an OSError; letting it escape exits 1, the
        # misfiling verdict, for a file the tool simply cannot read.
        self.commit_changelog(MISFILED)
        self.changelog.write_bytes(b"# Changelog\n\n\xff\xfe not utf-8\n")
        code, err = self.run_check()
        self.assertEqual(code, 2, err)
        self.assertIn("cannot decode", err)

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
