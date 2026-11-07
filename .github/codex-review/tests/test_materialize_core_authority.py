"""Core stays isolated and usable across ordinary provider releases."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("core_authority", ROOT / "materialize-core-authority.py")
assert spec is not None and spec.loader is not None
OWNER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(OWNER)

FAKE_TESSL = r'''
import json,os,sys
from pathlib import Path
root=Path.cwd()
project=json.loads((root/'tessl.json').read_text())
with Path(os.environ['CORE_TEST_LOG']).open('a') as log:
 log.write(json.dumps({'args':sys.argv[1:],'cwd':str(root),'project':project})+'\n')
plugin,version=sys.argv[2].rsplit('@',1)
assert project=={'mode':'managed','dependencies':{plugin:{'version':version}}},'Warning flag escaped its isolated workspace'
if os.environ.get('CORE_TEST_MODE')=='fail': raise SystemExit(1)
if os.environ.get('CORE_TEST_MODE')=='wrong-version': version='0.0.1'
project['dependencies']={plugin:{'version':version}}
if os.environ.get('CORE_TEST_MODE')=='extra-dependency':
 project['dependencies']['another/provider']={'version':'1.0.0'}
(root/'tessl.json').write_text(json.dumps(project))
provider=root/'.tessl/plugins/jbaruch/nanoclaw-core'
(provider/'.tessl-plugin').mkdir(parents=True)
name='another/provider' if os.environ.get('CORE_TEST_MODE')=='wrong-provider' else plugin
(provider/'.tessl-plugin/plugin.json').write_text(json.dumps({'name':name,'version':version}))
(provider/'rules').mkdir()
(provider/'rules/core-behavior.md').write_text('Current provider authority\n')
if os.environ.get('CORE_TEST_MODE')=='symlink-file':
 (provider/'rules/escaped.md').symlink_to(os.environ['CORE_TEST_LOG'])
'''


class MaterializationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.policy = self.root / "review context"
        self.policy.mkdir(mode=0o700)
        self.log = self.root / "calls.jsonl"
        binaries = self.root / "bin"
        binaries.mkdir()
        command = binaries / "tessl"
        command.write_text("#!" + sys.executable + "\n" + FAKE_TESSL)
        command.chmod(0o700)
        self.environment = patch.dict(os.environ, {
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "CORE_TEST_LOG": str(self.log),
            "CORE_TEST_MODE": "ok",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_installs_only_reviewed_core_and_preserves_surrounding_dependencies(self):
        project = self.policy / "tessl.json"
        project.write_text(json.dumps({"dependencies": {"another/retained": {"version": "latest"}}}))
        before = project.read_bytes()
        sibling = self.policy / ".tessl/plugins/another/retained/rules.md"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("Retained authority")
        proof = OWNER.materialize(self.policy)
        self.assertEqual(proof, {
            "schema_version": 2, "status": "materialized", "plugin": "jbaruch/nanoclaw-core",
            "version": "0.1.160",
        })
        self.assertEqual(project.read_bytes(), before)
        self.assertEqual(sibling.read_text(), "Retained authority")
        call, = self.calls()
        self.assertEqual(call["args"], [
            "install", "jbaruch/nanoclaw-core@0.1.160", "--yes", "--accept-warnings",
            "--strict", "--agent", "codex",
        ])
        self.assertNotEqual(Path(call["cwd"]), self.policy)
        self.assertEqual(Path(call["cwd"]).parent, self.policy)
        authority = self.policy / ".tessl/plugins/jbaruch/nanoclaw-core/rules/core-behavior.md"
        self.assertEqual(authority.read_text(), "Current provider authority\n")
        self.assertEqual(list(self.policy.glob("core-authority-*")), [])

    def test_pin_renewal_needs_no_copied_metadata_or_security_report(self):
        first = OWNER.materialize(self.policy)
        next_context = self.root / "next context"
        next_context.mkdir(mode=0o700)
        with patch.object(OWNER, "VERSION", "0.1.161"):
            second = OWNER.materialize(next_context)
        self.assertEqual((first["version"], second["version"]), ("0.1.160", "0.1.161"))
        self.assertEqual(len(self.calls()), 2)

    def test_repeated_call_reuses_this_review_context_without_another_install(self):
        first = OWNER.materialize(self.policy)
        self.assertEqual(OWNER.materialize(self.policy), first)
        self.assertEqual(len(self.calls()), 1)

    def test_nonprivate_context_refuses_before_install(self):
        self.policy.chmod(0o755)
        with self.assertRaisesRegex(OWNER.MaterializationError, "not_private"):
            OWNER.materialize(self.policy)
        self.assertEqual(self.calls(), [])

    def test_cli_refusal_is_nonzero_structured_and_does_not_start_installer(self):
        self.policy.chmod(0o755)
        result = subprocess.run(
            [sys.executable, str(ROOT / "materialize-core-authority.py"), str(self.policy)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout), {
            "schema_version": 2, "status": "refused", "reason": "context_directory_not_private",
        })
        self.assertIn("restore a private fresh context", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_install_failures_and_wrong_provider_do_not_expose_authority(self):
        for mode in ["fail", "extra-dependency", "wrong-provider", "wrong-version", "symlink-file"]:
            with self.subTest(mode=mode):
                os.environ["CORE_TEST_MODE"] = mode
                with self.assertRaises(OWNER.MaterializationError):
                    OWNER.materialize(self.policy)
                self.assertFalse((self.policy / ".tessl/plugins/jbaruch/nanoclaw-core").exists())
                self.assertEqual(list(self.policy.glob("core-authority-*")), [])


if __name__ == "__main__":
    unittest.main()
