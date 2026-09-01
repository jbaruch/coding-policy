"""Tests for teamlead.assign.

The load-bearing test in this file is that a busy agent is never typed into,
and that a refused run sends nothing at all.
"""

# Standalone-run shim: scripts/run-tests.sh executes each suite as
# `python3 <file>` from the repo root, so put the skill directory (this file's
# grandparent) on sys.path before the package imports below.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from teamlead.assign import (
    apply,
    assignment_text,
    build_steps,
    check_all_ready,
    dry_run,
    normalize_assignments,
    resolve_paths,
)
from teamlead.config import parse_config
from teamlead.errors import AgentBusyError, UsageError
from teamlead.herdr import HerdrClient

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
            "slash_delivery": "paste",
            "close_keys": ["esc"],
            "clear_prompt": "/clear",
        },
        {
            "name": "codex",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "paste",
            "clear_prompt": "/new",
        },
        {
            "name": "grok",
            "kind": "grok",
            "usage_prompt": "/usage",
            "usage_marker": "Weekly limit",
            "usage_read_source": "visible",
            "slash_delivery": "type",
            "close_keys": ["esc"],
            "clear_prompt": "/new",
            "dialog_next_tab_keys": ["tab"],
            "idle_markers": ["Shift+Tab:mode"],
            "working_markers": ["Esc:cancel"],
        },
    ],
}

BY_NAME = {agent.name: agent for agent in parse_config(CONFIG)}
ASSIGNMENTS = {"developer": "grok", "tester": "claude", "reviewer": "codex"}
PANES = {"claude": "w2:p1", "codex": "w3:p1", "grok": "w4:p1"}

# Verbatim grok footers. The working one is the idle one plus `Esc:cancel`,
# which is why the probe checks working markers first.
GROK_WORKING_FOOTER = (
    "  ╭────────────────────────╮\n"
    "  │ ❯ do the thing         │\n"
    "  ╰────────────────────────╯\n"
    "  Shift+Tab:mode  │  Esc:cancel  │  Ctrl+.:shortcuts\n"
)
GROK_IDLE_FOOTER = (
    "  ╭────────────────────────╮\n"
    "  │ ❯                      │\n"
    "  ╰────────────────────────╯\n"
    "  Shift+Tab:mode  │  Ctrl+.:shortcuts\n"
)


def runner_with(statuses, footers=None):
    """Script agent statuses, and the pane footer the idle probe would read."""
    footers = footers or {}
    runner = FakeRunner()
    for name, status in statuses.items():
        runner.set("agent get " + name, agent_json(name, status, PANES[name]))
        runner.set("agent prompt " + name, ok_json("agent_prompt"))
        runner.set("agent wait " + name, ok_json("agent_wait"))
        runner.set(
            "agent read {} --source visible --lines 40".format(name),
            footers.get(name, GROK_WORKING_FOOTER),
        )
    runner.set("pane send-text", ok_json("pane_send_text"))
    runner.set("pane send-keys", ok_json("pane_send_keys"))
    return runner


class PromptTextTest(unittest.TestCase):
    def test_exact_wording(self):
        self.assertEqual(
            assignment_text("developer", "/w/COMMON.md", "/w/dev.md"),
            "New assignment from the team lead. Your role for this task is "
            "DEVELOPER. Read /w/COMMON.md in full, then read /w/dev.md in full, "
            "and execute that brief exactly. Finish with the REPORT line it "
            "specifies.",
        )

    def test_role_is_upper_cased(self):
        self.assertIn("Your role for this task is TESTER.", assignment_text("tester", "c", "b"))


class NormalizeAssignmentsTest(unittest.TestCase):
    def test_accepts_plan_output(self):
        self.assertEqual(
            normalize_assignments({"schema_version": 1, "assignments": ASSIGNMENTS}), ASSIGNMENTS
        )

    def test_accepts_a_bare_role_to_agent_mapping(self):
        self.assertEqual(normalize_assignments(ASSIGNMENTS), ASSIGNMENTS)

    def test_preserves_role_order(self):
        self.assertEqual(list(normalize_assignments(ASSIGNMENTS)), list(ASSIGNMENTS))

    def test_rejects_a_non_object(self):
        with self.assertRaises(UsageError):
            normalize_assignments(["developer", "grok"])

    def test_rejects_an_empty_object(self):
        with self.assertRaises(UsageError):
            normalize_assignments({})

    def test_rejects_a_non_string_agent(self):
        with self.assertRaises(UsageError) as caught:
            normalize_assignments({"developer": ["grok"]})
        self.assertIn("developer", str(caught.exception))


class ResolvePathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teamlead-brief-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.briefs = {}
        for role in ASSIGNMENTS:
            path = Path(self.tmp) / (role + ".md")
            path.write_text("# " + role, encoding="utf-8")
            self.briefs[role] = str(path)
        self.common = str(Path(self.tmp) / "COMMON.md")
        Path(self.common).write_text("# common", encoding="utf-8")

    def test_paths_are_made_absolute(self):
        paths = resolve_paths(ASSIGNMENTS, self.briefs, self.common)
        self.assertTrue(os.path.isabs(paths["common"]))
        self.assertTrue(all(os.path.isabs(paths[role]) for role in ASSIGNMENTS))

    def test_missing_brief_for_a_role_names_the_role(self):
        del self.briefs["tester"]
        with self.assertRaises(UsageError) as caught:
            resolve_paths(ASSIGNMENTS, self.briefs, self.common)
        self.assertIn("tester", str(caught.exception))

    def test_nonexistent_brief_file_is_refused(self):
        self.briefs["tester"] = str(Path(self.tmp) / "nope.md")
        with self.assertRaises(UsageError) as caught:
            resolve_paths(ASSIGNMENTS, self.briefs, self.common)
        self.assertIn("nope.md", str(caught.exception))

    def test_nonexistent_common_file_is_refused(self):
        with self.assertRaises(UsageError):
            resolve_paths(ASSIGNMENTS, self.briefs, str(Path(self.tmp) / "gone.md"))


class BuildStepsTest(unittest.TestCase):
    def setUp(self):
        self.client = HerdrClient(binary="herdr", runner=FakeRunner())
        self.paths = {
            "common": "/w/COMMON.md",
            "developer": "/w/dev.md",
            "tester": "/w/test.md",
            "reviewer": "/w/review.md",
        }

    def test_one_step_per_role_in_assignment_order(self):
        steps = build_steps(self.client, ASSIGNMENTS, BY_NAME, self.paths)
        self.assertEqual([step["role"] for step in steps], ["developer", "tester", "reviewer"])
        self.assertEqual([step["agent"] for step in steps], ["grok", "claude", "codex"])

    def test_commands_are_check_clear_wait_assign(self):
        steps = build_steps(
            self.client,
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            panes={"grok": "w4:p1"},
            settle_timeout_ms=60000,
        )
        self.assertEqual(
            [command["shell"] for command in steps[0]["commands"]],
            [
                "herdr agent get grok",
                "herdr pane send-text w4:p1 /new",
                "herdr pane send-keys w4:p1 enter",
                "herdr agent wait grok --until idle --until done --timeout 60000",
                "herdr agent prompt grok 'New assignment from the team lead. Your role "
                "for this task is DEVELOPER. Read /w/COMMON.md in full, then read "
                "/w/dev.md in full, and execute that brief exactly. Finish with the "
                "REPORT line it specifies.'",
            ],
        )

    def test_each_agent_gets_its_native_clear_by_its_configured_path(self):
        steps = build_steps(
            self.client,
            ASSIGNMENTS,
            BY_NAME,
            self.paths,
            panes={"grok": "w4:p1", "claude": "w2:p1", "codex": "w3:p1"},
        )
        clears = [
            command["shell"]
            for step in steps
            for command in step["commands"]
            if command["shell"].endswith(("/new", "/clear"))
        ]
        self.assertEqual(
            clears,
            [
                "herdr pane send-text w4:p1 /new",  # grok types
                "herdr agent prompt claude /clear",  # claude pastes
                "herdr agent prompt codex /new",  # codex pastes
            ],
        )

    def test_grok_never_has_its_clear_pasted(self):
        steps = build_steps(
            self.client, {"developer": "grok"}, BY_NAME, self.paths, panes={"grok": "w4:p1"}
        )
        prompts = [
            command["shell"]
            for command in steps[0]["commands"]
            if " agent prompt " in command["shell"]
        ]
        self.assertEqual(len(prompts), 1)  # the assignment message alone
        self.assertIn("New assignment from the team lead.", prompts[0])
        self.assertNotIn("/new", prompts[0])

    def test_an_unresolved_pane_renders_as_a_placeholder(self):
        # --dry-run resolves no panes because it makes no herdr calls.
        steps = build_steps(self.client, {"developer": "grok"}, BY_NAME, self.paths)
        self.assertEqual(steps[0]["pane_id"], "PANE-ID-RESOLVED-AT-RUN-TIME")
        self.assertIn(
            "herdr pane send-text PANE-ID-RESOLVED-AT-RUN-TIME /new",
            [command["shell"] for command in steps[0]["commands"]],
        )

    def test_no_clear_drops_the_clear_and_the_settle_wait(self):
        steps = build_steps(
            self.client, {"developer": "grok"}, BY_NAME, self.paths, no_clear=True
        )
        shells = [command["shell"] for command in steps[0]["commands"]]
        self.assertEqual(len(shells), 2)
        self.assertEqual(shells[0], "herdr agent get grok")
        self.assertIn("DEVELOPER", shells[1])

    def test_unknown_agent_name_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            build_steps(self.client, {"developer": "gemini"}, BY_NAME, self.paths)
        self.assertIn("gemini", str(caught.exception))


