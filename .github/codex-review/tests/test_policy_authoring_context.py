"""Provider identity comes from the base; head rules are regular committed blobs."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "policy-authoring-context.py"
SPEC = importlib.util.spec_from_file_location("policy_authoring_context", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AuthoringContextTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        self.root = Path(scratch.name)
        os.chdir(self.root)
        self.environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                            "GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
                            "GIT_AUTHOR_EMAIL": "fixture@example.com", "GIT_COMMITTER_EMAIL": "fixture@example.com",
                            "GIT_AUTHOR_DATE": "2026-10-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-10-01T00:00:00Z"}
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", os.devnull)

    def git(self, *args):
        return subprocess.run(["git", *args], env=self.environment, text=True,
                              capture_output=True, check=True).stdout.strip()

    def write(self, path, body):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)

    def declare(self, name="jbaruch/example", rules=None, manifest=".tessl-plugin/plugin.json"):
        self.write(manifest, json.dumps({"name": name, "rules": [] if rules is None else rules}))

    def save(self):
        self.git("add", ".")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Fixture")
        return self.git("rev-parse", "HEAD")

    def provider(self):
        self.declare()
        return self.save()

    def test_verified_provider_reads_only_declared_committed_head_rules(self):
        base = self.provider()
        self.declare(rules=["rules/lookup.md"])
        self.write("rules/lookup.md", "authored rule body\n")
        head = self.save()
        self.write("rules/undeclared.md", "not authority\n")
        before = self.git("status", "--porcelain")
        result = MODULE.context("jbaruch/example", base)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["rules"], ["rules/lookup.md"])
        self.assertEqual((result["base"], result["head"]), (base, head))
        self.assertNotIn("authored rule body", json.dumps(result))
        self.assertEqual(self.git("status", "--porcelain"), before)

    def test_head_cannot_create_provider_identity(self):
        base = self.save()
        self.declare(rules=["rules/lookup.md"])
        self.write("rules/lookup.md", "cannot grant authority\n")
        self.save()
        self.assertFalse(MODULE.context("jbaruch/example", base)["eligible"])

    def test_foreign_base_identity_is_not_a_provider_for_this_repository(self):
        self.declare(name="another/plugin")
        base = self.save()
        self.declare()
        self.save()
        self.assertFalse(MODULE.context("jbaruch/example", base)["eligible"])

    def test_uncommitted_manifest_changes_do_not_grant_authority(self):
        base = self.provider()
        self.declare(rules=["rules/uncommitted.md"])
        self.assertEqual(MODULE.context("jbaruch/example", base)["rules"], [])

    def test_legacy_manifest_has_the_same_identity_guard(self):
        self.declare(manifest="tile.json")
        base = self.save()
        result = MODULE.context("jbaruch/example", base)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["manifest"], "tile.json")

    def test_directory_form_selects_nested_markdown_blobs_without_reading_bodies(self):
        base = self.provider()
        self.declare(rules="rules/")
        self.write("rules/one.md", "source rule, not executed\n")
        self.write("rules/nested/two.md", "nested rule\n")
        self.write("rules/notes.txt", "not a rule\n")
        head = self.save()
        result = MODULE.context("jbaruch/example", base)
        self.assertEqual(result["rules"], ["rules/nested/two.md", "rules/one.md"])
        self.assertEqual(result["rule_blobs"]["rules/one.md"], self.git("rev-parse", head + ":rules/one.md"))
        self.assertNotIn("source rule, not executed", json.dumps(result))

    def test_malformed_manifest_or_changed_identity_fails_visibly(self):
        base = self.provider()
        for body in ("[]", "not json", json.dumps({"name": "another/plugin"})):
            with self.subTest(body=body):
                self.write(".tessl-plugin/plugin.json", body)
                self.save()
                with self.assertRaises(MODULE.ContextError):
                    MODULE.context("jbaruch/example", base)

    def test_unsafe_missing_duplicate_and_non_file_rules_are_refused(self):
        base = self.provider()
        self.write("rules/one.md", "regular rule for the duplicate-path case\n")
        for rules in (["../private.md"], ["rules/missing.md"], ["rules/one.md", "rules/one.md"], [False]):
            with self.subTest(rules=rules):
                self.declare(rules=rules)
                self.save()
                with self.assertRaises(MODULE.ContextError):
                    MODULE.context("jbaruch/example", base)
        self.declare(rules=["rules/link.md"])
        self.write("private.txt", "do not read or execute\n")
        (self.root / "rules").mkdir(exist_ok=True)
        (self.root / "rules/link.md").symlink_to("../private.txt")
        self.save()
        with self.assertRaisesRegex(MODULE.ContextError, "regular files"):
            MODULE.context("jbaruch/example", base)

    def test_missing_git_reference_is_an_error_instead_of_consumer_mode(self):
        self.provider()
        with self.assertRaises(MODULE.ContextError):
            MODULE.context("jbaruch/example", "missing-base")

    def test_cli_ignores_consumer_python_modules(self):
        base = self.provider()
        self.write("json.py", "raise RuntimeError('consumer module executed')\n")
        result = subprocess.run([sys.executable, "-I", str(SOURCE), "--repo", "jbaruch/example", "--base", base],
                                env={**self.environment, "PYTHONPATH": str(self.root)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["eligible"])


if __name__ == "__main__":
    unittest.main()
