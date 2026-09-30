"""Verdict gates (#646): a blocking VERDICT holds the task's release until a re-check or the operator clears it."""

import copy
import hashlib
import io
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import assign, attention, cli, report_gates as gates  # noqa: E402 -- the skill dir is on sys.path only from here
from foreman.errors import StateError, UsageError  # noqa: E402 -- the skill dir is on sys.path only from here
from foreman.state import save_state, state_lock  # noqa: E402 -- the skill dir is on sys.path only from here
from tests import test_cli as cli_fixture  # noqa: E402 -- the skill dir is on sys.path only from here
from tests import test_engagement as engagement_fixture  # noqa: E402 -- the skill dir is on sys.path only from here
from tests import test_recovery_cli as recovery_fixture  # noqa: E402 -- the skill dir is on sys.path only from here
from tests import test_report_gates as gate_fixture  # noqa: E402 -- the skill dir is on sys.path only from here
from tests.fakes import FakeRunner  # noqa: E402 -- the skill dir is on sys.path only from here

AT, AFTER, BEFORE, JUDGE = gate_fixture.AT, gate_fixture.AFTER, gate_fixture.BEFORE, gate_fixture.JUDGE
LATEST = "2026-09-27T12:20:00+00:00"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class VerdictCase(gate_fixture.LedgerCase):
    """A reviewer report carrying `VERDICT: blocking`, with the owners' records a clear is bound to."""

    def setUp(self):
        super().setUp()
        self.report.write_text("B1 blocking: the parser accepts a quoted marker.\nVERDICT: blocking\n")
        self.reread.write_text("Re-checked at the new tip: B1 RESOLVED.\nVERDICT: approved\n")

    def verdict(self, report=None, dispatch="d-review", at=AT):
        report = report or self.report
        return gates.record_verdict(self.state, str(report), digest(report), dispatch, at)

    def parsed(self, path, verdict="approved"):
        """An owner-parsed verdict, as a report-sourced assessment records it."""
        self.assessments.append({"source": "report", "verdict": verdict, "report": str(path),
                                 "report_evidence": {"path": str(path), "sha256": digest(path)}})

    def open_sources(self):
        return sorted(gate["source"] for gate in gates.status(self.state)["open"])


class RecordVerdictTest(VerdictCase):
    def test_one_block_gate_per_report_bytes(self):
        first = self.verdict()["recorded"][0]
        self.assertEqual((first["source"], first["level"], first["dispatch"], first["status"]),
                         ("verdict", "block", "d-review", "open"))
        self.assertFalse(gates.CLASSIFIER_ONLY & set(first))
        again = self.verdict(at=AFTER)
        self.assertEqual((again["recorded"], len(again["replayed"])), ([], 1))
        self.report.write_text("B1 still blocking after the rewrite.\nVERDICT: blocking\n")
        self.assertEqual(len(self.verdict()["recorded"]), 1)
        self.assertEqual(len(gates.status(self.state)["open"]), 2)

    def test_a_replay_after_a_clear_never_reopens_the_gate(self):
        self.verdict()
        self.decision("decide-b1")
        self.clear(decision="decide-b1")
        replayed = self.verdict(at=LATEST)["replayed"][0]
        self.assertEqual(replayed["status"], "cleared")
        self.assertEqual(gates.status(self.state)["open"], [])

    def test_a_classifier_gate_on_the_same_bytes_is_a_separate_gate(self):
        self.verdict()
        recorded = self.record(gate_fixture.label(self.report, gate_fixture.HIGH))["recorded"]
        self.assertEqual(len(recorded), 1)
        self.assertEqual(self.open_sources(), ["classifier", "verdict"])

    def test_acceptance_never_reads_a_verdict_gate(self):
        self.verdict()
        gates.require_clear(self.state, str(self.report), True)

    def test_a_malformed_input_records_nothing(self):
        for sha, dispatch in (("xyz", "d-review"), (digest(self.report), "")):
            with self.subTest(sha=sha, dispatch=dispatch), self.assertRaises(UsageError):
                gates.record_verdict(self.state, str(self.report), sha, dispatch, AT)
        self.assertFalse(gates.storage_path(self.state).exists())


