"""close-member and check-member compose the per-report chains in one owner call (#508)."""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teamlead import members
from teamlead import supervision as store
from teamlead.errors import UsageError
from teamlead.state import empty_state

AT = "2026-09-01T12:00:00+00:00"
LATER = "2026-09-01T12:05:00+00:00"


def ledger_text(task, events, state: "str | Path" = "/state.json", version="1"):
    lines = ["---", "schema_version: " + version, "task: " + task, "base_revision: " + "a" * 40,
             "dispatch_state: " + str(state), "---", "", "# Task Ledger", ""]
    for event in events:
        lines.append("## " + event["id"])
        lines.append("")
        for key, value in event.items():
            lines.append("- {}: {}".format(key, value))
        lines.append("")
    return "\n".join(lines)


class MembersCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="teamlead-members-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        who = store.identity("lead-session", str(self.root), "fixture", pane_id="lead-pane")
        store.bind(self.path, who, AT, root=self.root / "bindings")
        self.report = str(self.root / "reviewer.md")
        store.enroll(self.path, {"id": "dispatch-a", "agent": "codex-a", "task": "task-a", "report": self.report,
                                 "pane_id": "pane-a", "native_session": None}, AT)
        self.ledger = self.root / "TASK-LEDGER.md"

    def write_ledger(self, *decisions, task="task-a", report=None, dispatch="dispatch-a", state=None, version="1",
                     drop=None):
        events = [{"schema_version": 1, "id": "event-{}".format(index), "at": AT, "subject": "assignment",
                   "dispatch_id": dispatch, "worker": "codex-a", "role": "reviewer", "report": report or self.report,
                   "observed": "report delivered", "decision": decision, "head_revision": "unknown",
                   "evidence": self.report, "assessment": "read in full"}
                  for index, decision in enumerate(decisions, 1)]
        if drop is not None:
            for event in events:
                event.pop(drop, None)
        self.ledger.write_text(ledger_text(task, events, state or self.path, version))

    def emit(self):
        store.transaction(self.path, lambda data: store.append_event(data, AT, "dispatch-a", "report", {"observation_only": True}))


class CloseMemberTest(MembersCase):
    def test_refuses_before_the_ledger_records_an_assessed_outcome(self):
        self.emit()
        for decisions in ((), ("reported",), ("accepted", "reported")):
            with self.subTest(decisions=decisions):
                self.write_ledger(*decisions)
                with self.assertRaisesRegex(UsageError, "no assessed outcome"):
                    members.close(self.path, "dispatch-a", self.ledger, LATER)
                data = store.load(self.path)
                self.assertTrue(store.pending(data))
                self.assertTrue(next(row for row in data["members"] if row["id"] == "dispatch-a")["active"])

    def test_acknowledges_this_members_events_and_resolves_it(self):
        self.emit()
        store.enroll(self.path, {"id": "dispatch-b", "agent": "codex-b", "task": "task-a", "report": str(self.root / "b.md"),
                                 "pane_id": "pane-b", "native_session": None}, AT)
        store.transaction(self.path, lambda data: store.append_event(data, AT, "dispatch-b", "report", {"observation_only": True}))
        self.write_ledger("reported", "accepted")
        result = members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertEqual((result["decision"], result["ledger_event"]), ("accepted", "event-2"))
        self.assertEqual(len(result["acknowledged"]), 1)
        data = store.load(self.path)
        self.assertEqual([row["member"] for row in store.pending(data)], ["dispatch-b"])
        self.assertFalse(next(row for row in data["members"] if row["id"] == "dispatch-a")["active"])

    def test_a_repeated_close_replays(self):
        self.emit()
        self.write_ledger("needs_work")
        first = members.close(self.path, "dispatch-a", self.ledger, LATER)
        again = members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertEqual(again["resolved"], first["resolved"])
        self.assertEqual(again["acknowledged"], [])

    def test_a_ledger_for_another_task_is_refused(self):
        self.write_ledger("accepted", task="task-z")
        with self.assertRaisesRegex(UsageError, "records task 'task-z'"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)

    def test_an_event_for_another_dispatch_does_not_count(self):
        self.write_ledger("accepted", dispatch="dispatch-earlier")
        with self.assertRaisesRegex(UsageError, "missing"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)

    def test_an_unusable_ledger_closes_nothing(self):
        self.emit()
        for kwargs, why in (({"state": "/elsewhere/state.json"}, "bound to dispatch state"),
                            ({"version": "2"}, "is schema 2"), ({"drop": "evidence"}, "lacks evidence")):
            with self.subTest(why=why):
                self.write_ledger("accepted", **kwargs)
                with self.assertRaisesRegex(UsageError, why):
                    members.close(self.path, "dispatch-a", self.ledger, LATER)
                self.assertTrue(store.pending(store.load(self.path)))

    def test_an_event_for_another_report_does_not_count(self):
        self.write_ledger("accepted", report=str(self.root / "other.md"))
        with self.assertRaisesRegex(UsageError, "missing"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)


