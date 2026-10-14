"""Owner-closed no-work recovery preserves actual history and initial budgets."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from foreman import cli, members, no_work_recovery as owner, recovery, supervision
from foreman.assign import validate_fix_history
from foreman.errors import HerdrError, UsageError
from foreman.state import add_assignment, empty_state, load_state_checked, save_state
from foreman.tiers import launch_flags, verify_argv, worker_launch_args
from tests.test_members import ledger_text

AT = "2026-02-03T10:00:00+00:00"
CLOSED = "2026-02-03T11:00:00+00:00"
RECOVERED = "2026-02-04T10:00:00+00:00"
RETRIED = "2026-02-04T10:01:00+00:00"
AUTH = {"source": "original operator request", "quote": "Ship this task."}


def missing(code):
    return HerdrError("Missing original identity.", {"stderr": json.dumps({"error": {"code": code}})})


class NoWorkFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        self.tree = self.root / "checkout"
        self.tree.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.tree / "source").write_text("original\n")
        self.git("add", "source")
        self.git("commit", "-m", "Initial fixture")
        self.base = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", self.base)
        self.state = empty_state()
        self.store = self.state["recovery"]
        recovery.register_task(self.store, {"task": "task", "base_revision": self.base, "scope": "fix source",
            "allowed_paths": ["source"], "authorization": AUTH}, AT)
        self.common = self.root / "COMMON.md"
        self.brief = self.root / "developer.md"
        self.report = self.root / "report.md"
        self.common.write_text("Original task and base " + self.base)
        self.brief.write_text("Use " + str(self.tree) + "\nReport: " + str(self.report))
        oracle = self.root / "expected.patch"
        oracle.write_text("fixed original oracle\n")
        self.oracle = {"kind": "patch", "path": str(oracle), "sha256": recovery.receipt(str(oracle))[0]["sha256"]}
        self.row = recovery.reserve(self.store, {"id": "original", "task": "task", "role": "developer",
            "agent": "developer-original", "fix_round": None, "fingerprint": "c" * 64,
            "plan": None, "work": None, "provider": "codex", "worker_kind": "codex",
            "brief": str(self.brief), "common": str(self.common), "oracle": self.oracle,
            "brief_identity": recovery.brief_identity({"common": str(self.common), "developer": str(self.brief)}, "developer", str(self.report))}, AT)
        recovery.mark_sending(self.store, "original", AT, {"context_session": None})
        add_assignment(self.state, AT, "developer", "developer-original", task="task", fix_round=None)
        recovery.finish_dispatch(self.store, "original", {"role": "developer", "agent": "developer-original",
            "task": "task", "fix_round": None, "status": "applied", "worker_kind": "codex",
            "assignment_scoped": True, "started": True, "at": AT, "pane_id": "original-pane",
            "context_session": {"agent": "codex", "kind": "id", "source": "fixture", "value": "native-session"}}, 0, AT)
        save_state(self.path, self.state)
        supervision.bind(self.path, supervision.identity("lead", str(self.root), "fixture", pane_id="lead-pane"), AT,
                         root=self.root / "bindings")
        supervision.enroll(self.path, {"id": "original", "agent": "developer-original", "task": "task",
            "pane_id": "original-pane", "native_session": None, "report": str(self.report)}, AT)
        self.ledger = self.root / "TASK-LEDGER.md"
        self.ledger.write_text(ledger_text("task", [{"schema_version": 1, "id": "unavailable", "at": CLOSED,
            "subject": "assignment", "dispatch_id": "original", "worker": "developer-original", "role": "developer",
            "report": str(self.report), "observed": "spent wait, no_work", "decision": "unavailable",
            "head_revision": self.base, "evidence": "original wait receipt", "assessment": "No source work."}],
            self.path, base=self.base))
        self.client = Mock()
        self.client.agent_get.side_effect = missing("agent_not_found")
        self.client.pane_get.side_effect = missing("pane_not_found")
        closure = {"pane_id": "original-pane", "agent": "developer-original", "closed": True, "replayed": False}
        with patch("foreman.members.lifecycle.close", return_value=closure):
            result = members.close(self.path, "original", str(self.ledger), CLOSED, self.client)
        self.closed = self.root / "closed.json"
        self.closed.write_text(json.dumps(result))
        self.wait = self.root / "wait.json"
        self.payload = {"agent": "developer-original", "state": "done", "report_path": str(self.report),
            "found": False, "elapsed_seconds": 4919, "stall": {"class": "no_work", "evidence": {
                "worktree": str(self.tree), "readable": True, "mid_operation": False, "base": self.base,
                **dict.fromkeys(owner.ZERO_FIELDS, 0)}}}
        self.wait.write_text(json.dumps(self.payload))
        self.data = {"dispatch": "original", "reason": "Retry the proved no-work initial assignment.",
            "authorization": AUTH, "evidence": str(self.wait), "closure": str(self.closed), "ledger": str(self.ledger)}

    def git(self, *args):
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.tree), *args],
            capture_output=True, text=True, check=True, env={**os.environ,
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_DATE": AT, "GIT_COMMITTER_DATE": AT})
        return result.stdout.strip()

    def recover(self):
        result = owner.record(self.path, self.state, self.data, RECOVERED, self.client)
        recovery.validate_store(self.store, self.state["assignments"])
        save_state(self.path, self.state)
        return result

    def retry(self, identifier="replacement"):
        owner.prepare(self.path, self.state, "task", None, self.row["brief_identity"],
                      str(self.root / (identifier + ".md")), self.oracle, self.client)
        return recovery.reserve(self.store, {"id": identifier, "fingerprint": identifier,
            "role": "developer", "agent": "developer-" + identifier, "task": "task", "fix_round": None,
            "plan": None, "work": None, "worker_kind": "codex", "provider": "codex",
            "brief_identity": self.row["brief_identity"], "oracle": self.oracle}, RETRIED)


class NoWorkRecoveryTests(NoWorkFixture):
    def test_cli_plan_apply_and_completed_replay_keep_original_oracle(self):
        self.recover()
        settings = json.loads((ROOT / "config.example.json").read_text())
        settings["worker_kinds"] = [row for row in settings["worker_kinds"] if row["name"] == "claude"]
        config = self.root / "config.json"
        config.write_text(json.dumps(settings))
        snapshot = self.root / "snapshot.json"
        snapshot.write_text(json.dumps({"agents": {"claude": {"headroom_pct": 95}}}))
        context = self.root / "round-context.json"
        context.write_text(json.dumps({"developer": {"oracle": {"kind": "patch", "path": self.oracle["path"]}}}))
        base = ["--state", str(self.path), "--config", str(config), "--now", RETRIED]

        def run(argv):
            out, err = io.StringIO(), io.StringIO()
            code = cli.main(argv, client=self.client, stdout=out, stderr=err)
            self.assertEqual(code, 0, err.getvalue())
            return json.loads(out.getvalue())

        with patch("foreman.lifecycle.identity", return_value="developer-replacement"):
            plan = run(["plan", *base, "--roles", "developer", "--task", "task", "--snapshot", str(snapshot),
                        "--round", "developer=mechanical", "--round-context", str(context)])
        self.assertEqual(plan["task_context"]["fix_round"], None)
        self.assertEqual(plan["oracle_pins"]["developer"]["sha256"], self.oracle["sha256"])
        new_report = str(self.root / "replacement-report.md")
        new_brief = self.root / "replacement-brief.md"
        new_brief.write_text(self.brief.read_text().replace(str(self.report), new_report))
        command = ["apply", *base, "--assignments", json.dumps(plan), "--task", "task",
                   "--dispatch-id", "replacement", "--common", str(self.common),
                   "--brief", "developer=" + str(new_brief), "--report", "developer=" + new_report]

        def live(name):
            if name == "developer-original":
                raise missing("agent_not_found")
            return {"name": name, "pane_id": "new-root", "agent_status": "idle"}
        self.client.agent_get.side_effect = live

        def proof(_client, worker, pane, tier):
            flags = worker_launch_args(worker.kind, worker.launch_args)
            verified = verify_argv(worker.kind, tier, [worker.kind] + flags + launch_flags(worker.kind, tier), flags)
            return {**verified, "source": "process_argv", "pid": 501, "pane_id": pane}

        def send(_client, _assignments, agents, _paths, _at, **options):
            state, usable = load_state_checked(self.path)
            self.assertTrue(usable)
            row = dict(state["recovery"]["dispatches"][-1])
            tier = options["tiers"]["developer"]
            worker = agents[row["agent"]]
            row.update(pane_id="new-root", cleared=True, clear_reason="automatic", context_session=None,
                       tier={**tier, "launch_args": worker_launch_args(worker.kind, worker.launch_args),
                             "verified": proof(_client, worker, "new-root", tier), "prompt_hash": "a" * 64},
                       status="applied", assignment_scoped=True)
            options["on_before_send"]({"role": "developer", "agent": row["agent"], "pane_id": "new-root"}, row)
            options["on_result"](row)
            return {"applied": [row]}

        with patch("foreman.cli.retrospective_runtime.Guard"), \
                patch("foreman.cli.verify_running", side_effect=proof), \
                patch("foreman.lifecycle.spawn", return_value="new-root") as spawn, \
                patch("foreman.cli.apply_assignments", side_effect=send) as sent:
            run(command)
            replay = run(command)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(sent.call_count, 1)
        self.assertTrue(replay["applied"][0]["replayed"])
        final, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertEqual([row["fix_round"] for row in final["assignments"]], [None, None])
        self.assertEqual(final["recovery"]["dispatches"][0], self.row)
        self.assertEqual(final["recovery"]["dispatches"][1]["oracle"], self.oracle)
        self.assertEqual(recovery.task_statuses(final["recovery"], final["assignments"])["task"]["remaining_fixes"], 5)

    def test_original_bug_then_retry_keeps_history_budget_and_replay(self):
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(UsageError, "already has a developer"):
            validate_fix_history({"developer": "new"}, self.state["assignments"], "task", None, recovery=self.store)
        saved = self.recover()
        validate_fix_history({"developer": "new"}, self.state["assignments"], "task", None, recovery=self.store)
        self.assertEqual(self.state["assignments"], before["assignments"])
        self.assertEqual(self.store["dispatches"], before["recovery"]["dispatches"])
        self.assertEqual(self.store["tasks"], before["recovery"]["tasks"])
        self.assertEqual(recovery.task_statuses(self.store, self.state["assignments"])["task"]["confirmed_fixes"], 0)
        self.assertEqual(recovery.task_statuses(self.store, self.state["assignments"])["task"]["remaining_fixes"], 5)
        self.assertEqual(self.recover(), saved)
        replacement = self.retry()
        self.assertEqual(replacement["fix_round"], None)
        with self.assertRaisesRegex(UsageError, "unresolved dispatch"):
            recovery.reserve(self.store, {**replacement, "id": "duplicate", "agent": "developer-other"}, RETRIED)
        recovery.mark_sending(self.store, "replacement", RETRIED, {"context_session": None})
        add_assignment(self.state, RETRIED, "developer", "developer-replacement", task="task")
        recovery.finish_dispatch(self.store, "replacement", {"role": "developer", "agent": "developer-replacement",
            "task": "task", "fix_round": None, "status": "applied", "worker_kind": "codex", "assignment_scoped": True}, 1, RETRIED)
        recovery.validate_store(self.store, self.state["assignments"])
        save_state(self.path, self.state)
        loaded, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertEqual(loaded["recovery"]["dispatches"][0], before["recovery"]["dispatches"][0])
        self.assertEqual(len(loaded["assignments"]), 2)
        self.assertEqual(recovery.task_statuses(self.store, self.state["assignments"])["task"]["confirmed_fixes"], 0)
        with self.assertRaisesRegex(UsageError, "already has a developer"):
            validate_fix_history({"developer": "other"}, loaded["assignments"], "task", None, recovery=loaded["recovery"])
        (self.tree / "source").write_text("replacement now working\n")
        self.assertEqual(owner.record(self.path, self.state, self.data, RETRIED, self.client)["retries"], ["replacement"])
        self.assertIsNone(owner.eligible(self.store, self.state["assignments"], "task", None))
        self.client.agent_start.assert_not_called()
        self.client.pane_close.assert_not_called()

    def test_cli_recovery_does_not_require_retired_worker_config(self):
        record = self.root / "input.json"
        record.write_text(json.dumps(self.data))
        args = cli.build_parser().parse_args(["recover-no-work", "--state", str(self.path), "--record", str(record), "--now", RECOVERED])
        result, error = cli.cmd_recovery(args, client=self.client)
        self.assertIsNone(error)
        self.assertEqual(result["id"], "original")
        loaded, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertIsNotNone(owner.eligible(loaded["recovery"], loaded["assignments"], "task", None))

    def test_reconcile_routes_closed_initial_assignment_to_no_work_owner(self):
        record = self.root / "reconcile.json"
        record.write_text(json.dumps({key: value for key, value in self.data.items() if key not in {"closure", "ledger"}}
                                     | {"outcome": "not_sent"}))
        config = self.root / "config.json"
        config.write_text((ROOT / "config.example.json").read_text())
        args = cli.build_parser().parse_args(["reconcile", "--state", str(self.path), "--config", str(config),
                                              "--record", str(record), "--now", RECOVERED])
        with self.assertRaises(HerdrError) as caught:
            cli.cmd_recovery(args, client=self.client)
        self.assertIn("recover-no-work --record", caught.exception.details["recovery"]["operation"])
        loaded, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertEqual(loaded["assignments"], self.state["assignments"])
        self.assertEqual(loaded["recovery"]["dispatches"], self.store["dispatches"])

    def test_only_initial_assignment_is_eligible(self):
        self.row["fix_round"] = 1
        with self.assertRaisesRegex(UsageError, "initial developer"):
            self.recover()

    def test_wait_receipt_unknown_partial_completed_or_wrong_binding_refuses(self):
        mutations = [lambda p: p.update(found=True), lambda p: p.update(state="working"),
            lambda p: p.update(agent="someone-else"), lambda p: p.update(report_path="/other.md"),
            lambda p: p.update(elapsed_seconds=True), lambda p: p["stall"].update(**{"class": "unknown"}),
            lambda p: p["stall"].update(**{"class": "partial_work"}),
            lambda p: p["stall"]["evidence"].update(base="a" * 40),
            lambda p: p["stall"]["evidence"].update(mid_operation=True)]
        mutations += [lambda p, field=field: p["stall"]["evidence"].update({field: 1}) for field in owner.ZERO_FIELDS]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                payload = copy.deepcopy(self.payload)
                mutate(payload)
                self.wait.write_text(json.dumps(payload))
                with self.assertRaises(UsageError):
                    self.recover()
                self.assertEqual(self.store["no_work_recoveries"], [])

    def test_current_modified_untracked_staged_and_mid_operation_refuse(self):
        self.recover()
        for mode in ("modified", "untracked", "staged", "rebase"):
            with self.subTest(mode=mode):
                marker = Path(self.git("rev-parse", "--absolute-git-dir")) / "rebase-merge"
                if mode == "rebase":
                    marker.mkdir()
                elif mode == "untracked":
                    (self.tree / "new").write_text("partial")
                else:
                    (self.tree / "source").write_text("partial")
                    if mode == "staged":
                        self.git("add", "source")
                with self.assertRaises(UsageError):
                    self.retry()
                if mode == "rebase":
                    marker.rmdir()
                elif mode == "untracked":
                    (self.tree / "new").unlink()
                else:
                    self.git("reset", "--hard", self.base)
        self.assertEqual(self.store["no_work_recoveries"][0]["retries"], [])

    def test_completed_pushed_work_is_not_retried(self):
        self.recover()
        (self.tree / "source").write_text("completed")
        self.git("add", "source")
        self.git("commit", "-m", "Completed work")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        with self.assertRaisesRegex(UsageError, "committed work"):
            self.retry()

    def test_report_and_reused_runtime_and_api_failure_refuse(self):
        self.report.write_text("completed report")
        with self.assertRaisesRegex(UsageError, "report now exists"):
            self.recover()
        self.report.unlink()
        for method, missing_code in ((self.client.agent_get, "agent_not_found"), (self.client.pane_get, "pane_not_found")):
            method.side_effect = None
            method.return_value = {"pane_id": "reused"}
            with self.assertRaisesRegex(UsageError, "worker or pane exists"):
                self.recover()
            method.side_effect = missing("connection_failed")
            with self.assertRaises(HerdrError):
                self.recover()
            method.side_effect = missing(missing_code)

    def test_disappeared_report_observation_cannot_be_reclassified_as_no_work(self):
        supervision.transaction(self.path, lambda data: supervision.append_event(
            data, CLOSED, "original", "report_observed", {"present": True}))
        with self.assertRaisesRegex(UsageError, "recorded a report"):
            self.recover()
        self.assertFalse(self.report.exists())
        self.assertEqual(self.store["no_work_recoveries"], [])

    def test_backdated_retry_refuses_without_consuming_permission(self):
        self.recover()
        before = copy.deepcopy(self.store)
        with self.assertRaisesRegex(UsageError, "must follow"):
            recovery.reserve(self.store, {"id": "early", "fingerprint": "early", "role": "developer",
                "agent": "developer-new", "task": "task", "fix_round": None, "worker_kind": "codex",
                "plan": None, "work": None, "brief_identity": self.row["brief_identity"], "oracle": self.oracle}, AT)
        self.assertEqual(self.store, before)

    def test_changed_closure_ledger_or_oracle_refuses(self):
        for path in (self.closed, self.ledger, Path(self.oracle["path"])):
            with self.subTest(path=path):
                original = path.read_text()
                path.write_text(original + "changed")
                with self.assertRaises(UsageError):
                    self.recover()
                path.write_text(original)
        self.recover()
        for identity, report, oracle in (("different", str(self.root / "new.md"), self.oracle),
                (self.row["brief_identity"], str(self.report), self.oracle),
                (self.row["brief_identity"], str(self.root / "new.md"), None)):
            with self.assertRaises(UsageError):
                owner.prepare(self.path, self.state, "task", None, identity, report, oracle, self.client)

    def test_not_sent_retry_can_resume_but_unknown_send_cannot(self):
        self.recover()
        retry = self.retry()
        recovery.abort_pre_send(self.store, retry["id"], RETRIED, "No input sent")
        self.retry("second-transport")
        recovery.mark_sending(self.store, "second-transport", RETRIED, {"context_session": None})
        with self.assertRaisesRegex(UsageError, "unresolved dispatch"):
            self.retry("third-transport")
        recovery.validate_store(self.store, self.state["assignments"])

    def test_migration_is_owner_only_and_preserves_history(self):
        old = copy.deepcopy(self.state)
        old["recovery"].pop("no_work_recoveries")
        old["recovery"]["schema_version"] = 17
        save_state(self.path, old)
        loaded, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertEqual(loaded["recovery"]["schema_version"], recovery.RECOVERY_STORE_VERSION)
        self.assertEqual(loaded["assignments"], old["assignments"])
        self.assertEqual(loaded["recovery"]["dispatches"], old["recovery"]["dispatches"])
        self.assertEqual(json.loads(self.path.read_text())["recovery"]["no_work_recoveries"], [])
        for version in (17, recovery.RECOVERY_STORE_VERSION + 1):
            damaged = copy.deepcopy(loaded)
            damaged["recovery"]["schema_version"] = version
            save_state(self.path, damaged)
            before = self.path.read_bytes()
            _, usable = load_state_checked(self.path, warn=lambda _: None)
            self.assertFalse(usable)
            self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_recovery_fails_closed(self):
        self.recover()
        original = copy.deepcopy(self.store["no_work_recoveries"][0])
        for field, value in (("schema_version", 2), ("source_sha256", "bad"), ("task_sha256", "bad"),
                             ("assignment_index", True), ("retries", ["missing"]), ("binding", {})):
            with self.subTest(field=field):
                self.store["no_work_recoveries"][0] = {**original, field: value}
                with self.assertRaises(UsageError):
                    recovery.validate_store(self.store, self.state["assignments"])


if __name__ == "__main__":
    unittest.main()
