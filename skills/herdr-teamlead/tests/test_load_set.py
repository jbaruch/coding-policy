"""Each foreman decision loads the owner-linked records it depends on (#483)."""

import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.errors import UsageError
from teamlead.load_set import build
from teamlead.recovery import register_task
from teamlead.state import add_assignment, empty_state, save_state
from tests.test_cli import CliCase

DEV_0 = "2026-09-23T08:00:00+00:00"
REVIEW_0 = "2026-09-23T08:30:00+00:00"
DEV_1 = "2026-09-23T09:00:00+00:00"
REVIEW_1 = "2026-09-23T09:30:00+00:00"
TEST_1 = "2026-09-23T09:40:00+00:00"


def dispatch(state, at, role, agent, task="t", fix_round=None, **extra):
    add_assignment(state, at, role, agent, task=task, fix_round=fix_round)
    identifier = "{}-{}:{}".format(task, len(state["assignments"]), role)
    state["recovery"]["dispatches"].append({"id": identifier, "task": task, "role": role, "agent": agent,
                                            "status": "applied", "assignment_index": len(state["assignments"]) - 1,
                                            "brief": "/r/{}.brief.md".format(identifier), "common": "/r/COMMON.md",
                                            "report": None, **extra})
    return identifier


def two_rounds():
    state = empty_state()
    register_task(state["recovery"], {"task": "t", "base_revision": "a" * 40, "scope": "fix it", "allowed_paths": ["src/*"],
                                      "authorization": {"source": "operator", "quote": "go"}}, DEV_0)
    ids = {"dev0": dispatch(state, DEV_0, "developer", "grok"),
           "rev0": dispatch(state, REVIEW_0, "reviewer", "claude"),
           "dev1": dispatch(state, DEV_1, "developer", "grok", fix_round=1),
           "rev1": dispatch(state, REVIEW_1, "reviewer", "claude"),
           "test1": dispatch(state, TEST_1, "tester", "codex")}
    state["recovery"]["dispatches"][0]["report"] = {"verdict": "blocking", "head_revision": "b" * 40,
                                                    "report": "/r/review-blocking.md"}
    return state, ids


def reports(ids):
    return {identifier: "/r/{}.report.md".format(identifier) for identifier in ids.values()}


def paths(result):
    return [row["path"] for row in result["files"]]


def run(state, ids, decision, **target):
    return build(state, reports(ids), {}, set(), decision, exists=lambda path: path != "/r/missing.md", **target)