class DryRunTest(unittest.TestCase):
    def setUp(self):
        self.runner = FakeRunner()
        self.client = HerdrClient(binary="herdr", runner=self.runner)
        self.paths = {
            "common": "/w/COMMON.md",
            "developer": "/w/dev.md",
            "tester": "/w/test.md",
            "reviewer": "/w/review.md",
        }

    def test_dry_run_makes_no_herdr_calls_at_all(self):
        result = dry_run(self.client, ASSIGNMENTS, BY_NAME, self.paths)
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["sent"])
        self.assertEqual(self.runner.calls, [])

    def test_dry_run_plans_even_when_every_agent_is_busy(self):
        # No status check happens, so a dry run against a busy fleet still
        # shows the plan instead of refusing it.
        result = dry_run(self.client, ASSIGNMENTS, BY_NAME, self.paths)
        self.assertEqual(len(result["steps"]), 3)
        self.assertEqual(self.runner.writes(), [])

    def test_dry_run_lists_the_conditional_probe_read(self):
        result = dry_run(self.client, {"developer": "grok"}, BY_NAME, self.paths)
        conditional = result["steps"][0]["conditional_commands"]
        self.assertEqual(
            [command["shell"] for command in conditional],
            ["herdr agent read grok --source visible --lines 40"],
        )
        self.assertIn("working", conditional[0]["when"])

    def test_dry_run_shows_the_prompt_text_that_would_be_sent(self):
        result = dry_run(self.client, {"developer": "grok"}, BY_NAME, self.paths)
        self.assertIn("DEVELOPER", result["steps"][0]["prompt"])


