"""Source-confirmed receipt must still match live exact registry evidence."""

from __future__ import annotations

import json
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[3] / "skills/release"
sys.path.insert(0, str(SOURCE))
import fleet_publish_confirm as confirmation  # noqa: E402 - co-shipped provider.
from fleet_publish_record import parse_record  # noqa: E402 - schema owner.


def record():
    return parse_record(
        {
            "schema_version": 1,
            "consumer_repository": "jbaruch/nanoclaw-construction",
            "source_sha": "a" * 40,
            "source_ref": "refs/heads/main",
            "source_tree_sha": "b" * 40,
            "workflow_repository": "jbaruch/coding-policy",
            "workflow_sha": "c" * 40,
            "run_id": "123",
            "run_attempt": 1,
            "plugin_name": "jbaruch/nanoclaw-construction",
            "state": "confirmed",
            "registry_baseline": "0.1.0",
            "version": "0.1.1",
            "publish_outcome": "success",
            "publish_exit_code": 0,
            "landed_after_error": False,
            "moderation": "pass",
            "origin_run_id": "123",
            "origin_run_attempt": 1,
            "bookkeeping_sha": "d" * 40,
        }
    )


class Readers:
    def __init__(self):
        self.calls = []
        self.outputs = [
            (
                0,
                {
                    "ok": True,
                    "run_conclusion": "success",
                    "pre": "0.1.0",
                    "current": "0.1.2",
                },
            ),
            (0, {"exists": True, "version": "0.1.1"}),
            (0, {"ok": True, "version": "0.1.1", "moderation_status": "pass"}),
        ]

    def __call__(self, *arguments):
        self.calls.append(arguments)
        code, data = self.outputs[len(self.calls) - 1]
        return subprocess.CompletedProcess(
            arguments, code, json.dumps(data).encode(), b"private diagnostic"
        )