class ReleaseGateTest(VerdictCase):
    def test_a_release_waits_for_the_tasks_open_verdict_gates(self):
        self.verdict()
        with self.assertRaisesRegex(UsageError, "open verdict gate") as caught:
            gates.require_no_verdict_gate(self.state, "task-a", self.dispatches)
        self.assertEqual([gate["report"] for gate in caught.exception.details["gates"]], [str(self.report)])
        gates.require_no_verdict_gate(self.state, "task-b", self.dispatches)
        self.decision("decide-b1")
        self.clear(decision="decide-b1")
        gates.require_no_verdict_gate(self.state, "task-a", self.dispatches)

    def test_a_classifier_block_never_holds_the_release(self):
        self.record(gate_fixture.label(self.report, gate_fixture.HIGH))
        gates.require_no_verdict_gate(self.state, "task-a", self.dispatches)

    def test_a_gate_whose_dispatch_the_ledger_lost_is_refused(self):
        self.verdict()
        with self.assertRaisesRegex(StateError, "does not hold"):
            gates.require_no_verdict_gate(self.state, "task-b", [])


class RecheckTest(VerdictCase):
    def test_an_approved_recheck_of_the_same_responsibility_clears(self):
        self.verdict()
        self.dispatch("d-review-2", "reviewer", "codex-b", self.reread, AFTER, observed=AFTER)
        self.parsed(self.reread)
        resolution = self.clear(evidence=str(self.reread), reason="B1 resolved at the new tip")["resolved"][0]["resolution"]
        self.assertEqual((resolution["by"], resolution["evidence"]["dispatch"]), ("worker", "d-review-2"))
        self.assertEqual(gates.status(self.state)["open"], [])

    def test_a_recheck_needs_an_owner_parsed_approved_verdict(self):
        self.verdict()
        self.dispatch("d-review-2", "reviewer", "codex-b", self.reread, AFTER, observed=AFTER)
        with self.assertRaisesRegex(UsageError, "no owner-parsed `VERDICT: approved`"):
            self.clear(evidence=str(self.reread))
        self.parsed(self.reread, "blocking")
        with self.assertRaisesRegex(UsageError, "no owner-parsed `VERDICT: approved`"):
            self.clear(evidence=str(self.reread))
        self.assertEqual(self.open_sources(), ["verdict"])

    def test_a_recheck_carrying_its_own_classifier_gate_clears_nothing(self):
        self.verdict()
        self.dispatch("d-review-2", "reviewer", "codex-b", self.reread, AFTER, observed=AFTER)
        self.parsed(self.reread)
        gates.record(self.state, {"labels": [gate_fixture.label(self.reread, gate_fixture.HIGH)]}, AT)
        with self.assertRaisesRegex(UsageError, "open classifier gate of its own"):
            self.clear(evidence=str(self.reread))
        self.assertEqual(self.open_sources(), ["classifier", "verdict"])

    def test_a_recheck_comes_from_the_gated_responsibility_after_the_gate(self):
        self.verdict()
        cases = {}
        for name, role, task, at in (("early", "reviewer", "task-a", BEFORE), ("tester", "tester", "task-a", AFTER),
                                     ("foreign", "reviewer", "task-b", AFTER), ("developer", "developer", "task-a", AFTER)):
            path = self.root / (name + ".md")
            path.write_text("Re-checked: B1 RESOLVED ({}).\nVERDICT: approved\n".format(name))
            self.dispatch("d-" + name, role, "codex-" + name, path, at, task=task, observed=AFTER)
            self.parsed(path)
            cases[name] = path
        for name, path in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(UsageError, "no re-check of this verdict gate"):
                self.clear(evidence=str(path))
        self.assertEqual(self.open_sources(), ["verdict"])

    def test_a_consultation_recheck_matches_the_gated_specialty(self):
        consult = self.root / "security.md"
        consult.write_text("ACCEPTANCE 1/1: met — threat model\nVERDICT: blocking\n")
        self.dispatch("d-sec", "advisor", "codex-s", consult, BEFORE, specialty="security")
        gates.record_verdict(self.state, str(consult), digest(consult), "d-sec", AT)
        other = self.root / "ux.md"
        other.write_text("ACCEPTANCE 1/1: met — flow\nVERDICT: approved\n")
        again = self.root / "security-2.md"
        again.write_text("ACCEPTANCE 1/1: met — threat model re-checked\nVERDICT: approved\n")
        self.dispatch("d-ux", "advisor", "codex-u", other, AFTER, specialty="ux", observed=AFTER)
        self.dispatch("d-sec-2", "advisor", "codex-t", again, AFTER, specialty="security", observed=AFTER)
        self.parsed(other)
        self.parsed(again)

        def clear(evidence):
            return gates.resolve(self.state, str(consult), "clear", "Re-checked", AFTER, self.view(),
                                 evidence=str(evidence))
        with self.assertRaisesRegex(UsageError, r"advisor role \(security\)"):
            clear(other)
        self.assertEqual(clear(again)["resolved"][0]["resolution"]["evidence"]["dispatch"], "d-sec-2")

    def test_a_judge_ruling_never_clears_a_verdict_gate(self):
        self.verdict()
        ruling = self.root / "judge.md"
        ruling.write_text("RULING: uphold B -- B1 is not a defect.\n")
        self.dispatch("d-judge", "judge", JUDGE, ruling, AFTER, judge_mode="adjudication", observed=AFTER)
        with self.assertRaisesRegex(UsageError, "never clears its gate"):
            self.clear(evidence=str(ruling), reason="Per the ruling")
        self.assertEqual(self.open_sources(), ["verdict"])

    def test_the_operator_clears_a_verdict_gate_by_a_resolved_decision(self):
        self.verdict()
        self.decision("decide-b1")
        resolution = self.clear(decision="decide-b1")["resolved"][0]["resolution"]
        self.assertEqual((resolution["by"], resolution["evidence"]), ("operator", {"attention": "decide-b1"}))

    def test_a_record_report_gate_is_cleared_by_a_reviewers_approved_receipt(self):
        developer = self.root / "developer.md"
        developer.write_text("Implemented the parser fix.\n")
        self.dispatch("d-dev", "developer", "grok-a", developer, BEFORE)
        self.verdict(dispatch="d-dev")
        self.dispatch("d-review-2", "reviewer", "codex-b", self.reread, AFTER, observed=AFTER)
        self.dispatches.append({"id": "d-dev-2", "task": "task-a", "role": "developer", "agent": "grok-a",
                                "status": "applied", "at": AFTER,
                                "report": {"report": str(self.reread), "verdict": "approved",
                                           "evidence": {"path": str(self.reread), "sha256": digest(self.reread)}}})
        resolution = self.clear(evidence=str(self.reread))["resolved"][0]["resolution"]
        self.assertEqual(resolution["evidence"]["dispatch"], "d-review-2")

    def test_a_clear_resolves_every_source_or_none(self):
        self.verdict()
        self.record(gate_fixture.label(self.report, gate_fixture.HIGH))
        ruling = self.root / "judge.md"
        ruling.write_text("RULING: uphold B\n")
        self.dispatch("d-judge", "judge", JUDGE, ruling, AFTER, judge_mode="adjudication", observed=AFTER)
        with self.assertRaisesRegex(UsageError, "never clears its gate"):
            self.clear(evidence=str(ruling))
        self.assertEqual(self.open_sources(), ["classifier", "verdict"])
        self.dispatch("d-review-2", "reviewer", "codex-b", self.reread, AFTER, observed=AFTER)
        self.parsed(self.reread)
        cleared = self.clear(evidence=str(self.reread))["resolved"]
        self.assertEqual(sorted((gate["source"], gate["resolution"]["by"]) for gate in cleared),
                         [("classifier", "worker"), ("verdict", "worker")])


