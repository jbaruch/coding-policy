"""The foreman's queue is derived from owner records, never from its memory (#483)."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.foreman_queue import waiting
from teamlead.recovery import close_task
from teamlead.state import add_assignment, empty_state, save_state
from tests.test_cli import CliCase

DEV = "2026-09-23T09:00:00+00:00"
REVIEW = "2026-09-23T09:10:00+00:00"
TEST = "2026-09-23T09:20:00+00:00"
REPORT = "2026-09-23T09:30:00+00:00"
RELEASE = "2026-09-23T09:40:00+00:00"
CLOSE = "2026-09-23T09:50:00+00:00"


def developed(task="t", at=DEV, agent="grok", fix_round=None):
    state = empty_state()
    add_assignment(state, at, "developer", agent, task=task, fix_round=fix_round)
    return state


def verified(state, task="t"):
    add_assignment(state, REVIEW, "reviewer", "claude", task=task)
    add_assignment(state, TEST, "tester", "codex", task=task)
    return state


def approve(state, task="t"):
    state["recovery"]["dispatches"].append({"task": task, "role": "developer", "status": "applied",
                                            "report": {"verdict": "approved", "at": REPORT}})
    return state


def stages(state, busy=()):
    return [(entry["task"], entry["waiting_for"]) for entry in waiting(state["recovery"], state["assignments"], set(busy))["queue"]]


class StageTest(unittest.TestCase):
    def test_a_developed_task_waits_for_its_verifiers(self):
        result = waiting(developed()["recovery"], developed()["assignments"], set())["queue"][0]
        self.assertEqual((result["waiting_for"], result["also_missing"]), ("reviewer", ["tester"]))

    def test_one_verifier_dispatched_leaves_the_other(self):
        state = developed()
        add_assignment(state, REVIEW, "reviewer", "claude", task="t")
        self.assertEqual(stages(state), [("t", "tester")])

    def test_both_verifiers_without_an_approved_report_wait_for_the_gate(self):
        self.assertEqual(stages(verified(developed())), [("t", "gate")])

    def test_an_approved_report_waits_for_release(self):
        self.assertEqual(stages(approve(verified(developed()))), [("t", "release")])

    def test_an_approval_for_an_earlier_round_does_not_count(self):
        state = approve(verified(developed()))
        add_assignment(state, "2026-09-23T10:00:00+00:00", "developer", "grok", task="t", fix_round=1)
        add_assignment(state, "2026-09-23T10:10:00+00:00", "reviewer", "claude", task="t")
        add_assignment(state, "2026-09-23T10:20:00+00:00", "tester", "codex", task="t")
        self.assertEqual(stages(state), [("t", "gate")])

    def test_a_release_waits_for_the_task_to_be_closed(self):
        state = approve(verified(developed()))
        add_assignment(state, RELEASE, "release", "grok", task="t")
        self.assertEqual(stages(state), [("t", "close")])

    def test_a_closed_task_leaves_the_queue(self):
        state = approve(verified(developed()))
        add_assignment(state, RELEASE, "release", "grok", task="t")
        close_task(state["recovery"], state["assignments"],
                   {"task": "t", "outcome": "merged", "evidence": "pr"}, CLOSE)
        self.assertEqual(stages(state), [])

    def test_a_task_in_flight_is_not_waiting(self):
        self.assertEqual(stages(developed(), busy={"t"}), [])

    def test_oldest_wait_comes_first_across_offsets(self):
        state = developed(task="late", at="2026-09-23T11:00:00+02:00")
        add_assignment(state, "2026-09-23T09:30:00+00:00", "developer", "codex", task="early")
        self.assertEqual([task for task, _ in stages(state)], ["late", "early"])


class ForemanQueueCommandTest(CliCase):
    def test_command_lists_the_queue_and_omits_busy_tasks(self):
        state = developed(task="waiting")
        add_assignment(state, DEV, "developer", "codex", task="busy")
        save_state(self.state, state)
        member = {"active": True, "assignment": {"agent": "codex", "task": "busy"}}
        with patch("teamlead.cli.supervision.load", return_value={"members": [member]}):
            code, out, err = self.run_cli(self.base() + ["foreman-queue"])
        self.assertEqual(code, 0, err)
        self.assertEqual([entry["task"] for entry in json.loads(out)["queue"]], ["waiting"])

    def test_empty_state_has_an_empty_queue(self):
        self.out, self.err = io.StringIO(), io.StringIO()
        code, out, err = self.run_cli(self.base() + ["foreman-queue"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"schema_version": 1, "queue": []})


if __name__ == "__main__":
    unittest.main()
