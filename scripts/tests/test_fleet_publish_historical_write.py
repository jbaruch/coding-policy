"""Composed immutable native Git reader and fixed create-only proof writer."""

from __future__ import annotations

import copy
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests/release/tests"))
import fleet_publish_archive_write as writer  # noqa: E402
import fleet_publish_producer as producer  # noqa: E402
import fleet_publish_historical_package as packages  # noqa: E402
from fleet_publish_archive import ArchiveReader, ref_name, native_run  # noqa: E402
from test_fleet_publish_archive_write import Git  # noqa: E402
from test_fleet_publish_resolver import Fixture, RUN, REPO, CENTRAL  # noqa: E402
from test_fleet_publish_historical import proof  # noqa: E402


class HistoricalGit:
    def __init__(self):
        self.original = Fixture()
        self.original.routes[f"repos/{REPO}"].update(
            private=False, visibility="public"
        )
        self.original.records[RUN, "receipt"] = None
        self.original.remove_archive("terminal")
        self.effects = Git()
        self.identity = {
            "GITHUB_REPOSITORY": CENTRAL,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "201",
            "GITHUB_SHA": self.original.central_tip,
            "GITHUB_WORKFLOW_REF": f"{CENTRAL}/.github/workflows/fleet-publish-finalize.yml@refs/heads/main",
        }
        envelope = ArchiveReader(self.original).run_intent(RUN)
        self.proof = proof(envelope)
        native = self.proof["native"]
        original_run = f"repos/{CENTRAL}/actions/runs/{RUN}"
        native["run"] = native_run(self.original.routes[original_run])
        native["attempt"] = native_run(
            self.original.routes[original_run + "/attempts/1"]
        )
        # Original source/claim facts are genuine hashed fixture Git objects.
        self.original.routes[original_run + "/attempts/1/jobs?per_page=100&page=1"] = {
            "total_count": 1,
            "jobs": [
                {**job, "id": int(job["id"]), "run_id": int(job["run_id"])}
                for job in native["jobs"]
            ],
        }
        for suffix in ("", "/attempts/1"):
            self.original.routes[f"repos/{CENTRAL}/actions/runs/201" + suffix] = {
                **self.original.routes[original_run],
                "id": 201,
                "path": writer.FINALIZER_WORKFLOW,
                "head_sha": self.original.central_tip,
                "event": "workflow_dispatch",
                "status": "in_progress",
                "conclusion": None,
            }
        self.writes = []

    def json(self, route):
        return self.native_json(route)

    def native_json(self, route):
        prefix = f"repos/{CENTRAL}/git/"
        if route.startswith(prefix + "ref/tags/"):
            ref = "refs/" + route.removeprefix(prefix + "ref/")
            if ref in self.effects.refs:
                return self.effects.native_json(route)
        for kind, records in (
            ("commits", self.effects.commits),
            ("trees", self.effects.trees),
            ("blobs", self.effects.blobs),
        ):
            if (
                route.startswith(prefix + kind + "/")
                and route.rsplit("/", 1)[1] in records
            ):
                return self.effects.native_json(route)
        return self.original.native_json(route)

    def artifact_evidence(self, run, kind):
        return self.original.artifact_evidence(run, kind)

    def api(self, route, payload=None):
        if payload is not None:
            self.writes.append((route, copy.deepcopy(payload)))
            return self.effects.api(route, payload)
        return self.native_json(route)

    def pages(self, route, key):
        assert route.endswith(f"/{RUN}/attempts/1/jobs") and key == "jobs"
        page = self.original.native_json(route + "?per_page=100&page=1")
        assert isinstance(page, dict) and isinstance(page.get("jobs"), list)
        return page["jobs"]


class ScopedReads:
    """A private consumer and central ledger refuse each other's credential."""

    def __init__(self, owner, repository):
        self.owner, self.repository = owner, repository
        self.calls = []

    def native_json(self, route):
        assert writer.owned_route(route, self.repository), route
        self.calls.append(route)
        return self.owner.native_json(route)

    def json(self, route):
        return self.native_json(route)

    def artifact_evidence(self, run, kind):
        assert self.repository == CENTRAL
        return self.owner.artifact_evidence(run, kind)


