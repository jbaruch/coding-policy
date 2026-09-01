"""Tests for teamlead.herdr. No test in this file starts a process."""

# Standalone-run shim: scripts/run-tests.sh executes each suite as
# `python3 <file>` from the repo root, so put the skill directory (this file's
# grandparent) on sys.path before the package imports below.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess
import unittest

from teamlead.errors import HerdrError
from teamlead.herdr import HerdrClient, format_argv

from tests.fakes import FakeRunner, agent_json, ok_json


class ArgvBuilderTest(unittest.TestCase):
    def setUp(self):
        self.client = HerdrClient(binary="herdr", runner=FakeRunner())

    def test_agent_get(self):
        self.assertEqual(self.client.argv_agent_get("grok"), ["herdr", "agent", "get", "grok"])

    def test_agent_read_with_source_and_lines(self):
        self.assertEqual(
            self.client.argv_agent_read("codex", source="recent-unwrapped", lines=80),
            ["herdr", "agent", "read", "codex", "--source", "recent-unwrapped", "--lines", "80"],
        )

    def test_agent_read_defaults_omit_optional_flags(self):
        self.assertEqual(self.client.argv_agent_read("codex"), ["herdr", "agent", "read", "codex"])

    def test_agent_prompt_plain(self):
        self.assertEqual(
            self.client.argv_agent_prompt("grok", "/usage"),
            ["herdr", "agent", "prompt", "grok", "/usage"],
        )

    def test_agent_prompt_with_wait_until_and_timeout(self):
        self.assertEqual(
            self.client.argv_agent_prompt(
                "grok", "/new", wait=True, until=("idle", "done"), timeout_ms=60000
            ),
            [
                "herdr", "agent", "prompt", "grok", "/new", "--wait",
                "--until", "idle", "--until", "done", "--timeout", "60000",
            ],
        )

    def test_agent_send_keys_takes_several_keys(self):
        self.assertEqual(
            self.client.argv_agent_send_keys("claude", ["esc", "esc"]),
            ["herdr", "agent", "send-keys", "claude", "esc", "esc"],
        )

    def test_agent_wait(self):
        self.assertEqual(
            self.client.argv_agent_wait("claude", until=("idle",), timeout_ms=1000),
            ["herdr", "agent", "wait", "claude", "--until", "idle", "--timeout", "1000"],
        )

    def test_pane_wait_output_with_regex(self):
        self.assertEqual(
            self.client.argv_pane_wait_output(
                "w2:p1", regex="Current week", source="visible", lines=60, timeout_ms=20000
            ),
            [
                "herdr", "pane", "wait-output", "--regex", "Current week",
                "--source", "visible", "--lines", "60", "--timeout", "20000", "w2:p1",
            ],
        )

    def test_pane_wait_output_with_literal_match(self):
        self.assertEqual(
            self.client.argv_pane_wait_output("w3:p1", match="Weekly limit"),
            ["herdr", "pane", "wait-output", "--match", "Weekly limit", "w3:p1"],
        )

    def test_pane_wait_output_needs_exactly_one_matcher(self):
        with self.assertRaises(HerdrError):
            self.client.argv_pane_wait_output("w3:p1")
        with self.assertRaises(HerdrError):
            self.client.argv_pane_wait_output("w3:p1", regex="a", match="b")

    def test_binary_override_is_honoured(self):
        client = HerdrClient(binary="/opt/herdr", runner=FakeRunner())
        self.assertEqual(client.argv_agent_get("x")[0], "/opt/herdr")


