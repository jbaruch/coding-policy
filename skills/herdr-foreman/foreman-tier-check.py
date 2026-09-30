#!/usr/bin/env python3
"""Headroom measurement and the foreman's tier proof, as one composite check.

The foreman's tier selection reads the snapshot `foreman measure` writes and
records its headroom (`skills/herdr-foreman/references/team-operation.md` Foreman Seat), so the
two are one check: the
round preflight runs this once and records its result. Config presence is read
apart from the measurement, so an absent `foreman` block is always the
`unconfigured` warning and never a block.

Usage: foreman-tier-check.py [--state FILE] [--config FILE] [--now ISO] [--no-measure]

Output contract (rules/script-delegation.md -- structured stdout):
  stdout: one JSON object --
    {"headroom": <row>, "foreman_tier": <row>}
    <row> = {"status": "<status>", "reason": "<blocking reason>"?, "detail": {...}?}
  headroom:     ok (the measure JSON as detail); skipped under --no-measure;
                failed when measure exits non-zero; blocked when it exits 0
                without one readable JSON object
  foreman_tier: ok (the verify-foreman result as detail) when headroom is ok or
                skipped and that result carries a process-argv proof of the
                selected tier -- see `proof_problem`; unconfigured, with the
                config file and block to add in a non-empty `detail.warning`,
                when config has no `foreman` block, whatever headroom reported;
                failed otherwise, naming the command to re-run
  A row carrying `reason` blocks the round; `unconfigured` and `skipped` do not.
  stderr: each collaborator's own diagnostic, relayed verbatim.

Exit codes -- the verdict, which the preflight gates on:
  0  ready: every row is ok, skipped, or unconfigured with its warning
  1  not ready: a row is failed or blocked, its reason in the JSON
  2  could not run: a collaborator could not be started; a diagnostic on
     stderr and no JSON
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

#: The owner CLI every row is derived from, beside this script.
FOREMAN = Path(__file__).resolve().parent / "foreman.sh"
#: Where to go when the running tier is not the selected one.
RESTART_POINTER = "skills/herdr-foreman/references/model-tiers.md Foreman Seat"


def run(args):
    """`(exit code, stdout)` of one owner command, its stderr relayed."""
    result = subprocess.run(["bash", str(FOREMAN), *args], capture_output=True, text=True, check=False)
    sys.stderr.write(result.stderr)
    return result.returncode, result.stdout


def json_object(text):
    """The parsed object, or a reason naming why the output is not one."""
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return None, "not readable JSON ({})".format(exc)
    if not isinstance(payload, dict):
        return None, "JSON {} where its contract emits one JSON object".format(type(payload).__name__)
    return payload, None


def headroom_row(common, clock, measure):
    if not measure:
        return {"status": "skipped"}
    code, out = run([*common, "measure", *clock])
    if code != 0:
        return {"status": "failed",
                "reason": "foreman measure exited {}; a seat cannot be ranked on an unmeasured roster".format(code)}
    payload, problem = json_object(out)
    if problem:
        return {"status": "blocked",
                "reason": "`foreman measure` wrote {}; re-run it, read its diagnostic, and repair it to emit one "
                          "JSON object before planning".format(problem)}
    return {"status": "ok", "detail": payload}


def configured_flag(out):
    payload, _problem = json_object(out)
    flag = payload.get("configured") if payload is not None else None
    return payload, flag if isinstance(flag, bool) else None


def proof_problem(result):
    """Why a configured verify-foreman result does not prove the selected tier, or None.

    The proof is `verified`: the live foreground argv read from the pane
    (`source` `process_argv`, a non-empty `argv`), whose proven model and
    effort equal the selected `tier`'s.
    """
    tier, verified = result.get("tier"), result.get("verified")
    if result.get("argv_verified") is not True or not isinstance(tier, dict) or not isinstance(verified, dict):
        return "carries no argv proof of the selected tier"
    if verified.get("source") != "process_argv":
        return "proves the tier from {!r}, not the pane's live process argv".format(verified.get("source"))
    argv = verified.get("argv")
    if not isinstance(argv, list) or not argv:
        return "carries no process argv"
    proven, selected = (verified.get("model"), verified.get("effort")), (tier.get("model"), tier.get("effort"))
    if not isinstance(selected[0], str) or not selected[0] or proven != selected:
        return "proves model/effort {} where the selected tier is {}".format(proven, selected)
    return None


def unconfigured_row(payload):
    warning = payload.get("warning")
    if isinstance(warning, str) and warning.strip():
        return {"status": "unconfigured", "detail": payload}
    return {"status": "failed",
            "reason": "foreman verify-foreman reported the seat unconfigured without the warning naming the block to "
                      "add; re-run it and read its diagnostic"}


def foreman_row(common, measured):
    if not measured:
        code, out = run([*common, "verify-foreman", "--config-only"])
        payload, flag = configured_flag(out) if code == 0 else (None, None)
        if flag is False and payload is not None:
            return unconfigured_row(payload)
        return {"status": "failed",
                "reason": "not verified: the foreman's tier selection reads the snapshot foreman measure writes, and "
                          "that check did not pass; fix checks.headroom, then re-run the preflight"}
    code, out = run([*common, "verify-foreman"])
    if code != 0:
        return {"status": "failed",
                "reason": "foreman verify-foreman exited {}; this pane does not run the foreman's selected tier. Read "
                          "its diagnostic, then restart the foreman with start-foreman from another shell ({})".format(
                              code, RESTART_POINTER)}
    payload, flag = configured_flag(out)
    if flag is True and payload is not None:
        problem = proof_problem(payload)
        if problem is None:
            return {"status": "ok", "detail": payload}
        return {"status": "failed",
                "reason": "foreman verify-foreman exited 0 but its result {}; the foreman's tier is unproven. Re-run "
                          "it and read its diagnostic ({})".format(problem, RESTART_POINTER)}
    if flag is False and payload is not None:
        return unconfigured_row(payload)
    return {"status": "failed",
            "reason": "foreman verify-foreman exited 0 without a readable configured flag; the foreman's tier is unproven"}


#: The row statuses a ready round may carry; any other status, or any row
#: carrying a reason, makes the verdict not ready.
READY_STATUSES = frozenset({"ok", "skipped", "unconfigured"})


def verdict(rows):
    """Exit 0 when every row is ready, 1 otherwise."""
    ready = all(row.get("status") in READY_STATUSES and "reason" not in row for row in rows.values())
    return 0 if ready else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Headroom and the foreman's tier proof, as one check.")
    parser.add_argument("--state")
    parser.add_argument("--config")
    parser.add_argument("--now")
    parser.add_argument("--no-measure", action="store_true")
    args = parser.parse_args(argv)
    common = []
    if args.state:
        common += ["--state", args.state]
    if args.config:
        common += ["--config", args.config]
    clock = ["--now", args.now] if args.now else []
    try:
        headroom = headroom_row(common, clock, not args.no_measure)
        foreman = foreman_row(common, headroom["status"] in ("ok", "skipped"))
    except OSError as exc:
        print("foreman-tier-check: cannot run {} ({}); restore the plugin's foreman.sh and a bash on PATH, then "
              "re-run the preflight".format(FOREMAN, exc), file=sys.stderr)
        return 2
    rows = {"headroom": headroom, "foreman_tier": foreman}
    print(json.dumps(rows, sort_keys=True))
    return verdict(rows)


if __name__ == "__main__":
    sys.exit(main())
