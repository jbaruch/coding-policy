"""Tests for teamlead.cli.

Every test drives `main()` with an injected herdr client and captured streams,
so nothing here spawns a process or reads the real clock (`--now` is always
passed where a timestamp would otherwise be taken).
"""

# Standalone-run shim: scripts/run-tests.sh executes each suite as
# `python3 <file>` from the repo root, so put the skill directory (this file's
# grandparent) on sys.path before the package imports below.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from teamlead.cli import build_parser, main
from teamlead.herdr import HerdrClient
from teamlead.state import STATE_SCHEMA_VERSION

from tests.fakes import FakeRunner, agent_json, composer_reads, ok_json

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
            "composer_glyph": "❯ ",
            "recover_keys": ["esc"],
            "close_keys": ["esc"],
            "clear_prompt": "/clear",
        },
        {
            "name": "codex",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "type",
            "composer_glyph": "› ",
            "recover_keys": ["ctrl+c"],
            "clear_prompt": "/new",
        },
        {
            "name": "grok",
            "kind": "grok",
            "usage_prompt": "/usage",
            "usage_marker": "Weekly limit",
            "usage_read_source": "visible",
            "slash_delivery": "type",
            "composer_glyph": "│ ❯",
            "recover_keys": ["esc"],
            "close_keys": ["esc"],
            "clear_prompt": "/new",
            "dialog_next_tab_keys": ["tab"],
            "idle_markers": ["Shift+Tab:mode"],
            "working_markers": ["Esc:cancel"],
        },
    ],
}

SNAPSHOT = {
    "schema_version": 1,
    "measured_at": AT,
    "agents": {
        "claude": {"kind": "claude", "state": "idle", "headroom_pct": 92.0},
        "codex": {"kind": "codex", "state": "idle", "headroom_pct": 87.0},
        "grok": {"kind": "grok", "state": "done", "headroom_pct": 100.0},
    },
}

CLAUDE_PANE = (
    "   Current session\n   ████    8% used\n   Resets 12:49am (Europe/Oslo)\n"
    "   Current week (all models)\n   █    2% used\n   Resets Sep 5 at 11:59pm (Europe/Oslo)\n"
)
GROK_PANE = "     Weekly limit: 0%\n     Next reset: September 6, 12:55\n     Credits: $16.42\n"

# Verbatim grok footers, as the idle probe reads them.
GROK_WORKING_FOOTER = "  │ ❯ go            │\n  Shift+Tab:mode  │  Esc:cancel  │  Ctrl+.:shortcuts\n"
GROK_IDLE_FOOTER = "  │ ❯               │\n  Shift+Tab:mode  │  Ctrl+.:shortcuts\n"