class ExecutionTest(unittest.TestCase):
    def test_agent_get_returns_the_agent_record(self):
        runner = FakeRunner({"agent get grok": None})
        runner.set("agent get grok", agent_json("grok", "idle", "w4:p1"))
        client = HerdrClient(runner=runner)
        agent = client.agent_get("grok")
        self.assertEqual(agent["agent_status"], "idle")
        self.assertEqual(agent["pane_id"], "w4:p1")

    def test_agent_list_returns_the_array(self):
        runner = FakeRunner()
        runner.set(
            "agent list",
            '{"id":"cli:agent:list","result":{"type":"agent_list","agents":[{"name":"grok"}]}}',
        )
        self.assertEqual(HerdrClient(runner=runner).agent_list(), [{"name": "grok"}])

    def test_agent_read_returns_raw_text_not_json(self):
        runner = FakeRunner()
        runner.set("agent read grok", "Weekly limit: 0%\nCredits: $16.42\n")
        text = HerdrClient(runner=runner).agent_read("grok", source="recent-unwrapped")
        self.assertIn("Weekly limit: 0%", text)

    def test_prompt_and_send_keys_return_the_result_body(self):
        runner = FakeRunner()
        runner.set("agent prompt grok", ok_json("agent_prompt"))
        runner.set("agent send-keys claude", ok_json("agent_send_keys"))
        client = HerdrClient(runner=runner)
        self.assertEqual(client.agent_prompt("grok", "/usage")["type"], "agent_prompt")
        self.assertEqual(client.agent_send_keys("claude", ["esc"])["type"], "agent_send_keys")

    def test_pane_wait_output_executes_the_built_argv(self):
        runner = FakeRunner()
        runner.set("pane wait-output", ok_json("pane_wait_output"))
        HerdrClient(runner=runner).pane_wait_output("w2:p1", regex="Current week", timeout_ms=5000)
        self.assertEqual(
            runner.commands(),
            ["pane wait-output --regex 'Current week' --timeout 5000 w2:p1"],
        )


class FailureTest(unittest.TestCase):
    def test_server_error_json_is_surfaced_with_its_code(self):
        runner = FakeRunner()
        runner.set(
            "agent get ghost",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"agent_not_found","message":"agent target ghost not found"}}',
        )
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("ghost")
        message = str(caught.exception)
        self.assertIn("agent_not_found", message)
        self.assertIn("ghost not found", message)

    def test_exit_two_is_reported_as_a_syntax_error(self):
        runner = FakeRunner()
        runner.set("agent get x", stdout="", returncode=2, stderr="unexpected argument")
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("x")
        self.assertIn("syntax error", str(caught.exception))

    def test_non_json_stderr_is_passed_through(self):
        runner = FakeRunner()
        runner.set("agent get x", stdout="", returncode=1, stderr="boom")
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("x")
        self.assertIn("boom", str(caught.exception))

    def test_non_json_stdout_on_a_control_command_is_an_error(self):
        runner = FakeRunner()
        runner.set("agent get x", stdout="not json at all")
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("x")
        self.assertIn("non-JSON", str(caught.exception))

    def test_json_without_a_result_field_is_an_error(self):
        runner = FakeRunner()
        runner.set("agent get x", stdout='{"id":"cli:agent:get"}')
        with self.assertRaises(HerdrError):
            HerdrClient(runner=runner).agent_get("x")

    def test_result_without_an_agent_record_is_an_error(self):
        runner = FakeRunner()
        runner.set("agent get x", stdout='{"id":"cli:agent:get","result":{"type":"agent_info"}}')
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("x")
        self.assertIn("herdr agent list", str(caught.exception))

    def test_missing_binary_names_the_env_override(self):
        runner = FakeRunner(raises={"agent get x": FileNotFoundError()})
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("x")
        self.assertIn("TEAMLEAD_HERDR_BIN", str(caught.exception))

    def test_subprocess_timeout_is_reported_with_the_command(self):
        runner = FakeRunner(
            raises={"agent wait x": subprocess.TimeoutExpired(cmd="herdr", timeout=1)}
        )
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_wait("x")
        self.assertIn("agent wait x", str(caught.exception))


class FormatArgvTest(unittest.TestCase):
    def test_quotes_arguments_containing_spaces(self):
        self.assertEqual(
            format_argv(["herdr", "agent", "prompt", "grok", "hello world"]),
            "herdr agent prompt grok 'hello world'",
        )


if __name__ == "__main__":
    unittest.main()
