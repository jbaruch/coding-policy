"""Tests for teamlead.measure. The herdr client is always a fake."""

# Standalone-run shim: scripts/run-tests.sh executes each suite as
# `python3 <file>` from the repo root, so put the skill directory (this file's
# grandparent) on sys.path before the package imports below.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from teamlead.config import parse_config
from teamlead.errors import HerdrError, ParseError
from teamlead.herdr import HerdrClient
from teamlead.measure import DEFAULT_READ_LINES, marker_pattern, measure, measure_agent, ready_agents

from tests.fakes import FakeRunner, agent_json, ok_json

AT = "2026-02-03T10:00:00+00:00"

CONFIG = {
    "schema_version": 1,
    "agents": [
        {
            "name": "claude",
            "kind": "claude",
            "usage_prompt": "/usage",
            "usage_marker": "Current week",
            "usage_read_source": "visible",
            "close_keys": ["esc"],
            "clear_prompt": "/clear",
        },
        {
            "name": "codex",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "clear_prompt": "/new",
        },
        {
            "name": "grok",
            "kind": "grok",
            "usage_prompt": "/usage",
            "usage_marker": "Weekly limit",
            "usage_read_source": "visible",
            "close_keys": ["esc"],
            "clear_prompt": "/new",
            "idle_markers": ["Shift+Tab:mode"],
            "working_markers": ["Esc:cancel"],
        },
    ],
}

CLAUDE_PANE = (
    "   Current session\n   ████    8% used\n   Resets 12:49am (Europe/Oslo)\n"
    "   Current week (all models)\n   █    2% used\n   Resets Sep 5 at 11:59pm (Europe/Oslo)\n"
)
CODEX_PANE = (
    "│  Account: jbaruch@sadogursky.com (Pro)  │\n"
    "│  Weekly limit: [███] 87% left (resets 17:26 on 7 Sep)  │\n"
)
GROK_PANE = "     Weekly limit: 0%\n     Next reset: September 6, 12:55\n     Credits: $16.42\n"
GROK_DIALOG_PANE = (
    "│  Weekly limit (X Premium+)      │\n"
    "│  ░░░░░░░░░░░░░░░░░░░░░░  1%     │\n"
    "│  Resets: September 6, 12:55     │\n"
    "│  Credits: $16.42                │\n"
)
# Footers the idle probe reads. Working is idle plus `Esc:cancel`.
GROK_IDLE_FOOTER = "  │ ❯          │\n  Shift+Tab:mode  │  Ctrl+.:shortcuts\n"
GROK_WORKING_FOOTER = "  │ ❯ go       │\n  Shift+Tab:mode  │  Esc:cancel  │  Ctrl+.:shortcuts\n"

AGENTS = parse_config(CONFIG)
BY_NAME = {agent.name: agent for agent in AGENTS}


def runner_with(statuses, panes, footers=None):
    """A FakeRunner scripted for the given agent statuses and pane texts.

    `footers` scripts the idle probe's read, which is a longer and therefore
    more specific argv prefix than the usage read.
    """
    footers = footers or {}
    runner = FakeRunner()
    panes_by_name = {"claude": "w2:p1", "codex": "w3:p1", "grok": "w4:p1"}
    for name, status in statuses.items():
        runner.set("agent get " + name, agent_json(name, status, panes_by_name[name]))
        runner.set("agent prompt " + name, ok_json("agent_prompt"))
        runner.set("agent send-keys " + name, ok_json("agent_send_keys"))
        if name in panes:
            runner.set("agent read " + name, panes[name])
        if name in footers:
            runner.set(
                "agent read {} --source visible --lines 40".format(name), footers[name]
            )
    runner.set("pane wait-output", ok_json("output_matched"))
    return runner


class MarkerPatternTest(unittest.TestCase):
    def test_literal_marker_becomes_an_escaped_pattern(self):
        # Herdr matches with a Rust regex; escaping keeps a literal marker
        # literal even when it grows punctuation.
        self.assertEqual(marker_pattern("Current week (all models)"), r"Current\ week\ \(all\ models\)")


