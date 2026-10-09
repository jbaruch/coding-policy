"""Compare a mechanical round's actual result against the oracle its plan declared.

A declared oracle is what licenses a round below its floor (#480). The licence
is only honest if the comparison runs: a digest nobody checks catches nothing.
This is that check. It reads the oracle from the saved plan, never from the
caller, so the round is judged against the oracle it was licensed on.

`digest` and `fixture` compare raw sha256. `patch` compares sha256 after
removing only the optional section heading after a unified hunk header:
`@@ -start[,count] +start[,count] @@ heading`. All other bytes, including
line endings and a missing final newline, remain significant. This is not
general patch equivalence or proof of application to a tree. Raw oracle pins
and raw expected/observed digests remain unchanged. Schema-2 verdicts add
`comparison: {mode, expected, observed}` with the digests that decide `match`.
Every file is read once in bounded chunks, including arbitrarily long lines.

A `patch` or `fixture` oracle is a path, and a path says nothing about the
bytes behind it. `plan` pins each such file's sha256 in the plan's
`oracle_pins` when it writes the plan; `verify-oracle` refuses an oracle file
whose bytes no longer hash to that pin, and a plan that pinned nothing (#488).

The plan is still a mutable file. `apply` binds each mechanical round's oracle,
pin included, into its dispatch record and fingerprint; `verify-oracle --task`
checks the round against the oracle the role's latest dispatch bound, and
refuses a plan that no longer declares it, so an edit to the pin map or the
digest after dispatch is refused rather than trusted (#585).

Exit 0 on a match. Exit 1 on a mismatch, with the verdict on stdout and
`match: false`, or on a usage error, with no verdict. A mismatch is a blocking
finding on the round, never a warning.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

from . import runnable
from .chronology import latest_assignment
from .errors import UsageError
from .tiers import ORACLE_DIGEST, ORACLE_KINDS, oracle_shape_problem

COMMANDS = frozenset({"verify-oracle"})


#: Bytes read per chunk while hashing. Bounds memory whatever the file's size.
CHUNK_BYTES = 1 << 20


RESULT_REMEDY = "Pass the file the round produced."
ORACLE_REMEDY = "Restore the oracle file the round was licensed on, or declare a readable one and replan."

_HUNK_DIGITS = {4: 5, 5: 5, 6: 7, 7: 7, 9: 10, 10: 10, 11: 12, 12: 12}
_HUNK_TOKENS = {(5, 44): 6, (5, 32): 8, (7, 32): 8, (8, 43): 9,
                (10, 44): 11, (10, 32): 13, (12, 32): 13,
                (13, 64): 14, (14, 64): 15, (15, 32): 16}


def _hunk_state(state, byte):
    """Streaming unified-header grammar; -1 copies the line, 16 drops its heading."""
    if state < 4:
        return state + 1 if byte == b"@@ -"[state] else -1
    if 48 <= byte <= 57:
        return _HUNK_DIGITS.get(state, -1)
    return _HUNK_TOKENS.get((state, byte), -1)


class _PatchHash:
    """Hash header-normalized bytes without buffering a line or its section text."""

    def __init__(self):
        self.digest = hashlib.sha256()
        self.state = 0
        self.trailing_cr = False

    def update(self, chunk):
        start = 0
        while start < len(chunk):
            newline = chunk.find(b"\n", start)
            end = len(chunk) if newline < 0 else newline
            fragment = chunk[start:end]
            offset = 0
            while self.state not in {-1, 16} and offset < len(fragment):
                self.state = _hunk_state(self.state, fragment[offset])
                if self.state == 16:
                    break
                offset += 1
            if self.state == 16:
                self.digest.update(fragment[:offset])
                if fragment:
                    self.trailing_cr = fragment[-1] == 13
            else:
                self.digest.update(fragment)
            if newline >= 0:
                if self.state == 16 and self.trailing_cr:
                    self.digest.update(b"\r")
                self.digest.update(b"\n")
                self.state, self.trailing_cr = 0, False
            start = end + 1

    def hexdigest(self):
        digest = self.digest.copy()
        if self.state == 16 and self.trailing_cr:
            digest.update(b"\r")
        return digest.hexdigest()


def _file_digests(path, what, remedy, *, patch=False):
    """Raw and comparison digests from the same bounded regular-file read."""
    digest = hashlib.sha256()
    comparison = _PatchHash() if patch else None
    try:
        # Opened non-blocking and checked before any read: a FIFO or a device
        # swapped in for the file would block the gate, or never reach EOF.
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise UsageError("The {} at {!r} is not a regular file. {}".format(
                    what, str(path), remedy), {"path": str(path)})
            for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
                digest.update(chunk)
                if comparison is not None:
                    comparison.update(chunk)
    except OSError as exc:
        raise UsageError("Cannot read the {} at {!r}: {}. {}".format(
            what, str(path), exc.strerror or exc, remedy), {"path": str(path)}) from None
    except (UnicodeEncodeError, ValueError) as exc:
        # An embedded NUL (ValueError) or a character the filesystem encoding
        # cannot carry, such as a lone surrogate (UnicodeEncodeError, itself a
        # ValueError), never names a file.
        raise UsageError("The {} path {!r} is not a usable file name: {}. {}".format(
            what, str(path), exc, remedy), {"path": repr(str(path))}) from None
    raw = digest.hexdigest()
    return raw, comparison.hexdigest() if comparison is not None else raw


def _sha256(path, what, remedy):
    """Raw file sha256 for immutable plan and dispatch pins."""
    return _file_digests(path, what, remedy)[0]


def plan_oracle(plan, role):
    """The oracle a saved plan declared for one role, or a refusal naming why none applies.

    The oracle's shape is checked here, before any file is read: a saved plan
    is a file on disk, and a malformed one is a usage error, never a traceback.
    """
    rounds = plan.get("rounds") if isinstance(plan, dict) else None
    entry = rounds.get(role) if isinstance(rounds, dict) else None
    context = entry.get("context") if isinstance(entry, dict) else None
    oracle = context.get("oracle") if isinstance(context, dict) else None
    if oracle is None:
        raise UsageError("The plan declares no oracle for {!r}; only a mechanical round is checked "
                         "against one.".format(role), {"role": role})
    round_type = entry.get("type") if isinstance(entry, dict) else None
    if round_type != "mechanical":
        # An oracle licenses only a mechanical round; one riding on any other
        # round was never checked at plan time and is never pinned (#576).
        raise UsageError("The plan's round for {!r} is {!r}, not mechanical; its oracle licensed "
                         "nothing and is not checked. Gate the round on its own review.".format(
                             role, round_type), {"role": role})
    problem = oracle_shape_problem(oracle)
    if problem is not None:
        raise UsageError("The plan's oracle for {!r} is malformed: {}. Replan with a well-formed "
                         "--round-context.".format(role, problem), {"role": role})
    if oracle["kind"] == "digest":
        return oracle
    pins = plan.get("oracle_pins")
    pin = pins.get(role) if isinstance(pins, dict) else None
    if (not isinstance(pin, dict) or pin.get("path") != oracle["path"]
            or not isinstance(pin.get("sha256"), str) or not ORACLE_DIGEST.match(pin["sha256"])):
        raise UsageError("The plan pins no content for the {} oracle of {!r} at {}; replan with `{}` "
                         "so the plan records the file's sha256.".format(
                             oracle["kind"], role, oracle["path"], runnable.command("plan")),
                         {"role": role, "path": oracle["path"]})
    return {**oracle, "sha256": pin["sha256"]}


def pin_oracles(rounds):
    """Each role's patch or fixture oracle file, pinned to its sha256 as `plan` writes the plan.

    Only a mechanical round's oracle is pinned: it is the only one that
    licenses anything, and `mechanical_allowed` has already checked its file is
    a readable regular file. A malformed oracle is left unpinned: the round's
    tier check has already refused it, and `verify-oracle` refuses it again.
    """
    pins = {}
    for role, entry in (rounds or {}).items():
        if not isinstance(entry, dict) or entry.get("type") != "mechanical":
            continue
        context = entry.get("context")
        oracle = context.get("oracle") if isinstance(context, dict) else None
        if oracle is None or oracle_shape_problem(oracle) is not None or oracle["kind"] == "digest":
            continue
        pins[role] = {"path": oracle["path"],
                      "sha256": _sha256(oracle["path"], "{} oracle".format(oracle["kind"]), ORACLE_REMEDY)}
    return pins


def has_oracle_round(plan, role):
    """Whether the plan's round for `role` is a mechanical round declaring an oracle."""
    rounds = plan.get("rounds") if isinstance(plan, dict) else None
    entry = rounds.get(role) if isinstance(rounds, dict) else None
    context = entry.get("context") if isinstance(entry, dict) else None
    return (isinstance(entry, dict) and entry.get("type") == "mechanical"
            and isinstance(context, dict) and context.get("oracle") is not None)


