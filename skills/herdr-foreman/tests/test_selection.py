"""Every plan records why each assignment got its model and effort (#602)."""

import copy
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import capabilities
from foreman.planner import PLAN_SCHEMA_VERSION
from foreman.tiers import BUILD_FAILED_GATES, PRESSURE_HEADROOM_PCT, REVIEW_ROW_FIX_ROUND, escalation_conditions
from tests.test_capability_routing import entry, table
from tests.test_cli import CliCase, CONFIG
from tests.tier_fixture import AT


def fired(result):
    return {row["field"]: row["fired"] for row in result["conditions"]}


class EscalationConditionsTest(unittest.TestCase):
    def tier(self, round_type, kind="claude", tier_row=None, de_escalated=False):
        return {"round": round_type, "tier_row": tier_row or round_type, "kind": kind, "de_escalated": de_escalated}

    def test_a_build_names_every_condition_and_which_fired(self):
        result = escalation_conditions("developer", self.tier("build"),
                                       {"failed_gates": BUILD_FAILED_GATES, "risk_flags": ["a", "b"]}, None)
        self.assertEqual(fired(result), {"failed_gates": True, "fix_round": False, "risk_flags": True,
                                         "input_bytes": False, "prior_high_miss": False})
        gates = result["conditions"][0]
        self.assertEqual((gates["value"], gates["effect"]), (BUILD_FAILED_GATES, {"tier_row": "review"}))
        self.assertEqual(result["conditions"][2]["effect"], {"model": "top", "effort": "xhigh"})
        self.assertEqual(result["pressure"], {"declines_at_or_below_pct": PRESSURE_HEADROOM_PCT, "de_escalated": False})

    def test_a_late_fix_round_fires_the_review_row(self):
        result = escalation_conditions("developer", self.tier("fix"), {}, REVIEW_ROW_FIX_ROUND)
        self.assertTrue(fired(result)["fix_round"])
        self.assertNotIn("failed_gates", fired(result))

    def test_a_kind_without_a_higher_effort_escalates_the_model_alone(self):
        result = escalation_conditions("developer", self.tier("build", kind="grok"), {}, None)
        self.assertEqual(result["conditions"][-1]["effect"], {"model": "top"})

    def test_a_judgment_round_is_never_declined_under_pressure(self):
        result = escalation_conditions("reviewer", self.tier("review"), {}, None)
        self.assertIsNone(result["pressure"]["declines_at_or_below_pct"])

    def test_a_consultation_names_the_evidence_that_moves_it(self):
        result = escalation_conditions("investigator", self.tier("reconciliation"), {"diagnosis_input": True}, None)
        consult = [row for row in result["conditions"] if row["effect"] == {"round": "reconciliation"}]
        self.assertEqual([(row["field"], row["fired"]) for row in consult],
                         [("diagnosis_input", True), ("prior_high_miss", False)])

    def test_the_pinned_judge_escalates_on_nothing(self):
        result = escalation_conditions("judge", {"round": "judge", "tier_row": "judge"}, {"prior_high_miss": True}, 9)
        self.assertEqual(result, {"conditions": [], "pressure": {"declines_at_or_below_pct": None, "de_escalated": False}})


