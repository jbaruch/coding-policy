"""`foreman finding-churn`: which findings sit on lines the previous fix round added.

A weighing nomination is deterministic screening, never judgment
(`skills/herdr-foreman/references/team-operation.md` Judge Seat). This module is the screening:
given the previous fix round's head (`--from`) and the head under review
(`--to`), it reports for each finding whether its line falls inside a hunk
that round added, and whether its file changed at all between the two heads.

`path_changed` answers the carry-over question for an internal finding a
ruling already covers: the ruling still covers it while its file is unchanged
from the ruling's `HEAD:` to the current head.

Contract:

* `added_ranges(diff_text)` is pure: the new-side `(start, count)` of every
  hunk in one file's `git diff -U0` output.
* `classify(diff_text, line)` is pure: `(added_by_last_fix, path_changed)`.
* `parse_changes(text)` is pure: `git diff --name-status -z -M` output as
  `{path at --to: (status letter, path at --from)}`, plus the set of paths
  that no longer exist at `--to`.
* `run_command(args, runner=...)` resolves both heads, refuses a `--from` that
  is not an ancestor of `--to`, and diffs each finding's file through
  `runner`, a callable taking a git argument list and returning its stdout.

Renames are detected (`-M`). A renamed file is diffed blob to blob, old path
at `--from` against new path at `--to`, so a pure rename adds no line and a
renamed-and-edited file reports only its edited lines. A rename always reads
`path_changed`, the direction that refuses carry-over. A finding on a path
deleted by, or renamed away within, the range, or on a change git classifies
some other way (a type change), is refused rather than guessed.
"""

import re

from .errors import UsageError
from .partition import _revision as resolve_commit
from .triggers import git_runner

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")


def added_ranges(diff_text):
    """New-side (start, count) of each hunk; a deletion-only hunk has count 0."""
    ranges = []
    for line in diff_text.split("\n"):
        match = HUNK_RE.match(line)
        if match:
            count = match["count"]
            ranges.append((int(match["start"]), 1 if count is None else int(count)))
    return ranges


def classify(diff_text, line):
    """Whether `line` sits in an added range, and whether the file changed at all."""
    added = any(start <= line < start + count for start, count in added_ranges(diff_text))
    return added, bool(diff_text.strip())


def parse_finding(value):
    path, separator, line = value.rpartition(":")
    if not separator or not path or not line.isdigit() or int(line) < 1:
        raise UsageError("--finding expects PATH:LINE with a positive line number, got {!r}.".format(value),
                         {"value": value})
    return path, int(line)


def parse_changes(text):
    """Name-status records by path at `--to`, and the paths gone from `--to`."""
    fields = [field for field in text.split("\0") if field != ""]
    changes, gone, index = {}, set(), 0
    while index < len(fields):
        status = fields[index]
        letter = status[:1]
        if letter in ("R", "C"):
            if index + 2 >= len(fields):
                raise UsageError("git diff --name-status ended inside a rename record; rerun against an intact repository.", {})
            old, new = fields[index + 1], fields[index + 2]
            changes[new] = (letter, old)
            if letter == "R":
                gone.add(old)
            index += 3
            continue
        if index + 1 >= len(fields):
            raise UsageError("git diff --name-status ended inside a record; rerun against an intact repository.", {})
        path = fields[index + 1]
        if letter == "D":
            gone.add(path)
        else:
            changes[path] = (letter, path)
        index += 2
    return changes, gone


DIFF = ["diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv"]


def run_command(args, runner=None):
    run = runner if runner is not None else git_runner(args.repo)
    findings = [parse_finding(value) for value in args.finding]
    # An unknown revision is refused by name; a broken repository keeps git's diagnostic.
    source = resolve_commit(run, args.source)
    target = resolve_commit(run, args.target)
    # Commits reachable from --from but not from --to: none iff --from is an ancestor.
    if run(["rev-list", "--count", target + ".." + source]).strip() != "0":
        raise UsageError("--from {} is not an ancestor of --to {}, so \"lines the previous fix round added\" is undefined; "
                         "pass the previous fix round's head on this branch as --from.".format(source, target),
                         {"from": source, "to": target})
    changes, gone = parse_changes(run(["diff", "--name-status", "-z", "-M", source, target]))
    diffs = {}
    rows = []
    for path, line in findings:
        if path not in diffs:
            letter, old = changes.get(path, ("", path))
            if path in gone and path not in changes:
                raise UsageError("{} does not exist at --to {}; name the finding's path at the head under review.".format(
                    path, target), {"path": path})
            if letter == "":
                diffs[path] = ""
            elif letter in ("A", "M"):
                diffs[path] = run([*DIFF, "--no-renames", source, target, "--", ":(literal)" + path])
            elif letter == "R":
                # A pure rename's blob diff is empty; the non-empty stand-in keeps
                # `path_changed` true with no added range.
                diffs[path] = run([*DIFF, source + ":" + old, target + ":" + path]) or "renamed"
            else:
                raise UsageError("git classifies {} as {!r} between --from and --to, which this command does not "
                                 "classify; nominate this finding from a MARGINAL line instead.".format(
                                     path, letter), {"path": path, "status": letter})
        added, changed = classify(diffs[path], line)
        rows.append({"path": path, "line": line, "added_by_last_fix": added, "path_changed": changed})
    return {"from": source, "to": target, "findings": rows}, None


def register_command(sub, common):
    parser = sub.add_parser(
        "finding-churn", parents=[common],
        help="Report which findings sit on lines the previous fix round added. Read-only.")
    parser.add_argument("--repo", required=True, metavar="DIR", help="Repository holding both heads.")
    parser.add_argument("--from", dest="source", required=True, metavar="REF",
                        help="The previous fix round's head, or a ruling's HEAD for carry-over.")
    parser.add_argument("--to", dest="target", required=True, metavar="REF", help="The head under review.")
    parser.add_argument("--finding", required=True, action="append", metavar="PATH:LINE",
                        help="A finding's file and line at --to; repeat for each finding.")
