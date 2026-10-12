"""Disappeared bootstrap recovery preserves work, budgets and foreign panes."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from foreman import bootstrap_recovery as bootstrap, cli, recovery, supervision
from foreman.assign import assignment_text, tiered_prompt
from foreman.errors import HerdrError, StateError, UsageError
from foreman.state import add_assignment, empty_state, load_state_checked, save_state

AT = "2026-02-03T10:00:00+00:00"
AUTH = {"source": "original operator request", "quote": "Continue this task."}


class Client:
    def __init__(self, visible):
        self.visible = visible
        self.shell = 100
        self.foreground = [{"pid": 100, "name": "zsh"}]
        self.live: dict | None = None
        self.occupants = []
        self.closed = []
        self.absent = False
        self.fail_close = False
        self.error: str | None = None

    def agent_get(self, name):
        if self.error:
            raise HerdrError("Connection failed; restore Herdr.", {"stderr": json.dumps({"error": {"code": self.error}})})
        if self.live is not None:
            return self.live
        raise HerdrError("Agent is absent.", {"stderr": json.dumps({"error": {"code": "agent_not_found"}})})

    def agent_list(self):
        return self.occupants

    def pane_process_info(self, pane):
        if self.absent:
            raise HerdrError("Pane is absent.", {"stderr": json.dumps({"error": {"code": "pane_not_found"}})})
        return {"pane_id": pane, "shell_pid": self.shell, "foreground_processes": self.foreground}

    def pane_read(self, pane, lines):
        return self.visible

    def pane_close(self, pane):
        if self.fail_close:
            raise HerdrError("Close failed; restore Herdr.", {})
        self.closed.append(pane)
        self.absent = True


class BootstrapRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        self.state = empty_state()
        store = self.state["recovery"]
        recovery.register_task(store, {"task": "task", "base_revision": "a" * 40,
                                      "scope": "bounded task", "allowed_paths": ["src/*"], "authorization": AUTH}, AT)
        self.common = self.root / "COMMON.md"
        self.brief = self.root / "brief.md"
        self.common.write_text("Original common instructions.\n")
        self.brief.write_text("CRITERION 1: Investigate the callback.\n")
        self.process = {"source": "process_argv", "pid": 200, "pane_id": "pane",
                        "argv": ["codex", "-m", "fixture-model"], "model": "fixture-model", "effort": "medium"}
        tier = {"model": "fixture-model", "effort": "medium", "kind": "codex", "verified": self.process}
        self.prompt, digest = tiered_prompt(assignment_text("advisor", str(self.common), str(self.brief)), tier, self.common, self.brief)
        tier["prompt_hash"] = digest
        self.row = recovery.reserve(store, {"id": "dispatch", "task": "task", "role": "advisor", "agent": "worker",
            "fix_round": None, "fingerprint": "c" * 64, "plan": None, "work": None, "provider": "codex",
            "worker_kind": "codex", "brief": str(self.brief), "common": str(self.common),
            "observed_before": {"context_session": None, "pane_id": "pane"}}, AT)
        recovery.mark_sending(store, "dispatch", AT, {"context_session": None, "tier": tier})
        save_state(self.path, self.state)
        supervision.bind(self.path, supervision.identity("lead", str(self.root), "fixture", pane_id="lead-pane"), AT,
                         root=self.root / "bindings")
        self.report = self.root / "report.md"
        supervision.enroll(self.path, {"id": "dispatch", "agent": "worker", "task": "task", "pane_id": "pane",
                                      "native_session": None, "report": str(self.report)}, AT)
        bootstrap.remember(self.path, "dispatch", "pane", 100, self.process, AT)
        self.evidence = self.root / "pane.txt"
        self.evidence.write_text("Unsent draft:\n" + self.prompt + "\n" + bootstrap.BOOTSTRAP_ERROR + " account/read failed: workspace routing discovery timed out (code -32603)\n$ ")
        self.client = Client(self.evidence.read_text())
        self.data = {"dispatch": "dispatch", "reason": "Original Codex exited before starting a native turn.",
                     "authorization": AUTH, "evidence": str(self.evidence)}

    def run_recovery(self):
        return bootstrap.reconcile(self.path, self.state, self.data, AT, self.client)

    def applied(self):
        add_assignment(self.state, AT, "advisor", "worker", task="task")
        recovery.finish_dispatch(self.state["recovery"], "dispatch", {
            "role": "advisor", "task": "task", "agent": "worker", "fix_round": None,
            "worker_kind": "codex", "assignment_scoped": True, "status": "applied",
            "started": False, "landed": True, "context_session": None}, 0, AT)
        save_state(self.path, self.state)

    def test_pending_transport_is_durable_before_owned_shell_closes(self):
        result = self.run_recovery()
        self.assertEqual(result["outcome"], "not_sent")
        self.assertFalse(result["retry_authorized"])
        self.assertEqual(self.client.closed, ["pane"])
        self.assertEqual(load_state_checked(self.path)[0]["recovery"]["dispatches"][0]["status"], "not_sent")
        self.assertFalse(supervision.load(self.path)["members"][0]["active"])

    def test_old_applied_false_landing_preserves_all_assignment_and_budget_history(self):
        self.applied()
        before = copy.deepcopy(self.state)
        result = self.run_recovery()
        self.assertEqual(result["historical_status"], "applied")
        self.assertEqual(self.state, before)
        self.assertEqual(load_state_checked(self.path)[0], before)
        self.assertEqual(self.client.closed, ["pane"])

    def test_failed_close_replays_transport_fact_without_resending_or_rewriting_history(self):
        self.client.fail_close = True
        with self.assertRaises(HerdrError):
            self.run_recovery()
        self.assertEqual(self.row["status"], "not_sent")
        self.assertIsNone(bootstrap.load(self.path)["outcomes"][0]["closure"])
        self.client.fail_close = False
        self.run_recovery()
        self.run_recovery()
        self.assertEqual(self.client.closed, ["pane"])
        self.assertEqual(len(bootstrap.load(self.path)["outcomes"]), 1)

    def test_missing_agent_without_original_shell_proof_is_not_ownership(self):
        bootstrap.store_path(self.path).unlink()
        with self.assertRaisesRegex(UsageError, "No original root-shell"):
            self.run_recovery()
        self.assertEqual(self.client.closed, [])
        self.assertEqual(self.row["status"], "sending")

    def archive(self):
        bootstrap.store_path(self.path).unlink()
        path = self.root / "original-process.json"
        info = {"pane_id": "pane", "shell_pid": 100,
                "foreground_processes": [{"pid": 200, "argv": self.process["argv"]}]}
        path.write_text(json.dumps({"result": {"process_info": info}}))
        self.data["origin_evidence"] = str(path)
        return path, info

    def test_legacy_archive_is_bound_to_original_pid_argv_and_root(self):
        self.applied()
        self.archive()
        self.run_recovery()
        self.assertEqual(self.client.closed, ["pane"])
        self.assertEqual(self.row["status"], "applied")

    def test_pending_legacy_archive_records_transport_without_importing_extra_input_fields(self):
        self.archive()
        self.run_recovery()
        self.assertEqual(self.row["status"], "not_sent")
        self.assertEqual(self.client.closed, ["pane"])

    def test_completed_receipt_never_closes_a_reused_pane(self):
        self.run_recovery()
        self.client.absent = False
        self.client.shell = 999
        self.run_recovery()
        self.assertEqual(self.client.closed, ["pane"])

    def test_transport_write_failure_preserves_the_surface_and_unknown_status(self):
        with patch("foreman.bootstrap_recovery.save_state", side_effect=StateError("Storage failed; restore owner storage.", {})):
            with self.assertRaises(StateError):
                self.run_recovery()
        self.assertEqual(self.client.closed, [])
        self.assertEqual(self.row["status"], "sending")

    def test_sidecar_resolution_failure_replays_after_pane_disappears(self):
        with patch("foreman.bootstrap_recovery.supervision.resolve", side_effect=StateError("Storage failed; restore supervision storage.", {})):
            with self.assertRaises(StateError):
                self.run_recovery()
        self.assertTrue(self.client.absent)
        self.run_recovery()
        self.assertEqual(self.client.closed, ["pane"])
        self.assertFalse(supervision.load(self.path)["members"][0]["active"])

    def test_a_current_shell_snapshot_cannot_replace_original_process_archive(self):
        path, info = self.archive()
        info["foreground_processes"] = [{"pid": 100, "argv": ["zsh"]}]
        path.write_text(json.dumps(info))
        with self.assertRaisesRegex(UsageError, "differs from the owner-recorded"):
            self.run_recovery()
        self.assertEqual(self.client.closed, [])

    def test_changed_prompt_inputs_preserve_surface(self):
        self.brief.write_text("Changed assignment")
        with self.assertRaisesRegex(UsageError, "Frozen prompt inputs changed"):
            self.run_recovery()
        self.assertEqual(self.client.closed, [])

    def test_incomplete_draft_or_nonbootstrap_error_is_unknown(self):
        for text in (self.prompt, "Unsent draft:\n" + self.prompt, "Unsent draft:\nother prompt\n" + bootstrap.BOOTSTRAP_ERROR):
            with self.subTest(text=text):
                self.evidence.write_text(text)
                with self.assertRaisesRegex(UsageError, "complete frozen prompt"):
                    self.run_recovery()
                self.assertEqual(self.client.closed, [])

    def test_hard_wrapped_prompt_and_error_preserve_exact_frozen_input_hash(self):
        text = self.evidence.read_text()
        wrapped = "\n".join(text[i:i + 37] for i in range(0, len(text), 37))
        self.evidence.write_text(wrapped)
        self.client.visible = wrapped
        self.assertEqual(self.run_recovery()["outcome"], "not_sent")

    def test_live_native_work_or_report_refuses_without_recording_outcome(self):
        for change in ("session", "started", "report", "other-history"):
            with self.subTest(change=change):
                saved = copy.deepcopy(self.state)
                if change == "session":
                    self.row["context_before_send"]["context_session"] = {"value": "native"}
                elif change == "started":
                    self.row["result"] = {"started": True}
                elif change == "report":
                    self.report.write_text("Actual report")
                else:
                    self.state["assignments"].append({"agent": "worker", "context_session": None})
                with self.assertRaises(UsageError):
                    self.run_recovery()
                self.assertEqual(self.client.closed, [])
                self.assertEqual(bootstrap.load(self.path)["outcomes"], [])
                self.state = saved
                self.row = self.state["recovery"]["dispatches"][0]
                if self.report.exists():
                    self.report.unlink()

    def test_changed_shell_foreign_process_live_name_and_foreign_registration_are_preserved(self):
        for change in ("shell", "process", "name", "occupant", "screen"):
            with self.subTest(change=change):
                self.client = Client(self.evidence.read_text())
                if change == "shell":
                    self.client.shell = 101
                elif change == "process":
                    self.client.foreground = [{"pid": 300, "name": "other-worker"}]
                elif change == "name":
                    self.client.live = {"pane_id": "pane", "agent_status": "idle"}
                elif change == "occupant":
                    self.client.occupants = [{"pane_id": "pane", "name": "someone-else"}]
                else:
                    self.client.visible = "$ unrelated shell"
                with self.assertRaises((UsageError, HerdrError)):
                    self.run_recovery()
                self.assertEqual(self.client.closed, [])
                self.assertEqual(bootstrap.load(self.path)["outcomes"], [])

    def test_archived_report_or_native_observation_survives_later_read_failure(self):
        for kind, data in (("report_observed", {"present": True}),
                           ("identity_observed", {"native_session": {"value": "native"}}),
                           ("lifecycle_observed", {"status": "working"})):
            with self.subTest(kind=kind):
                def record(store):
                    store["events"] = []
                    supervision.append_event(store, AT, "dispatch", kind, data)
                supervision.transaction(self.path, record)
                with self.assertRaisesRegex(UsageError, "Supervision recorded"):
                    self.run_recovery()
                self.assertEqual(self.client.closed, [])
                self.assertEqual(bootstrap.load(self.path)["outcomes"], [])

    def test_connection_failure_is_not_missing_agent(self):
        self.client.error = "transport_error"
        with self.assertRaises(HerdrError):
            self.run_recovery()
        self.assertEqual(self.client.closed, [])

    def test_report_appearing_after_transport_record_preserves_pane(self):
        real_save = save_state
        def save(path, data):
            real_save(path, data)
            if Path(path) == bootstrap.store_path(self.path):
                self.report.write_text("Report appeared during recovery")
        with patch("foreman.bootstrap_recovery.save_state", side_effect=save):
            with self.assertRaisesRegex(UsageError, "report work exists"):
                self.run_recovery()
        self.assertEqual(self.client.closed, [])

    def test_changed_shell_content_after_failed_close_preserves_unknown_work(self):
        self.client.fail_close = True
        with self.assertRaises(HerdrError):
            self.run_recovery()
        self.client.fail_close = False
        self.client.visible = "$ unrelated work"
        with self.assertRaisesRegex(UsageError, "complete frozen prompt"):
            self.run_recovery()
        self.assertEqual(self.client.closed, [])

    def test_appended_shell_activity_before_first_reconciliation_is_preserved(self):
        self.client.visible += "\n$ run-other-task\ncompleted other task\n$ "
        with self.assertRaisesRegex(UsageError, "pane changed"):
            self.run_recovery()
        self.assertEqual(self.client.closed, [])
        self.assertEqual(self.row["status"], "sending")
        self.assertEqual(bootstrap.load(self.path)["outcomes"], [])

    def test_invalid_timestamp_never_corrupts_origin_or_outcome_storage(self):
        before = bootstrap.store_path(self.path).read_bytes()
        with self.assertRaises(UsageError):
            bootstrap.remember(self.path, "other", "pane", 100, self.process, "not-a-timestamp")
        self.assertEqual(bootstrap.store_path(self.path).read_bytes(), before)
        with self.assertRaises(UsageError):
            bootstrap.reconcile(self.path, self.state, self.data, "not-a-timestamp", self.client)
        self.assertEqual(bootstrap.store_path(self.path).read_bytes(), before)
        self.assertEqual(self.client.closed, [])
        self.assertEqual(self.row["status"], "sending")
        self.assertEqual(self.run_recovery()["outcome"], "not_sent")

    def test_appended_shell_work_after_failed_close_is_preserved(self):
        self.client.fail_close = True
        with self.assertRaises(HerdrError):
            self.run_recovery()
        self.client.fail_close = False
        self.client.visible += "\n$ run-other-task\ncompleted other task\n$ "
        with self.assertRaisesRegex(UsageError, "pane changed"):
            self.run_recovery()
        self.assertEqual(self.client.closed, [])

    def test_incomplete_or_foreign_closure_receipts_cannot_clear_the_gate(self):
        self.client.fail_close = True
        with self.assertRaises(HerdrError):
            self.run_recovery()
        original = bootstrap.load(self.path)
        for closure in ({"closed": True},
                        {"pane_id": "other", "agent": "worker", "closed": True, "replayed": False},
                        {"pane_id": "pane", "agent": "other", "closed": True, "replayed": False}):
            with self.subTest(closure=closure):
                changed = copy.deepcopy(original)
                changed["outcomes"][0]["closure"] = closure
                bootstrap.store_path(self.path).write_text(json.dumps(changed))
                with self.assertRaises(StateError):
                    self.run_recovery()
                self.assertEqual(self.client.closed, [])

    def test_changed_evidence_cannot_replay_durable_outcome(self):
        self.client.fail_close = True
        with self.assertRaises(HerdrError):
            self.run_recovery()
        self.evidence.write_text(self.evidence.read_text() + "changed")
        with self.assertRaisesRegex(UsageError, "unchanged evidence"):
            self.run_recovery()
        self.assertEqual(self.client.closed, [])

    def test_newer_corrupt_and_malformed_stores_fail_closed(self):
        for data in ({"schema_version": 2, "origins": [], "outcomes": []},
                     {"schema_version": 1, "origins": [{}], "outcomes": []}, None):
            with self.subTest(data=data):
                bootstrap.store_path(self.path).write_text(json.dumps(data))
                with self.assertRaises(StateError):
                    self.run_recovery()
                self.assertEqual(self.client.closed, [])

    def test_cli_routes_without_requiring_a_live_worker_or_current_configuration(self):
        record = self.root / "recovery.json"
        record.write_text(json.dumps(self.data))
        args = cli.build_parser().parse_args(["reconcile-bootstrap", "--record", str(record), "--state", str(self.path), "--now", AT])
        result, _ = cli.cmd_recovery(args, client=self.client)
        self.assertEqual(result["outcome"], "not_sent")
        self.assertEqual(self.client.closed, ["pane"])


if __name__ == "__main__":
    unittest.main()
