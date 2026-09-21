"""Validate a review partition: one owner per changed file, no file unowned.

`rules/agent-team-operation.md` Review Before PR describes one reviewer per
round. A reviewer roaming an unbounded surface that reports no findings has not
established that the surface is clean -- only that this pass happened not to
reach a defect, which is why a twenty-round delivery ran three, three, two,
one, one, one, two, two, one, one blocking findings and never converged (#409).

A partition supplies the missing termination condition: a slice is saturated
when its reviewer reports clean at the current tip, and a change is reviewed
when every slice is saturated at one tip. That only holds while the partition
is DISJOINT and EXHAUSTIVE over the change. A gap is indistinguishable from a
clean slice in the result, and an overlap makes two verdicts answer for one
file while neither owns it.

This module decides that, and nothing else: it never chooses slices, never
assigns workers, and never reads a report. The lead writes the partition; the
planner seats it; this says whether it can carry a verdict.

Contract:

* `load_partition(path)` reads the partition document.
* `validate(changed, partition)` is pure over a changed-path set and that
  document.
* `run_command(args, runner=...)` collects the changed set through `runner`,
  a callable taking a git argument list and returning its stdout, and returns
  the `(payload, failure)` pair every command returns.
* The payload lists each slice with the paths it owns. A partition that cannot
  carry a verdict raises `UsageError` naming every unowned path and every
  overlap with the slices that claim it, so the round refuses before a worker
  is spent.
"""

import fnmatch
import hashlib
import json
import re
from pathlib import Path

from .errors import UsageError
from .tiers import SEAT_SEPARATOR, SEATABLE_ROLES, SLICE_NAME
from .triggers import git_runner, parse_name_status

#: The partition document's own version, so a later shape change is auditable
#: (`rules/stateful-artifacts.md` Migration Policy).
PARTITION_SCHEMA_VERSION = 1

COMMANDS = frozenset({"validate-partition"})

#: Characters a path glob never needs, and that a seat's rendered brief cannot
#: carry safely.
UNSAFE_GLOB = re.compile(r"[`\x00-\x1f\x7f]")

#: The responsibilities a partition seats, which are the seatable roles
#: `tiers.SEATABLE_ROLES` names.
PARTITION_ROLES = SEATABLE_ROLES




def load_partition(path):
    """Read the partition document the lead wrote for this round."""
    if not path:
        raise UsageError("Pass --partition naming the round's review partition; a multi-seat review round has no partition to seat without one.", {})
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError("Cannot read the partition at {}: {}. Write a JSON object with schema_version and slices.".format(path, exc), {"path": str(path)}) from None
    return validate_document(document, str(path))


def validate_document(document, path):
    """The document's own contract, shared by the raw and validated loaders."""
    if not isinstance(document, dict) or document.get("schema_version") != PARTITION_SCHEMA_VERSION:
        raise UsageError("The partition must be a JSON object at schema_version {}.".format(PARTITION_SCHEMA_VERSION), {"path": str(path)})
    partition_role(document)
    unknown = set(document) - {"schema_version", "role", "slices"}
    if unknown:
        raise UsageError("The partition carries unknown field(s) {}; it holds schema_version, an optional role, and slices.".format(", ".join(sorted(unknown))), {"path": str(path)})
    slices = document.get("slices")
    if not isinstance(slices, list) or len(slices) < 2:
        raise UsageError("A partition names at least two slices; a single-seat round needs none.", {"path": str(path)})
    seen = set()
    for entry in slices:
        if (not isinstance(entry, dict) or set(entry) != {"name", "paths"}
                or not isinstance(entry["name"], str) or not entry["name"].strip()):
            raise UsageError("Each slice is an object with a non-empty name and a paths array.", {"path": str(path)})
        if not SLICE_NAME.fullmatch(entry["name"]):
            raise UsageError(
                "Slice name {!r} cannot address its seat: name it with letters, digits, underscores, dots or hyphens, starting with a letter or digit. A seat is a CLI key, and a name carrying {!r}, a separator or whitespace does not read back.".format(
                    entry["name"], SEAT_SEPARATOR),
                {"path": str(path)})
        if entry["name"] in seen:
            raise UsageError("Slice name {!r} appears twice; each seat owns one named slice.".format(entry["name"]), {"path": str(path)})
        seen.add(entry["name"])
        patterns = entry["paths"]
        if (not isinstance(patterns, list) or not patterns
                or any(not isinstance(item, str) or not item.strip() for item in patterns)):
            raise UsageError("Slice {!r} needs a non-empty array of path globs.".format(entry["name"]), {"path": str(path)})
        # A glob is rendered verbatim into the seat's brief, where a backtick
        # or a control character could close the code span and append
        # instructions of its own. Refused here so an accepted partition always
        # composes, rather than failing a round later (#434).
        if any(UNSAFE_GLOB.search(item) for item in patterns):
            raise UsageError(
                "Slice {!r} has a path glob carrying a backtick or a control character; a glob needs "
                "neither, and each one is rendered into its seat's brief.".format(entry["name"]),
                {"path": str(path)})
    return document


