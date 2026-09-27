#!/usr/bin/env python3
"""Shipped command blocks invoke scripts through an explicit interpreter.

tessl packaging normalizes plugin files to mode 0644, so a command block that
names a script by bare path (`skills/release/foo.sh ...`) works from a clone,
where git keeps 0755, and fails with `permission denied` from an installed
plugin. #485 prefixed every such block it found; `skills/release/PUBLICATION.md`
kept six bare invocations it did not reach (#487). This suite scans every
fenced shell block under the shipped `skills/` and `hooks/` trees so the next
one is caught before it ships.

A command position is the start of a line, or what follows `$(`, a backtick,
`|`, `||`, `&&` or `;`, after leading `NAME=value` assignments and the shell
keywords that precede a command. A word there ending in `.sh` or `.py` is a
bare invocation.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

#: Trees that ship to consumers and carry agent-facing command blocks.
SHIPPED = ("skills", "hooks")

#: Fence info strings whose body is shell. An unlabeled fence counts too.
SHELL_FENCES = frozenset({"", "bash", "sh", "shell", "zsh", "console"})

FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)")
SEPARATOR = re.compile(r"\$\(|`|\|\||&&|\||;")
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=\S*\s*")
KEYWORDS = frozenset({"if", "then", "do", "else", "elif", "while", "until", "!", "exec", "command", "time"})
SCRIPT = re.compile(r"\.(sh|py)$")


def shell_blocks(text):
    """Yield (line_number, line) for every line inside a shell fence."""
    fence = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = FENCE.match(line)
        if fence is None:
            if match:
                fence = (match.group(1), match.group(2).lower() in SHELL_FENCES)
            continue
        if match and match.group(1) == fence[0] and not match.group(2):
            fence = None
            continue
        if fence[1]:
            yield number, line


def bare_invocations(line):
    """Words in command position that name a script with no interpreter."""
    found = []
    for segment in SEPARATOR.split(line):
        rest = segment.strip()
        while True:
            assignment = ASSIGNMENT.match(rest)
            if assignment:
                rest = rest[assignment.end():]
                continue
            words = rest.split(None, 1)
            if words and words[0] in KEYWORDS:
                rest = words[1] if len(words) > 1 else ""
                continue
            break
        words = rest.split(None, 1)
        if not words:
            continue
        word = words[0].strip("\"'")
        if SCRIPT.search(word):
            found.append(word)
    return found


def shipped_markdown():
    for tree in SHIPPED:
        yield from sorted((ROOT / tree).rglob("*.md"))


class BareInvocationDetectorTest(unittest.TestCase):
    """The detector flags command positions and nothing else."""

    def test_flags_bare_paths_in_command_position(self):
        for line, expected in (
            ("skills/release/verify-github-release.sh o r t 1", ["skills/release/verify-github-release.sh"]),
            ("PRE=$(skills/release/registry-baseline.sh w p) || exit", ["skills/release/registry-baseline.sh"]),
            ('out=$("$CP/skills/x/run.py" --flag)', ["$CP/skills/x/run.py"]),
            ("git status && ./check.sh", ["./check.sh"]),
            ("if skills/a/b.sh; then echo ok; fi", ["skills/a/b.sh"]),
            ("FOO=1 skills/a/b.sh", ["skills/a/b.sh"]),
        ):
            with self.subTest(line=line):
                self.assertEqual(bare_invocations(line), expected)

    def test_accepts_interpreter_prefixed_and_argument_paths(self):
        for line in (
            "bash skills/release/verify-github-release.sh o r t 1",
            "PRE=$(bash skills/release/registry-baseline.sh w p) || exit",
            'python3 "$CP/skills/x/run.py" --flag',
            "cat skills/a/b.sh | head",
            "bash -n skills/a/b.sh",
            "echo done",
        ):
            with self.subTest(line=line):
                self.assertEqual(bare_invocations(line), [])

    def test_reads_only_shell_fences(self):
        text = "\n".join((
            "skills/prose/mention.sh outside a fence",
            "```json",
            "skills/in/json.sh",
            "```",
            "```bash",
            "skills/in/bash.sh",
            "```",
            "```",
            "skills/in/plain.sh",
            "```",
        ))
        self.assertEqual([line for _, line in shell_blocks(text)], ["skills/in/bash.sh", "skills/in/plain.sh"])


class ShippedInvocationTest(unittest.TestCase):
    """Every shipped command block names its interpreter."""

    def test_no_bare_script_invocations(self):
        offenders = []
        for path in shipped_markdown():
            for number, line in shell_blocks(path.read_text(encoding="utf-8")):
                for word in bare_invocations(line):
                    offenders.append("{}:{}: {}".format(path.relative_to(ROOT), number, word))
        self.assertEqual(offenders, [], "prefix each invocation with `bash ` or `python3 ` — "
                         "an installed plugin's scripts are mode 0644")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
