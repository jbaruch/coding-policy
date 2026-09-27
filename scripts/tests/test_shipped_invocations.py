#!/usr/bin/env python3
"""Shipped command blocks invoke scripts through an explicit interpreter.

tessl packaging normalizes plugin files to mode 0644, so a command block that
names a script by bare path (`skills/release/foo.sh ...`) works from a clone,
where git keeps 0755, and fails with `permission denied` from an installed
plugin. #485 prefixed every such block it found; `skills/release/PUBLICATION.md`
kept six bare invocations it did not reach (#487). This suite scans every
fenced shell block under the shipped `skills/` and `hooks/` trees so the next
one is caught before it ships.

Each logical fenced line (backslash continuations joined) is tokenized with
`shlex` in POSIX mode, so quotes, escapes and operators are the shell's, not a
regex's. A command position is the first token, or the one after an operator,
a backtick, or the `(` of `$(`. Leading `NAME=value` assignments, redirections
with their targets, the keywords and braces in `KEYWORDS`, and the wrappers in
`WRAPPERS` with their assignments, their options and the values those options
take are skipped. A token there ending in
`.sh` or `.py` is a bare invocation. A line `shlex` cannot tokenize is
reported, never skipped.
"""

import re
import shlex
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

#: Trees that ship to consumers and carry agent-facing command blocks.
SHIPPED = ("skills", "hooks")

#: Fence info strings whose body is shell. An unlabeled fence counts too.
SHELL_FENCES = frozenset({"", "bash", "sh", "shell", "zsh", "console"})

FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)")
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
SCRIPT = re.compile(r"\.(sh|py)$")

#: `shlex` groups a run of these into one operator token. A run holding `<` or
#: `>` is a redirection, whose next token is its target; any other run ends a
#: command, so the next word is in command position.
OPERATOR_CHARS = frozenset("();<>|&")

#: Words that precede a command without being one.
KEYWORDS = frozenset({"if", "then", "do", "else", "elif", "while", "until", "!", "{", "}"})

#: Commands that run their first non-option, non-assignment operand, each
#: mapped to its options that consume the following token as their value.
WRAPPERS = {
    "env": frozenset({"-u", "-C", "-S", "-P"}),
    "sudo": frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "-R"}),
    "exec": frozenset({"-a"}),
    "command": frozenset(),
    "time": frozenset({"-f", "-o"}),
    "nohup": frozenset(),
    "nice": frozenset({"-n"}),
}


def shell_blocks(text):
    """Yield (line_number, line) for every logical line inside a shell fence."""
    fence = None
    pending = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = FENCE.match(line)
        if fence is None:
            if match:
                fence = (match.group(1), match.group(2).lower() in SHELL_FENCES)
            continue
        if match and match.group(1) == fence[0] and not match.group(2):
            if pending is not None:
                yield pending
            fence, pending = None, None
            continue
        if not fence[1]:
            continue
        pending = (number, line) if pending is None else (pending[0], pending[1] + " " + line)
        if pending[1].endswith("\\"):
            pending = (pending[0], pending[1][:-1])
            continue
        yield pending
        pending = None


def tokens(line):
    """POSIX shell tokens, with each backtick split off as its own token."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    out = []
    for token in lexer:
        for index, part in enumerate(token.split("`")):
            if index:
                out.append("`")
            if part:
                out.append(part)
    return out


def bare_invocations(line):
    """Words in command position that name a script with no interpreter.

    Raises ValueError when the line does not tokenize (an unbalanced quote).
    """
    words = tokens(line)
    found = []
    expecting = True
    wrapper = None
    index = 0
    while index < len(words):
        token = words[index]
        index += 1
        if token == "`":
            expecting, wrapper = True, None
            continue
        if set(token) <= OPERATOR_CHARS:
            if "<" in token or ">" in token:
                index += 1
            else:
                expecting, wrapper = True, None
            continue
        if token.isdigit() and index < len(words) and words[index][0] in "<>":
            continue
        if not expecting:
            continue
        if ASSIGNMENT.match(token) or token in KEYWORDS:
            continue
        if wrapper is not None and token.startswith("-"):
            if token in WRAPPERS[wrapper]:
                index += 1
            continue
        if token in WRAPPERS:
            wrapper = token
            continue
        if SCRIPT.search(token):
            found.append(token)
        expecting, wrapper = False, None
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
            ("out=$(skills/x/run.sh)", ["skills/x/run.sh"]),
            ("out=$(true);skills/x/run.sh", ["skills/x/run.sh"]),
            ("out=`skills/x/run.sh`", ["skills/x/run.sh"]),
            ("echo done & skills/x/run.sh", ["skills/x/run.sh"]),
            ("(skills/x/run.sh)", ["skills/x/run.sh"]),
            ("{ skills/x/run.sh; }", ["skills/x/run.sh"]),
            ('"skills/x/run.sh" --flag', ["skills/x/run.sh"]),
            ('X="two words" skills/x/run.sh', ["skills/x/run.sh"]),
            ("Y='a b' Z=c skills/x/run.sh", ["skills/x/run.sh"]),
            ("X=a\\ b skills/x/run.sh", ["skills/x/run.sh"]),
            ("env MODE=1 skills/x/run.sh", ["skills/x/run.sh"]),
            ("sudo -E skills/x/run.sh", ["skills/x/run.sh"]),
            ("sudo -u runner skills/x/run.sh", ["skills/x/run.sh"]),
            ("env -u NAME skills/x/run.sh", ["skills/x/run.sh"]),
            ("nice -n 10 skills/x/run.sh", ["skills/x/run.sh"]),
            (">out skills/x/run.sh", ["skills/x/run.sh"]),
            ("2>/dev/null skills/x/run.sh", ["skills/x/run.sh"]),
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
            'echo "(skills/x/run.sh)"',
            'echo "literal; skills/x/run.sh"',
            "bash a.sh > skills/x/out.sh",
            "bash a.sh 2>&1 | tee log",
            "env MODE=1 bash skills/x/run.sh",
            "echo 'cost$' skills/x/run.sh",
            "sudo -u runner bash skills/x/run.sh",
        ):
            with self.subTest(line=line):
                self.assertEqual(bare_invocations(line), [])

    def test_refuses_an_untokenizable_line(self):
        with self.assertRaises(ValueError):
            bare_invocations('echo "unterminated')

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

    def test_joins_continued_lines(self):
        text = "\n".join(("```bash", "bash a.sh \\", "  --flag | skills/x/run.sh", "```"))
        blocks = list(shell_blocks(text))
        self.assertEqual([number for number, _ in blocks], [2])
        self.assertEqual(bare_invocations(blocks[0][1]), ["skills/x/run.sh"])


class ShippedInvocationTest(unittest.TestCase):
    """Every shipped command block names its interpreter."""

    def test_no_bare_script_invocations(self):
        offenders = []
        for path in shipped_markdown():
            for number, line in shell_blocks(path.read_text(encoding="utf-8")):
                where = "{}:{}".format(path.relative_to(ROOT), number)
                try:
                    words = bare_invocations(line)
                except ValueError as error:
                    offenders.append("{}: cannot tokenize ({}); balance its quotes".format(where, error))
                    continue
                offenders.extend("{}: {}".format(where, word) for word in words)
        self.assertEqual(offenders, [], "prefix each invocation with `bash ` or `python3 ` — "
                         "an installed plugin's scripts are mode 0644")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