def dispatch_oracles(plan, roles):
    """Each role's oracle as its plan licensed it, for `apply` to bind into the dispatch (#585).

    A `patch` or `fixture` oracle carries its pin, and its file must still hash
    to that pin now: the dispatch binds the bytes the round is sent against, so
    a plan whose pin or oracle file was edited after dispatch no longer matches
    what `verify-oracle` reads back from the ledger.
    """
    bound = {}
    for role in roles:
        if not has_oracle_round(plan, role):
            continue
        licensed = plan_oracle(plan, role)
        if licensed["kind"] != "digest":
            current = _sha256(licensed["path"], "{} oracle".format(licensed["kind"]), ORACLE_REMEDY)
            if current != licensed["sha256"]:
                raise UsageError("The {} oracle at {} changed since the plan pinned it (pinned sha256 {}, now {}); "
                                 "restore the planned file, or replan with `{}` before dispatch.".format(
                                     licensed["kind"], licensed["path"], licensed["sha256"], current,
                                     runnable.command("plan")),
                                 {"role": role, "path": licensed["path"]})
        bound[role] = licensed
    return bound


def bound_oracle_problem(value):
    """Why a dispatch's bound oracle is malformed, or None when its shape is sound."""
    if not isinstance(value, dict):
        return "the bound oracle is not a JSON object"
    base = {key: item for key, item in value.items() if key != "sha256"}
    problem = oracle_shape_problem(base)
    if problem is not None:
        return problem
    if base["kind"] == "digest":
        return "a digest oracle carries a pin" if "sha256" in value else None
    pin = value.get("sha256")
    if not isinstance(pin, str) or not ORACLE_DIGEST.match(pin):
        return "a {} oracle's pin is not a lowercase sha256".format(base["kind"])
    return None


