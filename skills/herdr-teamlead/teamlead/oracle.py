"""Compare a mechanical round's actual result against the oracle its plan declared.

A declared oracle is what licenses a round below its floor (#480). The licence
is only honest if the comparison runs: a digest nobody checks catches nothing.
This is that check. It reads the oracle from the saved plan, never from the
caller, so the round is judged against the oracle it was licensed on.

`digest` compares the sha256 of the result file. `patch` and `fixture` compare
the result file byte for byte against the file the oracle names.

Exit 0 on a match. Exit 1 on a mismatch, with the verdict on stdout and
`match: false`, or on a usage error, with no verdict. A mismatch is a blocking
finding on the round, never a warning.
"""

import hashlib
import json
from pathlib import Path

from .errors import UsageError

COMMANDS = frozenset({"verify-oracle"})


def _read_bytes(path, what):
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise UsageError("Cannot read the {} at {}: {}. Pass the file the round produced.".format(
            what, path, exc.strerror or exc), {"path": str(path)}) from None


def plan_oracle(plan, role):
    """The oracle a saved plan declared for one role, or a refusal naming why none applies."""
    rounds = plan.get("rounds") if isinstance(plan, dict) else None
    entry = rounds.get(role) if isinstance(rounds, dict) else None
    context = entry.get("context") if isinstance(entry, dict) else None
    oracle = context.get("oracle") if isinstance(context, dict) else None
    if not isinstance(oracle, dict):
        raise UsageError("The plan declares no oracle for {!r}; only a mechanical round is checked "
                         "against one.".format(role), {"role": role})
    return oracle


def verify(oracle, result_path):
    """Compare the result file against the oracle. Returns the verdict document."""
    kind = oracle.get("kind")
    actual = _read_bytes(result_path, "round result")
    if kind == "digest":
        expected = oracle.get("value")
        observed = hashlib.sha256(actual).hexdigest()
        return {"schema_version": 1, "kind": kind, "match": observed == expected,
                "expected": expected, "observed": observed, "result": str(result_path)}
    if kind in ("patch", "fixture"):
        path = oracle.get("path")
        wanted = _read_bytes(path, "{} oracle".format(kind))
        return {"schema_version": 1, "kind": kind, "match": actual == wanted,
                "expected": hashlib.sha256(wanted).hexdigest(),
                "observed": hashlib.sha256(actual).hexdigest(),
                "oracle": path, "result": str(result_path)}
    raise UsageError("Oracle kind {!r} is not one of digest, patch, fixture.".format(kind), {})


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
