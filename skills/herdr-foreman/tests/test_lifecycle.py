"""Assignment-scoped worker lifecycle (issue #674)."""

import json
import os as _os
import sys as _sys
import unittest
from unittest.mock import Mock, call, patch

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from foreman.config import Agent
from foreman.errors import HerdrError, UsageError
from foreman.herdr import HerdrClient
from tests.fakes import FakeCompleted
from foreman.lifecycle import FRESH_SHELL_POLL_ATTEMPTS, close, identity, materialize, measure_worker_kinds, spawn


def template(name="claude", kind="claude", group="shared"):
    return Agent(
        name, kind, "/usage", "Current week", "visible", "/clear",
        composer_glyph="❯ ", window_group=group,
        tiers={"coordination": {"round": "coordination", "tier_row": "coordination",
            "kind": kind, "model": "sonnet-5", "effort": "low",
            "multiplier": 1.0, "effective_multiplier": 1.0,
            "billing_window": "unknown", "pressure_headroom": None,
            "de_escalated": False}},
        launch_args=("--dangerously-skip-permissions",), assignment_scoped=True,
    )


def missing(name):
    return HerdrError("missing", {"stderr": json.dumps(
        {"error": {"code": "agent_not_found", "message": name}})})


def failure(code, message):
    return HerdrError(message, {"stderr": json.dumps(
        {"error": {"code": code, "message": message}})})


class RepeatingReads:
    """Script observations in order, then keep returning the final one."""

    def __init__(self, observations):
        self.observations = iter(observations)
        self.last = None

    def __next__(self):
        try:
            self.last = next(self.observations)
        except StopIteration:
            if self.last is None:
                raise
        if isinstance(self.last, BaseException):
            raise self.last
        return self.last

    def __call__(self, *_args, **_kwargs):
        return next(self)


class IdentityTest(unittest.TestCase):
    def test_identity_is_safe_bounded_and_fresh(self):
        first = identity("reviewer#API", token="a" * 10)
        second = identity("reviewer#API", token="b" * 10)
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 32)
        self.assertRegex(first, r"^[a-z0-9-]+$")

    def test_materialize_keeps_kind_separate_from_live_identity(self):
        workers = materialize({"reviewer": "reviewer-a1"}, {"reviewer": "claude"}, [template()])
        self.assertEqual(workers["reviewer-a1"].name, "reviewer-a1")
        self.assertEqual(workers["reviewer-a1"].kind, "claude")
        self.assertTrue(workers["reviewer-a1"].assignment_scoped)