class SchemaTest(gate_fixture.GateCase):
    def legacy(self):
        """A schema-1 sidecar holding one resolved classifier gate, as #617 wrote it."""
        gate = {"schema_version": 1, "report": str(self.report), "sha256": digest(self.report), "level": "block",
                "reason": "high confidence", "probabilities": {"names_open_item": 0.99, "open_items_accepted": 0.01,
                                                               "open_items_out_of_scope": 0.01},
                "model": gates.JEV_MODEL, "question": "q", "bands": gates.BANDS_VERSION, "at": AT,
                "status": "cleared", "resolution": {"schema_version": 1, "at": AFTER, "action": "clear",
                                                    "by": "operator", "reason": "accept",
                                                    "evidence": {"attention": "decide-b1"}}}
        document = {"schema_version": 1, "state_path": str(self.state), "gates": [gate]}
        gates.storage_path(self.state).write_text(json.dumps(document))
        return document

    def test_the_owner_rewrites_a_schema_one_sidecar_on_its_first_read(self):
        self.legacy()
        gate = gates.status(self.state)["resolved"][0]
        self.assertEqual((gate["schema_version"], gate["source"], gate["dispatch"], gate["resolution"]["schema_version"]),
                         (2, "classifier", None, 2))
        saved = json.loads(gates.storage_path(self.state).read_text())
        self.assertEqual((saved["schema_version"], saved["gates"]), (2, [gate]))
        other = self.root / "other.md"
        other.write_text("VERDICT: blocking\n")
        gates.record_verdict(self.state, str(other), digest(other), "d-review", AFTER)
        saved = json.loads(gates.storage_path(self.state).read_text())
        self.assertEqual([row["source"] for row in saved["gates"]], ["classifier", "verdict"])

    def test_a_migration_inside_a_held_transaction_writes_once_under_that_lock(self):
        self.legacy()
        with gates.holding(self.state):
            gates.require_clear(self.state, str(self.report), True)
        self.assertEqual(json.loads(gates.storage_path(self.state).read_text())["schema_version"], 2)

    def test_a_migration_while_another_process_holds_the_lock_is_refused(self):
        before = json.dumps(self.legacy())
        with state_lock(gates.storage_path(self.state)):
            with self.assertRaisesRegex(StateError, "Another foreman command owns"):
                gates.status(self.state)
        self.assertEqual(gates.storage_path(self.state).read_text(), before)

    def test_an_unknown_schema_is_refused_never_read_as_no_gates(self):
        document = self.legacy()
        document["schema_version"] = 3
        gates.storage_path(self.state).write_text(json.dumps(document))
        with self.assertRaisesRegex(StateError, "unsupported schema"):
            gates.status(self.state)

    def test_a_schema_one_sidecar_holding_a_non_legacy_gate_is_refused(self):
        document = self.legacy()
        document["gates"][0]["source"] = "verdict"
        gates.storage_path(self.state).write_text(json.dumps(document))
        with self.assertRaisesRegex(StateError, "record 0 is malformed"):
            gates.status(self.state)

    def test_every_malformed_verdict_gate_is_a_state_error(self):
        gates.record_verdict(self.state, str(self.report), digest(self.report), "d-review", AT)
        saved = json.loads(gates.storage_path(self.state).read_text())
        breakages = {
            "a classifier field": lambda gate: gate.update(model=gates.JEV_MODEL),
            "a reread level": lambda gate: gate.update(level="reread"),
            "no dispatch": lambda gate: gate.update(dispatch=None),
            "an unknown source": lambda gate: gate.update(source="label"),
            "a judge clear": lambda gate: gate.update(status="cleared", resolution={
                "schema_version": gates.SCHEMA_VERSION, "at": AFTER, "action": "clear", "by": "judge",
                "reason": "ruled", "evidence": {"path": "/j.md", "sha256": "a" * 64, "dispatch": "d-judge"}}),
        }
        for name, breakage in breakages.items():
            with self.subTest(name=name):
                document = copy.deepcopy(saved)
                breakage(document["gates"][0])
                gates.storage_path(self.state).write_text(json.dumps(document))
                with self.assertRaisesRegex(StateError, "record 0 is malformed"):
                    gates.status(self.state)

    def test_a_classifier_gate_naming_a_dispatch_is_malformed(self):
        gates.record(self.state, {"labels": [gate_fixture.label(self.report, gate_fixture.HIGH)]}, AT)
        document = json.loads(gates.storage_path(self.state).read_text())
        document["gates"][0]["dispatch"] = "d-review"
        gates.storage_path(self.state).write_text(json.dumps(document))
        with self.assertRaisesRegex(StateError, "record 0 is malformed"):
            gates.status(self.state)


