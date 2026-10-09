"""Tests for foreman.composer.

The live failure this module exists to prevent: `agent prompt codex /new`
pasted `/new` into Codex's composer, the slash-autocomplete popup swallowed
the Enter, `agent wait --until idle` returned instantly because the agent had
never left idle, and the assignment was then pasted onto the unsent text.
Codex received `/newNew assignment from the team lead...` and rejected it.
"""

import os as _os
import sys as _sys

# Run as a script (`python3 tests/test_x.py`), Python puts tests/ on sys.path
# rather than the repo root, so neither `foreman` nor `tests.fakes` would
# resolve. Under `-m unittest` from the root this is already true and the
# insert is a no-op. The consuming repo's runner executes files as scripts.
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import unittest

from foreman.composer import (
    DispatchSession,
    FRESH_COMPOSER_ATTEMPTS,
    checkable,
    command_still_present,
    composer_text,
    ensure_ready,
    read_pane,
    screen_signature,
    inspect_composer,
    is_placeholder,
    recovery_allowed,
    send_command,
    send_message,
    strip_ansi,
    transcript_holds,
    unknown_skill_error,
)
from foreman.config import parse_config
from foreman.errors import HerdrError
from foreman.herdr import HerdrClient

from tests.fakes import FakeRunner, ScriptedReads, composer_screen, ok_json, painted_codex_composer


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
            "composer_placeholders": ["Ask Codex to do anything"],
            "recover_keys": [],
            "clear_prompt": "/new",
        },
        {
            "name": "recoverable",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "type",
            "composer_glyph": "› ",
            "composer_placeholders": ["Ask Codex to do anything"],
            "recover_keys": ["esc"],
            "clear_prompt": "/new",
        },
        {
            "name": "twoenter",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "type",
            "slash_enter_count": 2,
            "composer_glyph": "› ",
            "composer_placeholders": ["Ask Codex to do anything"],
            "recover_keys": [],
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
            "name": "claude",
            "kind": "claude",
            "usage_prompt": "/usage",
            "usage_marker": "Current week",
            "usage_read_source": "visible",
            "slash_delivery": "paste",
            "composer_glyph": "❯ ",
            "composer_ignore_dim": True,
            "recover_keys": ["esc"],
            "clear_prompt": "/clear",
        },
        {
            "name": "strict",
            "kind": "claude",
            "usage_prompt": "/usage",
            "usage_marker": "Current week",
            "usage_read_source": "visible",
            "slash_delivery": "paste",
            "composer_glyph": "❯ ",
            "composer_ignore_dim": False,
            "recover_keys": ["esc"],
            "clear_prompt": "/clear",
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
CODEX_UNPAINTED_EMPTY = (
    "\x1b[0m\x1b[1m› \x1b[0m\x1b[2mAsk Codex to do anything\x1b[0m\n"
    "\n"
    "  GPT-6.1-Sol high · /private/tmp/fixture · Execute issue #707 reset\n"
    "  \x1b[0m\x1b[1m?\x1b[0m for shortcuts     ⚠ 2 warnings · \x1b[0m\x1b[1mf2 \x1b[0mto view"
)
# Codex 0.162.0 startup, observed in an owned PTY without task input. These
# visible rows retain its SGR 22 intensity resets, not a Herdr ANSI export.
CODEX_INTENSITY_RESET_EMPTY = (
    "\x1b[22m\x1b[1m› \x1b[22m\x1b[2m\x1b[2mAsk Codex to do anything\n"
    "\n"
    "  GPT-6-Astra high · /private/tmp/fixture\n"
    "  \x1b[22m\x1b[1m? \x1b[22mfor shortcuts     ⚠ 3 warnings · "
    "\x1b[1mf2 \x1b[22mto view\x1b[0m"
)
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
        self.assertEqual(
            read_pane(HerdrClient(runner=runner), BY_NAME["blind"]), ("", False)
        )
        self.assertEqual(runner.calls, [])

    def test_an_agent_with_a_glyph_reads_its_viewport(self):
        runner = FakeRunner()
        runner.set("agent read codex", CODEX_EMPTY)
        text, ansi = read_pane(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertTrue(ansi)
        self.assertEqual(
            runner.commands(),
            ["agent read codex --source visible --lines 20 --format ansi"],
        )


class EnsureReadyTest(unittest.TestCase):
    """Recovery keys are the most dangerous thing foreman can send.

    On Codex the key that clears a composer is ctrl+c, and ctrl+c on an EMPTY
    Codex composer exits the process. Live, foreman read Codex's placeholder
    `Ask Codex to do anything` as typed text, sent the one recovery ctrl+c,
    and killed the agent. Every gate below exists because of that.
    """

    def _runner(self, screens, name="recoverable"):
        runner = FakeRunner()
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = ScriptedReads(screens)
        return runner

    def _ready(self, runner, name="recoverable", **kwargs):
        kwargs.setdefault("sleep", NO_SLEEP)
        kwargs.setdefault("warn", lambda message: None)
        return ensure_ready(HerdrClient(runner=runner), BY_NAME[name], **kwargs)

    def test_an_empty_composer_needs_no_recovery(self):
        runner = self._runner([CODEX_EMPTY])
        self._ready(runner)
        self.assertEqual(runner.writes(), [])

    def test_a_modal_over_the_prompt_refuses_before_any_input(self):
        # coding-policy#393: Codex drew its startup review dialog over the
        # prompt. An absent composer holds no content, so the content check
        # read it as ready and the assignment went into the dialog.
        runner = self._runner(["A startup review dialog and no prompt row"] * 40)
        with self.assertRaises(HerdrError) as caught:
            self._ready(runner)
        self.assertIn("prompt is not on screen", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_a_prompt_that_appears_late_is_waited_out(self):
        runner = self._runner(["still starting up", "still starting up", CODEX_EMPTY])
        self._ready(runner)
        self.assertEqual(runner.writes(), [])

    def test_text_foreman_did_not_type_is_refused_not_cleared(self):
        runner = self._runner([CODEX_HELD])
        with self.assertRaises(HerdrError) as caught:
            self._ready(runner)
        self.assertIn("--allow-recovery", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_a_command_foreman_typed_this_run_may_be_cleared(self):
        session = DispatchSession()
        session.remember("/new")
        runner = self._runner([CODEX_HELD, CODEX_EMPTY])
        self._ready(runner, session=session)
        self.assertEqual(runner.writes(), ["agent send-keys recoverable esc"])

    def test_allow_recovery_opts_in_to_clearing_a_strangers_text(self):
        runner = self._runner([CODEX_HELD, CODEX_EMPTY])
        self._ready(runner, session=DispatchSession(allow_recovery=True))
        self.assertEqual(runner.writes(), ["agent send-keys recoverable esc"])

    def test_recovery_is_sent_exactly_once_even_when_it_fails(self):
        # A second ctrl+c would exit Codex, so this must never loop.
        runner = self._runner([CODEX_HELD])
        with self.assertRaises(HerdrError):
            self._ready(runner, session=DispatchSession(allow_recovery=True))
        self.assertEqual(runner.writes(), ["agent send-keys recoverable esc"])

    def test_recovery_that_hides_the_prompt_refuses_before_later_input(self):
        runner = self._runner([CODEX_HELD] + ["modal with no prompt"] * 25)
        with self.assertRaisesRegex(HerdrError, "prompt is not on screen"):
            self._ready(runner, session=DispatchSession(allow_recovery=True))
        self.assertEqual(runner.writes(), ["agent send-keys recoverable esc"])

    def test_the_refusal_names_the_pane_without_echoing_the_text(self):
        runner = self._runner([CODEX_HELD])
        with self.assertRaises(HerdrError) as caught:
            self._ready(runner, pane_id="w3:p1")
        message = str(caught.exception)
        self.assertNotIn("/new", message)
        self.assertIn("holds input", message)
        self.assertIn("w3:p1", message)

    def test_an_agent_with_no_recover_keys_refuses_without_sending(self):
        runner = self._runner([CODEX_HELD], name="codex")
        with self.assertRaises(HerdrError) as caught:
            self._ready(runner, name="codex", session=DispatchSession(allow_recovery=True))
        self.assertIn("recover_keys", str(caught.exception))
        self.assertIn("exits the process", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_a_caller_supplied_read_is_reused(self):
        runner = self._runner([CODEX_EMPTY])
        self._ready(runner, text=CODEX_EMPTY)
        self.assertEqual(runner.calls, [])


class SendCommandTest(unittest.TestCase):
    def _runner(self, screens, name="codex"):
        runner = FakeRunner()
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = ScriptedReads(screens)
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
        self.assertEqual(result["extra_enters"], 0)
        self.assertFalse(result["recovered"])
        self.assertTrue(result["screen_changed"])

    def test_post_clear_startup_paint_settles_by_reading_without_any_extra_key(self):
        runner = self._runner([CODEX_EMPTY, "  › starting session\n", CODEX_FRESH])
        result = self._send(runner)
        self.assertTrue(result["consumed"])
        self.assertEqual(result["extra_enters"], 0)
        self.assertEqual(runner.writes(), ["pane send-text w3:p1 /new", "pane send-keys w3:p1 enter"])

    def test_post_clear_foreign_draft_remains_refused_after_bounded_reads(self):
        runner = self._runner([CODEX_EMPTY, "  › another person's draft\n"])
        with self.assertRaises(HerdrError) as caught:
            self._send(runner)
        self.assertTrue(caught.exception.details["composer_occupied"])
        self.assertEqual(runner.writes(), ["pane send-text w3:p1 /new", "pane send-keys w3:p1 enter"])

    def test_the_autocomplete_popup_costs_a_second_enter(self):
        # First Enter accepts the completion; the second submits.
        runner = self._runner([CODEX_EMPTY, CODEX_HELD, CODEX_FRESH])
        result = self._send(runner)
        self.assertEqual(result["extra_enters"], 1)
        self.assertTrue(result["consumed"])
        self.assertEqual(
            len([c for c in runner.commands() if c == "pane send-keys w3:p1 enter"]), 2
        )

    def test_live_guard_refuses_an_extra_enter_after_the_initial_delivery(self):
        runner = self._runner([CODEX_EMPTY, CODEX_HELD])

        def guard():
            if len(runner.writes()) >= 2:
                raise HerdrError("Foreground worker changed; inspect the pane before sending more input.", {})

        with self.assertRaisesRegex(HerdrError, "Foreground worker changed"):
            self._send(runner, before_input=guard)
        self.assertEqual(runner.writes(), ["pane send-text w3:p1 /new", "pane send-keys w3:p1 enter"])

    def test_a_command_that_will_not_submit_is_refused(self):
        runner = self._runner([CODEX_EMPTY, CODEX_HELD])
        with self.assertRaises(HerdrError) as caught:
            self._send(runner)
        message = str(caught.exception)
        self.assertIn("/new", message)
        self.assertIn("Nothing further was sent", message)
        self.assertIn("slash_enter_count", message)

    def test_the_extra_enters_are_bounded(self):
        runner = self._runner([CODEX_EMPTY, CODEX_HELD])
        with self.assertRaises(HerdrError):
            self._send(runner)
        # one from delivery, then MAX_EXTRA_ENTERS more, and no further
        self.assertEqual(
            len([c for c in runner.commands() if c == "pane send-keys w3:p1 enter"]), 3
        )

    def test_a_composer_holding_a_strangers_text_refuses_before_sending(self):
        runner = self._runner([CODEX_HELD, CODEX_EMPTY, CODEX_FRESH])
        with self.assertRaises(HerdrError):
            self._send(runner)
        self.assertEqual(runner.writes(), [])

    def test_post_command_composer_text_is_redacted_from_the_error(self):
        secret = "token-that-must-not-be-logged"
        runner = self._runner([CODEX_EMPTY, "  › {}\n".format(secret)])
        with self.assertRaises(HerdrError) as caught:
            self._send(runner)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(caught.exception.details))
        self.assertTrue(caught.exception.details["composer_occupied"])

    def test_a_composer_holding_the_foremans_own_command_is_recovered_first(self):
        session = DispatchSession()
        session.remember("/new")
        runner = self._runner([CODEX_HELD, CODEX_EMPTY, CODEX_FRESH], name="recoverable")
        result = send_command(
            HerdrClient(runner=runner),
            BY_NAME["recoverable"],
            "w3:p1",
            "/new",
            session=session,
            sleep=NO_SLEEP,
            warn=lambda message: None,
        )
        self.assertTrue(result["recovered"])
        self.assertTrue(result["consumed"])
        self.assertEqual(runner.commands()[1], "agent send-keys recoverable esc")

    def test_recovery_discovered_after_a_visibility_wait_is_reported(self):
        session = DispatchSession()
        session.remember("/new")
        runner = self._runner(
            ["modal with no prompt", CODEX_HELD, CODEX_EMPTY, CODEX_FRESH],
            name="recoverable",
        )
        result = send_command(
            HerdrClient(runner=runner),
            BY_NAME["recoverable"],
            "w3:p1",
            "/new",
            session=session,
            sleep=NO_SLEEP,
            warn=lambda message: None,
        )
        self.assertTrue(result["recovered"])

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




# --- Claude Code's dim ghost-text suggestion --------------------------------
#
# Nobody typed these. Claude Code pre-fills its input box after a task; Esc
# does not remove it, and the next thing typed replaces it.

DIM = "\x1b[2m"
GREY = "\x1b[38;5;242m"
BRIGHT_BLACK = "\x1b[90m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"
SUGGESTION = "check the other issues (#28, #30) for follow-up work"

CLAUDE_SUGGESTION = "  ───────\n  ❯ {}{}{}\n  ───────\n".format(DIM, SUGGESTION, RESET)
CLAUDE_GREY_SUGGESTION = "  ❯ {}{}{}\n".format(GREY, SUGGESTION, RESET)
CLAUDE_BRIGHT_BLACK = "  ❯ {}{}{}\n".format(BRIGHT_BLACK, SUGGESTION, RESET)
CLAUDE_TYPED = "  ❯ {}{}{}\n".format(BOLD, SUGGESTION, RESET)
CLAUDE_TYPED_AFTER = "  ❯ {}{}{}/clear\n".format(DIM, SUGGESTION, RESET)


class DimSuggestionTest(unittest.TestCase):
    """A dim suggestion is an empty composer wearing a costume."""

    def test_a_dim_suggestion_reads_as_empty(self):
        self.assertEqual(composer_text(CLAUDE_SUGGESTION, "❯ ", True), "")

    def test_a_grey_palette_suggestion_reads_as_empty(self):
        self.assertEqual(composer_text(CLAUDE_GREY_SUGGESTION, "❯ ", True), "")

    def test_bright_black_reads_as_empty(self):
        self.assertEqual(composer_text(CLAUDE_BRIGHT_BLACK, "❯ ", True), "")

    def test_the_same_text_in_bold_is_occupied(self):
        self.assertEqual(composer_text(CLAUDE_TYPED, "❯ ", True), SUGGESTION)

    def test_the_same_text_at_normal_weight_is_occupied(self):
        plain = "  ❯ {}\n".format(SUGGESTION)
        self.assertEqual(composer_text(plain, "❯ ", True), SUGGESTION)

    def test_a_typed_command_after_a_suggestion_is_occupied(self):
        self.assertEqual(composer_text(CLAUDE_TYPED_AFTER, "❯ ", True), "/clear")

    def test_without_the_setting_a_suggestion_still_counts(self):
        # A runtime with no ghost text must be unaffected.
        self.assertEqual(composer_text(CLAUDE_SUGGESTION, "❯ ", False), SUGGESTION)

    def test_the_earlier_live_suggestion_reads_as_empty_too(self):
        text = "  ❯ {}Wait for the architect's design note and phase-2 prompt{}\n".format(
            DIM, RESET
        )
        self.assertEqual(composer_text(text, "❯ ", True), "")

    def test_plain_text_with_no_escapes_is_unchanged(self):
        self.assertEqual(composer_text("  ❯ /clear\n", "❯ ", True), "/clear")

    def test_strip_ansi_leaves_only_visible_characters(self):
        self.assertEqual(strip_ansi("{}dim{}".format(DIM, RESET)), "dim")

    def test_the_screen_signature_ignores_styling(self):
        self.assertEqual(
            screen_signature("{}banner{}\n".format(DIM, RESET), "❯ "),
            screen_signature("banner\n", "❯ "),
        )


class DimAgentTest(unittest.TestCase):
    """The setting is per agent, and it reaches the dispatch gate."""

    def _runner(self, name, screens):
        runner = FakeRunner()
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = ScriptedReads(screens)
        return runner

    def test_a_suggestion_does_not_trigger_recovery(self):
        runner = self._runner("claude", [CLAUDE_SUGGESTION])
        ensure_ready(HerdrClient(runner=runner), BY_NAME["claude"], sleep=NO_SLEEP)
        self.assertEqual(runner.writes(), [])

    def test_the_same_pane_refuses_an_agent_that_does_not_ignore_dim(self):
        runner = self._runner("strict", [CLAUDE_SUGGESTION])
        with self.assertRaises(HerdrError):
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["strict"],
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )

    def test_dim_text_is_never_cleared_even_with_allow_recovery(self):
        # Nobody typed it, so no key is warranted whatever the operator asked
        # for. This is the gate that would have saved the Codex process.
        runner = self._runner("strict", [CLAUDE_SUGGESTION])
        with self.assertRaises(HerdrError) as caught:
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["strict"],
                session=DispatchSession(allow_recovery=True),
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )
        self.assertIn("dim", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_the_read_asks_for_ansi(self):
        runner = self._runner("claude", [CLAUDE_SUGGESTION])
        read_pane(HerdrClient(runner=runner), BY_NAME["claude"])
        self.assertEqual(
            runner.commands(),
            ["agent read claude --source visible --lines 20 --format ansi"],
        )

    def _fallback_runner(self, plain):
        runner = FakeRunner()
        runner.set(
            "agent read claude --source visible --lines 20 --format ansi",
            stdout="",
            returncode=2,
            stderr="unexpected argument '--format'",
        )
        runner.set("agent read claude --source visible --lines 20", plain)
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        return runner

    def test_a_failed_ansi_read_falls_back_to_plain_text(self):
        runner = self._fallback_runner("  ❯ /clear\n")
        warnings = []
        text, ansi = read_pane(
            HerdrClient(runner=runner), BY_NAME["claude"], warn=warnings.append
        )
        self.assertFalse(ansi)
        self.assertEqual(composer_text(text, "❯ ", True), "/clear")
        self.assertTrue(any("plain-text" in w for w in warnings))

    def test_the_plain_text_fallback_can_only_refuse_never_recover(self):
        # Without intensity, a dim placeholder is indistinguishable from typed
        # text -- so the fallback is never allowed to authorise a keystroke.
        runner = self._fallback_runner("  ❯ Ask Codex to do anything\n")
        with self.assertRaises(HerdrError) as caught:
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["claude"],
                session=DispatchSession(allow_recovery=True),
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )
        self.assertIn("plain text", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_the_fallback_refuses_even_text_foreman_typed(self):
        session = DispatchSession()
        session.remember("/clear")
        runner = self._fallback_runner("  ❯ /clear\n")
        with self.assertRaises(HerdrError):
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["claude"],
                session=session,
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )
        self.assertEqual(runner.writes(), [])


# --- the assignment has to land as a user message ---------------------------

UNKNOWN_SKILL = (
    "  ⏺ Args from unknown skill: assignment from the team lead. Your role for\n"
    "    this task is REVIEWER/ARCHITECT. Read /w/COMMON.md in full\n"
    "  ───────\n"
    "  ❯ \n"
)
LANDED = (
    "  > New assignment from the team lead. Your role for this task is REVIEWER.\n"
    "  ───────\n"
    "  ❯ \n"
)
WRAPPED = (
    "  > New assignment from the team\n"
    "    lead. Your role for this task is REVIEWER.\n"
    "  ❯ \n"
)
NOTHING = "  ───────\n  ❯ \n"

OPENING = "New assignment from the team lead."


class TranscriptTest(unittest.TestCase):
    def test_the_assignment_is_found_in_the_transcript(self):
        self.assertTrue(transcript_holds(LANDED, OPENING))

    def test_a_wrapped_message_is_still_found(self):
        self.assertTrue(transcript_holds(WRAPPED, OPENING))

    def test_an_empty_transcript_is_not_a_match(self):
        self.assertFalse(transcript_holds(NOTHING, OPENING))

    def test_styling_does_not_hide_the_match(self):
        self.assertTrue(transcript_holds("{}{}{}".format(DIM, LANDED, RESET), OPENING))

    def test_the_unknown_skill_shape_is_recognised(self):
        self.assertEqual(unknown_skill_error(UNKNOWN_SKILL), "unknown skill")

    def test_codexs_rejection_shape_is_recognised(self):
        text = "  Unrecognized command '/newNew'\n  › \n"
        self.assertEqual(unknown_skill_error(text), "Unrecognized command")

    def test_a_clean_transcript_reports_no_complaint(self):
        self.assertIsNone(unknown_skill_error(LANDED))


class SendMessageTest(unittest.TestCase):
    def _runner(self, screens, wait_ok=True):
        runner = FakeRunner()
        runner.set("agent prompt", ok_json("agent_prompt"))
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        if wait_ok:
            runner.set("agent wait", ok_json("agent_wait"))
        else:
            runner.set(
                "agent wait",
                stdout="",
                returncode=1,
                stderr='{"error":{"code":"timeout","message":"never left idle"}}',
            )
        runner.responses["agent read claude --source visible --lines 20"] = ScriptedReads(
            screens
        )
        self.runner = runner
        return HerdrClient(runner=runner)

    def _send(self, client, **kwargs):
        kwargs.setdefault("sleep", NO_SLEEP)
        kwargs.setdefault("warn", lambda message: None)
        return send_message(client, BY_NAME["claude"], "New assignment...", OPENING, **kwargs)

    def test_a_landed_assignment_is_confirmed(self):
        result = self._send(self._runner([NOTHING, LANDED]))
        self.assertTrue(result["landed"])
        self.assertTrue(result["started"])

    def test_the_unknown_skill_transcript_fails_the_agent(self):
        with self.assertRaises(HerdrError) as caught:
            self._send(self._runner([NOTHING, UNKNOWN_SKILL]))
        message = str(caught.exception)
        self.assertIn("slash command", message)
        self.assertIn("send-keys claude esc", message)

    def test_the_unknown_skill_path_stops_before_waiting_on_a_turn(self):
        with self.assertRaises(HerdrError):
            self._send(self._runner([NOTHING, UNKNOWN_SKILL]))
        self.assertEqual([c for c in self.runner.commands() if "agent wait" in c], [])

    def test_a_message_that_never_appears_and_never_starts_is_not_started(self):
        result = self._send(self._runner([NOTHING, NOTHING], wait_ok=False), attempts=2)
        self.assertFalse(result["landed"])
        self.assertFalse(result["started"])

    def test_that_case_warns(self):
        warnings = []
        self._send(
            self._runner([NOTHING, NOTHING], wait_ok=False),
            attempts=2,
            warn=warnings.append,
        )
        self.assertTrue(any("sent_but_not_started" in w for w in warnings))

    def test_a_turn_that_started_counts_even_without_a_transcript_match(self):
        result = self._send(self._runner([NOTHING, NOTHING]), attempts=1)
        self.assertFalse(result["landed"])
        self.assertTrue(result["started"])

    def test_the_landing_poll_is_bounded(self):
        self._send(self._runner([NOTHING, NOTHING], wait_ok=False), attempts=3)
        reads = [c for c in self.runner.commands() if c.startswith("agent read claude")]
        self.assertEqual(len(reads), 4)  # one for the composer gate, three polls

    def test_the_composer_is_checked_before_the_paste(self):
        client = self._runner([NOTHING, LANDED])
        self._send(client)
        commands = self.runner.commands()
        self.assertTrue(commands[0].startswith("agent read claude"))
        self.assertTrue(commands[1].startswith("agent prompt claude"))


# --- the live kill --------------------------------------------------------
#
# `apply` read Codex's empty-composer placeholder as typed text, sent the one
# recovery ctrl+c, and ctrl+c on an EMPTY Codex composer exits the process.
# The agent died and had to be restarted. Every assertion below is a gate that
# would have stopped it.

CODEX_PLACEHOLDER_DIM = "  Codex v1.2\n  ─────────\n  › {}Ask Codex to do anything{}\n".format(
    DIM, RESET
)
CODEX_PLACEHOLDER_PLAIN = "  Codex v1.2\n  ─────────\n  › Ask Codex to do anything\n"
CODEX_PLACEHOLDER_THEN_TYPED = "  › {}Ask Codex to do anything{}/new\n".format(DIM, RESET)
CODEX_DIM_TEXT_THEN_NORMAL_PLACEHOLDER = (
    "  › {}stale draft{}Ask Codex to do anything\n"
).format(DIM, RESET)
CODEX_RECALLED_MULTILINE = (
    "  Codex v1.2\n  ─────────\n"
    "  › {}New assignment from the team lead. Your role is DEVELOPER.{}\n"
    "    {}Read /tmp/brief-developer.md and execute it exactly.{}\n"
    "    {}Finish with the REPORT line it specifies.{}\n"
).format(DIM, RESET, DIM, RESET, DIM, RESET)
CODEX_PLACEHOLDER_FIRST_RECALL = (
    "  Codex v1.2\n  ─────────\n"
    "  › {}Ask Codex to do anything{}\n"
    "    {}but keep this recalled continuation{}\n"
    "\n"
    "  gpt-5.6-sol high · ~/Projects/example\n"
).format(DIM, RESET, DIM, RESET)
CODEX_GLYPH_FIRST_CONTINUATION = (
    "  › {}Ask Codex to do anything{}\n"
    "    {}› recalled continuation{}\n"
).format(DIM, RESET, DIM, RESET)
CODEX_BLANK_THEN_CONTINUATION = (
    "  › {}Ask Codex to do anything{}\n"
    "\n"
    "    {}stale second paragraph{}\n"
    "\n"
    "  gpt-5.6-sol high · ~/Projects/example\n"
).format(DIM, RESET, DIM, RESET)


class PlaceholderTest(unittest.TestCase):
    def test_native_intensity_reset_placeholder_excludes_the_footer(self):
        for leading_reset in ("0", "22"):
            for label_reset in ("0", "22"):
                for reset_after_space in (False, True):
                    with self.subTest(leading_reset=leading_reset,
                                     label_reset=label_reset,
                                     reset_after_space=reset_after_space):
                        shortcut = "\x1b[{}m\x1b[1m?{}for shortcuts".format(
                            leading_reset,
                            " \x1b[{}m".format(label_reset) if reset_after_space
                            else "\x1b[{}m ".format(label_reset),
                        )
                        screen = CODEX_INTENSITY_RESET_EMPTY.replace(
                            "\x1b[22m\x1b[1m? \x1b[22mfor shortcuts", shortcut,
                        )
                        composer = inspect_composer(screen, BY_NAME["codex"])
                        self.assertFalse(composer.occupied)
                        self.assertTrue(composer.placeholder)
                        self.assertEqual(composer.content, "")

    def test_intensity_reset_footer_does_not_hide_recalled_input(self):
        for draft in ("\n  keep this continuation", "\n\n  keep this paragraph"):
            with self.subTest(draft=draft):
                screen = CODEX_INTENSITY_RESET_EMPTY.replace("\n\n", draft + "\n\n", 1)
                composer = inspect_composer(screen, BY_NAME["codex"])
                self.assertTrue(composer.occupied)
                self.assertIn("keep this", composer.content)
                self.assertNotIn("for shortcuts", composer.content)

    def test_intensity_reset_footer_requires_native_key_style_and_separator(self):
        for screen in (
            strip_ansi(CODEX_INTENSITY_RESET_EMPTY),
            CODEX_INTENSITY_RESET_EMPTY.replace("\n\n", "\n"),
            CODEX_INTENSITY_RESET_EMPTY.replace("\x1b[1m? \x1b[22m", "\x1b[1m? "),
            CODEX_INTENSITY_RESET_EMPTY.replace("\x1b[1m?", "\x1b[2m?"),
            CODEX_INTENSITY_RESET_EMPTY.replace("\x1b[1m?", "\x1b[1:0m?"),
            CODEX_INTENSITY_RESET_EMPTY.replace("? \x1b[22m", "?\x1b[22m"),
            CODEX_INTENSITY_RESET_EMPTY + "\n  further draft text",
        ):
            with self.subTest(screen=screen):
                self.assertTrue(inspect_composer(screen, BY_NAME["codex"]).occupied)

    def test_native_unpainted_placeholder_excludes_the_styled_footer(self):
        for screen in (CODEX_UNPAINTED_EMPTY, CODEX_UNPAINTED_EMPTY.replace(
                "\x1b[1m?\x1b[0m for", "\x1b[1m? \x1b[0mfor")):
            with self.subTest(screen=screen):
                composer = inspect_composer(screen, BY_NAME["codex"])
                self.assertFalse(composer.occupied)
                self.assertTrue(composer.placeholder)

    def test_unpainted_recalled_draft_keeps_all_paragraphs_before_the_footer(self):
        for draft in (
            "\n  \x1b[2mkeep this recalled continuation\x1b[0m",
            "\n\n  \x1b[2mkeep this second paragraph\x1b[0m",
            "\n  \x1b[2mGPT-6.1-Sol high · /private/tmp/fixture\x1b[0m\n"
            "  \x1b[2m? for shortcuts\x1b[0m",
        ):
            with self.subTest(draft=draft):
                screen = CODEX_UNPAINTED_EMPTY.replace("\x1b[0m\n\n", "\x1b[0m" + draft + "\n\n", 1)
                composer = inspect_composer(screen, BY_NAME["codex"])
                self.assertTrue(composer.occupied)
                self.assertIn("\n", composer.content)
                self.assertNotIn("Execute issue #707 reset", composer.content)

    def test_unpainted_footer_without_ansi_or_separator_is_not_a_boundary(self):
        for screen in (
            strip_ansi(CODEX_UNPAINTED_EMPTY),
            CODEX_UNPAINTED_EMPTY.replace("\n\n", "\n"),
            CODEX_UNPAINTED_EMPTY + "\n  further draft text",
            CODEX_UNPAINTED_EMPTY.replace("\x1b[1m?\x1b[0m", "?"),
            CODEX_UNPAINTED_EMPTY.replace("\x1b[1m?\x1b[0m", "\x1b[1:0m?\x1b[0m"),
        ):
            with self.subTest(screen=screen):
                self.assertTrue(inspect_composer(screen, BY_NAME["codex"]).occupied)

    def test_painted_placeholder_excludes_indented_status_and_shortcuts(self):
        for background in ("48;2;62;64;81", "48;5;235", "44", "104"):
            with self.subTest(background=background):
                composer = inspect_composer(painted_codex_composer(background=background), BY_NAME["codex"])
                self.assertFalse(composer.occupied)
                self.assertTrue(composer.placeholder)

    def test_painted_recalled_paragraphs_remain_occupied(self):
        draft = "Ask Codex to do anything\n\nkeep this recalled continuation\n  › nested glyph"
        composer = inspect_composer(painted_codex_composer(draft), BY_NAME["codex"])
        self.assertTrue(composer.occupied)
        self.assertTrue(composer.dim)
        self.assertIn("keep this recalled continuation", composer.content)
        self.assertIn("› nested glyph", composer.content)
        self.assertNotIn("gpt-6-astra", composer.content)

    def test_footer_looking_recalled_text_is_still_input(self):
        draft = "Ask Codex to do anything\ngpt-6-astra high · ~/Projects/example\n← for agents · ? for shortcuts"
        composer = inspect_composer(painted_codex_composer(draft), BY_NAME["codex"])
        self.assertTrue(composer.occupied)
        self.assertIn("← for agents", composer.content)

    def test_plain_text_footer_ambiguity_remains_occupied(self):
        composer = inspect_composer(strip_ansi(painted_codex_composer()), BY_NAME["codex"])
        self.assertTrue(composer.occupied)

    def test_foreground_palette_operands_do_not_create_a_composer_boundary(self):
        # RGB components overlap background SGR codes but are color operands.
        for color in ("38;2;48;2;62", "38;5;48", "38;2;40;49;104", "58;5;44", "58;2;44;44;44"):
            with self.subTest(color=color):
                screen = "› \x1b[{}mAsk Codex to do anything\x1b[0m\n  keep this draft\n".format(color)
                self.assertTrue(inspect_composer(screen, BY_NAME["codex"]).occupied)

    def test_underline_color_operands_do_not_reset_inherited_background(self):
        for color in ("58;2;0;49;0", "58;5;49"):
            with self.subTest(color=color):
                screen = (
                    "\x1b[44m› Ask Codex to do anything\n"
                    "\x1b[{}m  actual content\x1b[0m\n  footer\n"
                ).format(color)
                composer = inspect_composer(screen, BY_NAME["codex"])
                self.assertTrue(composer.occupied)
                self.assertIn("actual content", composer.content)

    def test_background_inherited_across_rows_preserves_recalled_text(self):
        for reset in ("0", "49"):
            with self.subTest(reset=reset):
                screen = (
                    "\x1b[48;2;62;64;81m› Ask Codex to do anything\n"
                    "\n  do more\x1b[{}m\n  gpt-6-astra high\n"
                ).format(reset)
                composer = inspect_composer(screen, BY_NAME["codex"])
                self.assertTrue(composer.occupied)
                self.assertIn("do more", composer.content)
                self.assertNotIn("gpt-6-astra", composer.content)

    def test_reset_before_footer_ends_the_painted_composer(self):
        screen = "\x1b[44m› Ask Codex to do anything\n   \n\x1b[49m  gpt-6-astra high\n"
        self.assertFalse(inspect_composer(screen, BY_NAME["codex"]).occupied)

    def test_unsupported_colon_sgr_cannot_hide_a_recalled_continuation(self):
        screen = (
            "\x1b[48;2;62;64;81m› Ask Codex to do anything\x1b[0m\n"
            "\x1b[48:2::62:64:81m  do more\x1b[0m\n"
            "  gpt-6-astra high\n"
        )
        composer = inspect_composer(screen, BY_NAME["codex"])
        self.assertTrue(composer.occupied)
        self.assertIn("do more", composer.content)

    def test_the_hint_matches_exactly_after_trimming(self):
        self.assertTrue(is_placeholder("  Ask Codex to do anything  ", BY_NAME["codex"]))

    def test_a_command_appended_to_the_hint_is_not_the_hint(self):
        self.assertFalse(
            is_placeholder("Ask Codex to do anything/new", BY_NAME["codex"])
        )

    def test_an_agent_that_declares_none_matches_nothing(self):
        self.assertFalse(is_placeholder("Ask Codex to do anything", BY_NAME["claude"]))

    def test_empty_text_is_not_a_placeholder(self):
        self.assertFalse(is_placeholder("", BY_NAME["codex"]))
        self.assertFalse(is_placeholder(None, BY_NAME["codex"]))

    def test_a_dim_placeholder_reads_as_an_empty_composer(self):
        composer = inspect_composer(CODEX_PLACEHOLDER_DIM, BY_NAME["codex"])
        self.assertFalse(composer.occupied)
        self.assertEqual(composer.content, "")

    def test_a_normal_weight_placeholder_reads_as_empty_too(self):
        # Belt as well as braces: the exact-match list catches it even if the
        # runtime stops drawing the hint dim.
        composer = inspect_composer(CODEX_PLACEHOLDER_PLAIN, BY_NAME["codex"])
        self.assertFalse(composer.occupied)
        self.assertTrue(composer.placeholder)

    def test_a_command_typed_over_the_placeholder_is_occupied(self):
        composer = inspect_composer(CODEX_PLACEHOLDER_THEN_TYPED, BY_NAME["codex"])
        self.assertTrue(composer.occupied)
        self.assertEqual(composer.content, "Ask Codex to do anything/new")

    def test_dim_text_before_a_normal_placeholder_is_occupied(self):
        composer = inspect_composer(
            CODEX_DIM_TEXT_THEN_NORMAL_PLACEHOLDER, BY_NAME["codex"]
        )
        self.assertTrue(composer.occupied)
        self.assertIn("stale draft", composer.content)

    def test_a_dim_recalled_multiline_prompt_is_occupied(self):
        composer = inspect_composer(CODEX_RECALLED_MULTILINE, BY_NAME["codex"])
        self.assertTrue(composer.occupied)
        self.assertIn("New assignment from the team lead", composer.content)
        self.assertTrue(composer.dim)

    def test_a_placeholder_first_row_with_a_recalled_continuation_is_occupied(self):
        composer = inspect_composer(CODEX_PLACEHOLDER_FIRST_RECALL, BY_NAME["codex"])
        self.assertTrue(composer.occupied)
        self.assertIn("recalled continuation", composer.content)
        self.assertNotIn("gpt-5.6-sol", composer.content)

    def test_an_indented_glyph_at_the_start_of_a_continuation_stays_occupied(self):
        composer = inspect_composer(CODEX_GLYPH_FIRST_CONTINUATION, BY_NAME["codex"])
        self.assertTrue(composer.occupied)
        self.assertIn("recalled continuation", composer.content)

    def test_an_embedded_blank_does_not_hide_later_recalled_text(self):
        composer = inspect_composer(CODEX_BLANK_THEN_CONTINUATION, BY_NAME["codex"])
        self.assertTrue(composer.occupied)
        self.assertIn("stale second paragraph", composer.content)
        self.assertNotIn("gpt-5.6-sol", composer.content)

    def test_recovery_is_refused_for_a_placeholder(self):
        composer = inspect_composer(CODEX_PLACEHOLDER_PLAIN, BY_NAME["codex"])
        allowed, reason = recovery_allowed(
            BY_NAME["codex"], composer, DispatchSession(allow_recovery=True)
        )
        self.assertFalse(allowed)


class LiveKillSequenceTest(unittest.TestCase):
    """`agent get` ok, composer shows the placeholder -- send NO keys."""

    def _runner(self, screen, name="codex"):
        runner = FakeRunner()
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = ScriptedReads([screen])
        return runner

    def test_the_placeholder_sends_no_keys_at_all(self):
        runner = self._runner(CODEX_PLACEHOLDER_DIM)
        ensure_ready(HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP)
        self.assertEqual(runner.writes(), [])

    def test_painted_placeholder_with_footer_sends_no_recovery_keys(self):
        runner = self._runner(painted_codex_composer())
        ensure_ready(HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP)
        self.assertEqual(runner.writes(), [])

    def test_unpainted_native_placeholder_sends_no_recovery_keys(self):
        runner = self._runner(CODEX_UNPAINTED_EMPTY)
        ensure_ready(HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP)
        self.assertEqual(runner.writes(), [])

    def test_native_intensity_reset_placeholder_sends_no_recovery_keys(self):
        runner = self._runner(CODEX_INTENSITY_RESET_EMPTY)
        ensure_ready(HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP)
        self.assertEqual(runner.writes(), [])

    def test_no_ctrl_c_reaches_codex_on_the_live_pane(self):
        runner = self._runner(CODEX_PLACEHOLDER_DIM)
        ensure_ready(HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP)
        self.assertEqual([c for c in runner.commands() if "ctrl+c" in c], [])

    def test_the_undimmed_placeholder_sends_no_keys_either(self):
        runner = self._runner(CODEX_PLACEHOLDER_PLAIN)
        ensure_ready(HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP)
        self.assertEqual(runner.writes(), [])

    def test_dispatch_proceeds_normally_over_the_placeholder(self):
        runner = FakeRunner()
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.responses["agent read codex --source visible --lines 20"] = ScriptedReads(
            [CODEX_PLACEHOLDER_DIM, CODEX_FRESH]
        )
        result = send_command(
            HerdrClient(runner=runner),
            BY_NAME["codex"],
            "w3:p1",
            "/new",
            sleep=NO_SLEEP,
            warn=lambda message: None,
        )
        self.assertTrue(result["consumed"])
        self.assertFalse(result["recovered"])
        self.assertEqual([c for c in runner.commands() if "ctrl+c" in c], [])

    def test_even_a_stranger_text_cannot_reach_ctrl_c_on_codex(self):
        # The last line of defence: codex ships with recover_keys [].
        runner = self._runner(CODEX_HELD)
        with self.assertRaises(HerdrError):
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["codex"],
                session=DispatchSession(allow_recovery=True),
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )
        self.assertEqual([c for c in runner.commands() if "ctrl+c" in c], [])

    def test_a_slash_command_refuses_when_the_prompt_is_not_visible(self):
        runner = self._runner("a modal with no prompt row")
        with self.assertRaises(HerdrError) as caught:
            send_command(
                HerdrClient(runner=runner), BY_NAME["codex"], "w3:p1", "/status",
                sleep=NO_SLEEP, warn=lambda message: None,
            )
        self.assertIn("prompt is not on screen", str(caught.exception))
        self.assertEqual(runner.writes(), [])




# --- the dim-slash-text kill ----------------------------------------------
#
# Codex renders a slash command in a dim/highlight style while its
# autocomplete popup is open. `composer_ignore_dim: true` filtered the real,
# unsent `/new` out as if it were ghost text, foreman declared it consumed,
# and the brief was pasted onto it: "Unrecognized command '/newNew
# assignment...'". So while looking for the command it just typed, foreman
# ignores dimness entirely.

CODEX_HELD_DIM = "  Codex v1.2\n  ─────────\n  › {}/new{}\n".format(DIM, RESET)
CODEX_HELD_GREY = "  Codex v1.2\n  ─────────\n  › {}/new{}\n".format(GREY, RESET)


class DimSlashTextTest(unittest.TestCase):
    """A dim slash command is still a slash command."""

    def test_a_dim_command_counts_as_still_present(self):
        self.assertTrue(command_still_present(CODEX_HELD_DIM, BY_NAME["codex"], "/new"))

    def test_a_grey_command_counts_as_still_present(self):
        self.assertTrue(command_still_present(CODEX_HELD_GREY, BY_NAME["codex"], "/new"))

    def test_a_normal_weight_command_counts_too(self):
        self.assertTrue(command_still_present(CODEX_HELD, BY_NAME["codex"], "/new"))

    def test_an_empty_composer_does_not(self):
        self.assertFalse(command_still_present(CODEX_EMPTY, BY_NAME["codex"], "/new"))

    def test_the_placeholder_does_not(self):
        self.assertFalse(
            command_still_present(CODEX_PLACEHOLDER_DIM, BY_NAME["codex"], "/new")
        )

    def test_the_appended_shape_from_the_live_failure_counts(self):
        text = "  › /newNew assignment from the team lead.\n"
        self.assertTrue(command_still_present(text, BY_NAME["codex"], "/new"))

    def test_the_dim_filter_would_have_hidden_it(self):
        # The bug, stated: with dim filtering the composer looks empty.
        self.assertEqual(composer_text(CODEX_HELD_DIM, "› ", True), "")
        self.assertEqual(composer_text(CODEX_HELD_DIM, "› ", False), "/new")


class DimSlashTextSendTest(unittest.TestCase):
    def _runner(self, screens):
        runner = FakeRunner()
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.responses["agent read codex --source visible --lines 20"] = ScriptedReads(
            screens
        )
        self.runner = runner
        return HerdrClient(runner=runner)

    def _send(self, client, **kwargs):
        kwargs.setdefault("sleep", NO_SLEEP)
        kwargs.setdefault("warn", lambda message: None)
        return send_command(client, BY_NAME["codex"], "w3:p1", "/new", **kwargs)

    def test_a_dim_command_triggers_another_enter_rather_than_success(self):
        client = self._runner([CODEX_EMPTY, CODEX_HELD_DIM, CODEX_FRESH])
        result = self._send(client)
        self.assertEqual(result["extra_enters"], 1)
        self.assertTrue(result["consumed"])

    def test_a_dim_command_that_never_submits_is_refused(self):
        client = self._runner([CODEX_EMPTY, CODEX_HELD_DIM])
        with self.assertRaises(HerdrError) as caught:
            self._send(client)
        self.assertIn("/new", str(caught.exception))

    def test_nothing_is_pasted_after_that_refusal(self):
        client = self._runner([CODEX_EMPTY, CODEX_HELD_DIM])
        with self.assertRaises(HerdrError):
            self._send(client)
        self.assertEqual(self.runner.pasted_prompts(), [])

    def test_a_dim_placeholder_after_the_command_is_still_consumed(self):
        # Once the command text is gone, placeholder and dim rules apply
        # again -- otherwise Codex could never be dispatched at all.
        client = self._runner([CODEX_EMPTY, CODEX_PLACEHOLDER_DIM, CODEX_FRESH])
        result = self._send(client)
        self.assertEqual(result["extra_enters"], 0)
        self.assertTrue(result["consumed"])


class SlashEnterCountTest(unittest.TestCase):
    def test_codex_ships_with_two_enters(self):
        import json as _json

        from foreman.config import parse_config as _parse

        path = _os.path.join(_ROOT, "config.example.json")
        with open(path, encoding="utf-8") as handle:
            agents = {a.name: a for a in _parse(_json.load(handle))}
        self.assertEqual(agents["codex"].slash_enter_count, 2)
        self.assertEqual(agents["claude"].slash_enter_count, 1)
        self.assertEqual(agents["grok"].slash_enter_count, 1)

    def test_the_configured_count_is_what_gets_sent(self):
        runner = FakeRunner()
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.responses["agent read twoenter --source visible --lines 20"] = ScriptedReads(
            [CODEX_EMPTY, CODEX_FRESH]
        )
        send_command(
            HerdrClient(runner=runner),
            BY_NAME["twoenter"],
            "w3:p1",
            "/new",
            sleep=NO_SLEEP,
            warn=lambda message: None,
        )
        self.assertEqual(
            len([c for c in runner.commands() if c == "pane send-keys w3:p1 enter"]), 2
        )



class FreshStartupDeliveryTest(unittest.TestCase):
    EMPTY = "\x1b[2m› Ask Codex to do anything\x1b[0m"
    ANIMATED = "\x1b[2m› Ask Codex to do anything ✦\x1b[0m"

    def run_send(self, frames, *, observe=None, agent=None):
        from unittest.mock import Mock, patch
        client = Mock()
        writes = []
        client.agent_prompt.side_effect = lambda *args: writes.append(args)
        boundary = Mock()
        reads = iter(frames)
        with patch("foreman.composer.read_pane", side_effect=lambda *_a, **_k: next(reads)), \
                patch("foreman.composer._left_idle", return_value=True):
            result = send_message(client, agent or BY_NAME["codex"], "immutable brief", "immutable brief", pane_id="p1",
                                  startup_observe=observe or (lambda: ("p1", 42, "model", "high")),
                                  before_prompt=boundary, sleep=NO_SLEEP, attempts=1)
        return client, boundary, writes, result

    def test_native_intensity_reset_startup_sends_once_after_two_empty_reads(self):
        client, boundary, writes, result = self.run_send([
            (CODEX_INTENSITY_RESET_EMPTY, True),
            (CODEX_INTENSITY_RESET_EMPTY, True), ("immutable brief", True),
        ])
        self.assertEqual(writes, [("codex", "immutable brief")])
        self.assertTrue(result["landed"])
        boundary.assert_called_once()
        client.pane_send_keys.assert_not_called()
        client.pane_send_text.assert_not_called()

    def test_native_claude_dynamic_hint_permits_one_prompt_without_recovery(self):
        # #723: captured Claude 2.1.294 ANSI composer and decorated footer.
        frame = (
            '\x1b[0m\x1b[38;2;153;153;153m○ low · /effort\x1b[0m\n'
            '\x1b[0m\x1b[38;2;136;136;136m────────────────────\x1b[0m\n'
            '❯\xa0\x1b[0m\x1b[2mTry "how does promote-skill.sh work?"\x1b[0m\n'
            '\x1b[0m\x1b[38;2;136;136;136m────────────────────\x1b[0m\n'
            '  \x1b[0m\x1b[38;5;111m~/Projects/nanoclaw\x1b[0m'
            '\x1b[38;2;153;153;153m \ue0a0 main\x1b[0m'
            '\x1b[38;5;215m Haiku 5.5\x1b[0m\n'
            '  \x1b[0m\x1b[38;2;255;107;128m⏵⏵ bypass permissions on\x1b[0m'
        )
        client, boundary, writes, result = self.run_send(
            [(frame, True), (frame, True), ("immutable brief", True)], agent=BY_NAME["claude"])
        self.assertEqual(writes, [("claude", "immutable brief")])
        self.assertTrue(result["landed"])
        boundary.assert_called_once()
        client.pane_send_keys.assert_not_called()
        client.pane_send_text.assert_not_called()

    def test_claude_hint_does_not_admit_plain_typed_or_mixed_input(self):
        from unittest.mock import Mock, patch
        hint = '❯\xa0\x1b[2mTry "how does promote-skill.sh work?"\x1b[0m'
        for agent, frame, ansi in [
            (BY_NAME["claude"], hint, False),
            (BY_NAME["claude"], strip_ansi(hint), True),
            (BY_NAME["claude"], hint + " authored text", True),
            (BY_NAME["strict"], hint, True),
            (BY_NAME["claude"], "Trust this directory?", True),
            (BY_NAME["codex"], "\x1b[2m› recalled draft\x1b[0m", True),
        ]:
            with self.subTest(agent=agent.name, frame=frame, ansi=ansi):
                client, boundary = Mock(), Mock()
                with patch("foreman.composer.read_pane", return_value=(frame, ansi)), self.assertRaises(HerdrError):
                    send_message(client, agent, "brief", "brief", before_prompt=boundary,
                                 startup_observe=lambda: ("p1", 42), sleep=NO_SLEEP)
                boundary.assert_not_called()
                client.agent_prompt.assert_not_called()
                client.pane_send_keys.assert_not_called()
                client.pane_send_text.assert_not_called()

    def test_animation_settles_before_exactly_one_prompt_without_keys(self):
        client, boundary, writes, result = self.run_send([
            (self.ANIMATED, True), (self.EMPTY, True), (self.EMPTY, True), ("immutable brief", True)])
        self.assertEqual(writes, [("codex", "immutable brief")])
        boundary.assert_called_once()
        self.assertTrue(result["landed"])
        client.pane_send_keys.assert_not_called()
        client.pane_send_text.assert_not_called()

    def test_native_braille_paint_waits_for_two_real_empty_reads_before_prompt(self):
        # #725: first two complete rows of the captured Codex 0.160.1 frame.
        # The trace's truncated final row is not treated as native evidence.
        frame = (
            "\u001b[0m\u001b[38;2;134;139;165m\u001b[48;2;62;64;81m⠁\u001b[0m\u001b[48;2;62;64;81m   \u001b[0m\u001b[3"
            "8;2;138;144;171m\u001b[48;2;62;64;81m⠈\u001b[0m\u001b[48;2;62;64;81m         \u001b[0m\u001b[38"
            ";2;136;142;168m\u001b[48;2;62;64;81m⠄\u001b[0m\u001b[48;2;62;64;81m          \u001b[0m\u001b[38"
            ";2;138;144;171m\u001b[48;2;62;64;81m⢀\u001b[0m\u001b[48;2;62;64;81m                  "
            "     \u001b[0m\u001b[38;2;138;144;171m\u001b[48;2;62;64;81m⠐\u001b[0m\u001b[48;2;62;64;81m     "
            " \u001b[0m\u001b[38;2;71;74;92m\u001b[48;2;62;64;81m⡀\u001b[0m\u001b[38;2;120;125;149m\u001b[48;2;62"
            ";64;81m⠐\u001b[0m\u001b[48;2;62;64;81m  \u001b[0m\u001b[38;2;80;83;103m\u001b[48;2;62;64;81m⠄\u001b["
            "0m\u001b[48;2;62;64;81m             \u001b[0m\u001b[38;2;137;143;169m\u001b[48;2;62;64;81m"
            "⢀\u001b[0m\u001b[48;2;62;64;81m \u001b[0m\u001b[38;2;78;81;100m\u001b[48;2;62;64;81m⠄\u001b[0m\u001b[48;2"
            ";62;64;81m  \u001b[0m\u001b[38;2;129;134;159m\u001b[48;2;62;64;81m⠈\u001b[0m\u001b[48;2;62;64;8"
            "1m      \u001b[0m\u001b[38;2;95;98;119m\u001b[48;2;62;64;81m⠄\u001b[0m\u001b[48;2;62;64;81m    "
            "\u001b[0m\u001b[38;2;104;108;130m\u001b[48;2;62;64;81m⠠\u001b[0m\u001b[48;2;62;64;81m \u001b[0m\u001b[38;"
            "2;75;77;96m\u001b[48;2;62;64;81m⠠\u001b[0m\u001b[48;2;62;64;81m      \u001b[0m\u001b[38;2;90;94"
            ";114m\u001b[48;2;62;64;81m⡀\u001b[0m\u001b[48;2;62;64;81m    \u001b[0m\n\u001b[0m\u001b[1m\u001b[48;2;62;6"
            "4;81m›\u001b[0m\u001b[38;2;68;70;88m\u001b[48;2;62;64;81m⠁\u001b[0m\u001b[2m\u001b[48;2;62;64;81mAsk"
            " Codex to do anything\u001b[0m\u001b[48;2;62;64;81m   \u001b[0m\u001b[38;2;134;140;166m\u001b[4"
            "8;2;62;64;81m⠈\u001b[0m\u001b[48;2;62;64;81m       \u001b[0m\u001b[38;2;107;111;134m\u001b[48;2"
            ";62;64;81m⠂\u001b[0m\u001b[48;2;62;64;81m  \u001b[0m\u001b[38;2;98;102;123m\u001b[48;2;62;64;81"
            "m⠁\u001b[0m\u001b[48;2;62;64;81m                             \u001b[0m\u001b[38;2;135;140;"
            "166m\u001b[48;2;62;64;81m⠄\u001b[0m\u001b[48;2;62;64;81m    \u001b[0m\u001b[38;2;133;138;164m\u001b["
            "48;2;62;64;81m⠂\u001b[0m\u001b[48;2;62;64;81m         \u001b[0m\u001b[38;2;118;122;146m\u001b[4"
            "8;2;62;64;81m⠐\u001b[0m\u001b[48;2;62;64;81m \u001b[0m\u001b[38;2;114;119;143m\u001b[48;2;62;64"
            ";81m⠁\u001b[0m\u001b[48;2;62;64;81m   \u001b[0m\u001b[38;2;71;74;92m\u001b[48;2;62;64;81m⢀\u001b[0m\u001b"
            "[48;2;62;64;81m            \u001b[0m\u001b[38;2;70;72;90m\u001b[48;2;62;64;81m⠄\u001b[0m"
        )
        held = inspect_composer(frame, BY_NAME["codex"])
        self.assertTrue(held.visible)
        self.assertTrue(held.occupied)
        client, boundary, writes, result = self.run_send([
            (frame, True), (self.EMPTY, True), (self.EMPTY, True), ("immutable brief", True)])
        self.assertEqual(writes, [("codex", "immutable brief")])
        self.assertTrue(result["landed"])
        boundary.assert_called_once()
        client.pane_send_keys.assert_not_called()
        client.pane_send_text.assert_not_called()

    def test_empty_reads_must_be_consecutive_after_occupied_paint(self):
        client, boundary, writes, result = self.run_send([
            (self.EMPTY, True), (self.ANIMATED, True), (self.EMPTY, True),
            (self.EMPTY, True), ("immutable brief", True)])
        self.assertTrue(result["landed"])
        self.assertEqual(writes, [("codex", "immutable brief")])
        boundary.assert_called_once()
        client.pane_send_keys.assert_not_called()

    def test_persistent_drafts_refuse_at_the_readonly_bound_without_keys(self):
        from unittest.mock import Mock, patch
        for frame in ["› authored draft", "\x1b[2m› recalled draft\x1b[0m",
                      self.EMPTY + "\n  authored continuation", self.ANIMATED,
                      CODEX_INTENSITY_RESET_EMPTY.replace("\n\n", "\n  recalled draft\n\n", 1),
                      strip_ansi(CODEX_INTENSITY_RESET_EMPTY)]:
            with self.subTest(frame=frame):
                client, boundary = Mock(), Mock()
                with patch("foreman.composer.read_pane", return_value=(frame, True)) as reads, self.assertRaises(HerdrError) as caught:
                    send_message(client, BY_NAME["codex"], "brief", "brief", before_prompt=boundary,
                                 startup_observe=lambda: ("p1", 42), sleep=NO_SLEEP)
                self.assertEqual(reads.call_count, FRESH_COMPOSER_ATTEMPTS)
                self.assertEqual(caught.exception.details["failure_kind"], "startup_input_occupied")
                boundary.assert_not_called()
                client.agent_prompt.assert_not_called()
                client.pane_send_keys.assert_not_called()
                client.pane_send_text.assert_not_called()

    def test_initial_paint_without_composer_settles_before_one_prompt(self):
        client, boundary, writes, result = self.run_send([
            ("Starting Codex", True), (self.EMPTY, True), (self.EMPTY, True), ("immutable brief", True)])
        self.assertEqual(writes, [("codex", "immutable brief")])
        boundary.assert_called_once()
        self.assertTrue(result["landed"])
        client.pane_send_keys.assert_not_called()

    def test_claude_model_unavailable_notice_refuses_before_any_input_with_a_repair_path(self):
        # coding-policy#733: the saved native model_not_found text, verbatim.
        from unittest.mock import Mock, patch
        notice = ("There's an issue with the selected model (opus-6). It may not exist or you may not have "
                  "access to it. Run /model to pick a different model.")
        for frame in (notice + "\n❯ ", "\u23fa " + notice + "\n\x1b[2m❯ \x1b[0m"):
            with self.subTest(frame=frame):
                client, boundary = Mock(), Mock()
                with patch("foreman.composer.read_pane", return_value=(frame, True)), self.assertRaises(HerdrError) as caught:
                    send_message(client, BY_NAME["claude"], "brief", "brief", before_prompt=boundary,
                                 startup_observe=lambda: ("p1", 42), sleep=NO_SLEEP)
                details = caught.exception.details
                self.assertEqual((details["failure_kind"], details["model"]), ("model_identifier_unavailable", "opus-6"))
                self.assertIn(" plan", details["recovery"]["operation"])
                self.assertIn("same-family successor", details["recovery"]["condition"])
                boundary.assert_not_called()
                client.agent_prompt.assert_not_called()
                client.pane_send_keys.assert_not_called()
                client.pane_send_text.assert_not_called()

    def test_model_unavailable_notice_needs_a_bare_claude_row_with_a_plain_model_token(self):
        from foreman.composer import identifier_unavailable_model
        notice = "There's an issue with the selected model ({}). It may not exist or you may not have access to it."
        self.assertEqual(identifier_unavailable_model(notice.format("claude-opus-6") + "\n❯"), "claude-opus-6")
        for text in ("> " + notice.format("opus-6"), "see: " + notice.format("opus-6"), notice.format("opus-6; rm -rf"),
                     notice.format("") , "This content can't be shown\n❯"):
            with self.subTest(text=text):
                self.assertIsNone(identifier_unavailable_model(text))

    def test_occupied_composer_after_the_notice_takes_the_occupied_path_not_identifier_maintenance(self):
        from unittest.mock import Mock, patch
        notice = ("There's an issue with the selected model (opus-6). It may not exist or you may not have "
                  "access to it. Run /model to pick a different model.")
        # Plain: the occupied classification. Boxed: the same fail-closed path an
        # ordinary boxed occupied startup takes, never identifier maintenance.
        for frame, kind in ((notice + "\n\u276f pending input", "startup_input_occupied"),
                            (notice + "\n\u2502 \u276f pending input \u2502", "startup_dialog_pending")):
            with self.subTest(frame=frame):
                client, boundary = Mock(), Mock()
                with patch("foreman.composer.read_pane", return_value=(frame, True)), self.assertRaises(HerdrError) as caught:
                    send_message(client, BY_NAME["claude"], "brief", "brief", before_prompt=boundary,
                                 startup_observe=lambda: ("p1", 42), sleep=NO_SLEEP)
                details = caught.exception.details
                self.assertEqual(details["failure_kind"], kind)
                boundary.assert_not_called()
                client.agent_prompt.assert_not_called()
                client.pane_send_keys.assert_not_called()
                client.pane_send_text.assert_not_called()

    def test_model_unavailable_notice_ignores_fenced_indented_and_stale_rows(self):
        # Parity with wait-report.sh: examples and history are not a live notice.
        from foreman.composer import identifier_unavailable_model
        row = "There's an issue with the selected model (opus-6). It may not exist or you may not have access to it."
        for text in ("```\n" + row + "\n```\n❯", "~~~\n" + row + "\n❯", "    " + row + "\n❯", "\t" + row + "\n❯",
                     row + "\nStartup completed normally\n❯", row + "\nNew assignment from the team lead\n❯",
                     row + "\n│ ❯ pending input │"):
            with self.subTest(text=text):
                self.assertIsNone(identifier_unavailable_model(text))
        for text in (row, "\u23fa " + row + "\n╭────╮\n│ ❯  │\n╰────╯\n? for shortcuts",
                     "```\nexample\n```\n" + row + "\n❯ "):
            with self.subTest(text=text):
                self.assertEqual(identifier_unavailable_model(text), "opus-6")

    def test_other_adapters_ignore_the_claude_model_notice_and_keep_their_startup_path(self):
        from unittest.mock import Mock, patch
        notice = ("There's an issue with the selected model (opus-6). It may not exist or you may not have "
                  "access to it.")
        client = Mock()
        with patch("foreman.composer.read_pane", return_value=(notice + "\n› authored draft", True)), self.assertRaises(HerdrError) as caught:
            send_message(client, BY_NAME["codex"], "brief", "brief", startup_observe=lambda: ("p1", 42), sleep=NO_SLEEP)
        self.assertEqual(caught.exception.details["failure_kind"], "startup_input_occupied")

    def test_hook_review_glyph_is_not_an_assignment_composer(self):
        from unittest.mock import Mock, patch
        client, boundary = Mock(), Mock()
        frame = "Hooks need review\n1 hook is new or changed.\n› 1. Review hooks\n2. Trust all and continue"
        with patch("foreman.composer.read_pane", return_value=(frame, True)), self.assertRaises(HerdrError) as caught:
            send_message(client, BY_NAME["codex"], "brief", "brief", before_prompt=boundary,
                         startup_observe=lambda: ("p1", 42), sleep=NO_SLEEP)
        self.assertEqual(caught.exception.details["failure_kind"], "startup_dialog_pending")
        boundary.assert_not_called()
        client.agent_prompt.assert_not_called()
        client.pane_send_keys.assert_not_called()

    def test_unsafe_startup_frames_never_reach_prompt_or_sending_boundary(self):
        from unittest.mock import Mock, patch
        for frame in [("\x1b[2m› recalled draft\x1b[0m", True),
                      (self.EMPTY + "\n  real continuation", True), ("permission dialog", True),
                      (self.EMPTY, False), ("› /new", True)]:
            with self.subTest(frame=frame):
                client, boundary = Mock(), Mock()
                with patch("foreman.composer.read_pane", return_value=frame), self.assertRaises(HerdrError):
                    send_message(client, BY_NAME["codex"], "brief", "brief", before_prompt=boundary,
                                 startup_observe=lambda: ("p1", 42), sleep=NO_SLEEP)
                boundary.assert_not_called()
                client.agent_prompt.assert_not_called()
                client.pane_send_keys.assert_not_called()

    def test_animation_timeout_preserves_classification_and_sends_nothing(self):
        from unittest.mock import Mock, patch
        client, boundary = Mock(), Mock()
        with patch("foreman.composer.read_pane", return_value=(self.ANIMATED, True)), self.assertRaises(HerdrError) as caught:
            send_message(client, BY_NAME["codex"], "brief", "brief", before_prompt=boundary,
                         startup_observe=lambda: ("p1", 42), sleep=NO_SLEEP)
        self.assertTrue(caught.exception.details["dim"])
        self.assertFalse(caught.exception.details["placeholder"])
        boundary.assert_not_called()
        client.agent_prompt.assert_not_called()
        client.pane_send_keys.assert_not_called()

    def test_changed_startup_identity_refuses_before_input(self):
        from unittest.mock import Mock
        for changed in [("other-pane", 42, "model", "high"), ("p1", 43, "model", "high"),
                        ("p1", 42, "other-model", "high"), ("p1", 42, "model", "low")]:
            with self.subTest(changed=changed), self.assertRaises(HerdrError):
                self.run_send([(self.EMPTY, True)], observe=Mock(side_effect=[("p1", 42, "model", "high"), changed]))

if __name__ == "__main__":
    unittest.main()
