"""The foreman seat: operator config, verified at launch and at round start.

rules/agent-team-operation.md Foreman Seat: the foreman runs on an economical
model at low effort, named in config.json's `foreman` block. `start-foreman`
launches exactly that tier and proves it from the launch argv;
`verify-foreman` proves a live foreman pane's foreground argv carries it.
Every test drives `main()` or the launch helpers with an in-memory client, so
nothing spawns a process or contacts Herdr.
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

from foreman.cli import main
from foreman.config import FOREMAN_EFFORT, load_foreman, parse_config, parse_foreman
from foreman.errors import ConfigError, HerdrError
from foreman.launch import start_foreman, verify_foreman

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"

SEAT = {"agent": "foreman", "kind": "claude", "model": "sonnet-5", "effort": "low",
        "launch_args": ["--dangerously-skip-permissions"]}

WORKER = {"name": "claude", "kind": "claude", "usage_prompt": "/usage", "usage_marker": "Current week",
          "usage_read_source": "visible", "clear_prompt": "/clear"}


def payload(**seat):
    block = {**SEAT, **seat}
    return {"schema_version": 1, "agents": [dict(WORKER)],
            "judge": {"agent": "judge", "model": "claude-fable-5-1", "effort": "max"},
            "foreman": {key: value for key, value in block.items() if value is not None}}


class Client:
    """Herdr's agent.start and pane.process_info, recorded in memory."""

    def __init__(self, reply_argv=None, live_argv=None):
        self.starts = []
        self.reply_argv = reply_argv
        self.live_argv = live_argv

    def agent_start(self, name, kind, pane, flags):
        self.starts.append((name, kind, pane, list(flags)))
        argv = self.reply_argv if self.reply_argv is not None else [kind] + list(flags)
        return {"agent": {"name": name, "agent": kind, "pane_id": pane, "agent_status": "idle"}, "argv": argv}

    def pane_process_info(self, pane):
        return {"pane_id": pane, "shell_pid": 100,
                "foreground_processes": [{"name": "claude", "pid": 400, "argv": copy.deepcopy(self.live_argv)}]}


class ConfigTest(unittest.TestCase):
    def test_the_shipped_example_names_a_cheap_foreman_at_low_effort(self):
        seat = parse_foreman(json.loads(EXAMPLE.read_text(encoding="utf-8")))
        assert seat is not None
        self.assertEqual(seat.effort, FOREMAN_EFFORT)
        self.assertNotEqual(seat.model, "claude-fable-5-1")

    def test_an_absent_block_parses_to_none(self):
        self.assertIsNone(parse_foreman({"schema_version": 1, "agents": [WORKER]}))

    def test_a_valid_block_carries_its_tier_and_launch_options(self):
        seat = parse_foreman(payload())
        assert seat is not None
        self.assertEqual(seat.tier(), {"model": "sonnet-5", "effort": "low"})
        self.assertEqual(seat.launch_args, ("--dangerously-skip-permissions",))

    def test_a_model_without_an_effort_flag_omits_it(self):
        seat = parse_foreman(payload(model="claude-haiku-4-5", effort=None))
        assert seat is not None
        self.assertIsNone(seat.effort)

    def test_an_effort_above_low_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            parse_foreman(payload(effort="high"))
        self.assertIn("foreman.effort", str(caught.exception))

    def test_the_kinds_top_model_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            parse_foreman(payload(model="opus-5"))
        self.assertIn("top claude model", str(caught.exception))

    def test_the_judges_pinned_model_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            parse_foreman(payload(model="claude-fable-5-1"))
        self.assertIn("reserved for the judge", str(caught.exception))

    def test_a_worker_name_is_refused_by_parse_config(self):
        with self.assertRaises(ConfigError) as caught:
            parse_config(payload(agent="claude"))
        self.assertIn("also a configured worker", str(caught.exception))

    def test_a_model_override_in_launch_args_is_refused(self):
        with self.assertRaises(ConfigError):
            parse_foreman(payload(launch_args=["--model", "opus-5"]))

    def test_an_unknown_field_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            parse_foreman(payload(tiers={}))
        self.assertIn("unknown field", str(caught.exception))


class LaunchTest(unittest.TestCase):
    def setUp(self):
        seat = parse_foreman(payload())
        assert seat is not None
        self.seat = seat

    def test_start_launches_the_configured_model_and_effort(self):
        client = Client()
        proof = start_foreman(client, self.seat, "w1:p0")
        self.assertEqual(client.starts, [("foreman", "claude", "w1:p0",
                                          ["--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "low"])])
        self.assertEqual((proof["model"], proof["effort"]), ("sonnet-5", "low"))

    def test_start_refuses_a_launch_argv_that_differs_from_the_tier(self):
        client = Client(reply_argv=["claude", "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"])
        with self.assertRaises(HerdrError):
            start_foreman(client, self.seat, "w1:p0")

    def test_verify_proves_the_live_pane_from_its_foreground_argv(self):
        client = Client(live_argv=["claude", "--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "low"])
        proof = verify_foreman(client, self.seat, "w1:p0")
        self.assertEqual((proof["source"], proof["pid"]), ("process_argv", 400))

    def test_verify_refuses_a_foreman_left_on_a_strong_tier(self):
        client = Client(live_argv=["claude", "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"])
        with self.assertRaises(HerdrError):
            verify_foreman(client, self.seat, "w1:p0")


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps(payload()), encoding="utf-8")

    def run_cli(self, argv, client, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(_os.environ, env or {}, clear=False):
            code = main(["--config", str(self.config), "--state", str(self.tmp / "state.json")] + argv,
                        stdout=out, stderr=err, client=client)
        return code, out.getvalue(), err.getvalue()

    def test_start_foreman_launches_what_config_names(self):
        client = Client()
        code, out, err = self.run_cli(["start-foreman", "--pane", "w1:p0"], client)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertTrue(result["argv_verified"])
        self.assertEqual((result["model"], result["effort"]), ("sonnet-5", "low"))
        self.assertEqual(client.starts[0][3][-4:], ["--model", "sonnet-5", "--effort", "low"])

    def test_verify_foreman_reads_this_herdr_pane_by_default(self):
        client = Client(live_argv=["claude", "--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "low"])
        code, out, err = self.run_cli(["verify-foreman"], client, {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p0"})
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["pane"], "w1:p0")

    def test_verify_foreman_fails_on_a_mismatched_live_tier(self):
        client = Client(live_argv=["claude", "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"])
        code, out, _ = self.run_cli(["verify-foreman", "--pane", "w1:p0"], client)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

    def test_a_config_without_a_foreman_block_is_refused(self):
        self.config.write_text(json.dumps({"schema_version": 1, "agents": [WORKER]}), encoding="utf-8")
        client = Client()
        code, _, err = self.run_cli(["start-foreman", "--pane", "w1:p0"], client)
        self.assertEqual(code, 1)
        self.assertIn("no `foreman` block", err)
        self.assertEqual(client.starts, [])

    def test_load_foreman_refuses_a_missing_config(self):
        with self.assertRaises(ConfigError):
            load_foreman(self.tmp / "absent.json")


if __name__ == "__main__":
    unittest.main()
