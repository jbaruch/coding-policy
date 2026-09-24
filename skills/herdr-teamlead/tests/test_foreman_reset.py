"""The foreman resets its own context only when nothing would be lost (#483)."""

import io
import os
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead import foreman_reset, retrospective
from teamlead.state import state_lock
from teamlead.errors import HerdrError, StateError, UsageError
from tests.test_cli import CliCase

PANE = "w9:p1"
READY = {"id": "round-7", "kind": "stow", "reset_ready": True}


def supervision_data(*, active=False, events=False, held=False):
    member = {"id": "d1", "active": active}
    data = {"binding": {"identity": {"pane_id": PANE}}, "members": [member],
            "events": [{"id": "e1", "seq": 1}] if events else [], "acknowledgements": [], "holds": []}
    return data, held


def check(stow=READY, caller: "str | None" = PANE, **state):
    data, held = supervision_data(**state)
    with patch("teamlead.foreman_reset._handoff_held", return_value=held):
        return foreman_reset.preflight(stow, data, caller)


class PreflightTest(unittest.TestCase):
    def test_a_ready_stow_from_the_foreman_pane_with_nothing_active_is_scheduled(self):
        self.assertEqual(check(), {"pane_id": PANE, "stow": "round-7"})

    def test_active_work_needs_a_covering_hold(self):
        with self.assertRaisesRegex(UsageError, "cannot stop yet"):
            check(active=True)
        self.assertEqual(check(active=True, held=True)["pane_id"], PANE)

    def test_unhandled_events_always_refuse(self):
        with self.assertRaisesRegex(UsageError, "1 unhandled event"):
            check(events=True, held=True)

    def test_an_unready_stow_refuses(self):
        with self.assertRaisesRegex(UsageError, "not reset-ready"):
            check(stow={"id": "round-7", "kind": "stow", "reset_ready": False})

    def test_a_memory_record_that_is_not_a_stow_refuses(self):
        with self.assertRaisesRegex(UsageError, "not a stow"):
            check(stow={"id": "lesson-1", "kind": "lesson"})

    def test_only_the_bound_foreman_pane_may_reset(self):
        with self.assertRaisesRegex(UsageError, "own pane"):
            check(caller="w2:p1")
        with self.assertRaisesRegex(UsageError, "outside Herdr"):
            check(caller=None)

    def test_no_binding_refuses(self):
        with self.assertRaisesRegex(UsageError, "No foreman is bound"):
            foreman_reset.preflight(READY, {"binding": None, "members": [], "events": [], "acknowledgements": [], "holds": []}, PANE)


class FakeClient:
    def __init__(self, statuses, kind="claude"):
        self.statuses, self.kind, self.waits = list(statuses), kind, []

    def agent_list(self):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return [{"name": "other", "pane_id": "w2:p1", "agent_status": "idle", "agent": "codex"},
                {"name": "foreman", "pane_id": PANE, "agent_status": status, "agent": self.kind}]

    def agent_wait(self, name, until=(), timeout_ms=None):
        self.waits.append(name)


def worker(name, kind):
    return SimpleNamespace(name=name, kind=kind, clear_prompt="/clear")


class HandoffHoldTest(unittest.TestCase):
    def hold(self, kind):
        data = {"members": [{"id": "d1", "active": True}], "events": [], "acknowledgements": [],
                "holds": [{"kind": kind, "resumed_at": None, "through": 0, "members": "digest"}]}
        with patch("teamlead.foreman_reset.supervision.held", return_value=True), \
             patch("teamlead.foreman_reset.supervision.active_digest", return_value="digest"):
            return foreman_reset._handoff_held(data)

    def test_only_a_handoff_hold_lets_the_foreman_reset(self):
        self.assertTrue(self.hold("handoff"))
        self.assertFalse(self.hold("waiting_for_user"))


