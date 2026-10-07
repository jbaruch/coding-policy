"""Outcome checks for operator-authorized minimum-adequate routing (#695)."""

import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import capabilities, supervision
from tests.test_capability_routing import entry, table
from tests.test_cli import CliCase
from tests.test_cli import FreshOwnerNative
from tests.tier_fixture import AT


class MinimumAdequateTest(CliCase):
    def setUp(self):
        super().setUp()
        self.settings = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text())
        self.settings["schema_version"] = 8
        self.settings.pop("judge", None)
        worker = self.settings["worker_kinds"][0]
        self.settings["worker_kinds"] = [worker]
        worker["tiers"] = {
            "coordination": {"model": "sonnet-5", "effort": "low"},
            "consultation": {"model": "sonnet-5", "effort": "low"},
            "build": {"model": "opus-5", "effort": "high", "multiplier": 3},
            "fix": {"model": "sonnet-5", "effort": "medium", "multiplier": 1},
            "mechanical": {"model": "sonnet-5", "effort": "low", "multiplier": 1},
            "review": {"model": "opus-5", "effort": "high", "multiplier": 3},
        }
        worker["tier_routing"] = {"mode": "minimum_adequate", "evidence": {}}
        self.worker = worker
        snapshot = json.loads(self.snapshot.read_text())
        snapshot["measured_at"] = AT
        snapshot["agents"][worker["name"]]["window_group"] = worker["window_group"]
        self.snapshot.write_text(json.dumps(snapshot))
        for name, row in worker["tiers"].items():
            worker["tier_routing"]["evidence"][name] = {
                "model": row["model"], "effort": row["effort"],
                "launch": {"status": "supported", "ref": "fixture: provider catalog and CLI help", "checked_at": AT},
                "access": {"status": "accessible", "ref": "fixture: account launch result", "checked_at": AT,
                           "window_group": worker.get("window_group", "")},
            }
        self.record("opus-5", "high", "adequate")
        self.record("sonnet-5", "medium", "adequate")
        self.record("sonnet-5", "low", "adequate")

    def record(self, model, effort, verdict):
        path = capabilities.storage_path(self.state)
        document = json.loads(path.read_text()) if path.exists() else table()
        row = entry(model, effort, "implementation", verdict)
        row["source"]["dated"] = AT[:10]
        row["recorded_at"] = AT
        document["entries"] = [old for old in document["entries"]
                               if (old["model"], old["effort"]) != (model, effort)] + [row]
        path.write_text(json.dumps(document))

    def plan(self, *extra, expected=0):
        self.out, self.err = io.StringIO(), io.StringIO()
        self.config.write_text(json.dumps(self.settings))
        rc, out, err = self.run_cli(["plan", *self.base(), "--roles", "developer",
                                   "--snapshot", str(self.snapshot), "--now", AT, *extra])
        self.assertEqual(rc, expected, err)
        return json.loads(out) if rc == 0 else json.loads(err)

    def test_selects_cheapest_qualified_pair_and_minimum_adequate_effort(self):
        plan = self.plan()
        tier = plan["tiers"]["developer"]
        self.assertEqual((tier["model"], tier["effort"], tier["tier_row"], tier["round"]),
                         ("sonnet-5", "low", "mechanical", "build"))
        # Borrowing a supported pair never changes the task to a mechanical round.
        record = plan["selection"]["developer"]
        self.assertEqual(record["required_capabilities"]["model"], ["implementation"])
        self.assertFalse(record["cost"]["known"])

    def test_skips_inadequate_and_inaccessible_lower_effort_pairs(self):
        for cause in ("inadequate", "unavailable", "unsupported", "unknown", "stale", "wrong_pair", "wrong_account"):
            with self.subTest(cause=cause):
                settings = copy.deepcopy(self.settings)
                fact = self.worker["tier_routing"]["evidence"]["mechanical"]
                if cause == "inadequate":
                    self.record("sonnet-5", "low", "inadequate")
                elif cause in {"unavailable", "unknown"}:
                    fact["access"]["status"] = cause
                elif cause == "unsupported":
                    fact["launch"]["status"] = cause
                elif cause == "stale":
                    fact["access"]["checked_at"] = "2020-01-01T00:00:00Z"
                elif cause == "wrong_pair":
                    fact["model"] = "different-model"
                else:
                    fact["access"]["window_group"] = "different-account"
                plan = self.plan()
                self.assertEqual((plan["tiers"]["developer"]["model"], plan["tiers"]["developer"]["effort"]),
                                 ("sonnet-5", "medium"))
                rejected = next(row for row in plan["selection"]["developer"]["routing"]["candidates"]
                                if row["tier_row"] == "mechanical")
                self.assertTrue(rejected["rejected"])
                self.settings = settings
                self.worker = settings["worker_kinds"][0]
                self.record("sonnet-5", "low", "adequate")

    def test_explicit_pin_preserves_operator_pair_and_records_override(self):
        self.worker["tier_routing"]["mode"] = "pinned"
        plan = self.plan()
        self.assertEqual(plan["tiers"]["developer"]["model"], "opus-5")
        self.assertEqual(plan["selection"]["developer"]["routing"]["override"], "operator_pin")

    def test_own_window_accepts_matching_empty_identity_but_not_missing_proof(self):
        for omitted in (False, True):
            with self.subTest(omitted=omitted):
                if omitted:
                    self.worker.pop("window_group", None)
                else:
                    self.worker["window_group"] = ""
                for proof in self.worker["tier_routing"]["evidence"].values():
                    proof["access"]["window_group"] = ""
                snapshot = json.loads(self.snapshot.read_text())
                snapshot["agents"]["claude"]["window_group"] = ""
                self.snapshot.write_text(json.dumps(snapshot))
                plan = self.plan()
                self.assertEqual(plan["tiers"]["developer"]["tier_row"], "mechanical")
                rc, _, err, native = self.apply(plan)
                self.assertEqual(rc, 0, err)
                self.assertEqual(native.events, [])
                snapshot["agents"]["claude"].pop("window_group")
                self.snapshot.write_text(json.dumps(snapshot))
                rc, _, err, native = self.apply(plan)
                self.assertEqual(rc, 1)
                self.assertIn("capacity_account_unknown", err)
                self.assertEqual(native.events, [])

    def test_declined_risk_escalation_preserves_the_configured_floor(self):
        snapshot = json.loads(self.snapshot.read_text())
        snapshot["agents"]["claude"]["headroom_pct"] = 20
        self.snapshot.write_text(json.dumps(snapshot))
        plan = self.plan("--round-context", self.context({"prior_high_miss": True}))
        tier = plan["tiers"]["developer"]
        self.assertEqual((tier["model"], tier["effort"], tier["tier_row"]), ("opus-5", "high", "build"))
        self.assertTrue(tier["de_escalated"])
        self.assertEqual(plan["selection"]["developer"]["routing"]["override"], "risk_escalation")

    def test_repeated_failure_judgment_floor_is_preserved(self):
        plan = self.plan("--round-context", self.context({"failed_gates": 2}))
        self.assertEqual((plan["tiers"]["developer"]["model"], plan["tiers"]["developer"]["tier_row"]),
                         ("opus-5", "review"))
        self.assertEqual(plan["selection"]["developer"]["routing"]["override"], "judgment_floor")

    def test_pinned_judge_is_never_routed_to_a_cheaper_pair(self):
        self.settings["judge"] = {"worker_kind": "claude", "model": "opus-5", "effort": "high"}
        plan = self.plan("--roles", "judge", "--judge-mode", "adjudication")
        self.assertEqual(plan["tiers"]["judge"]["model"], "opus-5")
        self.assertEqual(plan["selection"]["judge"]["cheaper"]["floor"], "pinned_judge")

    def test_unknown_or_stale_qualification_skips_only_that_pair(self):
        for status in ("unknown", "stale"):
            with self.subTest(status=status):
                self.record("sonnet-5", "low", "unknown" if status == "unknown" else "adequate")
                if status == "stale":
                    path = capabilities.storage_path(self.state)
                    document = json.loads(path.read_text())
                    document["entries"][-1]["source"]["dated"] = "2020-01-01"
                    path.write_text(json.dumps(document))
                plan = self.plan()
                self.assertEqual(plan["tiers"]["developer"]["effort"], "medium")
                candidate = next(row for row in plan["selection"]["developer"]["routing"]["candidates"]
                                 if row["tier_row"] == "mechanical")
                self.assertIn("qualification_" + status, candidate["rejected"])

    def test_percentages_and_model_ids_do_not_establish_access_or_support(self):
        self.worker["tier_routing"]["evidence"] = {}
        error = self.plan(expected=1)
        rows = error["details"]["capability_refusals"][0]["routing_candidates"]
        self.assertTrue(all("access_unknown" in row["rejected"] and "launch_unknown" in row["rejected"] for row in rows))

    def test_ineligible_rows_are_not_selected_even_with_adequate_evidence(self):
        self.worker["tiers"]["consultation"]["multiplier"] = 0.01
        plan = self.plan()
        self.assertEqual(plan["tiers"]["developer"]["tier_row"], "mechanical")
        candidate = next(row for row in plan["selection"]["developer"]["routing"]["candidates"]
                         if row["tier_row"] == "consultation")
        self.assertIn("role_ineligible", candidate["rejected"])

    def test_cheaper_model_never_grants_missing_worker_eligibility(self):
        requirements = self.tmp / "requirements.json"
        requirements.write_text(json.dumps({"schema_version": 1, "assignments": {"developer": {
            "specialty": "storage", "required_capabilities": ["storage"],
            "independent": False, "engagement": "storage-implementation",
        }}}))
        self.worker["capabilities"] = []
        error = self.plan("--task", "storage-1", "--requirements", str(requirements), expected=1)
        self.assertIn("storage", json.dumps(error))

    def apply(self, plan, at=AT):
        self.out, self.err = io.StringIO(), io.StringIO()
        native = FreshOwnerNative()
        rc, out, err = self.run_cli(["apply", *self.base(), "--dry-run", "--now", at,
                                   "--assignments", json.dumps(plan), "--common", str(self.common),
                                   "--brief", "developer=" + str(self.briefs["developer"])], client=native)
        return rc, out, err, native

    def test_dispatch_rehearsal_uses_selected_launch_pair_without_input(self):
        plan = self.plan()
        rc, out, err, native = self.apply(plan)
        self.assertEqual(rc, 0, err)
        self.assertIn("sonnet-5", out)
        self.assertIn("low", out)
        self.assertEqual(native.events, [])

    def test_public_dispatch_launches_and_records_the_selected_pair(self):
        plan = self.plan("--task", "bounded-1")
        supervision.bind(self.state, {"kind": "id", "value": "fixture-foreman", "cwd": str(self.tmp),
            "herdr_env": "fixture", "pane_id": "fixture-foreman-pane"}, AT, root=self.tmp / "bindings")
        native = FreshOwnerNative()
        native.EMPTY = "❯ "
        native.frames = [native.EMPTY]
        report = (self.tmp / "developer-report.md").resolve()
        self.briefs["developer"].write_text("# developer\nREPORT: " + str(report) + "\n")
        self.out, self.err = io.StringIO(), io.StringIO()
        from foreman import assign, lifecycle
        spawn, apply = lifecycle.spawn, assign.apply
        with patch("foreman.cli.lifecycle.spawn", side_effect=lambda *a, **kw: spawn(*a, **kw, sleep=lambda _: None)), \
                patch("foreman.cli.apply_assignments", side_effect=lambda *a, **kw: apply(*a, **kw, sleep=lambda _: None)):
            rc, out, err = self.run_cli(["apply", *self.base(), "--now", AT, "--task", "bounded-1",
                "--assignments", json.dumps(plan), "--common", str(self.common),
                "--brief", "developer=" + str(self.briefs["developer"]),
                "--report", "developer=" + str(report), "--composer-settle", "0"], client=native)
        self.assertEqual(rc, 0, err)
        result = json.loads(out)["applied"][0]
        self.assertEqual((result["tier"]["model"], result["tier"]["effort"]), ("sonnet-5", "low"))
        self.assertNotIn("routing", result["tier"])
        self.assertEqual(len([event for event in native.events if event[0] == "prompt"]), 1)

    def test_dispatch_refuses_stale_capacity_before_native_actions(self):
        plan = self.plan()
        rc, _, err, native = self.apply(plan, "2026-01-08T12:16:00+00:00")
        self.assertEqual(rc, 1)
        self.assertIn("capacity_stale", err)
        self.assertEqual(native.events, [])

    def test_dispatch_refuses_changed_account_or_missing_snapshot_proof(self):
        for cause in ("account", "timestamp", "missing"):
            with self.subTest(cause=cause):
                snapshot = json.loads(self.snapshot.read_text())
                plan = self.plan()
                if cause == "account":
                    changed = copy.deepcopy(snapshot)
                    changed["agents"]["claude"]["window_group"] = "another-account"
                    self.snapshot.write_text(json.dumps(changed))
                elif cause == "timestamp":
                    plan["snapshot_ref"]["measured_at"] = "2026-01-08T12:01:00Z"
                else:
                    plan["snapshot_ref"]["source"] = str(self.tmp / "missing-snapshot.json")
                rc, _, err, native = self.apply(plan)
                self.assertEqual(rc, 1)
                self.assertIn("capacity_", err)
                self.assertEqual(native.events, [])
                self.snapshot.write_text(json.dumps(snapshot))

    def test_unknown_capacity_does_not_become_permission_to_select(self):
        snapshot = json.loads(self.snapshot.read_text())
        snapshot["agents"]["claude"]["headroom_pct"] = None
        self.snapshot.write_text(json.dumps(snapshot))
        self.assertIn("capacity_unknown", json.dumps(self.plan(expected=1)))

    def test_unrelated_evidence_refresh_is_not_a_delivery_prerequisite(self):
        plan = self.plan()
        self.worker["tier_routing"]["evidence"]["consultation"]["launch"]["ref"] = "fixture: refreshed unrelated provider catalog"
        self.config.write_text(json.dumps(self.settings))
        rc, _, err, native = self.apply(plan)
        self.assertEqual(rc, 0, err)
        self.assertEqual(native.events, [])

    def test_changed_selected_access_is_replanned_not_accepted_from_audit(self):
        plan = self.plan()
        self.worker["tier_routing"]["evidence"]["mechanical"]["access"]["status"] = "unavailable"
        self.config.write_text(json.dumps(self.settings))
        rc, _, err, native = self.apply(plan)
        self.assertEqual(rc, 1)
        self.assertIn("Plan tiers differ", err)
        self.assertEqual(native.events, [])

    def test_malformed_policy_and_old_schema_opt_in_are_actionable_refusals(self):
        self.settings["schema_version"] = 7
        self.assertIn("schema_version 8", self.plan(expected=1)["message"])
        self.settings["schema_version"] = 8
        self.worker["tier_routing"]["mode"] = True
        self.assertIn("tier_routing", self.plan(expected=1)["message"])

    def context(self, values):
        path = self.tmp / "context.json"
        path.write_text(json.dumps({"developer": values}))
        return str(path)


if __name__ == "__main__":
    unittest.main()
