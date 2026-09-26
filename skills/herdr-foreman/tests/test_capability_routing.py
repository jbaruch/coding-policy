"""Tier selection reads the capability table (#520)."""

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import capabilities
from foreman.errors import UsageError
from foreman.herdr import HerdrClient
from foreman.tiers import JUDGMENT_ROUNDS, ROLE_ROUNDS
from tests.fakes import FakeRunner
from tests.test_cli import CliCase, CONFIG
from tests.tier_fixture import AT

#: Every capability name the live table carried when #520 was filed. Routing
#: must read the names consultations actually record, not a parallel set.
RECORDED_IN_THE_LIVE_TABLE = (
    "advisory-synthesis", "causal-investigation", "context-window-1m", "implementation",
    "independent-defect-detection", "mechanical-execution", "pinned-judge-launch",
    "release-adjudication", "rotating-worker-judgment-tier", "report-verdict-classification",
)


def entry(model, effort, capability, verdict, kind="project"):
    return {"schema_version": 1, "model": model, "effort": effort, "capability": capability, "verdict": verdict,
            "source": {"kind": kind, "ref": "fixture", "dated": "2026-09-23"},
            "recorded_at": "2026-09-23T00:00:00+00:00"}


def table(*entries):
    return {"schema_version": 1, "refreshed_at": "2026-09-23T00:00:00+00:00", "entries": list(entries)}


class VocabularyTest(unittest.TestCase):
    def test_the_owned_vocabulary_is_the_live_tables(self):
        self.assertEqual(capabilities.VOCABULARY, frozenset(RECORDED_IN_THE_LIVE_TABLE))

    def test_every_round_a_role_can_run_needs_at_least_one_owned_capability(self):
        for role, rounds in ROLE_ROUNDS.items():
            for round_type in rounds:
                with self.subTest(role=role, round_type=round_type):
                    needs = capabilities.required(role, round_type, JUDGMENT_ROUNDS)
                    self.assertTrue(needs)
                    self.assertLessEqual(set(needs), capabilities.VOCABULARY - capabilities.RECORDED_ONLY)

    def test_a_consultation_needs_what_its_role_does(self):
        self.assertEqual(capabilities.required("investigator", "consultation", JUDGMENT_ROUNDS), ("causal-investigation",))
        self.assertEqual(capabilities.required("advisor", "consultation", JUDGMENT_ROUNDS), ("advisory-synthesis",))
        self.assertEqual(capabilities.required("reviewer", "review", JUDGMENT_ROUNDS),
                         ("rotating-worker-judgment-tier", "independent-defect-detection"))

    def test_a_capability_of_the_wrong_type_is_a_usage_error_not_a_traceback(self):
        for value in ([], {}, 3, None):
            with self.subTest(value=value), self.assertRaises(UsageError):
                capabilities.record("/nonexistent/state.json", {"entries": [
                    {"model": "m", "effort": "high", "capability": value, "verdict": "unknown",
                     "source": {"kind": "vendor", "ref": "x", "dated": "2026-09-23"}}]}, AT)

    def test_recording_a_name_routing_does_not_own_is_refused(self):
        with self.assertRaisesRegex(UsageError, "not one routing reads"):
            capabilities.record("/nonexistent/state.json", {"entries": [
                {"model": "m", "effort": "high", "capability": "binding-judgment", "verdict": "unknown",
                 "source": {"kind": "vendor", "ref": "x", "dated": "2026-09-23"}}]}, AT)


class AssessTest(unittest.TestCase):
    NEEDS = ("rotating-worker-judgment-tier", "independent-defect-detection")

    def test_an_inadequate_entry_refuses_and_cites_its_source(self):
        document = table(entry("gpt-6-astra", "high", "rotating-worker-judgment-tier", "inadequate"))
        with self.assertRaises(capabilities.InadequateCapability) as caught:
            capabilities.assess(document, "gpt-6-astra", "high", self.NEEDS)
        self.assertEqual(caught.exception.code, "capability_inadequate")
        for fragment in ("gpt-6-astra", "high", "rotating-worker-judgment-tier", "project", "2026-09-23"):
            self.assertIn(fragment, caught.exception.message)

    def test_a_missing_table_or_entry_is_unknown(self):
        self.assertEqual(capabilities.assess(capabilities.empty(), "opus-5", "high", self.NEEDS), "unknown")
        partial = table(entry("opus-5", "high", "independent-defect-detection", "adequate"))
        self.assertEqual(capabilities.assess(partial, "opus-5", "high", self.NEEDS), "unknown")

    def test_only_supported_adequate_entries_count(self):
        both = table(entry("opus-5", "high", self.NEEDS[0], "adequate"), entry("opus-5", "high", self.NEEDS[1], "adequate"))
        self.assertEqual(capabilities.assess(both, "opus-5", "high", self.NEEDS), "adequate")
        # A hand-edited vendor `adequate` never counts, whatever the file claims.
        forged = copy.deepcopy(both)
        forged["entries"][0]["source"]["kind"] = "vendor"
        self.assertEqual(capabilities.assess(forged, "opus-5", "high", self.NEEDS), "unknown")

    def test_a_model_without_an_effort_reads_the_default_effort_row(self):
        document = table(entry("claude-haiku-4-5", "default", "mechanical-execution", "inadequate"))
        with self.assertRaises(capabilities.InadequateCapability):
            capabilities.assess(document, "claude-haiku-4-5", None, ("mechanical-execution",))


