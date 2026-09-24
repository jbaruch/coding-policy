#!/usr/bin/env python3
"""Check that a PR links the issues it resolves, and that they closed on merge.

GitHub closes an issue on merge only when the PR body names it with a closing
keyword (`Closes #N`, `Fixes #N`, `Resolves #N`). `Refs #N`, `Related #N` and a
bare `#N` are mentions: the issue stays open after the merge, and the next
triage has to rediscover that the work already shipped (#517).

The PR body template in `skills/release/SKILL.md` Step 2 carries one of three
issue lines. This script reads what GitHub resolved from them.

Modes:
  default (before merge)
    Reads `closingIssuesReferences`. Passes when it names at least one issue,
    or when the body carries a `Part of #N` line (a partial resolution) or a
    `No issue` line (work with no tracking issue). Refuses otherwise: the body
    names no issue GitHub will close and declares no alternative.
  --merged (after merge)
    Requires the PR to be MERGED, then reads each closing issue's state until
    every one is CLOSED or the budget runs out. GitHub closes linked issues
    asynchronously, so a first read right after the merge can still see OPEN.

Poll interval and budget are script-owned constants, overridable by the
environment for tests: CLOSE_INTERVAL_SEC, CLOSE_BUDGET_SEC.

Usage: check-closing-issues.py <owner> <repo> <pr-number> [--merged]

Output contract (rules/script-delegation.md -- structured stdout):
  stdout: one JSON object --
    {"pr": N, "state": "OPEN|MERGED|CLOSED", "closing": [{"repo": "o/r",
     "number": N, "state": "OPEN|CLOSED|null"}], "partial": [N, ...],
     "no_issue": bool, "verdict": "ok|unlinked|still_open|not_merged"}
  stderr: an actionable diagnostic for every non-ok verdict.

Exit: 0 on verdict ok; 1 on a refusing verdict; 2 on a usage or `gh` error.
"""

import json
import os
import re
import subprocess
import sys
import time

CLOSE_INTERVAL_SEC = float(os.environ.get("CLOSE_INTERVAL_SEC", "5"))
CLOSE_BUDGET_SEC = float(os.environ.get("CLOSE_BUDGET_SEC", "60"))

# The two non-closing issue lines the Step 2 template allows. Anchored to a
# whole line so prose that happens to contain the words does not count.
PART_OF = re.compile(r"^\s*Part of\s+#(\d+)\b", re.MULTILINE)
NO_ISSUE = re.compile(r"^\s*No issue\b", re.MULTILINE)


class GhError(Exception):
    """`gh` failed or returned something that is not the expected JSON."""


def gh_json(args):
    """Run `gh` and parse its JSON stdout. Replaced by tests."""
    try:
        done = subprocess.run(["gh"] + args, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise GhError("gh is not installed; install the GitHub CLI and run `gh auth login`") from None
    if done.returncode != 0:
        raise GhError("gh {} failed: {}".format(" ".join(args), done.stderr.strip()))
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise GhError("gh {} returned invalid JSON: {}".format(" ".join(args), exc)) from None


def read_pr(owner, repo, pr):
    data = gh_json(["pr", "view", str(pr), "-R", "{}/{}".format(owner, repo),
                    "--json", "state,body,closingIssuesReferences"])
    closing = []
    for ref in data.get("closingIssuesReferences") or []:
        where = ref.get("repository") or {}
        owner_login = (where.get("owner") or {}).get("login", owner)
        closing.append({"repo": "{}/{}".format(owner_login, where.get("name", repo)),
                        "number": ref["number"], "state": None})
    body = data.get("body") or ""
    return {
        "pr": int(pr),
        "state": data["state"],
        "closing": closing,
        "partial": sorted({int(n) for n in PART_OF.findall(body)}),
        "no_issue": bool(NO_ISSUE.search(body)),
    }


def issue_state(repo, number):
    return gh_json(["issue", "view", str(number), "-R", repo, "--json", "state"])["state"]


def check_linked(report):
    if report["closing"] or report["partial"] or report["no_issue"]:
        return "ok"
    return "unlinked"


def check_closed(report, sleep=time.sleep, clock=time.monotonic):
    if report["state"] != "MERGED":
        return "not_merged"
    deadline = clock() + CLOSE_BUDGET_SEC
    while True:
        for issue in report["closing"]:
            if issue["state"] != "CLOSED":
                issue["state"] = issue_state(issue["repo"], issue["number"])
        if all(issue["state"] == "CLOSED" for issue in report["closing"]):
            return "ok"
        if clock() >= deadline:
            return "still_open"
        sleep(CLOSE_INTERVAL_SEC)


DIAGNOSTICS = {
    "unlinked": ("PR #{pr} names no issue GitHub will close. Add `Closes #<n>` for each issue it "
                 "resolves, `Part of #<n>` for one it only partly resolves, or a `No issue` line; "
                 "`Refs`, `Related` and a bare `#<n>` do not close anything."),
    "not_merged": "PR #{pr} is {state}, not MERGED. Run --merged only after the merge lands.",
    "still_open": ("PR #{pr} merged but these closing issues are still open after {budget:g}s: {open}. "
                   "Check the issue timeline for a reopen, then close each with a comment naming the PR."),
}


def main(argv):
    args = [a for a in argv if a != "--merged"]
    merged = len(args) != len(argv)
    if len(args) != 3 or not args[2].isdigit():
        print("usage: check-closing-issues.py <owner> <repo> <pr-number> [--merged]", file=sys.stderr)
        return 2
    owner, repo, pr = args
    try:
        report = read_pr(owner, repo, pr)
        verdict = check_closed(report) if merged else check_linked(report)
    except GhError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    report["verdict"] = verdict
    print(json.dumps(report))
    if verdict != "ok":
        still = ", ".join("{}#{}".format(i["repo"], i["number"])
                          for i in report["closing"] if i["state"] != "CLOSED")
        print(DIAGNOSTICS[verdict].format(pr=report["pr"], state=report["state"],
                                          budget=CLOSE_BUDGET_SEC, open=still), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
