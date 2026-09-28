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


TWO_VERSIONS = """# Changelog

## 0.3.9 — 2026-01-02

### Added

- **A second entry.** Published with it.

## 0.3.8 — 2026-01-01

### Added

- **An older entry.** Published first.
"""

# The #581 shape: two releases' items under one heading.
SHARED_HEADING = """# Changelog

## 0.3.9 — 2026-01-02

### Fixed

- **The later release's entry.** Published as 0.3.10.

- **This release's entry.** Published as 0.3.9.
"""

REPAIRED = """# Changelog

## 0.3.10 — 2026-01-02

### Fixed

- **The later release's entry.** Published as 0.3.10.

## 0.3.9 — 2026-01-02

### Fixed

- **This release's entry.** Published as 0.3.9.
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

    def test_a_swap_across_headings_that_keeps_the_count_is_still_flagged(self):
        # The count rule this replaced was satisfied by adding one misfiled
        # item while dropping another parked one. Dropped under one heading
        # and added under another is no in-place edit: nothing under the new
        # item's heading went away for it to pair with.
        swapped = TWO_VERSIONS.replace(
            "- **A second entry.** Published with it.\n",
            "- **A second entry.** Published with it.\n\n"
            "- **A new entry.** Not yet stamped.\n").replace(
            "\n- **An older entry.** Published first.\n", "")
        self.assertEqual(len(check.parked(swapped)), len(check.parked(TWO_VERSIONS)))
        self.assertEqual(len(check.newly_parked(TWO_VERSIONS, swapped)), 1)

    def test_a_heading_only_repair_parks_nothing_new(self):
        # #581: 0.3.291 published with no heading, its entry filed under
        # 0.3.290. The repair adds `## 0.3.291` and a `### Fixed` above
        # content the base already carries; only structure changes.
        self.assertEqual(check.newly_parked(SHARED_HEADING, REPAIRED), [])

    def test_a_declared_in_place_edit_is_not_new(self):
        # #503: rewording a published entry replaces the item's text under
        # the same heading, and a `Changelog-Edit:` trailer declares it.
        edited = HEADED.replace("Already stamped.", "Reworded in place.")
        self.assertEqual(check.newly_parked(HEADED, edited, {"0.3.9"}), [])

    def test_an_undeclared_same_heading_replacement_is_flagged(self):
        # Deleting a published item and adding unrelated work under the same
        # heading reads exactly like a reword. Without the declaration it is
        # new content parked where the stamp cannot reach.
        replaced = HEADED.replace(
            "- **A published entry.** Already stamped.\n",
            "- **A new entry.** Not yet stamped.\n")
        flagged = check.newly_parked(HEADED, replaced)
        self.assertEqual(len(flagged), 1)
        self.assertIn("A new entry.", flagged[0])

    def test_a_declaration_for_another_version_does_not_cover_an_edit(self):
        edited = HEADED.replace("Already stamped.", "Reworded in place.")
        self.assertEqual(len(check.newly_parked(HEADED, edited, {"0.3.8"})), 1)

    def test_an_edit_answers_one_removed_item_only(self):
        # One rewritten item pairs with one removed item; a second new item
        # under the same heading has nothing left to pair with.
        edited = HEADED.replace(
            "- **A published entry.** Already stamped.\n",
            "- **A published entry.** Reworded in place.\n\n"
            "- **A new entry.** Not yet stamped.\n")
        flagged = check.newly_parked(HEADED, edited, {"0.3.9"})
        self.assertEqual(len(flagged), 1)

    def test_an_edit_moved_to_another_heading_is_flagged(self):
        # An edit pairs only under the heading the base item sat under.
        moved = TWO_VERSIONS.replace(
            "- **A second entry.** Published with it.\n\n", "").replace(
            "- **An older entry.** Published first.\n",
            "- **An older entry.** Published first.\n\n"
            "- **A second entry.** Reworded and moved.\n")
        self.assertEqual(
            len(check.newly_parked(TWO_VERSIONS, moved, {"0.3.8", "0.3.9"})), 1)

    def test_a_column_zero_paragraph_is_its_own_item(self):
        # Older entries are prose paragraphs, not bullets.
        text = ("# Changelog\n\n## 0.3.9 — 2026-01-02\n\n### Added\n\n"
                "First paragraph.\n\nSecond paragraph.\n")
        self.assertEqual(check.parked(text), ["First paragraph.", "Second paragraph."])


class _RepoCase(unittest.TestCase):
    """A real repository whose `main` carries HEADED."""

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

    def rebase_on(self, text):
        """Replace the base commit's changelog with `text`."""
        self.changelog.write_text(text, encoding="utf-8")
        self.git("commit", "-q", "-am", "base changelog")

    def commit_changelog(self, text, message="entry"):
        self.git("checkout", "-q", "-b", "work")
        self.changelog.write_text(text, encoding="utf-8")
        self.git("add", "CHANGELOG.md")
        self.git("commit", "-q", "-m", message)