class CliCase(unittest.TestCase):
    """Shared temp workspace: config, state, briefs, snapshot."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="teamlead-cli-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps(CONFIG), encoding="utf-8")
        self.state = self.tmp / "state.json"
        self.snapshot = self.tmp / "snapshot.json"
        self.snapshot.write_text(json.dumps(SNAPSHOT), encoding="utf-8")
        self.common = self.tmp / "COMMON.md"
        self.common.write_text("# common\n", encoding="utf-8")
        self.briefs = {}
        for role in ("developer", "tester", "reviewer"):
            path = self.tmp / (role + ".md")
            path.write_text("# " + role + "\n", encoding="utf-8")
            self.briefs[role] = path
        # A stand-in herdr that always fails, for the paths that build a real
        # client instead of taking an injected one.
        self.fake_herdr = self.tmp / "herdr-stub"
        self.fake_herdr.write_text(
            '#!/bin/sh\necho \'{"error":{"code":"agent_not_found","message":"no"}}\' >&2\nexit 1\n',
            encoding="utf-8",
        )
        self.fake_herdr.chmod(0o755)
        self.out = io.StringIO()
        self.err = io.StringIO()

    def run_cli(self, argv, client=None):
        code = main(argv, stdout=self.out, stderr=self.err, client=client)
        return code, self.out.getvalue(), self.err.getvalue()

    def base(self):
        return ["--config", str(self.config), "--state", str(self.state)]

    def brief_args(self, *roles):
        args = []
        for role in roles:
            args += ["--brief", "{}={}".format(role, self.briefs[role])]
        return args


class ParserTest(unittest.TestCase):
    def test_every_subcommand_is_registered(self):
        parser = build_parser()
        for command in ("measure", "plan", "apply", "state"):
            args = parser.parse_args([command, "--assignments", "{}", "--common", "x"] if command == "apply" else [command])
            self.assertEqual(args.command, command)

    def test_a_missing_subcommand_exits_two(self):
        # argparse prints its usage to the real stderr; capture it so a
        # deliberate failure does not litter the test run.
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                build_parser().parse_args([])
        self.assertEqual(caught.exception.code, 2)

    def test_apply_requires_assignments_and_common(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["apply"])


class TraceFlagTest(CliCase):
    """--trace has to reach the real client, which tests never inject."""

    def test_the_flag_is_accepted_before_and_after_the_subcommand(self):
        parser = build_parser()
        self.assertTrue(parser.parse_args(["--trace", "state"]).trace)
        self.assertTrue(parser.parse_args(["state", "--trace"]).trace)

    def test_it_is_absent_by_default(self):
        self.assertFalse(getattr(build_parser().parse_args(["state"]), "trace", False))

    def test_every_herdr_command_is_printed_to_stderr(self):
        # No client injected: the CLI builds one and wires the trace sink to
        # the same stderr stream the warnings use.
        code, out, err = self.run_cli(
            self.base()
            + [
                "--trace",
                "measure",
                "--marker-poll-interval",
                "0",
                "--agent",
                "grok",
                "--now",
                AT,
                "--herdr-bin",
                str(self.fake_herdr),
            ]
        )
        self.assertEqual(code, 1)  # the stub herdr refuses every command
        self.assertIn("herdr> {} agent get grok".format(self.fake_herdr), err)
        self.assertIn("exit=1", err)
        self.assertIn("agent_not_found", err)
        # stdout stays the machine-readable snapshot; tracing never pollutes it
        self.assertEqual(json.loads(out)["failed_agents"], ["grok"])

    def test_without_the_flag_nothing_is_traced(self):
        code, _, err = self.run_cli(
            self.base()
            + [
                "measure",
                "--marker-poll-interval",
                "0",
                "--agent",
                "grok",
                "--now",
                AT,
                "--herdr-bin",
                str(self.fake_herdr),
            ]
        )
        self.assertEqual(code, 1)
        self.assertNotIn("herdr>", err)


class StateCommandTest(CliCase):
    def test_prints_a_fresh_document_when_no_state_exists(self):
        code, out, err = self.run_cli(self.base() + ["state"])
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out), {"schema_version": 1, "snapshots": [], "assignments": []}
        )
        self.assertEqual(err, "")

    def test_prints_what_was_written(self):
        self.state.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "snapshots": [SNAPSHOT],
                    "assignments": [{"at": AT, "role": "developer", "agent": "grok"}],
                }
            ),
            encoding="utf-8",
        )
        code, out, _ = self.run_cli(self.base() + ["state"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["assignments"][0]["agent"], "grok")


class PlanCommandTest(CliCase):
    def test_plans_from_a_snapshot_file(self):
        code, out, err = self.run_cli(
            self.base() + ["plan", "--roles", "developer,tester,reviewer", "--snapshot", str(self.snapshot)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out)["assignments"],
            {"developer": "grok", "tester": "claude", "reviewer": "codex"},
        )
        self.assertEqual(err, "")

    def test_roles_default_to_developer_tester_reviewer(self):
        code, out, _ = self.run_cli(self.base() + ["plan", "--snapshot", str(self.snapshot)])
        self.assertEqual(code, 0)
        self.assertEqual(list(json.loads(out)["assignments"]), ["developer", "tester", "reviewer"])

    def test_snapshot_ref_names_the_file(self):
        _, out, _ = self.run_cli(self.base() + ["plan", "--snapshot", str(self.snapshot)])
        self.assertEqual(json.loads(out)["snapshot_ref"]["source"], str(self.snapshot))

    def test_falls_back_to_the_newest_snapshot_in_state(self):
        self.state.write_text(
            json.dumps({"schema_version": 1, "snapshots": [SNAPSHOT], "assignments": []}),
            encoding="utf-8",
        )
        code, out, _ = self.run_cli(self.base() + ["plan"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["assignments"]["developer"], "grok")

    def test_previous_assignments_break_a_headroom_tie(self):
        tied = json.loads(json.dumps(SNAPSHOT))
        for record in tied["agents"].values():
            record["headroom_pct"] = 50.0
        tied_path = self.tmp / "tied.json"
        tied_path.write_text(json.dumps(tied), encoding="utf-8")
        self.state.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "snapshots": [],
                    "assignments": [
                        {"at": AT, "role": "developer", "agent": "claude"},
                        {"at": AT, "role": "developer", "agent": "codex"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        _, out, _ = self.run_cli(self.base() + ["plan", "--snapshot", str(tied_path)])
        self.assertEqual(json.loads(out)["assignments"]["developer"], "grok")

    def test_no_snapshot_anywhere_is_an_actionable_error(self):
        code, out, err = self.run_cli(self.base() + ["plan"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("teamlead measure", json.loads(err)["message"])

    def test_missing_snapshot_file_is_an_actionable_error(self):
        code, _, err = self.run_cli(self.base() + ["plan", "--snapshot", str(self.tmp / "no.json")])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "plan_error")

    def test_plan_never_touches_herdr(self):
        runner = FakeRunner()
        code, _, _ = self.run_cli(
            self.base() + ["plan", "--snapshot", str(self.snapshot)],
            client=HerdrClient(runner=runner),
        )
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [])


class MeasureCommandTest(CliCase):
    def _client(self, statuses, footers=None):
        footers = footers or {}
        runner = FakeRunner()
        panes = {"claude": "w2:p1", "codex": "w3:p1", "grok": "w4:p1"}
        texts = {"claude": CLAUDE_PANE, "grok": GROK_PANE}
        for name, status in statuses.items():
            runner.set("agent get " + name, agent_json(name, status, panes[name]))
            runner.set("agent prompt " + name, ok_json("agent_prompt"))
            runner.set("agent send-keys " + name, ok_json("agent_send_keys"))
            if name in texts:
                runner.set("agent read " + name, texts[name])
            if name in footers:
                runner.set(
                    "agent read {} --source visible --lines 40".format(name), footers[name]
                )
        runner.set("pane wait-output", ok_json("output_matched"))
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        for name in statuses:
            runner.responses[
                "agent read {} --source visible --lines 20".format(name)
            ] = composer_reads(name)
        self.runner = runner
        return HerdrClient(runner=runner)

    def test_measures_the_named_agents_only(self):
        client = self._client({"claude": "idle", "grok": "done"})
        code, out, err = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "claude", "--agent", "grok", "--now", AT],
            client=client,
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(sorted(payload["agents"]), ["claude", "grok"])
        self.assertEqual(payload["measured_at"], AT)
        self.assertEqual(err, "")

    def test_snapshot_is_appended_to_the_state_file(self):
        client = self._client({"grok": "idle"})
        self.run_cli(self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "grok", "--now", AT], client=client)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(len(state["snapshots"]), 1)
        self.assertEqual(state["snapshots"][0]["agents"]["grok"]["headroom_pct"], 100.0)

    def test_a_busy_agent_is_skipped_and_never_written_to(self):
        client = self._client({"claude": "idle", "codex": "working", "grok": "done"})
        code, out, _ = self.run_cli(self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--now", AT], client=client)
        self.assertEqual(code, 0)
        codex = json.loads(out)["agents"]["codex"]
        self.assertEqual(codex["state"], "working")
        self.assertIsNone(codex["headroom_pct"])
        self.assertEqual([c for c in self.runner.writes() if "codex" in c], [])

    def test_a_parse_failure_exits_one_but_still_prints_the_snapshot(self):
        client = self._client({"claude": "idle"})
        self.runner.set("agent read claude", "nothing useful here\n")
        code, out, err = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "claude", "--now", AT], client=client
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["failed_agents"], ["claude"])
        self.assertEqual(json.loads(err)["error"], "measure_incomplete")

    def test_records_which_signal_decided_the_state(self):
        client = self._client({"grok": "idle"})
        _, out, _ = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "grok", "--now", AT], client=client
        )
        record = json.loads(out)["agents"]["grok"]
        self.assertEqual(record["state_source"], "herdr")
        self.assertEqual(record["herdr_state"], "idle")

    def test_a_stale_herdr_state_is_overturned_and_warned_about_on_stderr(self):
        client = self._client({"grok": "working"}, footers={"grok": GROK_IDLE_FOOTER})
        code, out, err = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "grok", "--now", AT], client=client
        )
        self.assertEqual(code, 0)
        record = json.loads(out)["agents"]["grok"]
        self.assertEqual(record["state"], "idle")
        self.assertEqual(record["herdr_state"], "working")
        self.assertEqual(record["state_source"], "probe")
        self.assertEqual(record["headroom_pct"], 100.0)
        self.assertIn("stale", err)

    def test_a_genuinely_working_agent_is_still_skipped(self):
        client = self._client({"grok": "working"}, footers={"grok": GROK_WORKING_FOOTER})
        code, out, _ = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "grok", "--now", AT], client=client
        )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["agents"]["grok"]["skipped"])
        self.assertEqual(self.runner.writes(), [])

    def test_each_agent_asks_for_usage_by_its_configured_path_end_to_end(self):
        client = self._client({"claude": "idle", "grok": "done"})
        code, _, _ = self.run_cli(
            self.base()
            + [
                "measure",
                "--marker-poll-interval",
                "0",
                "--agent",
                "claude",
                "--agent",
                "grok",
                "--now",
                AT,
            ],
            client=client,
        )
        self.assertEqual(code, 0)
        commands = self.runner.commands()
        self.assertIn("agent prompt claude /usage", commands)  # claude pastes
        self.assertIn("pane send-text w4:p1 /usage", commands)  # grok types
        self.assertNotIn("agent prompt grok /usage", commands)

    def test_unknown_agent_name_is_an_actionable_error(self):
        code, out, err = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "gemini", "--now", AT],
            client=HerdrClient(runner=FakeRunner()),
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("gemini", json.loads(err)["message"])

    def test_missing_config_names_the_example_file(self):
        code, _, err = self.run_cli(
            ["--config", str(self.tmp / "gone.json"), "--state", str(self.state), "measure"],
            client=HerdrClient(runner=FakeRunner()),
        )
        self.assertEqual(code, 1)
        self.assertIn("config.example.json", json.loads(err)["message"])


class ApplyCommandTest(CliCase):
    def _client(self, statuses, footers=None):
        footers = footers or {}
        runner = FakeRunner()
        panes = {"claude": "w2:p1", "codex": "w3:p1", "grok": "w4:p1"}
        for name, status in statuses.items():
            runner.set("agent get " + name, agent_json(name, status, panes[name]))
            runner.set("agent prompt " + name, ok_json("agent_prompt"))
            runner.set("agent wait " + name, ok_json("agent_wait"))
            runner.set(
                "agent read {} --source visible --lines 40".format(name),
                footers.get(name, GROK_WORKING_FOOTER),
            )
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        for name in statuses:
            runner.responses[
                "agent read {} --source visible --lines 20".format(name)
            ] = composer_reads(name)
        self.runner = runner
        return HerdrClient(binary="herdr", runner=runner)

    def test_dry_run_prints_commands_and_sends_nothing(self):
        client = self._client({})
        code, out, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--dry-run",
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(self.runner.calls, [])
        shells = [command["shell"] for command in payload["steps"][0]["commands"]]
        self.assertEqual(shells[0], "herdr agent get grok")
        self.assertIn("herdr pane send-text PANE-ID-RESOLVED-AT-RUN-TIME /new", shells)
        self.assertIn("herdr pane send-keys PANE-ID-RESOLVED-AT-RUN-TIME enter", shells)
        self.assertIn("DEVELOPER", shells[-1])
        self.assertEqual(err, "")

    def test_dry_run_writes_nothing_to_the_state_file(self):
        client = self._client({})
        self.run_cli(
            self.base()
            + ["apply", "--composer-settle", "0", "--assignments", json.dumps({"developer": "grok"}), "--common", str(self.common), "--dry-run"]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertFalse(self.state.exists())

    def test_assignments_can_be_a_file_of_plan_output(self):
        plan_file = self.tmp / "plan.json"
        plan_file.write_text(
            json.dumps({"schema_version": 1, "assignments": {"developer": "grok"}}), encoding="utf-8"
        )
        client = self._client({})
        code, out, _ = self.run_cli(
            self.base()
            + ["apply", "--composer-settle", "0", "--assignments", str(plan_file), "--common", str(self.common), "--dry-run"]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["steps"][0]["agent"], "grok")

    def test_live_apply_clears_then_assigns_and_records_the_ledger(self):
        client = self._client({"grok": "idle", "claude": "done"})
        code, out, _ = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok", "tester": "claude"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer", "tester"),
            client=client,
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["applied"]), 2)
        ledger = json.loads(self.state.read_text(encoding="utf-8"))["assignments"]
        self.assertEqual(
            ledger,
            [
                {
                    "schema_version": STATE_SCHEMA_VERSION,
                    "at": AT,
                    "role": "developer",
                    "agent": "grok",
                },
                {
                    "schema_version": STATE_SCHEMA_VERSION,
                    "at": AT,
                    "role": "tester",
                    "agent": "claude",
                },
            ],
        )

    def test_each_agent_clears_by_its_configured_path_end_to_end(self):
        client = self._client({"grok": "idle", "claude": "done"})
        code, _, _ = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok", "tester": "claude"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer", "tester"),
            client=client,
        )
        self.assertEqual(code, 0)
        commands = self.runner.commands()
        self.assertIn("pane send-text w4:p1 /new", commands)  # grok types
        self.assertIn("agent prompt claude /clear", commands)  # claude pastes
        self.assertNotIn("agent prompt grok /new", commands)

    def test_busy_agent_is_refused_with_a_json_error_and_no_writes(self):
        client = self._client({"grok": "working"})
        code, out, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        payload = json.loads(err)
        self.assertEqual(payload["error"], "agent_busy")
        self.assertEqual(payload["details"]["busy"], {"grok": "working"})
        self.assertEqual(self.runner.writes(), [])

    def test_a_refused_round_leaves_the_ledger_untouched(self):
        client = self._client({"grok": "blocked"})
        self.run_cli(
            self.base()
            + ["apply", "--composer-settle", "0", "--assignments", json.dumps({"developer": "grok"}), "--common", str(self.common), "--now", AT]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertFalse(self.state.exists())

    def test_apply_proceeds_when_the_probe_overturns_a_stale_herdr_state(self):
        client = self._client({"grok": "working"}, footers={"grok": GROK_IDLE_FOOTER})
        code, out, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(code, 0)
        applied = json.loads(out)["applied"][0]
        self.assertEqual(applied["state_source"], "probe")
        self.assertEqual(applied["herdr_state_before"], "working")
        self.assertIn("stale", err)

    def _rejects(self, argv):
        # argparse prints its usage to stderr on a bad flag; swallow it so the
        # suite's own output stays readable.
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(argv)

    def test_apply_has_no_force_flag(self):
        # The override is gone from the surface, not merely discouraged.
        self._rejects(["apply", "--assignments", "{}", "--common", "x", "--force"])

    def test_measure_has_no_force_flag(self):
        self._rejects(["measure", "--force"])

    def test_a_busy_agent_refuses_the_whole_round(self):
        client = self._client({"grok": "working"})
        code, out, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("Refusing to interrupt", err)

    def test_no_clear_skips_the_clear_prompt(self):
        client = self._client({"grok": "idle"})
        self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--now",
                AT,
                "--no-clear",
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(len(self.runner.writes()), 1)

    def test_missing_brief_flag_is_an_actionable_error(self):
        client = self._client({})
        code, _, err = self.run_cli(
            self.base()
            + ["apply", "--composer-settle", "0", "--assignments", json.dumps({"developer": "grok"}), "--common", str(self.common), "--dry-run"],
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertIn("--brief developer=", json.loads(err)["message"])

    def test_malformed_brief_pair_is_rejected(self):
        client = self._client({})
        code, _, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--brief",
                "developer",
                "--dry-run",
            ],
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertIn("ROLE=PATH", json.loads(err)["message"])

    def test_nonexistent_brief_file_is_rejected_before_anything_is_sent(self):
        client = self._client({"grok": "idle"})
        code, _, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--brief",
                "developer={}".format(self.tmp / "missing.md"),
                "--now",
                AT,
            ],
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertIn("missing.md", json.loads(err)["message"])
        self.assertEqual(self.runner.calls, [])

    def test_malformed_assignments_json_is_rejected(self):
        client = self._client({})
        code, _, err = self.run_cli(
            self.base()
            + ["apply", "--composer-settle", "0", "--assignments", "{not json", "--common", str(self.common), "--dry-run"],
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "usage_error")


if __name__ == "__main__":
    unittest.main()
