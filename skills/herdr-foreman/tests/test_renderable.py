"""The shared renderable-text check every brief path and report marker uses (#578)."""

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from foreman import renderable

#: One character from each category the check refuses, plus the C1 controls
#: and separators #568 and #578 named.
REFUSED = ("\x00", "\n", "\t", "\x1b", "\x7f", "\x85", "\x9b", "\u2028", "\u2029",
           "\u202e", "\u200b", "\ue000", "\U0010ffff", "\ud800")


def run_command(stdin, *args):
    env = dict(os.environ, PYTHONPATH=str(SKILL_DIR))
    return subprocess.run([sys.executable, "-m", "foreman.renderable", *args], input=stdin,
                          capture_output=True, text=True, env=env, check=False)


class RenderableTests(unittest.TestCase):
    def test_every_unrenderable_character_is_refused(self):
        for char in REFUSED:
            with self.subTest(char=hex(ord(char))):
                self.assertFalse(renderable.renderable("/r/a" + char + "b.md"))
                self.assertEqual(renderable.offenders("/r/a" + char + "b.md"), [char])

    def test_ordinary_paths_pass_including_non_ascii_letters_and_spaces(self):
        for value in ("/Users/me/.local/state/r.md", "/r/Ünïcødé path/報告.md", "src/**/*.py", ""):
            with self.subTest(value=value):
                self.assertTrue(renderable.renderable(value))

    def test_a_backtick_is_refused_only_inside_a_code_span(self):
        self.assertTrue(renderable.renderable("src/`x`"))
        self.assertFalse(renderable.renderable("src/`x`", code_span=True))

    def test_a_value_that_is_not_text_is_not_renderable(self):
        for value in (None, 7, ["/r.md"], {"p": "/r.md"}):
            with self.subTest(value=value):
                self.assertFalse(renderable.renderable(value))

    def test_command_accepts_a_renderable_string(self):
        result = run_command(json.dumps("/r/report.md"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"renderable": True, "offenders": []})

    def test_command_names_each_offender_across_an_array(self):
        result = run_command(json.dumps(["/r/a\u2028.md", "/r/b\x85.md", "/r/c.md"]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"renderable": False, "offenders": ["\x85", "\u2028"]})

    def test_command_reads_a_nul_from_json_escapes(self):
        result = run_command('"/r/a\\u0000b.md"')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["offenders"], ["\x00"])

    def test_command_code_span_flag_refuses_a_backtick(self):
        self.assertEqual(run_command(json.dumps(["src/`x`"])).returncode, 0)
        result = run_command(json.dumps(["src/`x`"]), "--code-span")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["offenders"], ["`"])

    def test_command_refuses_a_value_that_is_not_text(self):
        for payload in ("null", "7", '["/r.md", 3]', '{"p": "/r.md"}'):
            with self.subTest(payload=payload):
                result = run_command(payload)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout), {"renderable": False, "offenders": []})

    def test_command_usage_and_malformed_input_are_exit_two(self):
        for stdin, args in (("{", ()), ('"/r.md"', ("--bogus",)), ('"/r.md"', ("--code-span", "--code-span"))):
            with self.subTest(stdin=stdin, args=args):
                result = run_command(stdin, *args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("renderable:", result.stderr)

    def test_main_is_callable_in_process(self):
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(renderable.main([], io.StringIO('"/r\u2029.md"'), out, err), 1)
        self.assertEqual(json.loads(out.getvalue())["offenders"], ["\u2029"])


if __name__ == "__main__":
    unittest.main()
