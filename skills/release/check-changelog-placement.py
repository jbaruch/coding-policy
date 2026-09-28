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
under `## 0.3.236`; #581, where 0.3.291 published with no heading at all).

Decision contract
-----------------
The unit is the ENTRY ITEM, not the `### ` block: a top-level bullet (a
column-0 `- ` line), or a column-0 paragraph that follows a blank line or a
heading, running to the next item or heading. `## ` and `### ` lines are
structure, never content. A PARKED item is one below the file's first `## `
heading; a file with no `## ` heading yet parks nothing (a first release).

An item parked on the branch is accepted when one of these holds, tried in
order, each consuming what it matches so no evidence answers twice:

1. MOVE -- its exact text is an item the base carries anywhere in the file.
   Occurrences are matched as a MULTISET: a SECOND copy of an item the base
   carries once has no occurrence left to answer, and is new content.
   A heading-only repair -- inserting a missing `## <version> — <date>` plus
   its `### <type>` lines above items the base already holds -- changes only
   structure, so every item it touches is a move.
2. IN-PLACE EDIT -- the base has a parked item under the SAME `## ` heading
   (matched by heading text) that the branch no longer carries anywhere. The
   edited item pairs with it one for one. Rewording a published entry is
   this case. An edit and a move of the same item in one change is not: land
   them separately.

Every other parked item is NEW content parked where the stamp cannot reach,
and the check refuses it. A merge only ever adds, so the misfiling this check
exists for never pairs with a removed base item.

Modes
-----
    check-changelog-placement.py --base <ref> [--changelog CHANGELOG.md]
        Measure the working-tree changelog against <ref> (a PR branch against
        origin/main, or a push against the commit it landed on).

    check-changelog-placement.py --push-before <sha> [--changelog CHANGELOG.md]
        Measure a publish push against the commit it landed on, as the stamp
        step does before publishing. Nothing to measure is a pass with a
        stderr notice, not an error: an empty or all-zero <sha> (a
        workflow_dispatch run, a first push to the ref), or a changelog absent
        at <sha> (its first commit). A <sha> not present in the checkout is a
        tool error.

Exit 0 when nothing new is parked (or, under --push-before, nothing to
measure). Exit 1 when a new item is parked. Exit 2 on a usage or tool error
(`git` unavailable, base ref unknown, unreadable file).
"""
import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

H2 = "## "
ENTRY = "### "


def git(*args: str) -> subprocess.CompletedProcess:
    """Run `git`, turning an absent binary into a tool error, never a verdict."""
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True)
    except OSError as exc:
        # An absent or unrunnable `git` is the absence of an answer, never the
        # misfiling verdict. Letting it raise exits 1, which is that verdict.
        raise RuntimeError(
            "cannot run `git` ({}); install it, or run this check from an "
            "environment where it is on PATH".format(exc)) from None


def base_text(base: str, changelog: str) -> str:
    """The changelog as it stands on `base`."""
    try:
        proc = git("show", f"{base}:{changelog}")
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


def push_base(before: str, changelog: str) -> str | None:
    """The ref to measure a publish push against, or None when there is none.

    None means nothing to measure, and says why on stderr.
    """
    if not before.strip("0"):
        print("notice: no before-commit (workflow_dispatch, or a first push "
              "to the ref); the placement check has nothing to measure",
              file=sys.stderr)
        return None
    present = git("cat-file", "-e", f"{before}^{{commit}}")
    if present.returncode != 0:
        raise RuntimeError(
            "before-commit {} is not in this checkout ({}). Check out with "
            "full history (`actions/checkout` with `fetch-depth: 0`) so the "
            "placement check can measure the push.".format(
                before, present.stderr.strip() or "no diagnostic"))
    listed = git("ls-tree", "--name-only", before, "--", changelog)
    if listed.returncode != 0:
        raise RuntimeError(
            "`git ls-tree {} -- {}` failed (exit {}): {}. Repair the checkout "
            "and re-run the publish.".format(
                before, changelog, listed.returncode,
                listed.stderr.strip() or "no diagnostic"))
    if not listed.stdout.strip():
        print("notice: {} does not exist at {}; the placement check has "
              "nothing to measure".format(changelog, before), file=sys.stderr)
        return None
    return before


def items(text: str) -> list[tuple[str | None, str]]:
    """Every entry item in `text`, with the `## ` heading it sits under.

    The heading is None above the first `## ` heading.
    """
    result = []
    heading = None
    current: list[str] = []
    previous_blank = True

    def close():
        if current:
            result.append((heading, "\n".join(current).rstrip()))
            current.clear()

    for line in text.splitlines():
        if line.startswith(H2) or line.startswith(ENTRY):
            close()
            if line.startswith(H2):
                heading = line.rstrip()
            previous_blank = True
            continue
        if not line.strip():
            if current:
                current.append(line)
            previous_blank = True
            continue
        starts_item = not line[0].isspace() and (
            line.startswith("- ") or previous_blank)
        if starts_item:
            close()
            # Content above the first `## ` that is not an entry -- the
            # `# Changelog` title, an intro paragraph -- is still an item: it
            # is never parked, and a moved copy of it is a move.
        current.append(line)
        previous_blank = False
    close()
    return result


def parked(text: str) -> list[str]:
    """The entry items sitting under a version heading.

    Empty while the file carries no version heading at all, which is a first
    release rather than a misfiling.
    """
    return [item for heading, item in items(text) if heading is not None]


def newly_parked(original: str, text: str) -> list[str]:
    """Parked items that are neither a move nor an in-place edit.

    See the decision contract in the module docstring.
    """
    known = Counter(item for _, item in items(original))
    branch = items(text)
    carried = Counter(item for _, item in branch)

    # Parked base items the branch no longer carries anywhere, by heading:
    # the only evidence an in-place edit can pair with.
    removed: Counter = Counter()
    for heading, item in items(original):
        if heading is None:
            continue
        if carried[item]:
            carried[item] -= 1
        else:
            removed[heading] += 1

    new_items = []
    for heading, item in branch:
        if heading is None:
            continue
        if known[item]:
            known[item] -= 1
        elif removed[heading]:
            removed[heading] -= 1
        else:
            new_items.append(item)
    return new_items


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base",
                      help="the ref this branch is measured against, e.g. origin/main")
    mode.add_argument("--push-before", metavar="SHA",
                      help="the commit a publish push landed on (github.event.before)")
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
        if args.push_before is not None:
            base = push_base(args.push_before, args.changelog)
            if base is None:
                return 0
        else:
            base = args.base
        original = base_text(base, args.changelog)
    except RuntimeError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    new_parked = newly_parked(original, text)
    if not new_parked:
        return 0
    print(
        "error: {} parks {} entry item(s) under an already-published version "
        "heading whose content is new to {}. The publish step stamps only what "
        "sits ABOVE the first `## ` heading, so an entry below one ships filed "
        "under a version that already shipped:".format(
            "this push" if args.push_before is not None else "this branch",
            len(new_parked), base), file=sys.stderr)
    for item in new_parked:
        print("  {}".format(item.splitlines()[0]), file=sys.stderr)
    if args.push_before is not None:
        print(
            "The publish is stopped so no version ships without its entry. "
            "Open a follow-up PR that moves the new item above the topmost "
            "`## ` heading; its merge publishes it under a fresh version.",
            file=sys.stderr)
    else:
        print(
            "Rebase onto {} and move the new item above the topmost `## ` "
            "heading. Moving an item the base already carries, adding the "
            "`## `/`### ` heading lines a repair needs, and rewording an item "
            "in place under its own heading are fine.".format(base),
            file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
