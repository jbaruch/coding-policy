"""Actual workflow module/CLI routing with owned fake effects, no GitHub calls."""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/fleet_publish_workflow.py"
RELEASE = ROOT / "skills/release"
ENV = {
    "GITHUB_REPOSITORY": "jbaruch/coding-policy",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": "c" * 40,
    "GITHUB_RUN_ID": "101",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_WORKFLOW_REF": "jbaruch/coding-policy/.github/workflows/fleet-publish.yml@refs/heads/main",
    "FLEET_CONSUMER_REPOSITORY": "jbaruch/nanoclaw-fixture",
    "FLEET_SOURCE_SHA": "a" * 40,
}
STUB = """
import json, os
from pathlib import Path
class PublishRefused(ValueError): pass
def require(value, message):
    if not value: raise PublishRefused(message)
def decode(raw): return json.loads(raw)
class GitHub: pass
def record(work, phase):
    with open(Path(os.environ['RUNNER_TEMP']) / 'calls', 'a') as stream: stream.write(phase+'\\n')
    if os.environ.get('FIXTURE_FAULT') == phase: raise PublishRefused('PRIVATE-token-body')
def prepare(github, repo, sha, work, environment):
    record(work, 'prepare')
    assert repo == os.environ['FLEET_CONSUMER_REPOSITORY']
    assert sha == os.environ['FLEET_SOURCE_SHA']
    assert environment['FLEET_PUBLISH_DATE']
    (work/'package').mkdir(mode=0o700)
    (work/'package/data').write_bytes(b'fixed inert source')
    (work/'intent.json').write_text(json.dumps({'repo':repo,'sha':sha}))
    (work/'selection.json').write_text(json.dumps({'mode':os.environ.get('FIXTURE_MODE','publish')}))
def claim(github,work):
    record(work, 'claim')
    (work/'claim.json').write_text('{"bookkeeping_sha":"native-fixed"}')
def execute(github,work):
    state=json.loads((work/'.phase.json').read_text())
    assert state['stage']=='executing' # real adapter persisted BEFORE entry
    record(work,'execute')
    (work/'receipt.json').write_text('{"result":"complete exact original"}')
    return os.environ.get('FIXTURE_RESULT','true') == 'true'
"""


