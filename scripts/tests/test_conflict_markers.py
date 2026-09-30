"""Outcome tests for check-conflict-markers.py, plus the live repo gate.

The live case scans this repository, so a PR that commits a leftover conflict
marker fails the suite CI runs (#668). Fixture cases build throwaway git repos.
Marker lines are assembled at run time so this file never carries one itself.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "check-conflict-markers.py"
REPO = Path(__file__).resolve().parents[2]

OURS, BASE, THEIRS, SPLIT = "<" * 7, "|" * 7, ">" * 7, "=" * 7


def run(*args):
    return subprocess.run(["python3", str(SCRIPT), *args], capture_output=True, text=True, check=False)


class LiveRepo(unittest.TestCase):
    def test_repo_has_no_conflict_markers(self):
        result = run(str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["clean"])


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": self.tmp.name}
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, env=env)

    def track(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        subprocess.run(["git", "-C", str(self.repo), "add", name], check=True)

    def test_each_marker_kind_is_reported_with_its_line(self):
        for marker in (OURS + " HEAD", BASE + " 7f5eec1a", THEIRS + " origin/main", BASE):
            with self.subTest(marker=marker):
                self.track("CHANGELOG.md", "# Changelog\n\nentry\n{}\nmore\n".format(marker))
                result = run(str(self.repo))
                self.assertEqual(result.returncode, 1)
                report = json.loads(result.stdout)
                self.assertFalse(report["clean"])
                self.assertEqual([(m["path"], m["line"]) for m in report["markers"]], [("CHANGELOG.md", 4)])
                self.assertIn("CHANGELOG.md:4", result.stderr)

    def test_lookalikes_pass(self):
        self.track("doc.md", "Title\n{}\n\n  {} indented\n{}x no space\n`{}` inline\n".format(SPLIT, OURS, THEIRS, BASE))
        result = run(str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"clean": True, "files_scanned": 1, "markers": []})

    def test_untracked_and_binary_files_are_skipped(self):
        self.track("image.bin", b"\0" + (OURS + " HEAD\n").encode())
        (self.repo / "scratch.md").write_text(OURS + " HEAD\n")
        result = run(str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["files_scanned"], 0)

    def test_a_non_checkout_is_a_setup_error(self):
        with tempfile.TemporaryDirectory() as bare:
            result = run(bare)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("git checkout", result.stderr)


if __name__ == "__main__":
    unittest.main()
