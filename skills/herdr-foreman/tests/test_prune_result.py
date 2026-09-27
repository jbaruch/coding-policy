"""Shape checks shared by every reader of the two prune scripts' results."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foreman.prune_result import prune_schema_error, remote_schema_error


def prune_doc(**over):
    doc = {"worktrees_removed": [], "worktrees_kept": [], "branches_deleted": [], "branches_kept": [], "failed": []}
    doc.update(over)
    return doc


def remote_doc(**over):
    doc = {"deleted": [], "questionable": [], "kept": [], "failed": [], "could_not_check": None, "default_branch": "main"}
    doc.update(over)
    return doc


class PruneResultTest(unittest.TestCase):
    def test_whole_prune_result_passes(self):
        kept = [{"path": "/w/a", "reason": "dirty", "age_hours": 30, "dirty_files": 2, "command": "git -C /w/a status"},
                {"path": "/w/b", "reason": "locked"}]
        branches = [{"branch": "feat/x", "reason": "unpushed", "unpushed_commits": 1, "age_hours": 40, "command": "git push"}]
        self.assertIsNone(prune_schema_error(prune_doc(worktrees_kept=kept, branches_kept=branches,
                                                       failed=[{"target": "/w/c", "error": "busy"}])))

    def test_malformed_prune_results_are_named(self):
        cases = {
            "not a JSON object": [],
            "no failed list": prune_doc(failed=None),
            "a malformed worktrees_removed entry": prune_doc(worktrees_removed=[None]),
            "a malformed branches_deleted entry": prune_doc(branches_deleted=[{}]),
            "a malformed worktrees_kept entry": prune_doc(worktrees_kept=[None]),
            "a malformed dirty worktree entry": prune_doc(worktrees_kept=[{"path": "/w/a", "reason": "dirty"}]),
            "a malformed branches_kept entry": prune_doc(branches_kept=[{"reason": "unpushed"}]),
            "a malformed unpushed branch entry": prune_doc(branches_kept=[{"branch": "b", "reason": "unpushed"}]),
            "a malformed failed entry": prune_doc(failed=[{"target": "/w/a"}]),
        }
        for why, doc in cases.items():
            with self.subTest(why=why):
                self.assertEqual(prune_schema_error(doc), why)

    def test_remote_result(self):
        self.assertIsNone(remote_schema_error(remote_doc()))
        self.assertEqual(remote_schema_error(remote_doc(default_branch=None)), "no default_branch")
        self.assertEqual(remote_schema_error(remote_doc(questionable=[{"branch": "b"}])), "a malformed questionable entry")
        self.assertEqual(remote_schema_error(remote_doc(failed=[None])), "a malformed failed entry")
        self.assertEqual(remote_schema_error(remote_doc(deleted=[None])), "a malformed deleted entry")
        self.assertEqual(remote_schema_error(remote_doc(kept=[{}])), "a malformed kept entry")
        self.assertEqual(remote_schema_error({"deleted": []}), "no questionable list")


if __name__ == "__main__":
    unittest.main()