class SpawnCloseTest(unittest.TestCase):
    def test_spawn_creates_unfocused_workspace_and_verifies_its_root_worker(self):
        client = Mock()
        client.workspace_create.return_value = "pane-new"
        client.pane_process_info.return_value = {
            "shell_pid": 10, "foreground_processes": [{"pid": 10, "name": "zsh"}],
        }
        worker = template()
        tier = worker.tiers["coordination"]
        with patch("foreman.lifecycle.start_worker", return_value={"pane_id": "pane-new"}) as start, \
                patch("foreman.lifecycle.verify_running", return_value={"pid": 1}) as verify:
            self.assertEqual(spawn(client, worker, tier, cwd="/work", history=[], sleep=lambda _: None), "pane-new")
        client.workspace_create.assert_called_once_with(cwd="/work", label=worker.name, focus=False)
        client.pane_split.assert_not_called()
        self.assertEqual(client.pane_process_info.call_args_list, [call("pane-new")] * 3)
        start.assert_called_once_with(client, worker, "pane-new", tier, owned_fresh=True)
        verify.assert_called_once_with(client, worker, "pane-new", tier)

    def test_spawn_runs_first_start_preflight_while_the_pane_is_still_a_shell(self):
        client = Mock()
        client.workspace_create.return_value = "pane-new"
        client.pane_process_info.return_value = {
            "shell_pid": 10, "foreground_processes": [{"pid": 10, "name": "zsh"}],
        }
        worker = template()
        order = []
        with patch("foreman.lifecycle.start_worker", side_effect=lambda *_args, **_kwargs: order.append("start")), \
                patch("foreman.lifecycle.verify_running", return_value={"pid": 1}):
            spawn(
                client, worker, worker.tiers["coordination"], history=[],
                before_start=lambda pane: order.append("preflight:" + pane), sleep=lambda _: None,
            )
        self.assertEqual(order, ["preflight:pane-new", "start"])

    def test_spawn_refuses_an_identity_with_prior_assignment_history(self):
        client = Mock()
        worker = template()
        with self.assertRaisesRegex(UsageError, "already appears"):
            spawn(client, worker, worker.tiers["coordination"],
                  history=[{"agent": worker.name, "role": "reviewer"}])
        client.workspace_create.assert_not_called()

    def test_unknown_workspace_creation_never_starts_or_closes_an_unproved_surface(self):
        client = Mock()
        client.workspace_create.side_effect = HerdrError("create result unknown; inspect orphan workspace", {})
        worker = template()
        with self.assertRaisesRegex(HerdrError, "unknown"):
            spawn(client, worker, worker.tiers["coordination"], history=())
        client.workspace_create.assert_called_once()
        client.pane_process_info.assert_not_called()
        client.agent_start.assert_not_called()
        client.pane_close.assert_not_called()

    def test_spawn_reports_primary_and_cleanup_failures_with_pane_action(self):
        client = Mock()
        client.workspace_create.return_value = "pane-new"
        client.pane_process_info.return_value = {"shell_pid": 10, "foreground_processes": [{"pid": 10}]}
        client.argv_pane_close.return_value = ["herdr", "pane", "close", "--pane", "pane-new"]
        client.pane_close.side_effect = failure("pane_busy", "cannot close")
        worker = template()
        with patch("foreman.lifecycle.start_worker", side_effect=failure("start_failed", "cannot start")), \
                self.assertRaisesRegex(HerdrError, "Cleanup also failed") as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        self.assertEqual(caught.exception.details["pane_id"], "pane-new")
        self.assertIn("pane close", str(caught.exception))

    def test_spawn_closes_the_created_root_pane_before_reraising_an_interrupt(self):
        client = Mock()
        client.workspace_create.return_value = "pane-new"
        client.pane_process_info.return_value = {
            "shell_pid": 10, "foreground_processes": [{"pid": 10}]}
        worker = template()
        client.agent_get.side_effect = missing(worker.name)
        with patch("foreman.lifecycle.start_worker", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        client.pane_close.assert_called_once_with("pane-new")

    def test_cleanup_failure_does_not_replace_a_spawn_interrupt(self):
        client = Mock()
        client.workspace_create.return_value = "pane-new"
        client.pane_process_info.return_value = {
            "shell_pid": 10, "foreground_processes": [{"pid": 10}]}
        client.pane_close.side_effect = failure("pane_busy", "cannot close")
        client.argv_pane_close.return_value = ["herdr", "pane", "close", "--pane", "pane-new"]
        worker = template()
        with patch("foreman.lifecycle.start_worker", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt) as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        self.assertIn("Cleanup also failed", " ".join(caught.exception.__notes__))

    def test_close_proves_the_assignment_disappeared(self):
        client = Mock()
        client.agent_get.side_effect = [{"pane_id": "pane-new"}, missing("reviewer-a1")]
        client.pane_process_info.side_effect = [
            {"pane_id": "pane-new", "shell_pid": 10,
             "foreground_processes": [{"pid": 20, "name": "claude"}]},
            failure("pane_not_found", "gone"),
        ]
        result = close(client, "reviewer-a1", "pane-new")
        client.pane_close.assert_called_once_with("pane-new")
        self.assertTrue(result["closed"])
        self.assertFalse(result["replayed"])

    def test_close_still_closes_a_recorded_pane_when_agent_is_absent(self):
        client = Mock()
        client.agent_get.side_effect = missing("reviewer-a1")
        client.pane_process_info.side_effect = [
            {"pane_id": "pane-new", "shell_pid": 10,
             "foreground_processes": [{"pid": 10, "name": "zsh"}]},
            failure("pane_not_found", "gone"),
        ]
        result = close(client, "reviewer-a1", "pane-new")
        client.pane_close.assert_called_once_with("pane-new")
        self.assertFalse(result["replayed"])

    def test_close_replays_only_when_agent_and_pane_are_both_absent(self):
        client = Mock()
        client.agent_get.side_effect = missing("reviewer-a1")
        client.pane_process_info.side_effect = failure("pane_not_found", "gone")
        result = close(client, "reviewer-a1", "pane-new")
        self.assertTrue(result["replayed"])
        client.pane_close.assert_not_called()

    def test_close_refuses_success_while_non_shell_occupant_remains(self):
        client = Mock()
        occupied = {"pane_id": "pane-new", "shell_pid": 10,
                    "foreground_processes": [{"pid": 20, "name": "claude",
                                              "argv": ["claude", "--token", "secret"]}]}
        client.agent_get.side_effect = [{"pane_id": "pane-new"}, missing("reviewer-a1")]
        client.pane_process_info.return_value = occupied
        with self.assertRaisesRegex(HerdrError, "non-shell occupant") as caught:
            close(client, "reviewer-a1", "pane-new", sleep=lambda _: None)
        self.assertEqual(caught.exception.details["foreground_pids"], [20])
        self.assertEqual(caught.exception.details["occupant_names"], ["claude"])
        self.assertNotIn("secret", json.dumps(caught.exception.details))
        client.pane_close.assert_called_once_with("pane-new")

    def test_close_refuses_reused_pane_with_a_different_shell(self):
        client = Mock()
        client.agent_get.side_effect = [{"pane_id": "pane-new"}, missing("reviewer-a1")]
        client.pane_process_info.side_effect = [
            {"pane_id": "pane-new", "shell_pid": 10,
             "foreground_processes": [{"pid": 20, "name": "claude"}]},
            {"pane_id": "pane-new", "shell_pid": 11,
             "foreground_processes": [{"pid": 11, "name": "zsh"}]},
        ]
        with self.assertRaisesRegex(HerdrError, "reused pane"):
            close(client, "reviewer-a1", "pane-new", sleep=lambda _: None)
        client.pane_close.assert_called_once_with("pane-new")

    def test_close_accepts_the_original_shell_remaining(self):
        client = Mock()
        client.agent_get.side_effect = [{"pane_id": "pane-new"}, missing("reviewer-a1")]
        client.pane_process_info.side_effect = [
            {"pane_id": "pane-new", "shell_pid": 10,
             "foreground_processes": [{"pid": 20, "name": "claude"}]},
            {"pane_id": "pane-new", "shell_pid": 10,
             "foreground_processes": [{"pid": 10, "name": "zsh"}]},
        ]
        result = close(client, "reviewer-a1", "pane-new", sleep=lambda _: None)
        self.assertTrue(result["closed"])
        self.assertFalse(result["replayed"])


class FreshShellStartupTest(unittest.TestCase):
    SHELL = {"shell_pid": 52704, "foreground_process_group_id": 52704,
             "foreground_processes": [{"pid": 52704, "name": "zsh", "argv": ["-zsh"]}]}
    SPARSE = {"pane_id": "pane-new", "shell_pid": 52704}
    INITIALIZING = {"shell_pid": 52704, "foreground_process_group_id": 52704, "foreground_processes": [
        {"pid": 52707, "name": "zsh", "argv": ["-zsh"]},
        {"pid": 52704, "name": "zsh", "argv": ["-zsh"]}]}
    ARGV = ["claude", "--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "low"]

    def client(self, observations):
        client = Mock()
        client.workspace_create.return_value = "pane-new"
        client.pane_process_info.side_effect = RepeatingReads(observations)
        client.agent_start.return_value = {"agent": {"pane_id": "pane-new", "name": "claude",
            "agent": "claude", "agent_status": "idle"}, "argv": self.ARGV}
        client.argv_pane_close.return_value = ["herdr", "pane", "close", "--pane", "pane-new"]
        def close_pane(_pane):
            client.pane_process_info.side_effect = failure("pane_not_found", "gone")
            client.agent_get.side_effect = missing("claude")
        client.pane_close.side_effect = close_pane
        return client

    def running(self):
        return {"shell_pid": 52704, "foreground_processes": [{"pid": 53000, "name": "claude", "argv": self.ARGV}]}

    def test_recorded_shell_child_settles_before_one_verified_start(self):
        client = self.client([self.INITIALIZING, self.SHELL, self.SHELL, self.SHELL, self.running()])
        sleeps = Mock()
        worker = template()
        self.assertEqual(spawn(client, worker, worker.tiers["coordination"], history=[], sleep=sleeps), "pane-new")
        self.assertEqual(sleeps.call_count, 2)
        client.agent_start.assert_called_once_with("claude", "claude", "pane-new", self.ARGV[1:])
        client.pane_close.assert_not_called()

    def test_native_not_ready_retains_only_the_exact_blocked_first_start(self):
        original = {"pane_id": "pane-new", "name": "claude", "agent": "claude", "agent_status": "blocked"}
        for change in ({}, {"pane_id": "foreign"}, {"name": "foreign"}, {"agent": "codex"},
                       {"agent_status": "working"}, {"agent_status": "unknown"}):
            with self.subTest(change=change):
                client, worker = self.client([self.SHELL] * 3 + [self.running()] * 2), template()
                client.agent_start.side_effect = failure("agent_not_ready", "native startup dialog")
                client.agent_get.return_value = {**original, **change}
                if change:
                    with self.assertRaises(HerdrError):
                        spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
                    client.pane_close.assert_called_once_with("pane-new")
                else:
                    self.assertEqual(spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None), "pane-new")
                    client.pane_close.assert_not_called()
                client.agent_start.assert_called_once()
                client.agent_prompt.assert_not_called()
                client.pane_send_keys.assert_not_called()

    def test_blocked_start_with_a_changed_tier_is_not_accepted_or_restarted(self):
        running = self.running()
        running["foreground_processes"][0]["argv"] = ["claude", "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"]
        client, worker = self.client([self.SHELL] * 3 + [running]), template()
        client.agent_start.side_effect = failure("agent_not_ready", "native startup dialog")
        client.agent_get.return_value = {"pane_id": "pane-new", "name": "claude", "agent": "claude", "agent_status": "blocked"}
        with self.assertRaises(HerdrError):
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        client.agent_start.assert_called_once()
        client.pane_close.assert_called_once_with("pane-new")
        client.agent_prompt.assert_not_called()

    def test_ready_shell_needs_spaced_confirmation_before_start(self):
        client = self.client([self.SHELL, self.SHELL, self.SHELL, self.running()])
        sleeps = Mock()
        worker = template()
        spawn(client, worker, worker.tiers["coordination"], history=[], sleep=sleeps)
        self.assertEqual(sleeps.call_count, 1)
        client.agent_start.assert_called_once()

    def test_late_recorded_initialization_recovers_inside_the_spawn_owner(self):
        client = self.client([self.SHELL, self.SHELL, self.INITIALIZING,
                              self.SHELL, self.SHELL, self.running()])
        worker, callback = template(), Mock()
        spawn(client, worker, worker.tiers["coordination"], history=[],
              before_start=callback, sleep=lambda _: None)
        client.agent_start.assert_called_once_with("claude", "claude", "pane-new", self.ARGV[1:])
        self.assertEqual(callback.call_count, 2)
        client.pane_close.assert_not_called()

    def test_native_pre_input_busy_race_rechecks_authority_before_one_launch(self):
        client = self.client([self.SHELL] * 5 + [self.running()])
        success = client.agent_start.return_value
        client.agent_start.side_effect = [failure("agent_pane_busy", "not an available shell"), success]
        worker, callback = template(), Mock()
        spawn(client, worker, worker.tiers["coordination"], history=[],
              before_start=callback, sleep=lambda _: None)
        self.assertEqual(client.agent_start.call_count, 2)
        self.assertEqual(callback.call_count, 2)
        client.require_start_retry_compatibility.assert_called_once_with()
        client.pane_send_text.assert_not_called()
        client.pane_send_keys.assert_not_called()
        client.pane_close.assert_not_called()

    def test_unproved_native_refusal_compatibility_never_repeats_start(self):
        client = self.client([self.SHELL] * 3)
        client.agent_start.side_effect = failure("agent_pane_busy", "not an available shell")
        client.require_start_retry_compatibility.side_effect = HerdrError("retry compatibility unavailable", {})
        worker = template()
        with self.assertRaisesRegex(HerdrError, "retry compatibility"):
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        client.agent_start.assert_called_once()
        client.pane_close.assert_called_once_with("pane-new")
        client.pane_send_text.assert_not_called()
        client.pane_send_keys.assert_not_called()

    def test_retrospective_initialization_read_recovers_without_bypassing_its_gate(self):
        from foreman.retrospective_runtime import _observation
        client = self.client([self.SHELL, self.SHELL, self.INITIALIZING,
                              self.SHELL, self.SHELL, self.SHELL, self.running()])
        worker = template()
        def callback(pane):
            _observation(client, worker.name, worker.kind, pane, starting=True)
        spawn(client, worker, worker.tiers["coordination"], history=[],
              before_start=callback, sleep=lambda _: None)
        client.agent_start.assert_called_once()
        client.agent_get.assert_not_called()
        client.pane_close.assert_not_called()

    def test_native_busy_followed_by_real_occupant_refuses_before_second_input(self):
        occupied = self.running()
        occupied["foreground_processes"][0].update(
            cmdline="claude --token sensitive-token-must-stay-private",
            environment={"TOKEN": "sensitive-token-must-stay-private"},
        )
        client = self.client([self.SHELL] * 3 + [occupied, occupied,
                              failure("pane_not_found", "gone")])
        client.agent_get.side_effect = missing("claude")
        client.agent_start.side_effect = failure("agent_pane_busy", "not an available shell")
        worker = template()
        with self.assertRaisesRegex(HerdrError, "occupied") as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        self.assertEqual(caught.exception.details["foreground_pids"], [53000])
        self.assertEqual(caught.exception.details["occupant_names"], ["claude"])
        self.assertTrue(caught.exception.details["pane_closure"]["closed"])
        self.assertNotIn("sensitive-token", json.dumps(caught.exception.details))
        client.agent_start.assert_called_once()
        client.pane_close.assert_called_once_with("pane-new")

    def test_native_non_busy_errors_are_never_retried_after_possible_input(self):
        for code in ("timeout", "agent_not_ready", "input_failed", "agent_name_taken"):
            with self.subTest(code=code):
                client = self.client([self.SHELL] * 3)
                client.agent_start.side_effect = failure(code, "start outcome unknown")
                worker = template()
                with self.assertRaisesRegex(HerdrError, "start outcome unknown"):
                    spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
                client.agent_start.assert_called_once()
                client.pane_close.assert_called_once_with("pane-new")

    def test_native_busy_recovery_is_bounded_and_preserves_no_input(self):
        client = self.client([])
        client.pane_process_info.side_effect = None
        client.pane_process_info.return_value = self.SHELL
        client.agent_start.side_effect = failure("agent_pane_busy", "not an available shell")
        worker = template()
        with self.assertRaisesRegex(HerdrError, "did not settle") as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        self.assertEqual(caught.exception.details["error_code"], "agent_pane_busy")
        self.assertLessEqual(client.agent_start.call_count, FRESH_SHELL_POLL_ATTEMPTS)
        client.pane_send_text.assert_not_called()
        client.pane_send_keys.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")

    def test_native_busy_evidence_survives_later_shell_initialization_reads(self):
        client = self.client([self.SHELL] * 3 + [self.INITIALIZING] * FRESH_SHELL_POLL_ATTEMPTS)
        client.agent_start.side_effect = failure("agent_pane_busy", "not an available shell")
        worker = template()
        with self.assertRaisesRegex(HerdrError, "did not settle") as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        self.assertEqual(caught.exception.details["error_code"], "agent_pane_busy")
        client.agent_start.assert_called_once()
        client.require_start_retry_compatibility.assert_called_once_with()
        client.pane_send_text.assert_not_called()
        client.pane_send_keys.assert_not_called()

    def test_absent_foreground_waits_for_explicit_stable_shell_before_start(self):
        client = self.client([self.SPARSE, self.SHELL, self.SHELL, self.SHELL, self.running()])
        worker, callback, sleeps = template(), Mock(), Mock()
        spawn(client, worker, worker.tiers["coordination"], history=[], before_start=callback, sleep=sleeps)
        callback.assert_called_once_with("pane-new")
        self.assertEqual(sleeps.call_count, 2)
        client.agent_start.assert_called_once_with("claude", "claude", "pane-new", self.ARGV[1:])
        client.pane_close.assert_not_called()

    def test_absent_foreground_breaks_consecutive_shell_confirmation(self):
        client = self.client([self.SHELL, self.SPARSE, self.SHELL, self.SHELL, self.SHELL, self.running()])
        worker, sleeps = template(), Mock()
        spawn(client, worker, worker.tiers["coordination"], history=[], sleep=sleeps)
        self.assertEqual(sleeps.call_count, 3)
        client.agent_start.assert_called_once()

    def test_persistent_absence_times_out_without_callback_start_or_input(self):
        client = self.client([self.SPARSE] * FRESH_SHELL_POLL_ATTEMPTS)
        worker, callback, sleeps = template(), Mock(), Mock()
        with self.assertRaisesRegex(HerdrError, "did not settle") as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[], before_start=callback, sleep=sleeps)
        self.assertEqual(client.pane_process_info.call_count, FRESH_SHELL_POLL_ATTEMPTS + 1)
        self.assertEqual(sleeps.call_count, FRESH_SHELL_POLL_ATTEMPTS - 1)
        self.assertIsNone(caught.exception.details["foreground_pids"])
        self.assertEqual(caught.exception.details["ready_reads"], 0)
        callback.assert_not_called()
        client.agent_start.assert_not_called()
        client.pane_send_text.assert_not_called()
        client.pane_send_keys.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")

    def test_sparse_polling_does_not_tolerate_malformed_next_observation(self):
        invalid = [{**self.SPARSE, "foreground_processes": value} for value in (None, {}, "unknown", [None], [{"pid": True}])]
        invalid += [{**self.SPARSE, "shell_pid": value} for value in (None, 0, -1, True, "52704")]
        invalid.append({**self.SPARSE, "pane_id": "other-pane"})
        for info in invalid:
            with self.subTest(info=info):
                client = self.client([self.SPARSE, info, self.SHELL])
                worker, callback, sleeps = template(), Mock(), Mock()
                with self.assertRaisesRegex(HerdrError, "malformed"):
                    spawn(client, worker, worker.tiers["coordination"], history=[], before_start=callback, sleep=sleeps)
                self.assertEqual(client.pane_process_info.call_count, 3)
                self.assertEqual(sleeps.call_count, 1)
                callback.assert_not_called()
                client.agent_start.assert_not_called()
                client.pane_close.assert_called_once_with("pane-new")

    def test_sparse_observations_still_bind_original_shell_pid(self):
        replacements = [{**self.SPARSE, "shell_pid": 52708},
            {"shell_pid": 52708, "foreground_processes": [{"pid": 52708}]}]
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                client = self.client([self.SPARSE, replacement])
                worker, callback = template(), Mock()
                with self.assertRaisesRegex(HerdrError, "shell changed"):
                    spawn(client, worker, worker.tiers["coordination"], history=[], before_start=callback, sleep=lambda _: None)
                callback.assert_not_called()
                client.agent_start.assert_not_called()
                client.pane_close.assert_called_once_with("pane-new")

    def test_api_failure_after_sparse_observation_is_not_retried(self):
        client = self.client([self.SPARSE, failure("read_failed", "unavailable"), self.SHELL])
        worker, callback = template(), Mock()
        with self.assertRaisesRegex(HerdrError, "Cannot read fresh pane"):
            spawn(client, worker, worker.tiers["coordination"], history=[], before_start=callback, sleep=lambda _: None)
        self.assertEqual(client.pane_process_info.call_count, 3)
        callback.assert_not_called()
        client.agent_start.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")

    def test_post_callback_absence_waits_for_the_same_root_without_proving_readiness(self):
        client = self.client([self.SPARSE, self.SHELL, self.SHELL, self.SPARSE,
                              self.SHELL, self.SHELL, self.running()])
        worker, callback, sleeps = template(), Mock(), Mock()
        spawn(client, worker, worker.tiers["coordination"], history=[], before_start=callback, sleep=sleeps)
        self.assertEqual(callback.call_count, 2)
        client.agent_start.assert_called_once()
        client.pane_close.assert_not_called()

    def test_retrospective_sparse_read_recovers_only_after_explicit_shell_evidence(self):
        from foreman.retrospective_runtime import _observation
        client = self.client([self.SHELL, self.SHELL, self.SPARSE,
                              self.SHELL, self.SHELL, self.SHELL, self.running()])
        worker = template()
        def callback(pane):
            _observation(client, worker.name, worker.kind, pane, starting=True)
        spawn(client, worker, worker.tiers["coordination"], history=[],
              before_start=callback, sleep=lambda _: None)
        client.agent_start.assert_called_once()
        client.agent_get.assert_not_called()

    def test_persistent_extra_process_exhausts_without_callback_or_start(self):
        client = self.client([self.INITIALIZING] * FRESH_SHELL_POLL_ATTEMPTS)
        sleeps, callback = Mock(), Mock()
        worker = template()
        with self.assertRaisesRegex(HerdrError, "inspect the shell startup") as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[], before_start=callback, sleep=sleeps)
        self.assertEqual(client.pane_process_info.call_count, FRESH_SHELL_POLL_ATTEMPTS + 1)
        self.assertEqual(sleeps.call_count, FRESH_SHELL_POLL_ATTEMPTS - 1)
        self.assertEqual(caught.exception.details["foreground_pids"], [52707, 52704])
        self.assertEqual(caught.exception.details["pane"], "pane-new")
        callback.assert_not_called()
        client.agent_start.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")

    def test_malformed_process_records_fail_without_waiting_or_start(self):
        invalid = [None, {}, {**self.SHELL, "shell_pid": None}, {**self.SHELL, "shell_pid": 0},
            {**self.SHELL, "shell_pid": -1}, {**self.SHELL, "shell_pid": True},
            {**self.SHELL, "foreground_processes": None}, {**self.SHELL, "foreground_processes": {}},
            {**self.SHELL, "foreground_processes": [None]}, {**self.SHELL, "foreground_processes": [{}]},
            {**self.SHELL, "foreground_processes": [{"pid": True}]},
            {**self.SHELL, "foreground_processes": [{"pid": 0}]},
            {**self.SHELL, "foreground_processes": [{"pid": "52704"}]},
            {**self.SHELL, "pane_id": "other-pane"}]
        worker = template()
        for info in invalid:
            with self.subTest(info=info):
                client = self.client([info])
                sleeps = Mock()
                with self.assertRaisesRegex(HerdrError, "malformed"):
                    spawn(client, worker, worker.tiers["coordination"], history=[], sleep=sleeps)
                sleeps.assert_not_called()
                client.agent_start.assert_not_called()
                client.pane_close.assert_called_once_with("pane-new")

    def test_process_info_failure_is_not_retried_as_shell_readiness(self):
        client = self.client([failure("read_failed", "unavailable")])
        worker, sleeps = template(), Mock()
        with self.assertRaisesRegex(HerdrError, "Cannot read fresh pane"):
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=sleeps)
        sleeps.assert_not_called()
        client.agent_start.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")

    def test_replaced_shell_is_refused_even_when_the_replacement_is_alone(self):
        replacement = {"shell_pid": 52708, "foreground_processes": [{"pid": 52708}]}
        for observations in ([self.INITIALIZING, replacement], [self.SHELL, replacement]):
            with self.subTest(observations=observations):
                client, worker = self.client(observations), template()
                with self.assertRaisesRegex(HerdrError, "shell changed"):
                    spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
                client.agent_start.assert_not_called()
                client.pane_close.assert_called_once_with("pane-new")

    def test_wait_interrupt_preserves_interrupt_and_cleanup_failure_note(self):
        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails):
                client, worker = self.client([self.INITIALIZING]), template()
                if cleanup_fails:
                    client.pane_close.side_effect = failure("pane_busy", "cannot close")
                with self.assertRaises(KeyboardInterrupt) as caught:
                    spawn(client, worker, worker.tiers["coordination"], history=[],
                          sleep=Mock(side_effect=KeyboardInterrupt))
                client.pane_close.assert_called_once_with("pane-new")
                client.agent_start.assert_not_called()
                if cleanup_fails:
                    self.assertIn("Cleanup also failed", " ".join(caught.exception.__notes__))

    def test_callback_occupies_ready_shell_and_final_proof_refuses(self):
        client = self.client([self.SHELL, self.SHELL, self.running()])
        worker, callback, sleeps = template(), Mock(), Mock()
        with self.assertRaisesRegex(HerdrError, "occupied"):
            spawn(client, worker, worker.tiers["coordination"], history=[], before_start=callback, sleep=sleeps)
        callback.assert_called_once_with("pane-new")
        self.assertEqual(sleeps.call_count, 1)
        client.agent_start.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")

    def test_retrospective_start_refusal_cleans_only_created_pane_and_proves_closure(self):
        from foreman.retrospective_runtime import _observation
        client = self.client([self.SHELL, self.SHELL, self.running()])
        worker = template()
        def callback(pane):
            _observation(client, worker.name, worker.kind, pane, starting=True)
        with self.assertRaisesRegex(HerdrError, "sole shell"):
            spawn(client, worker, worker.tiers["coordination"], history=(),
                  before_start=callback, sleep=lambda _: None)
        client.agent_get.assert_called_once_with("claude")
        client.agent_start.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")

    def test_late_unknown_children_are_not_reclassified_as_login_initialization(self):
        import copy
        for change in ({"argv": ["zsh", "-c", "work"]}, {"argv": None}, {"name": None}, {"name": "vim"}):
            with self.subTest(change=change):
                occupied = copy.deepcopy(self.INITIALIZING)
                occupied["foreground_processes"][0].update(change)
                client, worker = self.client([self.SHELL, self.SHELL, occupied]), template()
                with self.assertRaisesRegex(HerdrError, "occupied"):
                    spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
                client.agent_start.assert_not_called()
                client.pane_close.assert_called_once_with("pane-new")
        moved_group = {**self.INITIALIZING, "foreground_process_group_id": 52707}
        client, worker = self.client([self.SHELL, self.SHELL, moved_group]), template()
        with self.assertRaisesRegex(HerdrError, "occupied"):
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        client.agent_start.assert_not_called()

    def test_busy_retry_refuses_a_replaced_root_shell_without_second_start(self):
        replacement = {"shell_pid": 52708, "foreground_processes": [{"pid": 52708}]}
        client = self.client([self.SHELL] * 3 + [replacement])
        client.agent_start.side_effect = failure("agent_pane_busy", "not an available shell")
        worker = template()
        with self.assertRaisesRegex(HerdrError, "shell changed"):
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        client.agent_start.assert_called_once()
        client.pane_close.assert_called_once_with("pane-new")

    def test_new_startup_child_after_initial_readiness_settles_before_launch(self):
        # Live startup briefly reports the root alone, then a later init child.
        client = self.client([self.SHELL, self.INITIALIZING, self.SHELL, self.SHELL, self.SHELL, self.running()])
        worker, sleeps = template(), Mock()
        spawn(client, worker, worker.tiers["coordination"], history=[], sleep=sleeps)
        self.assertEqual(sleeps.call_count, 3)
        client.agent_start.assert_called_once_with("claude", "claude", "pane-new", self.ARGV[1:])
        client.pane_close.assert_not_called()

    def test_intermittent_sole_shell_never_satisfies_stable_readiness(self):
        observations = [self.SHELL, self.INITIALIZING] * (FRESH_SHELL_POLL_ATTEMPTS // 2)
        client, worker, callback = self.client(observations), template(), Mock()
        with self.assertRaisesRegex(HerdrError, "did not settle"):
            spawn(client, worker, worker.tiers["coordination"], history=[],
                  before_start=callback, sleep=lambda _: None)
        callback.assert_not_called()
        client.agent_start.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")

    def test_final_proof_still_binds_original_shell_identity(self):
        replacement = {"shell_pid": 52708, "foreground_processes": [{"pid": 52708}]}
        client = self.client([self.SHELL, self.SHELL, replacement])
        worker = template()
        with self.assertRaisesRegex(HerdrError, "shell changed"):
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        client.agent_start.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")

    def test_interrupt_during_confirmation_keeps_cleanup_and_primary_interrupt(self):
        client = self.client([self.SHELL])
        client.pane_close.side_effect = failure("pane_busy", "cannot close")
        worker = template()
        with self.assertRaises(KeyboardInterrupt) as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=Mock(side_effect=KeyboardInterrupt))
        client.agent_start.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")
        self.assertIn("Cleanup also failed", " ".join(caught.exception.__notes__))

    def test_callback_executes_between_ready_and_final_proof(self):
        client = self.client([self.SHELL, self.SHELL, self.SHELL, self.running()])
        worker, order = template(), []
        reads = client.pane_process_info.side_effect
        def observe(_pane):
            order.append("read")
            return next(reads)
        client.pane_process_info.side_effect = observe
        spawn(client, worker, worker.tiers["coordination"], history=[],
              before_start=lambda _pane: order.append("callback"), sleep=lambda _: None)
        self.assertEqual(order[:4], ["read", "read", "callback", "read"])

    def test_timeout_diagnostic_does_not_include_process_command_secrets(self):
        secret = "sensitive-token-must-stay-private"
        info = {"shell_pid": 52704, "foreground_processes": [
            {"pid": 52707, "argv": [secret], "cmdline": secret, "environment": {"TOKEN": secret}}]}
        client, worker = self.client([info] * FRESH_SHELL_POLL_ATTEMPTS), template()
        with self.assertRaises(HerdrError) as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[], sleep=lambda _: None)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, json.dumps(caught.exception.details))


