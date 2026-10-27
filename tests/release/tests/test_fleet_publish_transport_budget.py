"""Real bounded gh subprocess transport over immutable source/run fixtures."""

from __future__ import annotations

import base64
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import test_fleet_publish_resolver as fixture

sys.path.insert(0, str(fixture.SOURCE))
import fleet_publish_confirm as confirmation  # noqa: E402 - shipped reader owner.

FAKE_GH = """#!/usr/bin/env python3
import base64,json,os,sys
with open(os.environ['FIXTURE_GH_ROUTES']) as stream: routes=json.load(stream)
route=sys.argv[-1]
with open(os.environ['FIXTURE_GH_CALLS'],'a') as stream: stream.write(route+'\\n')
row=routes.get(route)
status=200 if row is not None else 404
raw=base64.b64decode(row) if row is not None else b'{"message":"Not Found"}'
if '--include' in sys.argv:
 sys.stdout.buffer.write(('HTTP/2.0 '+str(status)+' Native fixture\\nContent-Type: application/json\\r\\n\\r\\n').encode())
sys.stdout.buffer.write(raw)
sys.exit(0 if status==200 else 1)
"""


def graph(commits):
    native = fixture.Fixture()
    definitions = native.files[fixture.CENTRAL, native.workflow]
    finalizer = native.commit(
        fixture.CENTRAL,
        [native.workflow],
        {
            **definitions,
            "README.md": b"reviewed finalizer source epoch",
        },
    )
    current = native.commit(
        fixture.CENTRAL,
        [finalizer],
        {
            **definitions,
            "README.md": b"later central default source epoch",
        },
    )
    native.central_tip = current
    native.routes[f"repos/{fixture.CENTRAL}/git/ref/heads/main"]["object"]["sha"] = (
        current
    )
    for sha in (native.workflow, finalizer, current):
        native.routes[f"repos/{fixture.CENTRAL}/compare/{sha}...{current}"] = {
            "status": "identical" if sha == current else "ahead",
            "base_commit": {"sha": sha},
            "merge_base_commit": {"sha": sha},
        }
    envelope = fixture.archive_owner.make_intent(native.intent, native.claim)
    terminal = fixture.archive_owner.make_terminal(
        envelope,
        native.records[fixture.RUN, "receipt"],
        native.routes[f"repos/{fixture.CENTRAL}/actions/runs/{fixture.RUN}"],
        native.routes[f"repos/{fixture.CENTRAL}/actions/runs/{fixture.RUN}/attempts/1"],
        native.routes[
            f"repos/{fixture.CENTRAL}/actions/runs/{fixture.RUN}/attempts/1/jobs?per_page=100&page=1"
        ]["jobs"],
        finalizer={
            "workflow_path": fixture.archive_owner.FINALIZER_WORKFLOW,
            "workflow_sha": finalizer,
            "run_id": "999999999",
            "run_attempt": 1,
        },
    )
    native.put_archive(
        fixture.archive_owner.ref_name("terminal", run_id=fixture.RUN), terminal
    )
    for number in range(commits):
        next_tip = native.commit(
            fixture.REPO,
            [native.tip],
            {
                **native.files[fixture.REPO, native.tip],
                f"later-{number}.py": f"later source {number}".encode(),
            },
        )
        native.default(next_tip)
    return native