class RefusalTest(unittest.TestCase):
    def setUp(self):
        self.paths = {"common": "/w/COMMON.md", "developer": "/w/dev.md"}

    def test_working_agent_is_refused_and_nothing_is_sent(self):
        runner = runner_with({"grok": "working"})
        with self.assertRaises(AgentBusyError) as caught:
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertIn("grok (working)", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_blocked_agent_is_refused(self):
        runner = runner_with({"grok": "blocked"})
        with self.assertRaises(AgentBusyError):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.writes(), [])

    def test_one_busy_agent_blocks_the_whole_round(self):
        # claude is idle, but codex is working: nothing goes out, so the round
        # is never half-applied.
        runner = runner_with({"claude": "idle", "codex": "working"})
        paths = {"common": "/w/COMMON.md", "developer": "/w/dev.md", "tester": "/w/t.md"}
        with self.assertRaises(AgentBusyError):
            apply(
                HerdrClient(runner=runner),
                {"developer": "claude", "tester": "codex"},
                BY_NAME,
                paths,
                AT,
            )
        self.assertEqual(runner.writes(), [])

    def test_a_busy_agent_has_no_override(self):
        # rules/agent-team-operation.md Dispatch Safety is unconditional. The
        # refusal is the whole behavior: nothing is typed, and no argument
        # exists that would type it anyway.
        runner = runner_with({"grok": "working"})
        with self.assertRaises(AgentBusyError):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.writes(), [])
        # No override parameter exists to be passed. Asserted on the signature
        # rather than by calling with an invalid keyword, which a type checker
        # rightly rejects at the call site.
        self.assertNotIn("force", inspect.signature(apply).parameters)
        self.assertNotIn("force", inspect.signature(check_all_ready).parameters)

    def test_the_refusal_names_the_state_to_wait_for(self):
        runner = runner_with({"grok": "working"})
        with self.assertRaises(AgentBusyError) as caught:
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        message = str(caught.exception)
        self.assertIn("grok (working)", message)
        self.assertIn("idle or done", message)
        self.assertNotIn("--force", message)

    def test_stale_working_is_overturned_and_the_round_proceeds(self):
        # herdr's title-derived state says working; the pane footer says the
        # agent is sitting at an empty prompt. The pane wins.
        runner = runner_with({"grok": "working"}, footers={"grok": GROK_IDLE_FOOTER})
        result = apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            AT,
            warn=lambda message: None,
        )
        applied = result["applied"][0]
        self.assertEqual(applied["state_before"], "idle")
        self.assertEqual(applied["herdr_state_before"], "working")
        self.assertEqual(applied["state_source"], "probe")

    def test_the_overturn_warns_that_herdr_state_was_stale(self):
        runner = runner_with({"grok": "working"}, footers={"grok": GROK_IDLE_FOOTER})
        warnings = []
        apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            AT,
            warn=warnings.append,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("stale", warnings[0])

    def test_a_probe_confirming_working_still_refuses(self):
        runner = runner_with({"grok": "working"}, footers={"grok": GROK_WORKING_FOOTER})
        with self.assertRaises(AgentBusyError):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.writes(), [])

    def test_an_inconclusive_probe_still_refuses(self):
        runner = runner_with({"grok": "working"}, footers={"grok": "unknown screen\n"})
        with self.assertRaises(AgentBusyError):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.writes(), [])

    def test_a_blocked_agent_is_refused_without_a_probe_read(self):
        runner = runner_with({"grok": "blocked"}, footers={"grok": GROK_IDLE_FOOTER})
        with self.assertRaises(AgentBusyError):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.commands(), ["agent get grok"])

    def test_check_all_ready_reports_the_probe_verdict(self):
        runner = runner_with({"grok": "working"}, footers={"grok": GROK_IDLE_FOOTER})
        statuses = check_all_ready(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            warn=lambda message: None,
        )
        self.assertEqual(
            statuses["grok"],
            {
                "state": "idle",
                "herdr_state": "working",
                "state_source": "probe",
                "pane_id": "w4:p1",
            },
        )

    def test_check_all_ready_returns_statuses_when_everyone_is_free(self):
        runner = runner_with({"grok": "idle", "claude": "done"})
        statuses = check_all_ready(
            HerdrClient(runner=runner), {"developer": "grok", "tester": "claude"}, BY_NAME
        )
        self.assertEqual(
            statuses,
            {
                "grok": {
                    "state": "idle",
                    "herdr_state": "idle",
                    "state_source": "herdr",
                    "pane_id": "w4:p1",
                },
                "claude": {
                    "state": "done",
                    "herdr_state": "done",
                    "state_source": "herdr",
                    "pane_id": "w2:p1",
                },
            },
        )


