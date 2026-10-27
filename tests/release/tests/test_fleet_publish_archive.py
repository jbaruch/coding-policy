"""Prospective central archive owner; deterministic metadata, no network/effects."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[3] / "skills/release"
SPEC = importlib.util.spec_from_file_location(
    "fleet_publish_archive", SOURCE / "fleet_publish_archive.py"
)
assert SPEC is not None and SPEC.loader is not None
archive: Any = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    SPEC.loader.exec_module(archive)


def fixture() -> list[Any]:
    record = {
        "schema_version": 1,
        "consumer_repository": "jbaruch/nanoclaw-fixture",
        "source_sha": "a" * 40,
        "source_ref": "refs/heads/main",
        "source_tree_sha": "b" * 40,
        "workflow_repository": "jbaruch/coding-policy",
        "workflow_sha": "c" * 40,
        "run_id": "101",
        "run_attempt": 1,
        "plugin_name": "jbaruch/nanoclaw-fixture",
        "state": "claimed",
        "registry_baseline": "unpublished",
        "version": "0.1.0",
        "publish_outcome": "not_started",
        "publish_exit_code": None,
        "landed_after_error": False,
        "moderation": "not_checked",
        "origin_run_id": "101",
        "origin_run_attempt": 1,
        "bookkeeping_sha": None,
    }
    intent = archive.make_intent(record, "d" * 40)
    receipt = {
        **record,
        "state": "confirmed",
        "publish_outcome": "success",
        "publish_exit_code": 0,
        "moderation": "pass",
        "bookkeeping_sha": "d" * 40,
    }
    run = {
        "id": 101,
        "run_attempt": 1,
        "workflow_id": 77,
        "path": ".github/workflows/fleet-publish.yml",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": "c" * 40,
        "repository": {"full_name": "jbaruch/coding-policy"},
        "head_repository": {"full_name": "jbaruch/coding-policy"},
        "display_title": "fleet-publish-v1:jbaruch/nanoclaw-fixture:" + "a" * 40,
        "created_at": "2026-10-27T10:00:00Z",
        "status": "completed",
        "conclusion": "success",
        "private_unneeded_payload": "PRIVATE-metadata-must-not-persist",
    }
    jobs = [
        {
            "id": 901,
            "name": "publish",
            "run_id": 101,
            "run_attempt": 1,
            "head_sha": "c" * 40,
            "status": "completed",
            "conclusion": "success",
            "steps": [
                {
                    "number": n,
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                }
                for n, name in enumerate((archive.CLAIM_STEP, archive.EFFECT_STEP), 1)
            ],
        }
    ]
    finalizer = {
        "workflow_path": archive.FINALIZER_WORKFLOW,
        "workflow_sha": "e" * 40,
        "run_id": "102",
        "run_attempt": 1,
    }
    return [intent, receipt, run, copy.deepcopy(run), jobs, finalizer]


def terminal(parts):
    return archive.make_terminal(*parts[:5], finalizer=parts[5])


class ArchiveSchemaTests(unittest.TestCase):
    def test_source_and_per_run_names_are_fixed_disjoint_and_source_addressed(self):
        first = archive.ref_name(
            "source", repository="jbaruch/nanoclaw-fixture", source_sha="a" * 40
        )
        second = archive.ref_name(
            "source", repository="jbaruch/other", source_sha="a" * 40
        )
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("refs/tags/fleet-publish-v1/sources/"))
        self.assertEqual(
            archive.ref_name("intent", run_id="101"),
            "refs/tags/fleet-publish-v1/runs/101/1/intent",
        )
        for args in (
            {"run_id": "0"},
            {"run_id": "101", "attempt": 2},
            {"run_id": "101", "repository": "jbaruch/other"},
        ):
            with self.assertRaises(archive.ArchiveError):
                archive.ref_name("terminal", **args)

    def test_per_run_hold_is_exact_nonresettable_gate_metadata(self):
        finalizer = fixture()[5]
        held = archive.make_hold("101", 2, "c" * 40, finalizer=finalizer)
        self.assertEqual(
            archive.ref_name("hold", run_id="101"),
            "refs/tags/fleet-publish-v1/runs/101/hold",
        )
        self.assertEqual(archive.decode_archive(archive.encode_archive(held)), held)
        for changes in (
            {"observed_attempt": 1},
            {"reason": "clear"},
            {"schema_version": 2},
            {"extra": True},
        ):
            with self.assertRaises(archive.ArchiveError):
                archive.parse_hold({**held, **changes})

    def test_canonical_roundtrip_preserves_exact_intent_receipt_and_projects_only_native_facts(
        self,
    ):
        parts = fixture()
        result = terminal(parts)
        raw = archive.encode_archive(result)
        self.assertNotIn(b"PRIVATE", raw)
        self.assertEqual(archive.decode_archive(raw), result)
        self.assertEqual(result["intent"], parts[0])
        self.assertEqual(result["receipt"], parts[1])
        self.assertTrue(archive.positive_native(result["native"]))
        self.assertEqual(
            archive.decode_archive(archive.encode_archive(parts[0])), parts[0]
        )

    def test_failed_published_and_uncertain_outcomes_are_kept_without_upgrade(self):
        for state in ("published", "uncertain", "blocked"):
            parts = fixture()
            parts[1].update(
                state=state, moderation="pending" if state == "published" else "unknown"
            )
            parts[2]["conclusion"] = parts[3]["conclusion"] = "failure"
            parts[4][0]["conclusion"] = "failure"
            parts[4][0]["steps"][1]["conclusion"] = "failure"
            if state != "published":
                parts[1].update(
                    publish_outcome="unknown", publish_exit_code=None, version=None
                )
            result = terminal(parts)
            self.assertFalse(archive.positive_native(result["native"]))
            self.assertEqual(
                archive.decode_archive(archive.encode_archive(result))["receipt"][
                    "state"
                ],
                state,
            )
        parts = fixture()
        parts[1] = None
        self.assertIsNone(terminal(parts)["receipt"])

    def test_finalizer_rerun_can_attest_but_publisher_rerun_cannot(self):
        parts = fixture()
        parts[5]["run_attempt"] = 2
        self.assertEqual(terminal(parts)["finalizer"]["run_attempt"], 2)
        parts[2]["run_attempt"] = parts[3]["run_attempt"] = 2
        with self.assertRaises(archive.ArchiveError):
            terminal(parts)

    def test_confirmed_bytes_with_later_native_failure_remain_honest_but_not_positive(
        self,
    ):
        for fault in ("run_failure", "job_failure", "effect_skipped", "effect_missing"):
            parts = fixture()
            if fault == "run_failure":
                parts[2]["conclusion"] = parts[3]["conclusion"] = "failure"
            if fault == "job_failure":
                parts[4][0]["conclusion"] = "failure"
            if fault == "effect_skipped":
                parts[4][0]["steps"][1]["conclusion"] = "skipped"
            if fault == "effect_missing":
                parts[4][0]["steps"].pop()
            value = terminal(parts)
            self.assertEqual(value["receipt"], parts[1])
            self.assertFalse(archive.positive_native(value["native"]))
            self.assertEqual(
                archive.decode_archive(archive.encode_archive(value)), value
            )

    def test_cross_run_source_workflow_receipt_and_bookkeeping_refuse(self):
        for index, key, value in (
            (2, "id", 103),
            (3, "head_sha", "f" * 40),
            (2, "event", "push"),
            (2, "status", "in_progress"),
            (1, "source_sha", "f" * 40),
            (1, "bookkeeping_sha", "f" * 40),
            (1, "version", "0.2.0"),
            (5, "workflow_path", "wrong"),
            (5, "run_attempt", 0),
        ):
            parts = fixture()
            parts[index][key] = value
            with self.subTest(key=key), self.assertRaises(archive.ArchiveError):
                terminal(parts)

    def test_unknown_duplicate_noncanonical_extra_newer_and_oversize_records_refuse(
        self,
    ):
        value = terminal(fixture())
        raw = archive.encode_archive(value)
        bad = [
            raw.replace(b'"schema_version":1', b'"schema_version":2', 1),
            raw.replace(
                b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1
            ),
            json.dumps(value).encode(),
            b" " * (archive.MAX_ARCHIVE_BYTES + 1),
        ]
        extra = copy.deepcopy(value)
        extra["native"]["run"]["untrusted_extra"] = True
        bad.append(
            (json.dumps(extra, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        for data in bad:
            with self.assertRaises(archive.ArchiveError):
                archive.decode_archive(data)

    def test_documented_attempt_endpoint_jobs_may_omit_optional_row_attempt(self):
        parts = fixture()
        del parts[4][0]["run_attempt"]
        value = terminal(parts)
        self.assertEqual(value["native"]["jobs"][0]["run_attempt"], 1)
        self.assertEqual(archive.decode_archive(archive.encode_archive(value)), value)
        parts[4][0]["run_attempt"] = 2
        with self.assertRaises(archive.ArchiveError):
            terminal(parts)

    def test_bounded_unique_jobs_steps_and_exact_identity(self):
        for fault in (
            "duplicates",
            "too_many",
            "wrong_run",
            "wrong_head",
            "duplicate_step",
        ):
            parts = fixture()
            if fault == "duplicates":
                parts[4].append(copy.deepcopy(parts[4][0]))
            if fault == "too_many":
                parts[4] *= archive.MAX_JOBS + 1
            if fault == "wrong_run":
                parts[4][0]["run_id"] = 999
            if fault == "wrong_head":
                parts[4][0]["head_sha"] = "f" * 40
            if fault == "duplicate_step":
                parts[4][0]["steps"].append(copy.deepcopy(parts[4][0]["steps"][0]))
            with self.assertRaises(archive.ArchiveError):
                terminal(parts)


class GitFixture:
    def __init__(self):
        self.parts = fixture()
        self.intent = self.parts[0]
        self.terminal = terminal(self.parts)
        self.routes = {
            "repos/jbaruch/coding-policy": {
                "full_name": "jbaruch/coding-policy",
                "default_branch": "main",
                "archived": False,
                "disabled": False,
            },
            "repos/jbaruch/coding-policy/actions/runs/101": self.parts[2],
            "repos/jbaruch/coding-policy/actions/runs/101/attempts/1": self.parts[3],
            "repos/jbaruch/coding-policy/actions/runs/101/attempts/1/jobs?per_page=100&page=1": {
                "total_count": 1,
                "jobs": self.parts[4],
            },
        }
        self.routes["repos/jbaruch/coding-policy/git/ref/heads/main"] = {
            "ref": "refs/heads/main",
            "object": {"type": "commit", "sha": "e" * 40},
        }
        self.records = {
            ("101", "intent"): self.intent["intent"],
            ("101", "receipt"): self.terminal["receipt"],
        }
        self.calls = []
        self.put(
            archive.ref_name(
                "source",
                repository=self.intent["intent"]["consumer_repository"],
                source_sha="a" * 40,
            ),
            self.intent,
        )
        self.put(archive.ref_name("intent", run_id="101"), self.intent)
        self.put(archive.ref_name("terminal", run_id="101"), self.terminal)

    def put(self, ref, value, *, mode="100644", extra=False):
        raw = archive.encode_archive(value)
        blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        tree_bytes = mode.encode() + b" record.json\0" + bytes.fromhex(blob)
        tree = hashlib.sha1(
            f"tree {len(tree_bytes)}\0".encode() + tree_bytes
        ).hexdigest()
        commit_bytes = f"tree {tree}\nauthor Fixed Fixture <fixture@example.invalid> 1 +0000\ncommitter Fixed Fixture <fixture@example.invalid> 1 +0000\n\nFixed evidence\n".encode()
        commit = hashlib.sha1(
            f"commit {len(commit_bytes)}\0".encode() + commit_bytes
        ).hexdigest()
        root = "repos/jbaruch/coding-policy/git/"
        self.routes[root + "ref/" + ref.removeprefix("refs/")] = {
            "ref": ref,
            "object": {"type": "commit", "sha": commit},
        }
        self.routes[root + "commits/" + commit] = {
            "sha": commit,
            "tree": {"sha": tree},
            "parents": [],
        }
        entries = [{"path": "record.json", "type": "blob", "mode": mode, "sha": blob}]
        if extra:
            entries.append(
                {"path": "extra", "type": "blob", "mode": "100644", "sha": blob}
            )
        self.routes[root + "trees/" + tree] = {
            "sha": tree,
            "truncated": False,
            "tree": entries,
        }
        self.routes[root + "blobs/" + blob] = {
            "sha": blob,
            "encoding": "base64",
            "size": len(raw),
            "content": base64.b64encode(raw).decode(),
        }

    def native_json(self, route):
        self.calls.append(("native", route))
        value = self.routes.get(route)
        if value is None:
            raise archive.FleetPublishLookupError("native_missing", http_status=404)
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def json(self, route):
        return self.native_json(route)

    def artifact_evidence(self, run, kind):
        self.calls.append(("artifact", run, kind))
        value = self.records.get((run, kind))
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def expire(self):
        for route in list(self.routes):
            if "/actions/runs/" in route:
                del self.routes[route]
        for key in self.records:
            self.records[key] = archive.FleetPublishLookupError(
                "native_missing", http_status=404
            )


class ArchiveReaderTests(unittest.TestCase):
    def setUp(self):
        self.path = patch.object(sys, "path", [str(SOURCE), *sys.path])
        self.path.start()
        self.addCleanup(self.path.stop)
        import fleet_publish_producer

        self.protocol = patch.object(fleet_publish_producer, "verify_workflow_protocol")
        self.protocol.start()
        self.addCleanup(self.protocol.stop)
        self.github = GitFixture()
        self.reader = archive.ArchiveReader(self.github)

    def test_expired_artifacts_and_native_metadata_keep_positive_central_completion(
        self,
    ):
        self.github.expire()
        self.assertEqual(
            self.reader.record("101", "intent"), self.github.intent["intent"]
        )
        receipt = self.reader.record("101", "receipt")
        self.assertEqual(receipt, self.github.terminal["receipt"])
        proof = self.reader.verify_record_attempt(receipt, successful=True)
        self.assertTrue(proof["archived"])
        self.assertTrue(archive.positive_native(proof))
        self.assertLess(len(self.github.calls), 70)

    def test_index_absence_requires_current_native_git_access_and_supported_protocol(
        self,
    ):
        route = "repos/jbaruch/coding-policy/git/ref/heads/main"
        self.github.routes[route] = archive.FleetPublishLookupError(
            "native_http_error", http_status=403
        )
        with self.assertRaises(archive.FleetPublishLookupError):
            self.reader.source_claim("jbaruch/nanoclaw-fixture", "f" * 40)
        self.assertFalse(
            any(
                "/sources/" in call[1]
                for call in self.github.calls
                if call[0] == "native"
            )
        )

    def test_caller_record_cannot_substitute_another_receipt_with_genuine_native_success(
        self,
    ):
        forged = {**self.github.terminal["receipt"], "version": "0.2.0"}
        with self.assertRaises(archive.ArchiveError):
            self.reader.verify_record_attempt(forged, successful=True)

    def test_source_index_survives_empty_actions_catalog_and_no_consumer_stamp(self):
        self.github.expire()
        original = self.reader.source_claim("jbaruch/nanoclaw-fixture", "a" * 40)
        self.assertEqual(original, self.github.intent)
        self.assertIsNone(
            self.reader.source_claim("jbaruch/nanoclaw-fixture", "f" * 40)
        )
        self.assertFalse(
            any(
                "consumer" in route
                for kind, route, *_ in self.github.calls
                if kind == "native"
            )
        )

    def test_live_matching_records_and_metadata_are_compared_not_replaced(self):
        receipt = self.reader.record("101", "receipt")
        self.assertFalse(
            self.reader.verify_record_attempt(receipt, successful=True)["archived"]
        )
        self.github.records["101", "receipt"] = {**receipt, "version": "0.2.0"}
        with self.assertRaises(archive.ArchiveError):
            self.reader.record("101", "receipt")

    def test_live_403_transport_malformed_and_native_mismatch_never_fall_back(self):
        for kind in ("native_http_error", "command_timeout", "invalid_json"):
            self.github.records["101", "receipt"] = archive.FleetPublishLookupError(
                kind, http_status=403
            )
            with self.assertRaises(archive.FleetPublishLookupError):
                self.reader.record("101", "receipt")
        self.github.records["101", "receipt"] = self.github.terminal["receipt"]
        self.github.routes["repos/jbaruch/coding-policy/actions/runs/101"][
            "conclusion"
        ] = "failure"
        with self.assertRaises(archive.ArchiveError):
            self.reader.verify_record_attempt(
                self.github.terminal["receipt"], successful=True
            )

    def test_intent_without_terminal_never_becomes_success_after_native_expiry(self):
        ref = archive.ref_name("terminal", run_id="101")
        del self.github.routes[
            "repos/jbaruch/coding-policy/git/ref/" + ref.removeprefix("refs/")
        ]
        self.github.expire()
        self.assertIsNone(self.reader.record("101", "receipt"))
        with self.assertRaises(archive.ArchiveError):
            self.reader.verify_record_attempt(
                self.github.intent["intent"], successful=True
            )

    def test_honest_confirmed_receipt_native_failure_is_retained_but_never_success(
        self,
    ):
        self.github.parts[2]["conclusion"] = self.github.parts[3]["conclusion"] = (
            "failure"
        )
        self.github.parts[4][0]["conclusion"] = "failure"
        negative = terminal(self.github.parts)
        self.github.put(archive.ref_name("terminal", run_id="101"), negative)
        self.github.expire()
        receipt = self.reader.record("101", "receipt")
        self.assertEqual(receipt["state"], "confirmed")
        self.assertFalse(
            archive.positive_native(
                self.reader.verify_record_attempt(receipt, successful=False)
            )
        )
        with self.assertRaises(archive.ArchiveError):
            self.reader.verify_record_attempt(receipt, successful=True)

    def test_durable_hold_prevents_expiry_resurrecting_attempt1(self):
        held = archive.make_hold("101", 2, "c" * 40, finalizer=self.github.parts[5])
        self.github.put(archive.ref_name("hold", run_id="101"), held)
        self.github.expire()
        self.assertEqual(self.reader.run_hold("101"), held)
        with self.assertRaises(archive.ArchiveError):
            self.reader.record("101", "receipt")
        with self.assertRaises(archive.ArchiveError):
            self.reader.verify_record_attempt(
                self.github.terminal["receipt"], successful=True
            )

    def test_ref_deletion_change_linked_extra_or_unknown_protocol_is_never_absence(
        self,
    ):
        ref = archive.ref_name("intent", run_id="101")
        route = "repos/jbaruch/coding-policy/git/ref/" + ref.removeprefix("refs/")
        self.reader.run_intent("101")
        old = self.github.routes.pop(route)
        with self.assertRaises(archive.ArchiveError):
            self.reader.run_intent("101")
        self.github.routes[route] = old
        for mode, extra in (("120000", False), ("100644", True)):
            self.github.put(ref, self.github.intent, mode=mode, extra=extra)
            with self.assertRaises(archive.ArchiveError):
                archive.ArchiveReader(self.github).run_intent("101")
        self.github.put(ref, self.github.intent)
        import fleet_publish_producer

        with (
            patch.object(
                fleet_publish_producer,
                "verify_workflow_protocol",
                side_effect=archive.FleetPublishLookupError("unknown_producer"),
            ),
            self.assertRaises(archive.FleetPublishLookupError),
        ):
            archive.ArchiveReader(self.github).run_intent("101")

    def test_current_pending_native_attempt_can_be_bound_without_a_terminal_record(
        self,
    ):
        ref = archive.ref_name("terminal", run_id="101")
        del self.github.routes[
            "repos/jbaruch/coding-policy/git/ref/" + ref.removeprefix("refs/")
        ]
        for suffix in ("", "/attempts/1"):
            row = self.github.routes[
                "repos/jbaruch/coding-policy/actions/runs/101" + suffix
            ]
            row.update(status="in_progress", conclusion=None)
        proof = self.reader.verify_record_attempt(
            self.github.intent["intent"], successful=False
        )
        self.assertEqual(proof["run"]["status"], "in_progress")
        self.assertFalse(proof["archived"])


if __name__ == "__main__":
    unittest.main()
