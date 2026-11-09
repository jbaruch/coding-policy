"""Complete owner operation over real bounded gh children and fixture Git data.

No network, registry, native publication or credential is used. Only the two
declared gh read transports are replaced by a finite synthetic HTTP peer.
"""

from __future__ import annotations

import contextlib
import hashlib
import copy
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests/release/tests"))
import fleet_publish_archive_write as writer
import fleet_publish_historical_package as packages
import fleet_publish_producer as producer
import test_fleet_publish_historical_write as historical_tests
from fleet_publish_github import (
    FleetPublishLookupError,
    GitHubClient,
    ResolutionGitHubClient,
)
from test_fleet_publish_historical_write import HistoricalGit
from test_fleet_publish_resolver import CENTRAL, REPO, RUN, Fixture


class HistoricalBudgetTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        fixture = Fixture()
        raw = Path(producer.__file__).read_bytes()
        pins = {
            producer.PRODUCER_OWNER_PATH: hashlib.sha1(
                f"blob {len(raw)}\0".encode() + raw
            ).hexdigest()
        }
        # The complete hashed native graph is independently built from fixed
        # fixture files. Only its historical pinset is synthetic; the actual
        # ancestry/tree/claim readers and all owner gates run.
        self.stack.enter_context(
            patch.object(producer, "HISTORICAL_PRODUCERS", (pins,))
        )
        self.stack.enter_context(
            patch.object(historical_tests, "Fixture", return_value=fixture)
        )
        self.github = HistoricalGit()
        self.response = self.root / "response.json"
        self.calls = self.root / "calls.jsonl"
        gh = self.root / "gh"
        gh.write_text(
            f"#!{sys.executable}\n"
            "import json,os,sys\nfrom pathlib import Path\n"
            "a=sys.argv[1:];assert a[:5]==['api','--hostname','github.com','--method','GET']\n"
            "v=json.loads(Path(os.environ['BUDGET_801_RESPONSE']).read_text())\n"
            "assert a[-1]==v['route']\n"
            "assert os.environ.get('GH_TOKEN')==v['credential']\n"
            "assert 'GITHUB_TOKEN' not in os.environ and 'FLEET_CENTRAL_TOKEN' not in os.environ\n"
            "with Path(os.environ['BUDGET_801_CALLS']).open('a') as f:f.write(json.dumps([a[-1],v['status']])+'\\n')\n"
            "if '--include' in a:sys.stdout.buffer.write(('HTTP/2.0 '+str(v['status'])+' Result\\r\\nContent-Type: application/json\\r\\n\\r\\n').encode())\n"
            "sys.stdout.buffer.write(json.dumps(v['body']).encode());sys.stdout.flush()\n"
            "if v['status']!=200:sys.stderr.write('DO_NOT_ECHO_PRIVATE_PEER');raise SystemExit(1)\n"
        )
        gh.chmod(0o700)
        event = self.root / "event.json"
        event.write_text(json.dumps({"inputs": {"original_run_id": RUN}}))
        self.stack.enter_context(
            patch.dict(
                os.environ,
                {
                    **self.github.identity,
                    "GITHUB_EVENT_PATH": str(event),
                    "GH_TOKEN": "consumer-read-sentinel",
                    "FLEET_CENTRAL_TOKEN": "central-read-sentinel",
                    "GITHUB_TOKEN": "not-a-fallback",
                    "PATH": str(self.root) + os.pathsep + os.environ.get("PATH", ""),
                    "BUDGET_801_RESPONSE": str(self.response),
                    "BUDGET_801_CALLS": str(self.calls),
                },
            )
        )
        self.stack.enter_context(
            patch.object(writer.sys, "argv", ["fleet_publish_archive_write.py"])
        )
        self.stack.enter_context(
            patch.object(writer, "HistoricalGitHub", return_value=self.github)
        )
        self.stack.enter_context(
            patch.object(
                packages, "observe_package", return_value=self.github.proof["registry"]
            )
        )
        owner = self

        class Central(writer.CentralHistoricalClient):
            def _read(self, route, maximum, *, native_status=False):
                owner.snapshot(route, "central-read-sentinel")
                return super()._read(route, maximum, native_status=native_status)

        class Consumer(writer.ConsumerHistoricalClient):
            def _read(self, route, maximum, *, native_status=False):
                owner.snapshot(route, "consumer-read-sentinel")
                return super()._read(route, maximum, native_status=native_status)

        self.stack.enter_context(
            patch.object(writer, "CentralHistoricalClient", Central)
        )
        self.stack.enter_context(
            patch.object(writer, "ConsumerHistoricalClient", Consumer)
        )

    def snapshot(self, route, credential):
        status = 200
        if "/artifacts?" in route:
            # Actual incident had expired/missing artifacts and retained exact
            # immutable intent/source refs. No artifact text becomes authority.
            body = {"total_count": 0, "artifacts": []}
        else:
            try:
                body = self.github.native_json(route)
            except FleetPublishLookupError as error:
                if error.kind != "native_missing" or error.http_status != 404:
                    raise
                status, body = 404, {"message": "Not Found"}
        self.response.write_text(
            json.dumps(
                {
                    "route": route,
                    "credential": credential,
                    "status": status,
                    "body": body,
                }
            )
        )

    def main_result(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(writer.main(), 0)
        return json.loads(stdout.getvalue())

    def test_real_transport_completes_new_proof_and_final_main_result(self):
        value = self.main_result()
        self.assertEqual(
            value,
            {
                "schema_version": 2,
                "archived": True,
                "publication_performed": False,
                "proof_kind": "historical_source_proof",
                "original_run_id": RUN,
                "attester": {
                    "workflow_path": writer.FINALIZER_WORKFLOW,
                    "workflow_sha": self.github.original.central_tip,
                    "run_id": "201",
                    "run_attempt": 1,
                },
            },
        )
        central_calls = [
            row
            for row in self.calls.read_text().splitlines()
            if json.loads(row)[0].startswith(f"repos/{CENTRAL}/")
        ]
        self.assertLessEqual(len(central_calls), 512)
        self.assertTrue(self.github.effects.refs)
        self.assertEqual(
            sum(path.endswith("/refs") for path, _ in self.github.writes), 1
        )

    def test_public_transport_preserves_both_fixed_command_boundaries(self):
        route = f"repos/{REPO}"
        self.snapshot(route, "consumer-read-sentinel")
        # Exercise the public read operation against actual finite gh children.
        # A refused next call must not launch another child or reuse a result.
        for transport, limit in ((GitHubClient, 128), (ResolutionGitHubClient, 512)):
            with self.subTest(transport=transport.__name__):
                self.calls.write_text("")
                with patch.dict(
                    os.environ, writer.credential_environment("GH_TOKEN"), clear=True
                ):
                    client = transport()
                    expected = self.github.native_json(route)
                    for _ in range(limit):
                        self.assertEqual(client.native_json(route), expected)
                    with self.assertRaises(FleetPublishLookupError) as stopped:
                        client.native_json(route)
                    self.assertEqual(stopped.exception.kind, "command_budget")
                calls = [json.loads(row) for row in self.calls.read_text().splitlines()]
                self.assertEqual(calls, [[route, 200]] * limit)

    def test_existing_exact_proof_keeps_original_attester_and_never_writes_again(self):
        first = self.main_result()
        writes = copy.deepcopy(self.github.writes)
        refs = copy.deepcopy(self.github.effects.refs)
        second = self.main_result()
        self.assertEqual(second, first)
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(self.github.effects.refs, refs)

    def test_private_consumer_uses_the_same_complete_bounded_owner(self):
        self.github.original.routes[f"repos/{REPO}"].update(
            private=True, visibility="private"
        )
        self.assertTrue(self.main_result()["archived"])
        self.assertLessEqual(
            sum(
                json.loads(row)[0].startswith(f"repos/{CENTRAL}/")
                for row in self.calls.read_text().splitlines()
            ),
            512,
        )

    def test_unknown_ref_post_with_exact_readback_never_repeats_the_write(self):
        self.github.effects.after_create_error = True
        self.assertTrue(self.main_result()["archived"])
        self.assertEqual(
            sum(path.endswith("/refs") for path, _ in self.github.writes), 1
        )

    def test_unknown_uncommitted_post_stays_held_without_another_post(self):
        original = self.github.effects.api

        def uncertain(route, payload=None):
            if route.endswith("/refs"):
                raise OSError("DO_NOT_ECHO_PRIVATE_POST")
            return original(route, payload)

        with (
            patch.object(self.github.effects, "api", side_effect=uncertain),
            self.assertRaises(writer.ArchiveWriteRefused),
        ):
            self.main_result()
        self.assertEqual(self.github.effects.refs, {})
        self.assertEqual(
            sum(path.endswith("/refs") for path, _ in self.github.writes), 1
        )

    def test_conflicting_existing_proof_remains_immutable(self):
        self.main_result()
        refs, writes = (
            copy.deepcopy(self.github.effects.refs),
            copy.deepcopy(self.github.writes),
        )
        different = copy.deepcopy(self.github.proof["registry"])
        different["fingerprint"] = "f" * 64
        with (
            patch.object(packages, "observe_package", return_value=different),
            self.assertRaises(writer.ArchiveWriteRefused),
        ):
            self.main_result()
        self.assertEqual(self.github.effects.refs, refs)
        self.assertEqual(self.github.writes, writes)

    def test_actual_process_boundary_emits_only_fixed_private_safe_categories(self):
        for kind, expected in (
            ("command_budget", "lookup_budget"),
            ("PRIVATE_KIND_DO_NOT_ECHO", "lookup_refused"),
        ):
            error = FleetPublishLookupError(
                kind, returncode=91, stderr_sha256="private"
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                patch.object(writer.GitHub, "api", side_effect=error),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as stopped,
            ):
                runpy.run_path(
                    str(ROOT / "scripts/fleet_publish_archive_write.py"),
                    run_name="__main__",
                )
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(),
                f"Central evidence archival refused [{expected}]. Preserve the original claim/run and repair its exact missing evidence; never replay publication.\n",
            )
            self.assertNotIn("PRIVATE", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