def load_real() -> Any:
    spec = importlib.util.spec_from_file_location("workflow_adapter_under_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "scripts"), str(RELEASE), *sys.path]):
        spec.loader.exec_module(module)
    return module


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workflow-phase-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        os.chmod(self.root, 0o700)
        self.script = self.root / "fleet_publish_workflow.py"
        shutil.copyfile(SOURCE, self.script)
        (self.root / "fleet_publish.py").write_text(STUB)
        shutil.copyfile(
            RELEASE / "fleet_publish_record.py", self.root / "fleet_publish_record.py"
        )
        self.output = self.root / "output"
        self.output.touch(mode=0o600)
        self.env = {
            "PATH": os.environ["PATH"],
            **ENV,
            "RUNNER_TEMP": str(self.root),
            "GITHUB_OUTPUT": str(self.output),
        }
        self.work = self.root / "fleet-publish"

    def run_phase(self, phase, **changes):
        return subprocess.run(
            [sys.executable, str(self.script), phase],
            env={**self.env, **changes},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def ok(self, phase, **changes):
        result = self.run_phase(phase, **changes)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")
        return result

    def calls(self):
        target = self.root / "calls"
        return target.read_text().splitlines() if target.exists() else []

    def advance(self):
        self.ok("prepare")
        self.ok("claim")

    def test_actual_validate_cli_binds_repository_output_and_redacts_invalid_coordinates(
        self,
    ):
        script = self.root / "fleet_publish_validate.py"
        shutil.copyfile(ROOT / "scripts/fleet_publish_validate.py", script)

        def validate(environment):
            return subprocess.run(
                [sys.executable, str(script)],
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                check=False,
            )

        result = validate(self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(self.output.read_text(), "repository_name=nanoclaw-fixture\n")
        before = self.output.read_bytes()
        for changes in (
            {"FLEET_CONSUMER_REPOSITORY": "other/PRIVATE"},
            {"FLEET_SOURCE_SHA": "PRIVATE"},
        ):
            result = validate({**self.env, **changes})
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertNotIn(b"PRIVATE", result.stderr)
            self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual(self.calls(), [])

    def test_actual_module_import_is_inert_and_routes_real_functions(self):
        module = load_real()
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch.object(sys, "argv", [str(SOURCE), "prepare"]),
            patch.object(module, "GitHub"),
            patch.object(module, "datetime") as clock,
            patch.object(module, "prepare") as prepare,
        ):

            def output(_github, repo, sha, work, environment):
                self.assertEqual(
                    (repo, sha),
                    (ENV["FLEET_CONSUMER_REPOSITORY"], ENV["FLEET_SOURCE_SHA"]),
                )
                self.assertEqual(work, self.work)
                self.assertEqual(environment["FLEET_PUBLISH_DATE"], "2024-01-02")
                (work / "selection.json").write_text('{"mode":"already_confirmed"}')

            clock.now.return_value = datetime(2024, 1, 2, 23, 4, 5, tzinfo=UTC)
            prepare.side_effect = output
            self.assertEqual(module.main(), 0)
            self.assertEqual(module.main(), 0)
            prepare.assert_called_once()
        self.assertEqual(
            self.output.read_text(),
            "selected=false\nreview_required=false\nselected=false\nreview_required=false\n",
        )

    def test_all_three_real_cli_routes_resume_without_double_effect(self):
        self.work.mkdir(mode=0o700)  # empty preexisting owned work is valid
        self.advance()
        self.ok("execute")
        original = (self.work / "receipt.json").read_bytes()
        for phase in ("prepare", "claim", "execute", "execute"):
            self.ok(phase)
        self.assertEqual(self.calls(), ["prepare", "claim", "execute"])
        self.assertEqual((self.work / "receipt.json").read_bytes(), original)
        self.assertEqual(
            self.output.read_text(),
            "selected=true\nreview_required=true\nselected=true\nreview_required=true\n",
        )

    def test_known_failed_effect_returns_original_failed_result_without_replay(self):
        self.advance()
        result = self.run_phase("execute", FIXTURE_RESULT="false")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.run_phase("execute").returncode, 1)
        self.assertEqual(self.calls(), ["prepare", "claim", "execute"])

    def test_uncertain_started_effect_never_replays_on_restart(self):
        self.advance()
        first = self.run_phase("execute", FIXTURE_FAULT="execute")
        self.assertEqual(first.returncode, 1)
        self.assertNotIn(b"PRIVATE", first.stderr)
        state = (self.work / ".phase.json").read_bytes()
        for phase in ("execute", "claim", "prepare"):
            self.assertEqual(self.run_phase(phase).returncode, 1)
        self.assertEqual(self.calls(), ["prepare", "claim", "execute"])
        self.assertEqual((self.work / ".phase.json").read_bytes(), state)

    def test_partial_prepare_and_uncertain_native_claim_are_not_file_exists_errors(
        self,
    ):
        for phase in ("prepare", "claim"):
            with self.subTest(phase=phase):
                if phase == "claim":
                    self.ok("prepare")
                self.assertEqual(
                    self.run_phase(phase, FIXTURE_FAULT=phase).returncode, 1
                )
                count = len(self.calls())
                self.assertEqual(self.run_phase(phase).returncode, 1)
                self.assertEqual(len(self.calls()), count)
                shutil.rmtree(self.work)
                (self.root / "calls").unlink()

    def test_already_confirmed_noop_never_claims_or_executes(self):
        self.ok("prepare", FIXTURE_MODE="already_confirmed")
        self.ok("prepare")
        self.assertEqual(
            self.output.read_text(),
            "selected=false\nreview_required=false\nselected=false\nreview_required=false\n",
        )
        for phase in ("claim", "execute"):
            self.assertEqual(self.run_phase(phase).returncode, 1)
        self.assertEqual(self.calls(), ["prepare"])

    def test_changed_payload_new_attempt_source_or_workflow_holds_unchanged(self):
        self.advance()
        baseline = (self.work / ".phase.json").read_bytes()
        for key, value in (
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_RUN_ID", "102"),
            ("GITHUB_SHA", "b" * 40),
            ("FLEET_SOURCE_SHA", "d" * 40),
            ("GITHUB_WORKFLOW_REF", "PRIVATE"),
        ):
            result = self.run_phase("execute", **{key: value})
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(b"PRIVATE", result.stderr)
        (self.work / "package/data").write_bytes(b"changed")
        self.assertEqual(self.run_phase("execute").returncode, 1)
        self.assertEqual((self.work / ".phase.json").read_bytes(), baseline)
        self.assertNotIn("execute", self.calls())

    def test_concurrent_claim_is_refused_then_completed_claim_reuses_same_attempt(self):
        self.ok("prepare")
        with (self.work / ".phase.lock").open("r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_phase("claim").returncode, 1)
            self.assertEqual(self.calls(), ["prepare"])
        self.ok("claim")
        self.assertEqual(self.calls(), ["prepare", "claim"])

    def test_later_phases_use_persisted_source_with_only_native_runner_context(self):
        self.ok("prepare")
        native = {
            key: value
            for key, value in self.env.items()
            if not key.startswith("FLEET_")
        }
        for phase in ("claim", "execute", "execute"):
            result = subprocess.run(
                [sys.executable, str(self.script), phase],
                env=native,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["prepare", "claim", "execute"])
        (self.work / "receipt.json").write_text("changed terminal evidence")
        self.assertEqual(self.run_phase("execute").returncode, 1)
        self.assertEqual(self.calls().count("execute"), 1)

    def test_unknown_version_linked_or_nonprivate_state_refuses_without_effect(self):
        self.ok("prepare")
        state = self.work / ".phase.json"
        raw = state.read_bytes()
        changed = json.loads(raw)
        changed["schema_version"] = 2
        state.write_text(json.dumps(changed))
        self.assertEqual(self.run_phase("claim").returncode, 1)
        state.write_bytes(raw)
        state.chmod(0o644)
        self.assertEqual(self.run_phase("claim").returncode, 1)
        state.chmod(0o600)
        elsewhere = self.root / "linked-state"
        state.rename(elsewhere)
        state.symlink_to(elsewhere)
        self.assertEqual(self.run_phase("claim").returncode, 1)
        self.assertEqual(self.calls(), ["prepare"])

    def test_invalid_routes_runner_and_output_boundaries_emit_only_fixed_failure(self):
        for phase in ("PRIVATE-flag", "claim", "execute"):
            result = self.run_phase(phase)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertNotIn(b"PRIVATE", result.stderr)
        self.output.unlink()
        self.output.symlink_to(self.root / "elsewhere")
        result = self.run_phase("prepare")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), ["prepare"])
        self.output.unlink()
        self.output.touch(mode=0o600)
        self.ok("prepare")  # completed prepare can repair output without repeating
        self.assertEqual(self.calls(), ["prepare"])
        self.assertEqual(
            self.run_phase("prepare", RUNNER_TEMP="relative-PRIVATE").returncode, 1
        )

    def test_review_output_is_selection_bound_for_all_prepare_reentries(self):
        for mode, selected, review in (
            ("publish", True, True),
            ("moderation_only", True, False),
            ("already_confirmed", False, False),
        ):
            with self.subTest(mode=mode):
                self.ok("prepare", FIXTURE_MODE=mode)
                self.ok("prepare")
                expected = (
                    f"selected={str(selected).lower()}\n"
                    f"review_required={str(review).lower()}\n"
                )
                self.assertEqual(self.output.read_text(), expected * 2)
                self.assertEqual(self.calls(), ["prepare"])
                shutil.rmtree(self.work)
                (self.root / "calls").unlink()
                self.output.write_text("")

    def test_changed_selection_cannot_enable_skip_or_rewrite_review_output(self):
        self.ok("prepare")
        output = self.output.read_bytes()
        (self.work / "selection.json").write_text('{"mode":"moderation_only"}')
        self.assertEqual(self.run_phase("prepare").returncode, 1)
        self.assertEqual(self.output.read_bytes(), output)
        self.assertEqual(self.run_phase("claim").returncode, 1)
        self.assertEqual(self.calls(), ["prepare"])

    def test_canonical_composite_is_preclaim_and_receives_only_projected_skills(self):
        workflow = (ROOT / ".github/workflows/fleet-publish.yml").read_text()
        begin = workflow.index("      - name: Review projected skills")
        end = workflow.index("      - name: Claim source atomically")
        self.assertLess(workflow.index("id: prepare"), begin)
        self.assertLess(begin, end)
        review = workflow[begin:end]
        for required in (
            "if: steps.prepare.outputs.review_required == 'true'",
            "uses: ./.github/actions/skill-review",
            "skills-dir: ${{ runner.temp }}/fleet-publish/package/skills",
            "threshold: '85'",
            "base-ref: ''",
            "workspace: jbaruch",
            "credit-outage: fail",
            "GH_TOKEN: ''",
            "GITHUB_TOKEN: ''",
            "FLEET_CENTRAL_TOKEN: ''",
        ):
            self.assertIn(required, review)
        self.assertNotIn(
            "review-skills.sh", (ROOT / "scripts/fleet_publish.py").read_text()
        )
        self.assertNotIn(
            "always()",
            workflow[end : workflow.index("      - name: Persist immutable")],
        )

    def test_actual_composite_body_reviews_absolute_projected_paths_and_blocks_failure(
        self,
    ):
        action_dir = ROOT / ".github/actions/skill-review"
        action = (action_dir / "action.yml").read_text()
        lines = [
            line.strip()[5:]
            for line in action.splitlines()
            if line.strip().startswith("run: ")
        ]
        self.assertEqual(lines, ['bash "$GITHUB_ACTION_PATH/review-skills.sh"'])
        for mapping in (
            "EVENT_NAME: ${{ github.event_name }}",
            "RUNNER_TEMP: ${{ runner.temp }}",
            "BASE_OVERRIDE: ${{ inputs.base-ref }}",
            "THRESHOLD: ${{ inputs.threshold }}",
            "SKILLS_DIR: ${{ inputs.skills-dir }}",
            "WORKSPACE: ${{ inputs.workspace }}",
            "CREDIT_OUTAGE: ${{ inputs.credit-outage }}",
        ):
            self.assertIn(mapping, action)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        log = self.root / "review-args"
        cli = bin_dir / "tessl"
        cli.write_text(
            "#!/usr/bin/env python3\nimport json,os,sys\n"
            'with open(os.environ["FIXTURE_REVIEW_ARGS"],"a") as f: '
            'f.write(json.dumps(sys.argv[1:])+"\\n")\n'
            'sys.exit(int(os.environ["FIXTURE_REVIEW_EXIT"]))\n'
        )
        cli.chmod(0o700)
        native_temp = self.root / "native temp"
        projected = native_temp / "fleet-publish/package/skills"
        projected.mkdir(parents=True)
        (projected / "one").mkdir()
        (projected / "one/SKILL.md").write_text("# Projected fixture\n")
        (projected / "two").mkdir()
        (projected / "two/SKILL.md").write_text("# Another projected fixture\n")
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GITHUB_ACTION_PATH": str(action_dir),
            "RUNNER_TEMP": str(native_temp),
            "EVENT_NAME": "workflow_dispatch",
            "WORKSPACE": "jbaruch",
            "THRESHOLD": "85",
            "CREDIT_OUTAGE": "fail",
            "BASE_OVERRIDE": "",
            "SKILLS_DIR": str(projected),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": os.devnull,
            "FIXTURE_REVIEW_ARGS": str(log),
            "FIXTURE_REVIEW_EXIT": "0",
        }
        result = subprocess.run(
            ["bash", "-c", lines[0]],
            cwd=self.root,
            env=env,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [json.loads(line) for line in log.read_text().splitlines()],
            [
                [
                    "review",
                    "run",
                    "--workspace",
                    "jbaruch",
                    "--threshold",
                    "85",
                    str(projected / name / "SKILL.md"),
                ]
                for name in ("one", "two")
            ],
        )
        result = subprocess.run(
            ["bash", "-c", lines[0]],
            cwd=self.root,
            env={**env, "FIXTURE_REVIEW_EXIT": "7"},
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 7)
        log.unlink()
        shutil.rmtree(projected)
        projected.mkdir()
        result = subprocess.run(
            ["bash", "-c", lines[0]],
            cwd=self.root,
            env=env,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(log.exists())

        for changes in (
            {"EVENT_NAME": "push"},
            {"BASE_OVERRIDE": "a" * 40},
            {"RUNNER_TEMP": "relative"},
            {"RUNNER_TEMP": "/"},
            {"RUNNER_TEMP": str(native_temp) + "/../elsewhere"},
            {"SKILLS_DIR": str(self.root)},
        ):
            with self.subTest(rejected_mode=changes):
                result = subprocess.run(
                    ["bash", "-c", lines[0]],
                    cwd=self.root,
                    env={**env, **changes},
                    capture_output=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2)
                self.assertFalse(log.exists())
        actual = projected.with_name("actual-skills")
        projected.rename(actual)
        projected.symlink_to(actual, target_is_directory=True)
        result = subprocess.run(
            ["bash", "-c", lines[0]],
            cwd=self.root,
            env=env,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(log.exists())
        projected.unlink()
        actual.rename(projected)
        package = projected.parent
        actual_package = package.with_name("actual-package")
        package.rename(actual_package)
        package.symlink_to(actual_package, target_is_directory=True)
        result = subprocess.run(
            ["bash", "-c", lines[0]],
            cwd=self.root,
            env=env,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