class RepositoryTest(_RepoCase):
    """End to end over a real repository, so the diff half is exercised too."""

    def test_a_stampable_entry_passes(self):
        self.commit_changelog(STAMPABLE)
        code, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_an_entry_merged_under_a_heading_is_refused(self):
        self.commit_changelog(MISFILED)
        code, err = self.run_check()
        self.assertEqual(code, 1, err)
        self.assertIn("are new since", err)
        self.assertIn("Rebase onto main", err)

    def test_a_swap_that_keeps_the_count_is_refused(self):
        # Adds a misfiled item under one heading and drops a parked one from
        # another: the net count is unchanged, and identity still catches it.
        self.rebase_on(TWO_VERSIONS)
        swapped = TWO_VERSIONS.replace(
            "- **A second entry.** Published with it.\n",
            "- **A second entry.** Published with it.\n\n"
            "- **A new entry.** Not yet stamped.\n").replace(
            "\n- **An older entry.** Published first.\n", "")
        self.commit_changelog(swapped)
        code, err = self.run_check()
        self.assertEqual(code, 1, err)
        self.assertIn("are new since", err)

    def test_a_heading_only_repair_is_allowed(self):
        # #581: the repair inserts `## 0.3.10` and `### Fixed` above an item
        # the base files under 0.3.9. The old block-identity check read the
        # two split blocks as new content and refused it.
        self.rebase_on(SHARED_HEADING)
        self.commit_changelog(REPAIRED)
        code, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_declared_in_place_edit_of_a_published_entry_is_allowed(self):
        # #503: rewording a published entry under its own heading, declared
        # by a trailer on a commit in the range.
        self.commit_changelog(
            HEADED.replace("Already stamped.", "Reworded in place."),
            "Reword the entry\n\nChangelog-Edit: 0.3.9\n")
        code, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_an_undeclared_edit_is_refused_and_names_the_trailer(self):
        self.commit_changelog(HEADED.replace("Already stamped.", "Reworded in place."))
        code, err = self.run_check()
        self.assertEqual(code, 1, err)
        self.assertIn("Changelog-Edit: <version>", err)

    def test_an_edit_does_not_carry_a_new_item_through(self):
        edited = HEADED.replace(
            "- **A published entry.** Already stamped.\n",
            "- **A published entry.** Reworded in place.\n\n"
            "- **A new entry.** Not yet stamped.\n")
        self.commit_changelog(edited, "Reword\n\nChangelog-Edit: 0.3.9\n")
        code, err = self.run_check()
        self.assertEqual(code, 1, err)
        self.assertIn("A new entry.", err)

    def test_moving_an_entry_between_headings_is_allowed(self):
        # An archive repair -- filing a past entry under the version that
        # published it -- appears in the diff as an addition below a heading,
        # exactly like the hazard. Block-content identity tells them apart: the
        # moved block is text the base already carries (#452).
        moved = HEADED.replace(
            "## 0.3.9 — 2026-01-02\n\n### Added\n\n- **A published entry.** Already stamped.\n",
            "## 0.3.10 — 2026-01-03\n\n### Added\n\n- **A published entry.** Already stamped.\n")
        self.commit_changelog(moved)
        code, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_second_copy_of_a_parked_entry_is_a_new_parked_block(self):
        # coding-policy#457: `set()` dropped multiplicity, so both copies were
        # members of the one-element set and a genuinely added duplicate went
        # through. Occurrences are consumed one for one.
        duplicated = HEADED.replace(
            "### Added\n\n- **A published entry.** Already stamped.\n",
            "### Added\n\n- **A published entry.** Already stamped.\n"
            "\n### Added\n\n- **A published entry.** Already stamped.\n")
        self.commit_changelog(duplicated)
        code, err = self.run_check()
        self.assertEqual(code, 1, err)
        self.assertIn("are new since", err)

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


