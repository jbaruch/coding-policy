"""Tests for teamlead.parsers.

Every fixture is an inline string copied from a real pane read. No clock, no
randomness, no files on disk.
"""

# Standalone-run shim: scripts/run-tests.sh executes each suite as
# `python3 <file>` from the repo root, so put the skill directory (this file's
# grandparent) on sys.path before the package imports below.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from teamlead.errors import ParseError
from teamlead.parsers import (
    headroom_pct,
    parse_claude_usage,
    parse_codex_usage,
    parse_grok_usage,
    parse_usage,
)

CLAUDE_SAMPLE = """\
   Settings  Status   Config   Usage   Stats
   Session
   Total cost:            $0.0000
   Current session
   ████                                               8% used
   Resets 12:49am (Europe/Oslo)
   Current week (all models)
   █                                                  2% used
   Resets Sep 5 at 11:59pm (Europe/Oslo)
   +50% weekly limits promo through Sep 13 · clau.de/cc-50-promo
   Current week (Fable)
   ▌                                                  1% used
   Resets Sep 5 at 11:59pm (Europe/Oslo)
"""

CODEX_SAMPLE = """\
│  Account:                     jbaruch@sadogursky.com (Pro)                             │
│  Weekly limit:                [█████████████████░░░] 87% left (resets 17:26 on 7 Sep)  │
│  GPT-5.3-Codex-Spark limit:                                                            │
│  5h limit:                    [████████████████████] 100% left (resets 02:19 on 2 Sep) │
│  Weekly limit:                [████████████████████] 100% left (resets 21:19 on 8 Sep) │
"""

GROK_SAMPLE = """\
     Session usage: no model calls yet in this session.
     Weekly limit: 0%
     Next reset: September 6, 12:55
     Credits: $16.42
     Auto topup: $20
"""


class ClaudeParserTest(unittest.TestCase):
    def test_captures_every_window_in_order(self):
        result = parse_claude_usage(CLAUDE_SAMPLE)
        self.assertEqual(
            list(result["windows"]),
            ["Current session", "Current week (all models)", "Current week (Fable)"],
        )

    def test_session_window_numbers(self):
        window = parse_claude_usage(CLAUDE_SAMPLE)["windows"]["Current session"]
        self.assertEqual(window["used_pct"], 8.0)
        self.assertEqual(window["remaining_pct"], 92.0)
        self.assertEqual(window["resets"], "12:49am (Europe/Oslo)")

    def test_promo_line_is_not_read_as_a_percentage(self):
        # "+50% weekly limits promo" sits between two windows and must not
        # leak into either of them.
        windows = parse_claude_usage(CLAUDE_SAMPLE)["windows"]
        self.assertEqual(windows["Current week (all models)"]["used_pct"], 2.0)
        self.assertEqual(windows["Current week (Fable)"]["used_pct"], 1.0)

    def test_per_model_week_window_is_keyed_by_its_label(self):
        windows = parse_claude_usage(CLAUDE_SAMPLE)["windows"]
        self.assertEqual(windows["Current week (Fable)"]["remaining_pct"], 99.0)

    def test_claude_reports_no_credits(self):
        self.assertIsNone(parse_claude_usage(CLAUDE_SAMPLE)["credits"])

    def test_window_without_a_reset_line_yields_none(self):
        text = "Current session\n  █ 8% used\nCurrent week (all models)\n  █ 2% used\n"
        windows = parse_claude_usage(text)["windows"]
        self.assertIsNone(windows["Current session"]["resets"])
        self.assertIsNone(windows["Current week (all models)"]["resets"])

    def test_header_without_a_percentage_is_dropped(self):
        text = "Current session\nCurrent week (all models)\n  █ 2% used\n"
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(list(windows), ["Current week (all models)"])

    def test_zero_and_hundred_percent(self):
        text = (
            "Current session\n  0% used\n  Resets 1:00am (UTC)\n"
            "Current week (all models)\n  100% used\n  Resets Sep 5 at 11:59pm (UTC)\n"
        )
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(windows["Current session"]["remaining_pct"], 100.0)
        self.assertEqual(windows["Current week (all models)"]["remaining_pct"], 0.0)

    def test_fractional_percentage(self):
        text = "Current session\n  8.5% used\n"
        window = parse_claude_usage(text)["windows"]["Current session"]
        self.assertEqual(window["used_pct"], 8.5)
        self.assertEqual(window["remaining_pct"], 91.5)

    def test_empty_text_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_claude_usage("")

    def test_dialog_not_open_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_claude_usage("❯ \n  ~/Projects/foo  main\n")