def dispatched_oracle(plan, role, dispatches, task):
    """The oracle the role's latest dispatch under `task` bound, when it equals the plan's (#585).

    The plan is a mutable file; the dispatch row is what `apply` sent against.
    The latest dispatch of the role, by event time, must be applied, to the
    worker the plan assigns, and must have bound exactly the oracle the plan
    declares now, pin included. Anything else is a plan edited after dispatch,
    or a round never sent from it, and is refused.
    """
    licensed = plan_oracle(plan, role)
    context = plan.get("task_context")
    if not isinstance(context, dict) or context.get("task") != task:
        raise UsageError("The plan was not made for task {!r}, so no dispatch can be bound to it. Pass the task "
                         "the plan was dispatched under.".format(task), {"task": task})
    assignments = plan.get("assignments")
    agent = assignments.get(role) if isinstance(assignments, dict) else None
    latest = latest_assignment(dispatches, task=task, role=role)
    row = latest[1] if latest else None
    if not isinstance(agent, str) or row is None:
        raise UsageError("Role {} has no dispatch for task {}; a round nobody was sent has no oracle to check. "
                         "Dispatch it with `{}`.".format(role, task, runnable.command("apply")), {"role": role})
    mismatched = [key for key, expected in (("agent", agent), ("fix_round", context.get("fix_round")),
                                            ("plan", context.get("plan")), ("work", context.get("work")))
                  if row.get(key) != expected]
    if mismatched:
        raise UsageError("Role {}'s latest dispatch differs from this plan in {}; it was sent from another plan. "
                         "Verify against the plan that dispatch was sent from.".format(role, ", ".join(mismatched)),
                         {"role": role, "fields": mismatched})
    if row.get("status") != "applied":
        raise UsageError("Role {}'s latest dispatch is {!r}, not applied; reconcile it with `{}` before gating "
                         "the round.".format(role, row.get("status"), runnable.command("reconcile")),
                         {"role": role, "status": row.get("status")})
    bound = row.get("oracle")
    if bound is None:
        raise UsageError("Role {}'s dispatch bound no oracle, so the plan's oracle cannot be shown to be the one "
                         "the round was sent against. Replan with `{}` and dispatch again.".format(
                             role, runnable.command("plan")), {"role": role})
    if bound != licensed:
        raise UsageError("The plan's oracle for {} differs from the one its dispatch bound; the plan was edited "
                         "after dispatch. Verify against the oracle the round was sent with, or replan and "
                         "dispatch again.".format(role),
                         {"role": role, "dispatched": bound, "planned": licensed})
    return bound


