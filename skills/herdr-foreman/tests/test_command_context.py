"""Native parity and remote authority tests; no SSH or agent process in CI."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dataclasses import asdict, FrozenInstanceError, replace
from concurrent.futures import ThreadPoolExecutor
import json
import io
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from typing import Any, cast

from foreman.command_context import (
    HerdrCommandContext, RemoteForemanAttestation, RemoteContextError, RemoteIndeterminateError,
)
from foreman.herdr import HerdrClient
from foreman.remote_owner import RemoteForemanOwner, loaded_policy
from foreman.state import save_state, state_lock
from foreman.errors import HerdrError, StateError
from foreman import cli, recovery, supervision, supervision_runtime
from foreman.state import empty_state


class ContextTest(unittest.TestCase):
    def receipt(self):
        return RemoteForemanAttestation("controller", "principal", "machine_id", "session", "w1",
                                        "w1:p1", "terminal-foreman", 1, "0.3.1", "a" * 64)

    def test_native_defaults_and_path_with_spaces_are_one_executable(self):
        client = HerdrClient(binary="/directory with spaces/herdr")
        self.assertEqual(client.argv_agent_list(), ["/directory with spaces/herdr", "agent", "list"])
        self.assertEqual(client.context.mode, "native")

    def test_receipt_and_context_are_immutable(self):
        receipt = self.receipt()
        context = HerdrCommandContext("herdr", ("--machine", "machine_id"), "attested-remote", receipt)
        with self.assertRaises(FrozenInstanceError):
            setattr(context, "mode", "native")
        with self.assertRaises(FrozenInstanceError):
            setattr(receipt, "lease_epoch", 2)

    def test_malformed_prefixes_and_absent_attestation_are_refused(self):
        for prefix in (("machine_id", "--machine"), ("--machine",), (),
                       ("--machine", "other"), ("--machine", "machine_id", "--session", "s"),
                       ("--remote", "host"), ("--machine", "machine_id", "--remote", "host"),
                       ["--machine", "machine_id"], "--machine machine_id"):
            with self.subTest(prefix=prefix), self.assertRaises(RemoteContextError):
                # Deliberately malformed runtime input exercises the constructor guard.
                HerdrCommandContext("herdr", cast(Any, prefix), "attested-remote", self.receipt())
        with self.assertRaises(RemoteContextError):
            HerdrCommandContext("herdr", ("--machine", "machine_id"), "attested-remote")
        with self.assertRaises(RemoteContextError):
            HerdrCommandContext("herdr", ("--machine", "machine_id"))

    def test_invalid_receipt_fields_are_refused_without_echoing_them(self):
        for field, value in (("machine_profile_id", "ssh://secret@host:22"), ("lease_epoch", True),
                             ("lease_epoch", 0), ("policy_digest", "invalid"),
                             ("policy_version", "unknown"), ("service_principal", "")):
            with self.subTest(field=field), self.assertRaises(RemoteContextError) as caught:
                replace(self.receipt(), **{field: value})
            self.assertNotIn("ssh://secret", str(caught.exception))


class RemoteOwnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".tessl-plugin").mkdir()
        (self.root / ".tessl-plugin/plugin.json").write_text('{"version":"0.3.1"}', encoding="utf-8")
        (self.root / "rules").mkdir()
        (self.root / "rules/fixture.md").write_text("fixture policy\n", encoding="utf-8")
        (self.root / "skills/herdr-foreman").mkdir(parents=True)
        (self.root / "skills/herdr-foreman/SKILL.md").write_text("fixture skill\n", encoding="utf-8")
        self.store = self.root / "owner.json"
        version, digest = loaded_policy(self.root)
        self.receipt = RemoteForemanAttestation("controller", "principal", "machine_id", "session", "w1",
                                               "w1:p1", "terminal-foreman", 1, version, digest)
        self.context = HerdrCommandContext("herdr", ("--machine", "machine_id"), "attested-remote", self.receipt)
        self.owner = RemoteForemanOwner(context=self.context, store_path=self.store, plugin_root=self.root)
        self.calls = []
        self.snapshot = {"workspaces": [{"workspace_id": "w1"}], "agents": [],
                         "panes": [{"workspace_id": "w1", "pane_id": "w1:p1", "terminal_id": "terminal-foreman"}]}
        self.status = "status: running\nendpoint_compatible: yes\nsocket: machine:machine_id/session\n"
        self.lost = False
        self.exit_code = 0
        self.owner.initialize(self.runner, at="2026-09-01T12:00:00+00:00")
        self.calls.clear()
        self.client = HerdrClient(context=self.context, remote_authority=self.owner, runner=self.runner)

    def runner(self, argv):
        self.calls.append(argv)
        self.assertEqual(argv[:3], ["herdr", "--machine", "machine_id"])
        command = argv[3:]
        if command == ["status", "server"]:
            return subprocess.CompletedProcess(argv, 0, self.status, "")
        if command == ["api", "snapshot"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"result": {"snapshot": self.snapshot}}), "")
        if self.lost:
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, self.exit_code, '{"result":{}}', "disconnected" if self.exit_code else "")

    def effects(self):
        return [argv for argv in self.calls if argv[3:5] not in (["status", "server"], ["api", "snapshot"])]

    def test_all_builders_prefix_once_and_preserve_literal_text(self):
        c = self.client
        commands = [c.argv_agent_get("worker"), c.argv_agent_list(), c.argv_agent_read("worker"),
                    c.argv_agent_prompt("worker", "literal ; $(no shell) --session x"),
                    c.argv_agent_send_keys("worker", ["esc"]), c.argv_agent_wait("worker"),
                    c.argv_agent_start("worker", "codex", "w1:p2", ["--model", "owner-model"]),
                    c.argv_pane_send_text("w1:p2", "--machine not a selector"),
                    c.argv_pane_send_keys("w1:p2", ["enter"]), c.argv_pane_split("w1:p2"),
                    c.argv_workspace_create(cwd="/project", label="task"), c.argv_pane_close("w1:p2"),
                    c.argv_pane_wait_output("w1:p2", match="done"), c.argv_pane_process_info("w1:p2"),
                    c.argv_pane_layout("w1:p2"), c.argv_pane_rename("w1:p2", "label")]
        for argv in commands:
            self.assertEqual(argv[:3], ["herdr", "--machine", "machine_id"])
            self.assertNotIn("--machine", argv[3:5])
        self.assertIn("literal ; $(no shell) --session x", commands[3])

    def test_direct_status_get_read_and_rename_use_bound_context(self):
        self.status += "version: 0.9.2\n"
        self.client.require_start_retry_compatibility()
        with self.assertRaisesRegex(HerdrError, "no pane record"):
            self.client.pane_get("w1:p2")
        self.client.pane_read("w1:p2", 10)
        self.client.pane_rename("w1:p2", "worker")
        self.assertEqual(self.effects()[-1][3:5], ["pane", "rename"])
        self.assertTrue(all(argv[:3] == ["herdr", "--machine", "machine_id"] for argv in self.calls))

    def test_live_session_workspace_terminal_and_brain_drift_refuse_before_effect(self):
        cases = [lambda: setattr(self, "status", self.status.replace("/session", "/other")),
                 lambda: self.snapshot["workspaces"].clear(),
                 lambda: self.snapshot["panes"][0].update(workspace_id="w2"),
                 lambda: self.snapshot["panes"][0].update(terminal_id="replacement"),
                 lambda: self.snapshot["agents"].append({"pane_id": "w1:p1", "agent": "codex"})]
        original = json.dumps(self.snapshot)
        status = self.status
        for change in cases:
            self.snapshot = json.loads(original)
            self.status = status
            self.calls.clear()
            change()
            with self.assertRaises(RemoteContextError):
                self.client.pane_send_text("w1:p2", "task")
            self.assertEqual(self.effects(), [])

    def test_changed_lease_controller_principal_or_policy_refuses_before_effect(self):
        original = json.loads(self.store.read_text(encoding="utf-8"))
        for field, value in (("lease_epoch", 2), ("controller_id", "other"),
                             ("service_principal", "other"), ("policy_digest", "b" * 64)):
            changed = json.loads(json.dumps(original))
            changed["attestation"][field] = value
            save_state(self.store, changed)
            self.calls.clear()
            with self.assertRaises(RemoteContextError):
                self.client.pane_close("w1:p2")
            self.assertEqual(self.calls, [])
        save_state(self.store, original)
        (self.root / "rules/fixture.md").write_text("changed policy\n", encoding="utf-8")
        with self.assertRaises(RemoteContextError):
            self.client.pane_close("w1:p2")

    def test_lost_response_blocks_every_fresh_effect_across_owner_restart(self):
        self.lost = True
        with self.assertRaises(RemoteIndeterminateError) as caught:
            self.client.agent_start("worker", "codex", "w1:p2", ["--model", "policy-chosen"])
        operation_id = caught.exception.details["operation_id"]
        self.lost = False
        restarted = RemoteForemanOwner(context=self.context, store_path=self.store, plugin_root=self.root)
        client = HerdrClient(context=self.context, remote_authority=restarted, runner=self.runner)
        for effect in (lambda: client.agent_start("worker", "codex", "w1:p2", []),
                       lambda: client.agent_prompt("worker", "task"),
                       lambda: client.pane_send_text("w1:p2", "task"),
                       lambda: client.pane_send_keys("w1:p2", ["enter"]),
                       lambda: client.pane_close("w1:p2")):
            with self.assertRaises(RemoteIndeterminateError):
                effect()
        self.assertEqual(len(self.effects()), 1)
        self.calls.clear()
        restarted.reconcile(operation_id, outcome="applied", evidence="same-session native worker receipt", runner=self.runner)
        self.assertEqual([argv[3:] for argv in self.calls], [["status", "server"], ["api", "snapshot"]])
        self.assertIsNone(json.loads(self.store.read_text(encoding="utf-8"))["pending"])
        self.assertEqual(self.effects(), [])

    def test_disconnect_exit_is_indeterminate_not_provider_retirement(self):
        self.exit_code = 255
        with self.assertRaises(RemoteIndeterminateError) as caught:
            self.client.agent_start("worker", "grok", "w1:p2", ["--model", "policy-chosen"])
        self.assertEqual(caught.exception.details["outcome"], "indeterminate")
        self.assertNotIn("stderr", caught.exception.details)
        self.assertNotIn("provider", caught.exception.details)

    def test_reconciliation_does_not_cross_changed_identity_or_wrong_intent(self):
        self.lost = True
        with self.assertRaises(RemoteIndeterminateError) as caught:
            self.client.pane_close("w1:p2")
        identifier = caught.exception.details["operation_id"]
        self.lost = False
        self.snapshot["panes"][0]["terminal_id"] = "replacement"
        with self.assertRaises(RemoteContextError):
            self.owner.reconcile(identifier, outcome="not_applied", evidence="no response", runner=self.runner)
        self.assertIsNotNone(json.loads(self.store.read_text(encoding="utf-8"))["pending"])
        with self.assertRaises(RemoteContextError):
            self.owner.reconcile("wrong", outcome="applied", evidence="observed", runner=self.runner)

    def test_controller_lease_lock_is_held_through_effect(self):
        real_runner = self.runner
        def contender(argv):
            if argv[3:5] == ["pane", "close"]:
                with self.assertRaises(StateError):
                    with state_lock(self.store):
                        self.fail("another controller took the lease during an effect")
            return real_runner(argv)
        HerdrClient(context=self.context, remote_authority=self.owner, runner=contender).pane_close("w1:p2")

    def test_task_callers_cannot_route_execute_choose_models_or_retire_providers(self):
        for field in ("executable", "argv", "ssh", "shell", "worker", "provider", "model",
                      "pane", "command", "foreman_prompt", "discard_provider", "machine_profile_id"):
            with self.subTest(field=field), self.assertRaises(RemoteContextError):
                self.owner.task_request({"request_id": "request", "task": "Implement the task", field: "override"})
        first = self.owner.task_request({"request_id": "request", "task": "Implement the task"})
        self.assertEqual(first, self.owner.task_request({"request_id": "request", "task": "Implement the task"}))
        self.assertEqual(first, self.owner.task_query({"task_id": first["task_id"]}))
        self.assertEqual(self.calls, [])

    def test_no_raw_process_command_or_competing_foreman_is_allowed(self):
        for effect in (lambda: self.client.process_args(123), lambda: self.client.terminate_process(123),
                       lambda: self.client.pane_close("w1:p1"),
                       lambda: self.client.pane_send_text("w1:p1", "new foreman prompt"),
                       lambda: self.client.agent_start("another-foreman", "codex", "w1:p1", [])):
            with self.assertRaises(RemoteContextError):
                effect()
        self.assertEqual(self.calls, [])

    def test_unknown_schema_and_untrusted_store_do_not_reset_pending(self):
        document = json.loads(self.store.read_text(encoding="utf-8"))
        document["schema_version"] = 2
        save_state(self.store, document)
        with self.assertRaises(RemoteContextError):
            self.client.pane_close("w1:p2")
        self.assertEqual(json.loads(self.store.read_text(encoding="utf-8"))["schema_version"], 2)
        self.store.chmod(0o644)
        with self.assertRaises(RemoteContextError):
            self.owner.task_query({"task_id": "unknown"})

    def test_remote_binding_and_observation_client_never_manufacture_native_environment(self):
        binding = supervision.load(self.owner.state_path)["binding"]
        self.assertEqual(binding["schema_version"], 2)
        self.assertEqual(binding["identity"]["kind"], "attested-remote")
        self.assertNotIn("herdr_env", binding["identity"])
        self.assertNotIn("value", binding["identity"])
        self.assertEqual(supervision_runtime.bind_current(self.owner.state_path, self.client,
                         "2026-09-01T12:00:01+00:00", environ={}), binding)
        with patch.object(supervision_runtime, "observation_runner", self.runner):
            read = supervision_runtime.read_client(context=self.context, remote_authority=self.owner)
            self.assertTrue(read.remote_preflight(self.owner.state_path)["ready"])
        with self.assertRaises(RemoteContextError):
            self.client.remote_preflight(self.root / "different-state.json")

    def test_internal_cli_context_factory_and_state_guard_preserve_native_default(self):
        from argparse import Namespace
        args = Namespace(herdr_bin=None, herdr_context=self.context, herdr_authority=self.owner)
        self.assertEqual(cli._client(args).context, self.context)
        self.assertEqual(cli._client(Namespace()).context.mode, "native")
        stderr = io.StringIO()
        result = cli.main(["status", "--state", str(self.root / "wrong.json")],
                          client=self.client, stdout=io.StringIO(), stderr=stderr)
        self.assertEqual(result, 1)
        self.assertIn("remote_owner_state_path_changed", stderr.getvalue())
        self.assertEqual(self.calls, [])

    def test_pending_transport_remains_observable_but_not_context_ready(self):
        self.lost = True
        with self.assertRaises(RemoteIndeterminateError):
            self.client.pane_close("w1:p2")
        self.lost = False
        receipt = self.client.remote_preflight(allow_indeterminate=True)
        self.assertFalse(receipt["ready"])
        self.assertIsNotNone(receipt["pending_operation"])
        self.client.agent_read("worker", source="visible")
        with self.assertRaises(RemoteIndeterminateError):
            self.client.remote_preflight()

    def test_remote_context_ready_never_substitutes_for_controller_model_tier_proof(self):
        self.assertTrue(self.client.remote_preflight()["ready"])
        stderr = io.StringIO()
        result = cli.main(["verify-foreman", "--state", str(self.owner.state_path)],
                          client=self.client, stdout=io.StringIO(), stderr=stderr)
        self.assertEqual(result, 1)
        self.assertIn("native_foreman_tier_verifier_in_remote_context", stderr.getvalue())
        self.assertEqual(self.effects(), [])

    def test_malformed_success_response_keeps_intent_and_refuses_cleanup(self):
        original = self.runner
        def lost_body(argv):
            result = original(argv)
            if argv[3:5] == ["agent", "start"]:
                result.stdout = "truncated response"
            return result
        client = HerdrClient(context=self.context, remote_authority=self.owner, runner=lost_body)
        with self.assertRaises(RemoteIndeterminateError):
            client.agent_start("worker", "codex", "w1:p2", [])
        with self.assertRaises(RemoteIndeterminateError):
            client.pane_close("w1:p2")
        self.assertEqual(len(self.effects()), 1)

    def test_task_query_uses_existing_policy_task_state_not_transport_acceptance(self):
        request = self.owner.task_request({"request_id": "request", "task": "bounded task"})
        self.assertEqual(self.owner.queued_tasks(), [{"task_id": request["task_id"], "task": "bounded task"}])
        state = empty_state()
        recovery.register_task(state["recovery"], {"task": request["task_id"], "base_revision": "a" * 40,
            "scope": "bounded fixture", "allowed_paths": ["fixture.py"],
            "authorization": {"source": "authenticated-source", "quote": "Implement bounded task"}},
            "2026-09-01T12:00:00+00:00")
        save_state(self.owner.state_path, state)
        self.assertEqual(self.owner.task_query({"task_id": request["task_id"]})["status"], "within_authorized_budget")
        self.assertEqual(self.owner.queued_tasks(), [])

    def test_corrupt_pending_or_request_rows_cannot_disappear_into_no_prior_state(self):
        original = json.loads(self.store.read_text(encoding="utf-8"))
        for name, value in (("pending", {"status": "indeterminate"}), ("requests", [{}]),
                            ("reconciliations", [{}]), ("schema_version", True)):
            document = json.loads(json.dumps(original))
            document[name] = value
            save_state(self.store, document)
            with self.assertRaises(RemoteContextError):
                self.client.pane_close("w1:p2")
            self.assertEqual(json.loads(self.store.read_text(encoding="utf-8")), document)

    def test_parallel_read_only_observations_share_the_live_controller_lease(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.client.agent_read, "worker", source="visible") for _ in range(8)]
            self.assertEqual([future.result() for future in futures], ['{"result":{}}'] * 8)
        self.assertEqual(len(self.effects()), 8)
        self.assertIsNone(json.loads(self.store.read_text(encoding="utf-8"))["pending"])

    def test_lost_owner_record_cannot_be_reinitialized_over_prior_binding(self):
        self.lost = True
        with self.assertRaises(RemoteIndeterminateError):
            self.client.pane_close("w1:p2")
        self.lost = False
        self.store.unlink()
        with self.assertRaises(RemoteContextError):
            self.owner.initialize(self.runner, at="2026-09-01T12:00:01+00:00")
        self.assertFalse(self.store.exists())
        self.assertEqual(len(self.effects()), 1)

    def test_discovery_prevents_empty_owner_recreation_when_both_records_are_lost(self):
        self.store.unlink()
        supervision.store_path(self.owner.state_path).unlink()
        with self.assertRaises(StateError):
            self.owner.initialize(self.runner, at="2026-09-01T12:00:01+00:00")
        self.assertFalse(self.store.exists())
        self.assertEqual(self.effects(), [])


if __name__ == "__main__":
    unittest.main()
