#!/usr/bin/env python3
"""origin-repo.py names origin's GitHub repository or refuses (#655).

URL forms are checked on the pure parser; the end-to-end cases run the script
against a throwaway `git init` whose origin is only configured, never fetched,
so no case reaches the network or a live repository.
"""

import os as _os

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any

_SCRIPT = _os.path.join(_ROOT, "origin-repo.py")
_SPEC = importlib.util.spec_from_file_location("origin_repo", _SCRIPT)
assert _SPEC and _SPEC.loader, f"cannot load origin-repo.py at {_SCRIPT}"
# Typed Any: the module's attributes are only known once it executes.
origin_repo: Any = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(origin_repo)


class ParseGithubUrl(unittest.TestCase):
    def test_accepted_forms(self):
        cases = {
            "https://github.com/acme/widgets": ("acme", "widgets"),
            "https://github.com/acme/widgets.git": ("acme", "widgets"),
            "https://github.com/acme/widgets/": ("acme", "widgets"),
            "https://x-access-token:secret@github.com/acme/widgets.git": ("acme", "widgets"),
            "https://GitHub.com/Acme/my.repo_1": ("Acme", "my.repo_1"),
            "ssh://git@github.com/acme/widgets": ("acme", "widgets"),
            "ssh://git@github.com/acme/widgets.git": ("acme", "widgets"),
            "ssh://git@github.com:22/acme/widgets.git": ("acme", "widgets"),
            "ssh://github.com/acme/widgets": ("acme", "widgets"),
            "git@github.com:acme/widgets": ("acme", "widgets"),
            "git@github.com:acme/widgets.git": ("acme", "widgets"),
            "github.com:acme/widgets.git": ("acme", "widgets"),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(origin_repo.parse_github_url(url), expected)

    def test_refused_forms(self):
        for url in (
            "https://gitlab.com/acme/widgets.git",
            "git@gitlab.com:acme/widgets.git",
            "https://github.com.evil.example/acme/widgets",
            "https://github.com/acme",
            "https://github.com/acme/widgets/tree/main",
            "https://github.com/acme/..",
            "http://github.com/acme/widgets",
            "git://github.com/acme/widgets",
            "file:///srv/git/widgets.git",
            "/srv/git/widgets.git",
            "../widgets.git",
            "github-work:acme/widgets",
            "git@github.com:/acme/widgets",
            "",
        ):
            with self.subTest(url=url):
                self.assertIsNone(origin_repo.parse_github_url(url))


class Script(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        empty = _os.path.join(self.tmp, "gitconfig")
        with open(empty, "w", encoding="utf-8"):
            pass
        # The operator's git config (insteadOf rewrites included) never reaches the fixture.
        self.env = dict(_os.environ, GIT_CONFIG_GLOBAL=empty, GIT_CONFIG_NOSYSTEM="1")
        self.repo = _os.path.join(self.tmp, "repo")
        self.git("init", "-q", self.repo)

    def git(self, *args):
        subprocess.run(["git"] + list(args), check=True, env=self.env, capture_output=True)

    def run_script(self, *argv):
        return subprocess.run([sys.executable, _SCRIPT] + list(argv), capture_output=True, text=True,
                              env=self.env, check=False)

    def test_resolves_origin(self):
        self.git("-C", self.repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
        done = self.run_script(self.repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"repo": "acme/widgets", "owner": "acme", "name": "widgets"})

    def test_non_github_origin_refused_without_url(self):
        self.git("-C", self.repo, "remote", "add", "origin", "https://token123@gitlab.com/acme/widgets.git")
        done = self.run_script(self.repo)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        self.assertIn("not a GitHub repository URL", done.stderr)
        self.assertNotIn("token123", done.stderr)

    def test_push_url_elsewhere_refused(self):
        self.git("-C", self.repo, "remote", "add", "origin", "https://github.com/acme/widgets.git")
        self.git("-C", self.repo, "remote", "set-url", "--push", "origin", "https://github.com/other/widgets.git")
        done = self.run_script(self.repo)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        self.assertIn("pushes elsewhere", done.stderr)

    def test_same_repo_push_url_in_another_form_accepted(self):
        self.git("-C", self.repo, "remote", "add", "origin", "https://github.com/acme/widgets.git")
        self.git("-C", self.repo, "remote", "set-url", "--push", "origin", "git@github.com:acme/widgets")
        done = self.run_script(self.repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["repo"], "acme/widgets")

    def test_second_push_url_elsewhere_refused(self):
        self.git("-C", self.repo, "remote", "add", "origin", "https://github.com/acme/widgets.git")
        self.git("-C", self.repo, "remote", "set-url", "--add", "--push", "origin", "https://github.com/acme/widgets.git")
        self.git("-C", self.repo, "remote", "set-url", "--add", "--push", "origin", "https://github.com/other/widgets.git")
        done = self.run_script(self.repo)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        self.assertIn("pushes elsewhere", done.stderr)

    def test_case_only_push_difference_accepted_with_fetch_spelling(self):
        self.git("-C", self.repo, "remote", "add", "origin", "https://github.com/Acme/Widgets")
        self.git("-C", self.repo, "remote", "set-url", "--push", "origin", "git@github.com:acme/widgets.git")
        done = self.run_script(self.repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["repo"], "Acme/Widgets")

    def test_no_origin_is_precondition(self):
        done = self.run_script(self.repo)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertIn("remote add origin", done.stderr)

    def test_usage(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 1)
        self.assertIn("usage", done.stderr)


if __name__ == "__main__":
    unittest.main()