def recovery_graph(commits):
    native = graph(commits)
    original_finalizer = native.routes[
        f"repos/{fixture.CENTRAL}/git/commits/{native.central_tip}"
    ]["parents"][0]["sha"]
    original = native.records[fixture.RUN, "receipt"]
    original.update(state="published", moderation="pending")
    for suffix in ("", "/attempts/1"):
        native.routes[f"repos/{fixture.CENTRAL}/actions/runs/{fixture.RUN}{suffix}"][
            "conclusion"
        ] = "failure"
    native.routes[
        f"repos/{fixture.CENTRAL}/actions/runs/{fixture.RUN}/attempts/1/jobs?per_page=100&page=1"
    ]["jobs"][0]["conclusion"] = "failure"

    def terminal(run, intent, claim, finalizer):
        native.put_archive(
            fixture.archive_owner.ref_name("terminal", run_id=run),
            fixture.archive_owner.make_terminal(
                fixture.archive_owner.make_intent(intent, claim),
                native.records[run, "receipt"],
                native.routes[f"repos/{fixture.CENTRAL}/actions/runs/{run}"],
                native.routes[f"repos/{fixture.CENTRAL}/actions/runs/{run}/attempts/1"],
                native.routes[
                    f"repos/{fixture.CENTRAL}/actions/runs/{run}/attempts/1/jobs?per_page=100&page=1"
                ]["jobs"],
                finalizer={
                    "workflow_path": fixture.archive_owner.FINALIZER_WORKFLOW,
                    "workflow_sha": finalizer,
                    "run_id": "999999999",
                    "run_attempt": 1,
                },
            ),
        )

    terminal(fixture.RUN, native.intent, native.claim, original_finalizer)
    original_workflow = native.workflow
    recovery_workflow = native.central_tip
    definitions = native.files[fixture.CENTRAL, original_workflow]
    recovery_finalizer = native.commit(
        fixture.CENTRAL,
        [recovery_workflow],
        {**definitions, "README.md": b"recovery finalizer epoch"},
    )
    current = native.commit(
        fixture.CENTRAL,
        [recovery_finalizer],
        {**definitions, "README.md": b"latest default epoch"},
    )
    native.central_tip = current
    native.routes[f"repos/{fixture.CENTRAL}/git/ref/heads/main"]["object"]["sha"] = (
        current
    )
    for sha in (
        original_workflow,
        original_finalizer,
        recovery_workflow,
        recovery_finalizer,
        current,
    ):
        native.routes[f"repos/{fixture.CENTRAL}/compare/{sha}...{current}"] = {
            "status": "identical" if sha == current else "ahead",
            "base_commit": {"sha": sha},
            "merge_base_commit": {"sha": sha},
        }
    native.workflow = recovery_workflow
    recovery = native.new_intent(fixture.RECOVERY, origin=fixture.RUN)
    claim = native.claim_commit(native.tip, recovery)
    native.default(claim)
    native.add_run(fixture.RECOVERY, recovery)
    native.records[fixture.RECOVERY, "receipt"] = native.receipt(recovery, claim)
    native.archive_run(fixture.RECOVERY, claim)
    terminal(fixture.RECOVERY, recovery, claim, recovery_finalizer)
    return native