class DecisionTest(unittest.TestCase):
    def test_gate_loads_every_brief_and_report_of_the_current_round_only(self):
        state, ids = two_rounds()
        result = run(state, ids, "gate", task="t")
        self.assertEqual(result["round_start"], DEV_1)
        for key in ("dev1", "rev1", "test1"):
            self.assertIn("/r/{}.brief.md".format(ids[key]), paths(result))
            self.assertIn("/r/{}.report.md".format(ids[key]), paths(result))
        self.assertNotIn("/r/{}.report.md".format(ids["rev0"]), paths(result))
        self.assertEqual(paths(result).count("/r/COMMON.md"), 1)

    def test_brief_loads_this_rounds_reports_and_every_blocking_receipt(self):
        state, ids = two_rounds()
        result = run(state, ids, "brief", task="t")
        self.assertIn("/r/review-blocking.md", paths(result))
        self.assertIn("/r/{}.report.md".format(ids["rev1"]), paths(result))
        self.assertIn("/r/{}.brief.md".format(ids["dev1"]), paths(result))
        self.assertIn("/r/{}.brief.md".format(ids["rev1"]), paths(result))
        self.assertIn("correction_plan", result["records"])

    def test_diagnose_loads_every_round(self):
        state, ids = two_rounds()
        result = run(state, ids, "diagnose", task="t")
        for key in ids:
            self.assertIn("/r/{}.report.md".format(ids[key]), paths(result))
        self.assertIn("/r/review-blocking.md", paths(result))
        self.assertEqual(set(result["records"]), {"assessments", "checkpoints", "diagnoses", "approaches", "plans",
                                                   "historical_attempts"})

    def imported(self, state, at, verdict):
        add_assignment(state, at, "developer", "grok", task="t", fix_round=2)
        attempt = {"id": "manual-2", "task": "t", "assignment_index": len(state["assignments"]) - 1,
                   "receipts": {"report": {"path": "/r/manual-2.md", "sha256": "0" * 64}},
                   "reviews": [{"input": {"verdict": verdict, "head_revision": "d" * 40, "report": "/r/manual-2-review.md"}}]}
        state["recovery"]["historical_attempts"].append(attempt)
        return attempt

    def test_an_imported_current_round_is_gated_and_diagnosed(self):
        state, ids = two_rounds()
        attempt = self.imported(state, "2026-09-23T10:00:00+00:00", "blocking")
        gate = run(state, ids, "gate", task="t")
        self.assertEqual(gate["records"]["historical_attempts"], [attempt])
        self.assertIn("/r/manual-2.md", paths(gate))
        self.assertIn("/r/manual-2-review.md", paths(gate))
        self.assertNotIn("/r/{}.report.md".format(ids["rev1"]), paths(gate))
        self.assertIn("/r/manual-2-review.md", paths(run(state, ids, "brief", task="t")))
        self.assertIn("/r/manual-2.md", paths(run(state, ids, "diagnose", task="t")))

    def test_an_unknown_send_outcome_refuses_round_decisions(self):
        state, ids = two_rounds()
        state["recovery"]["dispatches"][-1]["status"] = "sent_but_not_started"
        for decision in ("brief", "gate", "diagnose"):
            with self.subTest(decision=decision), self.assertRaisesRegex(UsageError, "unknown send outcome: " + ids["test1"]):
                run(state, ids, decision, task="t")
        self.assertIsNotNone(run(state, ids, "plan", task="t"))

    def test_a_dispatch_without_an_enrollment_refuses_round_decisions(self):
        state, ids = two_rounds()
        paths_by_id = reports(ids)
        del paths_by_id[ids["rev1"]]
        for decision in ("brief", "gate", "diagnose"):
            with self.subTest(decision=decision), self.assertRaisesRegex(UsageError, "no supervision enrollment"):
                build(state, paths_by_id, {}, set(), decision, task="t", exists=lambda path: True)

    def test_a_spent_correction_plan_is_not_offered(self):
        state, ids = two_rounds()
        plan = {"id": "p1", "task": "t", "first_fix": 1, "last_fix": 1, "supersedes": None}
        state["recovery"]["plans"].append(plan)
        self.assertIsNone(run(state, ids, "brief", task="t")["records"]["correction_plan"])
        plan["last_fix"] = 2
        self.assertEqual(run(state, ids, "brief", task="t")["records"]["correction_plan"], plan)

    def test_diagnose_loads_superseded_receipts_and_assessment_records(self):
        state, ids = two_rounds()
        state["recovery"]["events"].append({"sequence": 1, "kind": "review_superseded", "task": "t", "at": REVIEW_1,
                                            "details": {"previous": {"verdict": "approved", "head_revision": "c" * 40,
                                                                     "report": "/r/superseded.md"}}})
        assessment = {"task": "t", "role": "investigator", "report": "/r/investigation.md", "outcome": "accepted",
                      "summary": "Loop stalls on flaky fixture."}
        state["specialist_assessments"].append(assessment)
        result = run(state, ids, "diagnose", task="t")
        self.assertIn("/r/superseded.md", paths(result))
        self.assertIn("/r/investigation.md", paths(result))
        self.assertEqual(result["records"]["assessments"], [assessment])

    def test_plan_names_the_queue_entry_and_the_reserved_developer(self):
        state = empty_state()
        dispatch(state, DEV_0, "developer", "grok")
        result = build(state, {}, {}, set(), "plan", task="t", exists=lambda path: True)
        self.assertEqual(result["records"]["queue"]["waiting_for"], ["reviewer", "tester"])
        self.assertEqual(result["records"]["reserved_developer"], ["grok"])

    def test_wake_is_keyed_by_enrollment(self):
        state, ids = two_rounds()
        result = run(state, ids, "wake", enrollment=ids["rev1"])
        self.assertEqual(result["task"], "t")
        self.assertEqual(paths(result), ["/r/{}.brief.md".format(ids["rev1"]), "/r/COMMON.md",
                                         "/r/{}.report.md".format(ids["rev1"])])

    def test_a_missing_report_is_listed_not_dropped(self):
        state, ids = two_rounds()
        result = build(state, {**reports(ids), ids["rev1"]: "/r/missing.md"}, {}, set(), "gate", task="t",
                       exists=lambda path: path != "/r/missing.md")
        self.assertIn({"path": "/r/missing.md", "why": "report for reviewer " + ids["rev1"], "present": False},
                      result["files"])

    def test_core_carries_open_attention_for_the_task_only(self):
        state, ids = two_rounds()
        entries = {"q": {"id": "q", "kind": "question", "title": "Advisory?", "status": "open", "task": "t"},
                   "done": {"id": "done", "kind": "question", "title": "x", "status": "resolved", "task": "t"},
                   "other": {"id": "other", "kind": "blocker", "title": "y", "status": "open", "task": "u"}}
        result = build(state, reports(ids), entries, set(), "gate", task="t", exists=lambda path: True)
        self.assertEqual(result["core"]["attention"], [entries["q"]])
        self.assertEqual(result["core"]["task"]["scope"], "fix it")