class DeliverTest(unittest.TestCase):
    def run_deliver(self, client, *, screen_changed=True, landed=True, budget=30, still_ready=lambda: True):
        calls = []
        ticks = iter(range(0, 10000, 5))

        def command(c, agent, pane, text, **kw):
            kw["before_input"]()
            calls.append(("command", agent.name, pane, text))
            return {"screen_changed": screen_changed}

        def message(c, agent, text, needle, **kw):
            kw["before_input"]()
            calls.append(("message", agent.name, kw["pane_id"], text))
            return {"landed": landed, "started": landed}

        with patch("teamlead.foreman_reset.send_command", side_effect=command), \
             patch("teamlead.foreman_reset.send_message", side_effect=message):
            result = foreman_reset.deliver(client, [worker("codex-a", "codex"), worker("claude-a", "claude")], PANE, "round-7",
                                           "/state/s.json",
                                           still_ready=still_ready, sleep=lambda seconds: None, clock=lambda: next(ticks),
                                           budget_sec=budget, poll_sec=5)
        return result, calls

    def test_waits_for_idle_then_clears_and_sends_the_resume_prompt(self):
        client = FakeClient(["working", "working", "idle"])
        result, calls = self.run_deliver(client)
        self.assertEqual(calls, [("command", "foreman", PANE, "/clear"),
                                 ("message", "foreman", PANE, foreman_reset.resume_prompt("round-7", "/state/s.json"))])
        self.assertIn("memory-show --state /state/s.json --id round-7", calls[1][3])
        self.assertIn("foreman-queue --state /state/s.json", calls[1][3])
        self.assertEqual(client.waits, ["foreman"])
        self.assertTrue(result["cleared"])

    def test_a_pane_that_never_idles_sends_nothing(self):
        with self.assertRaisesRegex(HerdrError, "stayed working"):
            self.run_deliver(FakeClient(["working"]), budget=10)

    def test_a_clear_that_changed_nothing_is_an_interrupted_delivery(self):
        with self.assertRaisesRegex(foreman_reset.DeliveryInterrupted, "(?s)context was not cleared.*not retried"):
            self.run_deliver(FakeClient(["idle"]), screen_changed=False)

    def test_a_resume_prompt_that_did_not_land_is_an_interrupted_delivery(self):
        with self.assertRaisesRegex(foreman_reset.DeliveryInterrupted, "did not land"):
            self.run_deliver(FakeClient(["idle"]), landed=False)

    def test_a_refusal_before_any_keystroke_is_an_ordinary_failure(self):
        with self.assertRaises(HerdrError) as caught:
            self.run_deliver(FakeClient(["idle", "working"]))
        self.assertNotIsInstance(caught.exception, foreman_reset.DeliveryInterrupted)

    def test_an_unconfigured_runtime_kind_is_refused(self):
        with self.assertRaisesRegex(StateError, "kind 'grok'"):
            self.run_deliver(FakeClient(["idle"], kind="grok"))

    def test_a_pane_that_starts_working_again_gets_no_keystroke(self):
        client = FakeClient(["idle", "working"])
        with self.assertRaisesRegex(HerdrError, "changed .* before typing"):
            self.run_deliver(client)

    def test_a_pane_whose_runtime_changed_gets_no_keystroke(self):
        client = FakeClient(["idle"])
        original = client.agent_list
        calls = {"n": 0}

        def swapped():
            calls["n"] += 1
            rows = original()
            if calls["n"] > 1:
                rows[1]["agent"] = "codex"
            return rows

        client.agent_list = swapped
        with self.assertRaisesRegex(HerdrError, "changed \\(codex foreman"):
            self.run_deliver(client)

    def test_a_stow_that_changed_while_waiting_stops_the_reset(self):
        with self.assertRaisesRegex(UsageError, "no longer reset-ready"):
            self.run_deliver(FakeClient(["idle"]), still_ready=lambda: False)

    def test_the_resume_prompt_quotes_a_state_path_with_spaces(self):
        prompt = foreman_reset.resume_prompt("round-7", "/tmp/owner state.json")
        self.assertIn("--state '/tmp/owner state.json'", prompt)

    def test_mechanics_copy_does_not_rename_the_template(self):
        template = worker("claude-a", "claude")
        foreman = foreman_reset.mechanics([template], "claude", "foreman")
        self.assertEqual((template.name, foreman.name), ("claude-a", "foreman"))


class RecordTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.state = Path(self.dir.name) / "state.json"
        self.plan = {"pane_id": PANE, "stow": "round-7"}
        self.starts = 0

    def start(self):
        self.starts += 1
        return 1000 + self.starts

    def schedule(self, at, live):
        return foreman_reset.schedule(self.state, self.plan, at, self.start, alive=lambda process: live,
                                      probe=lambda pid: {"pid": pid, "identity": "proc-%d" % pid})

    def me(self, pid=1001):
        return {"pid": pid, "identity": "proc-%d" % pid}

    def test_a_retry_of_a_live_reset_replays_without_spawning(self):
        first = self.schedule("2026-09-24T10:00:00+00:00", True)
        again = self.schedule("2026-09-24T10:05:00+00:00", True)
        self.assertEqual((first["replayed"], again["replayed"], again["process"], self.starts), (False, True, self.me(), 1))

    def test_a_delivered_reset_replays_even_after_its_process_exits(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        foreman_reset.finish(self.state, self.plan, "delivered", {"cleared": True})
        again = self.schedule("2026-09-24T10:05:00+00:00", False)
        self.assertTrue(again["replayed"])
        self.assertEqual(self.starts, 1)

    def test_a_reset_whose_deliverer_died_before_claiming_is_rescheduled(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        again = self.schedule("2026-09-24T10:05:00+00:00", False)
        self.assertEqual((again["replayed"], again["process"]), (False, self.me(1002)))
        rows = json.loads(foreman_reset.record_path(self.state).read_text())["resets"]
        self.assertEqual([row["status"] for row in rows], ["failed", "scheduled"])

    def test_an_interrupted_reset_is_never_retried_automatically(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        foreman_reset.finish(self.state, self.plan, "interrupted", {"error": "herdr_error"})
        with self.assertRaisesRegex(UsageError, "stopped mid-delivery"):
            self.schedule("2026-09-24T10:05:00+00:00", True)
        self.assertEqual(self.starts, 1)

    def test_a_reset_that_died_mid_delivery_is_never_retried_automatically(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        self.assertTrue(foreman_reset.claim(self.state, self.plan, self.me()))
        with self.assertRaisesRegex(UsageError, "stopped mid-delivery"):
            self.schedule("2026-09-24T10:05:00+00:00", False)
        self.assertEqual(self.starts, 1)

    def test_only_the_started_deliverer_claims_its_reset_once(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        self.assertFalse(foreman_reset.claim(self.state, self.plan, self.me(9999)))
        self.assertTrue(foreman_reset.claim(self.state, self.plan, self.me()))
        self.assertFalse(foreman_reset.claim(self.state, self.plan, self.me()))
        self.assertFalse(foreman_reset.claim(self.state, {"pane_id": PANE, "stow": "other"}, self.me()))

    def test_a_reused_pid_is_not_the_recorded_deliverer(self):
        foreman_reset.schedule(self.state, self.plan, "2026-09-24T10:00:00+00:00", self.start,
                               probe=lambda pid: {"pid": pid, "identity": "original"})
        self.assertFalse(foreman_reset._alive({"pid": 1001, "identity": "original"},
                                              probe=lambda pid: {"pid": pid, "identity": "someone-else"}))
        again = foreman_reset.schedule(self.state, self.plan, "2026-09-24T10:05:00+00:00", self.start,
                                       probe=lambda pid: {"pid": pid, "identity": "someone-else"})
        self.assertFalse(again["replayed"])

    def test_a_malformed_record_row_is_refused(self):
        foreman_reset.record_path(self.state).write_text(json.dumps(
            {"schema_version": 1, "resets": [{"schema_version": 1, "pane_id": PANE, "stow": "s", "status": ["x"], "scheduled_at": "t", "process": None, "result": None}]}))
        with self.assertRaisesRegex(StateError, "malformed"):
            foreman_reset.replay(self.state, self.plan)


class ResetCommandTest(CliCase):
    def test_schedules_a_detached_deliverer_for_the_bound_pane(self):
        spawned = []
        with patch("teamlead.cli.memory.show", return_value={"record": READY}), \
             patch("teamlead.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE}), \
             patch("teamlead.cli._spawn_detached", side_effect=lambda argv, sink: spawned.append(argv) or 4242):
            code, out, err = self.run_cli(self.base() + ["foreman-reset"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["scheduled"], result["process"]["pid"], result["pane_id"]), (True, 4242, PANE))
        self.assertIn("foreman-reset-deliver", spawned[0])
        self.assertEqual(spawned[0][spawned[0].index("--pane") + 1], PANE)
        self.assertEqual(spawned[0][spawned[0].index("--stow") + 1], "round-7")
        self.assertTrue(Path(spawned[0][spawned[0].index("--config") + 1]).is_absolute())
        self.assertTrue(Path(spawned[0][spawned[0].index("--state") + 1]).is_absolute())

    def test_a_retry_replays_before_preconditions_that_the_reset_itself_changed(self):
        # A live pid: this test process stands in for the running deliverer.
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid)
        with patch("teamlead.cli.memory.show", return_value={"record": {"id": "round-7", "kind": "stow", "reset_ready": False}}), \
             patch("teamlead.cli.supervision.load", return_value=supervision_data(active=True)[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE}), \
             patch("teamlead.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            code, out, err = self.run_cli(self.base() + ["foreman-reset"])
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["replayed"])

    def test_a_replay_still_refuses_a_caller_outside_the_foreman_pane(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid)
        with patch("teamlead.cli.memory.show", return_value={"record": READY}), \
             patch("teamlead.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": "w2:p1"}):
            self.out, self.err = io.StringIO(), io.StringIO()
            code, _, err = self.run_cli(self.base() + ["foreman-reset"])
        self.assertEqual(code, 1)
        self.assertIn("own pane", err)

    def test_the_deliverer_claims_its_reset_while_the_parent_holds_the_state_lock(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid)
        with state_lock(retrospective.canonical_state(self.state)), \
             patch("teamlead.foreman_reset.deliver", return_value={"cleared": True}):
            code, out, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                          client=object())
        self.assertEqual(code, 0, err)
        rows = json.loads(foreman_reset.record_path(self.state).read_text())["resets"]
        self.assertEqual(rows[-1]["status"], "delivered")

    def test_a_setup_failure_after_the_claim_fails_the_reset_instead_of_stranding_it(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid)
        with patch("teamlead.cli.load_config", side_effect=StateError("config unreadable", {})):
            code, _, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                        client=object())
        self.assertEqual(code, 1)
        rows = json.loads(foreman_reset.record_path(self.state).read_text())["resets"]
        self.assertEqual(rows[-1]["status"], "failed")

    def test_a_deliverer_that_does_not_own_the_reset_sends_nothing(self):
        code, out, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                      client=object())
        self.assertEqual(code, 0, err)
        self.assertIn("not the scheduled owner", json.loads(out)["skipped"])

    def test_a_refused_preflight_spawns_nothing(self):
        with patch("teamlead.cli.memory.show", return_value={"record": {"id": "s", "kind": "stow", "reset_ready": False}}), \
             patch("teamlead.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE}), \
             patch("teamlead.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            self.out, self.err = io.StringIO(), io.StringIO()
            code, _, err = self.run_cli(self.base() + ["foreman-reset"])
        self.assertEqual(code, 1)
        self.assertIn("not reset-ready", err)


if __name__ == "__main__":
    unittest.main()
