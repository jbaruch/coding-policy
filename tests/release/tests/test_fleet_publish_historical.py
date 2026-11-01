"""Later attestation never becomes missing original receipt bytes."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[3] / "skills/release"
sys.path.insert(0, str(SOURCE))
from test_fleet_publish_archive import fixture, GitFixture  # noqa: E402
import fleet_publish_archive as archive  # noqa: E402
from fleet_publish_github import FleetPublishLookupError  # noqa: E402
import fleet_publish_historical as historical  # noqa: E402
import fleet_publish_producer as producer  # noqa: E402
from fleet_publish_evidence import prior_publication, facts  # noqa: E402
from fleet_publish_record import FleetRecordError, parse_record  # noqa: E402


def proof(envelope=None):
    parts = fixture()
    if envelope is not None:
        parts[0] = copy.deepcopy(envelope)
    original = parts[0]["intent"]
    parts[2].update(
        id=int(original["run_id"]),
        head_sha=original["workflow_sha"],
        display_title=f"fleet-publish-v1:{original['consumer_repository']}:{original['source_sha']}",
    )
    parts[3] = copy.deepcopy(parts[2])
    parts[4][0].update(
        run_id=int(original["run_id"]), head_sha=original["workflow_sha"]
    )
    parts[4][0]["steps"] = [
        {"number": number, "name": name, "status": "completed", "conclusion": "success"}
        for name, number in zip(
            historical.CRITICAL_STEPS, (3, 7, 8, 9, 10, 11, 12), strict=True
        )
    ]
    run = archive.native_run(parts[2])
    descriptor = {"name": original["plugin_name"], "version": original["version"]}
    files = {
        ".tessl-plugin/plugin.json": json.dumps(descriptor).encode(),
        "tile.json": json.dumps(descriptor).encode(),
        "README.md": b"# Synthetic fixture\n",
        "rules/fixture.md": b"# Fixture\n",
    }
    installed = {**files, "tile.json": json.dumps(descriptor, indent=2).encode()}
    return historical.parse_historical(
        {
            "schema_version": 1,
            "kind": historical.KIND,
            "derivation": historical.DERIVATION,
            "intent": parts[0],
            "native": {
                "run": run,
                "attempt": run,
                "jobs": archive.native_jobs(parts[4], run),
            },
            "producer_pinset": producer.HISTORICAL_PRODUCERS[0],
            "registry": {
                "source": historical.describe_files(files),
                "installed": historical.describe_files(installed),
                "installer": {
                    "name": original["plugin_name"],
                    "version": original["version"],
                },
                "fingerprint": "a" * 64,
                "moderation": "pass",
                "version": original["version"],
                "observed_at": "2026-10-27T12:00:00Z",
                "tool": {"name": "tessl", "version": "0.119.0"},
            },
            "attester": parts[5],
        }
    )


def maximum_inventory_proof(path_characters=1000):
    value = proof()
    rows = [
        copy.deepcopy(row)
        for row in value["registry"]["source"]
        if row["path"] in historical.JSON_MEMBERS
    ]
    rows.extend(
        {
            "path": f"z/{index:04d}" + "x" * (path_characters - 6),
            "bytes": 1,
            "sha256": "a" * 64,
            "json_sha256": None,
        }
        for index in range(510)
    )
    value["registry"]["source"] = sorted(rows, key=lambda row: row["path"])
    value["registry"]["installed"] = copy.deepcopy(value["registry"]["source"])
    return value


class HistoricalTests(unittest.TestCase):
    def test_complete_maximum_inventory_exceeding_archive_budget_is_not_schema_valid(
        self,
    ):
        for unicode_paths in (False, True):
            value = maximum_inventory_proof()
            if unicode_paths:
                for rows in (
                    value["registry"]["source"],
                    value["registry"]["installed"],
                ):
                    for row in rows:
                        if row["path"].startswith("z/"):
                            row["path"] = row["path"].replace("x", "\u2603")
            expected = (
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
                + b"\n"
            )
            self.assertGreater(len(expected), archive.MAX_ARCHIVE_BYTES)
            with (
                self.subTest(unicode_paths=unicode_paths),
                self.assertRaises(archive.ArchiveError),
            ):
                historical.parse_historical(value)

    def test_separate_family_roundtrips_without_native_receipt_fields(self):
        value = proof()
        self.assertEqual(archive.decode_archive(archive.encode_archive(value)), value)
        self.assertEqual(
            archive.ref_name("historical-source-proof", run_id="101"),
            "refs/tags/fleet-publish-v1/runs/101/1/historical-source-proof",
        )
        derived = historical.prior_facts(value)
        self.assertEqual(derived["proof_kind"], "historical_source_proof")
        self.assertEqual(derived["attester"]["run_id"], "102")
        for key in (
            "publish_exit_code",
            "publish_outcome",
            "landed_after_error",
            "artifact_digest",
            "published_at",
        ):
            self.assertNotIn(key, derived)
        with self.assertRaises(FleetRecordError):
            parse_record(value)
        with self.assertRaises(FleetRecordError):
            parse_record(derived)

    def test_strict_schema_rejects_unknown_or_invented_fields(self):
        for key in ("receipt", "artifact_sha256", "published_at", "original_finalizer"):
            with self.subTest(key=key), self.assertRaises(archive.ArchiveError):
                historical.parse_historical({**proof(), key: "invented"})
        raw = json.dumps(proof())
        with self.assertRaises(archive.ArchiveError):
            archive.decode_archive(
                raw.replace(
                    '"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1
                ).encode()
            )

    def test_negative_critical_steps_reruns_and_unknown_closure_refuse(self):
        for change in (
            "failed",
            "skipped",
            "missing",
            "number",
            "rerun",
            "unknown-owner",
            "unknown-derivation",
        ):
            value = proof()
            if change in ("failed", "skipped"):
                value["native"]["jobs"][0]["steps"][2]["conclusion"] = change
            elif change == "missing":
                value["native"]["jobs"][0]["steps"].pop(2)
            elif change == "number":
                value["native"]["jobs"][0]["steps"][0]["number"] = 2
            elif change == "rerun":
                value["native"]["run"]["run_attempt"] = 2
            elif change == "unknown-owner":
                value["producer_pinset"][producer.PRODUCER_OWNER_PATH] = "f" * 40
            else:
                value["derivation"] = "arbitrary"
            with self.subTest(change=change), self.assertRaises(archive.ArchiveError):
                historical.parse_historical(value)

    def test_whole_package_and_full_json_values_bind_every_member(self):
        for change in (
            "missing",
            "extra",
            "bytes",
            "json",
            "fingerprint",
            "moderation",
            "installer",
        ):
            value = proof()
            registry = value["registry"]
            if change == "missing":
                registry["installed"].pop()
            elif change == "extra":
                registry["installed"].append(
                    {**registry["installed"][-1], "path": "z-extra"}
                )
            elif change == "bytes":
                next(
                    row for row in registry["installed"] if row["path"] == "README.md"
                )["sha256"] = "f" * 64
            elif change == "json":
                registry["installed"][0]["json_sha256"] = "f" * 64
            elif change == "fingerprint":
                registry["fingerprint"] = "unknown"
            elif change == "moderation":
                registry["moderation"] = "pending"
            else:
                registry["installer"]["extra"] = "unknown"
            with self.subTest(change=change), self.assertRaises(archive.ArchiveError):
                historical.parse_historical(value)
        for data in (b'{"name":1,"name":2}', b'{"name":NaN}'):
            with self.assertRaises(FleetPublishLookupError):
                historical.describe_files({"tile.json": data})

    def test_durable_proof_unlocks_only_typed_prior_and_leaves_receipt_missing(self):
        github = GitFixture()
        value = proof()
        github.records[("101", "receipt")] = None
        terminal_ref = archive.ref_name("terminal", run_id="101")
        del github.routes[
            "repos/jbaruch/coding-policy/git/ref/" + terminal_ref.removeprefix("refs/")
        ]
        # Match complete original native facts; no receipt was ever reconstructed.
        github.routes[
            "repos/jbaruch/coding-policy/actions/runs/101/attempts/1/jobs?per_page=100&page=1"
        ]["jobs"] = [
            {**job, "id": int(job["id"]), "run_id": int(job["run_id"])}
            for job in value["native"]["jobs"]
        ]
        with patch.object(
            producer,
            "verify_workflow_protocol",
            return_value=copy.deepcopy(producer.HISTORICAL_PRODUCERS[0]),
        ):
            reader = archive.ArchiveReader(github)
            self.assertIsNone(prior_publication(reader, "101"))
            github.put(archive.ref_name("historical-source-proof", run_id="101"), value)
            result = prior_publication(reader, "101")
            self.assertIsNotNone(result)
            if result is None:
                self.fail("Verified later proof must supply typed prior continuity.")
            self.assertEqual(result["proof_kind"], "historical_source_proof")
            self.assertEqual(facts(result)["source_sha"], "a" * 40)
            self.assertIsNone(reader.record("101", "receipt"))
            self.assertIsNone(reader.terminal("101"))
            # Expired native metadata can retain positive intent continuity;
            # original receipt access remains absent rather than reconstructed.
            old_routes = copy.deepcopy(github.routes)
            old_records = copy.deepcopy(github.records)
            github.expire()
            attempt = reader.verify_record_attempt(
                value["intent"]["intent"], successful=False
            )
            self.assertEqual(attempt["run"], value["native"]["run"])
            self.assertIsNone(reader.record("101", "receipt"))
            github.routes, github.records = old_routes, old_records
            github.routes["repos/jbaruch/coding-policy/actions/runs/101"][
                "run_attempt"
            ] = 2
            with self.assertRaises(archive.ArchiveError):
                prior_publication(reader, "101")


if __name__ == "__main__":
    unittest.main()
