"""Test authority from committed objects using the built ACR_METADATA_DECODER."""

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

    def assert_authority_refused(self, base, head, diagnostic, **environment):
        result = subprocess.run([sys.executable, "-I", str(SOURCE), "--repo", "jbaruch/example",
                                 "--base", base, "--head", head],
                                env={**self.environment, **environment}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn(diagnostic, result.stderr)

    def assert_base_disabled(self, ref, diagnostic, **environment):
        result = subprocess.run([sys.executable, "-I", str(SOURCE), "--repo", "jbaruch/example",
                                 "--base", ref, "--head", ref],
                                env={**self.environment, **environment}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["eligible"])
        self.assertIn(diagnostic, result.stderr)

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
        self.write("agent-plugin.yaml", """\
schemaVersion: 1
name: jbaruch/example
version: 1.0.0
description: Head-only authority fixture
source:
  repository: https://github.com/jbaruch/example
artifacts:
  rules:
    - id: lookup
      path: rules/lookup.md
      activation: {mode: always}
""")
        self.write("rules/lookup.md", "cannot grant authority\n")
        self.save()
        self.assertFalse(MODULE.context("jbaruch/example", base)["eligible"])

    def test_tessl_to_acr_migration_reads_candidate_rule_blob(self):
        rule = "rules/blog-writer-script-location.md"
        self.declare(name="jbaruch/blog-writer", rules=[rule])
        self.write(rule, "trusted rule\n")
        base = self.save()
        (self.root / ".tessl-plugin/plugin.json").unlink()
        self.write("agent-plugin.yaml", """\
schemaVersion: 1
name: jbaruch/blog-writer
source:
  repository: https://github.com/jbaruch/blog-writer
artifacts:
  rules:
    - id: blog-writer-script-location
      path: rules/blog-writer-script-location.md
      activation:
        mode: paths
        paths:
          - skills/**/*.md
""")
        self.write(rule, "candidate rule\n")
        head = self.save()
        self.write(rule, "uncommitted rule\n")
        self.write("agent-plugin.yaml", "name: another/plugin\n")
        result = MODULE.context("jbaruch/blog-writer", base, head)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["manifest"], ".tessl-plugin/plugin.json")
        self.assertEqual(result["rules"], [rule])
        self.assertEqual(result["rule_blobs"], {rule: self.git("rev-parse", f"{head}:{rule}")})

    def test_acr_source_must_match_the_repository_on_both_refs(self):
        base = self.provider()
        self.write("rules/lookup.md", "committed rule\n")
        for source in ("", "source: {repository: https://github.com/another/plugin}\n"):
            with self.subTest(source=source):
                self.write("agent-plugin.yaml", f"""\
schemaVersion: 1
name: jbaruch/example
version: 1.0.0
description: Source identity fixture
{source}artifacts:
  rules:
    - id: lookup
      path: rules/lookup.md
      activation: {{mode: always}}
""")
                invalid = self.save()
                self.assert_base_disabled(
                    invalid,
                    "set source.repository in agent-plugin.yaml on the base to https://github.com/jbaruch/example",
                )
                self.assert_authority_refused(base, invalid, "Head ACR source.repository")

    def test_acr_uses_decoded_binary_keys_and_values(self):
        rule = "rules/β.md"
        self.write(rule, "committed rule\n")
        self.write("agent-plugin.yaml", """\
schemaVersion: 1
!!binary bmFtZQ==: !!binary amJhcnVjaC9leGFtcGxl
version: 1.0.0
description: Binary key fixture
!!binary c291cmNl:
  <<: {repository: https://github.com/another/plugin}
  !!binary cmVwb3NpdG9yeQ==: https://github.com/jbaruch/example
!!binary YXJ0aWZhY3Rz:
  !!binary cnVsZXM=:
    - id: lookup
      !!binary cGF0aA==: !!binary cnVsZXMvzrIubWQ=
      activation: {mode: always}
""")
        revision = self.save()
        result = MODULE.context("jbaruch/example", revision, revision)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["rule_blobs"], {rule: self.git("rev-parse", f"{revision}:{rule}")})

    def test_acr_rejects_duplicate_canonical_keys_before_merging(self):
        base = self.provider()
        self.write("rules/lookup.md", "committed rule\n")
        for duplicate in ("name: another/plugin", "!!binary bmFtZQ==: another/plugin"):
            with self.subTest(duplicate=duplicate):
                self.write("agent-plugin.yaml", f"""\
schemaVersion: 1
version: 1.0.0
<<: {{description: Merged description}}
{duplicate}
name: jbaruch/example
source: {{repository: https://github.com/jbaruch/example}}
artifacts:
  rules:
    - id: lookup
      path: rules/lookup.md
      activation: {{mode: always}}
""")
                invalid = self.save()
                self.assertFalse(MODULE.context("jbaruch/example", invalid, base)["eligible"])
                self.assert_authority_refused(base, invalid, "Plugin manifest agent-plugin.yaml is invalid")

    def test_acr_resolves_each_distinct_explicit_rule_path(self):
        base = self.provider()
        self.write("rules/LOOKUP.MD", "uppercase rule\n")
        self.write("rules/notice", "extensionless rule\n")
        self.write("agent-plugin.yaml", """\
schemaVersion: 1
name: jbaruch/example
version: 1.0.0
description: Repeated rule path fixture
source: {repository: https://github.com/jbaruch/example}
artifacts:
  rules:
    - id: lookup
      path: rules/LOOKUP.MD
      activation: {mode: always}
    - id: lookup-for-skills
      path: rules/LOOKUP.MD
      activation: {mode: paths, paths: [skills/*.md]}
    - id: notice
      path: rules/notice
      activation: {mode: always}
""")
        head = self.save()
        result = MODULE.context("jbaruch/example", base, head)
        self.assertEqual(result["rules"], ["rules/LOOKUP.MD", "rules/notice"])
        self.assertEqual(result["rule_blobs"], {
            path: self.git("rev-parse", f"{head}:{path}") for path in ("rules/LOOKUP.MD", "rules/notice")
        })

    def test_acr_rule_lookup_preserves_git_path_line_endings(self):
        base = self.provider()
        rule = "rules/line\nbreak.md"
        self.write(rule, "declared rule\n")
        self.write("rules/line\rbreak.md", "different rule\n")
        self.write("agent-plugin.yaml", """\
schemaVersion: 1
name: jbaruch/example
version: 1.0.0
description: Rule path line endings fixture
source: {repository: https://github.com/jbaruch/example}
artifacts:
  rules:
    - id: lookup
      path: "rules/line\\nbreak.md"
      activation: {mode: always}
""")
        head = self.save()
        result = MODULE.context("jbaruch/example", base, head)
        self.assertEqual(result["rule_blobs"], {rule: self.git("rev-parse", f"{head}:{rule}")})

    def test_acr_rule_lookup_preserves_unquoted_numeric_path(self):
        base = self.provider()
        (self.root / ".tessl-plugin/plugin.json").unlink()
        self.write("00123", "declared rule\n")
        self.write("123", "different rule\n")
        self.write("agent-plugin.yaml", """\
schemaVersion: 1
name: jbaruch/example
version: 1.0.0
description: Numeric rule filename fixture
source: {repository: https://github.com/jbaruch/example}
artifacts:
  rules:
    - id: lookup
      path: 00123
      activation: {mode: always}
""")
        head = self.save()
        result = MODULE.context("jbaruch/example", base, head)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["rules"], ["00123"])
        self.assertEqual(result["rule_blobs"], {"00123": self.git("rev-parse", f"{head}:00123")})

    def test_acr_validates_explicit_path_tags_before_preserving_spelling(self):
        base = self.provider()
        manifest = """\
schemaVersion: 1
name: jbaruch/example
source: {repository: https://github.com/jbaruch/example}
artifacts:
  rules:
    - path: SCALAR
"""
        for scalar, path in (("!!int 00123", "00123"), ("08", "08"), ("09", "09")):
            with self.subTest(scalar=scalar):
                self.write(path, "declared rule\n")
                self.write("agent-plugin.yaml", manifest.replace("SCALAR", scalar))
                valid = self.save()
                self.write(path, "uncommitted replacement\n")
                result = MODULE.context("jbaruch/example", base, valid)
                self.assertEqual(result["rule_blobs"], {path: self.git("rev-parse", f"{valid}:{path}")})

        for path in ("08", "09", "00129", "18446744073709551616", "lookup.md"):
            with self.subTest(path=path):
                self.write(path, "existing path cannot rescue an invalid tag\n")
                self.write("agent-plugin.yaml", manifest.replace("SCALAR", f"!!int {path}"))
                invalid = self.save()
                diagnostic = "Plugin manifest agent-plugin.yaml is invalid"
                self.assertFalse(MODULE.context("jbaruch/example", invalid, base)["eligible"])
                self.assert_authority_refused(base, invalid, diagnostic)

    def test_invalid_acr_projection_shapes_are_refused_on_both_refs(self):
        base = self.provider()
        self.write("rules/lookup.md", "committed rule\n")
        manifest = """\
schemaVersion: 1
name: jbaruch/example
source: {repository: https://github.com/jbaruch/example}
"""
        cases = (
            ("artifacts: null\n", "artifacts must be a mapping"),
            ("artifacts: []\n", "artifacts must be a mapping"),
            ("artifacts: {rules: null}\n", "rules as a list"),
            ("artifacts: {rules: {path: rules/lookup.md}}\n", "rules as a list"),
            ("artifacts: {rules: [&empty null, *empty, {path: rules/lookup.md}]}\n", "rules as a list"),
            ("artifacts: {rules: [{path: !!binary /w==}]}\n", "valid UTF-8"),
            ("artifacts: {rules: [{path: rules/lookup.md, !!binary cGF0aA==: rules/lookup.md}]}\n", "rules as a list"),
            ("---\nname: jbaruch/example\n", "exactly one YAML document"),
        )
        for body, diagnostic in cases:
            with self.subTest(body=body):
                self.write("agent-plugin.yaml", manifest + body)
                invalid = self.save()
                self.assertFalse(MODULE.context("jbaruch/example", invalid, base)["eligible"])
                self.assert_authority_refused(base, invalid, diagnostic)
        for version in ("2", "null"):
            with self.subTest(version=version):
                self.write("agent-plugin.yaml", manifest.replace("schemaVersion: 1", f"schemaVersion: {version}"))
                invalid = self.save()
                self.assertFalse(MODULE.context("jbaruch/example", invalid, base)["eligible"])
                self.assert_authority_refused(base, invalid, "declare schemaVersion: 1")

    def test_acr_decoder_failures_do_not_select_authority(self):
        base = self.provider()
        self.write("agent-plugin.yaml", "schemaVersion: 1\nname: jbaruch/example\n")
        head = self.save()

        decoder = self.root / "decoder"
        self.write("decoder", "#!/bin/sh\nprintf 'invalid JSON'\n")
        decoder.chmod(0o755)
        self.assert_authority_refused(base, head, "absolute path", ACR_METADATA_DECODER="./decoder")
        self.assert_authority_refused(base, head, "Cannot run", ACR_METADATA_DECODER=str(self.root / "missing"))
        self.assert_authority_refused(base, head, "invalid JSON", ACR_METADATA_DECODER=str(decoder))
        self.write("decoder", "#!/bin/sh\nprintf '{}'\n")
        self.assert_authority_refused(base, head, "invalid fields", ACR_METADATA_DECODER=str(decoder))

    def test_dual_packages_can_declare_different_rules(self):
        self.declare(rules=["rules/common.md", "rules/tessl.md"])
        self.write("agent-plugin.yaml", """\
schemaVersion: 1
name: jbaruch/example
source: {repository: https://github.com/jbaruch/example}
artifacts:
  rules:
    - path: rules/common.md
    - path: rules/acr.md
""")
        for name in ("common", "tessl", "acr"):
            self.write(f"rules/{name}.md", f"{name} rule\n")
        base = self.save()
        result = MODULE.context("jbaruch/example", base, base)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["rules"], ["rules/acr.md", "rules/common.md", "rules/tessl.md"])

    def test_conflicting_identities_fail_on_either_ref(self):
        base = self.provider()
        self.write("agent-plugin.yaml", "schemaVersion: 1\nname: another/plugin\n")
        conflict = self.save()
        with self.assertRaisesRegex(MODULE.ContextError, "conflicting identities"):
            MODULE.context("jbaruch/example", base, conflict)
        self.assertFalse(MODULE.context("jbaruch/example", conflict, conflict)["eligible"])

    def test_unusable_base_yields_not_eligible(self):
        decoder = os.environ.get("ACR_METADATA_DECODER", "")

        # Case 1 (unrelated-providers): base ACR with invalid schemaVersion → not-eligible, warns stderr.
        self.write("agent-plugin.yaml", "schemaVersion: 2\nname: jbaruch/example\n")
        unrelated = self.save()
        self.assert_base_disabled(unrelated, "declare schemaVersion: 1", ACR_METADATA_DECODER=decoder)

        # Case 2 (no-legacy-name): base plugin.json without a plugin name → not-eligible, warns stderr.
        self.write(".tessl-plugin/plugin.json", '{"rules": []}')
        no_name = self.save()
        self.assert_base_disabled(no_name, "must declare a plugin name", ACR_METADATA_DECODER=decoder)

        # Case 3 (repair-canonical-repository): base ACR with wrong source.repository → not-eligible.
        self.write("agent-plugin.yaml", """\
schemaVersion: 1
name: jbaruch/example
source: {repository: https://github.com/another/repo}
artifacts: {rules: []}
""")
        wrong_repo = self.save()
        self.assertFalse(MODULE.context("jbaruch/example", wrong_repo, wrong_repo)["eligible"])

    def test_base_tool_failures_are_fatal(self):
        # Missing decoder on base with ACR keeps the run fatal (ContextError, not not-eligible).
        self.write("agent-plugin.yaml", "schemaVersion: 1\nname: jbaruch/example\n")
        base = self.save()
        self.assert_authority_refused(base, base, "absolute path", ACR_METADATA_DECODER="./decoder")
        self.assert_authority_refused(base, base, "Cannot run",
                                      ACR_METADATA_DECODER=str(self.root / "missing"))

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
        for body in ("[]", "not json", '{"name":"another/plugin","name":"jbaruch/example"}',
                     json.dumps({"name": "another/plugin"})):
            with self.subTest(body=body):
                self.write(".tessl-plugin/plugin.json", body)
                self.save()
                with self.assertRaises(MODULE.ContextError):
                    MODULE.context("jbaruch/example", base)
        self.declare()
        for body in ("name: [", "name: another/plugin\nname: jbaruch/example\n", "artifacts: {}\n"):
            with self.subTest(body=body):
                self.write("agent-plugin.yaml", body)
                self.save()
                with self.assertRaises(MODULE.ContextError):
                    MODULE.context("jbaruch/example", base)
        (self.root / "agent-plugin.yaml").unlink()
        self.write("agent-plugin.yaml/nested", "a directory cannot declare the plugin identity\n")
        self.save()
        with self.assertRaisesRegex(MODULE.ContextError, "regular files"):
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
