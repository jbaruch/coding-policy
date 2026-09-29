"""`foreman finding-churn`: which findings sit on lines the previous fix round added."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import churn, cli
from foreman.errors import UsageError


class PureTest(unittest.TestCase):
    def test_hunk_ranges_with_and_without_counts(self):
        diff = "diff --git a/x b/x\n@@ -3 +3 @@\n-a\n+b\n@@ -10,0 +11,2 @@\n+c\n+d\n@@ -20,2 +22,0 @@\n-e\n-f\n"
        self.assertEqual(churn.added_ranges(diff), [(3, 1), (11, 2), (22, 0)])

    def test_line_inside_an_added_range(self):
        diff = "@@ -10,0 +11,2 @@\n+c\n+d\n"
        self.assertEqual(churn.classify(diff, 12), (True, True))
        self.assertEqual(churn.classify(diff, 13), (False, True))

    def test_deletion_only_hunk_adds_no_line(self):
        self.assertEqual(churn.classify("@@ -5,2 +4,0 @@\n-a\n-b\n", 4), (False, True))

    def test_untouched_file(self):
        self.assertEqual(churn.classify("", 1), (False, False))

    def test_content_line_shaped_like_a_hunk_header_is_not_one(self):
        # A removed line reading "@@ -1 +1 @@" arrives prefixed with "-".
        self.assertEqual(churn.added_ranges("@@ -1 +0,0 @@\n-@@ -1 +1 @@\n"), [(0, 0)])

    def test_finding_parsing(self):
        self.assertEqual(churn.parse_finding("a:b/c.py:7"), ("a:b/c.py", 7))
        for bad in ("c.py", "c.py:0", "c.py:x", ":3"):
            with self.assertRaises(UsageError):
                churn.parse_finding(bad)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class RepoTest(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.repo = Path(holder.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "commit.gpgsign", "false")
        self.write("a.py", ["one", "two", "three", "four"])
        self.write("b.py", ["b1", "b2"])
        self.write("old.py", ["r1", "r2", "r3"])
        self.prior = self.commit("prior fix round")
        self.write("a.py", ["one", "TWO", "three", "four", "five"])
        git(self.repo, "mv", "old.py", "new.py")
        self.head = self.commit("this fix round")

    def write(self, name, lines):
        (self.repo / name).write_text("\n".join(lines) + "\n")

    def commit(self, message):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def run_cli(self, *findings, source=None, target=None):
        argv = ["finding-churn", "--repo", str(self.repo), "--from", source or self.prior,
                "--to", target or self.head]
        for finding in findings:
            argv += ["--finding", finding]
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(argv, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_added_context_and_untouched_findings(self):
        code, out, _ = self.run_cli("a.py:2", "a.py:3", "a.py:5", "b.py:1")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual((payload["from"], payload["to"]), (self.prior, self.head))
        self.assertEqual([(row["path"], row["line"], row["added_by_last_fix"], row["path_changed"])
                          for row in payload["findings"]],
                         [("a.py", 2, True, True), ("a.py", 3, False, True),
                          ("a.py", 5, True, True), ("b.py", 1, False, False)])

    def test_renamed_file_reads_as_added_and_changed(self):
        code, out, _ = self.run_cli("new.py:2")
        self.assertEqual(code, 0)
        row = json.loads(out)["findings"][0]
        self.assertEqual((row["added_by_last_fix"], row["path_changed"]), (True, True))

    def test_unknown_commit_is_refused(self):
        code, out, err = self.run_cli("a.py:1", source="0" * 40)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("--from", json.loads(err)["message"])


if __name__ == "__main__":
    unittest.main()
