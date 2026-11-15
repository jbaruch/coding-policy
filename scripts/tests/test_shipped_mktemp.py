#!/usr/bin/env python3
"""Every mktemp call in a shipped script names a template under ${TMPDIR:-/tmp}.

macOS mktemp ignores TMPDIR when called bare, with -d alone, or with -t, and
writes under /var/folders/.../T. A sandbox that allows writes only inside the
project refuses that path, and the hooks that catch the failure then skip
their check. GNU mktemp honors TMPDIR in every form, so CI on Linux cannot
reproduce the failure; this source check is the gate.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

#: A command-substituted mktemp call and its argument text.
CALL = re.compile(r"\$\(\s*mktemp\b([^)]*)\)")

#: The only accepted argument shape: optional -d, the TMPDIR template, and an
#: optional stderr redirect into a variable-named file.
TEMPLATE = re.compile(
    r'^\s*(-d\s+)?"\$\{TMPDIR:-/tmp\}/[A-Za-z0-9._-]+\.XXXXXX"(\s+2>"\$[A-Z_]+")?\s*$'
)


def offending_calls(text):
    """mktemp argument strings in `text` that lack the TMPDIR template."""
    bad = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for match in CALL.finditer(line):
            if not TEMPLATE.match(match.group(1)):
                bad.append((number, match.group(0)))
    return bad


def shipped_scripts():
    """Tracked shell scripts under skills/, excluding test trees."""
    listing = subprocess.run(["git", "ls-files", "skills"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
    return sorted(path for path in listing.stdout.split()
                  if path.endswith(".sh") and "/tests/" not in path)


class Matcher(unittest.TestCase):
    """The matcher flags each form macOS mishandles and accepts the template."""

    def test_flags_forms_that_ignore_tmpdir(self):
        fixture = "\n".join([
            'a=$(mktemp)',
            'b="$(mktemp -d)"',
            'c=$(mktemp -t name.XXXXXX)',
            'd=$(mktemp /tmp/fixed.XXXXXX)',
        ])
        self.assertEqual([n for n, _ in offending_calls(fixture)], [1, 2, 3, 4])

    def test_accepts_the_tmpdir_template(self):
        fixture = "\n".join([
            'a=$(mktemp "${TMPDIR:-/tmp}/name.XXXXXX")',
            'b="$(mktemp -d "${TMPDIR:-/tmp}/name.XXXXXX")"',
            'c="$(mktemp "${TMPDIR:-/tmp}/name.XXXXXX" 2>"$ERRFILE")"',
            '# x=$(mktemp) is only a comment',
        ])
        self.assertEqual(offending_calls(fixture), [])


class ShippedScripts(unittest.TestCase):
    def test_every_shipped_mktemp_call_names_a_tmpdir_template(self):
        scripts = shipped_scripts()
        self.assertTrue(scripts, "found no shipped scripts under skills/")
        calls, bad = 0, []
        for rel in scripts:
            text = (ROOT / rel).read_text()
            calls += len(CALL.findall(text))
            bad += [f"{rel}:{n}: {call}" for n, call in offending_calls(text)]
        self.assertGreater(calls, 0, "found no mktemp calls; the matcher is not seeing them")
        self.assertEqual(bad, [], "shipped mktemp calls without a \"${TMPDIR:-/tmp}/name.XXXXXX\" template")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
