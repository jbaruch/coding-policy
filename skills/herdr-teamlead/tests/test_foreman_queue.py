"""The foreman's queue is derived from owner records, never from its memory (#483)."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.foreman_queue import waiting
from teamlead.recovery import close_task, register_task
from teamlead.state import add_assignment, empty_state, save_state
from tests.test_cli import CliCase

DEV = "2026-09-23T09:00:00+00:00"
REVIEW = "2026-09-23T09:10:00+00:00"
TEST = "2026-09-23T09:20:00+00:00"


def developed(task="t", at=DEV, agent="grok", fix_round=None):
    state = empty_state()
    add_assignment(state, at, "developer", agent, task=task, fix_round=fix_round)
    return state


def verified(state, task="t"):
    add_assignment(state, REVIEW, "reviewer", "claude", task=task)
    add_assignment(state, TEST, "tester", "codex", task=task)
    return state


def register(state, task, at=DEV):
    register_task(state["recovery"], {"task": task, "base_revision": "a" * 40, "scope": "s", "allowed_paths": ["src/*"],
                                      "authorization": {"source": "operator", "quote": "go"}}, at)
    return state


def stages(state, busy=()):
    return [(entry["task"], entry["waiting_for"]) for entry in waiting(state["recovery"], state["assignments"], set(busy))["queue"]]


class StageTest(unittest.TestCase):
    def test_a_registered_task_without_a_developer_waits_for_one(self):
        state = register(empty_state(), "new")
        entry = waiting(state["recovery"], state["assignments"], set())["queue"][0]
        self.assertEqual((entry["task"], entry["waiting_for"], entry["since"]), ("new", ["developer"], DEV))

    def test_a_developed_task_waits_for_both_verifiers(self):
        self.assertEqual(stages(developed()), [("t", ["reviewer", "tester"])])

    def test_one_verifier_dispatched_leaves_the_other(self):
        state = developed()
        add_assignment(state, REVIEW, "reviewer", "claude", task="t")
        self.assertEqual(stages(state), [("t", ["tester"])])

    def test_both_verifiers_dispatched_leaves_no_seat_to_wait_for(self):
        self.assertEqual(stages(verified(developed())), [])

    def test_a_new_developer_round_reopens_the_verifier_seats(self):
        state = verified(developed())
        add_assignment(state, "2026-09-23T10:00:00+00:00", "developer", "grok", task="t", fix_round=1)
        self.assertEqual(stages(state), [("t", ["reviewer", "tester"])])

    def test_a_partitioned_verifier_stays_listed_with_its_dispatched_slices(self):
        state = developed()
        add_assignment(state, REVIEW, "reviewer#api", "claude", task="t")
        state["recovery"]["dispatches"].append({"task": "t", "role": "reviewer#api", "status": "applied",
                                                "assignment_index": 1})
        add_assignment(state, TEST, "tester", "codex", task="t")
        entry = waiting(state["recovery"], state["assignments"], set())["queue"][0]
        self.assertEqual((entry["waiting_for"], entry["dispatched_seats"]),
                         (["reviewer"], {"reviewer": ["reviewer#api"], "tester": ["tester"]}))

    def test_a_closed_task_leaves_the_queue(self):
        state = developed()
        close_task(state["recovery"], state["assignments"],
                   {"task": "t", "outcome": "abandoned", "evidence": "dropped"}, TEST)
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

    def test_an_unusable_state_file_fails_instead_of_an_empty_queue(self):
        self.state.write_text('{"schema_version": 2, broken', encoding="utf-8")
        code, _, err = self.run_cli(self.base() + ["foreman-queue"])
        self.assertEqual(code, 1)
        self.assertIn("unusable", err)

    def test_empty_state_has_an_empty_queue(self):
        self.out, self.err = io.StringIO(), io.StringIO()
        code, out, err = self.run_cli(self.base() + ["foreman-queue"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"schema_version": 1, "queue": []})


if __name__ == "__main__":
    unittest.main()
