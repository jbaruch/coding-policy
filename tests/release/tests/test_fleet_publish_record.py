"""Fixed record fixtures prove data validation, never publish authorization."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[3] / "skills/release/fleet_publish_record.py"
SPEC = importlib.util.spec_from_file_location("fleet_publish_record", PATH)
assert SPEC is not None and SPEC.loader is not None
owner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(owner)


def claimed() -> dict[str, object]:
    return {
        "schema_version": 1,
        "consumer_repository": "jbaruch/nanoclaw-construction",
        "source_sha": "a" * 40,
        "source_ref": "refs/heads/main",
        "source_tree_sha": "b" * 40,
        "workflow_repository": "jbaruch/coding-policy",
        "workflow_sha": "c" * 40,
        "run_id": "123456789",
        "run_attempt": 1,
        "plugin_name": "jbaruch/nanoclaw-construction",
        "state": "claimed",
        "registry_baseline": "unpublished",
        "version": None,
        "publish_outcome": "not_started",
        "publish_exit_code": None,
        "landed_after_error": False,
        "moderation": "not_checked",
        "origin_run_id": "123456789",
        "origin_run_attempt": 1,
        "bookkeeping_sha": None,
    }


def confirmed() -> dict[str, object]:
    return {
        **claimed(),
        "state": "confirmed",
        "version": "0.1.0",
        "publish_outcome": "success",
        "publish_exit_code": 0,
        "moderation": "pass",
        "bookkeeping_sha": "d" * 40,
    }


class FleetRecordTests(unittest.TestCase):
    def test_retains_exact_claimed_data_without_mutable_alias(self) -> None:
        original = claimed()
        parsed = owner.parse_record(original)
        self.assertEqual(parsed, original)
        self.assertIsNot(parsed, original)
        self.assertEqual(parsed["state"], "claimed")

    def test_positive_record_is_only_structural_until_native_binding(self) -> None:
        original = confirmed()
        self.assertEqual(owner.parse_record(original), original)

    def test_recovery_origin_is_retained_without_claiming_it_is_current_run(
        self,
    ) -> None:
        original = {**confirmed(), "run_id": "234567890"}
        self.assertNotEqual(original["run_id"], original["origin_run_id"])
        self.assertEqual(owner.parse_record(original), original)

    def test_every_required_field_and_unknown_schema_fail_closed(self) -> None:
        for key in claimed():
            with self.subTest(missing=key):
                value = claimed()
                del value[key]
                with self.assertRaises(owner.FleetRecordError):
                    owner.parse_record(value)
        for value in (
            {**claimed(), "schema_version": 2},
            {**claimed(), "approved": True},
        ):
            with self.assertRaises(owner.FleetRecordError):
                owner.parse_record(value)

    def test_invalid_identity_and_scalar_types_refuse_without_echo(self) -> None:
        cases = {
            "schema_version": [True, "1"],
            "consumer_repository": [
                "other/consumer",
                "jbaruch/../PRIVATE",
                "https://example.test/PRIVATE",
            ],
            "source_sha": ["PRIVATE", "a" * 39],
            "source_tree_sha": [None, "PRIVATE"],
            "source_ref": [
                "main",
                "refs/tags/v0.1.0",
                "refs/heads/",
                "refs/heads/PRIVATE\n",
                "refs/heads/\ud800",
            ],
            "workflow_repository": ["jbaruch/PRIVATE", "other/coding-policy"],
            "workflow_sha": [None, "PRIVATE"],
            "run_id": [123456789, "0", "-1", "1.0", "PRIVATE"],
            "run_attempt": [False, 0, -1, "1", 1.5],
            "plugin_name": ["other/plugin", "jbaruch/PRIVATE/extra"],
            "state": ["accepted", None, {}],
            "registry_baseline": [False, "0.01.0", "PRIVATE"],
            "version": [True, "0.1", "01.0.0", "0.1.0-01", "PRIVATE"],
            "publish_outcome": [None, "accepted", []],
            "publish_exit_code": [True, "0", 0.5],
            "landed_after_error": [0, "false", None],
            "moderation": [True, "approved"],
            "origin_run_id": [False, "0", "PRIVATE"],
            "origin_run_attempt": [False, 0, "1"],
            "bookkeeping_sha": [False, "PRIVATE"],
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value_type=type(value).__name__):
                    with self.assertRaises(owner.FleetRecordError) as error:
                        owner.parse_record({**claimed(), key: value})
                    self.assertNotIn("PRIVATE", str(error.exception))

    def test_confirmation_requires_all_positive_native_result_fields(self) -> None:
        for field, value in (
            ("publish_outcome", "failure"),
            ("publish_exit_code", 1),
            ("publish_exit_code", None),
            ("landed_after_error", True),
            ("moderation", "pending"),
            ("moderation", "fail"),
            ("version", None),
            ("bookkeeping_sha", None),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(owner.FleetRecordError):
                    owner.parse_record({**confirmed(), field: value})

    def test_uncertain_blocked_and_published_facts_do_not_turn_confirmed(self) -> None:
        for state in ("uncertain", "blocked", "published"):
            value = {
                **confirmed(),
                "state": state,
                "publish_outcome": "unknown",
                "publish_exit_code": None,
                "landed_after_error": True,
                "moderation": "unknown",
            }
            self.assertEqual(owner.parse_record(value)["state"], state)

    def test_accepts_semver_without_loosening_numeric_components(self) -> None:
        for version in ("0.0.0", "1.2.3-rc.1", "1.2.3+build.001"):
            self.assertTrue(owner.valid_version(version))
        for version in ("01.0.0", "1.2.03", "1.2.3-01", "1.2.3+", "1.2.3.4"):
            self.assertFalse(owner.valid_version(version))

    def test_decoder_refuses_duplicates_nonfinite_invalid_utf8_and_bounds(self) -> None:
        good = json.dumps(claimed()).encode()
        self.assertEqual(owner.decode_record(good), claimed())
        for raw in (
            good.replace(
                b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1'
            ),
            b"NaN",
            b"[" * 5000,
            b"9" * 5000,
            b"\xff",
            b"x" * (owner.MAX_RECORD_BYTES + 1),
        ):
            with self.subTest(prefix=raw[:1]):
                with self.assertRaises(owner.FleetRecordError):
                    owner.decode_record(raw)


if __name__ == "__main__":
    unittest.main()
