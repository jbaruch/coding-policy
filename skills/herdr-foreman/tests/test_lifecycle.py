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
    def test_spawn_splits_starts_and_verifies_one_fresh_worker(self):
        client = Mock()
        client.pane_split.return_value = "pane-new"
        client.pane_process_info.return_value = {
            "shell_pid": 10, "foreground_processes": [{"pid": 10, "name": "zsh"}],
        }
        worker = template()
        tier = worker.tiers["coordination"]
        with patch("foreman.lifecycle.start_worker", return_value={"pane_id": "pane-new"}) as start, \
                patch("foreman.lifecycle.verify_running", return_value={"pid": 1}) as verify:
            self.assertEqual(spawn(client, worker, tier, cwd="/work", history=[]), "pane-new")
        client.pane_split.assert_called_once_with(current=True, cwd="/work", focus=False)
        self.assertEqual(client.pane_process_info.call_args_list, [call("pane-new"), call("pane-new")])
        start.assert_called_once_with(client, worker, "pane-new", tier)
        verify.assert_called_once_with(client, worker, "pane-new", tier)

    def test_spawn_runs_first_start_preflight_while_the_pane_is_still_a_shell(self):
        client = Mock()
        client.pane_split.return_value = "pane-new"
        client.pane_process_info.return_value = {
            "shell_pid": 10, "foreground_processes": [{"pid": 10, "name": "zsh"}],
        }
        worker = template()
        order = []
        with patch("foreman.lifecycle.start_worker", side_effect=lambda *_args: order.append("start")), \
                patch("foreman.lifecycle.verify_running", return_value={"pid": 1}):
            spawn(
                client, worker, worker.tiers["coordination"], history=[],
                before_start=lambda pane: order.append("preflight:" + pane),
            )
        self.assertEqual(order, ["preflight:pane-new", "start"])

    def test_spawn_refuses_an_identity_with_prior_assignment_history(self):
        client = Mock()
        worker = template()
        with self.assertRaisesRegex(UsageError, "already appears"):
            spawn(client, worker, worker.tiers["coordination"],
                  history=[{"agent": worker.name, "role": "reviewer"}])
        client.pane_split.assert_not_called()

    def test_spawn_reports_primary_and_cleanup_failures_with_pane_action(self):
        client = Mock()
        client.pane_split.return_value = "pane-new"
        client.pane_process_info.return_value = {"shell_pid": 10, "foreground_processes": [{"pid": 10}]}
        client.argv_pane_close.return_value = ["herdr", "pane", "close", "--pane", "pane-new"]
        client.pane_close.side_effect = failure("pane_busy", "cannot close")
        worker = template()
        with patch("foreman.lifecycle.start_worker", side_effect=failure("start_failed", "cannot start")), \
                self.assertRaisesRegex(HerdrError, "Cleanup also failed") as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[])
        self.assertEqual(caught.exception.details["pane_id"], "pane-new")
        self.assertIn("pane close", str(caught.exception))

    def test_spawn_closes_the_split_pane_before_reraising_an_interrupt(self):
        client = Mock()
        client.pane_split.return_value = "pane-new"
        client.pane_process_info.return_value = {
            "shell_pid": 10, "foreground_processes": [{"pid": 10}]}
        worker = template()
        with patch("foreman.lifecycle.start_worker", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            spawn(client, worker, worker.tiers["coordination"], history=[])
        client.pane_close.assert_called_once_with("pane-new")

    def test_cleanup_failure_does_not_replace_a_spawn_interrupt(self):
        client = Mock()
        client.pane_split.return_value = "pane-new"
        client.pane_process_info.return_value = {
            "shell_pid": 10, "foreground_processes": [{"pid": 10}]}
        client.pane_close.side_effect = failure("pane_busy", "cannot close")
        client.argv_pane_close.return_value = ["herdr", "pane", "close", "--pane", "pane-new"]
        worker = template()
        with patch("foreman.lifecycle.start_worker", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt) as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[])
        self.assertIn("Cleanup also failed", " ".join(caught.exception.__notes__))

    def test_close_proves_the_assignment_disappeared(self):
        client = Mock()
        client.agent_get.side_effect = [{"pane_id": "pane-new"}, missing("reviewer-a1")]
        result = close(client, "reviewer-a1", "pane-new")
        client.pane_close.assert_called_once_with("pane-new")
        self.assertTrue(result["closed"])
        self.assertFalse(result["replayed"])

    def test_close_still_closes_a_recorded_pane_when_agent_is_absent(self):
        client = Mock()
        client.agent_get.side_effect = missing("reviewer-a1")
        result = close(client, "reviewer-a1", "pane-new")
        client.pane_close.assert_called_once_with("pane-new")
        self.assertFalse(result["replayed"])

    def test_close_replays_only_when_agent_and_pane_are_both_absent(self):
        client = Mock()
        client.agent_get.side_effect = missing("reviewer-a1")
        client.pane_close.side_effect = failure("pane_not_found", "gone")
        result = close(client, "reviewer-a1", "pane-new")
        self.assertTrue(result["replayed"])