class SlashCommandDeliveryTest(unittest.TestCase):
    """Regression: `agent prompt` pastes, and a pasted /clear is a message."""

    def setUp(self):
        self.paths = {
            "common": "/w/COMMON.md",
            "developer": "/w/dev.md",
            "tester": "/w/test.md",
            "reviewer": "/w/review.md",
        }

    def test_groks_clear_never_goes_through_agent_prompt(self):
        runner = runner_with({"grok": "idle", "claude": "idle", "codex": "idle"})
        apply(HerdrClient(runner=runner), ASSIGNMENTS, BY_NAME, self.paths, AT)
        self.assertNotIn(
            "agent prompt grok /new", runner.commands()
        )
        self.assertIn("pane send-text w4:p1 /new", runner.commands())

    def test_only_grok_types_its_clear(self):
        runner = runner_with({"grok": "idle", "claude": "idle", "codex": "idle"})
        apply(HerdrClient(runner=runner), ASSIGNMENTS, BY_NAME, self.paths, AT)
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("pane send-text")],
            ["pane send-text w4:p1 /new"],
        )

    def test_claude_and_codex_have_their_clears_pasted(self):
        runner = runner_with({"grok": "idle", "claude": "idle", "codex": "idle"})
        apply(HerdrClient(runner=runner), ASSIGNMENTS, BY_NAME, self.paths, AT)
        commands = runner.commands()
        self.assertIn("agent prompt claude /clear", commands)
        self.assertIn("agent prompt codex /new", commands)

    def test_every_role_still_gets_its_assignment_message_pasted(self):
        runner = runner_with({"grok": "idle", "claude": "idle", "codex": "idle"})
        apply(HerdrClient(runner=runner), ASSIGNMENTS, BY_NAME, self.paths, AT)
        messages = [
            text
            for text in runner.pasted_prompts()
            if "New assignment from the team lead." in text
        ]
        self.assertEqual(len(messages), 3)

    def test_every_typed_command_is_followed_by_enter(self):
        runner = runner_with({"grok": "idle"})
        apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        commands = runner.commands()
        index = commands.index("pane send-text w4:p1 /new")
        self.assertEqual(commands[index + 1], "pane send-keys w4:p1 enter")

    def test_a_silent_send_text_still_gets_its_enter(self):
        # herdr's `pane send-text` exits 0 with empty stdout on success.
        runner = runner_with({"grok": "idle"})
        runner.set("pane send-text", stdout="")
        runner.set("pane send-keys", stdout="")
        result = apply(
            HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT
        )
        commands = runner.commands()
        self.assertEqual(
            commands[commands.index("pane send-text w4:p1 /new") + 1],
            "pane send-keys w4:p1 enter",
        )
        self.assertEqual(len(result["applied"]), 1)

    def test_no_clear_types_nothing_at_all(self):
        runner = runner_with({"grok": "idle"})
        apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            AT,
            no_clear=True,
        )
        self.assertEqual([c for c in runner.commands() if c.startswith("pane send-")], [])

    def test_a_typing_agent_with_no_pane_is_refused_rather_than_typed_at_blindly(self):
        runner = FakeRunner()
        runner.set(
            "agent get grok",
            '{"id":"cli:agent:get","result":{"agent":{"name":"grok","agent_status":"idle"}}}',
        )
        with self.assertRaises(UsageError) as caught:
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertIn("--no-clear", str(caught.exception))
        self.assertEqual(runner.writes(), [])


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.paths = {
            "common": "/w/COMMON.md",
            "developer": "/w/dev.md",
            "tester": "/w/test.md",
        }

    def test_clear_settle_then_assign_in_order(self):
        runner = runner_with({"grok": "idle"})
        apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(
            runner.commands(),
            [
                "agent get grok",
                "pane send-text w4:p1 /new",
                "pane send-keys w4:p1 enter",
                "agent wait grok --until idle --until done --timeout 60000",
                "agent prompt grok 'New assignment from the team lead. Your role for "
                "this task is DEVELOPER. Read /w/COMMON.md in full, then read /w/dev.md "
                "in full, and execute that brief exactly. Finish with the REPORT line it "
                "specifies.'",
            ],
        )

    def test_status_of_every_target_is_checked_before_the_first_write(self):
        runner = runner_with({"grok": "idle", "claude": "idle"})
        apply(
            HerdrClient(runner=runner),
            {"developer": "grok", "tester": "claude"},
            BY_NAME,
            self.paths,
            AT,
        )
        commands = runner.commands()
        first_write = min(
            index
            for index, c in enumerate(commands)
            if c.startswith(runner.WRITE_PREFIXES)
        )
        self.assertEqual(commands[:first_write], ["agent get grok", "agent get claude"])

    def test_no_clear_sends_only_the_assignment(self):
        runner = runner_with({"grok": "idle"})
        apply(
            HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT, no_clear=True
        )
        self.assertEqual(len(runner.writes()), 1)
        self.assertIn("DEVELOPER", runner.writes()[0])

    def test_result_records_what_was_applied(self):
        runner = runner_with({"grok": "idle"})
        result = apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["applied_at"], AT)
        self.assertEqual(
            result["applied"],
            [
                {
                    "role": "developer",
                    "agent": "grok",
                    "state_before": "idle",
                    "herdr_state_before": "idle",
                    "state_source": "herdr",
                    "pane_id": "w4:p1",
                    "cleared": True,
                    "brief": "/w/dev.md",
                    "common": "/w/COMMON.md",
                    "at": AT,
                }
            ],
        )

    def test_the_ledger_callback_fires_once_per_hand_off(self):
        runner = runner_with({"grok": "idle", "claude": "idle"})
        seen = []
        apply(
            HerdrClient(runner=runner),
            {"developer": "grok", "tester": "claude"},
            BY_NAME,
            self.paths,
            AT,
            on_assigned=lambda role, agent, at: seen.append((role, agent, at)),
        )
        self.assertEqual(seen, [("developer", "grok", AT), ("tester", "claude", AT)])


if __name__ == "__main__":
    unittest.main()