class CodexParserTest(unittest.TestCase):
    def test_primary_and_secondary_model_windows(self):
        windows = parse_codex_usage(CODEX_SAMPLE)["windows"]
        self.assertEqual(
            sorted(windows),
            [
                "GPT-5.3-Codex-Spark 5h limit",
                "GPT-5.3-Codex-Spark Weekly limit",
                "Weekly limit",
            ],
        )

    def test_percent_left_is_remaining_not_used(self):
        window = parse_codex_usage(CODEX_SAMPLE)["windows"]["Weekly limit"]
        self.assertEqual(window["remaining_pct"], 87.0)
        self.assertEqual(window["used_pct"], 13.0)

    def test_secondary_block_is_not_attributed_to_the_primary_model(self):
        windows = parse_codex_usage(CODEX_SAMPLE)["windows"]
        # The second "Weekly limit" row is the Spark model's, so the primary
        # weekly number must stay 87, not be overwritten by 100.
        self.assertEqual(windows["Weekly limit"]["remaining_pct"], 87.0)
        self.assertEqual(windows["GPT-5.3-Codex-Spark Weekly limit"]["remaining_pct"], 100.0)

    def test_reset_text_is_kept_verbatim(self):
        windows = parse_codex_usage(CODEX_SAMPLE)["windows"]
        self.assertEqual(windows["Weekly limit"]["resets"], "17:26 on 7 Sep")
        self.assertEqual(windows["GPT-5.3-Codex-Spark 5h limit"]["resets"], "02:19 on 2 Sep")

    def test_last_status_block_wins(self):
        stale = CODEX_SAMPLE.replace("87% left", "40% left")
        text = stale + "some interleaved agent output\n" + CODEX_SAMPLE
        windows = parse_codex_usage(text)["windows"]
        self.assertEqual(windows["Weekly limit"]["remaining_pct"], 87.0)

    def test_block_without_an_account_row_still_parses(self):
        text = "│  Weekly limit: [███] 55% left (resets 17:26 on 7 Sep)  │\n"
        windows = parse_codex_usage(text)["windows"]
        self.assertEqual(windows["Weekly limit"]["remaining_pct"], 55.0)

    def test_row_without_a_bar_still_parses(self):
        text = "Account: a@b (Pro)\nWeekly limit: 12% left (resets 17:26 on 7 Sep)\n"
        windows = parse_codex_usage(text)["windows"]
        self.assertEqual(windows["Weekly limit"]["remaining_pct"], 12.0)

    def test_row_without_a_reset_clause_yields_none(self):
        text = "Account: a@b (Pro)\nWeekly limit: [██] 12% left\n"
        self.assertIsNone(parse_codex_usage(text)["windows"]["Weekly limit"]["resets"])

    def test_zero_and_hundred_percent_left(self):
        text = (
            "Account: a@b (Pro)\n"
            "5h limit: [░░░] 0% left (resets 02:19 on 2 Sep)\n"
            "Weekly limit: [███] 100% left (resets 21:19 on 8 Sep)\n"
        )
        windows = parse_codex_usage(text)["windows"]
        self.assertEqual(windows["5h limit"]["remaining_pct"], 0.0)
        self.assertEqual(windows["5h limit"]["used_pct"], 100.0)
        self.assertEqual(windows["Weekly limit"]["used_pct"], 0.0)

    def test_missing_rows_raise_parse_error(self):
        with self.assertRaises(ParseError):
            parse_codex_usage("Account: a@b (Pro)\nnothing useful here\n")

    def test_empty_text_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_codex_usage("")


