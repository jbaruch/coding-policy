"""The foreman seat: declared like a worker, its tier selected, the selection proven.

rules/agent-team-operation.md Foreman Seat: no model or effort is pinned for
the foreman. Its `coordination` round is resolved by the same machinery as
every seat -- its tier table (or its kind's worker table), the capability
table and measured headroom -- and `start-foreman` / `verify-foreman` prove
the SELECTED tier from argv. Every test drives `main()` or the launch helpers
with an in-memory client, so nothing spawns a process or contacts Herdr.
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import copy
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from foreman import capabilities
from foreman.cli import main
from foreman.config import load_foreman, parse_config, parse_foreman
from foreman.errors import ConfigError, HerdrError
from foreman.launch import start_foreman, verify_foreman

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"

AT = "2026-09-23T00:00:00+00:00"

ROWS = {
    "coordination": {"model": "sonnet-5", "effort": "medium"},
    "mechanical": {"model": "claude-haiku-4-5", "multiplier": 0.2},
    "review": {"model": "opus-5", "effort": "high", "multiplier": 3.0},
}

WORKER = {"name": "claude", "kind": "claude", "usage_prompt": "/usage", "usage_marker": "Current week",
          "usage_read_source": "visible", "clear_prompt": "/clear"}


def payload(**seat):
    block = {"agent": "foreman", "kind": "claude", "launch_args": ["--dangerously-skip-permissions"],
             "tiers": copy.deepcopy(ROWS), **seat}
    return {"schema_version": 1, "agents": [dict(WORKER)],
            "judge": {"agent": "judge", "model": "claude-fable-5-1", "effort": "max"},
            "foreman": {key: value for key, value in block.items() if value is not None}}


def argv(model, effort=None):
    return ["claude", "--dangerously-skip-permissions", "--model", model] + (["--effort", effort] if effort else [])


class Client:
    """Herdr's agent.start and pane.process_info, recorded in memory."""

    def __init__(self, reply_argv=None, live_argv=None):
        self.starts = []
        self.reply_argv = reply_argv
        self.live_argv = live_argv

    def agent_start(self, name, kind, pane, flags):
        self.starts.append((name, kind, pane, list(flags)))
        reply = self.reply_argv if self.reply_argv is not None else [kind] + list(flags)
        return {"agent": {"name": name, "agent": kind, "pane_id": pane, "agent_status": "idle"}, "argv": reply}

    def pane_process_info(self, pane):
        return {"pane_id": pane, "shell_pid": 100,
                "foreground_processes": [{"name": "claude", "pid": 400, "argv": copy.deepcopy(self.live_argv)}]}


class ConfigTest(unittest.TestCase):
    def test_the_shipped_example_pins_no_model_on_the_foreman(self):
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertNotIn("model", raw["foreman"])
        self.assertNotIn("effort", raw["foreman"])
        seat = parse_foreman(raw)
        assert seat is not None
        self.assertIn("coordination", seat.tiers)
        self.assertEqual(seat.tier_source, "agents.claude")

    def test_an_absent_block_parses_to_none(self):
        self.assertIsNone(parse_foreman({"schema_version": 1, "agents": [WORKER]}))

    def test_a_model_or_effort_field_is_refused(self):
        for field in ({"model": "sonnet-5"}, {"effort": "low"}):
            with self.subTest(field=field), self.assertRaises(ConfigError) as caught:
                parse_foreman(payload(**field))
            self.assertIn("tier selection", str(caught.exception))

    def test_any_model_and_effort_may_fill_a_row(self):
        seat = parse_foreman(payload(tiers={"coordination": {"model": "opus-5", "effort": "max"}}))
        assert seat is not None
        self.assertEqual(seat.tiers["coordination"]["effort"], "max")

    def test_the_judges_agent_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            parse_foreman(payload(agent="judge"))
        self.assertIn("pinned judge", str(caught.exception))

    def test_a_worker_name_is_refused_by_parse_config(self):
        with self.assertRaises(ConfigError) as caught:
            parse_config(payload(agent="claude"))
        self.assertIn("also a configured worker", str(caught.exception))

    def test_a_model_override_in_launch_args_is_refused(self):
        with self.assertRaises(ConfigError):
            parse_foreman(payload(launch_args=["--model", "opus-5"]))


