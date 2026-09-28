"""Report gates: a Jev label may add friction to accepting a report, never remove it."""

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import cli, members, report_gates as gates  # noqa: E402 -- the skill dir is on sys.path only from here
from foreman.errors import StateError, UsageError  # noqa: E402
from tests.test_members import LATER, MembersCase  # noqa: E402

AT = "2026-09-27T12:00:00+00:00"
HIGH = {"names_open_item": 0.995, "open_items_accepted": 0.01, "open_items_out_of_scope": 0.02,
        "concludes_nothing_blocks": 0.1}
MEDIUM = {**HIGH, "names_open_item": 0.93}
LOW = {**HIGH, "names_open_item": 0.6}


def label(report, probabilities, **overrides):
    data = Path(report).read_bytes()
    value = {"schema_version": 2, "report": str(report), "sha256": hashlib.sha256(data).hexdigest(),
             "question": "q" * 64, "agent": "jev", "model": gates.JEV_MODEL, "verdict": "blocking",
             "fallback": None, "answers": {qid: {"answer": gates.band(p), "evidence": "", "p_yes": p}
                                           for qid, p in probabilities.items()}}
    value.update(overrides)
    return value


class DecideTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="report-gates-")
        self.addCleanup(temporary.cleanup)
        self.report = Path(temporary.name) / "report.md"
        self.report.write_text("B1 blocking.\n")

    def test_confidence_decides_the_level(self):
        self.assertEqual(gates.decide(label(self.report, HIGH))["level"], "block")
        self.assertEqual(gates.decide(label(self.report, MEDIUM))["level"], "reread")
        self.assertIsNone(gates.decide(label(self.report, LOW))["level"])

    def test_a_disposed_item_earns_no_block(self):
        disposed = {**HIGH, "open_items_accepted": 0.5}
        self.assertIsNone(gates.decide(label(self.report, disposed))["level"])

    def test_only_a_pinned_jev_label_with_a_verdict_can_gate(self):
        for overrides in ({"agent": "claude"}, {"fallback": {"from": "jev", "reason": "key unset"}},
                          {"model": "jev-latest"}, {"verdict": "insufficient_evidence"}, {"answers": {}}):
            with self.subTest(overrides=overrides):
                self.assertIsNone(gates.decide(label(self.report, HIGH, **overrides))["level"])


class GateCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="report-gates-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state.json"
        self.report = self.root / "reviewer.md"
        self.report.write_text("B1 blocking: the parser accepts a quoted marker.\n")
        self.reread = self.root / "reviewer-reread.md"
        self.reread.write_text("Re-read reviewer.md in full at the current tip; B1 stands as blocking.\n")

    def record(self, *labels):
        return gates.record(self.state, {"schema_version": 2, "agent": "default", "labels": list(labels),
                                         "unannotated": []}, AT)


class RecordTest(GateCase):
    def test_records_a_gate_and_replays_the_same_bytes(self):
        first = self.record(label(self.report, HIGH))
        self.assertEqual([gate["level"] for gate in first["recorded"]], ["block"])
        again = self.record(label(self.report, HIGH))
        self.assertEqual((again["recorded"], len(again["replayed"])), ([], 1))

    def test_an_ungated_label_is_reported_not_recorded(self):
        result = self.record(label(self.report, HIGH, agent="claude"))
        self.assertEqual(result["recorded"], [])
        self.assertIn("never gates", result["no_gate"][0]["reason"])
        self.assertFalse(gates.storage_path(self.state).exists())

    def test_a_report_changed_since_classification_is_refused(self):
        stale = label(self.report, HIGH)
        self.report.write_text("rewritten\n")
        with self.assertRaisesRegex(UsageError, "changed since it was classified"):
            self.record(stale)
        self.assertFalse(gates.storage_path(self.state).exists())

    def test_an_unreadable_sidecar_is_never_read_as_no_gates(self):
        gates.storage_path(self.state).write_text("{broken")
        with self.assertRaises(StateError):
            gates.require_clear(self.state, str(self.report), True)