class ConfirmationTests(unittest.TestCase):
    def test_historical_opt_in_returns_schema2_and_new_attester_after_same_live_registry_gates(
        self,
    ):
        from test_fleet_publish_historical import proof
        from fleet_publish_historical import prior_facts
        from fleet_publish_evidence import HistoricalPublication

        historical = proof()
        readers = Readers()
        readers.outputs[0] = (0, {"version": "0.1.2"})
        readers.outputs[1][1]["version"] = "0.1.0"
        readers.outputs[2][1]["version"] = "0.1.0"
        value: HistoricalPublication = {
            "proof_kind": "historical_source_proof",
            "proof": historical,
            "facts": prior_facts(historical),
        }
        result = confirmation.confirm_evidence(value, "unpublished", readers)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["proof_kind"], "historical_source_proof")
        self.assertEqual(result["origin_run_id"], "101")
        self.assertEqual(result["attester"]["run_id"], "102")
        self.assertEqual(len(readers.calls), 3)
        self.assertEqual(readers.calls[0][0], "registry-version.sh")
        self.assertNotIn("record", result)
        value["facts"]["source_sha"] = "f" * 40
        with self.assertRaises(confirmation.ConfirmationHold):
            confirmation.confirm_evidence(value, "unpublished", readers)
        self.assertEqual(len(readers.calls), 3)

    def test_historical_confirmation_survives_expired_original_run(self):
        from test_fleet_publish_historical import proof
        from fleet_publish_historical import prior_facts
        from fleet_publish_evidence import HistoricalPublication

        historical = proof()
        value: HistoricalPublication = {
            "proof_kind": "historical_source_proof",
            "proof": historical,
            "facts": prior_facts(historical),
        }
        calls = []

        def registry_only(*arguments):
            calls.append(arguments)
            name = arguments[0]
            if name == "verify-publish-landed.sh":
                # This original native run is gone; it cannot supply a new
                # positive conclusion after the durable owner verified proof.
                return subprocess.CompletedProcess(arguments, 2, b"", b"run 404")
            outputs = {
                "registry-version.sh": {"version": "0.1.2"},
                "registry-has-version.sh": {"exists": True, "version": "0.1.0"},
                "verify-moderation-cleared.sh": {
                    "ok": True, "version": "0.1.0", "moderation_status": "pass"
                },
            }
            return subprocess.CompletedProcess(
                arguments, 0, json.dumps(outputs[name]).encode(), b""
            )

        result = confirmation.confirm_evidence(value, "unpublished", registry_only)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["registry_latest"], "0.1.2")
        self.assertEqual([call[0] for call in calls], [
            "registry-version.sh", "registry-has-version.sh",
            "verify-moderation-cleared.sh",
        ])

    def test_existing_four_argument_callers_remain_native_receipt_schema1_only(self):
        with (
            patch.object(confirmation, "GitHubClient"),
            patch.object(confirmation, "resolve", return_value=record()) as native,
            patch.object(
                confirmation,
                "resolve_evidence",
                side_effect=AssertionError(
                    "Old caller cannot request derived evidence"
                ),
            ),
            patch.object(
                confirmation,
                "confirm_registry",
                return_value={"schema_version": 1, "confirmed": True},
            ),
            patch.object(sys, "stdout", io.StringIO()) as output,
        ):
            self.assertEqual(
                confirmation.main(
                    ["jbaruch/nanoclaw-construction", "a" * 40, "0.1.0", "123"]
                ),
                0,
            )
            self.assertEqual(json.loads(output.getvalue())["schema_version"], 1)
            native.assert_called_once()

    def test_exact_version_confirmed_even_after_later_registry_publish(self):
        readers = Readers()
        result = confirmation.confirm_registry(record(), "0.1.0", readers)
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(result["registry_latest"], "0.1.2")
        self.assertTrue(result["confirmed"])
        self.assertEqual(readers.calls[1][-1], "0.1.1")
        self.assertEqual(readers.calls[2][-1], "0.1.1")

    def test_first_publication_requires_verified_unpublished_baselines(self):
        value = record()
        value["registry_baseline"] = "unpublished"
        readers = Readers()
        readers.outputs[0][1]["pre"] = "unpublished"
        self.assertTrue(
            confirmation.confirm_registry(value, "unpublished", readers)["confirmed"]
        )

    def test_failed_or_uncertain_receipt_never_reads_registry(self):
        for changes in [
            {"state": "uncertain"},
            {"publish_exit_code": 1},
            {"landed_after_error": True},
        ]:
            with self.subTest(changes=changes):
                value = record()
                value.update(changes)
                readers = Readers()
                with self.assertRaises(confirmation.ConfirmationHold):
                    confirmation.confirm_registry(value, "0.1.0", readers)
                self.assertEqual(readers.calls, [])

    def test_interleaved_registry_advance_does_not_replace_missing_exact_version(self):
        readers = Readers()
        readers.outputs[1] = (0, {"exists": False, "version": "0.1.1"})
        with self.assertRaises(confirmation.ConfirmationFailed):
            confirmation.confirm_registry(record(), "0.1.0", readers)
        self.assertEqual(len(readers.calls), 2)

    def test_wrong_exact_version_or_moderation_version_refuses(self):
        for index in [1, 2]:
            readers = Readers()
            readers.outputs[index][1]["version"] = "0.1.2"
            with self.assertRaises(confirmation.ConfirmationHold):
                confirmation.confirm_registry(record(), "0.1.0", readers)

    def test_publisher_baseline_cannot_precede_captured_before_merge_baseline(self):
        readers = Readers()
        with self.assertRaises(confirmation.ConfirmationHold):
            confirmation.confirm_registry(record(), "0.1.2", readers)
        self.assertEqual(readers.calls, [])

    def test_first_publish_record_cannot_hide_known_previous_registry_version(self):
        value = record()
        value["registry_baseline"] = "unpublished"
        with self.assertRaises(confirmation.ConfirmationHold):
            confirmation.confirm_registry(value, "0.1.0", Readers())

    def test_latest_cannot_be_behind_exact_attempted_version(self):
        readers = Readers()
        readers.outputs[0][1]["current"] = "0.1.0"
        with self.assertRaises(confirmation.ConfirmationHold):
            confirmation.confirm_registry(record(), "0.1.0", readers)

    def test_definitive_helper_failure_distinct_from_tool_failure(self):
        for code, expected in [
            (1, confirmation.ConfirmationFailed),
            (2, confirmation.ConfirmationHold),
        ]:
            readers = Readers()
            readers.outputs[2] = (code, {})
            with self.assertRaises(expected):
                confirmation.confirm_registry(record(), "0.1.0", readers)

    def test_helper_uses_fixed_central_repository_without_stdout_leak(self):
        completed = subprocess.CompletedProcess(
            [], 2, b"private output", b"private error"
        )
        with patch.object(
            confirmation.subprocess, "run", return_value=completed
        ) as run:
            result = confirmation.helper(
                "registry-has-version.sh", "jbaruch", "nanoclaw-construction", "0.1.1"
            )
        self.assertIs(result, completed)
        self.assertEqual(
            run.call_args.kwargs["env"]["GH_REPO"], "jbaruch/coding-policy"
        )
        self.assertEqual(
            run.call_args.args[0][1], str(SOURCE / "registry-has-version.sh")
        )

    def test_cli_validation_precedes_any_live_read(self):
        with patch.object(confirmation, "GitHubClient") as github:
            for arguments in [
                [],
                ["r", "s", "0.1.0", "0"],
                ["r", "s", "secret-value", "123"],
            ]:
                with self.assertRaises(confirmation.ConfirmationHold):
                    confirmation.main(arguments)
            github.assert_not_called()


if __name__ == "__main__":
    unittest.main()
