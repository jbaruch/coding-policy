"""Tag ownership, clean source and a fresh default gate the ACR publisher."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[3] / "skills/release/publish-acr.sh"
ACTION = SOURCE.parents[2] / ".github/actions/publish-acr/action.yml"


class PublishAcrTest(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.remote = self.root / "origin.git"
        self.repo = self.root / "package with spaces"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        self.git("init", "--bare", "-q", "--initial-branch=main", str(self.remote), cwd=self.root)
        self.git("clone", "-q", str(self.remote), str(self.repo), cwd=self.root)
        (self.repo / "agent-plugin.yaml").write_text("fixture manifest\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.git("push", "-qu", "origin", "main")
        self.head = self.git("rev-parse", "HEAD").strip()
        fake = self.bin / "acr"
        fake.write_text("#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n"
                        "if sys.argv[1] == 'validate':\n"
                        " print(json.dumps({'result':{'valid':True,'version':'0.4.0'}}))\n"
                        "else:\n Path(sys.argv[2]).parent.joinpath('published').touch()\n"
                        " print(json.dumps({'ok':True,'command':'publish'}))\n")
        fake.chmod(0o755)

    def git(self, *args, cwd=None):
        result = subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env,
                                capture_output=True, text=True, check=True)
        return result.stdout

    def publish(self, source=None):
        return subprocess.run(["bash", str(SOURCE), str(self.repo), *([source] if source else [])], cwd=self.root,
                              env=self.env, capture_output=True, text=True)

    def test_new_tag_publishes_and_same_commit_retry_preserves_it(self):
        first = self.publish()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(json.loads(first.stdout)["ok"])
        self.assertEqual(self.git("rev-parse", "v0.4.0").strip(), self.head)
        second = self.publish()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.git("rev-parse", "v0.4.0").strip(), self.head)

    def test_invalid_validation_result_has_an_actionable_diagnostic_and_no_tag(self):
        for result in ({"valid": False}, {"valid": True}, {"valid": True, "version": ""}):
            with self.subTest(result=result):
                (self.bin / "acr").write_text("#!/usr/bin/env python3\nprint(" + repr(json.dumps({"result": result})) + ")\n")
                failure = self.publish()
                self.assertEqual((failure.returncode, failure.stdout), (2, ""))
                self.assertIn("repair the manifest", failure.stderr)
                self.assertEqual(self.git("tag"), "")

    def test_an_existing_version_at_another_commit_is_never_moved(self):
        self.git("tag", "v0.4.0")
        (self.repo / "change").write_text("new version needs another tag\n")
        self.git("add", ".")
        self.git("commit", "-qm", "advance")
        self.git("push", "-q", "origin", "main", "v0.4.0")
        result = self.publish()
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("immutable", result.stderr)
        self.assertEqual(self.git("rev-parse", "v0.4.0").strip(), self.head)
        self.assertFalse((self.root / "published").exists())

    def test_non_commit_tag_is_preserved_and_refused_with_recovery(self):
        blob = self.git("hash-object", "agent-plugin.yaml").strip()
        self.git("tag", "v0.4.0", blob)
        self.git("push", "-q", "origin", "v0.4.0")
        result = self.publish()
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("non-commit object", result.stderr)
        self.assertIn("reconcile the existing publication", result.stderr)
        self.assertEqual(self.git("rev-parse", "v0.4.0").strip(), blob)
        self.assertFalse((self.root / "published").exists())

    def test_tag_creation_and_push_failures_are_actionable_and_do_not_publish(self):
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        fake = self.bin / "git"
        fake.write_text("#!/usr/bin/env python3\nimport os,sys\n"
                        "args=sys.argv[1:]\n"
                        "operation=args[2] if len(args)>2 and args[0]=='-C' else args[0]\n"
                        "if operation==os.environ['PUBLISH_FAIL_OPERATION']:\n"
                        " sys.stderr.write('private transport fixture')\n"
                        " sys.exit(1)\n"
                        "os.execv(" + repr(real_git) + ", ['git',*args])\n")
        fake.chmod(0o755)
        for operation, recovery in (("fetch", "restore origin access"), ("remote", "restore origin access"),
                                    ("tag", "repair local Git write access"), ("push", "restore origin write access")):
            with self.subTest(operation=operation):
                self.env["PUBLISH_FAIL_OPERATION"] = operation
                result = self.publish()
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn(recovery, result.stderr)
                self.assertNotIn("private transport fixture", result.stdout + result.stderr)
                self.assertFalse((self.root / "published").exists())

    def test_composite_action_forwards_package_binary_and_source_from_foreign_cwd(self):
        # Execute this action's checked-in single Bash step. This fixture reads
        # only its fixed env-expression/one-line-run layout, not arbitrary YAML.
        text = ACTION.read_text()
        self.assertIn("      shell: bash\n", text)
        bindings, run = text.split("      env:\n", 1)[1].split("      run: ", 1)
        inputs = {"path": str(self.repo), "bin-directory": str(self.bin), "source-commit": self.head}
        environment = dict(self.env, GITHUB_ACTION_PATH=str(ACTION.parent))
        for line in bindings.splitlines():
            match = re.fullmatch(r"        ([A-Z_]+): \$\{\{ inputs\.([a-z-]+) \}\}", line)
            self.assertIsNotNone(match, "update the strict fixture for the action's declared layout")
            assert match is not None
            environment[match[1]] = inputs[match[2]]
        self.assertEqual(len(run.splitlines()), 1)
        # A stale HEAD requires the explicit source argument to be forwarded.
        (self.repo / "advance").write_text("new default\n")
        self.git("add", ".")
        self.git("commit", "-qm", "advance")
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", self.head)
        # The supplied bin directory must win over the caller's PATH.
        poison = self.root / "wrong acr"
        poison.mkdir()
        (poison / "acr").write_text("#!/bin/sh\nexit 91\n")
        (poison / "acr").chmod(0o755)
        environment["PATH"] = str(poison) + os.pathsep + os.environ["PATH"]
        result = subprocess.run(["bash", "-euo", "pipefail", "-c", run], cwd=self.root, env=environment,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        self.assertTrue((self.root / "published").exists())
        self.assertEqual(self.git("rev-parse", "v0.4.0").strip(), self.head)

    def test_dirty_or_stale_default_never_creates_a_release_tag(self):
        (self.repo / "untracked").write_text("keep\n")
        self.assertEqual(self.publish().returncode, 2)
        self.assertEqual(self.git("tag"), "")
        (self.repo / "untracked").unlink()
        (self.repo / "advance").write_text("new default\n")
        self.git("add", ".")
        self.git("commit", "-qm", "advance")
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", self.head)
        result = self.publish()
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("fetched remote default", result.stderr)
        self.assertEqual(self.git("tag"), "")
        pinned = self.publish(self.head)
        self.assertEqual(pinned.returncode, 0, pinned.stderr)
        self.assertEqual(self.git("rev-parse", "v0.4.0").strip(), self.head)


if __name__ == "__main__":
    unittest.main()
