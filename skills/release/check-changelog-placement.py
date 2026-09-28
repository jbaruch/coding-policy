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
2. DECLARED IN-PLACE EDIT -- a commit in the measured range carries a
   `Changelog-Edit: <version>` trailer naming the item's `## ` heading, and
   the base has a parked item under that heading the branch no longer carries
   anywhere. The edited item pairs with it one for one. Rewording a published
   entry is this case. The trailer is the evidence: text alone cannot tell a
   reworded entry from a deleted one replaced by unrelated new work, and a
   misfiling merge carries no such declaration. An edit and a move of the
   same item in one change is not accepted: land them separately.

Every other parked item is NEW content parked where the stamp cannot reach,
and the check refuses it.

Modes
-----
    check-changelog-placement.py --base <ref> [--changelog CHANGELOG.md]
        Measure the working-tree changelog against <ref> (a PR branch against
        origin/main, or a push against the commit it landed on). Trailers
        are read from <ref>..HEAD.

    check-changelog-placement.py --since-last-publish [--changelog CHANGELOG.md]
        Measure the working tree against the last commit the publish pipeline
        recorded, as the stamp step does before publishing. That baseline is
        the newest first-parent ancestor of HEAD (HEAD included) authored by
        `github-actions[bot]` that changes only publish bookkeeping files: the
        changelog, `.tessl-plugin/plugin.json`, `tile.json` (the stamp commit
        and the version-bump commit). A publish the check stopped writes no
        such commit, so a later push or a manual `workflow_dispatch` is still
        measured from the last real publish and cannot carry a refused item
        through. Trailers are read from <baseline>..HEAD. A shallow checkout
        is a tool error. No bookkeeping commit in history (a repo's first
        pipeline publish), or a changelog absent at the baseline, is a pass
        with a stderr notice.

Output
------
stdout carries exactly one JSON object on every outcome, usage errors
included; human diagnostics go to stderr:

    {"verdict": "pass" | "nothing_to_measure" | "misfiled" | "error",
     "base": <ref or null>, "new_items": [<first line of each>],
     "reason": <string or null>}

Exit 0 on `pass` and `nothing_to_measure`. Exit 1 on `misfiled`. Exit 2 on
`error`: a usage or tool error (`git` unavailable or emitting undecodable
output, base ref unknown, shallow history, unreadable file).
"""
import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

H2 = "## "
ENTRY = "### "
BOT = "github-actions[bot]"
BOOKKEEPING = frozenset({".tessl-plugin/plugin.json", "tile.json"})
EDIT_TRAILER = "Changelog-Edit"


def git(*args: str) -> subprocess.CompletedProcess:
    """Run `git`, turning every failure to answer into a tool error.

    An absent binary and output that is not UTF-8 are the absence of an
    answer, never the misfiling verdict. Letting either raise exits 1, which
    is that verdict.
    """
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            "cannot run `git` ({}); install it, or run this check from an "
            "environment where it is on PATH".format(exc)) from None
    except UnicodeError as exc:
        raise RuntimeError(
            "`git {}` produced output that is not UTF-8 ({}); re-save the "
            "changelog as UTF-8, or rewrite the commit message carrying the "
            "undecodable bytes".format(" ".join(args), exc)) from None


def checked(*args: str) -> str:
    """Stdout of a `git` call that must succeed."""
    proc = git(*args)
    if proc.returncode != 0:
        raise RuntimeError(
            "`git {}` failed (exit {}): {}. Repair the checkout, or correct "
            "the ref, and re-run.".format(
                " ".join(args), proc.returncode,
                proc.stderr.strip() or "no diagnostic"))
    return proc.stdout


def base_text(base: str, changelog: str) -> str:
    """The changelog as it stands on `base`."""
    proc = git("show", f"{base}:{changelog}")
    if proc.returncode != 0:
        raise RuntimeError(
            "`git show {}:{}` failed (exit {}): {}. Fetch the base ref "
            "(`git fetch origin <branch>`) or correct --base, and check that "
            "--changelog names a path that exists in it.".format(
                base, changelog, proc.returncode,
                proc.stderr.strip() or "no diagnostic"))
    return proc.stdout


def last_publish(changelog: str) -> str | None:
    """The newest publish-bookkeeping commit on HEAD's first-parent line.

    None when history holds none. The predicate is in the module docstring.
    """
    if checked("rev-parse", "--is-shallow-repository").strip() == "true":
        raise RuntimeError(
            "this checkout is shallow, so the last publish commit may be "
            "missing from it. Check out with full history (`actions/checkout` "
            "with `fetch-depth: 0`).")
    allowed = BOOKKEEPING | {changelog}
    log = checked("log", "--first-parent", "--format=%H%x09%an", "HEAD")
    for line in log.splitlines():
        sha, _, author = line.partition("\t")
        if author != BOT:
            continue
        # NUL-delimited: a path holding spaces or newlines is one record,
        # never several, so a bookkeeping commit is never misread as not one.
        out = checked("diff-tree", "-z", "--no-commit-id", "--name-only", "-r",
                      "--root", sha)
        paths = [path for path in out.split("\0") if path]
        if paths and set(paths) <= allowed:
            return sha
    return None


def exists_at(ref: str, changelog: str) -> bool:
    """Whether `changelog` exists in `ref`'s tree."""
    return any(checked("ls-tree", "-z", "--name-only", ref, "--",
                       changelog).split("\0"))


