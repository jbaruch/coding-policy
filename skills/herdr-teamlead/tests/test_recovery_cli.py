"""Public owner-command workflows with controlled Herdr and report fixtures."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import io
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from teamlead.state import add_assignment, empty_state, save_state, state_lock
from tests import test_cli as fixture
from tests.fakes import FakeRunner, ScriptedReads, agent_json

AT = fixture.AT
TASK = "recovery-fixture"
BASE = "a" * 40
HEAD = "b" * 40
AUTH = {"source": "fixture operator message", "quote": "Approve this task and the stated correction bounds."}
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

    def seed_cap(self):
        state = empty_state()
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(state, "2026-02-03T09:00:0{}+00:00".format(fix or 0), "developer", "grok", task=TASK, fix_round=fix)
        add_assignment(state, AT, "judge", "claude", task=TASK)
        save_state(self.state, state)
        self.register()
        config = json.loads(self.config.read_text())
        config["judge"] = {"agent": "claude", "model": "claude-opus-4-6", "effort": "high"}
        self.config.write_text(json.dumps(config))
        judge = self.tmp / "judge.md"
        judge.write_text("RULING: amend — correct F1\nACTION: Use one canonical parser\n")
        code, _, err = self.owner("checkpoint", {"id": "cap-5", "task": TASK, "defect": "F1 is still blocking",
            "previous_attempts": "Five fixes changed parser handling", "progress": "Most fixtures now pass",
            "change_in_approach": "Use a single parser", "judge_report": str(judge)})
        self.assertEqual(code, 0, err)
        code, _, err = self.owner("authorize-corrections", {"id": "two-fixes", "task": TASK, "checkpoint": "cap-5",
            "scope": WORK["scope"], "allowed_paths": ["src/*"], "additional_fixes": 2, "authorization": AUTH})
        self.assertEqual(code, 0, err)

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
        self.assertEqual(len(self.saved()["recovery"]["plans"]), 1)
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

    def replacement_args(self, agent, report):
        # Each replacement carries a fresh report path, so the brief bytes
        # change and the dispatch identity is new (never a replay).
        self.briefs["tester"].write_text("# tester\nREPORT: {}\n".format(report))
        count = len(self.saved()["assignments"]) if self.state.exists() else 0
        at = (datetime.fromisoformat(AT) + timedelta(seconds=count)).isoformat()
        return ["apply", "--assignments", json.dumps({"tester": agent}), "--common", str(self.common),
                "--brief", "tester=" + str(self.briefs["tester"]), "--task", TASK, "--now", at, "--composer-settle", "0"]

    def refusal_receipt(self, agent, name):
        path = self.tmp / name
        path.write_text(json.dumps({"agent": agent, "state": "idle", "report_path": str(self.tmp / "tester.md"),
                                    "found": False, "elapsed_seconds": 12, "reason": "terminal_provider_refusal"}))
        return str(path)

    def test_a_refused_brief_moves_once_to_another_provider_and_then_stops(self):
        # coding-policy#399, live case: codex refused the tester brief
        # mid-execution. The lead may move the brief to one other provider;
        # a same-provider resend and a third dispatch after two refusals are
        # refused before any keystroke.
        self.register()
        code, out, err = self.invoke(self.replacement_args("codex", "/reports/tester-1.md"), self._client({"codex": "idle"}))
        self.assertEqual(code, 0, err)
        first = json.loads(out)["applied"][0]["dispatch_id"]
        code, out, err = self.owner("record-refusal", {"dispatch": first, "receipt": self.refusal_receipt("codex", "refusal-1.json")})
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["provider"], "codex")
        self.assertEqual(self.saved()["recovery"]["dispatches"][0]["refusal"]["provider"], "codex")
        code, out, err = self.invoke(self.replacement_args("codex", "/reports/tester-2.md"), self._client({"codex": "idle"}))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("same provider", err)
        self.assertEqual(self.runner.writes(), [])
        code, out, err = self.invoke(self.replacement_args("codex", "/reports/tester-2.md", ) + ["--dry-run"], self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("same provider", err)
        code, out, err = self.invoke(self.replacement_args("claude", "/reports/tester-2.md"), self._client({"claude": "idle"}))
        self.assertEqual(code, 0, err)
        second = json.loads(out)["applied"][0]["dispatch_id"]
        moved = self.saved()["recovery"]["dispatches"][-1]
        self.assertEqual((moved["id"], moved["refusal_move"]["from"], moved["refusal_move"]["from_provider"], moved["refusal_move"]["provider"]),
                         (second, first, "codex", "claude"))
        code, _, err = self.owner("record-refusal", {"dispatch": second, "receipt": self.refusal_receipt("claude", "refusal-2.json")})
        self.assertEqual(code, 0, err)
        code, out, err = self.invoke(self.replacement_args("grok", "/reports/tester-3.md"), self._client({"grok": "idle"}))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("refused by 2 providers", err)
        self.assertEqual(self.runner.writes(), [])
        code, _, err = self.invoke(["status"])
        self.assertEqual(code, 0, err)

    def test_record_refusal_needs_the_refused_worker_in_config(self):
        self.register()
        code, out, err = self.invoke(self.replacement_args("codex", "/reports/tester-1.md"), self._client({"codex": "idle"}))
        self.assertEqual(code, 0, err)
        first = json.loads(out)["applied"][0]["dispatch_id"]
        config = json.loads(self.config.read_text())
        config["agents"] = [agent for agent in config["agents"] if agent["name"] != "codex"]
        self.config.write_text(json.dumps(config))
        code, _, err = self.owner("record-refusal", {"dispatch": first, "receipt": self.refusal_receipt("codex", "refusal-1.json")})
        self.assertEqual(code, 1)
        self.assertIn("not in config.json", err)
        code, _, err = self.owner("record-refusal", {"dispatch": "no-such-dispatch", "receipt": self.refusal_receipt("codex", "refusal-1.json")})
        self.assertEqual(code, 1)
        self.assertIn("dispatch", err)


if __name__ == "__main__":
    unittest.main()