class LaunchTest(unittest.TestCase):
    def setUp(self):
        seat = parse_foreman(payload())
        assert seat is not None
        self.seat = seat
        self.tier = {"model": "sonnet-5", "effort": "medium"}

    def test_start_launches_the_tier_it_is_handed(self):
        client = Client()
        start_foreman(client, self.seat, "w1:p0", self.tier)
        self.assertEqual(client.starts[0][3], argv("sonnet-5", "medium")[1:])

    def test_start_refuses_a_launch_argv_that_differs(self):
        with self.assertRaises(HerdrError):
            start_foreman(Client(reply_argv=argv("opus-5", "high")), self.seat, "w1:p0", self.tier)

    def test_verify_refuses_a_running_tier_that_differs(self):
        with self.assertRaises(HerdrError):
            verify_foreman(Client(live_argv=argv("opus-5", "high")), self.seat, "w1:p0", self.tier)


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.config = self.tmp / "config.json"
        self.state = self.tmp / "state.json"
        self.write(payload())

    def write(self, document):
        self.config.write_text(json.dumps(document), encoding="utf-8")

    def record(self, model, effort, verdict):
        path = capabilities.storage_path(self.state)
        document = json.loads(path.read_text()) if path.exists() else {
            "schema_version": 1, "refreshed_at": AT, "entries": []}
        document["entries"].append({
            "schema_version": 1, "model": model, "effort": effort, "capability": "mechanical-execution",
            "verdict": verdict, "source": {"kind": "project", "ref": "fixture", "dated": "2026-09-23"},
            "recorded_at": AT})
        path.write_text(json.dumps(document))

    def run_cli(self, argv_, client, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(_os.environ, env or {}, clear=False):
            code = main(["--config", str(self.config), "--state", str(self.state)] + argv_,
                        stdout=out, stderr=err, client=client)
        return code, out.getvalue(), err.getvalue()

    def test_without_evidence_the_configured_coordination_row_launches(self):
        client = Client()
        code, out, err = self.run_cli(["start-foreman", "--pane", "w1:p0"], client)
        self.assertEqual(code, 0, err)
        self.assertEqual(client.starts[0][3][-4:], ["--model", "sonnet-5", "--effort", "medium"])
        result = json.loads(out)
        self.assertEqual(result["tier"]["tier_row"], "coordination")
        # The seat carries the same selection record a planned seat does (#602).
        self.assertEqual((result["selection"]["agent"], result["selection"]["round"]), ("foreman", "coordination"))
        self.assertEqual(result["selection"]["required_capabilities"]["model"], ["mechanical-execution"])

    def test_selection_reads_the_measured_headroom_of_the_foremans_window(self):
        from foreman.state import SNAPSHOT_SCHEMA_VERSION, add_snapshot, empty_state, save_state
        state = empty_state()
        add_snapshot(state, {"schema_version": SNAPSHOT_SCHEMA_VERSION, "measured_at": AT, "failed_agents": [],
                             "agents": {"claude": {"kind": "claude", "headroom_pct": 12.0,
                                                   "window_group": "claude-max-weekly", "tier_billing": {}},
                                        "codex": {"kind": "codex", "headroom_pct": 3.0,
                                                  "window_group": "", "tier_billing": {}}}})
        save_state(self.state, state)
        self.write(payload(window_group="claude-max-weekly"))
        client = Client()
        code, out, err = self.run_cli(["start-foreman", "--pane", "w1:p0"], client)
        self.assertEqual(code, 0, err)
        # The shared window's reading, not an unrelated worker's.
        self.assertEqual(json.loads(out)["tier"]["pressure_headroom"], 12.0)

    def test_a_foreman_without_a_window_is_unmeasured(self):
        client = Client()
        code, out, err = self.run_cli(["start-foreman", "--pane", "w1:p0"], client)
        self.assertEqual(code, 0, err)
        self.assertIsNone(json.loads(out)["tier"]["pressure_headroom"])

    def test_the_cheapest_adequate_row_wins_and_is_what_launches(self):
        self.record("claude-haiku-4-5", "default", "adequate")
        client = Client()
        code, out, err = self.run_cli(["start-foreman", "--pane", "w1:p0"], client)
        self.assertEqual(code, 0, err)
        self.assertEqual(client.starts[0][3][-2:], ["--model", "claude-haiku-4-5"])
        self.assertEqual(json.loads(out)["tier"]["capability"], "adequate")

    def test_an_inadequate_coordination_row_refuses_before_launch(self):
        self.record("sonnet-5", "medium", "inadequate")
        client = Client()
        code, _, err = self.run_cli(["start-foreman", "--pane", "w1:p0"], client)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "capability_inadequate")
        self.assertEqual(client.starts, [])

    def test_verify_proves_the_selected_tier_from_this_pane(self):
        client = Client(live_argv=argv("sonnet-5", "medium"))
        code, out, err = self.run_cli(["verify-foreman"], client, {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p0"})
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["argv_verified"])

    def test_verify_fails_when_the_running_tier_is_not_the_selected_one(self):
        self.record("claude-haiku-4-5", "default", "adequate")
        client = Client(live_argv=argv("sonnet-5", "medium"))
        code, out, _ = self.run_cli(["verify-foreman", "--pane", "w1:p0"], client)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

    def test_verify_without_a_block_warns_and_passes(self):
        self.write({"schema_version": 1, "agents": [WORKER]})
        code, out, err = self.run_cli(["verify-foreman", "--pane", "w1:p0"], Client())
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertFalse(result["configured"])
        self.assertIn("start-foreman", result["warning"])

    def test_start_without_a_block_is_refused(self):
        self.write({"schema_version": 1, "agents": [WORKER]})
        client = Client()
        code, _, err = self.run_cli(["start-foreman", "--pane", "w1:p0"], client)
        self.assertEqual(code, 1)
        self.assertIn("no `foreman` block", err)
        self.assertEqual(client.starts, [])

    def test_plan_refuses_to_seat_the_foreman_on_a_worker(self):
        code, _, err = self.run_cli(["plan", "--roles", "foreman", "--task", "t"], Client())
        self.assertEqual(code, 1)
        self.assertIn("never planned onto a worker", err)

    def test_load_foreman_reads_a_missing_config_as_no_block(self):
        self.assertIsNone(load_foreman(self.tmp / "absent.json"))


if __name__ == "__main__":
    unittest.main()
