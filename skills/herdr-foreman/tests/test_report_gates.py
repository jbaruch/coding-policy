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

from foreman import attention, cli, members, report_gates as gates, supervision  # noqa: E402 -- the skill dir is on sys.path only from here
from foreman.errors import StateError, UsageError  # noqa: E402
from tests.test_members import LATER, MembersCase, seed_contract  # noqa: E402
from foreman import supervision as store  # noqa: E402

AT = "2026-09-27T12:00:00+00:00"
AFTER = "2026-09-27T12:10:00+00:00"
BEFORE = "2026-09-27T11:50:00+00:00"
JUDGE = "claude-judge"
HIGH = {"names_open_item": 0.995, "open_items_accepted": 0.01, "open_items_out_of_scope": 0.02,
        "concludes_nothing_blocks": 0.1}
MEDIUM = {**HIGH, "names_open_item": 0.93}
LOW = {**HIGH, "names_open_item": 0.6}


def label(report, probabilities, **overrides):
    data = Path(report).read_bytes()
    value = {"schema_version": 2, "report": str(report), "sha256": hashlib.sha256(data).hexdigest(),
             "question": "q" * 64, "agent": "jev", "model": gates.JEV_MODEL, "verdict": "blocking",
             "answers": {qid: {"answer": gates.band(p), "evidence": "", "p_yes": p}
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
        for overrides in ({"agent": "claude"}, {"model": "jev-latest"}, {"answers": {}}):
            with self.subTest(overrides=overrides):
                self.assertIsNone(gates.decide(label(self.report, HIGH, **overrides))["level"])

    def test_the_label_verdict_never_decides_the_level(self):
        for verdict in ("insufficient_evidence", "approved", None):
            with self.subTest(verdict=verdict):
                self.assertEqual(gates.decide(label(self.report, HIGH, verdict=verdict))["level"], "block")
                self.assertEqual(gates.decide(label(self.report, MEDIUM, verdict=verdict))["level"], "reread")


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

    def test_a_stored_path_through_a_symlink_is_a_malformed_record(self):
        self.record(label(self.report, HIGH))
        (self.root / "linked").symlink_to(self.root)
        sidecar = gates.storage_path(self.state)
        document = json.loads(sidecar.read_text())
        document["gates"][0]["report"] = str(self.root / "linked" / "reviewer.md")
        sidecar.write_text(json.dumps(document))
        with self.assertRaisesRegex(StateError, "canonical"):
            gates.require_clear(self.state, str(self.report), True)

    def test_a_stored_path_that_cannot_resolve_is_a_malformed_record(self):
        self.record(label(self.report, HIGH))
        sidecar = gates.storage_path(self.state)
        document = json.loads(sidecar.read_text())
        document["gates"][0]["report"] = str(self.root / "bad\x00name.md")
        sidecar.write_text(json.dumps(document))
        with self.assertRaisesRegex(StateError, "malformed"):
            gates.require_clear(self.state, str(self.report), True)

class LedgerCase(GateCase):
    """A gated reviewer report, with the owners' records a resolution is bound to."""

    def setUp(self):
        super().setUp()
        who = supervision.identity("lead-session", str(self.root), "fixture", pane_id="lead-pane")
        supervision.bind(self.state, who, BEFORE, root=self.root / "bindings")
        self.dispatches = []
        self.dispatch("d-review", "reviewer", "codex-a", self.report, BEFORE)
        recovery = mock.patch.object(gates, "_recovery_store",
                                     lambda _path: {"dispatches": self.dispatches, "delivery_recoveries": []})
        recovery.start()
        self.addCleanup(recovery.stop)

    def dispatch(self, name, role, agent, report, at, *, task="task-a", judge_mode=None, observed=None):
        """An applied dispatch, its enrollment, and (at `observed`) supervision seeing its report."""
        row = {"id": name, "task": task, "role": role, "agent": agent, "status": "applied"}
        if judge_mode is not None:
            row["judge_mode"] = judge_mode
        self.dispatches.append(row)
        supervision.enroll(self.state, {"id": name, "agent": agent, "task": task, "report": str(report),
                                        "pane_id": None, "native_session": None}, at)
        if observed is not None:
            digest = hashlib.sha256(Path(report).read_bytes()).hexdigest()
            supervision.transaction(self.state, lambda data: supervision.append_event(
                data, observed, name, "report_observed", {"present": True, "path": str(report), "sha256": digest}))

    def view(self):
        return gates.ledger_view(self.state, JUDGE)

    def decision(self, name, task="task-a", resolved=True, answer="B1 is presentation only; accept."):
        attention.write(self.state, "record", {
            "id": name, "kind": "decision", "task": task, "title": "Does B1 block?", "context": "Gate on reviewer.md",
            "consequence": "Acceptance waits", "resolution_condition": "The operator answers",
            "sources": [{"schema_version": 1, "kind": "artifact", "ref": str(self.report)}]}, AFTER)
        if resolved:
            attention.write(self.state, "update", {
                "event_id": name + "-answer", "id": name, "expected_revision": 1, "action": "resolve",
                "reason": "The operator answered", "evidence": {"schema_version": 1, "kind": "user_answer",
                                                                 "ref": "message 42", "summary": answer}}, AFTER)

    def clear(self, **kwargs):
        options = {"evidence": None, "decision": None, **kwargs}
        reason = options.pop("reason", "B1 is advisory: presentation only" if options["evidence"] else None)
        return gates.resolve(self.state, str(self.report), "clear", reason, AFTER, self.view(), **options)


class ClearTest(LedgerCase):
    def test_a_block_refuses_acceptance_until_a_bound_clear(self):
        self.record(label(self.report, HIGH))
        gates.require_clear(self.state, str(self.report), False)
        with self.assertRaisesRegex(UsageError, "cannot be accepted until the block is cleared"):
            gates.require_clear(self.state, str(self.report), True)
        self.dispatch("d-review-2", "reviewer", "codex-b", self.reread, AT, observed=AFTER)
        with self.assertRaisesRegex(UsageError, "no open reread gate; its open block gate is resolved only by"):
            gates.resolve(self.state, str(self.report), "reread", "read it", AFTER, self.view(), evidence=str(self.reread))
        result = self.clear(evidence=str(self.reread))
        resolution = result["resolved"][0]["resolution"]
        self.assertEqual((resolution["by"], resolution["evidence"]["dispatch"]), ("worker", "d-review-2"))
        gates.require_clear(self.state, str(self.report), True)

    def test_a_clear_never_discharges_a_reread(self):
        self.record(label(self.report, MEDIUM))
        self.decision("decide-b1")
        ruling = self.root / "judge.md"
        ruling.write_text("RULING: uphold B\n")
        self.dispatch("d-judge", "judge", JUDGE, ruling, AT, judge_mode="adjudication", observed=AFTER)
        for options in ({"decision": "decide-b1"}, {"evidence": str(ruling)}):
            with self.subTest(options=options), self.assertRaisesRegex(
                    UsageError, "no open block gate; its open reread gate is resolved only by"):
                self.clear(**options)
        self.assertEqual([gate["level"] for gate in gates.status(self.state)["open"]], ["reread"])

    def test_each_command_resolves_only_its_own_level(self):
        self.record(label(self.report, HIGH))
        self.record(label(self.report, MEDIUM, question="r" * 64))
        self.assertEqual(sorted(gate["level"] for gate in gates.status(self.state)["open"]), ["block", "reread"])
        self.decision("decide-b1")
        cleared = self.clear(decision="decide-b1")["resolved"]
        self.assertEqual([gate["level"] for gate in cleared], ["block"])
        self.assertEqual([gate["level"] for gate in gates.status(self.state)["open"]], ["reread"])
        with self.assertRaisesRegex(UsageError, "open re-read gate"):
            gates.require_clear(self.state, str(self.report), True)
        self.dispatch("d-review-2", "reviewer", "codex-b", self.reread, AT, observed=AFTER)
        reread = gates.resolve(self.state, str(self.report), "reread", "Read in full; B1 is real.", AFTER,
                               self.view(), evidence=str(self.reread))["resolved"]
        self.assertEqual([gate["level"] for gate in reread], ["reread"])
        self.assertEqual(gates.status(self.state)["open"], [])
        gates.require_clear(self.state, str(self.report), True)

    def test_a_reread_first_leaves_the_block_standing(self):
        self.record(label(self.report, HIGH))
        self.record(label(self.report, MEDIUM, question="r" * 64))
        self.dispatch("d-review-2", "reviewer", "codex-b", self.reread, AT, observed=AFTER)
        gates.resolve(self.state, str(self.report), "reread", "Read in full.", AFTER, self.view(),
                      evidence=str(self.reread))
        self.assertEqual([gate["level"] for gate in gates.status(self.state)["open"]], ["block"])
        with self.assertRaisesRegex(UsageError, "cannot be accepted"):
            gates.require_clear(self.state, str(self.report), True)

    def test_the_adjudicating_judge_clears(self):
        self.record(label(self.report, HIGH))
        ruling = self.root / "judge.md"
        ruling.write_text("RULING: uphold B -- B1 lies outside this task's scope.\n")
        self.dispatch("d-judge", "judge", JUDGE, ruling, AT, judge_mode="adjudication", observed=AFTER)
        result = self.clear(evidence=str(ruling), reason="Out of scope per the ruling")
        self.assertEqual(result["resolved"][0]["resolution"]["by"], "judge")

    def test_the_operator_clears_by_a_resolved_decision_quoting_its_answer(self):
        self.record(label(self.report, HIGH))
        self.decision("decide-b1")
        resolution = self.clear(decision="decide-b1")["resolved"][0]["resolution"]
        self.assertEqual((resolution["by"], resolution["reason"], resolution["evidence"]),
                         ("operator", "B1 is presentation only; accept.", {"attention": "decide-b1"}))

    def test_a_reread_refuses_any_gating_until_a_bound_reread(self):
        self.record(label(self.report, MEDIUM))
        for accepting in (False, True):
            with self.subTest(accepting=accepting), self.assertRaisesRegex(UsageError, "open re-read gate"):
                gates.require_clear(self.state, str(self.report), accepting)
        self.dispatch("d-review-2", "reviewer", "codex-b", self.reread, AT, observed=AFTER)
        gates.resolve(self.state, str(self.report), "reread", "Read in full; B1 is real.", AFTER, self.view(),
                      evidence=str(self.reread))
        gates.require_clear(self.state, str(self.report), True)
        self.assertEqual(gates.status(self.state)["resolved"][0]["resolution"]["evidence"]["path"], str(self.reread))

    def test_a_judge_never_stands_in_for_a_reread(self):
        self.record(label(self.report, MEDIUM))
        ruling = self.root / "judge.md"
        ruling.write_text("RULING: uphold A\n")
        self.dispatch("d-judge", "judge", JUDGE, ruling, AT, judge_mode="adjudication", observed=AFTER)
        with self.assertRaisesRegex(UsageError, "no delivery receipt"):
            gates.resolve(self.state, str(self.report), "reread", "read", AFTER, self.view(), evidence=str(ruling))

    def test_a_forged_role_string_is_refused(self):
        self.record(label(self.report, HIGH))
        for by in ("operator", "judge", "worker"):
            with self.subTest(by=by), self.assertRaises(SystemExit) as caught:
                cli.main(["report-gate-clear", "--report", str(self.report), "--by", by, "--reason", "why",
                          "--state", str(self.state)], stdout=io.StringIO(), stderr=io.StringIO())
            self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(len(gates.status(self.state)["open"]), 1)

    def test_evidence_without_a_bound_delivery_is_refused(self):
        self.record(label(self.report, HIGH))
        other = self.root / "other.md"
        other.write_text("B1 is advisory.\n")
        ruling = self.root / "judge.md"
        ruling.write_text("RULING: uphold B\n")
        diagnosis = self.root / "diagnosis.md"
        diagnosis.write_text("DIAGNOSIS: the loop is stuck\n")
        early = self.root / "early.md"
        early.write_text("B1 is advisory.\n")
        stale = self.root / "stale.md"
        stale.write_text("B1 is advisory.\n")
        foreign = self.root / "foreign.md"
        foreign.write_text("B1 is advisory.\n")
        developer = self.root / "developer.md"
        developer.write_text("B1 is advisory.\n")
        self.dispatch("d-early", "reviewer", "codex-b", early, BEFORE, observed=AT.replace("12:00", "11:59"))
        self.dispatch("d-stale", "reviewer", "codex-c", stale, AT, observed=AFTER)
        stale.write_text("B1 is advisory, rewritten after delivery.\n")
        self.dispatch("d-foreign", "reviewer", "codex-d", foreign, AT, task="task-b", observed=AFTER)
        self.dispatch("d-dev", "developer", "codex-e", developer, AT, observed=AFTER)
        self.dispatch("d-diag", "judge", JUDGE, diagnosis, AT, judge_mode="diagnosis", observed=AFTER)
        self.dispatch("d-rogue", "judge", "grok-x", ruling, AT, judge_mode="adjudication", observed=AFTER)
        cases = {"no receipt": other, "delivered before the gate": early, "bytes changed since delivery": stale,
                 "another task": foreign, "another role": developer, "a diagnosis": diagnosis,
                 "not the pinned judge": ruling, "the gated report itself": self.report}
        for name, evidence in cases.items():
            with self.subTest(name=name), self.assertRaises(UsageError):
                self.clear(evidence=str(evidence))
        self.assertEqual(len(gates.status(self.state)["open"]), 1)

    def test_an_operator_clear_needs_a_resolved_decision_on_the_task(self):
        self.record(label(self.report, HIGH))
        self.decision("open-one", resolved=False)
        self.decision("other-task", task="task-b")
        for name in ("absent", "open-one", "other-task"):
            with self.subTest(name=name), self.assertRaisesRegex(UsageError, "attention decision"):
                self.clear(decision=name)
        with self.assertRaisesRegex(UsageError, "drop --reason"):
            self.clear(decision="other-task", reason="my own words")
        with self.assertRaisesRegex(UsageError, "exactly one"):
            self.clear()
        self.assertEqual(len(gates.status(self.state)["open"]), 1)

    def test_a_report_no_dispatch_enrolled_cannot_be_resolved(self):
        stray = self.root / "stray.md"
        stray.write_text("B1 blocking.\n")
        gates.record(self.state, {"labels": [label(stray, HIGH)]}, AT)
        self.decision("decide-b1")
        with self.assertRaisesRegex(UsageError, "no applied dispatch"):
            gates.resolve(self.state, str(stray), "clear", None, AFTER, self.view(), decision="decide-b1")

    def test_a_new_classification_after_a_clear_records_a_fresh_gate(self):
        self.record(label(self.report, HIGH))
        self.decision("decide-b1")
        self.clear(decision="decide-b1")
        again = self.record(label(self.report, HIGH, question="r" * 64))
        self.assertEqual((len(again["recorded"]), again["replayed"]), (1, []))
        with self.assertRaisesRegex(UsageError, "cannot be accepted"):
            gates.require_clear(self.state, str(self.report), True)

    def test_a_resolution_needs_an_open_gate(self):
        self.decision("decide-b1")
        with self.assertRaisesRegex(UsageError, "no open gate"):
            self.clear(decision="decide-b1")

    def test_the_cli_records_clears_and_lists(self):
        labels = self.root / "labels.json"
        labels.write_text(json.dumps({"labels": [label(self.report, HIGH)]}))
        self.dispatch("d-review-2", "reviewer", "codex-b", self.reread, AT, observed=AFTER)
        base = ["--state", str(self.state), "--config", str(self.root / "absent-config.json")]

        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            return cli.main([*args, *base], stdout=out, stderr=err), json.loads(out.getvalue() or "null"), err.getvalue()

        code, payload, err = run("report-gate-record", "--labels", str(labels), "--now", AT)
        self.assertEqual(code, 0, err)
        self.assertEqual(payload["recorded"][0]["level"], "block")
        code, payload, err = run("report-gate-status")
        self.assertEqual((code, len(payload["open"])), (0, 1), err)
        code, payload, err = run("report-gate-clear", "--report", str(self.report), "--evidence", str(self.reread),
                                 "--reason", "B1 is advisory: presentation only", "--now", AFTER)
        self.assertEqual(code, 0, err)
        self.assertEqual(payload["resolved"][0]["resolution"]["by"], "worker")
        code, payload, err = run("report-gate-status")
        self.assertEqual((len(payload["open"]), len(payload["resolved"])), (0, 1))


class ShapeTest(LedgerCase):
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
            "resolution crosses levels": lambda gate: gate.update(status="reread", resolution={
                "schema_version": 1, "at": AT, "action": "reread", "by": "worker", "reason": "r",
                "evidence": {"path": "/r.md", "sha256": "a" * 64, "dispatch": "d"}}),
            "resolution action disagrees": lambda gate: gate.update(status="cleared", resolution={
                "schema_version": 1, "at": AT, "action": "reread", "by": "worker", "reason": "r",
                "evidence": {"path": "/r.md", "sha256": "a" * 64}}),
            "worker clear without evidence": lambda gate: gate.update(status="cleared", resolution={
                "schema_version": 1, "at": AT, "action": "clear", "by": "worker", "reason": "r", "evidence": None}),
            "worker clear without its dispatch": lambda gate: gate.update(status="cleared", resolution={
                "schema_version": 1, "at": AT, "action": "clear", "by": "worker", "reason": "r",
                "evidence": {"path": "/r.md", "sha256": "a" * 64}}),
            "operator clear without a decision": lambda gate: gate.update(status="cleared", resolution={
                "schema_version": 1, "at": AT, "action": "clear", "by": "operator", "reason": "r", "evidence": None}),
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
                             lambda: gates.resolve(self.state, str(self.report), "clear", None, AFTER, {},
                                                   decision="decide-b1")):
                    with self.assertRaisesRegex(StateError, "record 0 is malformed"):
                        read()
                gates.storage_path(self.state).unlink()

    def test_a_valid_resolved_record_reads(self):
        self.record(label(self.report, HIGH))
        self.decision("decide-b1")
        self.clear(decision="decide-b1")
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
        # The report's recorded VERDICT is what an `accepted` closure reads first (#625).
        Path(self.report).write_text("B1 blocking: the parser accepts a quoted marker.\nVERDICT: approved\n")
        seed_contract(self)
        gates.record(self.path, {"labels": [label(self.report, probabilities)]}, "2026-09-01T12:01:00+00:00")

    def test_an_open_block_refuses_an_accepted_closure(self):
        self.emit()
        self.gate(HIGH)
        self.write_ledger("accepted")
        with self.assertRaisesRegex(UsageError, "cannot be accepted"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        ruling = self.root / "judge.md"
        ruling.write_text("RULING: uphold B -- B1 lies outside this task's scope.\n")
        store.enroll(self.path, {"id": "dispatch-j", "agent": JUDGE, "task": "task-a", "report": str(ruling),
                                 "pane_id": None, "native_session": None}, LATER)
        digest = hashlib.sha256(ruling.read_bytes()).hexdigest()
        store.transaction(self.path, lambda data: store.append_event(
            data, LATER, "dispatch-j", "report_observed", {"present": True, "path": str(ruling), "sha256": digest}))
        dispatches = [{"id": "dispatch-a", "task": "task-a", "role": "reviewer", "agent": "codex-a", "status": "applied"},
                      {"id": "dispatch-j", "task": "task-a", "role": "judge", "agent": JUDGE, "status": "applied",
                       "judge_mode": "adjudication"}]
        with mock.patch.object(gates, "_recovery_store",
                               lambda _path: {"dispatches": dispatches, "delivery_recoveries": []}):
            view = gates.ledger_view(self.path, JUDGE)
        gates.resolve(self.path, self.report, "clear", "The finding is out of scope per ruling R2.", LATER, view,
                      evidence=str(ruling))
        self.assertEqual(members.close(self.path, "dispatch-a", self.ledger, LATER)["decision"], "accepted")

    def test_a_block_never_refuses_a_needs_work_closure(self):
        self.emit()
        self.gate(HIGH)
        self.write_ledger("needs_work")
        self.assertEqual(members.close(self.path, "dispatch-a", self.ledger, LATER)["decision"], "needs_work")

    def test_a_gate_recorded_mid_closure_is_refused(self):
        self.emit()
        Path(self.report).write_text("B1 blocking: the parser accepts a quoted marker.\nVERDICT: approved\n")
        seed_contract(self)
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
