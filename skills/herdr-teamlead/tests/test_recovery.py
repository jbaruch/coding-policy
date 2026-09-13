"""Outcome tests for bounded recovery and truthful, reusable attempt history."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from teamlead.errors import UsageError
from teamlead.recovery import (
    abort_pre_send, authorize_context, authorize_plan, checkpoint, confirmed_fix,
    dispatch_identity, finish_dispatch, fresh_transition, mark_sending,
    migrate_store, prior_dispatch, record_refusal, record_report, refusal_move, register_task, reserve,
    task_statuses, validate_store, validate_work,
)
from teamlead.state import add_assignment, empty_state, load_state_checked, save_state

AT = "2026-02-03T10:00:00+00:00"
TASK = "fixture-task"
BASE = "a" * 40
HEAD = "b" * 40
AUTH = {"source": "fixture operator message", "quote": "Approve this task and its stated bounds."}
WORK = {"base_revision": BASE, "scope": "Correct parser findings", "paths": ["src/parser.py"], "findings": ["F1"]}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = empty_state()
        self.store = self.state["recovery"]
        self.history = self.state["assignments"]
        register_task(self.store, {"task": TASK, "base_revision": BASE, "scope": WORK["scope"],
                                  "allowed_paths": ["src/*"], "authorization": AUTH}, AT)
        self.judge_report = self.root / "judge.md"
        self.judge_report.write_text("RULING: amend — correct the remaining parser defect\nACTION: Use one canonical parser\n")
        self.review = self.root / "review.md"
        self.review.write_text("Reviewed head: " + HEAD + "\nBlocking finding F1: quoted input is still accepted as a completion signal.\n")

    def exhaust(self):
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(self.state, "2026-02-03T09:00:0{}+00:00".format(fix or 0), "developer", "worker", task=TASK, fix_round=fix)

    def seed_checkpoint(self):
        self.exhaust()
        add_assignment(self.state, AT, "judge", "judge", task=TASK)
        return checkpoint(self.store, self.history, {
            "id": "checkpoint-5", "task": TASK, "defect": "F1 remains open", "previous_attempts": "Five attempts changed parsing and quoting",
            "progress": "Some counterexamples now pass; the quoted case remains red", "change_in_approach": "Use one canonical parser",
            "judge_report": str(self.judge_report),
        }, AT, "judge")

    def approve(self):
        self.seed_checkpoint()
        return authorize_plan(self.store, self.history, {"id": "plan-1", "task": TASK, "checkpoint": "checkpoint-5",
            "scope": WORK["scope"], "allowed_paths": ["src/*"], "additional_fixes": 2, "authorization": AUTH}, AT)

    def reservation(self, number):
        return {"id": f"fix-{number}", "task": TASK, "role": "developer", "agent": "worker", "fix_round": number,
                "fingerprint": "c" * 64, "plan": "plan-1", "work": copy.deepcopy(WORK)}

    def finish(self, number):
        record = self.reservation(number)
        reserve(self.store, record, AT)
        mark_sending(self.store, record["id"], AT, {"cleared": True})
        add_assignment(self.state, "2026-02-03T11:00:0{}+00:00".format(number), "developer", "worker", task=TASK, fix_round=number)
        finish_dispatch(self.store, record["id"], {"status": "applied", **{
            key: record[key] for key in ("task", "role", "agent", "fix_round")}}, len(self.history) - 1, AT)

    def test_two_corrections_share_one_approval_and_the_first_outside_budget_refuses(self):
        plan = self.approve()
        self.assertEqual((plan["first_fix"], plan["last_fix"]), (6, 7))
        validate_work(self.store, self.history, TASK, 6, "plan-1", WORK)
        self.finish(6)
        with self.assertRaisesRegex(UsageError, "actual blocking review"):
            validate_work(self.store, self.history, TASK, 7, "plan-1", WORK)
        result = record_report(self.store, {"dispatch": "fix-6", "head_revision": HEAD, "verdict": "blocking",
            "review_mode": "full", "reviewer": "independent-reviewer", "report": str(self.review), "changed_paths": ["src/parser.py"]}, AT)
        self.assertEqual(result["evidence"]["sha256"], hashlib.sha256(self.review.read_bytes()).hexdigest())
        validate_work(self.store, self.history, TASK, 7, "plan-1", WORK)
        self.finish(7)
        self.assertEqual(len(self.store["plans"]), 1)
        self.assertEqual(confirmed_fix(self.history, TASK), 7)
        with self.assertRaisesRegex(UsageError, "outside the approved task or budget"):
            validate_work(self.store, self.history, TASK, 8, "plan-1", WORK)
        path = self.root / "state.json"
        save_state(path, self.state)
        restored, usable = load_state_checked(path)
        self.assertTrue(usable)
        self.assertEqual(restored, self.state)

    def test_ordinary_sixth_wrong_task_scope_base_and_paths_are_refused(self):
        self.approve()
        for task, plan, work in (
            (TASK, None, WORK), ("other-task", "plan-1", WORK),
            (TASK, "plan-1", {**WORK, "scope": "Other work"}),
            (TASK, "plan-1", {**WORK, "base_revision": HEAD}),
            (TASK, "plan-1", {**WORK, "paths": ["secrets/config.py"]}),
        ):
            with self.subTest(task=task, work=work), self.assertRaises(UsageError):
                validate_work(self.store, self.history, task, 6, plan, work)

    def test_checkpoint_pauses_implementation_even_when_an_audit_runs(self):
        self.seed_checkpoint()
        add_assignment(self.state, AT, "reviewer", "auditor", task=TASK)
        status = task_statuses(self.store, self.history)[TASK]
        self.assertEqual((status["status"], status["paused_work"]), ("waiting_for_operator", "implementation"))

    def test_a_cited_judge_report_still_needs_the_pinned_judge_after_latest_development(self):
        self.exhaust()
        with self.assertRaisesRegex(UsageError, "pinned judge"):
            checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1", "previous_attempts": "Five fixes",
                "progress": "Still blocked", "change_in_approach": "Reassess", "judge_report": str(self.judge_report)}, AT, "judge")

    def test_an_exhausted_allowance_reaches_the_operator_without_a_judge(self):
        # The allowance boundary is a budget decision only the operator makes.
        # Requiring a ruling first spent 16 of one fleet's 30 lifetime judge
        # dispatches on one task (jbaruch/coding-policy#396).
        self.exhaust()
        record = checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1",
            "previous_attempts": "Five fixes", "progress": "Still blocked",
            "change_in_approach": "Reassess"}, AT, "judge")
        self.assertNotIn("judge_evidence", record)
        self.assertNotIn("judge_agent", record)
        self.assertEqual(record["fix_round"], 5)
        status = task_statuses(self.store, self.history)[TASK]
        self.assertEqual((status["status"], status["paused_work"]), ("waiting_for_operator", "implementation"))

    def test_that_checkpoint_carries_the_operator_bounded_approval_through(self):
        self.exhaust()
        checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1",
            "previous_attempts": "Five fixes", "progress": "Still blocked",
            "change_in_approach": "Reassess"}, AT, "judge")
        plan = authorize_plan(self.store, self.history, {"id": "plan-1", "task": TASK, "checkpoint": "cp",
            "scope": WORK["scope"], "allowed_paths": ["src/*"], "additional_fixes": 2, "authorization": AUTH}, AT)
        self.assertEqual((plan["first_fix"], plan["last_fix"]), (6, 7))
        validate_work(self.store, self.history, TASK, 6, "plan-1", WORK)

    def test_an_unexhausted_budget_still_refuses_the_checkpoint(self):
        with self.assertRaisesRegex(UsageError, "not exhausted"):
            checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1",
                "previous_attempts": "None", "progress": "None",
                "change_in_approach": "Reassess"}, AT, "judge")

    def test_an_exhausted_budget_names_the_operator_not_the_judge(self):
        with self.assertRaisesRegex(UsageError, "operator checkpoint"):
            validate_work(self.store, self.history, TASK, 6, None, WORK)

    def replay_data(self):
        return {"id": "checkpoint-5", "task": TASK, "defect": "F1 remains open",
                "previous_attempts": "Five attempts changed parsing and quoting",
                "progress": "Some counterexamples now pass; the quoted case remains red",
                "change_in_approach": "Use one canonical parser", "judge_report": str(self.judge_report)}

    def test_a_checkpoint_written_before_the_bump_migrates_with_its_evidence(self):
        prior = self.seed_checkpoint()
        evidence = copy.deepcopy(prior["judge_evidence"])
        prior["schema_version"] = 1
        self.assertTrue(migrate_store(self.store))
        self.assertEqual(prior["schema_version"], 2)
        self.assertEqual((prior["judge_agent"], prior["judge_evidence"]), ("judge", evidence))
        validate_store(self.store, self.history)
        self.assertFalse(migrate_store(self.store))

    def test_an_older_checkpoint_without_its_required_ruling_refuses_to_migrate(self):
        prior = self.seed_checkpoint()
        prior["schema_version"] = 1
        del prior["judge_evidence"]
        with self.assertRaisesRegex(UsageError, "missing the ruling evidence"):
            migrate_store(self.store)

    def test_replaying_a_migrated_checkpoint_returns_it(self):
        # The row outlives the writer's version: re-running an already-recorded
        # checkpoint must return it, not read the version as changed evidence.
        prior = self.seed_checkpoint()
        prior["schema_version"] = 1
        migrate_store(self.store)
        replay = checkpoint(self.store, self.history, self.replay_data(), AT, "judge")
        self.assertIs(replay, prior)
        self.assertEqual(len(self.store["checkpoints"]), 1)
        validate_store(self.store, self.history)

    def test_reusing_a_checkpoint_identity_for_other_evidence_still_refuses(self):
        self.seed_checkpoint()
        with self.assertRaisesRegex(UsageError, "already describes different evidence"):
            checkpoint(self.store, self.history, {"id": "checkpoint-5", "task": TASK, "defect": "Another defect",
                "previous_attempts": "Five attempts changed parsing and quoting",
                "progress": "Some counterexamples now pass; the quoted case remains red",
                "change_in_approach": "Use one canonical parser", "judge_report": str(self.judge_report)}, AT, "judge")

    def test_a_second_cited_ruling_for_the_task_is_refused(self):
        # The bound is per task: a re-granted budget exhausting again must not
        # buy another ruling (jbaruch/coding-policy#396).
        self.seed_checkpoint()
        for fix in (6, 7):
            add_assignment(self.state, "2026-02-03T12:00:0{}+00:00".format(fix), "developer", "worker", task=TASK, fix_round=fix)
        with self.assertRaisesRegex(UsageError, "at most one per task"):
            checkpoint(self.store, self.history, {"id": "checkpoint-7", "task": TASK, "defect": "F1 still open",
                "previous_attempts": "Seven attempts", "progress": "Unchanged",
                "change_in_approach": "Rewrite the parser", "judge_report": str(self.judge_report)}, AT, "judge")

    def test_a_later_checkpoint_without_a_ruling_still_records(self):
        self.seed_checkpoint()
        for fix in (6, 7):
            add_assignment(self.state, "2026-02-03T12:00:0{}+00:00".format(fix), "developer", "worker", task=TASK, fix_round=fix)
        record = checkpoint(self.store, self.history, {"id": "checkpoint-7", "task": TASK, "defect": "F1 still open",
            "previous_attempts": "Seven attempts", "progress": "Unchanged",
            "change_in_approach": "Rewrite the parser"}, AT, "judge")
        self.assertEqual(record["fix_round"], 7)
        self.assertNotIn("judge_evidence", record)

    def test_a_partial_ruling_trio_on_a_checkpoint_is_refused(self):
        prior = self.seed_checkpoint()
        del prior["judge_evidence"]
        with self.assertRaises(UsageError):
            validate_store(self.store, self.history)

    def test_an_unknown_checkpoint_field_is_refused(self):
        self.exhaust()
        with self.assertRaisesRegex(UsageError, "optional judge_report"):
            checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1",
                "previous_attempts": "Five fixes", "progress": "Still blocked",
                "change_in_approach": "Reassess", "operator_note": "approve"}, AT, "judge")

    def test_retry_returns_the_same_dispatch_and_never_consumes_twice(self):
        self.approve()
        self.finish(6)
        previous = copy.deepcopy(self.state)
        replay = prior_dispatch(self.store, "fix-6", "c" * 64)
        assert replay is not None
        self.assertEqual(replay["status"], "applied")
        self.assertEqual(self.state, previous)
        self.assertEqual(confirmed_fix(self.history, TASK), 6)
        with self.assertRaisesRegex(UsageError, "different inputs"):
            prior_dispatch(self.store, "fix-6", "d" * 64)

    def test_interrupted_send_holds_its_slot_and_a_proven_pre_send_failure_retries(self):
        self.approve()
        record = self.reservation(6)
        reserve(self.store, record, AT)
        abort_pre_send(self.store, record["id"], AT, "clear refused before a brief was sent")
        replay = prior_dispatch(self.store, record["id"], record["fingerprint"])
        assert replay is not None
        self.assertEqual(replay["status"], "not_sent")
        reserve(self.store, record, AT)
        mark_sending(self.store, record["id"], AT, {"cleared": True})
        abort_pre_send(self.store, record["id"], AT, "connection lost after send began")
        with self.assertRaisesRegex(UsageError, "uncertain outcome"):
            prior_dispatch(self.store, record["id"], record["fingerprint"])
        with self.assertRaisesRegex(UsageError, "unresolved dispatch"):
            reserve(self.store, {**record, "id": "another-id"}, AT)
        self.assertEqual(len(self.store["dispatches"]), 1)
        self.assertEqual(confirmed_fix(self.history, TASK), 5)

    def test_release_clear_and_explicit_null_recovery_preserve_original_rows(self):
        add_assignment(self.state, AT, "developer", "worker", task=TASK)
        original = copy.deepcopy(self.history)
        permission = authorize_context(self.store, self.history, {"task": TASK, "assignment_index": 0,
            "reason": "Old dispatch missed its native session ID", "authorization": AUTH, "evidence": str(self.review)}, AT, None)
        self.assertEqual(permission["basis"], "operator_authorized_fresh_handoff")
        self.assertEqual(self.history, original)
        transition = fresh_transition(self.store, self.history, TASK, 1)
        assert transition is not None
        self.assertEqual(transition["reason"], "authorized_context_recovery")
        add_assignment(self.state, "2026-02-03T10:00:01+00:00", "release", "worker", task=TASK, cleared=True, clear_reason="automatic")
        transition = fresh_transition(self.store, self.history, TASK, 1)
        assert transition is not None
        self.assertEqual(transition["release_assignment"], 1)
        self.assertEqual(self.history[0], original[0])

    def test_corrupt_bounds_or_unrecorded_sixth_fix_leave_state_unusable(self):
        self.approve()
        self.finish(6)
        corrupt = copy.deepcopy(self.store)
        corrupt["plans"][0]["last_fix"] = 999
        with self.assertRaises(UsageError):
            validate_store(corrupt, self.history)

    def test_changed_operator_decision_supersedes_bounds_without_erasing_approval(self):
        original = copy.deepcopy(self.approve())
        data = {"id": "plan-2", "task": TASK, "checkpoint": "checkpoint-5", "scope": "A changed correction approach",
                "allowed_paths": ["src/parser.py"], "additional_fixes": 1, "authorization": AUTH}
        with self.assertRaisesRegex(UsageError, "supersedes"):
            authorize_plan(self.store, self.history, data, AT)
        changed = authorize_plan(self.store, self.history, {**data, "supersedes": "plan-1"}, AT)
        self.assertEqual(self.store["plans"][0], original)
        self.assertEqual((changed["first_fix"], changed["last_fix"]), (6, 6))
        with self.assertRaisesRegex(UsageError, "superseded"):
            validate_work(self.store, self.history, TASK, 6, "plan-1", WORK)
        validate_work(self.store, self.history, TASK, 6, "plan-2", {**WORK, "scope": data["scope"]})
        validate_store(self.store, self.history)

    def test_review_receipts_preserve_independence_full_approval_and_current_artifact(self):
        self.approve()
        self.finish(6)
        data = {"dispatch": "fix-6", "head_revision": HEAD, "verdict": "blocking", "review_mode": "full",
                "reviewer": "independent-reviewer", "report": str(self.review), "changed_paths": ["src/parser.py"]}
        for change in ({"reviewer": "worker"}, {"verdict": "approved", "review_mode": "scoped"},
                       {"head_revision": "c" * 40}, {"changed_paths": ["outside/scope.py"]}):
            with self.subTest(change=change), self.assertRaises(UsageError):
                record_report(self.store, {**data, **change}, AT)
        record_report(self.store, data, AT)
        self.review.write_text("This report was replaced after it was recorded.\n")
        with self.assertRaisesRegex(UsageError, "artifact changed"):
            validate_work(self.store, self.history, TASK, 7, "plan-1", WORK)

    def test_future_or_corrupt_recovery_records_preserve_disk_and_refuse_history(self):
        self.approve()
        self.finish(6)
        variants = []
        future = copy.deepcopy(self.state)
        future["recovery"]["plans"][0]["schema_version"] = 2
        variants.append(future)
        invalid_result = copy.deepcopy(self.state)
        invalid_result["recovery"]["dispatches"][0]["result"] = None
        variants.append(invalid_result)
        invalid_version = copy.deepcopy(self.state)
        invalid_version["recovery"]["schema_version"] = True
        variants.append(invalid_version)
        path = self.root / "invalid.json"
        for variant in variants:
            with self.subTest(variant=variant):
                save_state(path, variant)
                before = path.read_bytes()
                warnings = []
                restored, usable = load_state_checked(path, warn=warnings.append)
                self.assertFalse(usable)
                self.assertEqual(restored["assignments"], [])
                self.assertEqual(path.read_bytes(), before)
                self.assertTrue(warnings)
        corrupt = copy.deepcopy(self.store)
        corrupt["dispatches"] = []
        with self.assertRaisesRegex(UsageError, "lacks its owner-managed"):
            validate_store(corrupt, self.history)

    def test_brief_identity_changes_with_content_and_explicit_reuse_is_detectable(self):
        common, brief = self.root / "COMMON.md", self.root / "developer.md"
        common.write_text("Common\n")
        brief.write_text("First attempt\n")
        paths_by_role = {"common": str(common), "developer": str(brief)}
        first = dispatch_identity(TASK, "developer", "worker", 1, paths_by_role, "try-1")
        brief.write_text("Changed attempt\n")
        second = dispatch_identity(TASK, "developer", "worker", 1, paths_by_role, "try-1")
        self.assertEqual(first[0], second[0])
        self.assertNotEqual(first[1], second[1])

    def dispatch_tester(self, number, agent):
        record = {"id": "tester-{}-{}".format(number, agent), "task": TASK, "role": "tester", "agent": agent, "fix_round": None,
                  "fingerprint": ("%02d" % number) * 32, "plan": None, "work": None}
        reserve(self.store, record, AT)
        mark_sending(self.store, record["id"], AT, {"cleared": True})
        add_assignment(self.state, "2026-02-03T12:00:0{}+00:00".format(number), "tester", agent, task=TASK)
        finish_dispatch(self.store, record["id"], {"status": "applied", **{
            key: record[key] for key in ("task", "role", "agent", "fix_round")}}, len(self.history) - 1, AT)
        return record["id"]

    def refusal_receipt(self, agent, name="refusal.json", **overrides):
        path = self.root / name
        path.write_text(json.dumps({"agent": agent, "state": "idle", "report_path": "/reports/tester.md",
                                    "found": False, "elapsed_seconds": 12, "reason": "terminal_provider_refusal", **overrides}))
        return str(path)

    def test_refusal_is_recorded_once_against_its_applied_dispatch(self):
        # coding-policy#399: wait-report's exit 5 went to stdout and nowhere
        # else, so nothing could tell a first refusal from a second.
        first = self.dispatch_tester(1, "codex-a")
        receipt = self.refusal_receipt("codex-a")
        with self.assertRaisesRegex(UsageError, "requires dispatch and receipt"):
            record_refusal(self.store, {"dispatch": first}, AT, "codex")
        with self.assertRaisesRegex(UsageError, "exit-5 output"):
            record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a", "wrong.json", reason="marker_unconfirmed")}, AT, "codex")
        with self.assertRaisesRegex(UsageError, "exit-5 output"):
            record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("someone-else", "other.json")}, AT, "codex")
        with self.assertRaisesRegex(UsageError, "not wait-report JSON"):
            record_refusal(self.store, {"dispatch": first, "receipt": str(self.review)}, AT, "codex")
        result = record_refusal(self.store, {"dispatch": first, "receipt": receipt}, AT, "codex")
        self.assertEqual((result["provider"], result["reason"]), ("codex", "terminal_provider_refusal"))
        self.assertEqual(result["evidence"]["sha256"], hashlib.sha256(Path(receipt).read_bytes()).hexdigest())
        self.assertEqual(record_refusal(self.store, {"dispatch": first, "receipt": receipt}, AT, "codex"), result)
        with self.assertRaisesRegex(UsageError, "different refusal receipt"):
            record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a", "later.json", elapsed_seconds=99)}, AT, "codex")
        self.assertEqual([row["kind"] for row in self.store["events"] if row["kind"] == "provider_refusal_recorded"], ["provider_refusal_recorded"])
        pending = {"id": "tester-pending", "task": TASK, "role": "tester", "agent": "claude-a", "fix_round": None,
                   "fingerprint": "ab" * 32, "plan": None, "work": None}
        reserve(self.store, pending, AT)
        with self.assertRaisesRegex(UsageError, "confirmed applied dispatch"):
            record_refusal(self.store, {"dispatch": "tester-pending", "receipt": self.refusal_receipt("claude-a", "pending.json")}, AT, "claude")
        abort_pre_send(self.store, "tester-pending", AT, "fixture")
        validate_store(self.store, self.history)

    def test_one_refusal_permits_one_move_and_the_second_stops_the_line(self):
        self.assertIsNone(refusal_move(self.store, TASK, "tester", None, "codex"))
        first = self.dispatch_tester(1, "codex-a")
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex")
        with self.assertRaisesRegex(UsageError, "same provider"):
            refusal_move(self.store, TASK, "tester", None, "codex")
        self.assertIsNone(refusal_move(self.store, TASK, "reviewer", None, "codex"))
        self.assertIsNone(refusal_move(self.store, TASK, "tester", 3, "codex"))
        self.assertIsNone(refusal_move(self.store, "another-task", "tester", None, "codex"))
        move = refusal_move(self.store, TASK, "tester", None, "claude")
        self.assertEqual(move, {"schema_version": 1, "from": first, "from_provider": "codex", "provider": "claude"})
        second = self.dispatch_tester(2, "claude-a")
        self.store["dispatches"][-1]["refusal_move"] = move
        validate_store(self.store, self.history)
        record_refusal(self.store, {"dispatch": second, "receipt": self.refusal_receipt("claude-a", "second.json")}, AT, "claude")
        with self.assertRaisesRegex(UsageError, "refused by 2 providers") as caught:
            refusal_move(self.store, TASK, "tester", None, "grok")
        self.assertEqual(caught.exception.details["refusals"], [first, second])
        with self.assertRaisesRegex(UsageError, "refused by 2 providers"):
            refusal_move(self.store, TASK, "tester", None, "codex")
        validate_store(self.store, self.history)

    def test_a_second_move_waits_for_the_first_moves_outcome(self):
        first = self.dispatch_tester(1, "codex-a")
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex")
        move = refusal_move(self.store, TASK, "tester", None, "claude")
        pending = {"id": "tester-move", "task": TASK, "role": "tester", "agent": "claude-a", "fix_round": None,
                   "fingerprint": "ab" * 32, "plan": None, "work": None, "refusal_move": move}
        reserve(self.store, pending, AT)
        with self.assertRaisesRegex(UsageError, "already moved to provider claude"):
            refusal_move(self.store, TASK, "tester", None, "grok")
        abort_pre_send(self.store, "tester-move", AT, "fixture")
        retry = refusal_move(self.store, TASK, "tester", None, "grok")
        assert retry is not None
        self.assertEqual(retry["from"], first)
        second = self.dispatch_tester(2, "claude-a")
        self.store["dispatches"][-1]["refusal_move"] = move
        with self.assertRaisesRegex(UsageError, "already moved to provider claude") as caught:
            refusal_move(self.store, TASK, "tester", None, "grok")
        self.assertEqual(caught.exception.details["moves"], [second])
        record_refusal(self.store, {"dispatch": second, "receipt": self.refusal_receipt("claude-a", "second.json")}, AT, "claude")
        with self.assertRaisesRegex(UsageError, "refused by 2 providers"):
            refusal_move(self.store, TASK, "tester", None, "grok")
        validate_store(self.store, self.history)

    def test_store_six_migration_stamps_a_clean_five_and_refuses_unowned_refusals(self):
        first = self.dispatch_tester(1, "codex-a")
        old = copy.deepcopy(self.store)
        old["schema_version"] = 5
        before = copy.deepcopy(old)
        self.assertTrue(migrate_store(old))
        self.assertEqual(old["schema_version"], 6)
        self.assertEqual({key: value for key, value in old.items() if key != "schema_version"},
                         {key: value for key, value in before.items() if key != "schema_version"})
        validate_store(old, self.history)
        self.assertFalse(migrate_store(old))
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex")
        for version in (4, 5):
            stale = copy.deepcopy(self.store)
            stale["schema_version"] = version
            with self.assertRaisesRegex(UsageError, "unowned newer refusal"):
                migrate_store(stale)
        with self.assertRaisesRegex(UsageError, "Unsupported recovery schema"):
            validate_store({**copy.deepcopy(self.store), "schema_version": 5}, self.history)

    def test_corrupt_refusal_records_refuse_the_ledger(self):
        first = self.dispatch_tester(1, "codex-a")
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex")
        second = self.dispatch_tester(2, "claude-a")
        self.store["dispatches"][-1]["refusal_move"] = refusal_move(self.store, TASK, "tester", None, "claude")
        validate_store(self.store, self.history)
        for mutate in (
            lambda row: row["refusal"].update(reason="something_else"),
            lambda row: row["refusal"].update(evidence="not-a-receipt"),
            lambda row: row.update(status="not_sent", result=None),
        ):
            corrupt = copy.deepcopy(self.store)
            mutate(next(row for row in corrupt["dispatches"] if row["id"] == first))
            with self.assertRaises(UsageError):
                validate_store(corrupt, self.history)
        for mutate in (
            lambda row: row["refusal_move"].update({"from": second}),
            lambda row: row["refusal_move"].update(provider="codex"),
            lambda row: row["refusal_move"].update(from_provider="claude"),
            lambda row: row["refusal_move"].update(schema_version=2),
        ):
            corrupt = copy.deepcopy(self.store)
            mutate(next(row for row in corrupt["dispatches"] if row["id"] == second))
            with self.assertRaises(UsageError):
                validate_store(corrupt, self.history)


if __name__ == "__main__":
    unittest.main()
