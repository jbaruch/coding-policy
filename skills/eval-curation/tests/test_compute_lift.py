#!/usr/bin/env python3
"""Tests for skills/eval-curation/compute-lift.py."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "compute-lift.py"

spec = importlib.util.spec_from_file_location("compute_lift", SCRIPT)
compute_lift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compute_lift)


def make_scenario(sid, variants_scores):
    """Build one tessl-shaped scenario.

    `variants_scores` is {variant_name: [criterion_scores]}. Each criterion
    contributes one assessmentResults row with `score`.
    """
    solutions = []
    for variant, scores in variants_scores.items():
        solutions.append({
            "variant": variant,
            "assessmentResults": [
                {"name": f"c{i}", "score": s} for i, s in enumerate(scores)
            ],
        })
    return {"id": sid, "solutions": solutions}


def wrap(scenarios):
    return {"data": {"attributes": {"scenarios": scenarios}}}


class TestSolutionTotal(unittest.TestCase):
    def test_sums_criterion_scores(self):
        sol = {"assessmentResults": [
            {"name": "a", "score": 10},
            {"name": "b", "score": 25.5},
            {"name": "c", "score": 5},
        ]}
        self.assertEqual(compute_lift.solution_total(sol), 40.5)

    def test_missing_assessment_results_is_zero(self):
        self.assertEqual(compute_lift.solution_total({}), 0.0)
        self.assertEqual(compute_lift.solution_total({"assessmentResults": []}), 0.0)

    def test_missing_score_field_skipped(self):
        sol = {"assessmentResults": [
            {"name": "a", "score": 10},
            {"name": "b"},
            {"name": "c", "score": 5},
        ]}
        self.assertEqual(compute_lift.solution_total(sol), 15.0)

    def test_non_numeric_score_raises(self):
        sol = {"assessmentResults": [
            {"name": "a", "score": 10},
            {"name": "b", "score": "garbage"},
        ]}
        with self.assertRaises(ValueError) as ctx:
            compute_lift.solution_total(sol)
        self.assertIn("non-numeric score", str(ctx.exception))


class TestPickVariant(unittest.TestCase):
    def test_picks_first_preferred(self):
        sols = [
            {"variant": "baseline", "id": "b"},
            {"variant": "with-context", "id": "wc"},
        ]
        picked = compute_lift.pick_variant(sols, ["with-context", "usage-spec"])
        self.assertEqual(picked["id"], "wc")

    def test_falls_back_in_preference_order(self):
        sols = [{"variant": "usage-spec", "id": "us"}]
        picked = compute_lift.pick_variant(sols, ["with-context", "usage-spec"])
        self.assertEqual(picked["id"], "us")

    def test_returns_none_when_no_preferred_match(self):
        sols = [{"variant": "experimental", "id": "x"}]
        self.assertIsNone(
            compute_lift.pick_variant(sols, ["with-context", "usage-spec"])
        )

    def test_empty_list_returns_none(self):
        self.assertIsNone(compute_lift.pick_variant([], ["with-context"]))
        self.assertIsNone(compute_lift.pick_variant(None, ["with-context"]))


class TestComputeLifts(unittest.TestCase):
    def test_basic_lift_computation(self):
        scenarios = [make_scenario("sc-1", {
            "with-context": [25, 25, 25, 25],  # total 100
            "baseline": [10, 10, 10, 5],        # total 35
        })]
        result = compute_lift.compute_lifts(wrap(scenarios))
        self.assertEqual(len(result["lifts"]), 1)
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["lifts"][0]["lift"], 65.0)
        self.assertEqual(result["lifts"][0]["with_context_total"], 100.0)
        self.assertEqual(result["lifts"][0]["baseline_total"], 35.0)

    def test_variant_aliases_accepted(self):
        # `usage-spec` and `without-context` are older aliases that the
        # script should still handle.
        scenarios = [make_scenario("sc-2", {
            "usage-spec": [50, 50],
            "without-context": [30, 30],
        })]
        result = compute_lift.compute_lifts(wrap(scenarios))
        self.assertEqual(result["lifts"][0]["lift"], 40.0)

    def test_missing_variant_skipped(self):
        # Only with-context — no baseline. Should land in `skipped`,
        # not crash.
        scenarios = [make_scenario("sc-no-baseline", {
            "with-context": [50, 50],
        })]
        result = compute_lift.compute_lifts(wrap(scenarios))
        self.assertEqual(result["lifts"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["scenario_id"], "sc-no-baseline")
        self.assertIn("baseline present: False", result["skipped"][0]["reason"])

    def test_negative_lift_preserved(self):
        scenarios = [make_scenario("sc-neg", {
            "with-context": [20],
            "baseline": [80],
        })]
        result = compute_lift.compute_lifts(wrap(scenarios))
        self.assertEqual(result["lifts"][0]["lift"], -60.0)

    def test_zero_lift_preserved(self):
        scenarios = [make_scenario("sc-zero", {
            "with-context": [100],
            "baseline": [100],
        })]
        result = compute_lift.compute_lifts(wrap(scenarios))
        self.assertEqual(result["lifts"][0]["lift"], 0.0)

    def test_non_numeric_criterion_score_skipped(self):
        scenarios = [{
            "id": "sc-bad",
            "solutions": [
                {"variant": "with-context", "assessmentResults": [
                    {"score": 10}, {"score": "garbage"},
                ]},
                {"variant": "baseline", "assessmentResults": [{"score": 5}]},
            ],
        }]
        result = compute_lift.compute_lifts(wrap(scenarios))
        self.assertEqual(result["lifts"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("non-numeric", result["skipped"][0]["reason"])

    def test_invalid_envelope_raises(self):
        with self.assertRaises(ValueError):
            compute_lift.compute_lifts({})
        with self.assertRaises(ValueError):
            compute_lift.compute_lifts({"data": {"attributes": {"scenarios": "not-a-list"}}})

    def test_multiple_scenarios(self):
        scenarios = [
            make_scenario(f"sc-{i}", {
                "with-context": [50],
                "baseline": [20 + i * 5],
            })
            for i in range(3)
        ]
        result = compute_lift.compute_lifts(wrap(scenarios))
        self.assertEqual(len(result["lifts"]), 3)
        self.assertEqual([l["lift"] for l in result["lifts"]], [30.0, 25.0, 20.0])


class TestCLI(unittest.TestCase):
    def test_cli_reads_file_argument(self):
        scenarios = [make_scenario("sc-cli", {
            "with-context": [40, 40],
            "baseline": [10, 10],
        })]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(wrap(scenarios), f)
            path = f.name
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), path],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            out = json.loads(result.stdout)
            self.assertEqual(out["lifts"][0]["lift"], 60.0)
        finally:
            Path(path).unlink()

    def test_cli_reads_stdin(self):
        scenarios = [make_scenario("sc-stdin", {
            "with-context": [70],
            "baseline": [30],
        })]
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-"],
            input=json.dumps(wrap(scenarios)),
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        out = json.loads(result.stdout)
        self.assertEqual(out["lifts"][0]["lift"], 40.0)

    def test_cli_invalid_json_exits_2(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("not json {{")
            path = f.name
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), path],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("not valid json", result.stderr.lower())
        finally:
            Path(path).unlink()

    def test_cli_missing_file_exits_2_with_actionable_message(self):
        # Per `rules/script-delegation.md` self-error-handling + error-handling's
        # "actionable messages": missing path must produce a clear stderr
        # diagnostic + non-zero exit, never a raw Python traceback.
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "/tmp/eval-curation-does-not-exist-xxxxxx.json"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("not found", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())
        # The message must hint at recovery, not just state the failure.
        self.assertIn("check the path", result.stderr.lower())

    def test_cli_permission_denied_exits_2_with_actionable_message(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"data": {"attributes": {"scenarios": []}}}, f)
            path = f.name
        try:
            Path(path).chmod(0o000)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), path],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("permission", result.stderr.lower())
            self.assertNotIn("traceback", result.stderr.lower())
        finally:
            Path(path).chmod(0o600)
            Path(path).unlink()

    def test_cli_wrong_envelope_exits_2(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"wrong_shape": []}, f)
            path = f.name
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), path],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("scenarios", result.stderr.lower())
        finally:
            Path(path).unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