class MeasureAgentTest(unittest.TestCase):
    def test_idle_claude_is_measured_and_the_dialog_is_closed(self):
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["claude"])
        self.assertEqual(record["state"], "idle")
        self.assertEqual(record["headroom_pct"], 92.0)
        self.assertFalse(record["skipped"])
        self.assertIn("agent send-keys claude esc", runner.commands())

    def test_the_full_command_sequence_is_what_herdr_documents(self):
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["claude"], marker_timeout_ms=20000)
        self.assertEqual(
            runner.commands(),
            [
                "agent get claude",
                "agent prompt claude /usage",
                "pane wait-output --regex 'Current\\ week' --source visible --timeout 20000 w2:p1",
                "agent read claude --source visible",
                "agent send-keys claude esc",
            ],
        )

    def test_visible_reads_omit_a_line_count(self):
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["claude"])
        self.assertIn("agent read claude --source visible", runner.commands())

    def test_grok_dialog_format_is_measured_the_same_as_the_inline_one(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_DIALOG_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(record["headroom_pct"], 99.0)
        self.assertEqual(record["plan"], "X Premium+")
        self.assertEqual(record["credits"], 16.42)
        self.assertIn("agent send-keys grok esc", runner.commands())

    def test_scrollback_reads_ask_for_lines(self):
        runner = runner_with({"codex": "idle"}, {"codex": CODEX_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertIn(
            "agent read codex --source recent-unwrapped --lines {}".format(DEFAULT_READ_LINES),
            runner.commands(),
        )

    def test_agent_without_close_keys_gets_no_keystrokes(self):
        # codex prints its report inline and opens no dialog, so it configures
        # no close_keys and must receive no keystrokes.
        runner = runner_with({"codex": "idle"}, {"codex": CODEX_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("agent send-keys")], []
        )

    def test_grok_credits_are_carried_through(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(record["credits"], 16.42)
        self.assertEqual(record["headroom_pct"], 100.0)

    def test_done_counts_as_ready(self):
        runner = runner_with({"grok": "done"}, {"grok": GROK_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(record["state"], "done")
        self.assertEqual(record["headroom_pct"], 100.0)


class BusyAgentTest(unittest.TestCase):
    def test_working_agent_is_skipped_without_a_single_write(self):
        runner = runner_with({"codex": "working"}, {})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertEqual(record["state"], "working")
        self.assertIsNone(record["windows"])
        self.assertIsNone(record["headroom_pct"])
        self.assertTrue(record["skipped"])
        self.assertEqual(runner.writes(), [])
        self.assertEqual(runner.commands(), ["agent get codex"])

    def test_blocked_agent_is_skipped_too(self):
        runner = runner_with({"codex": "blocked"}, {})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertTrue(record["skipped"])
        self.assertEqual(runner.writes(), [])

    def test_force_measures_a_working_agent(self):
        runner = runner_with({"codex": "working"}, {"codex": CODEX_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["codex"], force=True)
        self.assertFalse(record["skipped"])
        self.assertEqual(record["headroom_pct"], 87.0)
        self.assertIn("agent prompt codex /status", runner.commands())

    def test_unknown_status_is_measured_rather_than_skipped(self):
        # `unknown` does not prove the agent is busy, and herdr's own prompt
        # path rejects a truly blocked agent before sending anything.
        runner = runner_with({"grok": "unknown"}, {"grok": GROK_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertFalse(record["skipped"])


class ProbeIntegrationTest(unittest.TestCase):
    """herdr's title-derived state goes stale; the pane is the tie-breaker."""

    def test_stale_working_is_overturned_and_the_agent_is_measured(self):
        runner = runner_with(
            {"grok": "working"}, {"grok": GROK_PANE}, footers={"grok": GROK_IDLE_FOOTER}
        )
        record = measure_agent(
            HerdrClient(runner=runner), BY_NAME["grok"], warn=lambda message: None
        )
        self.assertFalse(record["skipped"])
        self.assertEqual(record["state"], "idle")
        self.assertEqual(record["herdr_state"], "working")
        self.assertEqual(record["state_source"], "probe")
        self.assertEqual(record["headroom_pct"], 100.0)

    def test_the_overturn_is_warned_about_on_stderr(self):
        runner = runner_with(
            {"grok": "working"}, {"grok": GROK_PANE}, footers={"grok": GROK_IDLE_FOOTER}
        )
        warnings = []
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"], warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("stale", warnings[0])

    def test_a_genuinely_working_agent_is_still_skipped(self):
        runner = runner_with(
            {"grok": "working"}, {}, footers={"grok": GROK_WORKING_FOOTER}
        )
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertTrue(record["skipped"])
        self.assertEqual(record["state"], "working")
        self.assertEqual(record["state_source"], "probe")
        self.assertEqual(runner.writes(), [])

    def test_an_inconclusive_probe_keeps_the_refusal(self):
        runner = runner_with(
            {"grok": "working"}, {}, footers={"grok": "some other program\n"}
        )
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertTrue(record["skipped"])
        self.assertEqual(record["state_source"], "herdr")
        self.assertEqual(runner.writes(), [])

    def test_a_blocked_agent_is_never_probed(self):
        runner = runner_with({"grok": "blocked"}, {})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertTrue(record["skipped"])
        self.assertEqual(record["state_source"], "herdr")
        self.assertEqual(runner.commands(), ["agent get grok"])

    def test_an_idle_agent_is_not_probed_at_all(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(record["state_source"], "herdr")
        self.assertNotIn("agent read grok --source visible --lines 40", runner.commands())


class DialogAlwaysClosesTest(unittest.TestCase):
    """A usage dialog left open flips the agent to `working` and eats the
    next prompt, so the close keys must go out on every failure path."""

    def test_close_keys_are_sent_when_parsing_fails(self):
        runner = runner_with({"grok": "idle"}, {"grok": "nothing useful here\n"})
        with self.assertRaises(ParseError):
            measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(runner.commands()[-1], "agent send-keys grok esc")

    def test_close_keys_are_sent_when_the_marker_wait_times_out(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_DIALOG_PANE})
        runner.set(
            "pane wait-output",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"timeout","message":"no match within 20000ms"}}',
        )
        with self.assertRaises(HerdrError):
            measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(runner.commands()[-1], "agent send-keys grok esc")

    def test_close_keys_are_sent_when_the_read_itself_fails(self):
        runner = runner_with({"grok": "idle"}, {})
        runner.set(
            "agent read grok",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"pane_gone","message":"pane not found"}}',
        )
        with self.assertRaises(HerdrError):
            measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(runner.commands()[-1], "agent send-keys grok esc")

    def test_the_failure_still_surfaces_on_the_snapshot(self):
        runner = runner_with({"grok": "idle"}, {"grok": "nothing useful here\n"})
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["grok"]], AT)
        self.assertEqual(snapshot["failed_agents"], ["grok"])
        self.assertEqual(snapshot["agents"]["grok"]["error"]["code"], "parse_error")
        self.assertIn("agent send-keys grok esc", runner.commands())


class FailureTest(unittest.TestCase):
    def test_unparseable_pane_still_closes_the_dialog(self):
        runner = runner_with({"claude": "idle"}, {"claude": "nothing useful\n"})
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["claude"]], AT)
        self.assertEqual(snapshot["failed_agents"], ["claude"])
        self.assertEqual(snapshot["agents"]["claude"]["error"]["code"], "parse_error")
        self.assertIn("agent send-keys claude esc", runner.commands())

    def test_a_herdr_failure_is_recorded_on_that_agent_only(self):
        runner = runner_with({"claude": "idle", "grok": "idle"}, {"grok": GROK_PANE})
        runner.set("agent prompt claude", stdout="", returncode=1, stderr='{"error":{"code":"agent_blocked","message":"blocked"}}')
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["claude"], BY_NAME["grok"]], AT)
        self.assertEqual(snapshot["failed_agents"], ["claude"])
        self.assertEqual(snapshot["agents"]["claude"]["error"]["code"], "herdr_error")
        self.assertEqual(snapshot["agents"]["grok"]["headroom_pct"], 100.0)

    def test_missing_pane_id_is_an_error_not_a_crash(self):
        runner = FakeRunner()
        runner.set(
            "agent get grok",
            '{"id":"cli:agent:get","result":{"agent":{"name":"grok","agent_status":"idle"}}}',
        )
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["grok"]], AT)
        self.assertEqual(snapshot["failed_agents"], ["grok"])
        self.assertIn("no pane", snapshot["agents"]["grok"]["error"]["message"])
        self.assertEqual(runner.writes(), [])


