"""Tests for teamlead.composer.

The live failure this module exists to prevent: `agent prompt codex /new`
pasted `/new` into Codex's composer, the slash-autocomplete popup swallowed
the Enter, `agent wait --until idle` returned instantly because the agent had
never left idle, and the assignment was then pasted onto the unsent text.
Codex received `/newNew assignment from the team lead...` and rejected it.
"""

# Standalone-run shim: scripts/run-tests.sh executes each suite as
# `python3 <file>` from the repo root, so put the skill directory (this file's
# grandparent) on sys.path before the package imports below.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from teamlead.composer import (
    checkable,
    composer_text,
    ensure_ready,
    read_pane,
    screen_signature,
    send_command,
)
from teamlead.config import parse_config
from teamlead.errors import HerdrError
from teamlead.herdr import HerdrClient

from tests.fakes import FakeRunner, ScriptedReads, composer_screen, ok_json


def NO_SLEEP(seconds):
    """Stand-in for time.sleep; nothing here waits on a real clock."""


CONFIG = {
    "schema_version": 1,
    "agents": [
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
            "name": "blind",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "type",
            "clear_prompt": "/new",
        },
        {
            "name": "stubborn",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "type",
            "composer_glyph": "› ",
            "clear_prompt": "/new",
        },
    ],
}
BY_NAME = {agent.name: agent for agent in parse_config(CONFIG)}

# The live rows, as Codex draws them.
CODEX_EMPTY = "  Codex v1.2  ~/Projects/x\n  ─────────────\n  › \n"
CODEX_HELD = "  Codex v1.2  ~/Projects/x\n  ─────────────\n  › /new\n"
CODEX_FRESH = "  ╭─ Codex ─╮\n  │ new session │\n  ╰─────────╯\n  › \n"


class ComposerTextTest(unittest.TestCase):
    def test_an_empty_composer_reads_as_empty_string(self):
        self.assertEqual(composer_text(CODEX_EMPTY, "› "), "")

    def test_a_held_command_is_returned(self):
        self.assertEqual(composer_text(CODEX_HELD, "› "), "/new")

    def test_the_appended_text_from_the_live_failure_is_visible(self):
        text = "  › /newNew assignment from the team lead. Your role is TESTER.\n"
        self.assertEqual(
            composer_text(text, "› "),
            "/newNew assignment from the team lead. Your role is TESTER.",
        )

    def test_the_last_composer_row_wins(self):
        self.assertEqual(composer_text(CODEX_HELD + CODEX_EMPTY, "› "), "")

    def test_a_missing_glyph_reads_as_unknowable(self):
        self.assertIsNone(composer_text("no glyph anywhere\n", "› "))

    def test_an_unconfigured_glyph_reads_as_unknowable(self):
        self.assertIsNone(composer_text(CODEX_HELD, ""))

    def test_grok_box_borders_are_stripped_from_the_remainder(self):
        self.assertEqual(composer_text("  │ ❯                    │\n", "│ ❯"), "")
        self.assertEqual(composer_text("  │ ❯ /usage             │\n", "│ ❯"), "/usage")

    def test_claude_composer(self):
        self.assertEqual(composer_text("────\n❯ /clear\n────\n", "❯ "), "/clear")
        self.assertEqual(composer_text("────\n❯\n────\n", "❯ "), "")

    def test_checkable_follows_the_glyph(self):
        self.assertTrue(checkable(BY_NAME["codex"]))
        self.assertFalse(checkable(BY_NAME["blind"]))


class ScreenSignatureTest(unittest.TestCase):
    def test_the_composer_row_is_excluded(self):
        self.assertEqual(
            screen_signature(CODEX_EMPTY, "› "), screen_signature(CODEX_HELD, "› ")
        )

    def test_a_fresh_session_banner_is_a_different_signature(self):
        self.assertNotEqual(
            screen_signature(CODEX_EMPTY, "› "), screen_signature(CODEX_FRESH, "› ")
        )

    def test_blank_rows_do_not_count_as_change(self):
        self.assertEqual(
            screen_signature("a\n\n\nb\n", "› "), screen_signature("a\nb\n", "› ")
        )