class GrokParserTest(unittest.TestCase):
    def test_weekly_limit_is_percent_used(self):
        window = parse_grok_usage(GROK_SAMPLE)["windows"]["Weekly limit"]
        self.assertEqual(window["used_pct"], 0.0)
        self.assertEqual(window["remaining_pct"], 100.0)

    def test_next_reset_is_attached_to_the_weekly_window(self):
        window = parse_grok_usage(GROK_SAMPLE)["windows"]["Weekly limit"]
        self.assertEqual(window["resets"], "September 6, 12:55")

    def test_credits_are_captured_as_a_number(self):
        self.assertEqual(parse_grok_usage(GROK_SAMPLE)["credits"], 16.42)

    def test_last_report_wins(self):
        text = GROK_SAMPLE + "\n" + GROK_SAMPLE.replace("Weekly limit: 0%", "Weekly limit: 73%")
        result = parse_grok_usage(text)
        self.assertEqual(result["windows"]["Weekly limit"]["used_pct"], 73.0)

    def test_reset_of_an_earlier_report_does_not_leak_forward(self):
        first = "Weekly limit: 10%\nNext reset: September 1, 09:00\n"
        second = "Weekly limit: 20%\n"
        window = parse_grok_usage(first + second)["windows"]["Weekly limit"]
        self.assertEqual(window["used_pct"], 20.0)
        self.assertIsNone(window["resets"])

    def test_missing_credits_line_yields_none(self):
        self.assertIsNone(parse_grok_usage("Weekly limit: 5%\n")["credits"])

    def test_hundred_percent_used(self):
        window = parse_grok_usage("Weekly limit: 100%\n")["windows"]["Weekly limit"]
        self.assertEqual(window["remaining_pct"], 0.0)

    def test_fractional_percent_and_credits(self):
        result = parse_grok_usage("Weekly limit: 12.5%\nCredits: $0.01\n")
        self.assertEqual(result["windows"]["Weekly limit"]["remaining_pct"], 87.5)
        self.assertEqual(result["credits"], 0.01)

    def test_missing_weekly_line_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_grok_usage("Session usage: no model calls yet in this session.\n")


class DispatchTest(unittest.TestCase):
    def test_dispatch_picks_the_right_parser(self):
        self.assertEqual(
            parse_usage("codex", CODEX_SAMPLE)["windows"]["Weekly limit"]["remaining_pct"],
            87.0,
        )
        self.assertEqual(parse_usage("grok", GROK_SAMPLE)["credits"], 16.42)
        self.assertIn("Current session", parse_usage("claude", CLAUDE_SAMPLE)["windows"])

    def test_unknown_kind_raises_parse_error_naming_the_supported_kinds(self):
        with self.assertRaises(ParseError) as caught:
            parse_usage("gemini", "whatever")
        self.assertIn("claude", str(caught.exception))


class HeadroomTest(unittest.TestCase):
    def test_headroom_is_the_smallest_remaining_window(self):
        windows = parse_claude_usage(CLAUDE_SAMPLE)["windows"]
        self.assertEqual(headroom_pct(windows), 92.0)

    def test_headroom_across_codex_windows(self):
        windows = parse_codex_usage(CODEX_SAMPLE)["windows"]
        self.assertEqual(headroom_pct(windows), 87.0)

    def test_no_windows_yields_none(self):
        self.assertIsNone(headroom_pct({}))
        self.assertIsNone(headroom_pct(None))


if __name__ == "__main__":
    unittest.main()
