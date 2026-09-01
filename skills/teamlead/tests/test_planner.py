"""Tests for teamlead.planner. Pure function in, pure dict out."""

# Standalone-run shim: scripts/run-tests.sh executes each suite as
# `python3 <file>` from the repo root, so put the skill directory (this file's
# grandparent) on sys.path before the package imports below.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from teamlead.errors import PlanError
from teamlead.planner import plan

ROLES = ["developer", "tester", "reviewer"]


def snapshot(**headrooms):
    """Build a minimal snapshot carrying only what the planner reads."""
    return {
        "schema_version": 1,
        "measured_at": "2026-02-03T10:00:00+00:00",
        "agents": {
            name: {"kind": name, "state": "idle", "headroom_pct": headroom}
            for name, headroom in headrooms.items()
        },
    }


class OrderingTest(unittest.TestCase):
    def test_heaviest_role_goes_to_the_most_headroom(self):
        result = plan(ROLES, snapshot(claude=92.0, codex=87.0, grok=100.0))
        self.assertEqual(
            result["assignments"],
            {"developer": "grok", "tester": "claude", "reviewer": "codex"},
        )

    def test_assignment_order_follows_the_roles_given(self):
        result = plan(ROLES, snapshot(claude=92.0, codex=87.0, grok=100.0))
        self.assertEqual(list(result["assignments"]), ROLES)

    def test_reordering_the_roles_reorders_the_agents(self):
        result = plan(
            ["reviewer", "tester", "developer"], snapshot(claude=92.0, codex=87.0, grok=100.0)
        )
        self.assertEqual(result["assignments"]["reviewer"], "grok")
        self.assertEqual(result["assignments"]["developer"], "codex")

    def test_is_deterministic_across_repeated_calls(self):
        data = snapshot(claude=92.0, codex=87.0, grok=100.0)
        self.assertEqual(plan(ROLES, data), plan(ROLES, data))

    def test_integer_headrooms_work_the_same_as_floats(self):
        result = plan(ROLES, snapshot(claude=92, codex=87, grok=100))
        self.assertEqual(result["assignments"]["developer"], "grok")

    def test_fewer_roles_than_agents_leaves_the_rest_unassigned(self):
        result = plan(["developer"], snapshot(claude=92.0, codex=87.0, grok=100.0))
        self.assertEqual(result["assignments"], {"developer": "grok"})


class TieBreakTest(unittest.TestCase):
    def test_headroom_tie_falls_back_to_name(self):
        result = plan(["developer", "tester"], snapshot(zeta=50.0, alpha=50.0))
        self.assertEqual(result["assignments"]["developer"], "alpha")
        self.assertEqual(result["assignments"]["tester"], "zeta")

    def test_previous_role_count_beats_name(self):
        counts = {"developer": {"alpha": 3, "zeta": 1}}
        result = plan(["developer"], snapshot(zeta=50.0, alpha=50.0), counts)
        self.assertEqual(result["assignments"]["developer"], "zeta")

    def test_role_counts_are_per_role_not_global(self):
        # alpha has been developer a lot but tester never; the tester pick must
        # not be penalised by the developer history.
        counts = {"developer": {"alpha": 9}}
        result = plan(["tester"], snapshot(zeta=50.0, alpha=50.0), counts)
        self.assertEqual(result["assignments"]["tester"], "alpha")

    def test_headroom_still_outranks_role_history(self):
        counts = {"developer": {"grok": 10}}
        result = plan(["developer"], snapshot(claude=92.0, grok=100.0), counts)
        self.assertEqual(result["assignments"]["developer"], "grok")

    def test_equal_role_counts_fall_through_to_name(self):
        counts = {"developer": {"alpha": 2, "zeta": 2}}
        result = plan(["developer"], snapshot(zeta=50.0, alpha=50.0), counts)
        self.assertEqual(result["assignments"]["developer"], "alpha")


