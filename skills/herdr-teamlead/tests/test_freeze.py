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


class FreezeDecisionTest(unittest.TestCase):
    """A replay is the complete recorded identity, never a near match on some of its fields."""

    RECORDED = {("reviewer", "codex"): "fp-r", ("tester", "grok"): "fp-t"}

    def rows(self, *keys, status="applied"):
        return [{"id": "id-" + self.RECORDED[key], "fingerprint": self.RECORDED[key], "status": status} for key in keys]

    def decide(self, rows, assignments, identity=None):
        return freeze_decision(rows, assignments,
                               identity or (lambda role, name: {self.RECORDED.get((role, name), "fp-new")}))

    def test_new_work_is_frozen(self):
        self.assertEqual(self.decide([], {"reviewer": "codex"}), "frozen")
        self.assertEqual(self.decide(self.rows(("tester", "grok")), {"reviewer": "codex"}), "frozen")

    def test_the_recorded_identity_keeps_its_sources_whatever_its_status(self):
        # The status decides what the replay does -- a saved receipt, a
        # transport retry of an unsent row -- not whether it is one.
        for status in ("applied", "not_sent", "reserved"):
            with self.subTest(status=status):
                rows = self.rows(("reviewer", "codex"), ("tester", "grok"), status=status)
                self.assertEqual(self.decide(rows, {"reviewer": "codex", "tester": "grok"}), "source")

    def test_a_later_correction_over_the_same_paths_is_frozen(self):
        # A new fix round, correction plan, work record, option or reworded
        # brief resolves to another identity even under the recorded paths.
        rows = self.rows(("reviewer", "codex"))
        self.assertEqual(self.decide(rows, {"reviewer": "codex"}, lambda role, name: {"fp-correction"}), "frozen")

    def test_an_older_form_of_the_same_dispatch_is_a_replay(self):
        # A judge row from before the mode joined its identity (#478).
        rows = self.rows(("reviewer", "codex"))
        self.assertEqual(self.decide(rows, {"reviewer": "codex"}, lambda role, name: {"fp-now", "fp-r"}), "source")

    def test_a_batch_mixing_replays_and_new_roles_is_refused(self):
        with self.assertRaisesRegex(UsageError, "separate calls"):
            self.decide(self.rows(("reviewer", "codex")), {"reviewer": "codex", "tester": "grok"})


if __name__ == "__main__":
    unittest.main()