class AssessCommandTest(unittest.TestCase):
    """`assess-specialist` records the gate a blocking verdict earns, replay included."""

    # Set by the borrowed engagement setUp.
    root: Path
    path: Path
    state: dict

    setUp = engagement_fixture.EngagementTest.setUp
    seed = engagement_fixture.EngagementTest.seed
    seed_reviewer = engagement_fixture.EngagementTest.seed_reviewer

    def assess_cli(self, data):
        save_state(self.path, self.state)
        record = self.root / "assessment.json"
        record.write_text(json.dumps(data))
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["assess-specialist", "--record", str(record), "--now", engagement_fixture.LATER,
                         "--state", str(self.path), "--config", str(self.root / "absent-config.json")],
                        stdout=out, stderr=err)
        self.state = json.loads(self.path.read_text())
        return code, err.getvalue()

    def test_a_blocking_assessment_records_a_verdict_gate_once(self):
        data = self.seed_reviewer("Reviewed the tip; B1 is blocking.\nVERDICT: blocking\n")
        code, err = self.assess_cli(data)
        self.assertEqual(code, 0, err)
        opened = gates.status(self.path)["open"]
        self.assertEqual([(gate["source"], gate["dispatch"], gate["report"]) for gate in opened],
                         [("verdict", "review-1", str(Path(data["report"]).resolve()))])
        # A replay returns the saved assessment and records no second gate.
        code, err = self.assess_cli(data)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(gates.status(self.path)["open"]), 1)

    def test_an_approved_assessment_records_no_gate(self):
        data = self.seed_reviewer("Reviewed the tip; nothing blocks.\nVERDICT: approved\n")
        code, err = self.assess_cli(data)
        self.assertEqual(code, 0, err)
        self.assertFalse(gates.storage_path(self.path).exists())