def verify(oracle, result_path):
    """Compare the result file against the oracle. Returns the verdict document.

    A `patch` or `fixture` oracle carries the `sha256` its plan pinned; an
    oracle file that no longer hashes to it is refused, never compared.
    """
    kind = oracle.get("kind")
    if kind not in ORACLE_KINDS:
        raise UsageError("Oracle kind {!r} is not one of {}.".format(kind, ", ".join(ORACLE_KINDS)), {})
    observed, compared_result = _file_digests(result_path, "round result", RESULT_REMEDY, patch=kind == "patch")
    if kind == "digest":
        expected = oracle.get("value")
        return {"schema_version": 2, "kind": kind, "match": observed == expected,
                "expected": expected, "observed": observed, "result": str(result_path),
                "comparison": {"mode": "raw_sha256", "expected": expected, "observed": observed}}
    path = oracle.get("path")
    expected, compared_oracle = _file_digests(path, "{} oracle".format(kind), ORACLE_REMEDY, patch=kind == "patch")
    if expected != oracle.get("sha256"):
        raise UsageError("The {} oracle at {} changed since the plan was written (pinned sha256 {}, now {}); "
                         "restore the planned file or replan.".format(kind, path, oracle.get("sha256"), expected),
                         {"path": str(path), "pinned": oracle.get("sha256"), "observed": expected})
    return {"schema_version": 2, "kind": kind, "match": compared_result == compared_oracle,
            "expected": expected, "observed": observed, "oracle": path, "result": str(result_path),
            "comparison": {"mode": "unified_hunk_heading_ignored" if kind == "patch" else "raw_sha256",
                           "expected": compared_oracle, "observed": compared_result}}


def register_commands(sub, common):
    parser = sub.add_parser(
        "verify-oracle", parents=[common],
        help="Compare a mechanical round's result against the oracle its plan declared.",
    )
    parser.add_argument("--plan", required=True, metavar="FILE", help="The saved plan the round was dispatched from.")
    parser.add_argument("--role", required=True, help="The role whose round produced the result.")
    parser.add_argument("--result", required=True, metavar="FILE",
                        help="The whole result: the pushed diff for a patch oracle, the produced output otherwise.")
    parser.add_argument("--task", required=True,
                        help="The task the round was dispatched under; its dispatch holds the oracle it was sent with.")


def run_command(args, dispatches):
    """Returns (verdict, failure). A mismatch is the failure the caller exits 1 on.

    `dispatches` is the recovery ledger's dispatch rows; the oracle checked is
    the one the role's dispatch bound, and the plan must still declare it.
    """
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError("Cannot read plan {}: {}. Pass the plan file the round was dispatched from.".format(
            args.plan, exc), {}) from None
    verdict = verify(dispatched_oracle(plan, args.role, dispatches, args.task), args.result)
    if verdict["match"]:
        return verdict, None
    return verdict, "The round's result does not match its declared {} oracle; treat it as a blocking finding.".format(
        verdict["kind"])
