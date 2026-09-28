"""Resource use per task through acceptance comes from the owner records (#602)."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman.cost_report import UNRECORDED, report
from foreman.errors import UsageError
from foreman.recovery import close_task, register_task
from foreman.state import add_assignment, add_snapshot, empty_state, save_state
from tests.test_cli import CliCase

BEFORE = "2026-09-23T08:00:00+00:00"
DEV = "2026-09-23T09:00:00+00:00"
FIX = "2026-09-23T10:00:00+00:00"
REVIEW = "2026-09-23T10:30:00+00:00"
CLOSE = "2026-09-23T11:00:00+00:00"
AFTER = "2026-09-23T12:00:00+00:00"
LATE = "2026-09-23T13:00:00+00:00"


def snapshot(at, **agents):
    """`agents` maps a name to `(remaining_pct, resets, window_group)`."""
    return {"schema_version": 3, "measured_at": at, "agents": {
        name: {"kind": "codex", "state": "idle", "headroom_pct": pct, "skipped": False, "tier_billing": {},
               "window_group": group, "windows": {"weekly": {"used_pct": 100 - pct, "remaining_pct": pct, "resets": resets}}}
        for name, (pct, resets, group) in agents.items()}}


def delivered(close="merged"):
    state = empty_state()
    add_assignment(state, DEV, "developer", "codex", task="t")
    add_assignment(state, FIX, "developer", "codex", task="t", fix_round=2)
    add_assignment(state, REVIEW, "reviewer", "claude", task="t")
    add_assignment(state, REVIEW, "tester", "grok", "sent_but_not_started", task="t")
    if close:
        close_task(state["recovery"], state["assignments"], {"task": "t", "outcome": close, "evidence": "pr"}, CLOSE)
    return state


def only(state, **kwargs):
    return report(state, **kwargs)["tasks"][0]


class ReportTest(unittest.TestCase):
    def test_an_accepted_task_reports_each_quantity_separately(self):
        state = delivered()
        state["recovery"]["events"].append({"schema_version": 13, "sequence": 99, "at": FIX,
                                            "kind": "dispatch_transport_retry", "task": "t", "details": {}})
        entry = only(state)
        self.assertEqual((entry["status"], entry["started_at"], entry["ended_at"]), ("accepted", DEV, CLOSE))
        self.assertEqual(entry["elapsed_seconds"], 7200.0)
        self.assertEqual(entry["correction_rounds"], 2)
        self.assertEqual(entry["work"], {"developer": 2, "reviewer": 1})
        self.assertEqual(entry["coordination"], {"transport_retries": 1, "dispatches_not_sent": 0, "provider_refusals": 0,
                                                 "unstarted_assignments": 1, "foreman_tokens": "unknown"})
        self.assertEqual(entry["tokens"], {"uncached_input": "unknown", "cached_input": "unknown", "output": "unknown"})

    def test_every_unrecorded_field_is_named(self):
        self.assertEqual(report(delivered())["unrecorded"], list(UNRECORDED))

    def test_an_open_task_has_no_elapsed_time(self):
        entry = only(delivered(close=None))
        self.assertEqual((entry["status"], entry["elapsed_seconds"], entry["ended_at"]), ("open", "unknown", None))
        self.assertEqual({row["reason"] for row in entry["windows"]}, {"task_open"})

    def test_an_abandoned_task_is_not_reported_as_accepted(self):
        self.assertEqual(only(delivered(close="abandoned"))["status"], "abandoned")

    def test_work_after_acceptance_is_not_counted(self):
        state = delivered()
        state["recovery"]["events"].append({"schema_version": 13, "sequence": 99, "at": LATE,
                                            "kind": "dispatch_not_sent", "task": "t", "details": {}})
        self.assertEqual(only(state)["coordination"]["dispatches_not_sent"], 0)

    def test_an_unshared_idle_window_is_attributed_to_the_task(self):
        state = delivered()
        add_snapshot(state, snapshot(BEFORE, codex=(90, "Sep 30", "")))
        add_snapshot(state, snapshot(AFTER, codex=(80, "Sep 30", "")))
        window = [row for row in only(state)["windows"] if row["pool"] == "codex"][0]
        self.assertEqual((window["consumed_pct"], window["attribution"], window["reason"]), (10, "task", None))

    def test_a_shared_window_keeps_its_movement_but_not_its_attribution(self):
        state = delivered()
        add_snapshot(state, snapshot(BEFORE, codex=(90, "Sep 30", "openai"), spare=(90, "Sep 30", "openai")))
        add_snapshot(state, snapshot(AFTER, codex=(70, "Sep 30", "openai"), spare=(70, "Sep 30", "openai")))
        window = [row for row in only(state)["windows"] if row["pool"] == "openai"][0]
        self.assertEqual((window["consumed_pct"], window["attribution"], window["reason"]), (20, "unknown", "shared_window"))

    def test_another_tasks_work_on_the_window_leaves_attribution_unknown(self):
        state = delivered()
        add_assignment(state, FIX, "reviewer", "codex", task="other")
        add_snapshot(state, snapshot(BEFORE, codex=(90, "Sep 30", "")))
        add_snapshot(state, snapshot(AFTER, codex=(80, "Sep 30", "")))
        window = [row for row in report(state, task="t")["tasks"][0]["windows"] if row["pool"] == "codex"][0]
        self.assertEqual((window["attribution"], window["reason"]), ("unknown", "concurrent_work"))

    def test_a_reset_between_readings_makes_the_movement_unknown(self):
        state = delivered()
        add_snapshot(state, snapshot(BEFORE, codex=(20, "Sep 23", "")))
        add_snapshot(state, snapshot(AFTER, codex=(95, "Sep 30", "")))
        window = [row for row in only(state)["windows"] if row["pool"] == "codex"][0]
        self.assertEqual((window["consumed_pct"], window["reason"]), ("unknown", "window_reset"))

    def test_a_missing_bracketing_snapshot_is_named(self):
        state = delivered()
        add_snapshot(state, snapshot(AFTER, codex=(80, "Sep 30", "")))
        window = [row for row in only(state)["windows"] if row["pool"] == "codex"][0]
        self.assertEqual((window["consumed_pct"], window["reason"]), ("unknown", "no_snapshot_before"))

    def test_an_unknown_task_is_refused(self):
        with self.assertRaisesRegex(UsageError, "no registration or assignment"):
            report(delivered(), task="missing")

    def test_a_registered_task_without_work_starts_at_registration(self):
        state = empty_state()
        register_task(state["recovery"], {"task": "new", "base_revision": "a" * 40, "scope": "s", "allowed_paths": ["src/*"],
                                          "authorization": {"source": "operator", "quote": "go"}}, DEV)
        entry = only(state)
        self.assertEqual((entry["started_at"], entry["correction_rounds"], entry["work"], entry["windows"]),
                         (DEV, 0, {}, []))


class CostReportCommandTest(CliCase):
    def test_command_reports_from_the_saved_state(self):
        save_state(self.state, delivered())
        code, out, err = self.run_cli(self.base() + ["cost-report", "--task", "t"])
        self.assertEqual(code, 0, err)
        document = json.loads(out)
        self.assertEqual([(entry["task"], entry["status"]) for entry in document["tasks"]], [("t", "accepted")])

    def test_an_unusable_state_file_fails_instead_of_reporting_nothing(self):
        self.state.write_text('{"schema_version": 2, broken', encoding="utf-8")
        code, _, err = self.run_cli(self.base() + ["cost-report"])
        self.assertEqual(code, 1)
        self.assertIn("unusable", err)

    def test_the_report_writes_nothing(self):
        save_state(self.state, delivered())
        before = self.state.read_bytes()
        code, _, err = self.run_cli(self.base() + ["cost-report"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
