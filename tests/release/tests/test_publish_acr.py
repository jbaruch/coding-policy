"""Tag ownership, clean source and a fresh default gate the ACR publisher."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
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

    def test_publish_workflow_rejects_non_exact_main_before_credentialed_checkout(self):
        workflow = (SOURCE.parents[2] / ".github/workflows/publish.yml").read_text()
        tessl_job = workflow.split("  publish:\n", 1)[1].split("  acr:\n", 1)[0]
        guard, following = tessl_job.split("    steps:\n", 1)[1].split("\n      - ", 1)
        header, run = guard.split("        run: |\n", 1)
        self.assertEqual(header, "      - name: Require exact main ref\n        shell: bash\n")
        self.assertTrue(following.startswith("uses: actions/checkout@"))
        self.assertIn("ssh-key: ${{ secrets.STAMP_DEPLOY_KEY }}", following)
        self.assertIn("uses: ./.github/actions/smart-publish", following)
        for ref in ("refs/heads/main", "refs/heads/Main", "refs/heads/MAIN", "refs/heads/mAiN",
                    "refs/heads/feature", "refs/tags/main", "refs/heads/main-other", ""):
            with self.subTest(ref=ref):
                result = subprocess.run(["bash", "-c", textwrap.dedent(run)], cwd=self.root,
                                        env=dict(self.env, GITHUB_REF=ref), capture_output=True, text=True)
                if ref == "refs/heads/main":
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                else:
                    self.assertEqual((result.returncode, result.stdout), (1, ""))
                    self.assertIn("::error::Publication requires exact ref refs/heads/main", result.stderr)
                    self.assertIn("select main and dispatch again", result.stderr)

    def test_publish_workflow_tags_the_commit_back_version_even_after_main_advances(self):
        root = SOURCE.parents[2]
        workflow = (root / ".github/workflows/publish.yml").read_text()
        tessl_job, acr_job = workflow.split("  acr:\n", 1)
        self.assertIn("source-commit: ${{ steps.publish_autobump.outputs.source-commit }}", tessl_job)
        self.assertIn("ssh-key: ${{ secrets.STAMP_DEPLOY_KEY }}", tessl_job)
        self.assertIn("mode: auto-bump\n          stamp-changelog: 'true'", tessl_job)
        self.assertIn("uses: ./.github/actions/publish-landed-gate", tessl_job)
        self.assertIn("publish-outcome: ${{ steps.publish_autobump.outputs.source-commit != '' && steps.publish_autobump.outcome || 'failure' }}", tessl_job)
        self.assertIn("credit-signature: ${{ steps.publish_autobump.outputs.source-commit != '' && steps.publish_autobump.outputs.credit-signature || 'false' }}", tessl_job)
        self.assertIn("    needs: publish\n", acr_job)
        self.assertIn("ref: ${{ needs.publish.outputs.source-commit }}", acr_job)
        self.assertIn("source-commit: ${{ needs.publish.outputs.source-commit }}", acr_job)
        self.assertNotIn("${{ github.sha }}", acr_job)

        (self.repo / "agent-plugin.yaml").write_text("name: fixture/package\nversion: 0.4.0\n")
        (self.repo / ".tessl-plugin").mkdir()
        (self.repo / ".tessl-plugin/plugin.json").write_text(json.dumps({"name": "fixture/package", "version": "0.4.0"}))
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n### A merged change\n")
        self.git("add", ".")
        self.git("commit", "-qm", "merged source")
        self.git("push", "-q", "origin", "main")
        merged = self.git("rev-parse", "HEAD").strip()
        tessl = self.bin / "tessl"
        tessl.write_text(textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, sys
            if sys.argv[1] == 'api':
                if '/versions/' in sys.argv[2]:
                    print(json.dumps({'error': {'status': 404, 'title': 'Not Found', 'message': 'Not Found'}}))
                    sys.exit(1)
                print(json.dumps({'data': [{'attributes': {'version': '0.4.0'}}]}))
            else:
                print('Published')
            '''))
        tessl.chmod(0o755)
        action = root / ".github/actions/smart-publish/action.yml"
        action_text = action.read_text()
        self.assertIn("value: ${{ steps.publish.outputs.source-commit }}", action_text)
        run = textwrap.dedent(action_text.split("      run: |\n", 1)[1])
        run = run.replace("${{ inputs.skip-evals }}", "false")
        output = self.root / "outputs"
        environment = dict(self.env, MODE="auto-bump", PLUGIN_PATH=".", STAMP_CHANGELOG="true",
                           GITHUB_ACTION_PATH=str(action.parent), GITHUB_REF_NAME="main",
                           GITHUB_REF_TYPE="branch", GITHUB_OUTPUT=str(output))
        result = subprocess.run(["bash", "-c", run], cwd=self.repo, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
        source = outputs["source-commit"]
        self.assertNotEqual(source, merged)
        self.assertEqual(outputs["version"], "0.4.1")
        self.assertEqual(self.git("rev-parse", "origin/main").strip(), source)
        self.assertEqual(json.loads(self.git("show", source + ":.tessl-plugin/plugin.json"))["version"], "0.4.1")
        self.assertIn("version: 0.4.1\n", self.git("show", source + ":agent-plugin.yaml"))

        # A later merge must not change this run's ACR version or tag source.
        (self.repo / "agent-plugin.yaml").write_text("name: fixture/package\nversion: 9.9.9\n")
        self.git("add", ".")
        self.git("commit", "-qm", "later merge")
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", source)
        (self.bin / "acr").write_text(textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, sys
            from pathlib import Path
            if sys.argv[1] == 'validate':
                lines = (Path(sys.argv[2]) / 'agent-plugin.yaml').read_text().splitlines()
                version = next(line.split(': ', 1)[1] for line in lines if line.startswith('version: '))
                print(json.dumps({'result': {'valid': True, 'version': version}}))
            else:
                print(json.dumps({'ok': True}))
            '''))
        result = self.publish(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "v0.4.1").strip(), source)
        self.assertEqual(self.git("tag").strip(), "v0.4.1")
        retry = self.publish(source)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.git("rev-parse", "v0.4.1").strip(), source)

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
