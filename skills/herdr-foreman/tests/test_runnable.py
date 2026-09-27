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


def string_literals(source):
    """Every directive string literal in one module: (line, text), docstrings and help text excluded."""
    tree = ast.parse(source)
    skipped = _docstrings(tree) | _help_strings(tree)
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped]


def bare_hints(text, names):
    """The subcommand references in `text` that do not run as written."""
    alternation = "|".join(re.escape(name) for name in names)
    end = r"(?![\w-])"
    patterns = (
        r"`(?:foreman )?(?:{}){}".format(alternation, end),
        r"\bforeman (?:{}){}".format(alternation, end),
        r"\b(?:[Rr]un|[Rr]e-?run|[Uu]se|with|[Ii]nspect|save|[Vv]alidate) (?:{}){}".format(alternation, end),
    )
    return [match.group(0) for pattern in patterns for match in re.finditer(pattern, text)]


class BareHintTest(unittest.TestCase):
    def test_the_detector_flags_each_bare_shape_and_passes_the_rendered_one(self):
        names = ["measure", "supervision-bind", "state"]
        self.assertEqual(bare_hints("run `foreman measure` first", names), ["`foreman measure", "foreman measure"])
        self.assertEqual(bare_hints("run supervision-bind from the pane", names), ["run supervision-bind"])
        self.assertEqual(bare_hints("inspect foreman state", names), ["foreman state"])
        self.assertEqual(bare_hints("re-run `measure`", names), ["`measure"])
        self.assertEqual(bare_hints("run `{}` first", names), [])
        self.assertEqual(bare_hints("the foreman's state file", names), [])

    def test_no_package_string_names_a_subcommand_that_does_not_run_as_written(self):
        names = subcommands()
        self.assertIn("measure", names)
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            for line, text in string_literals(path.read_text(encoding="utf-8")):
                for hint in bare_hints(text, names):
                    offenders.append("{}:{}: {}".format(path.name, line, hint))
        self.assertEqual(offenders, [], "render these through foreman.runnable.command()")


class CommandTest(unittest.TestCase):
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