class ClearTest(GateCase):
    def test_a_block_refuses_acceptance_until_a_recorded_clear(self):
        self.record(label(self.report, HIGH))
        gates.require_clear(self.state, str(self.report), False)
        with self.assertRaisesRegex(UsageError, "cannot be accepted until the block is cleared"):
            gates.require_clear(self.state, str(self.report), True)
        with self.assertRaisesRegex(UsageError, "a re-read does not clear it"):
            gates.resolve(self.state, str(self.report), "reread", "read it", "worker", AT, str(self.reread))
        result = gates.resolve(self.state, str(self.report), "clear", "B1 is advisory per the judge ruling", "operator", AT)
        self.assertEqual(result["resolved"][0]["resolution"]["by"], "operator")
        gates.require_clear(self.state, str(self.report), True)

    def test_a_reread_refuses_any_gating_until_recorded(self):
        self.record(label(self.report, MEDIUM))
        for accepting in (False, True):
            with self.subTest(accepting=accepting), self.assertRaisesRegex(UsageError, "open re-read gate"):
                gates.require_clear(self.state, str(self.report), accepting)
        gates.resolve(self.state, str(self.report), "reread", "Read in full; B1 is real.", "worker", AT, str(self.reread))
        gates.require_clear(self.state, str(self.report), True)
        resolution = gates.status(self.state)["resolved"][0]["resolution"]
        self.assertEqual(resolution["evidence"]["path"], str(self.reread))
        self.assertEqual(gates.status(self.state)["open"], [])

    def test_a_resolution_needs_an_open_gate_a_reason_and_its_source(self):
        with self.assertRaisesRegex(UsageError, "no open gate"):
            gates.resolve(self.state, str(self.report), "clear", "why", "operator", AT)
        self.record(label(self.report, HIGH))
        refused = [("clear", "  ", "operator", None), ("clear", "why", "foreman", None),
                   ("clear", "why", "classifier", None), ("clear", "why", "worker", None),
                   ("clear", "why", "judge", str(self.root / "absent.md")), ("reread", "why", "operator", None),
                   ("reread", "why", "worker", str(self.report))]
        for action, reason, by, evidence in refused:
            with self.subTest(action=action, by=by, evidence=evidence), self.assertRaises(UsageError):
                gates.resolve(self.state, str(self.report), action, reason, by, AT, evidence)
        self.assertEqual(len(gates.status(self.state)["open"]), 1)

    def test_the_cli_records_clears_and_lists(self):
        labels = self.root / "labels.json"
        labels.write_text(json.dumps({"labels": [label(self.report, HIGH)]}))
        base = ["--state", str(self.state)]

        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            return cli.main([*args, *base], stdout=out, stderr=err), json.loads(out.getvalue() or "null"), err.getvalue()

        code, payload, err = run("report-gate-record", "--labels", str(labels), "--now", AT)
        self.assertEqual(code, 0, err)
        self.assertEqual(payload["recorded"][0]["level"], "block")
        code, payload, err = run("report-gate-status")
        self.assertEqual((code, len(payload["open"])), (0, 1), err)
        code, payload, err = run("report-gate-clear", "--report", str(self.report), "--by", "worker",
                                 "--evidence", str(self.reread), "--reason", "B1 is advisory: presentation only",
                                 "--now", AT)
        self.assertEqual(code, 0, err)
        code, payload, err = run("report-gate-status")
        self.assertEqual((len(payload["open"]), len(payload["resolved"])), (0, 1))


class CloseMemberGateTest(MembersCase):
    def gate(self, probabilities):
        Path(self.report).write_text("B1 blocking: the parser accepts a quoted marker.\n")
        gates.record(self.path, {"labels": [label(self.report, probabilities)]}, "2026-09-01T12:01:00+00:00")

    def test_an_open_block_refuses_an_accepted_closure(self):
        self.emit()
        self.gate(HIGH)
        self.write_ledger("accepted")
        with self.assertRaisesRegex(UsageError, "cannot be accepted"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        ruling = self.root / "judge.md"
        ruling.write_text("RULING: uphold B -- B1 lies outside this task's scope.\n")
        gates.resolve(self.path, self.report, "clear", "The finding is out of scope per ruling R2.", "judge", LATER,
                      str(ruling))
        self.assertEqual(members.close(self.path, "dispatch-a", self.ledger, LATER)["decision"], "accepted")

    def test_a_block_never_refuses_a_needs_work_closure(self):
        self.emit()
        self.gate(HIGH)
        self.write_ledger("needs_work")
        self.assertEqual(members.close(self.path, "dispatch-a", self.ledger, LATER)["decision"], "needs_work")

    def test_an_open_reread_refuses_every_closure(self):
        self.emit()
        self.gate(MEDIUM)
        self.write_ledger("needs_work")
        with self.assertRaisesRegex(UsageError, "re-read gate"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
