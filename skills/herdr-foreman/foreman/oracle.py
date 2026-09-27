"""Compare a mechanical round's actual result against the oracle its plan declared.

A declared oracle is what licenses a round below its floor (#480). The licence
is only honest if the comparison runs: a digest nobody checks catches nothing.
This is that check. It reads the oracle from the saved plan, never from the
caller, so the round is judged against the oracle it was licensed on.

`digest` compares the sha256 of the result file. `patch` and `fixture` compare
the result file's sha256 against the sha256 of the file the oracle names. Every
file is hashed in bounded chunks, so an oversized result or oracle never loads
whole into memory.

A `patch` or `fixture` oracle is a path, and a path says nothing about the
bytes behind it. `plan` pins each such file's sha256 in the plan's
`oracle_pins` when it writes the plan; `verify-oracle` refuses an oracle file
whose bytes no longer hash to that pin, and a plan that pinned nothing (#488).

Exit 0 on a match. Exit 1 on a mismatch, with the verdict on stdout and
`match: false`, or on a usage error, with no verdict. A mismatch is a blocking
finding on the round, never a warning.
"""

import hashlib
import json
from pathlib import Path

from .errors import UsageError
from .tiers import ORACLE_DIGEST, ORACLE_KINDS, oracle_shape_problem

COMMANDS = frozenset({"verify-oracle"})


#: Bytes read per chunk while hashing. Bounds memory whatever the file's size.
CHUNK_BYTES = 1 << 20


def _sha256(path, what):
    """The hex sha256 of a file, read in CHUNK_BYTES pieces."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError as exc:
        raise UsageError("Cannot read the {} at {!r}: {}. Pass the file the round produced.".format(
            what, str(path), exc.strerror or exc), {"path": str(path)}) from None
    except ValueError as exc:
        # An embedded NUL or an unencodable character never names a file.
        raise UsageError("The {} path {!r} is not a usable file name: {}. Pass the file the round "
                         "produced.".format(what, str(path), exc), {"path": repr(str(path))}) from None
    return digest.hexdigest()


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
        raise UsageError("The plan pins no content for the {} oracle of {!r} at {}; replan so `plan` "
                         "records the file's sha256.".format(oracle["kind"], role, oracle["path"]),
                         {"role": role, "path": oracle["path"]})
    return {**oracle, "sha256": pin["sha256"]}


def pin_oracles(rounds):
    """Each role's patch or fixture oracle file, pinned to its sha256 as `plan` writes the plan.

    A malformed oracle is left unpinned: the round's tier check has already
    refused it where it licenses anything, and `verify-oracle` refuses it again.
    """
    pins = {}
    for role, entry in (rounds or {}).items():
        context = entry.get("context") if isinstance(entry, dict) else None
        oracle = context.get("oracle") if isinstance(context, dict) else None
        if oracle is None or oracle_shape_problem(oracle) is not None or oracle["kind"] == "digest":
            continue
        pins[role] = {"path": oracle["path"],
                      "sha256": _sha256(oracle["path"], "{} oracle".format(oracle["kind"]))}
    return pins


def verify(oracle, result_path):
    """Compare the result file against the oracle. Returns the verdict document.

    A `patch` or `fixture` oracle carries the `sha256` its plan pinned; an
    oracle file that no longer hashes to it is refused, never compared.
    """
    kind = oracle.get("kind")
    if kind not in ORACLE_KINDS:
        raise UsageError("Oracle kind {!r} is not one of {}.".format(kind, ", ".join(ORACLE_KINDS)), {})
    observed = _sha256(result_path, "round result")
    if kind == "digest":
        expected = oracle.get("value")
        return {"schema_version": 1, "kind": kind, "match": observed == expected,
                "expected": expected, "observed": observed, "result": str(result_path)}
    path = oracle.get("path")
    expected = _sha256(path, "{} oracle".format(kind))
    if expected != oracle.get("sha256"):
        raise UsageError("The {} oracle at {} changed since the plan was written (pinned sha256 {}, now {}); "
                         "restore the planned file or replan.".format(kind, path, oracle.get("sha256"), expected),
                         {"path": str(path), "pinned": oracle.get("sha256"), "observed": expected})
    return {"schema_version": 1, "kind": kind, "match": observed == expected,
            "expected": expected, "observed": observed, "oracle": path, "result": str(result_path)}


def register_commands(sub, common):
    parser = sub.add_parser(
        "verify-oracle", parents=[common],
        help="Compare a mechanical round's result against the oracle its plan declared.",
    )
    parser.add_argument("--plan", required=True, metavar="FILE", help="The saved plan the round was dispatched from.")
    parser.add_argument("--role", required=True, help="The role whose round produced the result.")
    parser.add_argument("--result", required=True, metavar="FILE",
                        help="The whole result: the pushed diff for a patch oracle, the produced output otherwise.")


def run_command(args):
    """Returns (verdict, failure). A mismatch is the failure the caller exits 1 on."""
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError("Cannot read plan {}: {}. Pass the plan file the round was dispatched from.".format(
            args.plan, exc), {}) from None
    verdict = verify(plan_oracle(plan, args.role), args.result)
    if verdict["match"]:
        return verdict, None
    return verdict, "The round's result does not match its declared {} oracle; treat it as a blocking finding.".format(
        verdict["kind"])
