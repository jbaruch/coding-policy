"""A dispatched brief is frozen to a content-addressed copy nothing rewrites (#460)."""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import tempfile
import unittest
from pathlib import Path

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


class FreezeDecisionTest(unittest.TestCase):
    PATHS = {"common": "/b/COMMON.md", "reviewer": "/b/reviewer.md", "tester": "/b/tester.md"}

    def row(self, role, agent, brief=None, common="/b/COMMON.md", task="t"):
        return {"task": task, "role": role, "agent": agent, "brief": brief or self.PATHS[role], "common": common}

    def test_new_work_is_frozen(self):
        self.assertEqual(freeze_decision([], "t", {"reviewer": "codex"}, self.PATHS), "frozen")
        self.assertEqual(freeze_decision([self.row("reviewer", "codex")], None, {"reviewer": "codex"}, self.PATHS), "frozen")

    def test_an_exact_source_path_replay_keeps_its_sources(self):
        rows = [self.row("reviewer", "codex"), self.row("tester", "grok")]
        self.assertEqual(freeze_decision(rows, "t", {"reviewer": "codex", "tester": "grok"}, self.PATHS), "source")

    def test_a_near_match_is_new_work(self):
        for row in (self.row("reviewer", "claude"), self.row("reviewer", "codex", common="/b/OTHER.md"),
                    self.row("reviewer", "codex", task="other")):
            with self.subTest(row=row):
                self.assertEqual(freeze_decision([row], "t", {"reviewer": "codex"}, self.PATHS), "frozen")

    def test_a_batch_mixing_replays_and_new_roles_is_refused(self):
        with self.assertRaisesRegex(UsageError, "separate calls"):
            freeze_decision([self.row("reviewer", "codex")], "t", {"reviewer": "codex", "tester": "grok"}, self.PATHS)


if __name__ == "__main__":
    unittest.main()
