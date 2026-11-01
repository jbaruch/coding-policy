"""Composed immutable native Git reader and fixed create-only proof writer."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
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
        self.observation.start()
        self.addCleanup(self.observation.stop)

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


if __name__ == "__main__":
    unittest.main()