class WindowProbeTest(unittest.TestCase):
    def test_startup_dialog_is_retained_without_usage_input_or_capacity_inference(self):
        import tempfile
        from pathlib import Path
        for kind in ("codex", "claude", "grok"):
            for state in ("idle", "blocked"):
                with self.subTest(kind=kind, state=state), tempfile.TemporaryDirectory() as temporary:
                    worker, client = template(kind, kind, kind), Mock()
                    worker.tiers["coordination"] = {"model": kind + "-fixture", "effort": "low", "multiplier": 1.0}
                    client.agent_get.return_value = {"pane_id": "owned-probe", "agent_status": state, "name": "probe-fixed", "agent": kind}
                    client.agent_read.return_value = "Native startup dialog"
                    client.argv_pane_close.return_value = ["herdr", "pane", "close", "owned-probe"]
                    with patch("foreman.lifecycle.spawn", return_value="owned-probe"), \
                            patch("foreman.lifecycle.identity", return_value="probe-fixed"), \
                            patch("foreman.lifecycle.verify_running", return_value={"pid": 71, "source": "process_argv", "pane_id": "owned-probe"}), \
                            patch("foreman.lifecycle.close") as closed, patch("foreman.measure.measure") as usage:
                        result = measure_worker_kinds(client, [worker], "2026-10-08T00:00:00+00:00", state_path=Path(temporary) / "state.json", sleep=lambda _: None)
                    closed.assert_not_called()
                    usage.assert_not_called()
                    client.agent_prompt.assert_not_called()
                    client.agent_send_keys.assert_not_called()
                    client.pane_send_keys.assert_not_called()
                    self.assertEqual(result["failed_agents"], [kind])
                    self.assertIsNone(result["agents"][kind]["headroom_pct"])
                    self.assertIn("owned-probe", result["agents"][kind]["error"]["message"])
                    self.assertIn("Runtime Dialogs", result["agents"][kind]["error"]["message"])

    def test_failed_probe_preserves_catalog_and_unknown_capacity_beside_healthy_providers(self):
        import copy
        workers = [template("claude", "claude", "anthropic"),
                   template("codex", "codex", "openai"), template("grok", "grok", "xai")]
        catalog = copy.deepcopy([(worker.name, worker.kind, worker.tiers) for worker in workers])
        unavailable = {"claude"}
        def launched(_client, worker, _tier, **_options):
            if worker.kind in unavailable:
                raise failure("agent_pane_busy", "startup observation unavailable")
            return "pane-" + worker.kind
        def measured(_client, probes, _at, **_options):
            return {"agents": {probes[0].name: {"kind": probes[0].kind,
                     "headroom_pct": 89.0, "windows": []}}}
        with patch("foreman.lifecycle.identity", side_effect=lambda role: role + "-fixed"), \
                patch("foreman.lifecycle.spawn", side_effect=launched), \
                patch("foreman.lifecycle._prepare_fresh_probe"), \
                patch("foreman.lifecycle.close") as closed, patch("foreman.measure.measure", side_effect=measured):
            result = measure_worker_kinds(Mock(), workers, "2026-10-01T00:00:00+00:00")
        self.assertEqual(set(result["agents"]), {"claude", "codex", "grok"})
        self.assertEqual(result["failed_agents"], ["claude"])
        self.assertIsNone(result["agents"]["claude"]["headroom_pct"])
        self.assertIsNone(result["agents"]["claude"]["windows"])
        self.assertEqual(result["agents"]["codex"]["headroom_pct"], 89.0)
        self.assertEqual(result["agents"]["grok"]["headroom_pct"], 89.0)
        self.assertEqual([(worker.name, worker.kind, worker.tiers) for worker in workers], catalog)
        self.assertEqual(closed.call_count, 2)
        unavailable.clear()
        with patch("foreman.lifecycle.identity", side_effect=lambda role: role + "-fixed"), \
                patch("foreman.lifecycle.spawn", side_effect=launched), \
                patch("foreman.lifecycle._prepare_fresh_probe"), \
                patch("foreman.lifecycle.close"), patch("foreman.measure.measure", side_effect=measured):
            recovered = measure_worker_kinds(Mock(), workers, "2026-10-01T00:01:00+00:00")
        self.assertEqual(recovered["failed_agents"], [])
        self.assertEqual(recovered["agents"]["claude"]["headroom_pct"], 89.0)
        self.assertEqual([(worker.name, worker.kind, worker.tiers) for worker in workers], catalog)

    def test_persistent_native_account_refusal_keeps_unknown_capacity_and_its_actual_cause(self):
        worker = template()
        native = failure("agent_not_ready", "account access denied")
        with patch("foreman.lifecycle.identity", return_value="probe-claude-fixed"), \
                patch("foreman.lifecycle.spawn", side_effect=native) as launches, \
                patch("foreman.measure.measure") as usage:
            result = measure_worker_kinds(Mock(), [worker], "2026-10-01T00:00:00+00:00")
        launches.assert_called_once()
        usage.assert_not_called()
        self.assertEqual(result["failed_agents"], [worker.name])
        self.assertIsNone(result["agents"][worker.name]["headroom_pct"])
        self.assertIn("account access denied", json.dumps(result["agents"][worker.name]["error"]))
        self.assertEqual(worker.name, "claude")

    def test_settled_probe_keeps_a_measurement_failure_and_closes_its_pane(self):
        fixture = FreshShellStartupTest()
        client = fixture.client([fixture.INITIALIZING, fixture.SHELL, fixture.SHELL, fixture.SHELL, fixture.running()])
        client.agent_start.return_value["agent"]["name"] = "probe-fixed"
        client.agent_get.side_effect = [{"pane_id": "pane-new"}, missing("probe-fixed")]
        sleeps = Mock()
        def measured(_client, probes, measured_at, **_options):
            return {"agents": {probes[0].name: {"windows": None, "headroom_pct": None,
                "error": {"code": "parse_error", "message": "usage unavailable"}}}}
        with patch("foreman.lifecycle.identity", return_value="probe-fixed"), \
                patch("foreman.lifecycle._prepare_fresh_probe"), \
                patch("foreman.measure.measure", side_effect=measured) as usage:
            result = measure_worker_kinds(client, [template()], "2026-10-01T00:00:00+00:00", sleep=sleeps)
        self.assertEqual(sleeps.call_count, 2)
        usage.assert_called_once()
        self.assertEqual(result["failed_agents"], ["claude"])
        self.assertIsNone(result["agents"]["claude"]["headroom_pct"])
        self.assertEqual(result["agents"]["claude"]["error"]["code"], "parse_error")
        client.pane_close.assert_called_once_with("pane-new")

    def test_probe_startup_exhaustion_reports_failure_without_usage_read(self):
        fixture = FreshShellStartupTest()
        client = fixture.client([fixture.INITIALIZING] * FRESH_SHELL_POLL_ATTEMPTS)
        with patch("foreman.lifecycle.identity", return_value="probe-fixed"), \
                patch("foreman.measure.measure") as usage:
            result = measure_worker_kinds(client, [template()], "2026-10-01T00:00:00+00:00", sleep=lambda _: None)
        usage.assert_not_called()
        client.agent_start.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")
        self.assertEqual(result["failed_agents"], ["claude"])
        self.assertIsNone(result["agents"]["claude"]["headroom_pct"])
        self.assertIn("did not settle", result["agents"]["claude"]["error"]["message"])

    def test_one_probe_measurement_is_shared_by_one_window_group(self):
        workers = [template("claude", "claude"), template("codex", "codex")]
        client = Mock()

        def measured(_client, probes, measured_at, **_options):
            probe = probes[0]
            return {"schema_version": 4, "measured_at": measured_at,
                    "agents": {probe.name: {"kind": probe.kind, "state": "idle",
                        "herdr_state": "idle", "state_source": "herdr", "pane_id": "probe-pane",
                        "windows": [], "credits": None, "plan": None, "headroom_pct": 90.0,
                        "window_group": "shared", "skipped": False, "tier_billing": {}}},
                    "failed_agents": []}

        with patch("foreman.lifecycle.spawn", return_value="probe-pane") as started, \
                patch("foreman.lifecycle._prepare_fresh_probe"), \
                patch("foreman.lifecycle.identity", return_value="probe-claude-fixed"), \
                patch("foreman.measure.measure", side_effect=measured) as usage, \
                patch("foreman.lifecycle.close") as stopped:
            result = measure_worker_kinds(client, workers, "2026-10-01T00:00:00+00:00")
        self.assertEqual(started.call_count, 1)
        self.assertEqual(usage.call_count, 1)
        self.assertEqual(stopped.call_count, 1)
        self.assertEqual(set(result["agents"]), {"claude", "codex"})
        self.assertTrue(all(row["pane_id"] is None for row in result["agents"].values()))

    def test_implicit_self_window_does_not_collide_with_an_explicit_group_name(self):
        workers = [template("foo", "claude", ""), template("bar", "codex", "foo")]
        client = Mock()

        def measured(_client, probes, measured_at, **_options):
            probe = probes[0]
            return {"schema_version": 4, "measured_at": measured_at,
                    "agents": {probe.name: {"kind": probe.kind, "state": "idle",
                        "herdr_state": "idle", "state_source": "herdr", "pane_id": "probe-pane",
                        "windows": [], "credits": None, "plan": None, "headroom_pct": 90.0,
                        "window_group": None, "skipped": False, "tier_billing": {}}},
                    "failed_agents": []}

        with patch("foreman.lifecycle.spawn", side_effect=["pane-foo", "pane-bar"]) as started, \
                patch("foreman.lifecycle._prepare_fresh_probe"), \
                patch("foreman.lifecycle.identity", side_effect=["probe-foo-fixed", "probe-bar-fixed"]), \
                patch("foreman.measure.measure", side_effect=measured) as usage, \
                patch("foreman.lifecycle.close"):
            measure_worker_kinds(client, workers, "2026-10-01T00:00:00+00:00")
        self.assertEqual(started.call_count, 2)
        self.assertEqual(usage.call_count, 2)

    def test_probe_cleanup_failure_is_attached_to_an_interrupt(self):
        client = Mock()
        client.argv_pane_close.return_value = ["herdr", "pane", "close", "--pane", "probe-pane"]
        with patch("foreman.lifecycle.spawn", return_value="probe-pane"), \
                patch("foreman.lifecycle._prepare_fresh_probe"), \
                patch("foreman.lifecycle.identity", return_value="probe-fixed"), \
                patch("foreman.measure.measure", side_effect=KeyboardInterrupt), \
                patch("foreman.lifecycle.close", side_effect=failure("pane_busy", "cannot close")), \
                self.assertRaises(KeyboardInterrupt) as caught:
            measure_worker_kinds(client, [template()], "2026-10-01T00:00:00+00:00")
        notes = " ".join(caught.exception.__notes__)
        self.assertIn("Probe cleanup also failed", notes)
        self.assertIn("herdr pane close --pane probe-pane", notes)