class CheckMemberTest(MembersCase):
    def test_passes_base_send_time_and_worktree_from_the_records(self):
        state = empty_state()
        state["recovery"]["tasks"]["task-a"] = {"task": "task-a", "base_revision": "b" * 40}
        state["recovery"]["dispatches"] += [
            {"id": "dispatch-earlier", "agent": "codex-a", "task": "task-a", "report": self.report, "status": "applied",
             "result": {"at": AT}},
            {"id": "dispatch-a", "agent": "codex-a", "task": "task-a", "report": self.report, "status": "applied",
             "result": {"at": LATER}}]
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 1, stdout='{"found": false}', stderr="pending")

        with patch("teamlead.members.load_state_checked", return_value=(state, True)):
            payload, code = members.check(self.path, "dispatch-a", "/work/tree", run=run)
        argv = calls[0]
        self.assertEqual(argv[2:], ["--once", "--worktree", "/work/tree", "--base", "b" * 40, "--since", LATER,
                                    "codex-a", self.report])
        self.assertTrue(argv[1].endswith("wait-report.sh"))
        self.assertEqual((code, payload["exit"], payload["wait"]), (1, 1, '{"found": false}'))

    def test_a_dispatch_without_a_send_time_is_refused_before_waiting(self):
        with patch("teamlead.members.load_state_checked", return_value=(empty_state(), True)), \
             self.assertRaisesRegex(UsageError, "no applied send time"):
            members.check(self.path, "dispatch-a", run=subprocess.run)

    def test_an_unknown_enrollment_is_refused(self):
        with self.assertRaisesRegex(UsageError, "Unknown enrollment"):
            members.check(self.path, "dispatch-z", run=subprocess.run)

    def test_an_unusable_state_is_refused_with_its_repair(self):
        from teamlead.errors import StateError
        with patch("teamlead.members.load_state_checked", return_value=(empty_state(), False)), \
             self.assertRaisesRegex(StateError, "restore it"):
            members.check(self.path, "dispatch-a", run=subprocess.run)


class CheckMemberCliTest(MembersCase):
    def run_cli(self, code):
        import io
        import json as _json
        from teamlead.cli import main
        payload = {"schema_version": 1, "enrollment": "dispatch-a", "inputs": {}, "exit": code, "wait": "",
                   "diagnostics": "wait-report: agent not found"}
        out, err = io.StringIO(), io.StringIO()
        with patch("teamlead.members.check", return_value=(payload, code)):
            rc = main(["--state", str(self.path), "--config", str(self.root / "config.json"), "check-member",
                       "--enrollment", "dispatch-a"], stdout=out, stderr=err)
        return rc, _json.loads(out.getvalue()), err.getvalue()

    def test_a_verdict_exits_zero_with_it_in_the_payload(self):
        for code in (0, 1, 3, 4, 5):
            with self.subTest(code=code):
                rc, payload, _ = self.run_cli(code)
                self.assertEqual((rc, payload["exit"]), (0, code))

    def test_a_wait_that_did_not_run_fails_the_command(self):
        rc, payload, err = self.run_cli(2)
        self.assertEqual((rc, payload["exit"]), (1, 2))
        self.assertIn("wait_failed", err)
        self.assertIn("agent not found", err)


if __name__ == "__main__":
    unittest.main()
