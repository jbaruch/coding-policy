#!/usr/bin/env python3
"""worktree-lock.py reports a worktree's lock and its reason (#831).

Each case runs the script against a throwaway `git init` with real
`git worktree add` and `git worktree lock` fixtures; nothing reaches the
network or the operator's git config.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../skills/release/worktree-lock.py"))


class WorktreeLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        empty = os.path.join(self.tmp, "gitconfig")
        with open(empty, "w", encoding="utf-8"):
            pass
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=empty, GIT_CONFIG_NOSYSTEM="1")
        self.repo = os.path.join(self.tmp, "repo")
        self.git("init", "-q", self.repo)
        self.git("-C", self.repo, "-c", "user.name=fixture", "-c", "user.email=fixture@example.com",
                 "commit", "-q", "--allow-empty", "-m", "init")

    def git(self, *args):
        subprocess.run(["git"] + list(args), check=True, env=self.env, capture_output=True)

    def add_worktree(self, path, lock=None):
        """Add a detached worktree; lock=[] locks it bare, lock=[reason] locks it with that reason."""
        self.git("-C", self.repo, "worktree", "add", "-q", "--detach", path)
        if lock is not None:
            self.git("-C", self.repo, "worktree", "lock", *(["--reason"] + lock if lock else []), path)

    def run_script(self, *argv):
        return subprocess.run([sys.executable, _SCRIPT] + list(argv), capture_output=True, text=True,
                              cwd=self.repo, env=self.env, check=False)

    def assert_reports(self, target, locked, reason):
        done = self.run_script(target)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout),
                         {"path": os.path.realpath(target), "locked": locked, "reason": reason})

    def test_lock_with_reason(self):
        path = os.path.join(self.tmp, "held")
        self.add_worktree(path, ["factory task 831"])
        self.assert_reports(path, True, "factory task 831")

    def test_bare_lock_has_no_reason(self):
        path = os.path.join(self.tmp, "bare")
        self.add_worktree(path, [])
        self.assert_reports(path, True, None)

    def test_unlocked(self):
        path = os.path.join(self.tmp, "loose")
        self.add_worktree(path)
        self.assert_reports(path, False, None)

    def test_path_with_newline(self):
        # Line-based parsing splits this stanza at the newline and misses the lock.
        path = os.path.join(self.tmp, "nl\nlocked spoof")
        self.add_worktree(path, ["factory task 831"])
        self.assert_reports(path, True, "factory task 831")

    def test_symlinked_directory(self):
        real = os.path.join(self.tmp, "real")
        os.mkdir(real)
        link = os.path.join(self.tmp, "link")
        os.symlink(real, link)
        self.add_worktree(os.path.join(link, "held"), ["factory task 831"])
        self.assert_reports(os.path.join(link, "held"), True, "factory task 831")

    def test_neighbour_lock_does_not_leak(self):
        self.add_worktree(os.path.join(self.tmp, "held"), ["factory task 831"])
        path = os.path.join(self.tmp, "loose")
        self.add_worktree(path)
        self.assert_reports(path, False, None)

    def test_unlisted_path_fails(self):
        stray = os.path.join(self.tmp, "stray")
        os.mkdir(stray)
        done = self.run_script(stray)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertIn("resolves to", done.stderr)

    def test_usage(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 1)
        self.assertIn("usage:", done.stderr)


if __name__ == "__main__":
    unittest.main()