class PublishModeTest(_RepoCase):
    """`--since-last-publish`: the stamp step's check before it publishes.

    #581's merge published 0.3.291 while its entry sat under 0.3.290 and
    nothing sat above the first `## ` heading, so the stamp found nothing to
    head and the version shipped unheaded. The baseline is the last publish
    bookkeeping commit, so a stopped publish stays stopped.
    """

    def setUp(self):
        super().setUp()
        self.manifest = self.root / ".tessl-plugin" / "plugin.json"
        self.manifest.parent.mkdir()
        self.manifest.write_text('{"version": "0.3.8"}\n', encoding="utf-8")
        self.git("add", ".tessl-plugin/plugin.json")
        self.git("commit", "-q", "-m", "manifest")
        self.bump("0.3.9")

    def bump(self, version):
        """The publish pipeline's version-bump commit, authored by the bot."""
        self.manifest.write_text('{"version": "%s"}\n' % version, encoding="utf-8")
        self.git("-c", "user.name=github-actions[bot]", "commit", "-q", "-am",
                 "Bump to " + version)

    def merge(self, text, message="merge"):
        self.changelog.write_text(text, encoding="utf-8")
        self.git("commit", "-q", "-am", message)

    def run_publish(self):
        proc = subprocess.run(
            [_sys.executable, _os.path.join(_ROOT, "check-changelog-placement.py"),
             "--since-last-publish"],
            cwd=self.root, capture_output=True, text=True)
        return proc.returncode, proc.stderr

    def test_a_merge_parking_its_entry_under_a_published_heading_stops_the_publish(self):
        # The #581 shape: the merged item lands inside the block the previous
        # stamp headed, so nothing sits above the first `## ` heading.
        self.merge(HEADED.replace(
            "### Added\n\n",
            "### Added\n\n- **A new entry.** Not yet stamped.\n\n"))
        code, err = self.run_publish()
        self.assertEqual(code, 1, err)
        self.assertIn("The publish is stopped", err)

    def test_a_stampable_merge_publishes(self):
        self.merge(STAMPABLE)
        code, err = self.run_publish()
        self.assertEqual(code, 0, err)

    def test_a_later_push_cannot_carry_a_stopped_item_through(self):
        # The stopped publish wrote no bookkeeping commit, so the next push
        # (or a manual dispatch) is measured from the last real publish.
        self.merge(MISFILED)
        (self.root / "other.txt").write_text("x", encoding="utf-8")
        self.git("add", "other.txt")
        self.git("commit", "-q", "-m", "unrelated")
        code, err = self.run_publish()
        self.assertEqual(code, 1, err)
        self.assertIn("A new entry.", err)

    def test_a_publish_after_the_last_bump_has_nothing_new(self):
        # A manual dispatch right after a publish: HEAD is the bump commit.
        code, err = self.run_publish()
        self.assertEqual(code, 0, err)

    def test_a_bot_commit_touching_other_files_is_not_a_baseline(self):
        self.merge(MISFILED)
        (self.root / "generated.txt").write_text("x", encoding="utf-8")
        self.git("add", "generated.txt")
        self.git("-c", "user.name=github-actions[bot]", "commit", "-q", "-m", "regen")
        code, err = self.run_publish()
        self.assertEqual(code, 1, err)

    def test_a_declared_edit_publishes(self):
        self.merge(HEADED.replace("Already stamped.", "Reworded in place."),
                   "Reword\n\nChangelog-Edit: 0.3.9\n")
        code, err = self.run_publish()
        self.assertEqual(code, 0, err)

    def test_no_publish_in_history_has_nothing_to_measure(self):
        fresh = tempfile.TemporaryDirectory()
        self.addCleanup(fresh.cleanup)
        root = Path(fresh.name)
        for args in (("init", "-q", "-b", "main"),
                     ("config", "user.email", "t@example.com"),
                     ("config", "user.name", "Test")):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
        (root / "CHANGELOG.md").write_text(MISFILED, encoding="utf-8")
        subprocess.run(["git", "add", "CHANGELOG.md"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=root, check=True, capture_output=True)
        proc = subprocess.run(
            [_sys.executable, _os.path.join(_ROOT, "check-changelog-placement.py"),
             "--since-last-publish"],
            cwd=root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing to measure", proc.stderr)

    def test_a_shallow_checkout_is_a_tool_error(self):
        self.merge(MISFILED)
        clone = tempfile.TemporaryDirectory()
        self.addCleanup(clone.cleanup)
        target = Path(clone.name) / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1",
                        "file://" + str(self.root), str(target)],
                       check=True, capture_output=True)
        proc = subprocess.run(
            [_sys.executable, _os.path.join(_ROOT, "check-changelog-placement.py"),
             "--since-last-publish"],
            cwd=target, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("fetch-depth: 0", proc.stderr)

    def test_a_changelog_absent_at_the_last_publish_has_nothing_to_measure(self):
        self.git("rm", "-q", "CHANGELOG.md")
        self.git("commit", "-q", "-m", "drop")
        self.bump("0.3.10")
        self.changelog.write_text(MISFILED, encoding="utf-8")
        self.git("add", "CHANGELOG.md")
        self.git("commit", "-q", "-m", "first changelog")
        code, err = self.run_publish()
        self.assertEqual(code, 0, err)
        self.assertIn("does not exist at the last publish", err)

    def test_the_two_modes_are_exclusive(self):
        proc = subprocess.run(
            [_sys.executable, _os.path.join(_ROOT, "check-changelog-placement.py"),
             "--base", "main", "--since-last-publish"],
            cwd=self.root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)

if __name__ == "__main__":
    unittest.main()