class NullHeadroomTest(unittest.TestCase):
    def test_null_headroom_agents_sort_last(self):
        result = plan(ROLES, snapshot(claude=None, codex=87.0, grok=100.0))
        self.assertEqual(
            result["assignments"],
            {"developer": "grok", "tester": "codex", "reviewer": "claude"},
        )

    def test_null_headroom_agents_are_ordered_by_name_alone(self):
        result = plan(["developer", "tester"], snapshot(zeta=None, alpha=None))
        self.assertEqual(result["assignments"], {"developer": "alpha", "tester": "zeta"})

    def test_role_history_does_not_reorder_null_headroom_agents(self):
        counts = {"developer": {"alpha": 5, "zeta": 0}}
        result = plan(["developer"], snapshot(zeta=None, alpha=None), counts)
        self.assertEqual(result["assignments"]["developer"], "alpha")

    def test_zero_headroom_still_outranks_null(self):
        result = plan(["developer", "tester"], snapshot(alpha=None, zeta=0.0))
        self.assertEqual(result["assignments"]["developer"], "zeta")

    def test_missing_headroom_key_is_treated_as_null(self):
        data = {"agents": {"alpha": {"state": "working"}, "zeta": {"headroom_pct": 10.0}}}
        result = plan(["developer"], data)
        self.assertEqual(result["assignments"]["developer"], "zeta")


class OutputShapeTest(unittest.TestCase):
    def test_carries_a_schema_version(self):
        self.assertEqual(plan(["developer"], snapshot(grok=100.0))["schema_version"], 1)

    def test_rationale_has_one_line_per_role_naming_the_field(self):
        result = plan(ROLES, snapshot(claude=92.0, codex=87.0, grok=100.0))
        self.assertEqual(len(result["rationale"]), 3)
        self.assertIn("developer -> grok", result["rationale"][0])
        self.assertIn("100% headroom", result["rationale"][0])
        self.assertIn("grok=100%", result["rationale"][0])

    def test_rationale_names_the_previous_role_count(self):
        counts = {"developer": {"grok": 4}}
        result = plan(["developer"], snapshot(grok=100.0), counts)
        self.assertIn("held this role 4x before", result["rationale"][0])

    def test_rationale_explains_a_null_headroom_pick(self):
        result = plan(["developer"], snapshot(grok=None))
        self.assertIn("no headroom reading", result["rationale"][0])

    def test_snapshot_ref_defaults_to_the_measurement_timestamp(self):
        result = plan(["developer"], snapshot(grok=100.0))
        self.assertEqual(
            result["snapshot_ref"],
            {"source": None, "measured_at": "2026-02-03T10:00:00+00:00"},
        )

    def test_snapshot_ref_is_echoed_when_supplied(self):
        ref = {"source": "/tmp/snap.json", "measured_at": "2026-02-03T10:00:00+00:00"}
        result = plan(["developer"], snapshot(grok=100.0), snapshot_ref=ref)
        self.assertEqual(result["snapshot_ref"], ref)


class RefusalTest(unittest.TestCase):
    def test_no_roles_is_an_error(self):
        with self.assertRaises(PlanError):
            plan([], snapshot(grok=100.0))

    def test_duplicate_roles_are_an_error(self):
        with self.assertRaises(PlanError) as caught:
            plan(["developer", "developer"], snapshot(claude=1.0, grok=2.0))
        self.assertIn("developer", str(caught.exception))

    def test_empty_snapshot_is_an_error_naming_measure(self):
        with self.assertRaises(PlanError) as caught:
            plan(ROLES, {"agents": {}})
        self.assertIn("teamlead measure", str(caught.exception))

    def test_missing_snapshot_is_an_error(self):
        with self.assertRaises(PlanError):
            plan(ROLES, None)

    def test_more_roles_than_agents_is_an_error_not_a_silent_drop(self):
        with self.assertRaises(PlanError) as caught:
            plan(ROLES, snapshot(grok=100.0, claude=92.0))
        self.assertIn("3 roles", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