class LoadSetCommandTest(CliCase):
    def test_wake_needs_an_enrollment_and_the_rest_need_a_task(self):
        save_state(self.state, empty_state())
        for argv in (["--decision", "wake", "--task", "t"], ["--decision", "gate", "--enrollment", "e"]):
            with self.subTest(argv=argv):
                code, _, err = self.run_cli(self.base() + ["load-set", *argv])
                self.assertEqual(code, 1)
                self.assertIn("--enrollment for wake and --task", err)

    def test_command_returns_the_load_set(self):
        state = empty_state()
        register_task(state["recovery"], {"task": "t", "base_revision": "a" * 40, "scope": "fix it",
                                          "allowed_paths": ["src/*"], "authorization": {"source": "operator", "quote": "go"}}, DEV_0)
        add_assignment(state, DEV_0, "developer", "grok", task="t")
        save_state(self.state, state)
        code, out, err = self.run_cli(self.base() + ["load-set", "--decision", "plan", "--task", "t"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["decision"], result["core"]["task"]["scope"]), ("plan", "fix it"))
        self.assertEqual(result["records"]["reserved_developer"], ["grok"])

    def test_an_unknown_task_or_enrollment_fails(self):
        save_state(self.state, empty_state())
        for argv, message in ((["--decision", "plan", "--task", "ghost"], "neither registered nor assigned"),
                              (["--decision", "wake", "--enrollment", "ghost"], "no supervision enrollment")):
            with self.subTest(argv=argv):
                self.out, self.err = io.StringIO(), io.StringIO()
                code, _, err = self.run_cli(self.base() + ["load-set", *argv])
                self.assertEqual(code, 1)
                self.assertIn(message, err)

    def test_the_command_creates_no_files(self):
        state = empty_state()
        register_task(state["recovery"], {"task": "t", "base_revision": "a" * 40, "scope": "fix it",
                                          "allowed_paths": ["src/*"], "authorization": {"source": "operator", "quote": "go"}}, DEV_0)
        add_assignment(state, DEV_0, "developer", "grok", task="t")
        save_state(self.state, state)
        before = sorted(self.tmp.rglob("*"))
        code, _, err = self.run_cli(self.base() + ["load-set", "--decision", "plan", "--task", "t"])
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(self.tmp.rglob("*")), before)

    def test_an_unusable_state_file_fails(self):
        self.state.write_text('{"schema_version": 2, broken', encoding="utf-8")
        code, _, err = self.run_cli(self.base() + ["load-set", "--decision", "plan", "--task", "t"])
        self.assertEqual(code, 1)
        self.assertIn("unusable", err)


if __name__ == "__main__":
    unittest.main()