def seat_name(role, slice_name):
    """The planner's name for the seat that owns `slice_name`."""
    return role + SEAT_SEPARATOR + slice_name


def seats_for(partition, role):
    """`{seat_name: role}` for every slice, in declaration order.

    The planner is role-keyed throughout, so several seats of one role reach it
    as distinct names mapped back to the responsibility they fill (#409).
    """
    return {seat_name(role, entry["name"]): role for entry in partition["slices"]}


def seat_digest(seat, paths):
    """A short digest over ONE seat and the paths it owns.

    Per seat, not per round: a round-level digest is identical in every seat's
    brief, so swapping two seats' briefs passes a check that only asks whether
    the digest appears. Binding the seat's own name and globs makes each brief
    answerable for its own boundary (#453).
    """
    canonical = json.dumps([seat, list(paths)], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def slice_scope(seat, paths, digest):
    """The canonical scope block a seated brief must carry, verbatim.

    One sentence, not three facts a brief may scatter: a hand-written brief
    that mentions the slice name, a path and the digest while directing a
    whole-repository pass satisfies three substring checks and still dispatches
    a full-surface verdict as a slice one. Requiring this block requires the
    restrictions with it (#453).

    `compose-briefs.sh` renders the same string for the brief; its
    `slice_scope()` and this function are pinned together by
    `tests/test_slice_scope_parity.py`.
    """
    listed = ", ".join("`{}`".format(glob) for glob in paths)
    return (
        "Your slice this round is **{}**, and it owns {}. That slice is your "
        "whole surface: a full pass covers all of it and nothing beyond it. An "
        "observation outside your slice goes in a separate section of your "
        "report and forms no part of your verdict. (Partition {}.)".format(
            seat.split(SEAT_SEPARATOR, 1)[1], listed, digest))


def slice_digest(seat_paths):
    """A short digest over the accepted `{seat: [glob, ...]}` map.

    Carried from the plan into each seat's brief and checked at dispatch, so a
    boundary edited between `validate-partition` and the send is refused rather
    than dispatched (#453). Twelve hex characters: enough to catch an edit,
    short enough to sit in a brief a worker reads.
    """
    canonical = json.dumps({seat: list(paths) for seat, paths in sorted(seat_paths.items())},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def load_validated(path):
    """Read a `validate-partition` RESULT, not the document it was built from.

    The result is the proof: it carries the slices with their RESOLVED paths
    and the `changed` set they were checked against, so a plan seated from it
    is seated from a partition proven disjoint and exhaustive over this round's
    change. `load_partition` alone reads shape and cannot check ownership —
    `plan` has no repo, base or head to check against (#453).
    """
    if not path:
        raise UsageError(
            "Pass --partition naming the output of `teamlead validate-partition`; a "
            "multi-seat round seats from the checked result, never from the document.",
            {})
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(
            "Cannot read the validated partition at {}: {}. Pass the JSON "
            "`validate-partition` wrote.".format(path, exc), {"path": str(path)}) from None
    if not isinstance(document, dict) or "changed" not in document:
        raise UsageError(
            "The partition at {} carries no `changed` set, so it is the document rather "
            "than a checked result: run `teamlead validate-partition --repo <path> --base "
            "<sha> [--head <sha>] --partition {}` and pass its output.".format(path, path),
            {"path": str(path)})
    changed = document["changed"]
    if not isinstance(changed, list) or not changed or any(
            not isinstance(item, str) or not item.strip() for item in changed):
        raise UsageError(
            "The validated partition's `changed` must list the round's changed paths; "
            "re-run `validate-partition` rather than editing its result.",
            {"path": str(path)})
    # The document's own contract, then its OWNERSHIP re-derived against the
    # `changed` set it carries. Reading the result's shape and trusting its
    # verdict would accept an edited result: overlapping slices and an
    # uncovered file pass a shape check, and the round seats against them.
    # Re-deriving costs nothing and re-proves the property rather than taking
    # the artifact's word for it (#453).
    inner = {key: value for key, value in document.items() if key != "changed"}
    accepted = validate_document(inner, str(path))
    validate_resolved(set(changed), accepted, str(path))
    return accepted


def validate_resolved(changed, partition, source):
    """Re-prove a VALIDATED result disjoint and exhaustive over `changed`.

    Set membership, not `fnmatch`. A result's `slices[].paths` carries the
    resolved changed files `validate()` assigned, not the globs the document
    it read carried, and a resolved name is a literal: re-matching it as a
    pattern reads `src/api/[x].py` as a character class and reports the file
    it names unowned, rejecting a valid result (#453).
    """
    owners_of = {}
    for entry in partition["slices"]:
        for resolved in entry["paths"]:
            owners_of.setdefault(resolved, []).append(entry["name"])
    unowned = sorted(changed - set(owners_of))
    overlaps = sorted(path for path, names in owners_of.items() if len(names) > 1)
    stray = sorted(set(owners_of) - changed)
    empty = sorted(entry["name"] for entry in partition["slices"] if not entry["paths"])
    if unowned or overlaps or stray or empty:
        parts = []
        if unowned:
            parts.append("leaves {} changed path(s) unowned, starting with {}".format(
                len(unowned), unowned[0]))
        if overlaps:
            parts.append("gives {} path(s) more than one owner, starting with {}".format(
                len(overlaps), overlaps[0]))
        if stray:
            parts.append("assigns {} path(s) the `changed` set does not carry, starting "
                         "with {}".format(len(stray), stray[0]))
        if empty:
            parts.append("leaves slice(s) {} owning nothing".format(", ".join(empty)))
        raise UsageError(
            "The validated partition at {} does not cover its own `changed` set: it {}. "
            "Re-run `validate-partition` rather than editing its result.".format(
                source, "; and it ".join(parts)),
            {"unowned": unowned, "overlaps": overlaps, "stray": stray, "empty": empty})
    return partition


def seat_paths(partition, role):
    """`{seat_name: [path, ...]}` — the paths each seat's slice owns.

    Resolved literals, not patterns: they come from a `validate-partition`
    RESULT, whose `slices[].paths` carry the changed files `validate()`
    assigned rather than the globs the document it read carried. The composer
    requires a seat's paths and reads no partition document, so the plan
    carries them out of the validated partition rather than leaving the lead
    to copy the boundary by hand (#434).
    """
    return {seat_name(role, entry["name"]): list(entry["paths"]) for entry in partition["slices"]}


def slice_of(seat):
    """The slice a seat owns, or None for a plain role name."""
    if not isinstance(seat, str) or SEAT_SEPARATOR not in seat:
        return None
    return seat.split(SEAT_SEPARATOR, 1)[1]


def partition_role(partition):
    """The role the partition seats; `reviewer` unless the document says."""
    role = partition.get("role", "reviewer")
    # A JSON document can name an unhashable role. The membership test would
    # raise TypeError past every caller expecting this module's UsageError.
    if not isinstance(role, str) or role not in PARTITION_ROLES:
        raise UsageError(
            "A partition seats {}; every other responsibility carries per-task gates one seat owns.".format(
                " or ".join(sorted(PARTITION_ROLES))),
            {"role": role})
    return role


def owners(path, slices):
    """The slice names whose globs match `path`, in declaration order."""
    matched = []
    for entry in slices:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in entry["paths"]):
            matched.append(entry["name"])
    return matched


def validate(changed, partition):
    """Decide whether `partition` can carry a verdict over `changed`.

    Returns the per-slice ownership payload. Raises UsageError naming every
    unowned path and every overlap: a gap reads as a clean slice, and an
    overlap leaves a file two verdicts and no owner.
    """
    slices = partition["slices"]
    assignment = {entry["name"]: [] for entry in slices}
    unowned = []
    overlaps = []
    for path in sorted(changed):
        matched = owners(path, slices)
        if not matched:
            unowned.append(path)
        elif len(matched) > 1:
            overlaps.append({"path": path, "slices": matched})
        else:
            assignment[matched[0]].append(path)
    # A slice party to an overlap owns nothing yet, but its emptiness is that
    # overlap's doing and naming it again would send the reader after the wrong
    # fix.
    contested = {name for row in overlaps for name in row["slices"]}
    empty = sorted(name for name, paths in assignment.items()
                   if not paths and name not in contested)
    # Every problem in ONE run. Raising on the first class would hide an
    # overlap behind a gap and cost a round per class to find them all.
    if unowned or overlaps or empty:
        parts = []
        if unowned:
            parts.append("leaves {} changed path(s) unowned, starting with {} (a gap is indistinguishable from a clean slice in the result)".format(
                len(unowned), ", ".join(unowned[:5])))
        if overlaps:
            first = overlaps[0]
            parts.append("gives {} changed path(s) more than one owner, starting with {} claimed by {} (two verdicts over one file leave it owned by neither)".format(
                len(overlaps), first["path"], ", ".join(first["slices"])))
        if empty:
            parts.append("has slice(s) {} owning no changed path (a seat with nothing to review is a worker spent for no verdict)".format(
                ", ".join(empty)))
        raise UsageError(
            "The partition cannot carry a verdict: it {}. Extend, narrow or drop the slices named in the details and re-run.".format(
                "; and it ".join(parts)),
            {"unowned": unowned, "overlaps": overlaps, "empty": empty},
        )
    return {"schema_version": PARTITION_SCHEMA_VERSION,
            "role": partition_role(partition),
            "slices": [{"name": entry["name"], "paths": assignment[entry["name"]]} for entry in slices],
            "changed": sorted(changed)}


def register_commands(sub, common):
    parser = sub.add_parser(
        "validate-partition", parents=[common],
        help="Check that a review partition is disjoint and exhaustive over the round's changed paths.",
    )
    parser.add_argument("--repo", required=True, metavar="PATH", help="The repository whose diff the partition covers.")
    parser.add_argument("--base", required=True, metavar="REV", help="The revision the round started from.")
    parser.add_argument("--head", metavar="REV", help="The pushed head; omit to read the working tree.")
    parser.add_argument("--partition", required=True, metavar="FILE", help="The round's partition document.")


def run_command(args, runner=None):
    """Validate this round's partition against the paths its diff changed."""
    partition = load_partition(args.partition)
    run = runner if runner is not None else git_runner(args.repo)
    head = getattr(args, "head", None)
    span = [args.base + "..." + head] if head else [args.base]
    changes = parse_name_status(run(["diff", "--no-renames", *span, "--name-status", "-z"]))
    return validate(set(changes), partition), None
