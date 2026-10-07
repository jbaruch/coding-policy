"""Verified successors inherit a spot, never a measured verdict (#700)."""

import copy
import io
import json
import sys
import unittest
from datetime import timedelta
from http.client import HTTPMessage
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import capabilities, successors, supervision
from foreman.chronology import timestamp
from foreman.errors import UsageError
from tests import test_minimum_adequate as routing_fixture
from tests.test_cli import CliCase
from tests.tier_fixture import AT


class SuccessorTest(CliCase):
    record = routing_fixture.MinimumAdequateTest.record
    plan = routing_fixture.MinimumAdequateTest.plan
    apply = routing_fixture.MinimumAdequateTest.apply

    def setUp(self):
        super().setUp()
        self.settings = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text())
        self.settings["schema_version"] = 8
        self.settings.pop("judge", None)
        self.worker = self.settings["worker_kinds"][0]
        self.settings["worker_kinds"] = [self.worker]
        self.worker["tier_routing"] = {"mode": "minimum_adequate", "evidence": {}}
        self.worker["tiers"]["build"] = {"model": "sonnet-5", "effort": "low", "multiplier": 1}
        self.worker["tier_routing"]["evidence"] = {}
        snapshot = json.loads(self.snapshot.read_text())
        snapshot["measured_at"] = AT
        snapshot["agents"][self.worker["name"]]["window_group"] = self.worker["window_group"]
        self.snapshot.write_text(json.dumps(snapshot))
        self.record("sonnet-5", "low", "unknown")
        document = capabilities.load(self.state)
        document["refreshed_at"] = AT
        capabilities.storage_path(self.state).write_text(json.dumps(document))
        self.quote = "Fixture only: sonnet-5 (Sonnet 5) is replaced by sonnet-5.1 (Sonnet 5.1)."
        self.report = {
            "id": "sonnet-upgrade", "worker": self.worker["name"], "role": "developer",
            "round": "build", "tier_row": "build", "successor": "sonnet-5.1",
            "provenance": {
                "provider": "anthropic", "citations": [{"ref": "https://www.anthropic.com/fixture-successor", "quote": self.quote}],
                "checked_at": AT, "relationship": "same_family_successor",
                "predecessor": {"model": "sonnet-5", "family": "Sonnet", "version": "5", "status": "active"},
                "successor": {"model": "sonnet-5.1", "family": "Sonnet", "version": "5.1", "status": "active"},
            },
        }

    def successor_record(self, report=None, expected=0, observed=None):
        self.config.write_text(json.dumps(self.settings))
        report_path = self.tmp / "successor-report.json"
        report_path.write_text(json.dumps(report or self.report))
        self.out, self.err = io.StringIO(), io.StringIO()
        with patch("foreman.successors.read_provider", return_value=self.quote if observed is None else observed):
            rc, out, err = self.run_cli(["capability-successor", *self.base(),
                                       "--record", str(report_path), "--now", AT])
        self.assertEqual(rc, expected, err)
        return json.loads(out) if rc == 0 else json.loads(err)

    def upgrade(self):
        row = self.worker["tiers"]["build"]
        row["model"] = "sonnet-5.1"
        self.worker["tier_routing"]["evidence"]["build"] = {
            "model": row["model"], "effort": row["effort"],
            "launch": {"status": "supported", "ref": "fixture: installed CLI launch", "checked_at": AT},
            "access": {"status": "accessible", "ref": "fixture: account result", "checked_at": AT,
                       "window_group": self.worker["window_group"]},
        }

    def test_successor_replaces_authorized_spot_without_relabeling_unknown(self):
        predecessor = copy.deepcopy(capabilities.load(self.state)["entries"])
        self.successor_record()
        self.upgrade()
        plan = self.plan()
        self.assertEqual(plan["tiers"]["developer"]["model"], "sonnet-5.1")
        self.assertEqual(plan["selection"]["developer"]["capability"], "unknown")
        candidate = next(row for row in plan["selection"]["developer"]["routing"]["candidates"]
                         if row["tier_row"] == "build")
        self.assertEqual(candidate["qualification"]["status"], "unknown")
        self.assertEqual(candidate["placement"]["status"], "provisional")
        self.assertEqual(candidate["placement"]["origin"]["entries"],
                         [row for row in predecessor if row["model"] == "sonnet-5" and row["effort"] == "low"])
        self.assertEqual(capabilities.load(self.state)["entries"], predecessor)
        rc, _, err, native = self.apply(plan)
        self.assertEqual(rc, 0, err)
        self.assertEqual(native.events, [])

    def test_missing_successor_proof_refuses_upgrade(self):
        self.upgrade()
        self.assertIn("qualification_unknown", json.dumps(self.plan(expected=1)))

    def test_wrong_family_untrusted_source_and_retirement_do_not_record(self):
        for cause in ("family", "source", "retired", "relationship"):
            with self.subTest(cause=cause):
                report = copy.deepcopy(self.report)
                proof = report["provenance"]
                if cause == "family":
                    proof["successor"]["family"] = "Haiku"
                elif cause == "source":
                    proof["citations"][0]["ref"] = "https://model-self-report.example/claim"
                elif cause == "retired":
                    proof["successor"]["status"] = "retired"
                else:
                    proof["relationship"] = "similar_prefix"
                before = capabilities.storage_path(self.state).read_bytes()
                self.successor_record(report, expected=1)
                self.assertEqual(capabilities.storage_path(self.state).read_bytes(), before)

    def test_provider_quote_must_exist_at_trusted_source(self):
        self.successor_record(expected=1, observed="fixture: unrelated provider page")

    def test_same_prefix_alone_never_establishes_provider_identity(self):
        report = copy.deepcopy(self.report)
        report["provenance"]["citations"][0]["quote"] = "sonnet-5 and sonnet-5.1 share a prefix"
        self.successor_record(report, expected=1)

    def test_new_no_effort_successor_needs_no_hardcoded_model_allowlist(self):
        self.worker["tiers"]["build"] = {"model": "haiku-4.5", "effort": None, "multiplier": 1}
        self.report["successor"] = "haiku-5.5"
        self.report["provenance"]["predecessor"] = {"model": "haiku-4.5", "family": "Haiku", "version": "4.5", "status": "active"}
        self.report["provenance"]["successor"] = {"model": "haiku-5.5", "family": "Haiku", "version": "5.5", "status": "active"}
        self.quote = "Fixture only: haiku-4.5 (Haiku 4.5) is replaced by haiku-5.5 (Haiku 5.5)."
        self.report["provenance"]["citations"][0]["quote"] = self.quote
        self.successor_record()
        self.upgrade()
        self.worker["tiers"]["build"]["model"] = "haiku-5.5"
        self.worker["tier_routing"]["evidence"]["build"]["model"] = "haiku-5.5"
        plan = self.plan()
        self.assertIsNone(plan["tiers"]["developer"]["effort"])
        rc, out, err, native = self.apply(plan)
        self.assertEqual(rc, 0, err)
        self.assertIn("haiku-5.5", out)
        self.assertNotIn("--effort", out)
        self.assertEqual(native.events, [])

    def test_changed_selected_access_is_refused_at_apply_before_native_actions(self):
        self.successor_record()
        self.upgrade()
        plan = self.plan()
        self.worker["tier_routing"]["evidence"]["build"]["access"]["status"] = "unavailable"
        self.config.write_text(json.dumps(self.settings))
        rc, _, err, native = self.apply(plan)
        self.assertEqual(rc, 1)
        self.assertIn("access_unavailable", err)
        self.assertEqual(native.events, [])

    def test_older_table_is_read_only_until_owner_records_migration(self):
        before = capabilities.storage_path(self.state).read_bytes()
        self.assertEqual(capabilities.load(self.state)["schema_version"], 1)
        self.assertEqual(capabilities.storage_path(self.state).read_bytes(), before)
        self.successor_record()
        document = capabilities.load(self.state)
        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(document["entries"], json.loads(before)["entries"])

    def test_malformed_report_is_an_actionable_refusal_not_a_traceback(self):
        for field in ("id", "tier_row", "role", "round", "successor"):
            report = {**self.report, field: []}
            self.successor_record(report, expected=1)

    def test_owner_replays_do_not_duplicate_or_rewrite_history(self):
        first = self.successor_record()
        before = capabilities.storage_path(self.state).read_bytes()
        self.assertEqual(self.successor_record(), first)
        self.assertEqual(capabilities.storage_path(self.state).read_bytes(), before)
        self.recalibrate("withdraw", status="retired")
        before = capabilities.storage_path(self.state).read_bytes()
        self.recalibrate("withdraw", status="retired")
        self.assertEqual(capabilities.storage_path(self.state).read_bytes(), before)

    def test_missing_evidence_wrong_capability_and_future_provenance_do_not_record(self):
        for cause in ("missing", "capability", "future", "stale"):
            report = copy.deepcopy(self.report)
            if cause == "missing":
                report["provenance"].pop("citations")
            elif cause == "capability":
                report["tier_row"] = "mechanical"
            else:
                delta = timedelta(days=1 if cause == "future" else -8)
                report["provenance"]["checked_at"] = (timestamp(AT, "Fixture") + delta).isoformat()
            self.successor_record(report, expected=1)

    def test_prior_explicit_negative_qualification_cannot_be_inherited(self):
        self.record("sonnet-5", "low", "inadequate")
        self.successor_record(expected=1)

    def test_retired_predecessor_can_be_replaced_but_its_status_is_preserved(self):
        self.report["provenance"]["predecessor"]["status"] = "retired"
        self.successor_record()
        self.upgrade()
        plan = self.plan()
        candidate = next(row for row in plan["selection"]["developer"]["routing"]["candidates"]
                         if row["tier_row"] == "build")
        self.assertEqual(candidate["placement"]["provenance"]["predecessor"]["status"], "retired")
        self.assertEqual(plan["tiers"]["developer"]["model"], "sonnet-5.1")

    def test_future_maintenance_checkpoint_remains_visible_and_refuses_inheritance(self):
        self.successor_record()
        self.upgrade()
        future = (timestamp(AT, "Fixture") + timedelta(days=1)).isoformat()
        self.recalibrate("keep", at=future)
        document = capabilities.load(self.state)
        self.assertEqual(capabilities.cadence(document, AT)["successors_due"][0]["status"], "future")
        self.assertIn("placement_future", json.dumps(self.plan(expected=1)))

    def test_unrelated_model_changed_round_effort_weight_or_account_cannot_borrow_spot(self):
        self.successor_record()
        self.upgrade()
        original = copy.deepcopy(self.settings)
        for cause in ("model", "effort", "weight", "account", "round"):
            with self.subTest(cause=cause):
                if cause == "model":
                    self.worker["tiers"]["build"]["model"] = "unrelated-model"
                elif cause == "effort":
                    self.worker["tiers"]["build"]["effort"] = "medium"
                elif cause == "weight":
                    self.worker["tiers"]["build"]["multiplier"] = 0.1
                elif cause == "account":
                    self.worker["window_group"] = "different-account"
                error = self.plan(*(["--round", "developer=fix"] if cause == "round" else []), expected=1)
                self.assertIn("qualification_unknown", json.dumps(error))
                self.settings = copy.deepcopy(original)
                self.worker = self.settings["worker_kinds"][0]

    def test_inherited_qualification_never_supplies_live_launch_access_or_capacity(self):
        self.successor_record()
        self.upgrade()
        settings = copy.deepcopy(self.settings)
        snapshot = self.snapshot.read_text()
        for cause in ("launch", "access", "stale_access", "wrong_account", "capacity", "stale_capacity"):
            with self.subTest(cause=cause):
                proof = self.worker["tier_routing"]["evidence"]["build"]
                if cause in {"launch", "access"}:
                    proof[cause]["status"] = "unknown"
                elif cause == "stale_access":
                    proof["access"]["checked_at"] = (timestamp(AT, "Fixture") - timedelta(days=8)).isoformat()
                elif cause == "wrong_account":
                    proof["access"]["window_group"] = "different-account"
                else:
                    data = json.loads(snapshot)
                    if cause == "capacity":
                        data["agents"][self.worker["name"]]["headroom_pct"] = None
                    else:
                        data["measured_at"] = (timestamp(AT, "Fixture") - timedelta(minutes=16)).isoformat()
                    self.snapshot.write_text(json.dumps(data))
                self.plan(expected=1)
                self.settings = copy.deepcopy(settings)
                self.worker = self.settings["worker_kinds"][0]
                self.snapshot.write_text(snapshot)

    def test_new_negative_evidence_vetoes_inheritance_and_apply_before_actions(self):
        self.successor_record()
        self.upgrade()
        plan = self.plan()
        for model in ("sonnet-5", "sonnet-5.1"):
            with self.subTest(model=model):
                self.record(model, "low", "inadequate")
                rc, _, err, native = self.apply(plan)
                self.assertEqual(rc, 1)
                self.assertIn("inadequate", err)
                self.assertEqual(native.events, [])
                self.record(model, "low", "unknown")

    def recalibrate(self, action, verdict="unknown", status="active", at=AT, entries=None):
        report = {"recalibrations": [{"id": self.report["id"], "action": action, "verdict": verdict,
                  "source": {"kind": "project", "ref": "fixture: recorded outcome", "dated": at[:10]},
                  "provider_status": status}]}
        if entries is not None:
            report["entries"] = entries
        return capabilities.record(self.state, report, at)

    def test_normal_maintenance_revisits_due_placement_without_blocking_delivery(self):
        self.successor_record()
        self.upgrade()
        later = (timestamp(AT, "Fixture") + timedelta(days=7)).isoformat()
        # A refresh of unrelated evidence must not hide a due placement.
        capabilities.record(self.state, {"entries": [{"model": "unrelated", "effort": "low",
                            "capability": "implementation", "verdict": "unknown",
                            "source": {"kind": "project", "ref": "fixture", "dated": later[:10]}}]}, later)
        self.out, self.err = io.StringIO(), io.StringIO()
        rc, out, err = self.run_cli(["capability-check", *self.base(), "--now", later])
        self.assertEqual(rc, 0, err)
        check = json.loads(out)
        self.assertTrue(check["due"])
        self.assertEqual(check["successors_due"][0]["id"], self.report["id"])
        # Only facts the selected seat uses need refresh. Due is not a routing veto.
        for proof in self.worker["tier_routing"]["evidence"].values():
            for key in ("launch", "access"):
                proof[key]["checked_at"] = later
        snapshot = json.loads(self.snapshot.read_text())
        snapshot["measured_at"] = later
        self.snapshot.write_text(json.dumps(snapshot))
        self.plan("--now", later)
        before = copy.deepcopy(capabilities.load(self.state)["successors"][0]["origin"])
        document = self.recalibrate("keep", at=later)
        self.assertEqual(document["successors"][0]["origin"], before)
        self.assertFalse(capabilities.cadence(document, later)["due"])
        self.assertEqual(capabilities.assess(document, "sonnet-5.1", "low", ["implementation"]), "unknown")

    def test_revise_requires_actual_qualification_and_preserves_origin(self):
        self.successor_record()
        with self.assertRaisesRegex(UsageError, "actual successor capability entries"):
            self.recalibrate("revise", "adequate")
        document = self.recalibrate("revise", "adequate", entries=[{
            "model": "sonnet-5.1", "effort": "low", "capability": "implementation", "verdict": "adequate",
            "source": {"kind": "project", "ref": "fixture: accepted implementation outcome", "dated": AT[:10]}}])
        self.assertEqual(successors.inspect(document["successors"][0], AT)["status"], "confirmed")
        self.assertEqual(document["successors"][0]["origin"]["verdict"], "unknown")

    def test_withdrawal_and_retirement_remain_visible_and_veto_even_adequate_pair(self):
        self.successor_record()
        self.upgrade()
        plan = self.plan()
        document = self.recalibrate("withdraw", status="retired")
        self.assertEqual(document["successors"][0]["history"][-1]["provider_status"], "retired")
        self.record("sonnet-5.1", "low", "adequate")
        rc, _, err, native = self.apply(plan)
        self.assertEqual(rc, 1)
        self.assertIn("placement_withdrawn", err)
        self.assertEqual(native.events, [])

    def test_explicit_pin_and_judgment_do_not_accept_provisional_reassignment(self):
        self.worker["tier_routing"]["mode"] = "pinned"
        self.successor_record(expected=1)
        self.worker["tier_routing"]["mode"] = "minimum_adequate"
        for role, round_type in (("reviewer", "review"), ("tester", "hostile_verify"), ("judge", "judge")):
            report = {**self.report, "role": role, "round": round_type}
            self.successor_record(report, expected=1)

    def test_public_dispatch_launches_successor_with_real_owner_path(self):
        self.successor_record()
        self.upgrade()
        plan = self.plan("--task", "successor-1")
        from foreman import assign, lifecycle
        from tests.test_cli import FreshOwnerNative
        supervision.bind(self.state, {"kind": "id", "value": "fixture-foreman", "cwd": str(self.tmp),
                         "herdr_env": "fixture", "pane_id": "fixture-pane"}, AT, root=self.tmp / "bindings")
        native = FreshOwnerNative()
        native.EMPTY = "❯ "
        native.frames = [native.EMPTY]
        report = (self.tmp / "report.md").resolve()
        self.briefs["developer"].write_text("# developer\nREPORT: " + str(report) + "\n")
        self.out, self.err = io.StringIO(), io.StringIO()
        spawn, apply = lifecycle.spawn, assign.apply
        with patch("foreman.cli.lifecycle.spawn", side_effect=lambda *a, **kw: spawn(*a, **kw, sleep=lambda _: None)), \
                patch("foreman.cli.apply_assignments", side_effect=lambda *a, **kw: apply(*a, **kw, sleep=lambda _: None)):
            rc, out, err = self.run_cli(["apply", *self.base(), "--now", AT, "--task", "successor-1",
                "--assignments", json.dumps(plan), "--common", str(self.common),
                "--brief", "developer=" + str(self.briefs["developer"]), "--report", "developer=" + str(report),
                "--composer-settle", "0"], client=native)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["applied"][0]["tier"]["model"], "sonnet-5.1")
        self.assertEqual(len([event for event in native.events if event[0] == "prompt"]), 1)


