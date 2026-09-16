#!/usr/bin/env python3
"""Refuse a CHANGELOG entry that a publish would file under the wrong version.

`stamp-changelog.py` stamps the topmost UN-HEADED `### ` block with the version
being published, and is a documented no-op when the file's first `## ` heading
already sits above the first `### `. Both halves are correct on their own. The
hazard is between them: a branch cut before an intervening publish carries its
`### ` block at what WAS the top, and the three-way merge lands that block
BELOW a version heading added since. The entry is then already headed, the
stamp finds nothing to do, and the work ships filed under someone else's
version -- silently, because every step behaved as designed
(jbaruch/coding-policy#452, where #384's entry published as 0.3.238 and landed
under `## 0.3.236`).

The rule is a COUNT, not a position: a branch must not increase the number of
entry blocks parked under already-published headings. Counting sidesteps diff
attribution, which cannot answer "which entry is the new one" when two blocks
read alike -- given two adjacent `### Added`, git marks the lower as added
though the upper is the new entry. It also lets a deliberate archive repair
through: moving an entry under the heading that published it leaves the count
unchanged, while a new entry parked under a heading raises it.

Usage:
    check-changelog-placement.py --base <ref> [--changelog CHANGELOG.md]

Exit 0 when the count did not rise. Exit 1 when it did. Exit 2 on a usage or
tool error (`git` unavailable, base ref unknown, unreadable file).
"""
import argparse
import subprocess
import sys
from pathlib import Path

H2 = "## "
ENTRY = "### "


def base_text(base: str, changelog: str) -> str:
    """The changelog as it stands on `base`."""
    try:
        proc = subprocess.run(["git", "show", f"{base}:{changelog}"],
                              capture_output=True, text=True)
    except OSError as exc:
        # An absent or unrunnable `git` is the absence of an answer, never the
        # misfiling verdict. Letting it raise exits 1, which is that verdict.
        raise RuntimeError(
            "cannot run `git` ({}); install it, or run this check from an "
            "environment where it is on PATH".format(exc)) from None
    if proc.returncode != 0:
        raise RuntimeError(
            "`git show {}:{}` failed (exit {}): {}".format(
                base, changelog, proc.returncode,
                proc.stderr.strip() or "no diagnostic"))
    return proc.stdout


def parked(text: str) -> int:
    """How many entry blocks sit under a version heading.

    Zero while the file carries no version heading at all, which is a first
    release rather than a misfiling.
    """
    lines = text.splitlines()
    first_h2 = next((i for i, ln in enumerate(lines) if ln.startswith(H2)), None)
    if first_h2 is None:
        return 0
    return sum(1 for ln in lines[first_h2:] if ln.startswith(ENTRY))


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
        original = base_text(args.base, args.changelog)
    except RuntimeError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    before, after = parked(original), parked(text)
    if after <= before:
        return 0
    print(
        "error: this branch parks {} more entry block(s) under an "
        "already-published version heading ({} -> {}). The publish step stamps "
        "only what sits ABOVE the first `## ` heading, so an entry below one "
        "ships filed under a version that already shipped.".format(
            after - before, before, after), file=sys.stderr)
    print(
        "Rebase onto {} and move the new block above the topmost `## ` heading. "
        "Moving an existing entry between headings is fine and does not raise "
        "the count.".format(args.base), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