class ReleaseCommandTest(cli_fixture.CliCase):
    """`record-report` records the gate; `apply` holds a fresh release until it clears."""

    _client = cli_fixture.ApplyCommandTest._client
    # Set by the borrowed `_client`.
    runner: FakeRunner

    def setUp(self):
        super().setUp()
        self.briefs["release"] = self.tmp / "release.md"
        self.briefs["release"].write_text("Release the verified current tip; stop for any source correction.\n")

    invoke = recovery_fixture.RecoveryCommandTests.invoke
    owner = recovery_fixture.RecoveryCommandTests.owner
    register = recovery_fixture.RecoveryCommandTests.register
    saved = recovery_fixture.RecoveryCommandTests.saved
    apply_args = recovery_fixture.RecoveryCommandTests.apply_args
    fresh_client = recovery_fixture.RecoveryCommandTests.fresh_client

    def blocking_review(self):
        """The initial developer dispatch and a recorded blocking review of it."""
        self.register()
        code, out, err = self.invoke(self.apply_args(), self.fresh_client("previous-task", "developer-0"))
        self.assertEqual(code, 0, err)
        dispatch = json.loads(out)["applied"][0]["dispatch_id"]
        review = self.tmp / "review.md"
        review.write_text("Reviewed head " + recovery_fixture.HEAD + "\nBlocking F1.\nVERDICT: blocking\n")
        code, _, err = self.owner("record-report", {
            "dispatch": dispatch, "head_revision": recovery_fixture.HEAD, "verdict": "blocking", "review_mode": "full",
            "reviewer": "codex", "report": str(review), "changed_paths": ["src/parser.py"]})
        self.assertEqual(code, 0, err)
        return dispatch, review

    def operator_clear(self, review):
        later = (datetime.fromisoformat(cli_fixture.AT) + timedelta(hours=1)).isoformat()
        attention.write(self.state, "record", {
            "id": "ship-f1", "kind": "decision", "task": recovery_fixture.TASK, "title": "Ship with F1?",
            "context": "Blocking review", "consequence": "Release waits", "resolution_condition": "Operator answers",
            "sources": [{"schema_version": 1, "kind": "artifact", "ref": str(review)}]}, later)
        attention.write(self.state, "update", {
            "event_id": "ship-f1-answer", "id": "ship-f1", "expected_revision": 1, "action": "resolve",
            "reason": "Answered", "evidence": {"schema_version": 1, "kind": "user_answer", "ref": "message 7",
                                               "summary": "F1 is fixed upstream; release."}}, later)
        code, out, err = self.invoke(["report-gate-clear", "--report", str(review), "--decision", "ship-f1",
                                      "--now", later])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_record_report_records_the_gate_against_the_reviewed_dispatch(self):
        dispatch, review = self.blocking_review()
        opened = gates.status(self.state)["open"]
        self.assertEqual([(gate["source"], gate["dispatch"]) for gate in opened], [("verdict", dispatch)])

    def test_a_fresh_release_waits_for_the_open_verdict_gate_dry_run_included(self):
        _dispatch, review = self.blocking_review()
        for extra in ((), ("--dry-run",)):
            with self.subTest(extra=extra):
                code, _, err = self.invoke(self.apply_args("release", None, *extra), self._client({"grok": "idle"}))
                self.assertEqual(code, 1)
                self.assertIn("open verdict gate", err)
                self.assertEqual(self.runner.writes(), [])
        self.assertEqual(self.saved()["assignments"][-1]["role"], "developer")
        self.assertEqual(self.operator_clear(review)["resolved"][0]["resolution"]["by"], "operator")
        # The dispatch checkpoint follows the operator's recorded answer.
        after_answer = (datetime.fromisoformat(cli_fixture.AT) + timedelta(hours=2)).isoformat()
        code, _, err = self.invoke(self.apply_args("release") + ["--now", after_answer], self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.saved()["assignments"][-1]["role"], "release")

    def test_a_verdict_gate_recorded_mid_release_is_refused(self):
        self.register()
        code, out, err = self.invoke(self.apply_args(), self.fresh_client("previous-task", "developer-0"))
        self.assertEqual(code, 0, err)
        review = self.tmp / "racing-review.md"
        review.write_text("VERDICT: blocking\n")
        attempts = []
        real_send = assign.send_message

        def racing(*args, **kwargs):
            # Another process records a verdict gate after the release's gate
            # check, before its send; it holds none of this process's locks.
            try:
                with mock.patch.object(gates, "_HELD", set()):
                    gates.record_verdict(self.state, str(review), digest(review), "d-any", cli_fixture.AT)
                attempts.append("recorded")
            except StateError as exc:
                attempts.append(exc.message)
            return real_send(*args, **kwargs)

        with mock.patch("foreman.assign.send_message", side_effect=racing):
            code, _, err = self.invoke(self.apply_args("release"), self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(attempts), 1)
        self.assertIn("Another foreman command owns", attempts[0])
        self.assertEqual(gates.status(self.state)["open"], [])

    def test_a_recheck_dispatch_is_never_held(self):
        self.blocking_review()
        args = ["apply", "--assignments", json.dumps({"reviewer": "codex"}), "--common", str(self.common),
                "--brief", "reviewer=" + str(self.briefs["reviewer"]), "--task", recovery_fixture.TASK,
                "--now", cli_fixture.AT, "--composer-settle", "0"]
        code, _, err = self.invoke(args, self._client({"codex": "idle"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.saved()["assignments"][-1]["role"], "reviewer")

    def test_a_completed_release_replays_while_a_later_gate_is_open(self):
        self.register()
        code, out, err = self.invoke(self.apply_args(), self.fresh_client("previous-task", "developer-0"))
        self.assertEqual(code, 0, err)
        dispatch = json.loads(out)["applied"][0]["dispatch_id"]
        release = self.apply_args("release", None, "--dispatch-id", "release-1")
        code, _, err = self.invoke(release, self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        review = self.tmp / "late-review.md"
        review.write_text("Reviewed head " + recovery_fixture.HEAD + "\nBlocking F2.\nVERDICT: blocking\n")
        code, _, err = self.owner("record-report", {
            "dispatch": dispatch, "head_revision": recovery_fixture.HEAD, "verdict": "blocking", "review_mode": "full",
            "reviewer": "codex", "report": str(review), "changed_paths": ["src/parser.py"]})
        self.assertEqual(code, 0, err)
        before = self.state.read_bytes()
        code, out, err = self.invoke(release, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["replayed"])
        self.assertEqual(self.state.read_bytes(), before)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
