"""Public apply recovery for a retrospective failure before enrollment (#764)."""

import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foreman import assign, cli, lifecycle, recovery, supervision
from foreman.diagnostics import PREFIX
from foreman.errors import HerdrError, StateError, UsageError
from foreman.state import load_state_checked
from tests.test_cli import AT, SNAPSHOT, FreshOwnerNative


class PreflightNative(FreshOwnerNative):
    """A late unknown foreground process appears at the real retrospective read."""

    def __init__(self):
        super().__init__()
        self.fail_preflight = True
        self.fail_close = False
        self.reads = {}
        self.frames = [self.EMPTY]

    def pane_process_info(self, pane_id):
        value = super().pane_process_info(pane_id)
        self.reads[pane_id] = self.reads.get(pane_id, 0) + 1
        if self.fail_preflight and self.reads[pane_id] == 3:
            value["foreground_processes"] += [
                {"pid": 73181, "name": "unknown", "argv": ["unknown"]},
                {"pid": 73178, "name": "unknown", "argv": ["unknown"]}]
        return value

    def pane_close(self, pane_id):
        if self.fail_close:
            raise HerdrError("closure unproved; inspect the original pane", {})
        return super().pane_close(pane_id)


class PreflightRetryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config, self.state, self.snapshot, self.common, self.brief, self.report = (
            self.root / name for name in ("config.json", "state.json", "snapshot.json", "COMMON.md", "judge.md", "report.md"))
        payload = json.loads((ROOT / "config.example.json").read_text())
        payload["judge"] = {"worker_kind": "codex", "model": "gpt-6-astra", "effort": "high"}
        self.config.write_text(json.dumps(payload))
        self.snapshot.write_text(json.dumps(SNAPSHOT))
        self.common.write_text("Immutable common instructions\n")
        self.brief.write_text("Immutable assignment\nREPORT: " + str(self.report) + "\n")
        self.base = ["--config", str(self.config), "--state", str(self.state)]
        self.native = PreflightNative()
        self.plan = self.make_plan("judge-764")
        supervision.bind(self.state, {"kind": "id", "value": "fixture-foreman", "cwd": str(self.root),
            "herdr_env": "fixture", "pane_id": "foreman-pane"}, AT, root=self.root / "bindings")
        real_spawn, real_apply = lifecycle.spawn, assign.apply
        for target, operation in (("foreman.cli.lifecycle.spawn", real_spawn), ("foreman.cli.apply_assignments", real_apply)):
            patched = patch(target, side_effect=lambda *a, operation=operation, **kw: operation(*a, **kw, sleep=lambda _: None))
            patched.start()
            self.addCleanup(patched.stop)

    def invoke(self, arguments):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(self.base + arguments + ["--now", AT], stdout=out, stderr=err, client=self.native)
        raw = out.getvalue() if code == 0 else "\n".join(line for line in err.getvalue().splitlines() if not line.startswith(PREFIX))
        return code, json.loads(raw)

    def make_plan(self, name):
        with patch("foreman.lifecycle.identity", return_value=name):
            code, plan = self.invoke(["plan", "--roles", "judge", "--judge-mode", "adjudication",
                "--task", "preflight-764", "--snapshot", str(self.snapshot)])
        self.assertEqual(code, 0, plan)
        return plan

    def apply(self, plan=None):
        return self.invoke(["apply", "--assignments", json.dumps(plan or self.plan), "--judge-mode", "adjudication",
            "--task", "preflight-764", "--common", str(self.common), "--brief", "judge=" + str(self.brief),
            "--report", "judge=" + str(self.report), "--composer-settle", "0"])

    def test_closed_preflight_failure_retries_the_identical_plan_and_sends_once(self):
        pins = {path: path.read_bytes() for path in (self.config, self.common, self.brief)}
        code, failure = self.apply()
        self.assertEqual(code, 1, failure)
        details = failure["details"]
        self.assertEqual(details["failure_kind"], "launch_preflight")
        self.assertIn("sole shell", details["failure_message"])
        self.assertEqual(details["recovery"]["outcome"], "retryable")
        self.assertIn(" apply ", " " + details["recovery"]["operation"] + " ")
        self.assertEqual(details["launch_attempt"], 1)
        self.assertEqual(self.native.panes, {})
        self.assertEqual(self.native.agents, {})
        self.assertFalse(any(row[0] in {"start", "prompt"} for row in self.native.events))
        saved, usable = load_state_checked(self.state)
        self.assertTrue(usable)
        self.assertEqual(saved["assignments"], [])
        self.assertEqual(saved["recovery"]["dispatches"], [])
        self.assertEqual(supervision.load(self.state)["members"], [])
        failures = copy.deepcopy(saved["recovery"]["events"])
        self.assertEqual([row["details"]["attempt"] for row in failures if row["kind"] == "launch_transient_failure"], [1])
        self.native.fail_preflight = False
        code, result = self.apply()
        self.assertEqual(code, 0, result)
        self.assertEqual(result["applied"][0]["agent"], "judge-764")
        self.assertEqual(result["applied"][0]["status"], "applied")
        code, result = self.apply()
        self.assertEqual(code, 0, result)
        self.assertEqual(len([row for row in self.native.events if row[0] == "prompt"]), 1)
        final = load_state_checked(self.state)[0]
        self.assertEqual(final["recovery"]["events"][:len(failures)], failures)
        self.assertEqual([(row["task"], row["fix_round"]) for row in final["assignments"]], [("preflight-764", None)])
        self.assertEqual({path: path.read_bytes() for path in pins}, pins)

    def test_budget_survives_reload_and_fresh_names_without_any_native_call_at_exhaustion(self):
        for attempt in range(1, recovery.TRANSIENT_LAUNCH_LIMIT + 1):
            code, failure = self.apply()
            self.assertEqual(code, 1, failure)
            self.assertEqual(failure["details"]["launch_attempt"], attempt)
            self.assertEqual(failure["details"]["recovery"]["outcome"],
                "exhausted" if attempt == recovery.TRANSIENT_LAUNCH_LIMIT else "retryable")
        plan = self.make_plan("judge-new-764")
        events = list(self.native.events)
        before = self.state.read_bytes()
        code, failure = self.apply(plan)
        self.assertEqual(code, 1, failure)
        self.assertEqual(failure["details"]["recovery"]["outcome"], "exhausted")
        self.assertEqual(self.native.events, events)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse(any(row[0] in {"start", "prompt"} for row in events))

    def test_failed_cleanup_never_authorizes_a_retry(self):
        self.native.fail_close = True
        code, failure = self.apply()
        self.assertEqual(code, 1, failure)
        self.assertEqual(failure["details"]["recovery"]["outcome"], "blocked")
        self.assertTrue(self.native.panes)
        saved = load_state_checked(self.state)[0]
        self.assertFalse(any(row["kind"] == "launch_transient_failure" for row in saved["recovery"]["events"]))
        self.assertFalse(any(row[0] in {"start", "prompt"} for row in self.native.events))

    def test_failed_budget_persistence_never_advertises_a_retry(self):
        save = cli.save_state
        def failing_save(path, state):
            if any(row["kind"] == "launch_transient_failure" for row in state["recovery"]["events"]):
                raise StateError("Owner storage unavailable; restore it before retrying", {})
            return save(path, state)
        with patch("foreman.cli.save_state", side_effect=failing_save):
            code, failure = self.apply()
        self.assertEqual(code, 1, failure)
        self.assertEqual(failure["details"]["recovery"]["outcome"], "blocked")
        self.assertEqual(failure["details"]["failure_kind"], "launch_retry_persistence_failed")
        self.assertEqual(self.native.panes, {})
        self.assertFalse(any(row[0] in {"start", "prompt"} for row in self.native.events))

    def test_retrospective_policy_refusal_is_not_a_runtime_retry(self):
        with patch("foreman.retrospective_runtime.Guard.before_start", side_effect=UsageError("Record required retrospective coverage", {})):
            code, failure = self.apply()
        self.assertEqual(code, 1, failure)
        self.assertEqual(failure["details"]["recovery"]["outcome"], "blocked")
        self.assertNotEqual(failure["details"]["failure_kind"], "launch_preflight")
        self.assertFalse(any(row[0] in {"start", "prompt"} for row in self.native.events))


if __name__ == "__main__":
    unittest.main()
