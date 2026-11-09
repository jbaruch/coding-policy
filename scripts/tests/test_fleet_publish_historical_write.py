"""Composed immutable native Git reader and fixed create-only proof writer."""

from __future__ import annotations

import copy
import contextlib
import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests/release/tests"))
import fleet_publish_archive_write as writer  # noqa: E402
import fleet_publish_producer as producer  # noqa: E402
import fleet_publish_historical_package as packages  # noqa: E402
from fleet_publish_archive import ArchiveError, ArchiveReader, ref_name, native_run  # noqa: E402
from test_fleet_publish_archive_write import Git  # noqa: E402
from test_fleet_publish_resolver import Fixture, RUN, REPO, CENTRAL  # noqa: E402
from test_fleet_publish_historical import proof, maximum_inventory_proof  # noqa: E402
import test_fleet_publish_historical_package as package_tests  # noqa: E402


class HistoricalGit(writer.HistoricalGitHub):
    def __init__(self):
        super().__init__()
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
                "workflow_id": 78,
                "status": "in_progress",
                "conclusion": None,
            }
        for path, workflow_id, rows in (
            (writer.CENTRAL_WORKFLOW, 77, [self.original.routes[original_run]]),
            (
                writer.FINALIZER_WORKFLOW,
                78,
                [self.original.routes[f"repos/{CENTRAL}/actions/runs/201"]],
            ),
        ):
            self.original.routes[
                f"repos/{CENTRAL}/actions/workflows/{path.rsplit('/', 1)[1]}"
            ] = {
                "id": workflow_id,
                "path": path,
                "state": "active",
            }
            self.original.routes[
                f"repos/{CENTRAL}/actions/workflows/{workflow_id}/runs?per_page=100&page=1"
            ] = {
                "total_count": len(rows),
                "workflow_runs": copy.deepcopy(rows),
            }
        self.writes = []
        self.catalog_reads = []

    def catalog(self, workflow_id):
        return self.original.routes[
            f"repos/{CENTRAL}/actions/workflows/{workflow_id}/runs?per_page=100&page=1"
        ]

    def set_catalog(self, workflow_id, rows):
        prefix = f"repos/{CENTRAL}/actions/workflows/{workflow_id}/runs?per_page=100&page="
        for route in list(self.original.routes):
            if route.startswith(prefix):
                del self.original.routes[route]
        for page, start in enumerate(range(0, max(1, len(rows)), 100), start=1):
            self.original.routes[prefix + str(page)] = {
                "total_count": len(rows),
                "workflow_runs": copy.deepcopy(rows[start : start + 100]),
            }

    def json(self, route):
        return self.native_json(route)

    def native_json(self, route) -> Any:
        root, _, query = route.partition("?")
        prefix = f"repos/{CENTRAL}/actions/workflows/"
        if root.startswith(prefix) and root.endswith("/runs"):
            workflow_id = int(root.removeprefix(prefix).removesuffix("/runs"))
            parameters = parse_qs(query, strict_parsing=True)
            assert set(parameters) in (
                {"per_page", "page"},
                {"status", "per_page", "page"},
            )
            limit, page = int(parameters["per_page"][0]), int(parameters["page"][0])
            assert limit in (1, 100) and page > 0
            self.catalog_reads.append(route)
            first = self.catalog(workflow_id)
            pages = sorted(
                (
                    (int(key.rsplit("=", 1)[1]), value)
                    for key, value in self.original.routes.items()
                    if key.startswith(f"{root}?per_page=100&page=")
                ),
            )
            rows = [row for _, value in pages for row in value["workflow_runs"]]
            status = parameters.get("status", [None])[0]
            if status is not None:
                rows = [row for row in rows if row.get("status") == status]
            result: dict[str, Any] = {
                "workflow_runs": copy.deepcopy(rows[(page - 1) * limit : page * limit])
            }
            if "total_count" in first:
                result["total_count"] = len(rows) if status else first["total_count"]
            return result
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

    def api(self, path, payload=None, *, binary=False, method="POST") -> Any:
        assert binary is False and method == "POST"
        if payload is not None:
            self.writes.append((path, copy.deepcopy(payload)))
            return self.effects.api(path, payload)
        return self.native_json(path)

    def pages(self, route, key=None):
        if key == "workflow_runs":
            return super().pages(route, key)
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
            producer, "verify_workflow_protocol", return_value=None
        )
        self.protocol.start()
        self.addCleanup(self.protocol.stop)
        self.pinset = patch.object(
            producer,
            "historical_pinset",
            return_value=copy.deepcopy(producer.HISTORICAL_PRODUCERS[0]),
        )
        self.pinset.start()
        self.addCleanup(self.pinset.stop)
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

    def test_maximum_inventory_exact_encoded_budget_roundtrips_through_create_readback(
        self,
    ):
        value = maximum_inventory_proof(6)

        def expected_bytes():
            return (
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
                + b"\n"
            )

        from fleet_publish_archive import (
            MAX_ARCHIVE_BYTES,
            decode_archive,
            encode_archive,
        )

        remaining = MAX_ARCHIVE_BYTES - len(expected_bytes())
        if remaining % 2:
            value["attester"]["run_id"] = "1002"
            remaining -= 1
        for index, row in enumerate(value["registry"]["source"]):
            if row["path"].startswith("z/"):
                addition = min(remaining // 2, 1000 - len(row["path"]))
                row["path"] += "x" * addition
                value["registry"]["installed"][index]["path"] = row["path"]
                remaining -= addition * 2
        self.assertEqual(remaining, 0)
        raw = expected_bytes()
        self.assertEqual(len(value["registry"]["source"]), 512)
        self.assertEqual(len(raw), MAX_ARCHIVE_BYTES)
        self.assertEqual(decode_archive(encode_archive(value)), value)
        github = Git()
        ref = ref_name("historical-source-proof", run_id="101")
        writer.create_only(github, ref, value, lambda: github.lookup(ref))
        self.assertEqual(github.lookup(ref), value)
        self.assertEqual(len(github.refs), 1)
        count = len(github.calls)
        writer.create_only(github, ref, value, lambda: github.lookup(ref))
        self.assertEqual(len(github.calls), count)
        # One extra encoded byte must be refused before any new effect, even
        # though every individual inventory/path field remains accepted.
        over = copy.deepcopy(value)
        over["attester"]["run_id"] += "0"
        self.assertEqual(
            len(
                json.dumps(
                    over, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            )
            + 1,
            MAX_ARCHIVE_BYTES + 1,
        )
        with self.assertRaises(ArchiveError):
            writer.create_only(
                github,
                ref_name("historical-source-proof", run_id="102"),
                over,
                lambda: None,
            )
        self.assertEqual(len(github.calls), count)
        self.assertEqual(github.lookup(ref), value)

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
                patch.object(writer, "HistoricalGitHub", return_value=self.github),
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

    def test_inflight_poll_publish_or_other_finalizer_never_creates_proof(self):
        for workflow_id, event in (
            (77, "schedule"),
            (77, "workflow_dispatch"),
            (78, "workflow_run"),
            (78, "workflow_dispatch"),
        ):
            for status in ("queued", "in_progress", "waiting", "requested", "pending"):
                with self.subTest(workflow_id=workflow_id, event=event, status=status):
                    self.github = HistoricalGit()
                    self.reader = ArchiveReader(self.github)
                    page = self.github.catalog(workflow_id)
                    page["workflow_runs"].append(
                        {
                            **page["workflow_runs"][0],
                            "id": 301,
                            "event": event,
                            "status": status,
                            "conclusion": None,
                        }
                    )
                    page["total_count"] += 1
                    with self.assertRaises(ValueError):
                        self.attest()
                    self.assertEqual(self.github.writes, [])
                    self.assertEqual(self.github.effects.refs, {})

    def test_completed_failed_skipped_otherbranch_and_rerun_are_quiescent(self):
        for run_id, (workflow_id, event, conclusion, branch, attempt) in enumerate(
            (
                (77, "schedule", "success", "main", 1),
                (77, "workflow_dispatch", "failure", "feature/old", 2),
                (78, "workflow_run", "skipped", "feature/old", 3),
            ),
            start=301,
        ):
            page = self.github.catalog(workflow_id)
            page["workflow_runs"].append(
                {
                    **page["workflow_runs"][0],
                    "id": run_id,
                    "event": event,
                    "conclusion": conclusion,
                    "status": "completed",
                    "head_branch": branch,
                    "run_attempt": attempt,
                }
            )
            page["total_count"] += 1
        self.assertTrue(self.attest())
        self.assertIsNotNone(self.reader.historical_source_proof(RUN))

    def test_large_completed_history_supports_public_private_creation_and_noop(self):
        for private in (False, True):
            with self.subTest(private=private):
                self.github = HistoricalGit()
                self.github.original.routes[f"repos/{REPO}"].update(
                    private=private, visibility="private" if private else "public"
                )
                self.reader = ArchiveReader(self.github)
                for workflow_id, start in ((77, 1000), (78, 10000)):
                    first = copy.deepcopy(
                        self.github.catalog(workflow_id)["workflow_runs"][0]
                    )
                    rows = [first] + [
                        {
                            **first,
                            "id": run_id,
                            "status": "completed",
                            "conclusion": "skipped",
                            "event": "schedule"
                            if workflow_id == 77
                            else "workflow_run",
                        }
                        for run_id in range(start, start + 1100)
                    ]
                    self.github.set_catalog(workflow_id, rows)
                self.assertTrue(self.attest())
                saved = self.reader.historical_source_proof(RUN)
                self.assertIsNotNone(saved)
                count = len(self.github.writes)
                self.assertTrue(self.attest())
                self.assertEqual(self.github.writes[count:], [])
                self.assertEqual(self.reader.historical_source_proof(RUN), saved)
                # The resource contract must not regress to a completed-history
                # crawl, regardless of the observed completed total.
                self.assertFalse(
                    any(
                        "/runs?per_page=100" in route
                        for route in self.github.catalog_reads
                    )
                )

    def test_complement_holds_when_a_status_query_omits_or_misses_an_active_run(self):
        for change in ("omitted", "transition", "unknown-status"):
            with self.subTest(change=change):
                self.github = HistoricalGit()
                self.reader = ArchiveReader(self.github)
                first = copy.deepcopy(self.github.catalog(77)["workflow_runs"][0])
                self.github.set_catalog(
                    77,
                    [
                        first,
                        {
                            **first,
                            "id": 301,
                            "status": "mystery"
                            if change == "unknown-status"
                            else "waiting",
                            "conclusion": None,
                        },
                    ],
                )
                actual = self.github.api
                reached = False

                def missed(route, payload=None, *, binary=False, method="POST"):
                    nonlocal reached
                    if "/77/runs?status=waiting&per_page=100" in route:
                        reached = True
                        if change == "omitted":
                            return {"total_count": 0, "workflow_runs": []}
                        if change == "transition":
                            # A native waiting -> queued transition occurs
                            # after queued was queried. No ordering assumption
                            # may turn that missed row into quiescence.
                            self.github.catalog(77)["workflow_runs"][1]["status"] = (
                                "queued"
                            )
                    return actual(route, payload, binary=binary, method=method)

                with patch.object(self.github, "api", side_effect=missed):
                    with self.assertRaises(writer.ArchiveWriteRefused):
                        self.attest()
                self.assertTrue(reached)
                self.assertEqual(self.github.writes, [])

    def test_summary_damage_or_changes_hold_without_effect(self):
        for change in (
            "missing-total",
            "boolean-total",
            "negative-total",
            "completed-exceeds-all",
            "missing-sample",
            "extra-sample",
            "wrong-workflow",
            "completed-not-terminal",
            "changed-total",
            "changed-sample",
        ):
            with self.subTest(change=change):
                self.github = HistoricalGit()
                self.reader = ArchiveReader(self.github)
                actual = self.github.api
                calls = 0

                def damaged(route, payload=None, *, binary=False, method="POST"):
                    nonlocal calls
                    result = actual(route, payload, binary=binary, method=method)
                    if "/77/runs?" in route and "per_page=1&" in route:
                        calls += 1
                        if change == "missing-total":
                            result.pop("total_count")
                        elif change == "boolean-total":
                            result["total_count"] = True
                        elif change == "negative-total":
                            result["total_count"] = -1
                        elif (
                            change == "completed-exceeds-all"
                            and "status=completed" in route
                        ):
                            result["total_count"] = 2
                        elif change == "missing-sample":
                            result["workflow_runs"] = []
                        elif change == "extra-sample":
                            result["workflow_runs"] *= 2
                        elif change == "wrong-workflow":
                            result["workflow_runs"][0]["workflow_id"] = 78
                        elif (
                            change == "completed-not-terminal"
                            and "status=completed" in route
                        ):
                            result["workflow_runs"][0].update(
                                status="queued", conclusion=None
                            )
                        elif change == "changed-total" and calls == 4:
                            result["total_count"] = 2
                        elif change == "changed-sample" and calls == 4:
                            result["workflow_runs"][0]["run_attempt"] = 2
                    return result

                with patch.object(self.github, "api", side_effect=damaged):
                    with self.assertRaises((ValueError, RuntimeError)):
                        self.attest()
                self.assertEqual(self.github.writes, [])

    def test_same_native_identity_cannot_be_both_completed_and_active(self):
        current = copy.deepcopy(self.github.catalog(78)["workflow_runs"][0])
        self.github.set_catalog(
            78,
            [
                current,
                {**current, "id": 301, "status": "completed", "conclusion": "success"},
            ],
        )
        actual = self.github.api

        def contradictory(route, payload=None, *, binary=False, method="POST"):
            result = actual(route, payload, binary=binary, method=method)
            if "/78/runs?status=completed&per_page=1" in route:
                result["workflow_runs"] = [
                    {**current, "status": "completed", "conclusion": "success"}
                ]
            return result

        with patch.object(self.github, "api", side_effect=contradictory):
            with self.assertRaises(writer.ArchiveWriteRefused):
                self.attest()
        self.assertEqual(self.github.writes, [])

    def test_excess_active_catalog_never_becomes_absence(self):
        first = copy.deepcopy(self.github.catalog(77)["workflow_runs"][0])
        self.github.set_catalog(
            77,
            [first]
            + [
                {**first, "id": run_id, "status": "queued", "conclusion": None}
                for run_id in range(1000, 2001)
            ],
        )
        with self.assertRaises(ValueError):
            self.attest()
        self.assertEqual(self.github.writes, [])

    def test_fixed_original_and_attester_native_fences_hold_identity_changes(self):
        for target in ("original", "attester"):
            for after_write in (False, True):
                with self.subTest(target=target, after_write=after_write):
                    self.github = HistoricalGit()
                    self.reader = ArchiveReader(self.github)
                    actual = self.github.api
                    run_id = RUN if target == "original" else "201"
                    route = f"repos/{CENTRAL}/actions/runs/{run_id}"
                    calls = 0

                    def changed(path, payload=None, *, binary=False, method="POST"):
                        nonlocal calls
                        result = actual(path, payload, binary=binary, method=method)
                        if path == route:
                            calls += 1
                            if calls == (3 if after_write else 2):
                                result["run_attempt"] = 2
                        return result

                    with patch.object(self.github, "api", side_effect=changed):
                        with self.assertRaises((ValueError, RuntimeError)):
                            self.attest()
                    posts = [
                        route
                        for route, _ in self.github.writes
                        if route.endswith("/refs")
                    ]
                    self.assertEqual(len(posts), 1 if after_write else 0)
                    self.assertEqual(len(self.github.effects.refs), len(posts))

    def test_complete_catalog_uses_all_pages_and_holds_for_later_inflight_row(self):
        for terminal in (False, True):
            with self.subTest(terminal=terminal):
                self.github = HistoricalGit()
                self.reader = ArchiveReader(self.github)
                page = self.github.catalog(77)
                original = copy.deepcopy(page["workflow_runs"][0])
                page["workflow_runs"].extend(
                    {**original, "id": run_id, "event": "schedule"}
                    for run_id in range(400, 499)
                )
                page["total_count"] = 101
                self.github.original.routes[
                    f"repos/{CENTRAL}/actions/workflows/77/runs?per_page=100&page=2"
                ] = {
                    "total_count": 101,
                    "workflow_runs": [
                        {
                            **original,
                            "id": 499,
                            "event": "schedule",
                            "status": "completed" if terminal else "queued",
                            "conclusion": "skipped" if terminal else None,
                        }
                    ],
                }
                if terminal:
                    self.assertTrue(self.attest())
                    self.assertIsNotNone(self.reader.historical_source_proof(RUN))
                else:
                    with self.assertRaises(ValueError):
                        self.attest()
                    self.assertEqual(self.github.writes, [])

    def test_unavailable_catalog_preserves_no_effect_and_exception(self):
        actual = self.github.api
        unavailable = OSError("synthetic unavailable native catalog")

        def failed_catalog(route, payload=None, *, binary=False, method="POST"):
            if "/actions/workflows/" in route:
                raise unavailable
            return actual(route, payload, binary=binary, method=method)

        with patch.object(self.github, "api", side_effect=failed_catalog):
            with self.assertRaises(OSError) as caught:
                self.attest()
        self.assertIs(caught.exception, unavailable)
        self.assertEqual(self.github.writes, [])

    def test_unknown_catalog_and_attester_identity_never_write(self):
        changes = (
            ("missing-attester", None),
            ("empty-publisher", None),
            ("workflow-path", None),
            ("workflow-id", None),
            ("missing-total", None),
            ("oversize", None),
            ("status", "mystery"),
            ("conclusion", None),
            ("status", []),
            ("conclusion", {}),
            ("event", []),
            ("run_attempt", True),
            ("run_attempt", 0),
            ("repository", {"full_name": "fixture/other"}),
            ("head_sha", None),
            ("created_at", "not-a-date"),
            ("head_branch", None),
            ("event", "push"),
            ("attester-head", None),
            ("attester-attempt", None),
            ("attester-main", None),
            ("attester-event", None),
            ("duplicate-id", None),
        )
        for change, value in changes:
            with self.subTest(change=change, value=value):
                self.github = HistoricalGit()
                self.reader = ArchiveReader(self.github)
                page = self.github.catalog(77)
                current = self.github.catalog(78)["workflow_runs"][0]
                metadata = self.github.original.routes[
                    f"repos/{CENTRAL}/actions/workflows/fleet-publish.yml"
                ]
                if change in ("missing-attester", "empty-publisher"):
                    page = self.github.catalog(
                        78 if change == "missing-attester" else 77
                    )
                    page.update(total_count=0, workflow_runs=[])
                elif change == "workflow-path":
                    metadata["path"] = writer.FINALIZER_WORKFLOW
                elif change == "workflow-id":
                    metadata["id"] = 78
                elif change == "missing-total":
                    page.pop("total_count")
                elif change == "oversize":
                    page["total_count"] = 1001
                elif change.startswith("attester-"):
                    key, replacement = {
                        "attester-head": ("head_sha", "a" * 40),
                        "attester-attempt": ("run_attempt", 2),
                        "attester-main": ("head_branch", "feature/other"),
                        "attester-event": ("event", "workflow_run"),
                    }[change]
                    current[key] = replacement
                elif change == "duplicate-id":
                    page = self.github.catalog(78)
                    page["workflow_runs"].append(
                        copy.deepcopy(page["workflow_runs"][0])
                    )
                    page["total_count"] += 1
                else:
                    page["workflow_runs"][0][change] = value
                with self.assertRaises((ValueError, RuntimeError)):
                    self.attest()
                self.assertEqual(self.github.writes, [])

    def test_catalog_changes_after_write_hold_without_repeating_or_deleting_ref(self):
        for change in ("attempt", "new-run", "removed-run", "inflight", "workflow-id"):
            with self.subTest(change=change):
                self.github = HistoricalGit()
                self.reader = ArchiveReader(self.github)
                actual = self.github.effects.api

                def changed_after_ref(route, payload=None):
                    result = actual(route, payload)
                    if route.endswith("/refs"):
                        page = self.github.catalog(77)
                        if change == "attempt":
                            page["workflow_runs"][0]["run_attempt"] = 2
                        elif change == "new-run":
                            page["workflow_runs"].append(
                                {**page["workflow_runs"][0], "id": 301}
                            )
                            page["total_count"] += 1
                        elif change == "removed-run":
                            page.update(total_count=0, workflow_runs=[])
                        elif change == "inflight":
                            page["workflow_runs"][0].update(
                                status="queued", conclusion=None
                            )
                        else:
                            self.github.original.routes[
                                f"repos/{CENTRAL}/actions/workflows/fleet-publish.yml"
                            ]["id"] = 78
                    return result

                with patch.object(
                    self.github.effects, "api", side_effect=changed_after_ref
                ):
                    with self.assertRaises((ValueError, RuntimeError)):
                        self.attest()
                self.assertEqual(
                    len(
                        [
                            route
                            for route, _ in self.github.writes
                            if route.endswith("/refs")
                        ]
                    ),
                    1,
                )
                self.assertIsNotNone(self.reader.historical_source_proof(RUN))

    def test_existing_proof_also_requires_fresh_stable_quiescence(self):
        self.assertTrue(self.attest())
        saved = copy.deepcopy(self.github.effects.refs)
        count = len(self.github.writes)
        actual = self.github.pages
        calls = 0

        def changed_second_catalog(route, key=None):
            nonlocal calls
            if key == "workflow_runs" and route.endswith("/77/runs?status=queued"):
                calls += 1
                if calls == 2:
                    self.github.catalog(77)["workflow_runs"][0]["run_attempt"] = 2
            return actual(route, key)

        with patch.object(self.github, "pages", side_effect=changed_second_catalog):
            with self.assertRaises(ValueError):
                self.attest()
        self.assertEqual(self.github.effects.refs, saved)
        self.assertEqual(len(self.github.writes), count)


class HistoricalCredentialTransportTests(unittest.TestCase):
    def test_real_package_and_archive_api_children_keep_only_their_own_credential(self):
        fixture = package_tests.PackageTests()
        fixture.setUp()
        original = fixture.envelope["intent"]
        repo = original["consumer_repository"]
        source = package_tests.PackageGit(fixture)
        routes = {
            **{f"repos/{repo}/git/commits/{sha}": value for sha, value in source.commits.items()},
            **{f"repos/{repo}/git/trees/{sha}?recursive=1": value for sha, value in source.trees.items()},
            **{
                f"repos/{repo}/git/blobs/{sha}": {
                    "encoding": "base64", "size": len(raw),
                    "content": base64.b64encode(raw).decode(),
                }
                for sha, raw in source.blobs.items()
            },
            f"repos/{CENTRAL}": {"private": True},
            f"repos/{CENTRAL}/actions/runs/{RUN}/jobs?per_page=100&page=1": {
                "total_count": 0, "jobs": [],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            route_file, log, gh = root / "routes.json", root / "calls", root / "gh"
            route_file.write_text(json.dumps(routes))
            gh.write_text(
                f"#!{sys.executable}\n"
                "import json,os,sys\n"
                "from pathlib import Path\n"
                "a=sys.argv[1:];assert a[:3]==['api','--hostname','github.com']\n"
                "route=a[3] if a[3]!='--method' else a[-1]\n"
                "central=route=='repos/jbaruch/coding-policy' or route.startswith('repos/jbaruch/coding-policy/')\n"
                "expected=os.environ['FIXTURE_CENTRAL'] if central else os.environ['FIXTURE_CONSUMER']\n"
                "assert os.environ.get('GH_TOKEN')==expected\n"
                "assert 'GITHUB_TOKEN' not in os.environ and 'FLEET_CENTRAL_TOKEN' not in os.environ\n"
                "write='--input' in a\n"
                "if write:assert central and a[-4:]==['--method','POST','--input','-'];json.load(sys.stdin)\n"
                "with Path(os.environ['FIXTURE_CALLS']).open('a') as out:out.write(json.dumps([route,central,write])+'\\n')\n"
                "print(json.dumps({'sha':'fixture-write'} if write else json.loads(Path(os.environ['FIXTURE_ROUTES']).read_text())[route]))\n"
            )
            gh.chmod(0o700)
            environment = {
                "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
                "GH_TOKEN": "consumer-read-sentinel",
                "FLEET_CENTRAL_TOKEN": "central-write-sentinel",
                "GITHUB_TOKEN": "must-not-fallback",
                "FIXTURE_CENTRAL": "central-write-sentinel",
                "FIXTURE_CONSUMER": "consumer-read-sentinel",
                "FIXTURE_CALLS": str(log), "FIXTURE_ROUTES": str(route_file),
            }
            with patch.dict(os.environ, environment):
                github = writer.HistoricalGitHub()
                github.bind_consumer(repo)
                destination = root / "projection"
                packages.historical_projection(github, fixture.envelope, destination)
                self.assertEqual((destination / "CHANGELOG.md").read_bytes(), fixture.stamped_changelog)
                self.assertEqual(
                    github.pages(f"repos/{CENTRAL}/actions/runs/{RUN}/jobs", "jobs"), []
                )
                before = dict(os.environ)
                def archive_read(reader):
                    self.assertEqual(dict(os.environ), before)
                    return reader.client.json(f"repos/{CENTRAL}")
                self.assertEqual(github.archive_operation(archive_read), {"private": True})
                self.assertEqual(dict(os.environ), before)
                with self.assertRaises(writer.ArchiveWriteRefused):
                    writer.HistoricalGitHub().api(f"repos/{repo}")
                for kind in ("blobs", "trees", "commits", "refs"):
                    self.assertEqual(github.api(f"repos/{CENTRAL}/git/{kind}", {"fixture": True}), {"sha": "fixture-write"})
                recorded = log.read_bytes()
                for route, payload, method in (
                    (f"repos/{repo}/git/refs", {"fixture": True}, "POST"),
                    (f"repos/{CENTRAL}/releases", {"fixture": True}, "POST"),
                    (f"repos/{CENTRAL}/git/refs", {"fixture": True}, "PATCH"),
                    (f"repos/{repo}-suffix/git/refs", None, "POST"),
                    ("repos/jbaruch/unselected/git/refs", None, "POST"),
                ):
                    with self.assertRaises(writer.ArchiveWriteRefused):
                        github.api(route, payload, method=method)
                with self.assertRaises(writer.ArchiveWriteRefused):
                    github.bind_consumer("jbaruch/unselected")
                self.assertEqual(log.read_bytes(), recorded)
            rows = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertTrue(any(not central for _, central, _ in rows))
            self.assertEqual(sum(write for _, _, write in rows), 4)

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
