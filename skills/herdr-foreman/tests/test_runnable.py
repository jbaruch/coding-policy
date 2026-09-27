"""Every hint that tells the reader to run a subcommand names the runnable launcher (#532)."""

import argparse
import ast
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import runnable
from foreman.cli import build_parser

PACKAGE = Path(__file__).resolve().parents[1] / "foreman"
#: Verbs that put a one-word subcommand in a command position.
IMPERATIVE_VERBS = "|".join(("[Rr]un", "[Rr]e-?run", "[Rr]etry", "[Rr]eload", "[Uu]se", "with", "[Ii]nspect",
                             "save", "[Vv]alidate", "[Ff]inish", "through"))


def subcommands():
    parser = build_parser()
    action = next(item for item in parser._actions if isinstance(item, argparse._SubParsersAction))
    return sorted(action.choices, key=len, reverse=True)


def _docstrings(tree):
    """The string nodes that are docstrings, which describe rather than direct."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def _help_strings(tree):
    """The string nodes inside argparse `help=` values, read from inside the CLI they document."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "help":
            found.update(id(inner) for inner in ast.walk(node.value))
    return found


#: Calls whose string arguments name a subcommand as data, never as a hint to the reader.
NAMING_CALLS = frozenset({"runnable.command", "command", "sub.add_parser"})


def _named_as_data(tree, names):
    """String nodes that are subcommand names as data: a rendering argument, or a bare name (a dispatch key)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func) in NAMING_CALLS:
            found.update(id(inner) for inner in ast.walk(node))
        elif isinstance(node, ast.Constant) and node.value in names:
            found.add(id(node))
    return found


def string_literals(source, names=()):
    """Every directive string literal in one module: (line, text).

    Docstrings, argparse help text and subcommand names used as data are excluded.
    """
    tree = ast.parse(source)
    skipped = _docstrings(tree) | _help_strings(tree) | _named_as_data(tree, set(names))
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped]


def bare_hints(text, names):
    """The subcommand references in `text` that do not run as written.

    A hyphenated name is never English, so any bare occurrence is a reference;
    only a message opening with its own command's name (`start-judge requires
    ...`) and the resume template's `{tl}` launcher placeholder are rendered
    forms. A one-word name (`plan`, `state`) counts only in a command position:
    backticked, after `foreman `, or after a verb that tells the reader to run it.
    """
    alternation = "|".join(re.escape(name) for name in names)
    hyphenated = "|".join(re.escape(name) for name in names if "-" in name)
    end = r"(?![\w-])"
    patterns = [
        r"`(?:foreman )?(?:{}){}".format(alternation, end),
        r"\bforeman (?:{}){}".format(alternation, end),
        r"\b(?:{}) (?:{}){}".format(IMPERATIVE_VERBS, alternation, end),
    ]
    if hyphenated:
        patterns.append(r"(?<!^)(?<![\w./`-])(?<!\{{tl\}} )(?<!foreman )(?:{})(?![\w/-]|\.\w)".format(hyphenated))
    found = []
    for pattern in patterns:
        found.extend(match.group(0) for match in re.finditer(pattern, text) if match.group(0) not in found)
    return found


class BareHintTest(unittest.TestCase):
    def test_the_detector_flags_each_bare_shape_and_passes_the_rendered_one(self):
        names = ["measure", "supervision-bind", "state"]
        self.assertEqual(bare_hints("run `foreman measure` first", names), ["`foreman measure", "foreman measure"])
        self.assertEqual(bare_hints("run supervision-bind from the pane", names),
                         ["run supervision-bind", "supervision-bind"])
        self.assertEqual(bare_hints("inspect foreman state", names), ["foreman state"])
        self.assertEqual(bare_hints("re-run `measure`", names), ["`measure"])
        self.assertEqual(bare_hints("run `{}` first", names), [])
        self.assertEqual(bare_hints("the foreman's state file", names), [])
        self.assertEqual(bare_hints("Retry state for the owner", names), ["Retry state"])
        self.assertEqual(bare_hints("the handoff needs supervision-bind first", names),
                         ["supervision-bind"])
        self.assertEqual(bare_hints("supervision-bind requires a pane", names), [])
        self.assertEqual(bare_hints("run `{tl} supervision-bind {flags}`", names), [])
        self.assertEqual(bare_hints("the .supervision-bind.json lock", names), [])
        self.assertEqual(bare_hints("the owner needs supervision-bind.", names), ["supervision-bind"])

    def test_no_package_string_names_a_subcommand_that_does_not_run_as_written(self):
        names = subcommands()
        self.assertIn("measure", names)
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            for line, text in string_literals(path.read_text(encoding="utf-8"), names):
                for hint in bare_hints(text, names):
                    offenders.append("{}:{}: {}".format(path.name, line, hint))
        self.assertEqual(offenders, [], "render these through foreman.runnable.command()")


class CommandTest(unittest.TestCase):
    def test_a_task_identity_in_a_hint_is_one_shell_word(self):
        from foreman.cli import _dispatched_seat_briefs
        from foreman.errors import UsageError
        with self.assertRaises(UsageError) as caught:
            _dispatched_seat_briefs({"task_context": {"task": "other"}}, {}, [], "repo task 322; rm -rf x")
        found = re.search(r"`(bash [^`]+)`", caught.exception.message)
        self.assertIsNotNone(found, caught.exception.message)
        assert found is not None  # narrows the Optional for pyright; the assertion above reports the message
        self.assertEqual(shlex.split(found.group(1))[-2:], ["--task", "repo task 322; rm -rf x"])

    def test_command_names_the_quoted_launcher_beside_the_package(self):
        with patch.object(runnable, "launcher", return_value="/opt/my plugins/foreman.sh"):
            self.assertEqual(runnable.command("measure --state F"),
                             "bash '/opt/my plugins/foreman.sh' measure --state F")

    def test_the_launcher_is_the_skill_script(self):
        self.assertEqual(Path(runnable.launcher()), PACKAGE.parent / "foreman.sh")
        self.assertTrue(Path(runnable.launcher()).is_file())

    def test_a_rendered_command_runs_as_written(self):
        with tempfile.TemporaryDirectory(prefix="foreman-runnable-") as root:
            env = {key: value for key, value in os.environ.items() if not key.startswith(("HERDR", "FOREMAN"))}
            env.update(HOME=root, XDG_STATE_HOME=root, XDG_CONFIG_HOME=root)
            for tail in ("measure --help", "supervision-bind --help", "close-task --help"):
                with self.subTest(tail=tail):
                    result = subprocess.run(shlex.split(runnable.command(tail)), capture_output=True, text=True,
                                            env=env, cwd=root, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage: foreman " + tail.split()[0], result.stdout)


if __name__ == "__main__":
    unittest.main()