class HistoricalWriterTests(unittest.TestCase):
    def setUp(self):
        # Only the graph owner is synthetic; native/source/claim/ref paths and
        # final create/readback use the actual composed implementation.
        self.protocol = patch.object(
            producer,
            "verify_workflow_protocol",
            return_value=copy.deepcopy(producer.HISTORICAL_PRODUCERS[0]),
        )
        self.protocol.start()
        self.addCleanup(self.protocol.stop)
        self.writer_protocol = patch.object(
            writer,
            "verify_workflow_protocol",
            side_effect=producer.verify_workflow_protocol,
        )
        self.writer_protocol.start()
        self.addCleanup(self.writer_protocol.stop)
        self.github = HistoricalGit()
        self.reader = ArchiveReader(self.github)
        self.observation = patch.object(
            packages, "observe_package", return_value=self.github.proof["registry"]
        )
        self.package_observer = self.observation.start()
        self.addCleanup(self.observation.stop)
        self.consumer_reader = patch.object(
            writer,
            "ConsumerHistoricalClient",
            side_effect=lambda repository: ScopedReads(self.github, repository),
        )
        self.consumer_factory = self.consumer_reader.start()
        self.addCleanup(self.consumer_reader.stop)

    def attest(self, **inputs):
        return writer.attest_historical(
            self.github,
            self.reader,
            self.github.identity,
            {"inputs": inputs or {"original_run_id": RUN}},
        )

    def test_creates_only_the_fixed_later_ref_and_preserves_missing_native_receipt(
        self,
    ):
        before = copy.deepcopy(self.github.original.routes)
        self.assertTrue(self.attest())
        self.assertEqual(
            set(self.github.effects.refs),
            {ref_name("historical-source-proof", run_id=RUN)},
        )
        saved = self.reader.historical_source_proof(RUN)
        self.assertIsNotNone(saved)
        self.assertIsNone(self.reader.record(RUN, "receipt"))
        self.assertIsNone(self.reader.terminal(RUN))
        self.assertEqual(self.github.original.routes, before)
        count = len(self.github.writes)
        self.assertTrue(self.attest())
        self.assertEqual(len(self.github.writes), count)
        self.assertEqual(self.reader.historical_source_proof(RUN), saved)
        from fleet_publish_resolver import resolve, resolve_evidence, CentralPublishHold

        evidence = resolve_evidence(
            self.github, REPO, self.github.original.source, run_id=RUN
        )
        self.assertEqual(evidence["proof_kind"], "historical_source_proof")
        with self.assertRaises(CentralPublishHold):
            resolve(self.github, REPO, self.github.original.source, run_id=RUN)

    def test_exact_readback_after_unknown_ref_post_never_repeats_the_effect(self):
        self.github.effects.after_create_error = True
        self.assertTrue(self.attest())
        posts = [
            payload for route, payload in self.github.writes if route.endswith("/refs")
        ]
        self.assertEqual(len(posts), 1)
        self.assertEqual(
            posts[0]["ref"], ref_name("historical-source-proof", run_id=RUN)
        )

    def test_private_consumer_is_supported_by_historical_derivation(self):
        self.github.original.routes[f"repos/{REPO}"].update(
            private=True, visibility="private"
        )
        central = ScopedReads(self.github, CENTRAL)
        self.reader = ArchiveReader(central)
        self.assertTrue(self.attest())
        self.assertTrue(central.calls)
        self.assertEqual(
            set(self.github.effects.refs),
            {ref_name("historical-source-proof", run_id=RUN)},
        )

    def test_unverified_consumer_identity_never_observes_package_or_creates_proof(self):
        variants = [
            {"full_name": "fixture/wrong-consumer"},
            {"archived": True},
            {"disabled": True},
            {"default_branch": None},
        ]
        for fields in variants:
            with self.subTest(fields=fields):
                self.github = HistoricalGit()
                self.reader = ArchiveReader(self.github)
                self.github.original.routes[f"repos/{REPO}"].update(fields)
                before = copy.deepcopy(self.github.original.routes)
                with patch.object(
                    packages, "observe_package", return_value=self.github.proof["registry"]
                ) as package_observer:
                    with self.assertRaises((ValueError, RuntimeError)):
                        self.attest()
                    package_observer.assert_not_called()
                self.assertEqual(self.github.writes, [])
                self.assertEqual(self.github.effects.refs, {})
                self.assertEqual(self.github.original.routes, before)

    def test_selected_app_read_accepts_visibility_change_without_scope_change(self):
        def changed_visibility(*_arguments):
            self.github.original.routes[f"repos/{REPO}"].update(
                private=True, visibility="private"
            )
            return self.github.proof["registry"]

        with patch.object(packages, "observe_package", side_effect=changed_visibility):
            self.assertTrue(self.attest())
        self.assertEqual(
            set(self.github.effects.refs),
            {ref_name("historical-source-proof", run_id=RUN)},
        )

    def test_preflight_derives_only_durable_consumer_without_consumer_reads_or_writes(self):
        central = ScopedReads(self.github, CENTRAL)
        identity, envelope = writer.historical_selection(
            self.github,
            ArchiveReader(central),
            self.github.identity,
            {"inputs": {"original_run_id": RUN}},
        )
        self.assertEqual(envelope["intent"]["consumer_repository"], REPO)
        self.assertEqual(identity["run_id"], "201")
        self.package_observer.assert_not_called()
        self.assertEqual(self.github.writes, [])
        self.assertTrue(central.calls)

    def test_native_output_preflight_emits_only_the_derived_repository_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary) / "event.json"
            event.write_text(json.dumps({"inputs": {"original_run_id": RUN}}))
            output = Path(temporary) / "output"
            stdout = io.StringIO()
            with (
                patch.dict(os.environ, {**self.github.identity, "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output)}),
                patch.object(sys, "argv", ["fleet_publish_archive_write.py", "select-consumer"]),
                patch("fleet_publish.GitHub", return_value=self.github),
                patch.object(writer, "CentralHistoricalClient", return_value=ScopedReads(self.github, CENTRAL)),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(writer.main(), 0)
            name = REPO.split("/", 1)[1]
            self.assertEqual(output.read_text(), "repository_name=" + name + "\n")
            self.assertEqual(json.loads(stdout.getvalue()), {"schema_version": 1, "repository_name": name})
            self.consumer_factory.assert_not_called()
            self.package_observer.assert_not_called()
            self.assertEqual(self.github.writes, [])

    def test_unknown_uncommitted_ref_post_holds_without_a_second_post(self):
        actual = self.github.effects.api

        def uncertain(route, payload=None):
            if route.endswith("/refs"):
                raise OSError("SYNTHETIC unknown POST")
            return actual(route, payload)

        with patch.object(self.github.effects, "api", side_effect=uncertain):
            with self.assertRaises(writer.ArchiveWriteRefused):
                self.attest()
        self.assertEqual(
            len([route for route, _ in self.github.writes if route.endswith("/refs")]),
            1,
        )
        self.assertEqual(self.github.effects.refs, {})

    def test_conflicting_existing_proof_is_preserved_without_replacement(self):
        different = copy.deepcopy(self.github.proof)
        different["registry"]["fingerprint"] = "f" * 64
        ref = ref_name("historical-source-proof", run_id=RUN)
        writer.create_only(
            self.github,
            ref,
            different,
            lambda: self.reader.historical_source_proof(RUN),
        )
        original = copy.deepcopy(self.github.effects.refs)
        count = len(self.github.writes)
        with self.assertRaises(writer.ArchiveWriteRefused):
            self.attest()
        self.assertEqual(self.github.effects.refs, original)
        self.assertEqual(len(self.github.writes), count)
        retained = self.reader.historical_source_proof(RUN)
        assert retained is not None
        self.assertEqual(retained["registry"]["fingerprint"], "f" * 64)

    def test_rerun_negative_native_missing_steps_and_untrusted_inputs_never_write(self):
        for change in ("rerun", "failure", "missing-step", "caller-record"):
            with self.subTest(change=change):
                self.github = HistoricalGit()
                self.reader = ArchiveReader(self.github)
                route = f"repos/{CENTRAL}/actions/runs/{RUN}"
                if change == "rerun":
                    self.github.original.routes[route]["run_attempt"] = 2
                elif change == "failure":
                    self.github.original.routes[route]["conclusion"] = "failure"
                elif change == "missing-step":
                    self.github.original.routes[
                        route + "/attempts/1/jobs?per_page=100&page=1"
                    ]["jobs"][0]["steps"].pop()
                with self.assertRaises((ValueError, RuntimeError)):
                    if change == "caller-record":
                        self.attest(original_run_id=RUN, record=self.github.proof)
                    else:
                        self.attest()
                self.assertEqual(self.github.writes, [])


class HistoricalCredentialTransportTests(unittest.TestCase):
    def test_real_gh_children_keep_private_consumer_and_central_credentials_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "calls"
            gh = root / "gh"
            gh.write_text(
                f"#!{sys.executable}\n"
                "import json,os,sys\n"
                "from pathlib import Path\n"
                "a=sys.argv[1:]\n"
                "assert a[:5]==['api','--hostname','github.com','--method','GET']\n"
                "route=a[-1]\n"
                "central=route.startswith('repos/jbaruch/coding-policy/') or route=='repos/jbaruch/coding-policy'\n"
                "expected=os.environ['FIXTURE_CENTRAL'] if central else os.environ['FIXTURE_CONSUMER']\n"
                "assert os.environ.get('GH_TOKEN')==expected\n"
                "assert 'GITHUB_TOKEN' not in os.environ and 'FLEET_CENTRAL_TOKEN' not in os.environ\n"
                "with Path(os.environ['FIXTURE_CALLS']).open('a') as out:out.write(json.dumps([route,central])+'\\n')\n"
                "if '--include' in a:sys.stdout.write('HTTP/2.0 200 OK\\r\\nContent-Type: application/json\\r\\n\\r\\n')\n"
                "print(json.dumps({'total_count':0,'artifacts':[]} if '/artifacts?' in route else {'private':True}))\n"
            )
            gh.chmod(0o700)
            with patch.dict(
                os.environ,
                {
                    "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
                    "GH_TOKEN": "consumer-read-sentinel",
                    "FLEET_CENTRAL_TOKEN": "central-read-sentinel",
                    "GITHUB_TOKEN": "must-not-fallback",
                    "FIXTURE_CENTRAL": "central-read-sentinel",
                    "FIXTURE_CONSUMER": "consumer-read-sentinel",
                    "FIXTURE_CALLS": str(log),
                },
            ):
                central = writer.CentralHistoricalClient()
                consumer = writer.ConsumerHistoricalClient(REPO)
                transport = writer.HistoricalEvidenceTransport(central, consumer, REPO)
                self.assertEqual(transport.json(f"repos/{REPO}"), {"private": True})
                self.assertEqual(transport.native_json(f"repos/{CENTRAL}"), {"private": True})
                self.assertIsNone(transport.artifact_evidence(RUN, "intent"))
                before = log.read_bytes()
                for route in (
                    "repos/jbaruch/unselected",
                    f"repos/{CENTRAL}-suffix/git/commits/" + "a" * 40,
                    f"repos/{REPO}-suffix",
                ):
                    with self.assertRaises(writer.ArchiveWriteRefused):
                        transport.json(route)
                with self.assertRaises(writer.ArchiveWriteRefused):
                    consumer.json(f"repos/{CENTRAL}")
                with self.assertRaises(writer.ArchiveWriteRefused):
                    central.json(f"repos/{REPO}")
                with patch.dict(os.environ, {"GH_TOKEN": ""}):
                    with self.assertRaises(writer.ArchiveWriteRefused):
                        consumer.json(f"repos/{REPO}")
                self.assertEqual(log.read_bytes(), before)
                self.assertEqual(os.environ["GH_TOKEN"], "consumer-read-sentinel")
                self.assertEqual(os.environ["FLEET_CENTRAL_TOKEN"], "central-read-sentinel")
            rows = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(rows), 3)
            self.assertEqual([row[1] for row in rows], [False, True, True])


if __name__ == "__main__":
    unittest.main()