def declared_edits(base: str) -> set[str]:
    """Versions a `Changelog-Edit:` trailer in <base>..HEAD names."""
    out = checked(
        "log", "--format=%(trailers:key={},valueonly)".format(EDIT_TRAILER),
        f"{base}..HEAD")
    return {token.strip(",") for line in out.splitlines() for token in line.split()}


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


def version_of(heading: str) -> str:
    """The version token of a `## <version> — <date>` heading line."""
    parts = heading.split()
    return parts[1] if len(parts) > 1 else ""


def newly_parked(original: str, text: str,
                 editable: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Parked items that are neither a move nor a declared in-place edit.

    `editable` holds the versions a `Changelog-Edit:` trailer declared. See
    the decision contract in the module docstring.
    """
    known = Counter(item for _, item in items(original))
    branch = items(text)
    carried = Counter(item for _, item in branch)

    # Parked base items the branch no longer carries anywhere, by heading:
    # the only evidence a declared edit can pair with.
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
        elif removed[heading] and version_of(heading) in editable:
            removed[heading] -= 1
        else:
            new_items.append(item)
    return new_items


def emit(verdict: str, base: str | None = None,
         new_items: list[str] | None = None, reason: str | None = None) -> None:
    """The one JSON verdict line on stdout."""
    print(json.dumps({"verdict": verdict, "base": base,
                      "new_items": new_items or [], "reason": reason}))


class Parser(argparse.ArgumentParser):
    """A usage error still answers in JSON on stdout, exit 2."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print("error: {}".format(message), file=sys.stderr)
        emit("error", reason=message)
        sys.exit(2)


def fail(message: str, base: str | None = None) -> int:
    print("error: {}".format(message), file=sys.stderr)
    emit("error", base=base, reason=message)
    return 2


def nothing(message: str, base: str | None = None) -> int:
    print("notice: {}".format(message), file=sys.stderr)
    emit("nothing_to_measure", base=base, reason=message)
    return 0


def main(argv=None) -> int:
    parser = Parser(description=(__doc__ or "").splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base",
                      help="the ref this branch is measured against, e.g. origin/main")
    mode.add_argument("--since-last-publish", action="store_true",
                      help="measure against the last publish-bookkeeping commit")
    parser.add_argument("--changelog", default="CHANGELOG.md")
    args = parser.parse_args(argv)

    path = Path(args.changelog)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return fail("cannot read {}: {}. Correct --changelog, or restore the "
                    "file and its read permission.".format(path, exc))
    except UnicodeError as exc:
        # Not an OSError. Letting it escape exits 1, the misfiling verdict,
        # so a file this tool cannot decode would read as a finding.
        return fail("cannot decode {} as UTF-8: {}. Re-save it as UTF-8, or "
                    "point --changelog at the file that is.".format(path, exc))
    base = args.base
    try:
        if args.since_last_publish:
            base = last_publish(args.changelog)
            if base is None:
                return nothing("no publish-bookkeeping commit by {} in this "
                               "history; the placement check has nothing to "
                               "measure".format(BOT))
            if not exists_at(base, args.changelog):
                return nothing("{} does not exist at the last publish {}; the "
                               "placement check has nothing to measure".format(
                                   args.changelog, base), base)
        original = base_text(base, args.changelog)
        editable = declared_edits(base)
    except RuntimeError as exc:
        return fail(str(exc), base)

    new_parked = newly_parked(original, text, editable)
    if not new_parked:
        emit("pass", base=base)
        return 0
    firsts = [item.splitlines()[0] for item in new_parked]
    print(
        "error: {} entry item(s) sit under an already-published version heading "
        "and are new since {}. The publish step stamps only what sits ABOVE the "
        "first `## ` heading, so an entry below one ships filed under a version "
        "that already shipped:".format(len(new_parked), base), file=sys.stderr)
    for first in firsts:
        print("  {}".format(first), file=sys.stderr)
    if args.since_last_publish:
        hint = ("The publish is stopped so no version ships without its entry. "
                "Open a follow-up PR that moves the new item above the topmost "
                "`## ` heading; its merge publishes it under a fresh version.")
    else:
        hint = ("Rebase onto {} and move the new item above the topmost `## ` "
                "heading. Moving an item the base already carries and adding the "
                "`## `/`### ` heading lines a repair needs are fine. To reword a "
                "published item in place, add a `{}: <version>` trailer naming its "
                "heading to a commit in the change.".format(base, EDIT_TRAILER))
    print(hint, file=sys.stderr)
    emit("misfiled", base=base, new_items=firsts, reason=hint)
    return 1


if __name__ == "__main__":
    sys.exit(main())