class WorkspacePlacementTest(unittest.TestCase):
    """Stateful transport replay preserves the focused foreman workspace."""

    def client(self, *, unknown_start=False):
        focused = "w1"
        created = {}
        calls = []
        flags = ["claude", "--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "low"]

        def runner(argv):
            nonlocal focused
            calls.append(argv)
            op = argv[1:3]
            if op == ["workspace", "create"]:
                if "--focus" in argv:
                    focused = "w9"
                created["w9:p7"] = False
                result = {"workspace": {"workspace_id": "w9"}, "tab": {"tab_id": "w9:t2"},
                          "root_pane": {"pane_id": "w9:p7", "workspace_id": "w9"}}
            elif op == ["pane", "process-info"]:
                pane = argv[-1]
                if pane not in created:
                    return FakeCompleted(1, "", json.dumps(
                        {"error": {"code": "pane_not_found", "message": "pane not found"}}))
                result = {"process_info": {"pane_id": pane, "shell_pid": 10,
                    "foreground_processes": [{"pid": 20, "name": "claude", "argv": flags}]
                    if created[pane] else [{"pid": 10, "name": "zsh"}]}}
            elif op == ["agent", "start"]:
                pane = argv[argv.index("--pane") + 1]
                if pane not in created:
                    raise AssertionError("start outside created surface")
                created[pane] = True
                if unknown_start:
                    return FakeCompleted(stdout="truncated start response")
                result = {"agent": {"name": argv[3], "agent": "claude", "pane_id": pane, "agent_status": "idle"}, "argv": flags}
            elif op == ["pane", "close"]:
                if argv[-1] not in created:
                    raise AssertionError("cleanup outside created surface")
                del created[argv[-1]]
                result = {"closed": True}
            elif op == ["agent", "get"]:
                return FakeCompleted(1, "", json.dumps({"error": {"code": "agent_not_found"}}))
            else:
                raise AssertionError("unexpected command: " + str(argv))
            return FakeCompleted(stdout=json.dumps({"result": result}))

        return HerdrClient(binary="herdr", runner=runner), calls, created, lambda: focused

    def test_worker_starts_only_in_returned_workspace_root_and_preserves_focus(self):
        client, calls, created, focus = self.client()
        worker = template()
        preflight = Mock()
        pane = spawn(client, worker, worker.tiers["coordination"], cwd="/owned checkout", history=(),
                     before_start=preflight, sleep=lambda _: None)
        self.assertEqual(pane, "w9:p7")
        self.assertEqual(focus(), "w1")
        self.assertEqual(set(created), {"w9:p7"})
        preflight.assert_called_once_with("w9:p7")
        self.assertEqual(calls[0], ["herdr", "workspace", "create", "--cwd", "/owned checkout",
                                   "--label", "claude", "--no-focus"])
        self.assertFalse(any(argv[1:3] == ["pane", "split"] for argv in calls))

    def test_unknown_start_never_retries_and_cleans_only_created_root(self):
        client, calls, created, focus = self.client(unknown_start=True)
        worker = template()
        with self.assertRaises(HerdrError):
            spawn(client, worker, worker.tiers["coordination"], history=(), sleep=lambda _: None)
        self.assertEqual(focus(), "w1")
        self.assertEqual(created, {})
        self.assertEqual(sum(argv[1:3] == ["agent", "start"] for argv in calls), 1)
        self.assertEqual(calls[-3], ["herdr", "pane", "close", "w9:p7"])
        self.assertEqual(calls[-2], ["herdr", "agent", "get", "claude"])
        self.assertEqual(calls[-1], ["herdr", "pane", "process-info", "--pane", "w9:p7"])

    def test_probe_measurement_uses_separate_root_without_changing_focus_and_closes_it(self):
        client, calls, created, focus = self.client()
        def measured(_client, probes, measured_at, **_options):
            self.assertEqual(set(created), {"w9:p7"})
            self.assertEqual(focus(), "w1")
            return {"agents": {probes[0].name: {"headroom_pct": 90, "pane_id": "w9:p7"}}}
        with patch("foreman.lifecycle.identity", return_value="probe-claude-fixed"), \
                patch("foreman.lifecycle._prepare_fresh_probe"), \
                patch("foreman.measure.measure", side_effect=measured):
            result = measure_worker_kinds(client, [template()], "2026-10-01T00:00:00+00:00", sleep=lambda _: None)
        self.assertEqual(result["failed_agents"], [])
        self.assertEqual(result["agents"]["claude"]["headroom_pct"], 90)
        self.assertEqual(created, {})
        self.assertEqual(focus(), "w1")
        self.assertIn("probe-claude-fixed", calls[0])
        self.assertIn("--no-focus", calls[0])
        self.assertFalse(any(argv[1:3] == ["pane", "split"] for argv in calls))


if __name__ == "__main__":
    unittest.main()
