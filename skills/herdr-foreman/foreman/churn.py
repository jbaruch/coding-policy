"""`foreman finding-churn`: which findings sit on lines the previous fix round added.

A weighing nomination is deterministic screening, never judgment
(`rules/agent-team-operation.md` Judge Seat). This module is the screening:
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
* `run_command(args, runner=...)` resolves both heads and diffs each finding's
  path through `runner`, a callable taking a git argument list and returning
  its stdout.

Each path is diffed alone with rename detection off. A renamed file then reads
as wholly added and changed, which errs toward nominating the finding and
toward refusing carry-over; neither direction waives a finding.
"""

import re

from .errors import UsageError
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


def resolve(run, ref, flag):
    try:
        return run(["rev-parse", "--verify", "--quiet", ref + "^{commit}"]).strip()
    except UsageError as exc:
        raise UsageError("{} {!r} names no commit in this repository ({}); pass the full sha of the head it "
                         "stands for.".format(flag, ref, exc.message), {"ref": ref}) from None


def run_command(args, runner=None):
    run = runner if runner is not None else git_runner(args.repo)
    findings = [parse_finding(value) for value in args.finding]
    source = resolve(run, args.source, "--from")
    target = resolve(run, args.target, "--to")
    diffs = {}
    rows = []
    for path, line in findings:
        if path not in diffs:
            diffs[path] = run(["diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv",
                               "--no-renames", source, target, "--", path])
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