class ReadPaneTest(unittest.TestCase):
    def test_an_agent_with_no_glyph_is_never_read(self):
        runner = FakeRunner()
        self.assertEqual(read_pane(HerdrClient(runner=runner), BY_NAME["blind"]), "")
        self.assertEqual(runner.calls, [])

    def test_an_agent_with_a_glyph_reads_its_viewport(self):
        runner = FakeRunner()
        runner.set("agent read codex", CODEX_EMPTY)
        read_pane(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertEqual(
            runner.commands(), ["agent read codex --source visible --lines 20"]
        )


class EnsureReadyTest(unittest.TestCase):
    def _runner(self, screens):
        runner = FakeRunner()
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.responses["agent read codex --source visible --lines 20"] = ScriptedReads(
            screens
        )
        return runner

    def test_an_empty_composer_needs_no_recovery(self):
        runner = self._runner([CODEX_EMPTY])
        ensure_ready(HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP)
        self.assertEqual(runner.writes(), [])

    def test_a_held_composer_is_recovered_once(self):
        runner = self._runner([CODEX_HELD, CODEX_EMPTY])
        ensure_ready(
            HerdrClient(runner=runner),
            BY_NAME["codex"],
            sleep=NO_SLEEP,
            warn=lambda message: None,
        )
        self.assertEqual(runner.writes(), ["agent send-keys codex ctrl+c"])

    def test_recovery_is_sent_exactly_once_even_when_it_fails(self):
        # A second ctrl+c would exit Codex, so this must never loop.
        runner = self._runner([CODEX_HELD])
        with self.assertRaises(HerdrError):
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["codex"],
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )
        self.assertEqual(runner.writes(), ["agent send-keys codex ctrl+c"])

    def test_the_failure_names_the_stuck_text_and_says_once_only(self):
        runner = self._runner([CODEX_HELD])
        with self.assertRaises(HerdrError) as caught:
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["codex"],
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )
        message = str(caught.exception)
        self.assertIn("/new", message)
        self.assertIn("never twice", message)

    def test_an_agent_with_no_recover_keys_refuses_without_sending(self):
        runner = FakeRunner()
        runner.responses["agent read stubborn --source visible --lines 20"] = ScriptedReads(
            [CODEX_HELD]
        )
        with self.assertRaises(HerdrError) as caught:
            ensure_ready(HerdrClient(runner=runner), BY_NAME["stubborn"], sleep=NO_SLEEP)
        self.assertIn("recover_keys", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_a_caller_supplied_read_is_reused(self):
        runner = self._runner([CODEX_EMPTY])
        ensure_ready(
            HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP, text=CODEX_EMPTY
        )
        self.assertEqual(runner.calls, [])


class SendCommandTest(unittest.TestCase):
    def _runner(self, screens):
        runner = FakeRunner()
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.responses["agent read codex --source visible --lines 20"] = ScriptedReads(
            screens
        )
        return runner

    def _send(self, runner, **kwargs):
        kwargs.setdefault("sleep", NO_SLEEP)
        kwargs.setdefault("warn", lambda message: None)
        return send_command(
            HerdrClient(runner=runner), BY_NAME["codex"], "w3:p1", "/new", **kwargs
        )

    def test_a_consumed_command_needs_no_second_enter(self):
        result = self._send(self._runner([CODEX_EMPTY, CODEX_FRESH]))
        self.assertTrue(result["consumed"])
        self.assertFalse(result["extra_enter"])
        self.assertFalse(result["recovered"])
        self.assertTrue(result["screen_changed"])

    def test_the_autocomplete_popup_costs_a_second_enter(self):
        # First Enter accepts the completion; the second submits.
        runner = self._runner([CODEX_EMPTY, CODEX_HELD, CODEX_FRESH])
        result = self._send(runner)
        self.assertTrue(result["extra_enter"])
        self.assertTrue(result["consumed"])
        self.assertEqual(
            [c for c in runner.commands() if c == "pane send-keys w3:p1 enter"],
            ["pane send-keys w3:p1 enter", "pane send-keys w3:p1 enter"],
        )

    def test_a_command_still_stuck_after_two_enters_is_refused(self):
        runner = self._runner([CODEX_EMPTY, CODEX_HELD])
        with self.assertRaises(HerdrError) as caught:
            self._send(runner)
        message = str(caught.exception)
        self.assertIn("/new", message)
        self.assertIn("Nothing further was sent", message)
        self.assertIn("slash_delivery", message)

    def test_the_refusal_pressed_enter_only_twice(self):
        runner = self._runner([CODEX_EMPTY, CODEX_HELD])
        with self.assertRaises(HerdrError):
            self._send(runner)
        self.assertEqual(
            len([c for c in runner.commands() if c == "pane send-keys w3:p1 enter"]), 2
        )

    def test_a_composer_holding_text_beforehand_is_recovered_first(self):
        runner = self._runner([CODEX_HELD, CODEX_EMPTY, CODEX_FRESH])
        result = self._send(runner)
        self.assertTrue(result["recovered"])
        self.assertTrue(result["consumed"])
        self.assertEqual(
            runner.commands()[1], "agent send-keys codex ctrl+c"
        )

    def test_an_unchanged_screen_is_reported_not_assumed(self):
        runner = self._runner([CODEX_EMPTY, CODEX_EMPTY])
        result = self._send(runner, screen_attempts=2)
        self.assertTrue(result["consumed"])
        self.assertFalse(result["screen_changed"])

    def test_the_screen_change_wait_is_bounded(self):
        runner = self._runner([CODEX_EMPTY, CODEX_EMPTY])
        self._send(runner, screen_attempts=2)
        reads = [c for c in runner.commands() if c.startswith("agent read codex")]
        self.assertEqual(len(reads), 4)  # before, after, then two re-reads

    def test_zero_attempts_skips_the_screen_change_wait(self):
        runner = self._runner([CODEX_EMPTY, CODEX_EMPTY])
        self._send(runner, screen_attempts=0)
        reads = [c for c in runner.commands() if c.startswith("agent read codex")]
        self.assertEqual(len(reads), 2)

    def test_an_agent_with_no_glyph_is_sent_the_command_and_not_checked(self):
        runner = FakeRunner()
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        result = send_command(
            HerdrClient(runner=runner),
            BY_NAME["blind"],
            "w3:p1",
            "/new",
            sleep=NO_SLEEP,
            warn=lambda message: None,
        )
        self.assertTrue(result["consumed"])
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("agent read")], []
        )


if __name__ == "__main__":
    unittest.main()
