#!/usr/bin/env python3
"""An existing-PR release selects the exact checkout without creating a PR.

Git checks use isolated real repositories. GitHub reads use fixed metadata or
a local fake gh executable; no test contacts a service or changes a live PR.
"""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills/release/existing-pr.py"
SPEC = importlib.util.spec_from_file_location("existing_pr_test", SCRIPT)
assert SPEC and SPEC.loader
MOD: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
URL = "https://github.com/acme/widgets/pull/17"


class ExistingPr(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "selected checkout"
        self.checkout.mkdir()
        config = self.root / "empty-gitconfig"
        config.write_text("")
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update({
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(config),
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
        })
        self.environment = env
        self.patch_environment = patch.dict(os.environ, env, clear=True)
        self.patch_environment.start()
        self.addCleanup(self.patch_environment.stop)
        self.git("init", "--initial-branch=main")
        (self.checkout / "fixture.txt").write_text("fixture source\n")
        self.git("add", "fixture.txt")
        self.git("commit", "-m", "Fixture source")
        self.git("checkout", "-b", "promote/fixture")
        self.git("remote", "add", "origin", "https://github.com/acme/widgets.git")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.repository = {"full_name": "acme/widgets", "default_branch": "main"}
        self.pr = {
            "number": 17, "html_url": URL, "state": "open", "merged": False, "draft": False,
            "head": {"ref": "promote/fixture", "sha": self.sha, "repo": {"full_name": "acme/widgets"}},
            "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": "acme/widgets"}},
        }

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.checkout, env=self.environment,
            capture_output=True, text=True, check=True,
        ).stdout

    def api(self, endpoint, checkout):
        self.assertEqual(checkout, self.checkout.resolve())
        if endpoint == "repos/acme/widgets":
            return copy.deepcopy(self.repository)
        self.assertEqual(endpoint, "repos/acme/widgets/pulls/17")
        return copy.deepcopy(self.pr)

    def select(self):
        with patch.object(MOD, "api", side_effect=self.api):
            return MOD.select(URL, self.checkout)

    def test_selects_existing_source_without_local_or_remote_writes(self):
        before = self.git("status", "--porcelain"), self.git("show-ref")
        result = self.select()
        self.assertEqual(result, {
            "schema_version": 1, "repo": "acme/widgets", "owner": "acme", "name": "widgets",
            "pr_number": 17, "url": URL, "head_sha": self.sha, "branch": "promote/fixture",
            "base_ref": "main", "base_sha": "b" * 40, "is_draft": False,
            "checkout": str(self.checkout.resolve()),
        })
        self.assertEqual((self.git("status", "--porcelain"), self.git("show-ref")), before)
        self.assertEqual(self.select(), result)

    def test_draft_is_reported_without_changing_it_or_claiming_readiness(self):
        self.pr["draft"] = True
        selected = self.select()
        self.assertTrue(selected["is_draft"])
        self.assertNotIn("ready", selected)
        self.assertNotIn("approved", selected)
        self.assertTrue(self.pr["draft"])

    def test_case_insensitive_origin_and_all_push_urls_bind_the_same_repo(self):
        self.git("remote", "set-url", "origin", "git@github.com:Acme/Widgets.git")
        self.git("config", "--add", "remote.origin.pushurl", "ssh://git@github.com/acme/widgets.git")
        self.git("config", "--add", "remote.origin.pushurl", "https://github.com/ACME/widgets")
        self.assertEqual(self.select()["head_sha"], self.sha)

    def test_wrong_fetch_or_push_origin_refuses_before_any_api_call(self):
        for field in ["remote.origin.url", "remote.origin.pushurl"]:
            with self.subTest(field=field):
                self.git("config", field, "https://github.com/acme/other.git")
                with patch.object(MOD, "api") as api:
                    with self.assertRaises(MOD.SelectionError):
                        MOD.select(URL, self.checkout)
                    api.assert_not_called()
                self.git("config", field, "https://github.com/acme/widgets.git")

    def test_dirty_tracked_and_untracked_files_are_preserved_and_refused(self):
        for relative in ["fixture.txt", "untracked.txt"]:
            with self.subTest(relative=relative):
                path = self.checkout / relative
                path.write_text("keep these local bytes\n")
                with self.assertRaises(MOD.SelectionError):
                    self.select()
                self.assertEqual(path.read_text(), "keep these local bytes\n")
                if relative == "fixture.txt":
                    self.git("checkout", "--", relative)
                else:
                    path.unlink()

    def test_detached_or_wrong_source_branch_is_refused(self):
        self.git("checkout", "--detach", self.sha)
        with self.assertRaises(MOD.SelectionError):
            self.select()
        self.git("checkout", "main")
        with self.assertRaises(MOD.SelectionError):
            self.select()

    def test_closed_fork_wrong_base_or_head_metadata_is_refused(self):
        original = copy.deepcopy(self.pr)
        for field, value in [
            ("state", "closed"), ("merged", True), ("number", True), ("number", 18),
            ("html_url", "https://github.com/acme/other/pull/17"), ("draft", None),
            ("head", {**original["head"], "sha": "f" * 40}),
            ("head", {**original["head"], "ref": "other-branch"}),
            ("head", {**original["head"], "repo": {"full_name": "forker/widgets"}}),
            ("base", {**original["base"], "ref": "release"}),
            ("base", {**original["base"], "sha": "not-a-complete-sha"}),
            ("base", {**original["base"], "repo": None}),
        ]:
            with self.subTest(field=field, value=value):
                self.pr = {**copy.deepcopy(original), field: value}
                with self.assertRaises(MOD.SelectionError):
                    self.select()
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.sha)

    def test_remote_head_change_during_selection_refuses(self):
        changed = copy.deepcopy(self.pr)
        changed["head"]["sha"] = "f" * 40
        with patch.object(MOD, "api", side_effect=[self.repository, self.pr, changed]):
            with self.assertRaises(MOD.SelectionError):
                MOD.select(URL, self.checkout)

    def test_local_change_during_remote_read_is_preserved_and_refused(self):
        calls = 0

        def api(endpoint, checkout):
            nonlocal calls
            calls += 1
            if calls == 3:
                (self.checkout / "fixture.txt").write_text("concurrent local edit\n")
            return self.api(endpoint, checkout)

        with patch.object(MOD, "api", side_effect=api):
            with self.assertRaises(MOD.SelectionError):
                MOD.select(URL, self.checkout)
        self.assertEqual((self.checkout / "fixture.txt").read_text(), "concurrent local edit\n")

    def test_origin_change_during_remote_read_refuses(self):
        calls = 0

        def api(endpoint, checkout):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.git("remote", "set-url", "--push", "origin", "https://github.com/acme/other")
            return self.api(endpoint, checkout)

        with patch.object(MOD, "api", side_effect=api):
            with self.assertRaises(MOD.SelectionError):
                MOD.select(URL, self.checkout)

    def test_bad_urls_never_fall_back_to_an_implicit_pr(self):
        for url in ["17", "https://github.com/acme/widgets/pull/0", URL + "#review", URL + "?x=y",
                    "https://github.com.evil.invalid/acme/widgets/pull/17",
                    "https://github.com/acme/../pull/17", "https://github.com/a@b/widgets/pull/17"]:
            with self.subTest(url=url), patch.object(MOD, "api") as api:
                with self.assertRaises(MOD.SelectionError):
                    MOD.select(url, self.checkout)
                api.assert_not_called()

    def test_cli_uses_only_explicit_github_gets_and_sanitizes_errors(self):
        binary = self.root / "bin"
        binary.mkdir()
        responses = self.root / "responses.json"
        responses.write_text(json.dumps({"repository": self.repository, "pr": self.pr}))
        log = self.root / "gh-requests.jsonl"
        fake = binary / "gh"
        fake.write_text(
            "#!" + sys.executable + "\n"
            "import json,os,sys\n"
            "from pathlib import Path\n"
            "with open(os.environ['PR_TEST_LOG'],'a') as out: out.write(json.dumps(sys.argv[1:])+'\\n')\n"
            "if os.environ.get('PR_TEST_FAIL'): print('secret-token private-source',file=sys.stderr); sys.exit(17)\n"
            "data=json.loads(Path(os.environ['PR_TEST_DATA']).read_text())\n"
            "print(json.dumps(data['pr' if '/pulls/' in sys.argv[-1] else 'repository']))\n"
        )
        fake.chmod(0o755)
        env = {**self.environment, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
               "PR_TEST_DATA": str(responses), "PR_TEST_LOG": str(log)}

        def run(extra=None):
            return subprocess.run([sys.executable, str(SCRIPT), URL, str(self.checkout)],
                                  capture_output=True, text=True, check=False, env={**env, **(extra or {})})

        result = run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["head_sha"], self.sha)
        requests = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(requests), 3)
        for request in requests:
            self.assertEqual(request[:5], ["api", "--hostname", "github.com", "--method", "GET"])
        failed = run({"PR_TEST_FAIL": "1"})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertNotIn("secret-token", failed.stderr)
        self.assertNotIn("private-source", failed.stderr)


if __name__ == "__main__":
    unittest.main()
