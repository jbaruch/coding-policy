"""Prospective central archive owner; deterministic metadata, no network/effects."""

from __future__ import annotations

import copy
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

    def test_confirmed_receipt_never_survives_native_failure_or_skipped_effect(self):
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
            with self.assertRaises(archive.ArchiveError):
                terminal(parts)

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
            (5, "run_attempt", 2),
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


if __name__ == "__main__":
    unittest.main()
