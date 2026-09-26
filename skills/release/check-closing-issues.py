#!/usr/bin/env python3
"""Check that a PR links the issues it resolves, and that they closed on merge.

GitHub closes an issue on merge only when the PR body names it with a closing
keyword (`Closes #N`, `Fixes #N`, `Resolves #N`). `Refs #N`, `Related #N` and a
bare `#N` are mentions: the issue stays open after the merge, and the next
triage has to rediscover that the work already shipped (#517).

The PR body template in `skills/release/SKILL.md` Step 2 carries issue lines.
This script reads what GitHub resolved from them.

Modes:
  default (before merge)
    Reads `closingIssuesReferences`. Passes when it names at least one issue,
    or when the body carries a whole `Part of #N` line (a partial resolution)
    or a whole `No issue` line (work with no tracking issue). Refuses
    otherwise: the body names no issue GitHub will close and declares no
    alternative.
  --merged (after merge)
    Requires the PR to be MERGED, then reads each closing issue's state until
    every one is CLOSED or the budget runs out. GitHub closes linked issues
    asynchronously, so a first read right after the merge can still see OPEN.
    No read starts after the budget, and no `gh` call outlives it.

Poll interval and budget are script-owned constants, overridable by the
environment: CLOSE_INTERVAL_SEC and CLOSE_BUDGET_SEC, each a finite number of
seconds above 0. GH_TIMEOUT_SEC bounds any single `gh` call.

Usage: check-closing-issues.py <owner> <repo> <pr-number> [--merged]

Output contract (rules/script-delegation.md -- structured stdout):
  stdout: one JSON object --
    {"pr": N, "state": "OPEN|MERGED|CLOSED", "closing": [{"repo": "o/r",
     "number": N, "state": "OPEN|CLOSED|null"}], "partial": [N, ...],
     "no_issue": bool, "verdict": "ok|unlinked|still_open|not_merged"}
  stderr: an actionable diagnostic for every non-ok verdict.

Exit: 0 on verdict ok; 1 on a refusing verdict; 2 on a usage, environment or
`gh` error, with nothing on stdout.
"""

import json
import math
import os
import re
import subprocess
import sys
import time

DEFAULT_INTERVAL_SEC = 5.0
DEFAULT_BUDGET_SEC = 60.0
GH_TIMEOUT_SEC = 30.0
PR_STATES = frozenset({"OPEN", "MERGED", "CLOSED"})
ISSUE_STATES = frozenset({"OPEN", "CLOSED"})

# The two non-closing issue lines the Step 2 template allows, each a whole
# line, so "No issue found" or "Part of #483 remains open" does not count.
PART_OF = re.compile(r"^[ \t]*Part of #(\d+)[ \t]*$", re.MULTILINE)
NO_ISSUE = re.compile(r"^[ \t]*No issue[ \t]*$", re.MULTILINE)


class GhError(Exception):
    """`gh` failed, timed out, or returned a shape this script cannot read."""


class EnvError(Exception):
    """A poll override is not a usable number."""


