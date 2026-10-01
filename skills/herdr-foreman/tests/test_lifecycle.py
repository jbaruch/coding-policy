"""Assignment-scoped worker lifecycle (issue #674)."""

import json
import os as _os
import sys as _sys
import unittest
from unittest.mock import Mock, patch

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from foreman.config import Agent
from foreman.errors import HerdrError, UsageError
from foreman.lifecycle import close, identity, materialize, measure_worker_kinds, spawn


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
        client.pane_process_info.assert_called_once_with("pane-new")
        start.assert_called_once_with(client, worker, "pane-new", tier)
        verify.assert_called_once_with(client, worker, "pane-new", tier)

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


class WindowProbeTest(unittest.TestCase):
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
                patch("foreman.measure.measure", side_effect=measured) as usage, \
                patch("foreman.lifecycle.close") as stopped:
            result = measure_worker_kinds(client, workers, "2026-10-01T00:00:00+00:00")
        self.assertEqual(started.call_count, 1)
        self.assertEqual(usage.call_count, 1)
        self.assertEqual(stopped.call_count, 1)
        self.assertEqual(set(result["agents"]), {"claude", "codex"})
        self.assertTrue(all(row["pane_id"] is None for row in result["agents"].values()))


if __name__ == "__main__":
    unittest.main()
