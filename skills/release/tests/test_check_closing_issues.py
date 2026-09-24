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
from unittest import mock

_SCRIPT = _os.path.join(_ROOT, "check-closing-issues.py")
_SPEC = importlib.util.spec_from_file_location("check_closing_issues", _SCRIPT)
assert _SPEC and _SPEC.loader, f"cannot load check-closing-issues.py at {_SCRIPT}"
# Typed Any: the module's attributes are only known once it executes.
check: Any = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check)


class FakeClock:
    """Time that moves only when the script sleeps or a gh call costs time."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeGh:
    """Serves `gh pr view` from one PR record and `gh issue view` from a state script."""

    def __init__(self, pr, issue_states=None, clock=None, call_cost=0.0):
        self.pr = pr
        self.issue_states = {k: list(v) for k, v in (issue_states or {}).items()}
        self.calls = []
        self.clock = clock
        self.call_cost = call_cost

    def __call__(self, args, timeout):
        self.calls.append((args, timeout))
        if self.clock is not None:
            self.clock.now += self.call_cost
        if args[:2] == ["pr", "view"]:
            return self.pr
        if args[:2] == ["issue", "view"]:
            key = "{}#{}".format(args[4], args[2])
            states = self.issue_states[key]
            return {"state": states.pop(0) if len(states) > 1 else states[0]}
        raise AssertionError("unexpected gh call: {}".format(args))


def ref(number, owner="jbaruch", name="coding-policy"):
    return {"number": number, "repository": {"name": name, "owner": {"login": owner}}}


def run(fake, *argv, clock=None, env=None):
    clock = clock or FakeClock()
    out, err = io.StringIO(), io.StringIO()
    environ = {"CLOSE_INTERVAL_SEC": "5", "CLOSE_BUDGET_SEC": "60", **(env or {})}
    with mock.patch.dict(_os.environ, environ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = check.main(["jbaruch", "coding-policy"] + list(argv), gh=fake, sleep=clock.sleep, clock=clock)
    report: Any = json.loads(out.getvalue()) if out.getvalue() else None
    return code, report, err.getvalue()


def open_pr(body, refs=()):
    return {"state": "OPEN", "body": body, "closingIssuesReferences": list(refs)}


def merged_pr(refs):
    return {"state": "MERGED", "body": "", "closingIssuesReferences": list(refs)}


class BeforeMerge(unittest.TestCase):
    def test_refs_only_links_nothing_and_is_refused(self):
        code, report, err = run(FakeGh(open_pr("## Summary\n- x\n\nRefs #479\n")), "12")
        self.assertEqual((code, report["verdict"], report["closing"]), (1, "unlinked", []))
        self.assertIn("Closes #<n>", err)

    def test_a_closing_reference_passes(self):
        code, report, _ = run(FakeGh(open_pr("Closes #479\n", [ref(479)])), "12")
        self.assertEqual((code, report["verdict"], report["closing"][0]["number"]), (0, "ok", 479))

    def test_a_whole_part_of_line_declares_a_partial_resolution(self):
        code, report, _ = run(FakeGh(open_pr("## Summary\n\nPart of #483  \n")), "12")
        self.assertEqual((code, report["partial"]), (0, [483]))

    def test_a_whole_no_issue_line_passes(self):
        code, report, _ = run(FakeGh(open_pr("## Summary\n\nNo issue\n")), "12")
        self.assertEqual((code, report["no_issue"]), (0, True))

    def test_lines_that_only_start_like_the_alternatives_do_not_count(self):
        for body in ("This is part of #483 work.\n", "Part of #483 remains open\n", "No issue found\n"):
            with self.subTest(body=body):
                code, _, _ = run(FakeGh(open_pr(body)), "12")
                self.assertEqual(code, 1)


class AfterMerge(unittest.TestCase):
    def test_closed_issues_pass(self):
        fake = FakeGh(merged_pr([ref(1)]), {"jbaruch/coding-policy#1": ["CLOSED"]})
        code, report, _ = run(fake, "12", "--merged")
        self.assertEqual((code, report["closing"][0]["state"]), (0, "CLOSED"))

    def test_an_issue_that_closes_on_a_later_read_passes_after_one_interval(self):
        clock = FakeClock()
        fake = FakeGh(merged_pr([ref(1)]), {"jbaruch/coding-policy#1": ["OPEN", "CLOSED"]})
        code, _, _ = run(fake, "12", "--merged", clock=clock)
        self.assertEqual((code, clock.sleeps), (0, [5.0]))

    def test_an_issue_still_open_at_budget_is_named_with_the_permission_boundary(self):
        clock = FakeClock()
        fake = FakeGh(merged_pr([ref(1), ref(2, owner="other", name="repo")]),
                      {"jbaruch/coding-policy#1": ["CLOSED"], "other/repo#2": ["OPEN"]})
        code, report, err = run(fake, "12", "--merged", clock=clock, env={"CLOSE_BUDGET_SEC": "12"})
        self.assertEqual((code, report["verdict"]), (1, "still_open"))
        self.assertIn("other/repo#2", err)
        self.assertNotIn("coding-policy#1", err)
        self.assertIn("external-repo-contributions", err)
        # Two full intervals, then the last sleep is capped at the 2s left.
        self.assertEqual(clock.sleeps, [5.0, 5.0, 2.0])

    def test_no_read_starts_after_the_budget_and_no_call_outlives_it(self):
        clock = FakeClock()
        fake = FakeGh(merged_pr([ref(1)]), {"jbaruch/coding-policy#1": ["OPEN"]}, clock=clock, call_cost=4.0)
        code, _, _ = run(fake, "12", "--merged", clock=clock, env={"CLOSE_BUDGET_SEC": "10"})
        self.assertEqual(code, 1)
        timeouts = [timeout for args, timeout in fake.calls if args[:2] == ["issue", "view"]]
        # pr view costs 4s before the deadline is set; t=4 read (10s left), sleep 5 -> t=13 read (1s left), t=17 past it.
        self.assertEqual(timeouts, [10.0, 1.0])

    def test_an_unmerged_pr_is_refused(self):
        fake = FakeGh({"state": "OPEN", "body": "Closes #1", "closingIssuesReferences": [ref(1)]})
        code, report, _ = run(fake, "12", "--merged")
        self.assertEqual((code, report["verdict"]), (1, "not_merged"))


class Errors(unittest.TestCase):
    def test_non_numeric_pr_is_a_usage_error(self):
        code, report, err = run(FakeGh({}), "abc")
        self.assertEqual((code, report), (2, None))
        self.assertIn("usage", err)

    def test_gh_failure_is_exit_2_with_a_diagnostic(self):
        def broken(args, timeout):
            raise check.GhError("gh pr view failed: HTTP 404")
        code, report, err = run(broken, "12")
        self.assertEqual((code, report), (2, None))
        self.assertIn("HTTP 404", err)

    def test_unusable_poll_overrides_are_exit_2_naming_the_variable(self):
        cases = {"CLOSE_INTERVAL_SEC": ["x", "0", "-1", "nan", "inf"], "CLOSE_BUDGET_SEC": ["x", "0", "-1", "nan", "inf"]}
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    code, report, err = run(FakeGh(merged_pr([])), "12", "--merged", env={name: value})
                    self.assertEqual((code, report), (2, None))
                    self.assertIn(name, err)

    def test_unexpected_response_shapes_are_exit_2_not_a_traceback(self):
        shapes = [[], {"state": 3}, {"state": "OPEN", "closingIssuesReferences": {}},
                  {"state": "OPEN", "closingIssuesReferences": [{"number": "1"}]},
                  {"state": "OPEN", "closingIssuesReferences": [{"number": 1, "repository": []}]}]
        for shape in shapes:
            with self.subTest(shape=shape):
                code, report, err = run(FakeGh(shape), "12")
                self.assertEqual((code, report), (2, None))
                self.assertIn("unexpected shape", err)

    def test_an_issue_state_of_the_wrong_shape_is_exit_2(self):
        def gh(args, timeout):
            return merged_pr([ref(1)]) if args[:2] == ["pr", "view"] else {"state": None}
        code, report, err = run(gh, "12", "--merged")
        self.assertEqual((code, report), (2, None))
        self.assertIn("issue jbaruch/coding-policy#1", err)


if __name__ == "__main__":
    unittest.main()
