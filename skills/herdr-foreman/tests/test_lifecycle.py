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
from foreman.errors import HerdrError
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


class IdentityTest(unittest.TestCase):
    def test_identity_is_safe_bounded_and_fresh(self):
        first = identity("reviewer#API")
        second = identity("reviewer#API")
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
        worker = template()
        tier = worker.tiers["coordination"]
        with patch("foreman.lifecycle.verify_running", return_value={"pid": 1}) as verify:
            self.assertEqual(spawn(client, worker, tier, cwd="/work"), "pane-new")
        client.pane_split.assert_called_once_with(current=True, cwd="/work", focus=False)
        client.agent_start.assert_called_once()
        verify.assert_called_once_with(client, worker, "pane-new", tier)

    def test_close_proves_the_assignment_disappeared(self):
        client = Mock()
        client.agent_get.side_effect = [{"pane_id": "pane-new"}, missing("reviewer-a1")]
        result = close(client, "reviewer-a1", "pane-new")
        client.pane_close.assert_called_once_with("pane-new")
        self.assertTrue(result["closed"])
        self.assertFalse(result["replayed"])


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
