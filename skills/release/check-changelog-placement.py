#!/usr/bin/env python3
"""Refuse a CHANGELOG entry that a publish would file under the wrong version.

`stamp-changelog.py` stamps the topmost UN-HEADED `### ` block with the version
being published, and is a documented no-op when the file's first `## ` heading
already sits above the first `### `. Both halves are correct on their own. The
hazard is between them: a branch cut before an intervening publish carries its
`### ` block at what WAS the top, and the three-way merge lands that block
BELOW a version heading added since. The entry is then already headed, the
stamp finds nothing to do, and the work ships filed under someone else's
version — silently, because every step behaved as designed
(jbaruch/coding-policy#452, where #384's entry published as 0.3.238 and landed
under `## 0.3.236`).

This check runs on the pull request, where the fix is still a rebase. It reads
the entries the branch ADDS and requires each to sit above the first version
heading, which is the one place the stamp can reach.

Usage:
    check-changelog-placement.py --base <ref> [--changelog CHANGELOG.md]

Exit 0 when every added entry is stampable, or when the branch adds none.
Exit 1 when an added entry sits under a version heading. Exit 2 on a usage or
tool error (`git` unavailable, base ref unknown, unreadable file).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

H2 = "## "
ENTRY = "### "
HUNK = re.compile(r"^@@ -\\d+(?:,\\d+)? \\+(\\d+)(?:,\\d+)? @@")


def added_entry_lines(base: str, changelog: str) -> list[int]:
    """The NEW-file line numbers of `### ` lines this branch adds.

    Line numbers, never the line text: `### Added` heads many blocks, so
    matching by content would flag a published entry that happens to read the
    same as the new one.
    """
    proc = subprocess.run(
        ["git", "diff", "--unified=0", f"{base}...HEAD", "--", changelog],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "`git diff {}...HEAD -- {}` failed (exit {}): {}".format(
                base, changelog, proc.returncode,
                proc.stderr.strip() or "no diagnostic"))
    numbers: list[int] = []
    cursor = 0
    for line in proc.stdout.splitlines():
        header = HUNK.match(line)
        if header:
            cursor = int(header.group(1))
            continue
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            if line[1:].startswith(ENTRY):
                numbers.append(cursor)
            cursor += 1
        elif not line.startswith("-"):
            cursor += 1
    return numbers


def misfiled(text: str, added: list[int]) -> list[str]:
    """The added entries at or below the first version heading, as text."""
    lines = text.splitlines()
    first_h2 = next((i for i, ln in enumerate(lines) if ln.startswith(H2)), None)
    if first_h2 is None:
        return []
    return [lines[n - 1] for n in added
            if 0 < n <= len(lines) and n - 1 > first_h2]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True,
                        help="the ref this branch is measured against, e.g. origin/main")
    parser.add_argument("--changelog", default="CHANGELOG.md")
    args = parser.parse_args(argv)

    path = Path(args.changelog)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print("error: cannot read {}: {}".format(path, exc), file=sys.stderr)
        return 2
    try:
        added = added_entry_lines(args.base, args.changelog)
    except RuntimeError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    if not added:
        return 0
    bad = misfiled(text, added)
    if not bad:
        return 0
    print(
        "error: {} entry line(s) this branch adds sit under a version heading, "
        "where the publish step cannot stamp them — they would ship filed under "
        "an already-published version:".format(len(bad)), file=sys.stderr)
    for line in bad:
        print("  {}".format(line), file=sys.stderr)
    print(
        "Rebase onto {} and move the block above the topmost `## ` heading, so "
        "the stamp step files it under the version this merge publishes.".format(args.base),
        file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
