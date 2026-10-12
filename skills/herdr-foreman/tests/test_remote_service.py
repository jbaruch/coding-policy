"""Caller lifecycle outcomes through the JSON boundary; no live worker effects."""

import io
import json
import sys
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import recovery, supervision
from foreman.command_context import RemoteContextError, RemoteIndeterminateError
from foreman.remote_owner import RemoteForemanOwner
from foreman.remote_service import MAX_REQUEST_CHARS, METHODS, RemoteTaskService, serve, serve_stdio
from foreman.state import add_assignment, empty_state, save_state
from tests import test_command_context as context_fixture
from tests import test_engagement as report_fixture
from tests import test_recovery as recovery_fixture

AT = "2026-09-01T12:00:00+00:00"
LATER = "2026-09-01T13:00:00+00:00"


class RemoteServiceTest(context_fixture.RemoteOwnerFixture):
    def setUp(self):
        super().setUp()
        self.service = RemoteTaskService(self.owner, lambda _method, _params: True)

    def call(self, method, params):
        return self.service.call({"schema_version": 1, "id": "wire-1", "method": method, "params": params})

    def request(self, request_id="host-job-1"):
        return self.call("task_request", {"request_id": request_id, "task": "Implement authorized fixture"})["result"]["task_id"]

    def register(self, task_id):
        self.owner.claim_task(task_id)
        state = empty_state()
        recovery.register_task(state["recovery"], {"task": task_id, "base_revision": "a" * 40,
            "scope": "fixture", "allowed_paths": ["fixture.py"],
            "authorization": {"source": "authenticated host", "quote": "Implement authorized fixture"}}, AT)
        save_state(self.owner.state_path, state)
        return state

    def restart(self):
        self.owner = RemoteForemanOwner(context=self.context, store_path=self.store, plugin_root=self.root,
                                        lease_reader=lambda: self.current_receipt)
        self.service = RemoteTaskService(self.owner, lambda _method, _params: True)

    def dispatch(self, state, task, *, complete=True):
        record = {"id": "dispatch-1", "task": task, "role": "developer", "agent": "worker",
                  "fix_round": 1, "fingerprint": "f" * 64, "plan": None,
                  "work": {"base_revision": "a" * 40, "scope": "fixture", "paths": ["fixture.py"], "findings": ["F1"]}}
        recovery.reserve(state["recovery"], record, AT)
        recovery.mark_sending(state["recovery"], record["id"], AT, {"cleared": True})
        if complete:
            add_assignment(state, AT, "developer", "worker", task=task, fix_round=1)
            recovery.finish_dispatch(state["recovery"], record["id"],
                {"status": "applied", **{key: record[key] for key in ("task", "role", "agent", "fix_round")}}, 0, AT)
        save_state(self.owner.state_path, state)

    def test_exact_ingress_schemas_refuse_overrides_and_internal_methods(self):
        task = self.request()
        params = {"task_request": {"request_id": "host-job-2", "task": "fixture"},
                  "task_query": {"task_id": task}, "task_inspect": {"task_id": task},
                  "task_report": {"task_id": task, "report_id": "report"},
                  "task_cancel": {"task_id": task, "operation_id": "host-cancel"},
                  "task_resume": {"task_id": task, "operation_id": "host-resume"},
                  "task_reconcile": {"request_id": "host-job-1"}, "operation_query": {"operation_id": "host-cancel"}}
        original = self.store.read_bytes()
        for method in METHODS:
            for field in ("model", "provider", "argv", "ssh", "shell", "lease_epoch", "policy_digest", "evidence", "outcome"):
                with self.subTest(method=method, field=field), self.assertRaises(RemoteContextError):
                    self.call(method, {**params[method], field: "caller override"})
        for method in ("initialize", "reconcile", "invoke", "claim_task", "resolve_task_control", "caller_receipt"):
            with self.subTest(method=method), self.assertRaises(RemoteContextError):
                self.call(method, {})
        self.assertEqual(self.store.read_bytes(), original)
        self.assertEqual(self.calls, [])

    def test_admission_is_idempotent_and_reconciles_a_lost_reply_after_restart(self):
        task = self.request()
        self.restart()
        self.assertEqual(self.request(), task)
        reply = self.call("task_reconcile", {"request_id": "host-job-1"})
        self.assertEqual(reply["result"], {"found": True, "task_id": task, "status": "queued"})
        self.assertEqual(reply["owner_receipt"]["attestation"]["controller_id"], "controller")
        self.assertEqual(reply["owner_receipt"]["attestation"]["policy_digest"], self.receipt.policy_digest)
        self.assertFalse(reply["owner_receipt"]["production_enabled"])
        self.assertFalse(reply["owner_receipt"]["reattachment_supported"])
        self.assertEqual(self.call("task_reconcile", {"request_id": "unseen"})["result"],
                         {"found": False, "task_id": None, "status": None})
        with self.assertRaises(RemoteContextError):
            self.call("task_request", {"request_id": "host-job-1", "task": "changed bytes"})
        self.assertEqual(len(self.owner.queued_tasks()), 1)

    def test_cancel_before_claim_is_terminal_and_cannot_dispatch_after_restart(self):
        task = self.request()
        params = {"task_id": task, "operation_id": "host-cancel"}
        cancelled = self.call("task_cancel", params)["result"]
        self.assertEqual(cancelled["status"], "completed")
        self.assertEqual(cancelled["evidence"]["outcome"], "cancelled_before_dispatch")
        self.restart()
        self.assertEqual(self.call("task_cancel", params)["result"], cancelled)
        self.assertEqual(self.call("operation_query", {"operation_id": "host-cancel"})["result"]["operation"], cancelled)
        self.assertEqual(self.owner.queued_tasks(), [])
        self.assertEqual(self.call("task_query", {"task_id": task})["result"]["status"], "cancelled")
        with self.assertRaises(RemoteContextError):
            self.owner.claim_task(task)
        with self.assertRaises(RemoteContextError):
            self.call("task_resume", {"task_id": task, "operation_id": "resume"})
        self.assertEqual(self.effects(), [])

    def test_cancel_after_dispatch_waits_for_owned_cleanup_and_original_task_closure(self):
        task = self.request()
        state = self.register(task)
        self.dispatch(state, task)
        supervision.enroll(self.owner.state_path, {"id": "dispatch-1", "task": task, "agent": "worker",
            "report": str(self.root / "report.md"), "pane_id": "w1:p2", "native_session": None}, AT)
        params = {"task_id": task, "operation_id": "host-cancel"}
        self.assertEqual(self.call("task_cancel", params)["result"]["status"], "pending")
        original = self.owner.state_path.read_bytes()
        self.restart()
        with self.assertRaisesRegex(RemoteContextError, "cleanup"):
            self.owner.resolve_task_control("host-cancel", runner=self.runner)
        self.assertEqual(self.owner.state_path.read_bytes(), original)
        recovery.close_task(state["recovery"], state["assignments"], {"task": task,
                            "outcome": "abandoned", "evidence": "Owned worker stopped and artifacts retained"}, LATER)
        save_state(self.owner.state_path, state)
        with self.assertRaises(RemoteContextError):
            self.owner.resolve_task_control("host-cancel", runner=self.runner)
        evidence = self.root / "cleanup.md"
        evidence.write_text("Observed owned worker cleanup\n")
        supervision.resolve(self.owner.state_path, {"id": "dispatch-1", "outcome": "cancelled", "evidence": [str(evidence)]}, LATER)
        completed = self.owner.resolve_task_control("host-cancel", runner=self.runner)
        self.assertEqual(completed["evidence"]["outcome"], "cancelled")
        self.assertEqual(self.owner.resolve_task_control("host-cancel", runner=self.runner), completed)
        view = self.call("task_inspect", {"task_id": task})["result"]
        self.assertTrue(view["terminal"])
        self.assertEqual(view["closure"]["details"]["outcome"], "abandoned")
        self.assertEqual(self.effects(), [])

    def test_uncertain_task_dispatch_refuses_resume_and_cancellation_completion(self):
        task = self.request()
        state = self.register(task)
        self.dispatch(state, task, complete=False)
        self.assertEqual(self.call("task_query", {"task_id": task})["result"]["status"], "dispatch_outcome_unknown")
        with self.assertRaises(RemoteContextError):
            self.call("task_resume", {"task_id": task, "operation_id": "resume"})
        self.call("task_cancel", {"task_id": task, "operation_id": "cancel"})
        recovery.close_task(state["recovery"], state["assignments"],
            {"task": task, "outcome": "abandoned", "evidence": "Historical closure alone cannot settle delivery"}, LATER)
        save_state(self.owner.state_path, state)
        with self.assertRaises(RemoteContextError):
            self.owner.resolve_task_control("cancel", runner=self.runner)
        self.assertEqual(self.owner.pending_task_controls()[0]["operation_id"], "cancel")

    def test_resume_requires_policy_block_and_never_rewrites_fix_history(self):
        task = self.request()
        params = {"task_id": task, "operation_id": "host-resume"}
        with self.assertRaises(RemoteContextError):
            self.call("task_resume", params)
        self.owner.claim_task(task)
        fixture = recovery_fixture.RecoveryTests()
        with patch("tests.test_recovery.TASK", task):
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            save_state(self.owner.state_path, fixture.state)
            with self.assertRaises(RemoteContextError):
                self.call("task_resume", params)
            fixture.seed_checkpoint()
            save_state(self.owner.state_path, fixture.state)
            self.assertEqual(self.call("task_query", {"task_id": task})["result"]["status"], "awaiting_diagnosis")
            original = self.owner.state_path.read_bytes()
            control = self.call("task_resume", params)["result"]
            self.assertEqual(control["status"], "pending")
            with self.assertRaises(RemoteContextError):
                self.owner.resolve_task_control("host-resume", runner=self.runner)
            self.assertEqual(self.owner.state_path.read_bytes(), original)
            # Only the existing policy owner records the actual judge diagnosis
            # and bounded continuation. Caller resume grants neither.
            fixture.run_diagnosis(fixture.diagnosis("resume-diagnosis", "continue", 1), "judge")
            save_state(self.owner.state_path, fixture.state)
            authorized = self.owner.state_path.read_bytes()
            completed = self.owner.resolve_task_control("host-resume", runner=self.runner)
        self.assertEqual(completed["evidence"]["outcome"], "resumed")
        self.assertEqual(self.owner.state_path.read_bytes(), authorized)
        self.assertEqual(recovery.confirmed_fix(fixture.history, task), 5)
        self.assertEqual(self.effects(), [])

    def test_host_reconciliation_cannot_clear_an_uncertain_remote_effect(self):
        task = self.request()
        self.register(task)
        self.call("task_cancel", {"task_id": task, "operation_id": "host-cancel"})
        self.lost = True
        with self.assertRaises(RemoteIndeterminateError) as caught:
            self.client.pane_close("w1:p2")
        effect_id = caught.exception.details["operation_id"]
        self.lost = False
        original = self.store.read_bytes()
        self.restart()
        self.assertFalse(self.call("operation_query", {"operation_id": effect_id})["result"]["found"])
        self.assertTrue(self.call("task_reconcile", {"request_id": "host-job-1"})["result"]["found"])
        self.assertTrue(self.call("task_inspect", {"task_id": task})["result"]["effect_reconciliation_required"])
        with self.assertRaises(RemoteIndeterminateError):
            self.owner.resolve_task_control("host-cancel", runner=self.runner)
        self.assertEqual(self.store.read_bytes(), original)
        self.assertEqual(len(self.effects()), 1)

    def test_control_ids_are_immutable_and_only_one_pending_control_per_task(self):
        task = self.request()
        self.register(task)
        params = {"task_id": task, "operation_id": "host-operation"}
        control = self.call("task_cancel", params)["result"]
        self.assertEqual(self.call("task_cancel", params)["result"], control)
        with self.assertRaises(RemoteContextError):
            self.call("task_resume", params)
        with self.assertRaises(RemoteContextError):
            self.call("task_cancel", {**params, "operation_id": "second"})
        self.assertEqual(self.owner.pending_task_controls(), [control])

    def test_lease_handoff_and_policy_upgrade_preserve_history_and_refuse_callers(self):
        task = self.request()
        original = self.store.read_bytes()
        self.current_receipt = replace(self.receipt, lease_epoch=2)
        for method, params in (("task_query", {"task_id": task}),
                               ("task_cancel", {"task_id": task, "operation_id": "cancel"}),
                               ("task_reconcile", {"request_id": "host-job-1"})):
            with self.subTest(method=method), self.assertRaises(RemoteContextError):
                self.call(method, params)
        self.current_receipt = self.receipt
        (self.root / "rules/fixture.md").write_text("new policy\n")
        with self.assertRaises(RemoteContextError):
            self.call("task_inspect", {"task_id": task})
        self.assertEqual(self.store.read_bytes(), original)

    def test_owner_migrates_old_records_without_losing_request_or_effect_history(self):
        task = self.request()
        self.lost = True
        with self.assertRaises(RemoteIndeterminateError):
            self.client.pane_close("w1:p2")
        document = json.loads(self.store.read_text())
        document["schema_version"] = 1
        del document["controls"]
        document["requests"][0]["schema_version"] = 2
        save_state(self.store, document)
        self.restart()
        self.assertEqual(self.call("task_query", {"task_id": task})["result"]["status"], "queued")
        migrated = json.loads(self.store.read_text())
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["requests"][0]["schema_version"], 3)
        self.assertEqual(migrated["pending"], document["pending"])
        self.assertEqual(migrated["reconciliations"], document["reconciliations"])
        for version in (True, 3):
            migrated["schema_version"] = version
            save_state(self.store, migrated)
            original = self.store.read_bytes()
            with self.assertRaises(RemoteContextError):
                self.call("task_query", {"task_id": task})
            self.assertEqual(self.store.read_bytes(), original)

    def test_report_content_is_task_scoped_and_matches_assessed_evidence(self):
        task = self.request()
        self.owner.claim_task(task)
        fixture = report_fixture.EngagementTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.assess()
        state = json.loads(json.dumps(fixture.state).replace('"task-1"', json.dumps(task)))
        recovery.register_task(state["recovery"], {"task": task, "base_revision": "a" * 40,
            "scope": "fixture", "allowed_paths": ["fixture.py"],
            "authorization": {"source": "authenticated host", "quote": "Implement authorized fixture"}}, AT)
        save_state(self.owner.state_path, state)
        view = self.call("task_inspect", {"task_id": task})["result"]
        report_id = view["reports"][0]["report_id"]
        report = self.call("task_report", {"task_id": task, "report_id": report_id})["result"]
        self.assertEqual(report["content"], report_fixture.CONSULT_REPORT)
        other = self.request("another-host-job")
        with self.assertRaises(RemoteContextError):
            self.call("task_report", {"task_id": other, "report_id": report_id})
        fixture.report.write_text("changed report")
        with self.assertRaises(RemoteContextError):
            self.call("task_report", {"task_id": task, "report_id": report_id})

    def test_stdio_is_machine_readable_and_lost_response_does_not_repeat_admission(self):
        line = json.dumps({"schema_version": 1, "id": "wire", "method": "task_request",
                           "params": {"request_id": "host-job-1", "task": "Implement authorized fixture"}}) + "\n"
        class Disconnected(io.StringIO):
            def write(self, value):
                raise BrokenPipeError("fixture disconnected")
        with self.assertRaises(BrokenPipeError):
            serve(self.service, io.StringIO(line), Disconnected())
        self.restart()
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(line)), patch("sys.stdout", output):
            self.assertEqual(serve_stdio(self.owner, lambda _method, _params: True), 0)
        self.assertEqual(json.loads(output.getvalue())["result"]["task_id"], self.request())
        self.assertEqual(len(self.owner.queued_tasks()), 1)

    def test_malformed_or_unauthorized_pipe_requests_fail_closed_without_sensitive_echo(self):
        self.service = RemoteTaskService(self.owner, lambda _method, _params: False)
        original = self.store.read_bytes()
        for line in ('{"id":"wire","id":"duplicate"}\n', 'not-json secret-token\n',
                     json.dumps({"schema_version": 1, "id": "wire", "method": "task_request",
                                 "params": {"request_id": "job", "task": "secret-token"}}) + "\n"):
            output, diagnostic = io.StringIO(), io.StringIO()
            self.assertEqual(serve(self.service, io.StringIO(line), output, diagnostic), 1)
            result = json.loads(output.getvalue())
            self.assertFalse(result["ok"])
            self.assertTrue(diagnostic.getvalue())
            self.assertNotIn("secret-token", output.getvalue() + diagnostic.getvalue())
        self.assertEqual(self.store.read_bytes(), original)
        self.assertEqual(self.effects(), [])

    def test_corrupt_control_history_refuses_without_rewriting_or_requeueing(self):
        task = self.request()
        self.call("task_cancel", {"task_id": task, "operation_id": "cancel"})
        original = json.loads(self.store.read_text())
        for field, value in (("schema_version", 2), ("action", []), ("status", []),
                             ("task_id", "unowned"), ("evidence", None),
                             ("evidence", {"schema_version": 1, "outcome": [], "ledger_digest": "a" * 64}),
                             ("evidence", {"schema_version": 1, "outcome": "cancelled_before_dispatch", "ledger_digest": "a" * 64})):
            with self.subTest(field=field, value=value):
                document = json.loads(json.dumps(original))
                document["controls"][0][field] = value
                save_state(self.store, document)
                before = self.store.read_bytes()
                with self.assertRaises(RemoteContextError):
                    self.owner.queued_tasks()
                self.assertEqual(self.store.read_bytes(), before)
        document = json.loads(json.dumps(original))
        document["controls"] = []
        save_state(self.store, document)
        with self.assertRaises(RemoteContextError):
            self.owner.claim_task(task)

    def test_wire_framing_and_extra_envelope_fields_never_admit_work(self):
        envelope = {"schema_version": 1, "id": "wire", "method": "task_request",
                    "params": {"request_id": "host-job", "task": "fixture"}}
        original = self.store.read_bytes()
        lines = [json.dumps({**envelope, "model": "caller-choice"}) + "\n",
                 json.dumps({**envelope, "schema_version": True}) + "\n",
                 json.dumps({**envelope, "params": []}) + "\n",
                 json.dumps(envelope), " " * (MAX_REQUEST_CHARS + 1) + "\n",
                 '{"schema_version":1,"id":"wire","method":"task_request","params":{"task":"one","task":"two","request_id":"job"}}\n']
        for line in lines:
            with self.subTest(length=len(line)):
                output = io.StringIO()
                self.assertEqual(serve(self.service, io.StringIO(line), output, io.StringIO()), 1)
                self.assertFalse(json.loads(output.getvalue())["ok"])
        self.assertEqual(self.store.read_bytes(), original)

    def test_supported_sidecar_runs_over_real_process_pipes_with_no_shell(self):
        # A host-supplied trusted bootstrap supplies owner/lease/authentication;
        # this process fixture substitutes only those external dependencies.
        bootstrap = (
            "from tests.test_remote_service import RemoteServiceTest\n"
            "from foreman.remote_service import serve_stdio\n"
            "fixture = RemoteServiceTest()\n"
            "fixture.setUp()\n"
            "try:\n"
            "    result = serve_stdio(fixture.owner, lambda method, params: True)\n"
            "finally:\n"
            "    fixture.doCleanups()\n"
            "raise SystemExit(result)\n"
        )
        request = {"schema_version": 1, "id": "wire-start", "method": "task_request",
                   "params": {"request_id": "host-job", "task": "fixture"}}
        lookup = {"schema_version": 1, "id": "wire-recover", "method": "task_reconcile",
                  "params": {"request_id": "host-job"}}
        process = subprocess.run([sys.executable, "-c", bootstrap],
            cwd=Path(__file__).resolve().parents[1], input=json.dumps(request) + "\n" + json.dumps(lookup) + "\n",
            text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([row["id"] for row in replies], ["wire-start", "wire-recover"])
        self.assertEqual(replies[0]["result"]["task_id"], replies[1]["result"]["task_id"])
        self.assertTrue(replies[1]["result"]["found"])
        self.assertEqual(process.stderr, "")


if __name__ == "__main__":
    unittest.main()
