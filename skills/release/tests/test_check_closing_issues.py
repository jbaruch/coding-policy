#!/usr/bin/env python3
"""A PR must name the issues it resolves so GitHub closes them on merge (#517)."""

import os as _os

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

import contextlib
import importlib.util
import io
import json
import unittest
from typing import Any

_SCRIPT = _os.path.join(_ROOT, "check-closing-issues.py")
_SPEC = importlib.util.spec_from_file_location("check_closing_issues", _SCRIPT)
assert _SPEC and _SPEC.loader, f"cannot load check-closing-issues.py at {_SCRIPT}"
# Typed Any: the module's attributes are only known once it executes.
check: Any = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check)


class FakeGh:
    """Serves `gh pr view` from one PR record and `gh issue view` from a state script."""

    def __init__(self, pr, issue_states=None):
        self.pr = pr
        self.issue_states = {k: list(v) for k, v in (issue_states or {}).items()}
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["pr", "view"]:
            return self.pr
        if args[:2] == ["issue", "view"]:
            key = "{}#{}".format(args[4], args[2])
            states = self.issue_states[key]
            return {"state": states.pop(0) if len(states) > 1 else states[0]}
        raise AssertionError("unexpected gh call: {}".format(args))


def ref(number, owner="jbaruch", name="coding-policy"):
    return {"number": number, "repository": {"name": name, "owner": {"login": owner}}}


def run(fake, *argv):
    out, err = io.StringIO(), io.StringIO()
    saved = check.gh_json, check.CLOSE_INTERVAL_SEC, check.CLOSE_BUDGET_SEC
    check.gh_json, check.CLOSE_INTERVAL_SEC, check.CLOSE_BUDGET_SEC = fake, 0, 0.05
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check.main(["jbaruch", "coding-policy"] + list(argv))
    finally:
        check.gh_json, check.CLOSE_INTERVAL_SEC, check.CLOSE_BUDGET_SEC = saved
    report: Any = json.loads(out.getvalue()) if out.getvalue() else None
    return code, report, err.getvalue()


class BeforeMerge(unittest.TestCase):
    def test_refs_only_links_nothing_and_is_refused(self):
        fake = FakeGh({"state": "OPEN", "body": "## Summary\n- x\n\nRefs #479\n",
                       "closingIssuesReferences": []})
        code, report, err = run(fake, "12")
        self.assertEqual(code, 1)
        self.assertEqual(report["verdict"], "unlinked")
        self.assertEqual(report["closing"], [])
        self.assertIn("Closes #<n>", err)

    def test_a_closing_reference_passes(self):
        fake = FakeGh({"state": "OPEN", "body": "Closes #479\n",
                       "closingIssuesReferences": [ref(479)]})
        code, report, _ = run(fake, "12")
        self.assertEqual((code, report["verdict"]), (0, "ok"))
        self.assertEqual(report["closing"][0]["number"], 479)

    def test_part_of_line_declares_a_partial_resolution(self):
        fake = FakeGh({"state": "OPEN", "body": "## Summary\n\nPart of #483\n",
                       "closingIssuesReferences": []})
        code, report, _ = run(fake, "12")
        self.assertEqual((code, report["partial"]), (0, [483]))

    def test_part_of_inside_prose_does_not_count(self):
        fake = FakeGh({"state": "OPEN", "body": "This is part of #483 work.\n",
                       "closingIssuesReferences": []})
        code, _, _ = run(fake, "12")
        self.assertEqual(code, 1)

    def test_no_issue_line_passes(self):
        fake = FakeGh({"state": "OPEN", "body": "## Summary\n\nNo issue\n",
                       "closingIssuesReferences": []})
        code, report, _ = run(fake, "12")
        self.assertEqual((code, report["no_issue"]), (0, True))


class AfterMerge(unittest.TestCase):
    def test_closed_issues_pass(self):
        fake = FakeGh({"state": "MERGED", "body": "Closes #1", "closingIssuesReferences": [ref(1)]},
                      {"jbaruch/coding-policy#1": ["CLOSED"]})
        code, report, _ = run(fake, "12", "--merged")
        self.assertEqual((code, report["closing"][0]["state"]), (0, "CLOSED"))

    def test_an_issue_that_closes_on_a_later_read_passes(self):
        fake = FakeGh({"state": "MERGED", "body": "Closes #1", "closingIssuesReferences": [ref(1)]},
                      {"jbaruch/coding-policy#1": ["OPEN", "CLOSED"]})
        code, _, _ = run(fake, "12", "--merged")
        self.assertEqual(code, 0)

    def test_an_issue_still_open_at_budget_is_named(self):
        fake = FakeGh({"state": "MERGED", "body": "Closes #1\nCloses #2",
                       "closingIssuesReferences": [ref(1), ref(2, owner="other", name="repo")]},
                      {"jbaruch/coding-policy#1": ["CLOSED"], "other/repo#2": ["OPEN"]})
        code, report, err = run(fake, "12", "--merged")
        self.assertEqual((code, report["verdict"]), (1, "still_open"))
        self.assertIn("other/repo#2", err)
        self.assertNotIn("coding-policy#1", err)

    def test_an_unmerged_pr_is_refused(self):
        fake = FakeGh({"state": "OPEN", "body": "Closes #1", "closingIssuesReferences": [ref(1)]})
        code, report, _ = run(fake, "12", "--merged")
        self.assertEqual((code, report["verdict"]), (1, "not_merged"))


class Usage(unittest.TestCase):
    def test_non_numeric_pr_is_a_usage_error(self):
        code, report, err = run(FakeGh({}), "abc")
        self.assertEqual((code, report), (2, None))
        self.assertIn("usage", err)

    def test_gh_failure_is_exit_2_with_a_diagnostic(self):
        def broken(args):
            raise check.GhError("gh pr view failed: HTTP 404")
        code, report, err = run(broken, "12")
        self.assertEqual((code, report), (2, None))
        self.assertIn("HTTP 404", err)


if __name__ == "__main__":
    unittest.main()
