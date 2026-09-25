"""Public owner-command workflows with controlled Herdr and report fixtures."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import io
import json
from pathlib import Path
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from teamlead.errors import UsageError
from teamlead.state import add_assignment, empty_state, save_state, state_lock
from tests import test_cli as fixture
from tests.fakes import FakeRunner, ScriptedReads, agent_json

AT = fixture.AT
TASK = "recovery-fixture"
BASE = "a" * 40
HEAD = "b" * 40
AUTH = {"source": "fixture operator message", "quote": "Approve this task and the stated correction bounds."}
REQUEST = {"source": "fixture operator message", "quote": "Ask the judge to rule on this exhaustion."}
WORK = {"base_revision": BASE, "scope": "Correct parser findings", "paths": ["src/parser.py"], "findings": ["F1"]}


class RecoveryCommandTests(fixture.CliCase):
    _client = fixture.ApplyCommandTest._client

    def setUp(self):
        super().setUp()
        self.runner = FakeRunner()
        self.briefs["release"] = self.tmp / "release.md"
        self.briefs["release"].write_text("Release the verified current tip; stop for any source correction.\n")
        self.evidence = self.tmp / "evidence.md"
        self.evidence.write_text("Fixture evidence: the previous confirmed developer dispatch has completed.\n")
        self.work = self.tmp / "work.json"
        self.work.write_text(json.dumps(WORK))

    def invoke(self, args, client=None):
        self.out, self.err = io.StringIO(), io.StringIO()
        return self.run_cli(self.base() + args, client=client)

    def owner(self, command, data, client=None):
        path = self.tmp / (command + ".json")
        path.write_text(json.dumps(data))
        return self.invoke([command, "--record", str(path), "--now", AT], client=client)

    def register(self):
        code, _, err = self.owner("task", {"task": TASK, "base_revision": BASE, "scope": WORK["scope"],
                                           "allowed_paths": ["src/*"], "authorization": AUTH})
        self.assertEqual(code, 0, err)

    def investigation(self):
        """A real investigator report the diagnosis verifies live."""
        path = self.tmp / "investigation.md"
        if not path.exists():
            path.write_text("Reproduction, causal assessment and discriminating experiment.\n")
        return str(path)

    def investigation_sha(self):
        import hashlib
        import pathlib
        return hashlib.sha256(pathlib.Path(self.investigation()).read_bytes()).hexdigest()

    def record_investigation(self):
        """Seed the assessed investigator consultation #408 requires.

        The assessment machinery has its own suite; this fixture only needs
        the record the diagnosis gate reads.
        """
        state = self.saved()
        add_assignment(state, "2026-02-03T13:00:00+00:00", "investigator", "grok", task=TASK)
        index = len(state["assignments"]) - 1
        row = state["assignments"][index]
        row["status"] = "applied"
        state["recovery"]["dispatches"].append({
            "schema_version": 1, "at": "2026-02-03T13:00:00+00:00", "id": "investigator-dispatch",
            "fingerprint": "e" * 64, "role": "investigator", "agent": "grok", "task": TASK,
            "fix_round": None, "plan": None, "work": None, "status": "applied",
            "assignment_index": index,
            "result": {"schema_version": 1, "task": TASK, "role": "investigator", "agent": "grok",
                       "fix_round": None, "status": "applied"}, "report": None})
        state["specialist_assessments"].append({
            "schema_version": 1, "at": "2026-02-03T14:00:00+00:00", "id": "inv-1", "dispatch": "investigator-dispatch",
            "assignment_index": index, "task": TASK, "role": "investigator",
            "agent": "grok", "report": self.investigation(), "delivery": "/reports/delivery.json",
            "outcome": "delivered", "contribution": "design", "summary": "The find-rate tracks review surface area.",
            "report_evidence": {"path": self.investigation(), "sha256": self.investigation_sha()},
            "delivery_evidence": {"path": "/reports/delivery.json", "sha256": "b" * 64}})
        save_state(self.state, state)

    def saved(self):
        return json.loads(self.state.read_text())

    def apply_args(self, role="developer", fix=None, *extra):
        # Each dispatch is a distinct fixed-clock event, including release.
        count = len(self.saved()["assignments"]) if self.state.exists() else 0
        at = (datetime.fromisoformat(AT) + timedelta(seconds=count)).isoformat()
        result = ["apply", "--assignments", json.dumps({role: "grok"}), "--common", str(self.common),
                  "--brief", role + "=" + str(self.briefs[role]), "--task", TASK, "--now", at,
                  "--composer-settle", "0", *extra]
        return result + (["--fix-round", str(fix)] if fix else [])

    def fresh_client(self, before, after):
        client = self._client({"grok": "idle"})
        self.runner.responses["agent get grok"] = ScriptedReads([
            agent_json("grok", "idle", "w4:p1", before),
            agent_json("grok", "idle", "w4:p1", after),
        ])
        return client

    def seed_cap(self, diagnosis_only=False, skip_diagnosis=False):
        state = empty_state()
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(state, "2026-02-03T09:00:0{}+00:00".format(fix or 0), "developer", "grok", task=TASK, fix_round=fix)
        save_state(self.state, state)
        self.register()
        config = json.loads(self.config.read_text())
        config["judge"] = {"agent": "claude", "model": "claude-opus-4-6", "effort": "high"}
        self.config.write_text(json.dumps(config))
        # The consultation precedes the judge dispatch that rules on it (#408).
        self.record_investigation()
        state = self.saved()
        add_assignment(state, "2026-02-03T15:00:00+00:00", "judge", "claude", task=TASK, judge_mode="diagnosis")
        # The dispatch the enrollment is keyed to: #412 resolves the judge's
        # enrollment by this identity, not by newest-for-task-and-agent.
        judge_index = len(state["assignments"]) - 1
        state["recovery"]["dispatches"].append({
            "schema_version": 1, "at": "2026-02-03T15:00:00+00:00", "id": "judge-dispatch",
            "fingerprint": "f" * 64, "role": "judge", "agent": "claude", "task": TASK,
            "fix_round": None, "plan": None, "work": None, "status": "applied",
            "assignment_index": judge_index,
            "result": {"schema_version": 1, "task": TASK, "role": "judge", "agent": "claude",
                       "fix_round": None, "status": "applied"}, "report": None})
        save_state(self.state, state)
        judge = self.tmp / "judge.md"
        judge.write_text("RULING: amend — correct F1\nACTION: Use one canonical parser\n")
        code, _, err = self.owner("checkpoint", {"id": "cap-5", "task": TASK, "defect": "F1 is still blocking",
            "previous_attempts": "Five fixes changed parser handling", "progress": "Most fixtures now pass",
            "change_in_approach": "Use a single parser", "judge_report": str(judge),
            "requested_by": REQUEST})
        self.assertEqual(code, 0, err)
        # The operator's budget overrides a recorded remedy; a bound round
        # enrolls the judge's report before its diagnosis (#407).
        diagnosis = self.tmp / "diagnosis.md"
        diagnosis.write_text("DIAGNOSIS: the find-rate held flat\nREMEDY: continue — two more rounds\n"
                             "BOUND: 1 — one attempt per open finding\nASSESSMENT: " + self.investigation() + "\nEVIDENCE: rounds 1-5\nUNVERIFIED: none\n")
        if skip_diagnosis:
            return
        code, _, err = self.owner("diagnose", {"id": "diag-cap", "task": TASK, "checkpoint": "cap-5",
            "judge_report": str(diagnosis), "scope": WORK["scope"], "allowed_paths": ["src/*"]})
        self.assertEqual(code, 0, err)
        if diagnosis_only:
            return
        code, _, err = self.owner("authorize-corrections", {"id": "two-fixes", "task": TASK, "checkpoint": "cap-5",
            "scope": WORK["scope"], "allowed_paths": ["src/*"], "additional_fixes": 2, "authorization": AUTH,
            "supersedes": "diag-cap:plan"})
        self.assertEqual(code, 0, err)

    def test_a_later_correction_over_a_legacy_dispatchs_source_paths_is_frozen(self):
        # coding-policy#460 review: only the recorded dispatch's complete
        # identity keeps its source paths. A correction reusing the same brief
        # files, even byte-identical, is new work and reads a frozen copy.
        self.register()
        with patch("teamlead.cli.freeze_paths", side_effect=lambda paths: paths):
            code, _, err = self.invoke(self.apply_args(), self.fresh_client("previous-task", "developer-0"))
        self.assertEqual(code, 0, err)
        legacy = self.saved()["recovery"]["dispatches"][-1]
        self.assertEqual(legacy["brief"], str(self.briefs["developer"]))
        # Replaying that exact dispatch keeps its recorded paths and its receipt.
        code, out, err = self.invoke(self.apply_args(), self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["replayed"])
        self.assertEqual(len(self.saved()["recovery"]["dispatches"]), 1)
        code, _, err = self.invoke(self.apply_args("release"), self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        code, _, err = self.invoke(self.apply_args("developer", 1), self.fresh_client("release-session", "developer-1"))
        self.assertEqual(code, 0, err)
        correction = self.saved()["recovery"]["dispatches"][-1]
        self.assertEqual(correction["fix_round"], 1)
        self.assertEqual(Path(correction["brief"]).parent.name, ".dispatched")
        self.assertEqual(Path(correction["brief"]).read_bytes(), self.briefs["developer"].read_bytes())

    def test_two_release_fresh_fix_cycles_preserve_task_base_history_and_next_number(self):
        self.register()
        code, _, err = self.invoke(self.apply_args(), self.fresh_client("previous-task", "developer-0"))
        self.assertEqual(code, 0, err)
        for number in (1, 2):
            with self.subTest(number=number):
                self.briefs["release"].write_text(f"Release verified tip after developer {number - 1}.\n")
                code, _, err = self.invoke(self.apply_args("release"), self._client({"grok": "idle"}))
                self.assertEqual(code, 0, err)
                before = self.saved()["assignments"]
                self.assertEqual(before[-1]["role"], "release")
                self.assertTrue(before[-1]["cleared"])
                self.briefs["developer"].write_text(f"Fix blocking review finding F{number}; prior attempts: {number - 1}.\n")
                code, out, err = self.invoke(self.apply_args("developer", number), self.fresh_client("release-session", f"developer-{number}"))
                self.assertEqual(code, 0, err)
                result = json.loads(out)["applied"][0]
                self.assertEqual(result["context_transition"]["reason"], "release_handoff")
                self.assertEqual((result["fix_round"], result["context_session"]["value"]), (number, f"developer-{number}"))
                after = self.saved()
                self.assertEqual(after["assignments"][:-1], before)
                self.assertEqual(after["recovery"]["tasks"][TASK]["base_revision"], BASE)
                self.assertEqual(after["recovery"]["context_permissions"], [])
        code, _, err = self.invoke(self.apply_args("developer", 3, "--retain-context"),
                                   self._client({"grok": "idle"}, sessions={"grok": "developer-2"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.runner.writes()), 1)
        self.assertEqual(self.saved()["assignments"][-1]["fix_round"], 3)

    def test_null_session_recovery_records_observation_without_rewriting_original_proof(self):
        state = empty_state()
        add_assignment(state, AT, "developer", "grok", task=TASK)
        save_state(self.state, state)
        original = self.saved()["assignments"][0]
        self.register()
        data = {"task": TASK, "assignment_index": 0, "reason": "First prompt did not expose a native ID",
                "authorization": AUTH, "evidence": str(self.evidence)}
        code, out, err = self.owner("recover-context", data, self._client({"grok": "idle"}, sessions={"grok": "observed-later"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["observed_session"]["value"], "observed-later")
        self.assertEqual(self.saved()["assignments"][0], original)
        self.assertEqual(self.runner.writes(), [])
        code, out, err = self.invoke(self.apply_args("developer", 1), self.fresh_client("observed-later", "fresh-recovery"))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["applied"][0]["context_transition"]["reason"], "authorized_context_recovery")
        self.assertEqual(self.saved()["assignments"][0], original)

    def test_apply_refuses_a_judge_seat_before_the_assessment_exists(self):
        # coding-policy#408: the dispatch path, not only the record, so the
        # expensive seat is never spent on an uninvestigated loop.
        state = empty_state()
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(state, "2026-02-03T09:00:0{}+00:00".format(fix or 0), "developer", "grok", task=TASK, fix_round=fix)
        save_state(self.state, state)
        self.register()
        config = json.loads(self.config.read_text())
        config["judge"] = {"agent": "claude", "model": "claude-opus-4-6", "effort": "high"}
        self.config.write_text(json.dumps(config))
        self.briefs["judge"] = self.tmp / "judge-brief.md"
        self.briefs["judge"].write_text("# judge\n")
        args = ["apply", "--assignments", json.dumps({"judge": "claude"}), "--common", str(self.common),
                "--brief", "judge=" + str(self.briefs["judge"]), "--task", TASK, "--now", AT,
                "--judge-mode", "diagnosis", "--composer-settle", "0"]
        for extra in ((), ("--dry-run",)):
            code, out, err = self.invoke(args + list(extra), self._client({"claude": "idle"}))
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("consult the investigator", err)
        self.record_investigation()
        code, out, err = self.invoke(args + ["--dry-run"], self._client({}))
        self.assertEqual(code, 0, err)

    def seat_judge(self):
        """An exhausted task with its investigation assessed, and a judge seat ready."""
        state = empty_state()
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(state, "2026-02-03T09:00:0{}+00:00".format(fix or 0), "developer", "grok", task=TASK, fix_round=fix)
        save_state(self.state, state)
        self.register()
        config = json.loads(self.config.read_text())
        config["judge"] = {"agent": "claude", "model": "claude-opus-4-6", "effort": "high"}
        self.config.write_text(json.dumps(config))
        self.briefs["judge"] = self.tmp / "judge-brief.md"
        self.briefs["judge"].write_text("# judge\n")
        self.record_investigation()
        client = self._client({"claude": "idle"}, sessions={"claude": "judge-native"})
        self.runner.set("pane process-info --pane w2:p1", json.dumps({"result": {"process_info": {
            "pane_id": "w2:p1", "foreground_processes": [{"name": "claude", "pid": 200,
            "argv": ["claude", "--dangerously-skip-permissions", "--model", "claude-opus-4-6",
                     "--effort", "high"]}]}}}))
        return client

    def judge_args(self, mode, *extra):
        return ["apply", "--assignments", json.dumps({"judge": "claude"}), "--common", str(self.common),
                "--brief", "judge=" + str(self.briefs["judge"]), "--task", TASK, "--now", AT,
                "--judge-mode", mode, "--composer-settle", "0", "--no-clear", *extra]

    def test_an_interrupted_judge_recovers_the_mode_it_was_sent_for(self):
        # coding-policy#494 review: the mode must survive a send that never
        # confirmed, or reconciliation records the dispatch as `unknown`.
        client = self.seat_judge()
        args = self.judge_args("diagnosis", "--dispatch-id", "interrupted-judge")
        with patch("teamlead.assign.send_message", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.invoke(args, client)
        dispatch = self.saved()["recovery"]["dispatches"][-1]
        self.assertEqual((dispatch["status"], dispatch["judge_mode"]), ("sending", "diagnosis"))
        self.assertEqual(dispatch["schema_version"], 3)
        self.assertEqual(dispatch["context_before_send"]["judge_mode"], "diagnosis")
        data = {"dispatch": "interrupted-judge:judge", "outcome": "applied",
                "reason": "Original report proves delivery and completion",
                "authorization": AUTH, "evidence": str(self.evidence)}
        code, _, err = self.owner("reconcile", data, self._client({"claude": "idle"}, sessions={"claude": "later"}))
        self.assertEqual(code, 0, err)
        row = self.saved()["assignments"][-1]
        self.assertEqual((row["role"], row["judge_mode"]), ("judge", "diagnosis"))
        dispatch = self.saved()["recovery"]["dispatches"][-1]
        self.assertEqual((dispatch["result"]["schema_version"], dispatch["result"]["judge_mode"]), (3, "diagnosis"))
        # A saved result naming the other mode is an inconsistent record, not
        # a second truth: the ledger refuses to read it.
        document = self.saved()
        document["recovery"]["dispatches"][-1]["result"]["judge_mode"] = "adjudication"
        self.state.write_text(json.dumps(document))
        from teamlead.state import load_state_checked
        _state, usable = load_state_checked(self.state, warn=lambda _message: None)
        self.assertFalse(usable)

    def test_a_dispatch_records_and_sends_a_frozen_copy_of_its_brief(self):
        # coding-policy#460: the worker reads the bytes preflight checked, even
        # if the source brief is rewritten after the send.
        client = self.seat_judge()
        checked = self.briefs["judge"].read_bytes()
        code, _, err = self.invoke(self.judge_args("diagnosis"), client)
        self.assertEqual(code, 0, err)
        row = self.saved()["recovery"]["dispatches"][-1]
        frozen = Path(row["brief"])
        self.assertEqual(frozen.parent.name, ".dispatched")
        self.assertEqual(frozen.read_bytes(), checked)
        self.assertIn(str(frozen), "".join(self.runner.pasted_prompts()))
        self.briefs["judge"].write_text("rewritten after the send\n")
        self.assertEqual(frozen.read_bytes(), checked)

    def test_a_judge_dispatch_from_before_the_mode_is_never_sent_twice(self):
        # coding-policy#494 review: the mode joined the fingerprint, so an
        # older judge dispatch no longer matches by identity. Running the same
        # brief again must stop at that record, not send the round again.
        client = self.seat_judge()
        code, _, err = self.invoke(self.judge_args("diagnosis"), client)
        self.assertEqual(code, 0, err)
        document = self.saved()
        row = document["recovery"]["dispatches"][-1]
        from teamlead import recovery as recovery_module
        options = {"task": TASK, "fix_round": None, "plan": None, "work": None, "rounds": {},
                   "retain_context": False, "no_clear": True}
        _, legacy = recovery_module.dispatch_identity(
            TASK, "judge", "claude", None, {"common": str(self.common), "judge": str(self.briefs["judge"])},
            options=options)
        # Rewrite the recorded dispatch as its pre-12 self: version 1, no mode,
        # and the source brief paths every dispatch recorded before #460's freeze.
        row["fingerprint"] = legacy
        row["brief"], row["common"] = str(self.briefs["judge"]), str(self.common)
        row["schema_version"] = 1
        del row["judge_mode"]
        del row["result"]["judge_mode"]
        row["result"]["schema_version"] = 1
        row["context_before_send"].pop("judge_mode", None)
        document["assignments"][row["assignment_index"]]["judge_mode"] = "unknown"
        self.state.write_text(json.dumps(document))
        code, out, err = self.invoke(self.judge_args("diagnosis"), self._client({"claude": "idle"}))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("before its mode was part of its identity", err)
        self.assertEqual(self.runner.writes(), [])

    def test_a_legacy_judge_dispatch_that_never_sent_stays_retryable(self):
        # `not_sent` reached no worker; the upgrade guard must not strand it.
        client = self.seat_judge()
        document = json.loads(self.state.read_text())
        from teamlead import recovery as recovery_module
        options = {"task": TASK, "fix_round": None, "plan": None, "work": None, "rounds": {},
                   "retain_context": False, "no_clear": True}
        _, legacy = recovery_module.dispatch_identity(
            TASK, "judge", "claude", None, {"common": str(self.common), "judge": str(self.briefs["judge"])},
            options=options)
        document["recovery"]["dispatches"].append({
            "schema_version": 1, "at": AT, "id": "legacy-judge", "fingerprint": legacy, "task": TASK,
            "role": "judge", "agent": "claude", "fix_round": None, "plan": None, "work": None,
            "status": "not_sent", "result": None, "report": None})
        self.state.write_text(json.dumps(document))
        code, out, err = self.invoke(self.judge_args("diagnosis"), client)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["applied"][0]["judge_mode"], "diagnosis")

    def test_a_direct_judge_dispatch_without_a_mode_is_refused_before_input(self):
        from teamlead.assign import apply as apply_assignments
        client = self.seat_judge()
        with self.assertRaisesRegex(UsageError, "declares its mode"):
            from teamlead.config import load_config
            agents = {agent.name: agent for agent in load_config(self.config)}
            apply_assignments(client, {"judge": "claude"}, agents, {}, AT)
        self.assertEqual(self.runner.writes(), [])

    def test_the_same_brief_under_another_mode_is_another_dispatch(self):
        # coding-policy#494 review: without the mode in the identity, a
        # diagnosis would replay a completed adjudication's receipt.
        from teamlead import recovery as recovery_module
        brief = self.tmp / "same-brief.md"
        brief.write_text("# judge\n")
        paths = {"common": str(self.common), "judge": str(brief)}
        adjudication = recovery_module.dispatch_identity(TASK, "judge", "claude", None, paths,
                                                         options={"judge_mode": "adjudication"})
        diagnosis = recovery_module.dispatch_identity(TASK, "judge", "claude", None, paths,
                                                      options={"judge_mode": "diagnosis"})
        self.assertNotEqual(adjudication[1], diagnosis[1])

    def test_an_older_store_carrying_a_judge_mode_is_refused_as_newer_data(self):
        from teamlead import recovery as recovery_module
        store = recovery_module.empty_recovery()
        store["schema_version"] = 11
        store["dispatches"].append({"schema_version": 1, "id": "d", "role": "judge", "agent": "claude",
                                    "task": TASK, "status": "sending", "judge_mode": "diagnosis"})
        with self.assertRaisesRegex(UsageError, "judge mode this version never wrote"):
            recovery_module.migrate_store(store)

    def test_an_older_store_carrying_a_task_closure_is_refused_as_newer_data(self):
        from teamlead import recovery as recovery_module
        store = recovery_module.empty_recovery()
        store["schema_version"] = 12
        store["events"].append({"schema_version": 1, "sequence": 1, "at": AT, "kind": "task_closed",
                                "task": TASK, "details": {"outcome": "merged", "evidence": "pr"}})
        with self.assertRaisesRegex(UsageError, "task closure this version never wrote"):
            recovery_module.migrate_store(store)

    def test_a_version_twelve_store_with_judge_modes_still_migrates(self):
        # Judge modes are owned since store version 12; bumping past it must
        # upgrade such a store, not refuse it as newer data.
        from teamlead import recovery as recovery_module
        store = recovery_module.empty_recovery()
        store["schema_version"] = 12
        store["dispatches"].append({"schema_version": 3, "id": "d", "role": "judge", "agent": "claude",
                                    "task": TASK, "status": "sending", "judge_mode": "diagnosis"})
        self.assertTrue(recovery_module.migrate_store(store))
        self.assertEqual(store["schema_version"], recovery_module.RECOVERY_STORE_VERSION)
        self.assertEqual(store["dispatches"][0]["judge_mode"], "diagnosis")

    def test_a_version_eleven_store_with_seated_dispatches_still_migrates(self):
        # Seats have been owned since store version 10. A version bump after it
        # must upgrade a seated store, not refuse it as newer data.
        from teamlead import recovery as recovery_module
        for version in (10, 11):
            with self.subTest(version=version):
                store = recovery_module.empty_recovery()
                store["schema_version"] = version
                if version == 10:
                    del store["approaches"]
                store["dispatches"].append({"schema_version": 1, "id": "d", "role": "reviewer#api",
                                            "agent": "claude", "task": TASK, "status": "applied",
                                            "result": {"schema_version": 1, "role": "reviewer#api"}})
                self.assertTrue(recovery_module.migrate_store(store))
                self.assertEqual(store["schema_version"], recovery_module.RECOVERY_STORE_VERSION)
                self.assertEqual(store["dispatches"][0]["role"], "reviewer#api")

    def test_a_live_judge_dispatch_records_its_declared_mode_on_the_assignment(self):
        # coding-policy#478: 51 recorded judge rounds, none of them saying
        # which mode they ran. The ledger is where an adjudication and a
        # diagnosis stay distinguishable after the fact.
        client = self.seat_judge()
        code, out, err = self.invoke(self.judge_args("diagnosis"), client)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["applied"][0]["judge_mode"], "diagnosis")
        row = self.saved()["assignments"][-1]
        self.assertEqual((row["role"], row["judge_mode"]), ("judge", "diagnosis"))

    def test_diagnose_wires_the_pinned_judge_and_its_enrolled_report(self):
        # coding-policy#407: the public command, not just the owner function —
        # pinned-judge loading, the enrollment lookup, and state persistence.
        self.seed_cap(diagnosis_only=True)
        saved = self.saved()["recovery"]
        record = next(row for row in saved["diagnoses"] if row["id"] == "diag-cap")
        self.assertEqual((record["remedy"], record["bound"], record["judge_agent"]), ("continue", 1, "claude"))
        self.assertEqual(record["plan"], "diag-cap:plan")
        plan = next(row for row in saved["plans"] if row["id"] == "diag-cap:plan")
        self.assertEqual((plan["first_fix"], plan["last_fix"]), (6, 6))
        self.assertEqual(plan["authorization"]["source"], record["judge_evidence"]["path"])
        # Its own bound is unspent, so a second diagnosis is refused here too.
        code, _, err = self.owner("diagnose", {"id": "diag-again", "task": TASK, "checkpoint": "cap-5",
            "judge_report": record["judge_evidence"]["path"], "scope": WORK["scope"], "allowed_paths": ["src/*"]})
        self.assertEqual(code, 1)
        self.assertIn("unspent attempts under plan diag-cap:plan", err)

    def test_a_bound_lead_must_cite_the_enrolled_report(self):
        # coding-policy#407: every team round is supervised, so the public
        # command resolves the enrollment and refuses anything else.
        from teamlead import supervision
        self.seed_cap(diagnosis_only=True, skip_diagnosis=True)
        # Restored on teardown: a leaked path outlives this test's temp dir.
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.tmp / "xdg")})
        environment.start()
        self.addCleanup(environment.stop)
        who = supervision.identity("lead-native", str(self.tmp), "fixture", pane_id="lead-pane")
        supervision.bind(self.state, who, AT, root=self.tmp / "supervision-bindings")
        delivered = self.tmp / "delivered-diagnosis.md"
        delivered.write_text("DIAGNOSIS: flat find-rate\nREMEDY: continue — two more rounds\n"
                             "BOUND: 2 — one attempt per open finding\nASSESSMENT: " + self.investigation() + "\nEVIDENCE: rounds 1-5\nUNVERIFIED: none\n")
        supervision.enroll(self.state, {"id": "judge-dispatch", "agent": "claude", "task": TASK,
                                        "report": str(delivered), "pane_id": None, "native_session": None}, AT)
        other = self.tmp / "elsewhere.md"
        other.write_text(delivered.read_text())
        code, _, err = self.owner("diagnose", {"id": "diag-wrong", "task": TASK, "checkpoint": "cap-5",
            "judge_report": str(other), "scope": WORK["scope"], "allowed_paths": ["src/*"]})
        self.assertEqual(code, 1)
        self.assertIn("supervision enrolled", err)
        code, out, err = self.owner("diagnose", {"id": "diag-bound", "task": TASK, "checkpoint": "cap-5",
            "judge_report": str(delivered), "scope": WORK["scope"], "allowed_paths": ["src/*"]})
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["remedy"], "continue")

    def test_apply_reads_the_plan_s_mode_when_no_flag_is_passed(self):
        # coding-policy#425: a planned diagnosis dispatched without the flag
        # must still meet the diagnosis gates, and a planned adjudication must
        # still be exempt from the assessment requirement.
        self.register()
        config = json.loads(self.config.read_text())
        config["judge"] = {"agent": "claude", "model": "claude-opus-4-6", "effort": "high"}
        self.config.write_text(json.dumps(config))
        self.briefs["judge"] = self.tmp / "judge-brief.md"
        self.briefs["judge"].write_text("# judge\n")
        state = empty_state()
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(state, "2026-02-03T09:00:0{}+00:00".format(fix or 0), "developer", "grok",
                           task=TASK, fix_round=fix)
        save_state(self.state, state)
        code, _, err = self.owner("task", {"task": TASK, "base_revision": BASE, "scope": WORK["scope"],
                                           "allowed_paths": WORK["paths"], "authorization": AUTH})
        self.assertEqual(code, 0, err)

        def plan_with(mode):
            path = self.tmp / ("plan-" + mode + ".json")
            path.write_text(json.dumps({"schema_version": 6, "assignments": {"judge": "claude"},
                "judge": {"agent": "claude", "model": "claude-opus-4-6", "effort": "high", "mode": mode}}))
            return ["apply", "--assignments", str(path), "--common", str(self.common),
                    "--brief", "judge=" + str(self.briefs["judge"]), "--task", TASK, "--now", AT,
                    "--composer-settle", "0", "--dry-run"]

        # No flag, planned diagnosis: the assessment gate still applies.
        code, out, err = self.invoke(plan_with("diagnosis"), self._client({"claude": "idle"}))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("consult the investigator", err)
        # No flag, planned adjudication: exempt, and it reaches the dispatch.
        code, _, err = self.invoke(plan_with("adjudication"), self._client({}))
        self.assertEqual(code, 0, err)

    def test_an_older_enrollment_cannot_stand_in_for_this_judge_dispatch(self):
        # coding-policy#412: resolving the enrollment by newest-for-task-and-
        # agent accepted a stale member when the dispatch persisted and its own
        # enrollment then failed. The current dispatch's identity decides it.
        from teamlead import supervision
        self.seed_cap(diagnosis_only=True, skip_diagnosis=True)
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.tmp / "xdg")})
        environment.start()
        self.addCleanup(environment.stop)
        who = supervision.identity("lead-native", str(self.tmp), "fixture", pane_id="lead-pane")
        supervision.bind(self.state, who, AT, root=self.tmp / "supervision-bindings")
        stale = self.tmp / "stale-diagnosis.md"
        stale.write_text("DIAGNOSIS: flat find-rate\nREMEDY: continue — two more rounds\n"
                         "BOUND: 2 — one attempt per open finding\nASSESSMENT: " + self.investigation()
                         + "\nEVIDENCE: rounds 1-5\nUNVERIFIED: none\n")
        # An enrollment for the same task and judge, under another dispatch id,
        # and the newest member on record.
        supervision.enroll(self.state, {"id": "older-judge-dispatch", "agent": "claude", "task": TASK,
                                        "report": str(stale), "pane_id": None, "native_session": None}, AT)
        code, _, err = self.owner("diagnose", {"id": "diag-stale", "task": TASK, "checkpoint": "cap-5",
            "judge_report": str(stale), "scope": WORK["scope"], "allowed_paths": ["src/*"]})
        self.assertEqual(code, 1)
        self.assertIn("no supervision enrollment binds a report", err)

    def test_a_stop_remedy_files_a_user_attention_obligation(self):
        # coding-policy#415: `stop` is terminal and waits on nobody, but the
        # operator still holds the override and cannot exercise one they never
        # learn they have.
        from teamlead import attention
        self.seed_cap(skip_diagnosis=True)
        stop = self.tmp / "stop-diagnosis.md"
        stop.write_text("DIAGNOSIS: the review surface is the cause\nREMEDY: stop — ship the parser, track F1\n"
                        "BOUND: none\nASSESSMENT: " + self.investigation() + "\n"
                        "EVIDENCE: rounds 1-5\nUNVERIFIED: none\n")
        # A free-text diagnosis identity still yields a valid obligation id.
        request = {"id": "diag stop 1", "task": TASK, "checkpoint": "cap-5", "judge_report": str(stop),
                   "scope": WORK["scope"], "allowed_paths": ["src/*"]}
        code, _, err = self.owner("diagnose", request)
        self.assertEqual(code, 0, err)
        _document, entries, _progress = attention.load(self.state)
        self.assertEqual(len(entries), 1)
        entry = next(iter(entries.values()))
        self.assertEqual((entry["kind"], entry["task"], entry["status"]), ("failure", TASK, "open"))
        self.assertNotIn(entry["kind"], attention.GATING_KINDS)
        self.assertIn("diag stop 1", entry["context"])
        self.assertEqual(entry["sources"][0]["ref"], str(stop))
        # The obligation refuses no further dispatch on the task.
        self.assertEqual(attention.dispatch_gate(self.state, TASK, AT), [])
        # Replaying the diagnosis records no second obligation.
        code, _, err = self.owner("diagnose", request)
        self.assertEqual(code, 0, err)
        _document, replayed, _progress = attention.load(self.state)
        self.assertEqual(list(replayed), list(entries))

    def test_diagnose_refuses_a_report_the_pinned_judge_did_not_deliver(self):
        self.seed_cap(diagnosis_only=True)
        other = self.tmp / "other-diagnosis.md"
        other.write_text("DIAGNOSIS: x\nREMEDY: restructure — split the surface\n"
                         "BOUND: 1 — one attempt per open finding\nASSESSMENT: " + self.investigation() + "\n"
                         "EVIDENCE: rounds 1-5\nUNVERIFIED: none\n")
        config = json.loads(self.config.read_text())
        config["judge"] = {"agent": "grok", "model": "grok-4", "effort": "high"}
        self.config.write_text(json.dumps(config))
        # Past the unspent-bound check, the pinned judge from config.json is
        # the one whose completed assignment the diagnosis needs.
        code, _, err = self.owner("diagnose", {"id": "diag-wrong", "task": TASK, "checkpoint": "cap-5",
            "judge_report": str(other), "scope": WORK["scope"], "allowed_paths": ["src/parser.py"],
            "supersedes": "diag-cap:plan"})
        self.assertEqual(code, 1)
        self.assertIn("pinned judge", err)

    def test_two_extra_fixes_use_one_approval_with_actual_blocking_review_between(self):
        self.seed_cap()
        extra = ["--correction-plan", "two-fixes", "--work", str(self.work)]
        code, plan_text, err = self.invoke(["plan", "--roles", "developer", "--snapshot", str(self.snapshot),
                                          "--task", TASK, "--fix-round", "6", "--now", AT, *extra])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(plan_text)["task_context"]["fix_round"], 6)
        code, out, err = self.invoke(self.apply_args("developer", 6, *extra), self.fresh_client("fix-5", "fix-6"))
        self.assertEqual(code, 0, err)
        dispatch = json.loads(out)["applied"][0]["dispatch_id"]
        code, _, err = self.invoke(self.apply_args("developer", 7, *extra), self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("actual blocking review", err)
        self.assertEqual(self.runner.calls, [])
        review = self.tmp / "review-6.md"
        review.write_text("Reviewed head " + HEAD + "\nBlocking F1: an escaped quote is still mishandled.\n")
        code, _, err = self.owner("record-report", {"dispatch": dispatch, "head_revision": HEAD, "verdict": "blocking",
            "review_mode": "full", "reviewer": "codex", "report": str(review), "changed_paths": ["src/parser.py"]})
        self.assertEqual(code, 0, err)
        with patch("teamlead.assign.send_message", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.invoke(self.apply_args("developer", 7, *extra), self.fresh_client("fix-6", "fix-7-interrupted"))
        pending = self.saved()["recovery"]["dispatches"][-1]
        self.assertEqual(pending["status"], "sending")
        code, _, err = self.invoke(self.apply_args("developer", 7, *extra), self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("uncertain outcome", err)
        self.assertEqual(self.runner.calls, [])
        self.evidence.write_text("Fixture transport evidence: the interruption occurred before the assignment was sent.\n")
        code, _, err = self.owner("reconcile", {"dispatch": pending["id"], "outcome": "not_sent",
            "reason": "Fixture proves no prompt was delivered", "authorization": AUTH, "evidence": str(self.evidence)},
            self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        code, _, err = self.invoke(self.apply_args("developer", 7, *extra), self.fresh_client("fix-6", "fix-7"))
        self.assertEqual(code, 0, err)
        before = self.state.read_bytes()
        for command in (self.apply_args("developer", 8, *extra),
                        ["plan", "--roles", "developer", "--snapshot", str(self.snapshot), "--task", TASK,
                         "--fix-round", "8", "--now", AT, *extra]):
            code, _, err = self.invoke(command, self._client({}))
            self.assertEqual(code, 1)
            self.assertIn("outside the approved task or budget", err)
            self.assertEqual(self.runner.calls, [])
            self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual([row["id"] for row in self.saved()["recovery"]["plans"] if not row.get("supersedes")], ["diag-cap:plan"])
        code, out, err = self.invoke(["status"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["tasks"][TASK]["status"], "checkpoint_required")

    def test_an_authorized_approach_plans_and_dispatches_without_a_correction_plan(self):
        # coding-policy#462: a fresh allowance is spent like any allowance --
        # no `--correction-plan`, no `--work`, no repeated operator prompt.
        self.seed_cap(skip_diagnosis=True)
        code, _, err = self.owner("authorize-approach", {
            "id": "approach-1", "task": TASK, "checkpoint": "cap-5",
            "direction": "Collect callables at the return expression instead of walking the call graph.",
            "verification": "The tuple-returning fixture reports both callables.",
            "allowance": 2, "authorization": AUTH})
        self.assertEqual(code, 0, err)
        code, out, err = self.invoke(["status"])
        self.assertEqual(code, 0, err)
        status = json.loads(out)["tasks"][TASK]
        self.assertEqual((status["status"], status["confirmed_fixes"], status["approach"],
                          status["approach_attempts"], status["approach_allowance"], status["remaining_fixes"]),
                         ("within_authorized_budget", 5, "approach-1", 0, 2, 2))
        code, plan_text, err = self.invoke(["plan", "--roles", "developer", "--snapshot", str(self.snapshot),
                                            "--task", TASK, "--fix-round", "6", "--now", AT])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(plan_text)["task_context"]["fix_round"], 6)
        code, _, err = self.invoke(self.apply_args("developer", 6), self.fresh_client("fix-5", "fix-6"))
        self.assertEqual(code, 0, err)
        code, _, err = self.invoke(self.apply_args("developer", 7), self.fresh_client("fix-6", "fix-7"))
        self.assertEqual(code, 0, err)
        # The new allowance bounds the new direction exactly as the first did.
        code, _, err = self.invoke(self.apply_args("developer", 8), self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("correction allowance is exhausted", err)
        code, out, err = self.invoke(["status"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["tasks"][TASK]["status"], "checkpoint_required")

    def test_repeated_completed_apply_never_sends_or_consumes_again(self):
        self.register()
        args = self.apply_args("developer", None, "--dispatch-id", "initial")
        code, _, err = self.invoke(args, self.fresh_client("old", "new"))
        self.assertEqual(code, 0, err)
        before = self.state.read_bytes()
        code, out, err = self.invoke(args, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["replayed"])
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.state.read_bytes(), before)
        self.briefs["developer"].write_text("Changed task inputs\n")
        code, _, err = self.invoke(args, self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("different inputs", err)
        self.assertEqual(self.runner.calls, [])

    def test_interrupted_send_is_held_until_explicit_reconciliation_without_resend(self):
        self.register()
        args = self.apply_args("developer", None, "--dispatch-id", "interrupted")
        client = self.fresh_client("old", "started-session")
        with patch("teamlead.assign.send_message", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.invoke(args, client)
        self.assertEqual(self.saved()["assignments"], [])
        self.assertEqual(self.saved()["recovery"]["dispatches"][0]["status"], "sending")
        code, _, err = self.invoke(args, self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("uncertain outcome", err)
        self.assertEqual(self.runner.calls, [])
        data = {"dispatch": "interrupted:developer", "outcome": "applied", "reason": "Original report proves delivery and completion",
                "authorization": AUTH, "evidence": str(self.evidence)}
        code, _, err = self.owner("reconcile", data, self._client({"grok": "idle"}, sessions={"grok": "later-observation"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runner.writes(), [])
        self.assertIsNone(self.saved()["assignments"][0]["context_session"])
        code, out, err = self.invoke(args, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["recovered"])
        self.assertEqual(len(self.saved()["assignments"]), 1)
        self.assertEqual(self.runner.calls, [])

    def test_pane_label_interruption_keeps_confirmed_delivery_and_retry_is_read_only(self):
        self.register()
        client = self.fresh_client("old", "new")
        self.runner.raises["pane rename"] = KeyboardInterrupt()
        args = self.apply_args()
        with self.assertRaises(KeyboardInterrupt):
            self.invoke(args, client)
        self.assertEqual(self.saved()["assignments"][0]["status"], "applied")
        code, out, err = self.invoke(args, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["replayed"])
        self.assertEqual(self.runner.calls, [])

    def test_live_transaction_lock_prevents_a_second_dispatch(self):
        with state_lock(self.state):
            code, _, err = self.invoke(self.apply_args(), self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("Another teamlead command", err)
        self.assertEqual(self.runner.calls, [])
        code, _, err = self.invoke(self.apply_args(), self.fresh_client("old", "new"))
        self.assertEqual(code, 0, err)

    def test_dry_run_requests_owner_migration_without_writing_files(self):
        self.state.write_text(json.dumps({"schema_version": 4, "assignments": [], "snapshots": []}))
        before = self.state.read_bytes()
        code, _, err = self.invoke(self.apply_args("developer", None, "--dry-run"), self._client({}))
        self.assertEqual(code, 1, err)
        self.assertIn("Run `teamlead state`", err)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse(self.state.with_suffix(".json.lock").exists())


    # coding-policy#460 review: the partition gate accepts only the dispatches
    # THIS plan sent.
    def dispatch_partitioned_plan(self, task: "str | None" = TASK):
        self.register()
        partition = self.tmp / "validated.json"
        partition.write_text(json.dumps(fixture.validated_partition()))
        code, out, err = self.invoke(["plan", "--roles", "reviewer", "--partition", str(partition), "--now", AT,
                                      "--snapshot", str(self.snapshot)] + (["--task", task] if task else []))
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.plan_file = self.tmp / "partitioned-plan.json"
        self.plan_file.write_text(json.dumps(plan))
        briefs = []
        for seat in plan["assignments"]:
            brief = self.tmp / (seat.replace("#", "-") + ".md")
            brief.write_text(fixture.seat_brief_text(seat, plan), encoding="utf-8")
            briefs += ["--brief", seat + "=" + str(brief)]
        code, _, err = self.invoke(["apply", "--assignments", str(self.plan_file), "--common", str(self.common),
                                    "--task", TASK, "--now", AT, "--composer-settle", "0", *briefs],
                                   self._client({name: "idle" for name in plan["assignments"].values()}))
        self.assertEqual(code, 0, err)
        return plan

    def gate(self):
        return self.invoke(["verify-partition", "--plan", str(self.plan_file), "--repo", str(self.tmp),
                            "--head", "HEAD", "--task", TASK])

    def test_the_plans_own_dispatches_reach_the_proof_checks(self):
        self.dispatch_partitioned_plan()
        code, _, err = self.gate()
        # Past the binding: the fixture's proof names another repository.
        self.assertEqual(code, 1)
        self.assertIn("was proven in /repo", err)

    def test_an_older_dispatch_to_another_worker_does_not_pass_a_new_plan(self):
        plan = self.dispatch_partitioned_plan()
        seat = sorted(plan["assignments"])[0]
        others = sorted({"claude", "codex", "grok"} - set(plan["assignments"].values()))
        plan["assignments"][seat] = others[0]
        self.plan_file.write_text(json.dumps(plan))
        code, _, err = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("differs from this plan in agent", err)

    def test_a_newer_dispatch_that_never_applied_is_not_the_review(self):
        plan = self.dispatch_partitioned_plan()
        seat = sorted(plan["assignments"])[0]
        document = self.saved()
        row = next(item for item in document["recovery"]["dispatches"] if item["role"] == seat)
        document["recovery"]["dispatches"].append({**row, "id": "resent-" + seat, "fingerprint": "0" * 64,
                                                   "status": "not_sent", "result": None, "report": None})
        self.state.write_text(json.dumps(document))
        code, _, err = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("latest dispatch is 'not_sent', not applied", err)

    def test_a_plan_made_without_the_task_is_refused(self):
        self.dispatch_partitioned_plan(task=None)
        code, _, err = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("was not made for task", err)

    def test_a_dispatched_brief_rewritten_in_place_is_refused(self):
        self.dispatch_partitioned_plan()
        row = next(item for item in self.saved()["recovery"]["dispatches"] if "#" in item["role"])
        Path(row["brief"]).write_text("rewritten after the send\n")
        code, _, err = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("not an intact frozen copy", err)

if __name__ == "__main__":
    unittest.main()