class FreshShellStartupTest(unittest.TestCase):
    SHELL = {"shell_pid": 52704, "foreground_processes": [{"pid": 52704, "name": "-zsh"}]}
    INITIALIZING = {"shell_pid": 52704, "foreground_processes": [
        {"pid": 52707, "name": "-zsh"}, {"pid": 52704, "name": "-zsh"}]}
    ARGV = ["claude", "--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "low"]

    def client(self, observations):
        client = Mock()
        client.pane_split.return_value = "pane-new"
        client.pane_process_info.side_effect = observations
        client.agent_start.return_value = {"agent": {"pane_id": "pane-new", "name": "claude",
            "agent": "claude", "agent_status": "idle"}, "argv": self.ARGV}
        client.argv_pane_close.return_value = ["herdr", "pane", "close", "--pane", "pane-new"]
        return client

    def running(self):
        return {"shell_pid": 52704, "foreground_processes": [{"pid": 53000, "name": "claude", "argv": self.ARGV}]}

    def test_recorded_shell_child_settles_before_one_verified_start(self):
        client = self.client([self.INITIALIZING, self.SHELL, self.SHELL, self.running()])
        sleeps = Mock()
        worker = template()
        self.assertEqual(spawn(client, worker, worker.tiers["coordination"], history=[], sleep=sleeps), "pane-new")
        self.assertEqual(sleeps.call_count, 1)
        client.agent_start.assert_called_once_with("claude", "claude", "pane-new", self.ARGV[1:])
        client.pane_close.assert_not_called()

    def test_already_ready_shell_starts_without_sleep(self):
        client = self.client([self.SHELL, self.SHELL, self.running()])
        sleeps = Mock()
        worker = template()
        spawn(client, worker, worker.tiers["coordination"], history=[], sleep=sleeps)
        sleeps.assert_not_called()
        client.agent_start.assert_called_once()

    def test_persistent_extra_process_exhausts_without_callback_or_start(self):
        client = self.client([self.INITIALIZING] * FRESH_SHELL_POLL_ATTEMPTS)
        sleeps, callback = Mock(), Mock()
        worker = template()
        with self.assertRaisesRegex(HerdrError, "inspect the shell startup") as caught:
            spawn(client, worker, worker.tiers["coordination"], history=[], before_start=callback, sleep=sleeps)
        self.assertEqual(client.pane_process_info.call_count, FRESH_SHELL_POLL_ATTEMPTS)
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
        client = self.client([self.SHELL, self.INITIALIZING])
        worker, callback, sleeps = template(), Mock(), Mock()
        with self.assertRaisesRegex(HerdrError, "occupied"):
            spawn(client, worker, worker.tiers["coordination"], history=[], before_start=callback, sleep=sleeps)
        callback.assert_called_once_with("pane-new")
        sleeps.assert_not_called()
        client.agent_start.assert_not_called()
        client.pane_close.assert_called_once_with("pane-new")

    def test_callback_executes_between_ready_and_final_proof(self):
        client = self.client([self.SHELL, self.SHELL, self.running()])
        worker, order = template(), []
        reads = client.pane_process_info.side_effect
        def observe(_pane):
            order.append("read")
            return next(reads)
        client.pane_process_info.side_effect = observe
        spawn(client, worker, worker.tiers["coordination"], history=[],
              before_start=lambda _pane: order.append("callback"), sleep=lambda _: None)
        self.assertEqual(order[:3], ["read", "callback", "read"])

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
    def test_settled_probe_keeps_a_measurement_failure_and_closes_its_pane(self):
        fixture = FreshShellStartupTest()
        client = fixture.client([fixture.INITIALIZING, fixture.SHELL, fixture.SHELL, fixture.running()])
        client.agent_start.return_value["agent"]["name"] = "probe-fixed"
        client.agent_get.side_effect = [{"pane_id": "pane-new"}, missing("probe-fixed")]
        sleeps = Mock()
        def measured(_client, probes, measured_at, **_options):
            return {"agents": {probes[0].name: {"windows": None, "headroom_pct": None,
                "error": {"code": "parse_error", "message": "usage unavailable"}}}}
        with patch("foreman.lifecycle.identity", return_value="probe-fixed"), \
                patch("foreman.measure.measure", side_effect=measured) as usage:
            result = measure_worker_kinds(client, [template()], "2026-10-01T00:00:00+00:00", sleep=sleeps)
        self.assertEqual(sleeps.call_count, 1)
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
                patch("foreman.lifecycle.identity", return_value="probe-fixed"), \
                patch("foreman.measure.measure", side_effect=KeyboardInterrupt), \
                patch("foreman.lifecycle.close", side_effect=failure("pane_busy", "cannot close")), \
                self.assertRaises(KeyboardInterrupt) as caught:
            measure_worker_kinds(client, [template()], "2026-10-01T00:00:00+00:00")
        notes = " ".join(caught.exception.__notes__)
        self.assertIn("Probe cleanup also failed", notes)
        self.assertIn("herdr pane close --pane probe-pane", notes)


if __name__ == "__main__":
    unittest.main()
