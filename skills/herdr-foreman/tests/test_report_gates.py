"""Report gates: a Jev label may add friction to accepting a report, never remove it."""

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


    def test_a_new_classification_of_the_same_bytes_records_a_fresh_gate(self):
        self.record(label(self.report, HIGH))
        gates.resolve(self.state, str(self.report), "clear", "B1 is advisory per the judge ruling", "operator", AT)
        again = self.record(label(self.report, HIGH, question="r" * 64))
        self.assertEqual((len(again["recorded"]), again["replayed"]), (1, []))
        with self.assertRaisesRegex(UsageError, "cannot be accepted"):
            gates.require_clear(self.state, str(self.report), True)

    def test_a_live_sidecar_link_is_refused_and_left_in_place(self):
        elsewhere = self.root / "elsewhere.json"
        elsewhere.write_text(json.dumps({"schema_version": gates.SCHEMA_VERSION,
                                         "state_path": str(self.state), "gates": []}))
        gates.storage_path(self.state).symlink_to(elsewhere)
        with self.assertRaisesRegex(StateError, "symlink"):
            gates.require_clear(self.state, str(self.report), True)
        self.assertTrue(gates.storage_path(self.state).is_symlink())

    def test_a_non_canonical_report_path_is_a_malformed_record(self):
        self.record(label(self.report, HIGH))
        sidecar = gates.storage_path(self.state)
        document = json.loads(sidecar.read_text())
        document["gates"][0]["report"] = str(self.root / "sub" / ".." / "reviewer.md")
        sidecar.write_text(json.dumps(document))
        with self.assertRaisesRegex(StateError, "canonical"):
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


class ShapeTest(GateCase):
    """A saved gate record is validated whole; a malformed one refuses every reader."""

    def saved(self):
        self.record(label(self.report, HIGH))
        return json.loads(gates.storage_path(self.state).read_text())

    def test_every_malformed_record_is_a_state_error(self):
        breakages = {
            "missing report": lambda gate: gate.pop("report"),
            "relative report": lambda gate: gate.update(report="reviewer.md"),
            "bad sha256": lambda gate: gate.update(sha256="xyz"),
            "unknown level": lambda gate: gate.update(level="warn"),
            "empty reason": lambda gate: gate.update(reason=""),
            "probability out of range": lambda gate: gate["probabilities"].update(names_open_item=1.5),
            "missing probability": lambda gate: gate["probabilities"].pop("open_items_accepted"),
            "extra field": lambda gate: gate.update(note="x"),
            "open with a resolution": lambda gate: gate.update(resolution={}),
            "cleared without a resolution": lambda gate: gate.update(status="cleared"),
            "resolution action disagrees": lambda gate: gate.update(status="cleared", resolution={
                "schema_version": 1, "at": AT, "action": "reread", "by": "worker", "reason": "r",
                "evidence": {"path": "/r.md", "sha256": "a" * 64}}),
            "worker clear without evidence": lambda gate: gate.update(status="cleared", resolution={
                "schema_version": 1, "at": AT, "action": "clear", "by": "worker", "reason": "r", "evidence": None}),
            "resolution without schema_version": lambda gate: gate.update(status="cleared", resolution={
                "at": AT, "action": "clear", "by": "operator", "reason": "r", "evidence": None}),
            "resolution with a newer schema_version": lambda gate: gate.update(status="cleared", resolution={
                "schema_version": 2, "at": AT, "action": "clear", "by": "operator", "reason": "r",
                "evidence": None}),
        }
        for name, breakage in breakages.items():
            with self.subTest(name=name):
                document = self.saved()
                breakage(document["gates"][0])
                gates.storage_path(self.state).write_text(json.dumps(document))
                for read in (lambda: gates.require_clear(self.state, str(self.report), False),
                             lambda: gates.status(self.state),
                             lambda: gates.resolve(self.state, str(self.report), "clear", "why", "operator", AT)):
                    with self.assertRaisesRegex(StateError, "record 0 is malformed"):
                        read()
                gates.storage_path(self.state).unlink()

    def test_a_valid_resolved_record_reads(self):
        self.record(label(self.report, HIGH))
        gates.resolve(self.state, str(self.report), "clear", "advisory only", "worker", AT, str(self.reread))
        resolved = gates.status(self.state)["resolved"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["resolution"]["schema_version"], gates.SCHEMA_VERSION)


class InterleavingTest(GateCase):
    """A gate recorded while an acceptance is being committed is refused, never slipped in."""

    def test_record_report_holds_the_gate_lock_through_its_commit(self):
        attempts = []

        def racing(args, state_path, warn, client, trace):
            # A second process records a gate between the check and the commit.
            try:
                gates.record(state_path, {"labels": [label(self.report, HIGH)]}, AT)
                attempts.append("recorded")
            except StateError as exc:
                attempts.append(exc.message)
            return {}, None

        receipt = self.root / "receipt.json"
        receipt.write_text("{}")
        with mock.patch.object(cli, "_run_recovery", side_effect=racing):
            code = cli.main(["record-report", "--record", str(receipt), "--state", str(self.state), "--now", AT],
                            stdout=io.StringIO(), stderr=io.StringIO())
        self.assertEqual(code, 0)
        self.assertEqual(len(attempts), 1)
        self.assertIn("Another foreman command owns", attempts[0])
        self.assertEqual(gates.status(self.state)["open"], [])


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

    def test_a_gate_recorded_mid_closure_is_refused(self):
        self.emit()
        Path(self.report).write_text("B1 blocking: the parser accepts a quoted marker.\n")
        self.write_ledger("accepted")
        attempts = []
        real_resolve = members.supervision.resolve

        def racing(*args, **kwargs):
            try:
                gates.record(self.path, {"labels": [label(self.report, HIGH)]}, LATER)
                attempts.append("recorded")
            except StateError as exc:
                attempts.append(exc.message)
            return real_resolve(*args, **kwargs)

        with mock.patch.object(members.supervision, "resolve", side_effect=racing):
            self.assertEqual(members.close(self.path, "dispatch-a", self.ledger, LATER)["decision"], "accepted")
        self.assertEqual(len(attempts), 1)
        self.assertIn("Another foreman command owns", attempts[0])
        self.assertEqual(gates.status(self.path)["open"], [])

    def test_an_open_reread_refuses_every_closure(self):
        self.emit()
        self.gate(MEDIUM)
        self.write_ledger("needs_work")
        with self.assertRaisesRegex(UsageError, "re-read gate"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
