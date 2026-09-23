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

The rule is block-content IDENTITY, not a position and not a count: a branch
must not park an entry block whose content the base does not already carry.
Identity sidesteps diff
attribution, which cannot answer "which entry is the new one" when two blocks
read alike -- given two adjacent `### Added`, git marks the lower as added
though the upper is the new entry. It also lets a deliberate archive repair
through: moving an entry under the heading that published it parks a block the
base already holds, while a genuinely new entry parked under a heading is
content the base has never seen.

Occurrences are matched as a MULTISET, not a set: each parked block consumes
one base occurrence, so a branch that parks a SECOND copy of an entry the base
carries once is a new parked block rather than a member of a set that already
contains it.

Usage:
    check-changelog-placement.py --base <ref> [--changelog CHANGELOG.md]

Exit 0 when the branch parks no block whose content is new to the base. Exit 1
when it parks one. Exit 2 on a usage or tool error (`git` unavailable, base ref unknown, unreadable file).
"""
import argparse
import subprocess
import sys
from collections import Counter
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
    except UnicodeError as exc:
        raise RuntimeError(
            "cannot decode {}:{} as UTF-8 ({}); re-save it as UTF-8".format(
                base, changelog, exc)) from None
    if proc.returncode != 0:
        raise RuntimeError(
            "`git show {}:{}` failed (exit {}): {}. Fetch the base ref "
            "(`git fetch origin <branch>`) or correct --base, and check that "
            "--changelog names a path that exists in it.".format(
                base, changelog, proc.returncode,
                proc.stderr.strip() or "no diagnostic"))
    return proc.stdout


def block_at(lines: list[str], index: int) -> str:
    """The entry block starting at `index`, to the next entry or heading."""
    end = len(lines)
    for i in range(index + 1, len(lines)):
        if lines[i].startswith(ENTRY) or lines[i].startswith(H2):
            end = i
            break
    return "\n".join(lines[index:end]).rstrip()


def blocks(text: str) -> list[str]:
    """Every entry block in `text`, in order."""
    lines = text.splitlines()
    return [block_at(lines, i) for i, ln in enumerate(lines) if ln.startswith(ENTRY)]


def parked(text: str) -> list[str]:
    """The entry blocks sitting under a version heading.

    Empty while the file carries no version heading at all, which is a first
    release rather than a misfiling.
    """
    lines = text.splitlines()
    first_h2 = next((i for i, ln in enumerate(lines) if ln.startswith(H2)), None)
    if first_h2 is None:
        return []
    return [block_at(lines, i) for i, ln in enumerate(lines)
            if i > first_h2 and ln.startswith(ENTRY)]


def newly_parked(original: str, text: str) -> list[str]:
    """Parked blocks whose content is new to `original`.

    Identity, not a count. A net-count rule is satisfied by a branch that adds
    a misfiled block while moving or dropping another parked one, and the
    misfiling goes through. A block whose text already exists on the base is a
    move — filing a past entry under the version that published it, say — and
    a block that does not is new content parked where the stamp cannot reach.

    Occurrences are consumed, not merely looked up: a set answers "is this text
    anywhere on the base", so a branch parking two identical copies of a
    one-occurrence block passes twice on the same evidence.
    """
    known = Counter(blocks(original))
    new_blocks = []
    for block in parked(text):
        if known[block]:
            # One parked copy answers one base occurrence. A SECOND copy of the
            # same entry has no occurrence left to answer, and is new content.
            known[block] -= 1
        else:
            new_blocks.append(block)
    return new_blocks


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
        print("error: cannot read {}: {}. Correct --changelog, or restore the "
              "file and its read permission.".format(path, exc), file=sys.stderr)
        return 2
    except UnicodeError as exc:
        # Not an OSError. Letting it escape exits 1 — the misfiling verdict —
        # so a file this tool cannot decode would read as a finding.
        print("error: cannot decode {} as UTF-8: {}. Re-save it as UTF-8, or "
              "point --changelog at the file that is.".format(path, exc), file=sys.stderr)
        return 2
    try:
        original = base_text(args.base, args.changelog)
    except RuntimeError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    new_parked = newly_parked(original, text)
    if not new_parked:
        return 0
    print(
        "error: this branch parks {} entry block(s) under an already-published "
        "version heading whose content is new to {}. The publish step stamps "
        "only what sits ABOVE the first `## ` heading, so an entry below one "
        "ships filed under a version that already shipped:".format(
            len(new_parked), args.base), file=sys.stderr)
    for block in new_parked:
        print("  {}".format(block.splitlines()[0]), file=sys.stderr)
    print(
        "Rebase onto {} and move the new block above the topmost `## ` heading. "
        "Moving an entry the base already carries between headings is fine: it "
        "parks no content the base has not seen.".format(args.base), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
