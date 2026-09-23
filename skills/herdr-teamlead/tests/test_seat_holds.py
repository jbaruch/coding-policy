"""Developer reservations and busy workers come from owner records, not memory (#483)."""

import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.composition import seat_holds
from teamlead.errors import UsageError
from teamlead.recovery import close_task, developer_reservations, task_closure, validate_store
from teamlead.state import add_assignment, empty_state, load_state, save_state
from tests.test_cli import CliCase

DEV_AT = "2026-09-23T09:05:56+00:00"
REVIEW_AT = "2026-09-23T09:19:43+00:00"
CLOSE_AT = "2026-09-23T11:00:00+00:00"
REOPEN_AT = "2026-09-23T12:00:00+00:00"
MERGED = {"task": "media-77", "outcome": "merged", "evidence": "https://github.com/example/media/pull/77"}


def media_round():
    """The 09:22 near-miss: codex-census developed media-77 and awaits review."""
    state = empty_state()
    add_assignment(state, DEV_AT, "developer", "codex-census", task="media-77")
    add_assignment(state, REVIEW_AT, "reviewer", "claude-review", task="media-77")
    return state


class ReservationTest(unittest.TestCase):
    def test_developer_awaiting_review_is_held_to_its_task(self):
        state = media_round()
        self.assertEqual(developer_reservations(state["recovery"], state["assignments"]), {"codex-census": "media-77"})

    def test_closing_the_task_releases_its_developer(self):
        state = media_round()
        close_task(state["recovery"], state["assignments"], MERGED, CLOSE_AT)
        self.assertEqual(developer_reservations(state["recovery"], state["assignments"]), {})

    def test_a_developer_round_after_closure_reopens_the_hold(self):
        state = media_round()
        close_task(state["recovery"], state["assignments"], MERGED, CLOSE_AT)
        add_assignment(state, REOPEN_AT, "developer", "codex-census", task="media-77", fix_round=1)
        self.assertIsNone(task_closure(state["recovery"], state["assignments"], "media-77"))
        self.assertEqual(developer_reservations(state["recovery"], state["assignments"]), {"codex-census": "media-77"})

    def test_a_fourth_fix_takes_a_fresh_worker_so_nothing_is_held(self):
        state = empty_state()
        add_assignment(state, DEV_AT, "developer", "grok", task="t", fix_round=4)
        self.assertEqual(developer_reservations(state["recovery"], state["assignments"]), {})

    def test_a_later_assignment_of_the_worker_ends_the_hold(self):
        state = media_round()
        add_assignment(state, CLOSE_AT, "tester", "codex-census", task="other-task")
        self.assertEqual(developer_reservations(state["recovery"], state["assignments"]), {})

    def test_another_worker_reopening_the_task_does_not_re_hold_the_first(self):
        state = media_round()
        close_task(state["recovery"], state["assignments"], MERGED, CLOSE_AT)
        add_assignment(state, REOPEN_AT, "developer", "grok", task="media-77")
        self.assertEqual(developer_reservations(state["recovery"], state["assignments"]), {"grok": "media-77"})

    def test_a_malformed_closure_is_refused_rather_than_releasing_anyone(self):
        state = media_round()
        close_task(state["recovery"], state["assignments"], MERGED, CLOSE_AT)
        for broken in ({"outcome": "done", "evidence": "x"}, {"outcome": "merged", "evidence": " "},
                       {"outcome": "merged"}):
            with self.subTest(details=broken):
                store = copy.deepcopy(state["recovery"])
                store["events"][-1]["details"] = broken
                with self.assertRaisesRegex(UsageError, "Task-closed event 1 is malformed"):
                    validate_store(store, state["assignments"])
        validate_store(state["recovery"], state["assignments"])

    def test_repeating_a_closure_is_idempotent_and_a_different_one_is_refused(self):
        state = media_round()
        first = close_task(state["recovery"], state["assignments"], MERGED, CLOSE_AT)
        again = close_task(state["recovery"], state["assignments"], dict(MERGED), REOPEN_AT)
        self.assertEqual(first, again)
        self.assertEqual(len(state["recovery"]["events"]), 1)
        with self.assertRaisesRegex(UsageError, "already closed"):
            close_task(state["recovery"], state["assignments"], {**MERGED, "outcome": "abandoned"}, REOPEN_AT)

    def test_malformed_or_unknown_closures_are_refused(self):
        state = media_round()
        for data, message in (({**MERGED, "extra": 1}, "exactly task, outcome and evidence"),
                              ({**MERGED, "outcome": "done"}, "outcome must be one of"),
                              ({**MERGED, "task": "never-dispatched"}, "no recorded assignment")):
            with self.subTest(message=message), self.assertRaisesRegex(UsageError, message):
                close_task(state["recovery"], state["assignments"], data, CLOSE_AT)
        self.assertEqual(state["recovery"]["events"], [])


class SeatHoldsTest(unittest.TestCase):
    def test_reserved_developer_is_barred_from_other_tasks(self):
        result = seat_holds(["tester", "reviewer"], "telegram-lint-zero", {"codex-census": "media-77"}, {})
        self.assertEqual(result["exclude"], {"tester": ["codex-census"], "reviewer": ["codex-census"]})
        self.assertIn("close-task", result["rationale"][0])

    def test_reserved_developer_may_take_its_own_fix_and_release(self):
        result = seat_holds(["developer", "release", "reviewer"], "media-77", {"codex-census": "media-77"}, {})
        self.assertEqual(result["exclude"], {"developer": [], "release": [], "reviewer": ["codex-census"]})

    def test_busy_worker_is_barred_from_every_seat(self):
        result = seat_holds(["developer", "reviewer#api"], "t", {}, {"claude": "fleet-deps-admin"})
        self.assertEqual(result["exclude"], {"developer": ["claude"], "reviewer#api": ["claude"]})
        self.assertIn("supervision-resolve", result["rationale"][0])


class PlanHoldsTest(CliCase):
    def plan(self, task="other-task"):
        return self.run_cli(self.base() + ["plan", "--roles", "developer", "--snapshot", str(self.snapshot),
                                           "--task", task])

    def seed(self):
        state = empty_state()
        add_assignment(state, DEV_AT, "developer", "grok", task="media-77")
        save_state(self.state, state)

    def test_plan_skips_the_reserved_developer_and_says_why(self):
        self.seed()
        code, out, err = self.plan()
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertNotEqual(result["assignments"]["developer"], "grok")
        self.assertTrue(any("reserved as developer for media-77" in line for line in result["rationale"]))

    def test_plan_offers_the_developer_again_once_its_task_closes(self):
        self.seed()
        record = self.tmp / "close.json"
        record.write_text(json.dumps(MERGED), encoding="utf-8")
        code, out, err = self.run_cli(self.base() + ["close-task", "--record", str(record), "--now", CLOSE_AT])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["kind"], "task_closed")
        self.assertEqual(load_state(self.state)["recovery"]["events"][-1]["task"], "media-77")
        self.out, self.err = io.StringIO(), io.StringIO()
        code, out, err = self.plan()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["assignments"]["developer"], "grok")

    def test_plan_skips_a_busy_worker(self):
        member = {"active": True, "assignment": {"agent": "grok", "task": "fleet-deps-admin"}}
        with patch("teamlead.cli.supervision.load", return_value={"members": [member]}):
            code, out, err = self.plan()
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertNotEqual(result["assignments"]["developer"], "grok")
        self.assertTrue(any("busy on the active enrollment for task fleet-deps-admin" in line
                            for line in result["rationale"]))


if __name__ == "__main__":
    unittest.main()