class ProviderReadTest(unittest.TestCase):
    def test_bounded_official_read_extracts_visible_quote(self):
        response = MagicMock()
        response.geturl.return_value = "https://www.anthropic.com/fixture"
        response.read.return_value = b"<p>Fixture provider &amp; catalog</p><script>self-claim</script>"
        response.__enter__.return_value = response
        with patch("foreman.successors.build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(successors.read_provider(response.geturl(), "anthropic"), "Fixture provider & catalog")

    def test_wrong_host_credentials_port_or_redirect_are_refused(self):
        for ref in ("http://www.anthropic.com/x", "https://www.anthropic.com.evil.example/x",
                    "https://secret@www.anthropic.com/x", "https://www.anthropic.com:8080/x"):
            with self.subTest(ref=ref), self.assertRaises(UsageError):
                successors.read_provider(ref, "anthropic")
        with self.assertRaises(UsageError):
            successors.ProviderRedirect("anthropic").redirect_request(
                Request("https://www.anthropic.com/fixture"), io.BytesIO(), 302, "", HTTPMessage(), "https://evil.example/x")

    def test_unreachable_and_oversize_source_refuse(self):
        with patch("foreman.successors.build_opener") as opener:
            opener.return_value.open.side_effect = OSError("fixture outage")
            with self.assertRaisesRegex(UsageError, "Restore access"):
                successors.read_provider("https://www.anthropic.com/fixture", "anthropic")
        response = MagicMock()
        response.geturl.return_value = "https://www.anthropic.com/fixture"
        response.read.return_value = b"x" * (successors.BODY_LIMIT + 1)
        response.__enter__.return_value = response
        with patch("foreman.successors.build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(UsageError, "smaller catalog page"):
                successors.read_provider(response.geturl(), "anthropic")


if __name__ == "__main__":
    unittest.main()