class RoutingTest(CliCase):
    def setUp(self):
        super().setUp()
        self.settings = copy.deepcopy(CONFIG)
        self.settings["schema_version"] = 2
        self.settings["agents"] = self.settings["agents"][:1]
        self.settings["agents"][0]["tiers"] = {
            "build": {"model": "opus-5", "effort": "high", "multiplier": 3.0},
            "fix": {"model": "sonnet-5", "effort": "high", "multiplier": 1.0},
        }
        self.config.write_text(json.dumps(self.settings), encoding="utf-8")

    def record(self, *entries):
        capabilities.storage_path(self.state).write_text(json.dumps(table(*entries)), encoding="utf-8")

    def plan(self):
        rc, output, error = self.run_cli(["plan", *self.base(), "--roles", "developer",
                                          "--snapshot", str(self.snapshot), "--now", AT])
        document: Any = json.loads(output) if output.strip() else None
        return rc, document, error

    def test_a_plan_without_a_table_keeps_the_row_and_says_unknown(self):
        rc, document, error = self.plan()
        self.assertEqual(rc, 0, error)
        tier = document["tiers"]["developer"]
        self.assertEqual((tier["model"], tier["capability"], tier["cheaper_adequate"]), ("opus-5", "unknown", None))

    def test_a_cheaper_adequate_row_is_recorded_and_never_selected(self):
        self.record(entry("opus-5", "high", "implementation", "adequate"),
                    entry("sonnet-5", "high", "implementation", "adequate"))
        rc, document, error = self.plan()
        self.assertEqual(rc, 0, error)
        tier = document["tiers"]["developer"]
        self.assertEqual((tier["model"], tier["capability"]), ("opus-5", "adequate"))
        self.assertEqual(tier["cheaper_adequate"], {
            "model": "sonnet-5", "effort": "high", "tier_row": "fix",
            "sources": [{"capability": "implementation", "kind": "project", "ref": "fixture", "dated": "2026-09-23"}]})

    def test_a_cheaper_adequate_row_is_recorded_even_when_the_selected_row_is_unknown(self):
        self.record(entry("sonnet-5", "high", "implementation", "adequate"))
        rc, document, error = self.plan()
        self.assertEqual(rc, 0, error)
        tier = document["tiers"]["developer"]
        self.assertEqual((tier["capability"], tier["cheaper_adequate"]["model"]), ("unknown", "sonnet-5"))

    def test_a_cheaper_row_the_role_cannot_run_is_never_named(self):
        # `release_mechanics` is cheaper and adequately evidenced, but a developer can never run it.
        self.settings["agents"][0]["tiers"]["release_mechanics"] = {"model": "sonnet-5", "effort": "medium", "multiplier": 0.5}
        self.settings["agents"][0]["tiers"]["fix"] = {"model": "opus-5", "effort": "high", "multiplier": 3.0}
        self.config.write_text(json.dumps(self.settings), encoding="utf-8")
        self.record(entry("opus-5", "high", "implementation", "adequate"),
                    entry("sonnet-5", "medium", "implementation", "adequate"))
        rc, document, error = self.plan()
        self.assertEqual(rc, 0, error)
        self.assertIsNone(document["tiers"]["developer"]["cheaper_adequate"])

    def test_an_inadequate_candidate_is_not_planned_and_the_plan_says_why(self):
        self.record(entry("opus-5", "high", "implementation", "inadequate"))
        rc, _, error = self.plan()
        self.assertEqual(rc, 1)
        self.assertIn("No tier is eligible", error)
        self.assertIn("inadequate for implementation", error)
        self.assertEqual(json.loads(error)["details"]["capability_refusals"][0]["capability"], "implementation")

    def test_apply_refuses_an_assigned_worker_the_table_now_records_inadequate(self):
        rc, document, error = self.plan()
        self.assertEqual(rc, 0, error)
        self.record(entry("opus-5", "high", "implementation", "inadequate"))
        runner = FakeRunner()
        rc, _output, error = self.run_cli(
            ["apply", *self.base(), "--assignments", json.dumps(document), "--common", str(self.common),
             *self.brief_args("developer"), "--now", AT, "--composer-settle", "0"],
            client=HerdrClient("herdr", runner))
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(error)["error"], "capability_inadequate")
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