def env_seconds(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        raise EnvError("{}={!r} is not usable; set a finite number of seconds above 0, or unset it for the default {:g}.".format(
            name, raw, default))
    return value


def gh_json(args, timeout):
    """Run `gh` and parse its JSON stdout. Replaced by tests."""
    try:
        done = subprocess.run(["gh"] + args, capture_output=True, text=True, check=False, timeout=timeout)
    except FileNotFoundError:
        raise GhError("gh is not installed; install the GitHub CLI and run `gh auth login`") from None
    except subprocess.TimeoutExpired:
        raise GhError("gh {} did not answer within {:g}s; check `gh auth status` and network access, then re-run".format(
            " ".join(args), timeout)) from None
    if done.returncode != 0:
        raise GhError("gh {} failed ({}). Check `gh auth status`, and that the owner, repo and number name an "
                      "existing PR or issue you can read, then re-run".format(
                          " ".join(args), done.stderr.strip() or "exit {}, no message".format(done.returncode)))
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise GhError("gh {} returned invalid JSON ({}). Run the same command by hand to see its output, update the "
                      "GitHub CLI if it is not JSON, then re-run".format(" ".join(args), exc)) from None


def _shape(ok, what):
    if not ok:
        raise GhError("gh returned {} in an unexpected shape; update the GitHub CLI (`gh --version`) and re-run".format(what))


def _absent_as(value, default):
    """`default` for a missing or null field; any other value, empty or not, as is."""
    return default if value is None else value


def read_pr(owner, repo, pr, gh):
    data = gh(["pr", "view", str(pr), "-R", "{}/{}".format(owner, repo),
               "--json", "state,body,closingIssuesReferences"], GH_TIMEOUT_SEC)
    _shape(isinstance(data, dict) and data.get("state") in PR_STATES, "the PR")
    refs = _absent_as(data.get("closingIssuesReferences"), [])
    body = _absent_as(data.get("body"), "")
    _shape(isinstance(refs, list) and isinstance(body, str), "the PR")
    closing = []
    for ref in refs:
        _shape(isinstance(ref, dict) and type(ref.get("number")) is int, "a closing issue reference")
        where = _absent_as(ref.get("repository"), {})
        _shape(isinstance(where, dict), "a closing issue's repository")
        who = _absent_as(where.get("owner"), {})
        _shape(isinstance(who, dict), "a closing issue's repository owner")
        owner_login = who.get("login") or owner
        closing.append({"repo": "{}/{}".format(owner_login, where.get("name") or repo),
                        "number": ref["number"], "state": None})
    return {
        "pr": int(pr),
        "state": data["state"],
        "closing": closing,
        "partial": sorted({int(n) for n in PART_OF.findall(body)}),
        "no_issue": bool(NO_ISSUE.search(body)),
    }


def issue_state(repo, number, gh, timeout):
    data = gh(["issue", "view", str(number), "-R", repo, "--json", "state"], timeout)
    _shape(isinstance(data, dict) and data.get("state") in ISSUE_STATES, "issue {}#{}".format(repo, number))
    return data["state"]


def check_linked(report):
    if report["closing"] or report["partial"] or report["no_issue"]:
        return "ok"
    return "unlinked"


def check_closed(report, gh, interval, budget, *, sleep=time.sleep, clock=time.monotonic):
    """Poll until every closing issue is CLOSED; no read starts after the budget."""
    if report["state"] != "MERGED":
        return "not_merged"
    deadline = clock() + budget
    while True:
        for issue in report["closing"]:
            if issue["state"] != "CLOSED":
                remaining = deadline - clock()
                if remaining <= 0:
                    return "still_open"
                issue["state"] = issue_state(issue["repo"], issue["number"], gh, min(GH_TIMEOUT_SEC, remaining))
        if all(issue["state"] == "CLOSED" for issue in report["closing"]):
            return "ok"
        remaining = deadline - clock()
        if remaining <= 0:
            return "still_open"
        sleep(min(interval, remaining))


DIAGNOSTICS = {
    "unlinked": ("PR #{pr} names no issue GitHub will close. Add `Closes #<n>` for each issue it "
                 "resolves, a whole `Part of #<n>` line for one it only partly resolves, or a whole "
                 "`No issue` line; `Refs`, `Related` and a bare `#<n>` do not close anything."),
    "not_merged": "PR #{pr} is {state}, not MERGED. Run --merged only after the merge lands.",
    "still_open": ("PR #{pr} merged but these closing issues are still open after {budget:g}s: {open}. "
                   "Check each issue's timeline for a reopen. Closing one with a comment is an action on "
                   "that repository: take it only where rules/external-repo-contributions.md permits it, "
                   "and otherwise report the open issue to the operator."),
}


def main(argv, gh=gh_json, sleep=time.sleep, clock=time.monotonic):
    args = [a for a in argv if a != "--merged"]
    merged = len(args) != len(argv)
    if len(args) != 3 or not args[2].isdigit():
        print("usage: check-closing-issues.py <owner> <repo> <pr-number> [--merged]", file=sys.stderr)
        return 2
    owner, repo, pr = args
    try:
        interval = env_seconds("CLOSE_INTERVAL_SEC", DEFAULT_INTERVAL_SEC)
        budget = env_seconds("CLOSE_BUDGET_SEC", DEFAULT_BUDGET_SEC)
        report = read_pr(owner, repo, pr, gh)
        verdict = check_closed(report, gh, interval, budget, sleep=sleep, clock=clock) if merged else check_linked(report)
    except (EnvError, GhError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    report["verdict"] = verdict
    print(json.dumps(report))
    if verdict != "ok":
        still = ", ".join("{}#{}".format(i["repo"], i["number"])
                          for i in report["closing"] if i["state"] != "CLOSED")
        print(DIAGNOSTICS[verdict].format(pr=report["pr"], state=report["state"],
                                          budget=budget, open=still), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
