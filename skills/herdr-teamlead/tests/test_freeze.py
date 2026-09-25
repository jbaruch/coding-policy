"""A dispatched brief is frozen to a content-addressed copy nothing rewrites (#460)."""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teamlead.assign import FROZEN_DIR, freeze_decision, freeze_paths
from teamlead.errors import UsageError


class FreezeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="teamlead-freeze-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.common = self.root / "COMMON.md"
        self.brief = self.root / "reviewer.md"
        self.common.write_text("common rules\n")
        self.brief.write_text("review this\n")
        self.paths = {"common": str(self.common), "reviewer": str(self.brief)}

    def test_freezes_every_brief_beside_its_source(self):
        frozen = freeze_paths(self.paths)
        for key, source in self.paths.items():
            with self.subTest(key=key):
                copy = Path(frozen[key])
                self.assertEqual(copy.parent, Path(source).parent / FROZEN_DIR)
                self.assertEqual(copy.read_bytes(), Path(source).read_bytes())

    def test_the_same_content_freezes_to_the_same_file(self):
        self.assertEqual(freeze_paths(self.paths), freeze_paths(self.paths))

    def test_changed_content_freezes_to_a_new_file_and_keeps_the_old(self):
        first = freeze_paths(self.paths)["reviewer"]
        self.brief.write_text("review something else\n")
        second = freeze_paths(self.paths)["reviewer"]
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_text(), "review this\n")

    def test_a_frozen_file_with_other_content_is_never_rewritten(self):
        target = Path(freeze_paths(self.paths)["reviewer"])
        target.write_text("tampered\n")
        with self.assertRaisesRegex(UsageError, "never rewritten"):
            freeze_paths(self.paths)
        self.assertEqual(target.read_text(), "tampered\n")

    def test_an_unreadable_source_names_the_repair(self):
        with self.assertRaisesRegex(UsageError, "Restore readability"):
            freeze_paths({"common": str(self.root / "missing.md")})


class FreezeLinkTest(FreezeTest):
    def test_a_frozen_path_that_is_a_link_is_refused(self):
        target = Path(freeze_paths(self.paths)["reviewer"])
        target.unlink()
        target.symlink_to(self.brief)
        with self.assertRaisesRegex(UsageError, "is a link"):
            freeze_paths(self.paths)

    def test_a_frozen_path_hard_linked_to_its_source_is_refused(self):
        # A hard link shares the source's inode: rewriting the source would
        # rewrite the "frozen" copy with it.
        target = Path(freeze_paths(self.paths)["reviewer"])
        target.unlink()
        os.link(self.brief, target)
        with self.assertRaisesRegex(UsageError, "is a link"):
            freeze_paths(self.paths)

    def test_a_symlinked_frozen_directory_is_refused(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.root / FROZEN_DIR).symlink_to(elsewhere)
        with self.assertRaisesRegex(UsageError, "is a link or not a directory"):
            freeze_paths(self.paths)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_copy_read_back_through_a_symlinked_directory_is_refused(self):
        from teamlead.assign import read_frozen
        frozen = Path(freeze_paths(self.paths)["reviewer"])
        moved = self.root / "moved"
        frozen.parent.rename(moved)
        frozen.parent.symlink_to(moved)
        with self.assertRaisesRegex(UsageError, "is a link or not a directory"):
            read_frozen(frozen)

    def test_a_fifo_or_directory_at_the_frozen_path_is_refused(self):
        for plant in (os.mkfifo, os.mkdir):
            with self.subTest(plant=plant.__name__):
                target = Path(freeze_paths(self.paths)["reviewer"])
                target.unlink()
                plant(target)
                with self.assertRaisesRegex(UsageError, "not a regular file"):
                    freeze_paths(self.paths)
                target.unlink() if plant is os.mkfifo else target.rmdir()

    def test_a_frozen_path_that_fails_inspection_is_an_actionable_refusal(self):
        # coding-policy#460 review: the inspection used to run inside the
        # `except FileExistsError` block, where the sibling `except OSError`
        # never catches it, so a vanished or unreadable copy leaked a traceback.
        freeze_paths(self.paths)
        for error in (FileNotFoundError(2, "No such file or directory"), PermissionError(13, "Permission denied")):
            with self.subTest(error=type(error).__name__), patch("teamlead.assign.os.open", side_effect=error):
                with self.assertRaisesRegex(UsageError, "Cannot open frozen brief .*: {}. Restore".format(error.strerror)):
                    freeze_paths(self.paths)
        with patch("teamlead.assign.os.fstat", side_effect=OSError(5, "Input/output error")):
            with self.assertRaisesRegex(UsageError, "Cannot read frozen brief .*Input/output error"):
                freeze_paths(self.paths)


class FreezeDecisionTest(unittest.TestCase):
    """The decision follows the replay answer per role, and never lets a new role skip the freeze."""

    def decide(self, assignments, replays):
        return freeze_decision(assignments, lambda role, name: (role, name) in replays)

    def test_new_work_is_frozen(self):
        self.assertEqual(self.decide({"reviewer": "codex"}, set()), "frozen")

    def test_a_batch_of_replays_keeps_its_sources(self):
        both = {("reviewer", "codex"), ("tester", "grok")}
        self.assertEqual(self.decide({"reviewer": "codex", "tester": "grok"}, both), "source")

    def test_a_replay_is_per_agent(self):
        self.assertEqual(self.decide({"reviewer": "claude"}, {("reviewer", "codex")}), "frozen")

    def test_a_batch_mixing_replays_and_new_roles_is_refused(self):
        with self.assertRaisesRegex(UsageError, "separate calls"):
            self.decide({"reviewer": "codex", "tester": "grok"}, {("reviewer", "codex")})


if __name__ == "__main__":
    unittest.main()