class NativeTransport(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fleet-real-budget-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o700)
        self.gh = self.bin / "gh"
        self.gh.write_text(FAKE_GH)
        self.gh.chmod(0o700)
        self.routes = self.root / "routes.json"
        self.calls = self.root / "calls"
        self.environment = patch.dict(
            os.environ,
            {
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "FIXTURE_GH_ROUTES": str(self.routes),
                "FIXTURE_GH_CALLS": str(self.calls),
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def bind(self, native):
        responses = {
            route: json.dumps(value).encode() for route, value in native.routes.items()
        }
        artifact_id = 100
        for run in {run for run, _kind in native.records}:
            rows = []
            for kind in ("intent", "receipt"):
                value = native.records.get((run, kind))
                if value is None:
                    continue
                target = io.BytesIO()
                with zipfile.ZipFile(target, "w") as archive:
                    member = zipfile.ZipInfo(kind + ".json")
                    member.create_system = 3
                    member.external_attr = (stat.S_IFREG | 0o644) << 16
                    archive.writestr(member, fixture.encoded(value))
                data = target.getvalue()
                artifact_id += 1
                rows.append(
                    {
                        "id": artifact_id,
                        "name": "fleet-publish-" + kind + "-v1",
                        "expired": False,
                        "size_in_bytes": len(data),
                        "workflow_run": {"id": int(run)},
                    }
                )
                responses[
                    f"repos/{fixture.CENTRAL}/actions/artifacts/{artifact_id}/zip"
                ] = data
            responses[
                f"repos/{fixture.CENTRAL}/actions/runs/{run}/artifacts?per_page=100&page=1"
            ] = fixture.encoded({"total_count": len(rows), "artifacts": rows})
        self.routes.write_text(
            json.dumps(
                {
                    route: base64.b64encode(raw).decode()
                    for route, raw in responses.items()
                }
            )
        )
        return patch.object(fixture.producer, "TRUSTED_PRODUCERS", (native.pinset,))

    def test_measure_real_resolve_transport_with_three_protocol_epochs(self):
        for commits in (0, 4, 8, 124):
            with self.subTest(commits=commits):
                native = graph(commits)
                client = fixture.github_owner.ResolutionGitHubClient()
                with self.bind(native):
                    try:
                        result = fixture.resolver.resolve(
                            client, fixture.REPO, native.source
                        )
                        self.assertEqual(result, native.records[fixture.RUN, "receipt"])
                        outcome = "confirmed"
                    except fixture.resolver.CentralPublishHold as error:
                        outcome = error.kind
                print(
                    f"REAL_TRANSPORT commits={commits} commands={client._commands} outcome={outcome}"
                )
                self.assertEqual(outcome, "confirmed")
                self.assertLessEqual(client._commands, 512)

    def test_base_budget_stays128_and_reader_exhaustion_is_a_refusal(self):
        native = graph(4)
        client = fixture.github_owner.GitHubClient()
        with self.bind(native):
            with self.assertRaises(fixture.resolver.CentralPublishHold):
                fixture.resolver.resolve(client, fixture.REPO, native.source)
        self.assertEqual(client._commands, 128)
        reader = fixture.github_owner.ResolutionGitHubClient()
        reader._commands = fixture.github_owner.RESOLUTION_MAX_COMMANDS
        before = self.calls.read_bytes()
        with self.assertRaises(fixture.FleetPublishLookupError) as error:
            reader.json(f"repos/{fixture.CENTRAL}")
        self.assertEqual(error.exception.kind, "command_budget")
        self.assertEqual(self.calls.read_bytes(), before)

    def test_near_bound_recovery_with_five_distinct_protocol_epochs(self):
        native = recovery_graph(120)
        client = fixture.github_owner.ResolutionGitHubClient()
        with self.bind(native):
            result = fixture.resolver.resolve(
                client, fixture.REPO, native.source, run_id=fixture.RECOVERY
            )
        self.assertEqual(result, native.records[fixture.RECOVERY, "receipt"])
        self.assertLessEqual(client._commands, 512)
        print(f"REAL_RECOVERY epochs=5 commits=120 commands={client._commands}")

    def test_measure_actual_confirmation_entrypoint_transport(self):
        native = graph(4)
        client = fixture.github_owner.ResolutionGitHubClient()
        helper_dir = self.root / "registry-helpers"
        helper_dir.mkdir()
        for name, value in (
            (
                "verify-publish-landed.sh",
                {
                    "ok": True,
                    "run_conclusion": "success",
                    "pre": "0.1.0",
                    "current": "0.1.1",
                },
            ),
            ("registry-has-version.sh", {"exists": True, "version": "0.1.1"}),
            ("verify-moderation-cleared.sh", {"ok": True, "version": "0.1.1"}),
        ):
            (helper_dir / name).write_text(
                "printf '%s\\n' '" + json.dumps(value) + "'\n"
            )
        output = io.StringIO()
        with (
            self.bind(native),
            patch.object(confirmation, "GitHubClient", return_value=client),
            patch.object(confirmation, "ROOT", helper_dir),
            redirect_stdout(output),
        ):
            try:
                result = confirmation.main(
                    [fixture.REPO, native.source, "0.1.0", fixture.RUN]
                )
                self.assertEqual(result, 0)
                outcome = "confirmed"
            except confirmation.CentralPublishHold as error:
                outcome = error.kind
        print(f"REAL_CONFIRM commands={client._commands} outcome={outcome}")
        self.assertEqual(outcome, "confirmed")
        self.assertTrue(json.loads(output.getvalue())["confirmed"])


if __name__ == "__main__":
    unittest.main()