class SnapshotTest(unittest.TestCase):
    def test_snapshot_shape(self):
        runner = runner_with(
            {"claude": "idle", "codex": "working", "grok": "done"},
            {"claude": CLAUDE_PANE, "grok": GROK_PANE},
        )
        snapshot = measure(HerdrClient(runner=runner), AGENTS, AT)
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["measured_at"], AT)
        self.assertEqual(sorted(snapshot["agents"]), ["claude", "codex", "grok"])
        self.assertEqual(snapshot["failed_agents"], [])

    def test_busy_agent_appears_with_nulls_rather_than_being_dropped(self):
        runner = runner_with(
            {"claude": "idle", "codex": "working", "grok": "done"},
            {"claude": CLAUDE_PANE, "grok": GROK_PANE},
        )
        snapshot = measure(HerdrClient(runner=runner), AGENTS, AT)
        codex = snapshot["agents"]["codex"]
        self.assertEqual(codex["state"], "working")
        self.assertIsNone(codex["windows"])
        self.assertIsNone(codex["headroom_pct"])

    def test_measured_at_is_taken_from_the_caller_not_the_clock(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_PANE})
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["grok"]], "1999-01-01T00:00:00+00:00")
        self.assertEqual(snapshot["measured_at"], "1999-01-01T00:00:00+00:00")

    def test_windows_survive_into_the_snapshot(self):
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["claude"]], AT)
        windows = snapshot["agents"]["claude"]["windows"]
        self.assertEqual(windows["Current session"]["used_pct"], 8.0)
        self.assertEqual(windows["Current week (all models)"]["remaining_pct"], 98.0)

    def test_ready_agents_lists_only_the_settled_ones(self):
        runner = runner_with(
            {"claude": "idle", "codex": "working", "grok": "done"},
            {"claude": CLAUDE_PANE, "grok": GROK_PANE},
        )
        snapshot = measure(HerdrClient(runner=runner), AGENTS, AT)
        self.assertEqual(ready_agents(snapshot), ["claude", "grok"])


if __name__ == "__main__":
    unittest.main()