class PlanSelectionTest(CliCase):
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

    def plan(self, *extra):
        self.out, self.err = io.StringIO(), io.StringIO()
        rc, output, error = self.run_cli(["plan", *self.base(), "--roles", "developer",
                                          "--snapshot", str(self.snapshot), "--now", AT, *extra])
        self.assertEqual(rc, 0, error)
        document: Any = json.loads(output)
        return document

    def test_the_plan_records_the_selection_for_its_assignment(self):
        self.record(entry("opus-5", "high", "implementation", "adequate"),
                    entry("sonnet-5", "high", "implementation", "inadequate"))
        document = self.plan()
        self.assertEqual(document["schema_version"], PLAN_SCHEMA_VERSION)
        record = document["selection"]["developer"]
        self.assertEqual((record["agent"], record["tiered"], record["model"], record["effort"], record["tier_row"]),
                         ("claude", True, "opus-5", "high", "build"))
        self.assertEqual(record["required_capabilities"], {"model": ["implementation"], "worker": []})
        self.assertEqual(record["capability"], "adequate")
        self.assertEqual(record["evidence"], [{"capability": "implementation", "verdict": "adequate",
                                               "source": {"kind": "project", "ref": "fixture", "dated": "2026-09-23"}}])
        # No isolated billing evidence: the cost is unknown, never assumed.
        self.assertEqual(record["cost"], {"billing_window": "unknown", "effective_multiplier": 3.0, "known": False})
        cheaper = record["cheaper"]
        self.assertIsNone(cheaper["floor"])
        self.assertEqual([(row["tier_row"], row["model"], row["verdict"]) for row in cheaper["candidates"]],
                         [("fix", "sonnet-5", "inadequate")])
        self.assertIn("conditions", record["escalation"])

    def test_the_successor_is_a_top_model_so_only_cheaper_families_are_barred_from_a_judgment_seat(self):
        # coding-policy#733: the floor audit reads the same TOP_MODELS the
        # parser does, so the successor row is held to the judgment floor and
        # the cheaper family row stays barred.
        tiers = self.settings["agents"][0]["tiers"]
        tiers["hostile_verify"] = {"model": "claude-opus-5-5", "effort": "high", "multiplier": 3.0}
        tiers["recheck"] = {"model": "claude-opus-5-5", "effort": "xhigh", "multiplier": 1.0}
        tiers["test_plan"] = {"model": "claude-sonnet-5-5", "effort": "high", "multiplier": 1.0}
        self.config.write_text(json.dumps(self.settings), encoding="utf-8")
        self.out, self.err = io.StringIO(), io.StringIO()
        rc, output, error = self.run_cli(["plan", *self.base(), "--roles", "tester",
                                          "--snapshot", str(self.snapshot), "--now", AT])
        self.assertEqual(rc, 0, error)
        record: Any = json.loads(output)["selection"]["tester"]
        self.assertEqual((record["model"], record["effort"]), ("claude-opus-5-5", "high"))
        self.assertEqual(record["cheaper"]["floor"], "judgment_round")
        self.assertEqual({row["tier_row"]: row["barred_by_floor"] for row in record["cheaper"]["candidates"]},
                         {"recheck": False, "test_plan": True})

    def test_a_cheaper_row_without_evidence_reads_unknown_with_no_source(self):
        record = self.plan()["selection"]["developer"]
        self.assertEqual(record["evidence"], [{"capability": "implementation", "verdict": "unknown", "source": None}])
        self.assertEqual([(row["verdict"], row["sources"]) for row in record["cheaper"]["candidates"]], [("unknown", [])])

    def test_round_context_reaches_the_escalation_record(self):
        context = self.tmp / "context.json"
        context.write_text(json.dumps({"developer": {"input_bytes": 1}}), encoding="utf-8")
        record = self.plan("--round-context", str(context))["selection"]["developer"]
        self.assertEqual(fired(record["escalation"])["input_bytes"], False)
        context.write_text(json.dumps({"developer": {"prior_high_miss": True}}), encoding="utf-8")
        self.settings["agents"][0]["tiers"]["review"] = {"model": "opus-5", "effort": "high", "multiplier": 3.0}
        self.config.write_text(json.dumps(self.settings), encoding="utf-8")
        record = self.plan("--round-context", str(context))["selection"]["developer"]
        self.assertTrue(fired(record["escalation"])["prior_high_miss"])
        self.assertEqual(record["effort"], "xhigh")


class UntieredSelectionTest(CliCase):
    def test_an_untiered_worker_records_unknown_rather_than_a_guess(self):
        rc, output, error = self.run_cli(["plan", *self.base(), "--roles", "developer,reviewer",
                                          "--snapshot", str(self.snapshot), "--now", AT])
        self.assertEqual(rc, 0, error)
        document = json.loads(output)
        self.assertEqual(set(document["selection"]), {"developer", "reviewer"})
        for record in document["selection"].values():
            self.assertFalse(record["tiered"])
            for field in ("model", "effort", "round", "tier_row", "capability", "evidence", "escalation"):
                self.assertEqual(record[field], "unknown", field)
            self.assertEqual(record["required_capabilities"]["model"], "unknown")
            self.assertEqual(record["cheaper"]["candidates"], "unknown")
            self.assertFalse(record["cost"]["known"])


if __name__ == "__main__":
    unittest.main()
