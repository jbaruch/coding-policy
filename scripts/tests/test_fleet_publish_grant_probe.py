"""Real CLI and fake-gh transport tests; no credentials or GitHub access."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/fleet_publish_grant_probe.py"
TARGET = "jbaruch/nanoclaw-construction"
SHA = "a" * 40
ENV = {
    "GITHUB_REPOSITORY": "jbaruch/coding-policy",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_WORKFLOW_REF": "jbaruch/coding-policy/.github/workflows/fleet-publish-grant-probe.yml@refs/heads/main",
    "GITHUB_RUN_ID": "101",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_SHA": "c" * 40,
}


class ProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="grant-probe-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.calls = self.root / "calls"
        self.fixture = self.root / "fixture.json"
        self.responses = {
            f"repos/{TARGET}": {
                "full_name": TARGET,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
            },
            f"repos/{TARGET}/branches/main": {"name": "main", "commit": {"sha": SHA}},
            f"repos/{TARGET}/pulls?state=all&per_page=1&page=1": [],
            f"repos/{TARGET}/commits/{SHA}/check-runs?per_page=1&page=1": {
                "total_count": 4,
                "check_runs": [{"name": "PRIVATE-provider-body"}],
            },
            f"repos/{TARGET}/commits/{SHA}/status?per_page=1&page=1": {
                "total_count": 0,
                "statuses": [],
            },
        }
        gh = self.root / "gh"
        gh.write_text(
            f"#!{sys.executable}\n"
            + """
import json,os,sys
from pathlib import Path
with open(os.environ['PROBE_CALLS'],'a') as out: out.write(json.dumps(sys.argv[1:])+'\\n')
assert sys.argv[1:6] == ['api','--hostname','github.com','--method','GET']
if os.environ.get('PROBE_FAIL'):
    print('PRIVATE-provider-body',file=sys.stderr)
    sys.exit(1)
responses=json.loads(Path(os.environ['PROBE_FIXTURE']).read_text())
value=responses[sys.argv[6]]
if value == 'MALFORMED': print('{PRIVATE-provider-body')
else: print(json.dumps(value))
"""
        )
        gh.chmod(0o700)

    def run_probe(self, phase="verify", **changes):
        self.fixture.write_text(json.dumps(self.responses))
        return subprocess.run(
            [sys.executable, str(SCRIPT), phase],
            env={
                **ENV,
                "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                "PROBE_CALLS": str(self.calls),
                "PROBE_FIXTURE": str(self.fixture),
                **changes,
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def recorded(self):
        return (
            [json.loads(line) for line in self.calls.read_text().splitlines()]
            if self.calls.exists()
            else []
        )

    def refused(self, result):
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"leave publication disabled", result.stderr)
        self.assertNotIn(b"PRIVATE", result.stderr)

    def test_validate_is_inert_before_mint(self):
        result = self.run_probe("validate")
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, b"", b"")
        )
        self.assertEqual(self.recorded(), [])

    def test_real_cli_uses_five_fixed_gets_and_emits_only_redacted_bound_metadata(self):
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(result.stdout)
        self.assertEqual(
            proof,
            {
                "schema_version": 1,
                "workflow_repository": "jbaruch/coding-policy",
                "workflow_path": ".github/workflows/fleet-publish-grant-probe.yml",
                "workflow_sha": "c" * 40,
                "run_id": "101",
                "run_attempt": 1,
                "consumer_repository": TARGET,
                "consumer_ref": "refs/heads/main",
                "consumer_sha": SHA,
                "requested_permissions": {
                    "contents": "write",
                    "pull_requests": "read",
                    "checks": "read",
                    "statuses": "read",
                },
                "permission_evidence": "preceding_successful_native_token_mint",
                "metadata_reads": "pass",
                "metadata_method": "GET",
                "revocation_evidence": "requires_successful_action_post_job",
                "publication_authorized": False,
                "protected_write_tested": False,
                "coverage_complete": False,
            },
        )
        self.assertEqual([call[5] for call in self.recorded()], list(self.responses))
        self.assertNotIn(b"PRIVATE", result.stdout + result.stderr)

    def test_bad_runtime_refuses_before_any_api(self):
        for field, value in (
            ("GITHUB_REPOSITORY", "other/PRIVATE"),
            ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REF", "refs/heads/other"),
            ("GITHUB_WORKFLOW_REF", "PRIVATE"),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_RUN_ID", "../PRIVATE"),
            ("GITHUB_SHA", "PRIVATE"),
        ):
            with self.subTest(field=field):
                self.refused(self.run_probe(**{field: value}))
                self.assertEqual(self.recorded(), [])

    def test_api_failure_or_malformed_json_never_emits_positive_proof(self):
        self.refused(self.run_probe(PROBE_FAIL="1"))
        self.responses[f"repos/{TARGET}"] = "MALFORMED"
        self.refused(self.run_probe())

    def test_foreign_archived_missing_or_wrong_native_metadata_refuses(self):
        original = dict(self.responses)
        cases = [
            (
                f"repos/{TARGET}",
                {
                    "full_name": "other/PRIVATE",
                    "default_branch": "main",
                    "archived": False,
                    "disabled": False,
                },
            ),
            (
                f"repos/{TARGET}",
                {
                    "full_name": TARGET,
                    "default_branch": "main",
                    "archived": True,
                    "disabled": False,
                },
            ),
            (
                f"repos/{TARGET}/branches/main",
                {"name": "main", "commit": {"sha": "PRIVATE"}},
            ),
            (f"repos/{TARGET}/pulls?state=all&per_page=1&page=1", {}),
            (
                f"repos/{TARGET}/commits/{SHA}/check-runs?per_page=1&page=1",
                {"check_runs": []},
            ),
            (
                f"repos/{TARGET}/commits/{SHA}/status?per_page=1&page=1",
                {"total_count": 0, "statuses": [{"state": "PRIVATE"}]},
            ),
        ]
        for route, value in cases:
            with self.subTest(route=route):
                self.responses = {**original, route: value}
                self.refused(self.run_probe())

    def test_unknown_cli_mode_is_not_an_effect_entrypoint(self):
        self.refused(self.run_probe("publish"))
        self.assertEqual(self.recorded(), [])

    def test_workflow_is_manual_only_fixed_scope_with_default_revocation(self):
        workflow = (
            ROOT / ".github/workflows/fleet-publish-grant-probe.yml"
        ).read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("FLEET_PUBLISHER_ENABLED", workflow)
        self.assertNotIn("TESSL_TOKEN", workflow)
        self.assertNotIn("upload-artifact", workflow)
        self.assertNotIn("skip-token-revoke", workflow)
        self.assertIn("repositories: nanoclaw-construction", workflow)
        for value in (
            "contents: write",
            "pull-requests: read",
            "checks: read",
            "statuses: read",
        ):
            self.assertIn("permission-" + value, workflow)
        self.assertLess(
            workflow.index("grant_probe.py validate"),
            workflow.index("actions/create-github-app-token@v3"),
        )
        self.assertLess(
            workflow.index("actions/create-github-app-token@v3"),
            workflow.index("grant_probe.py verify"),
        )
        self.assertNotIn("fleet_publish_workflow.py", workflow)


if __name__ == "__main__":
    unittest.main()
