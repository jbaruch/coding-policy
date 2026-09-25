"""The foreman resets its own context only when nothing would be lost (#483)."""

import io
import os
import fcntl
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead import foreman_reset, retrospective, supervision_runtime
from teamlead.state import state_lock
from teamlead.errors import HerdrError, StateError, UsageError
from tests.test_cli import CliCase

PANE = "w9:p1"
READY = {"id": "round-7", "kind": "stow", "reset_ready": True}


def supervision_data(*, active=False, events=False, held=False, hold_kind="handoff"):
    """Real supervision shapes: an enrolled member and, when held, a hold covering it."""
    from teamlead import supervision
    member = {"id": "d1", "active": active, "refinements": [],
              "assignment": {"id": "d1", "agent": "worker", "task": "t", "report": "/r.md", "pane_id": "w2:p1",
                             "native_session": None}}
    data = {"binding": {"identity": {"pane_id": PANE}}, "members": [member],
            "events": [{"id": "e1", "seq": 1, "member": "d1"}] if events else [], "acknowledgements": [], "holds": []}
    if held:
        data["holds"].append({"kind": hold_kind, "resumed_at": None, "through": len(data["events"]),
                              "members": supervision.active_digest(data)})
    return data, held


def check(stow=READY, caller: "str | None" = PANE, **state):
    data, _held = supervision_data(**state)
    return foreman_reset.preflight(stow, data, caller)


class PreflightTest(unittest.TestCase):
    def test_an_open_user_pause_refuses_even_under_a_handoff_hold(self):
        data, _ = supervision_data(active=True, held=True, hold_kind="handoff")
        data["holds"].append({"id": "ask-user", "kind": "waiting_for_user", "resumed_at": None,
                              "through": 0, "members": "x"})
        with self.assertRaisesRegex(UsageError, "user pause is still open"):
            foreman_reset.preflight(READY, data, PANE)

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
    def test_only_a_handoff_hold_lets_the_foreman_reset(self):
        self.assertEqual(check(active=True, held=True, hold_kind="handoff")["pane_id"], PANE)
        with self.assertRaisesRegex(UsageError, "user pause is still open"):
            check(active=True, held=True, hold_kind="waiting_for_user")


class DeliverTest(unittest.TestCase):
    def run_deliver(self, client, *, screen_changed=True, landed=True, started=None, budget=30, still_ready=lambda: True):
        calls = []
        ticks = iter(range(0, 10000, 5))

        def command(c, agent, pane, text, **kw):
            kw["before_input"]()
            calls.append(("command", agent.name, pane, text))
            return {"screen_changed": screen_changed}

        def message(c, agent, text, needle, **kw):
            kw["before_input"]()
            calls.append(("message", agent.name, kw["pane_id"], text))
            return {"landed": landed, "started": landed if started is None else started}

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
        with self.assertRaisesRegex(HerdrError, "(?s)stayed working.*Do not run foreman-reset again"):
            self.run_deliver(FakeClient(["working"]), budget=10)

    def test_a_clear_that_changed_nothing_is_an_interrupted_delivery(self):
        with self.assertRaisesRegex(foreman_reset.DeliveryInterrupted, "(?s)context was not cleared.*not retried"):
            self.run_deliver(FakeClient(["idle"]), screen_changed=False)

    def test_a_resume_prompt_that_did_not_land_is_an_interrupted_delivery(self):
        with self.assertRaisesRegex(foreman_reset.DeliveryInterrupted, "did not land"):
            self.run_deliver(FakeClient(["idle"]), landed=False)

    def test_a_prompt_that_landed_but_started_no_turn_is_interrupted(self):
        with self.assertRaisesRegex(foreman_reset.DeliveryInterrupted, "start a turn"):
            self.run_deliver(FakeClient(["idle"]), started=False)

    def test_a_stow_that_changes_between_keystrokes_stops_the_prompt(self):
        answers = iter([True, True, False])
        with self.assertRaisesRegex(foreman_reset.DeliveryInterrupted, "stopped being reset-ready"):
            self.run_deliver(FakeClient(["idle"]), still_ready=lambda: next(answers))

    def test_every_resume_command_is_a_runnable_teamlead_call(self):
        prompt = foreman_reset.resume_prompt("round-7", "/s.json")
        for command in ("memory-show", "supervision-bind", "supervision-resume", "supervision-status",
                        "supervision-drain", "foreman-queue", "load-set"):
            self.assertIn("`teamlead {} --state /s.json".format(command), prompt)

    def test_a_refusal_before_any_keystroke_is_an_ordinary_failure(self):
        with self.assertRaises(HerdrError) as caught:
            self.run_deliver(FakeClient(["idle", "working"]))
        self.assertNotIsInstance(caught.exception, foreman_reset.DeliveryInterrupted)

    def test_an_unconfigured_runtime_kind_is_refused(self):
        with self.assertRaisesRegex(StateError, "kind 'grok'"):
            self.run_deliver(FakeClient(["idle"], kind="grok"))

    def test_a_single_done_flicker_is_not_an_ended_turn(self):
        # references/herdr.md: `done` can read for one poll mid-turn.
        client = FakeClient(["done", "working"])
        with self.assertRaisesRegex(HerdrError, "stayed working"):
            self.run_deliver(client, budget=30)

    def test_a_pane_that_starts_working_again_gets_no_keystroke(self):
        client = FakeClient(["idle"] * foreman_reset.RESET_STABLE_READS + ["working"])
        with self.assertRaisesRegex(HerdrError, "(?s)changed .* before typing.*Do not run foreman-reset again"):
            self.run_deliver(client)

    def test_a_pane_whose_runtime_changed_gets_no_keystroke(self):
        client = FakeClient(["idle"])
        original = client.agent_list
        calls = {"n": 0}

        def swapped():
            calls["n"] += 1
            rows = original()
            if calls["n"] > foreman_reset.RESET_STABLE_READS:
                rows[1]["agent"] = "codex"
            return rows

        client.agent_list = swapped
        with self.assertRaisesRegex(HerdrError, "changed \\(codex foreman"):
            self.run_deliver(client)

    def test_a_stow_that_changed_while_waiting_stops_the_reset(self):
        with self.assertRaisesRegex(UsageError, "(?s)no longer reset-ready.*Do not run foreman-reset again"):
            self.run_deliver(FakeClient(["idle"]), still_ready=lambda: False)

    def test_the_resume_prompt_quotes_a_state_path_with_spaces(self):
        prompt = foreman_reset.resume_prompt("round-7", "/tmp/owner state.json")
        self.assertIn("--state '/tmp/owner state.json'", prompt)

    def test_the_resume_prompt_carries_config_and_herdr_on_every_command(self):
        prompt = foreman_reset.resume_prompt("round-7", "/s.json", config="/c/my config.json", herdr_bin="/opt/herdr")
        flags = "--state /s.json --config '/c/my config.json' --herdr-bin /opt/herdr"
        for command in ("memory-show", "supervision-bind", "supervision-resume", "supervision-status",
                        "supervision-drain", "foreman-queue", "load-set"):
            with self.subTest(command=command):
                self.assertIn("teamlead {} {}".format(command, flags), prompt)

    def test_an_unnamed_foreman_pane_sends_nothing(self):
        class Unnamed(FakeClient):
            def agent_list(self):
                return [{"pane_id": PANE, "agent_status": "idle", "agent": "claude"}]
        with self.assertRaisesRegex(HerdrError, "(?s)no agent name.*Do not run foreman-reset again"):
            self.run_deliver(Unnamed(["idle"]))

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

    DELIVERED = {"schema_version": 1, "pane_id": PANE, "stow": "round-7", "agent": "foreman", "cleared": True,
                 "resume": {"landed": True, "started": True}}
    FAILURE = {"error": "herdr_error", "message": "boom", "details": {}, "resume_prompt": "resume"}

    def test_a_retry_of_a_live_reset_replays_without_spawning(self):
        first = self.schedule("2026-09-24T10:00:00+00:00", True)
        again = self.schedule("2026-09-24T10:05:00+00:00", True)
        self.assertEqual((first["replayed"], again["replayed"], again["process"], self.starts), (False, True, self.me(), 1))

    def test_a_delivered_reset_replays_even_after_its_process_exits(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        foreman_reset.claim(self.state, self.plan, self.me())
        foreman_reset.finish(self.state, self.plan, "delivered", self.DELIVERED)
        again = self.schedule("2026-09-24T10:05:00+00:00", False)
        self.assertTrue(again["replayed"])
        self.assertEqual(self.starts, 1)

    def test_no_ended_reset_is_retried_and_each_refusal_carries_the_resume_prompt(self):
        for status in ("failed", "interrupted", "scheduled", "delivering"):
            with self.subTest(status=status):
                foreman_reset.record_path(self.state).unlink(missing_ok=True)
                self.starts = 0
                self.schedule("2026-09-24T10:00:00+00:00", True)
                if status != "scheduled":
                    foreman_reset.claim(self.state, self.plan, self.me())
                if status not in ("scheduled", "delivering"):
                    foreman_reset.finish(self.state, self.plan, status, self.FAILURE)
                with self.assertRaises(foreman_reset.ResetEnded) as caught:
                    self.schedule("2026-09-24T10:05:00+00:00", False)
                self.assertEqual(caught.exception.code, "reset_ended")
                self.assertIn("Do not run foreman-reset again", caught.exception.message)
                expected = "resume" if status in ("failed", "interrupted") else "memory-show --state"
                self.assertIn(expected, caught.exception.details["resume_prompt"])
                row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]
                self.assertEqual(row["status"], {"scheduled": "failed", "delivering": "interrupted"}.get(status, status))
                self.assertEqual(self.starts, 1)

    def test_a_reused_pid_is_not_the_recorded_deliverer(self):
        original = {"pid": 1001, "identity": "original"}
        foreman_reset.schedule(self.state, self.plan, "2026-09-24T10:00:00+00:00", lambda: 1001,
                               probe=lambda pid: original)
        with patch("teamlead.foreman_reset.process_identity", return_value=original):
            live = foreman_reset.replay(self.state, self.plan)
        self.assertIsNotNone(live)
        assert live is not None  # narrowed for the type checker; the assertion above is the check
        self.assertTrue(live["replayed"])
        # The same pid now belongs to another process: the reset is not live.
        with patch("teamlead.foreman_reset.process_identity", return_value={"pid": 1001, "identity": "someone-else"}), \
             self.assertRaises(foreman_reset.ResetEnded):
            foreman_reset.replay(self.state, self.plan)

    def test_a_launch_failure_leaves_a_failed_row_with_the_resume_prompt(self):
        def broken():
            raise StateError("spawn failed", {})
        with self.assertRaisesRegex(foreman_reset.ResetEnded, "(?s)spawn failed.*Do not run foreman-reset again") as caught:
            foreman_reset.schedule(self.state, self.plan, "2026-09-24T10:00:00+00:00", broken)
        row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]
        self.assertEqual((row["status"], row["process"]), ("failed", None))
        self.assertIn("memory-show", row["result"]["resume_prompt"])
        self.assertEqual(caught.exception.details["resume_prompt"], row["result"]["resume_prompt"])

    def test_a_deliverer_gone_before_identification_fails_the_row(self):
        with self.assertRaisesRegex(foreman_reset.ResetEnded, "exited before it could be identified"):
            foreman_reset.schedule(self.state, self.plan, "2026-09-24T10:00:00+00:00", self.start, probe=lambda pid: None)
        row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]
        self.assertEqual((row["status"], row["process"]), ("failed", None))

    def test_only_the_started_deliverer_claims_its_reset_once(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        self.assertFalse(foreman_reset.claim(self.state, self.plan, self.me(9999)))
        self.assertTrue(foreman_reset.claim(self.state, self.plan, self.me()))
        self.assertFalse(foreman_reset.claim(self.state, self.plan, self.me()))
        self.assertFalse(foreman_reset.claim(self.state, {"pane_id": PANE, "stow": "other"}, self.me()))

    def test_a_truncated_delivered_result_is_refused_and_not_recorded(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        foreman_reset.claim(self.state, self.plan, self.me())
        before = foreman_reset.record_path(self.state).read_text()
        with self.assertRaises(foreman_reset.ResetRecordUnusable):
            foreman_reset.finish(self.state, self.plan, "delivered", {"cleared": True})
        self.assertEqual(foreman_reset.record_path(self.state).read_text(), before)

    def test_a_delivered_result_for_another_reset_is_malformed(self):
        for field, value in (("stow", "round-8"), ("pane_id", "w9:p9"), ("schema_version", 2)):
            with self.subTest(field=field):
                row = {"schema_version": 1, "pane_id": PANE, "stow": "round-7", "status": "delivered",
                       "scheduled_at": "2026-09-24T10:00:00+00:00", "options": {}, "process": self.me(),
                       "result": {**self.DELIVERED, field: value}}
                foreman_reset.record_path(self.state).write_text(json.dumps({"schema_version": 1, "resets": [row]}))
                with self.assertRaises(foreman_reset.ResetRecordUnusable):
                    foreman_reset.replay(self.state, self.plan)

    def test_only_a_delivering_row_finishes(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        with self.assertRaisesRegex(foreman_reset.ResetRecordUnusable, "no delivering reset"):
            foreman_reset.finish(self.state, self.plan, "delivered", self.DELIVERED)
        with self.assertRaisesRegex(foreman_reset.ResetRecordUnusable, "no delivering reset"):
            foreman_reset.finish(self.state, {"pane_id": PANE, "stow": "never"}, "failed", self.FAILURE)

    def test_a_null_process_is_valid_only_before_identification(self):
        for status, result, valid in (("scheduled", None, True), ("failed", self.FAILURE, True),
                                      ("delivering", None, False), ("interrupted", self.FAILURE, False),
                                      ("delivered", self.DELIVERED, False)):
            with self.subTest(status=status):
                row = {"schema_version": 1, "pane_id": PANE, "stow": "round-7", "status": status,
                       "scheduled_at": "2026-09-24T10:00:00+00:00", "options": {}, "process": None, "result": result}
                foreman_reset.record_path(self.state).write_text(json.dumps({"schema_version": 1, "resets": [row]}))
                status = foreman_reset.outstanding(self.state, alive=lambda process: True)
                self.assertEqual(any(item["status"] == "record_unusable" for item in status), not valid)

    def test_duplicate_rows_for_one_reset_are_malformed(self):
        row = {"schema_version": 1, "pane_id": PANE, "stow": "round-7", "status": "failed",
               "scheduled_at": "2026-09-24T10:00:00+00:00", "options": {}, "process": None, "result": self.FAILURE}
        foreman_reset.record_path(self.state).write_text(json.dumps({"schema_version": 1, "resets": [row, row]}))
        with self.assertRaises(foreman_reset.ResetRecordUnusable):
            foreman_reset.replay(self.state, self.plan)

    def test_unreadable_and_linked_records_are_unusable_and_untouched(self):
        path = foreman_reset.record_path(self.state)
        path.write_bytes(b"{not json")
        with self.assertRaises(foreman_reset.ResetRecordUnusable):
            foreman_reset.replay(self.state, self.plan)
        self.assertEqual(path.read_bytes(), b"{not json")
        path.unlink()
        path.symlink_to(Path(self.dir.name) / "elsewhere.json")
        with self.assertRaises(foreman_reset.ResetRecordUnusable):
            self.schedule("2026-09-24T10:00:00+00:00", True)
        self.assertTrue(path.is_symlink())
        self.assertFalse((Path(self.dir.name) / "elsewhere.json").exists())

    def test_claim_waits_for_the_lock_the_scheduling_parent_holds(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        lock = Path(str(foreman_reset.record_path(self.state)) + ".lock").open("a")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        waits = []

        def parent_finishes(seconds):
            waits.append(seconds)
            lock.close()

        self.assertTrue(foreman_reset.claim(self.state, self.plan, self.me(), sleep=parent_finishes, clock=lambda: 0.0))
        self.assertEqual(waits, [foreman_reset.CLAIM_LOCK_POLL_SEC])

    def test_claim_gives_up_at_its_budget_without_claiming(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        lock = Path(str(foreman_reset.record_path(self.state)) + ".lock").open("a")
        self.addCleanup(lock.close)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = [0.0]

        def tick(seconds):
            now[0] += seconds

        with self.assertRaisesRegex(StateError, "still held"):
            foreman_reset.claim(self.state, self.plan, self.me(), sleep=tick, clock=lambda: now[0])
        lock.close()
        row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]
        self.assertEqual(row["status"], "scheduled")

    def test_a_newer_record_reads_as_no_prior_reset_and_refuses_writes_untouched(self):
        path = foreman_reset.record_path(self.state)
        newer = json.dumps({"schema_version": 2, "resets": [{"shape": "from the future"}]})
        path.write_text(newer)
        self.assertIsNone(foreman_reset.replay(self.state, self.plan))
        with self.assertRaises(foreman_reset.ResetRecordNewer) as caught:
            self.schedule("2026-09-24T10:00:00+00:00", True)
        self.assertEqual(caught.exception.code, "reset_record_newer")
        self.assertIn("update the coding-policy plugin", caught.exception.message)
        with self.assertRaises(foreman_reset.ResetRecordNewer):
            foreman_reset.claim(self.state, self.plan, self.me())
        self.assertEqual((path.read_text(), self.starts), (newer, 0))

    def test_a_corrupt_record_is_distinct_from_a_newer_one(self):
        path = foreman_reset.record_path(self.state)
        for document in ([], {"schema_version": 0, "resets": []}, {"schema_version": "2", "resets": []}):
            with self.subTest(document=document):
                path.write_text(json.dumps(document))
                with self.assertRaises(foreman_reset.ResetRecordUnusable) as caught:
                    foreman_reset.replay(self.state, self.plan)
                self.assertEqual(caught.exception.code, "reset_record_unusable")
                self.assertEqual(path.read_text(), json.dumps(document))

    def test_a_malformed_record_row_is_refused(self):
        foreman_reset.record_path(self.state).write_text(json.dumps(
            {"schema_version": 1, "resets": [{"schema_version": 1, "pane_id": PANE, "stow": "s", "status": ["x"], "scheduled_at": "t", "process": None, "result": None}]}))
        with self.assertRaisesRegex(StateError, "malformed"):
            foreman_reset.replay(self.state, self.plan)


#: A fixed reset time, so no test records a run-dependent timestamp.
RESET_AT = "2026-09-24T10:00:00+00:00"


class ResetCommandTest(CliCase):
    def test_a_reset_with_an_invalid_time_writes_nothing(self):
        with patch("teamlead.cli.memory.show", return_value={"record": READY}), \
             patch("teamlead.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1"}), \
             patch("teamlead.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", "not-a-time"])
        self.assertEqual(code, 1)
        self.assertFalse(foreman_reset.record_path(self.state).exists())

    def test_schedules_a_detached_deliverer_for_the_bound_pane(self):
        spawned = []
        with patch("teamlead.cli.memory.show", return_value={"record": READY}), \
             patch("teamlead.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1"}), \
             patch("teamlead.cli._spawn_detached", side_effect=lambda argv, sink: spawned.append(argv) or 4242), \
             patch("teamlead.foreman_reset.process_identity", side_effect=lambda pid: {"pid": pid, "identity": "child"}):
            code, out, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["scheduled"], result["process"]["pid"], result["pane_id"]), (True, 4242, PANE))
        self.assertIn("foreman-reset-deliver", spawned[0])
        self.assertEqual(spawned[0][spawned[0].index("--pane") + 1], PANE)
        self.assertEqual(spawned[0][spawned[0].index("--stow") + 1], "round-7")
        self.assertTrue(Path(spawned[0][spawned[0].index("--config") + 1]).is_absolute())
        self.assertEqual(json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]["scheduled_at"], RESET_AT)
        self.assertTrue(Path(spawned[0][spawned[0].index("--state") + 1]).is_absolute())

    def test_a_retry_replays_before_preconditions_that_the_reset_itself_changed(self):
        # A live pid: this test process stands in for the running deliverer.
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid)
        with patch("teamlead.cli.memory.show", return_value={"record": {"id": "round-7", "kind": "stow", "reset_ready": False}}), \
             patch("teamlead.cli.supervision.load", return_value=supervision_data(active=True)[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1"}), \
             patch("teamlead.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            code, out, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["replayed"])

    def test_a_replay_still_refuses_a_caller_outside_the_foreman_pane(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid)
        with patch("teamlead.cli.memory.show", return_value={"record": READY}), \
             patch("teamlead.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": "w2:p1", "HERDR_ENV": "1"}):
            self.out, self.err = io.StringIO(), io.StringIO()
            code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 1)
        self.assertIn("own pane", err)

    def test_the_deliverer_claims_its_reset_while_the_parent_holds_the_state_lock(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid)
        with state_lock(retrospective.canonical_state(self.state)), \
             patch("teamlead.foreman_reset.deliver", return_value={
                 "schema_version": 1, "pane_id": PANE, "stow": "round-7", "agent": "foreman", "cleared": True,
                 "resume": {"landed": True, "started": True}}):
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
        emitted = json.loads(err)
        self.assertEqual(emitted["error"], "reset_ended")
        self.assertEqual(emitted["details"]["record"], str(foreman_reset.record_path(self.state)))
        self.assertEqual(emitted["details"]["resume_prompt"], rows[-1]["result"]["resume_prompt"])
        self.assertIn("--config", emitted["details"]["resume_prompt"])
        self.assertEqual(emitted["details"]["cause"]["message"], "config unreadable")
        outstanding = foreman_reset.outstanding(self.state)
        self.assertEqual([(item["stow"], item["status"]) for item in outstanding], [("round-7", "failed")])
        self.assertEqual(outstanding[0]["resume_prompt"], rows[-1]["result"]["resume_prompt"])

    def test_an_unrecordable_failure_is_not_reset_ended_and_still_surfaces(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid)
        with patch("teamlead.cli.load_config", side_effect=StateError("config unreadable", {})), \
             patch("teamlead.foreman_reset.finish", side_effect=foreman_reset.ResetRecordUnusable("record gone", {})):
            code, _, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                        client=object())
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "reset_record_unusable")
        # The row never reached an outcome; once its deliverer is gone, catch-up shows it.
        outstanding = foreman_reset.outstanding(self.state, alive=lambda process: False)
        self.assertEqual([(item["status"], item["resume_prompt"]) for item in outstanding], [("delivering", None)])
        self.assertIn("outcome is unknown", outstanding[0]["needed"])

    def test_a_delivery_the_record_cannot_confirm_says_the_foreman_resumed(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid)
        delivered = {"schema_version": 1, "pane_id": PANE, "stow": "round-7", "agent": "foreman", "cleared": True,
                     "resume": {"landed": True, "started": True}}
        with patch("teamlead.foreman_reset.deliver", return_value=delivered), \
             patch("teamlead.foreman_reset.finish", side_effect=foreman_reset.ResetRecordUnusable("record gone", {})):
            code, _, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                        client=object())
        self.assertEqual(code, 1)
        emitted = json.loads(err)
        self.assertIn("do not recover the pane", emitted["message"])
        self.assertEqual(emitted["details"]["delivered"], delivered)

    def test_catch_up_surfaces_an_outstanding_reset_ahead_of_the_queue(self):
        from teamlead import attention_view
        plan = {"pane_id": PANE, "stow": "round-7"}
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid)
        foreman_reset.claim(self.state, plan, supervision_runtime.process_identity(os.getpid()))
        foreman_reset.finish(self.state, plan, "failed",
                             {"error": "herdr_error", "message": "boom", "details": {}, "resume_prompt": "paste me"})
        result = attention_view.catch_up(self.state, "2026-09-24T11:00:00+00:00")
        self.assertEqual([(item["stow"], item["resume_prompt"]) for item in result["foreman_resets"]], [("round-7", "paste me")])
        self.assertIn("Foreman reset failed", result["attention_markdown"])
        self.assertNotIn("Nothing currently needs your attention", result["attention_markdown"])

    def test_a_later_delivered_reset_supersedes_an_older_failure(self):
        first, second = {"pane_id": PANE, "stow": "round-7"}, {"pane_id": PANE, "stow": "round-8"}
        me = supervision_runtime.process_identity(os.getpid())
        foreman_reset.schedule(self.state, first, "2026-09-24T10:00:00+00:00", os.getpid)
        foreman_reset.claim(self.state, first, me)
        foreman_reset.finish(self.state, first, "failed",
                             {"error": "herdr_error", "message": "boom", "details": {}, "resume_prompt": "p"})
        foreman_reset.schedule(self.state, second, "2026-09-24T11:00:00+00:00", os.getpid)
        foreman_reset.claim(self.state, second, me)
        foreman_reset.finish(self.state, second, "delivered", {
            "schema_version": 1, "pane_id": PANE, "stow": "round-8", "agent": "foreman", "cleared": True,
            "resume": {"landed": True, "started": True}})
        self.assertEqual(foreman_reset.outstanding(self.state), [])

    def test_reconcile_closes_a_dead_delivery_either_way(self):
        plan = {"pane_id": PANE, "stow": "round-7"}
        for outcome, status in (("delivered", "reconciled"), ("failed", "failed")):
            with self.subTest(outcome=outcome):
                foreman_reset.record_path(self.state).unlink(missing_ok=True)
                foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid,
                                       options={"config": "/c/cfg.json"})
                foreman_reset.claim(self.state, plan, supervision_runtime.process_identity(os.getpid()))
                row = foreman_reset.reconcile(self.state, plan, outcome, "2026-09-24T11:00:00+00:00",
                                              alive=lambda process: False)
                self.assertEqual(row["status"], status)
                items = foreman_reset.outstanding(self.state, alive=lambda process: False)
                if outcome == "delivered":
                    self.assertEqual(items, [])
                else:
                    self.assertIn("--config /c/cfg.json", items[0]["resume_prompt"])

    def test_reconcile_refuses_a_live_or_finished_reset(self):
        plan = {"pane_id": PANE, "stow": "round-7"}
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid)
        with self.assertRaisesRegex(UsageError, "still has its deliverer running"):
            foreman_reset.reconcile(self.state, plan, "failed", "2026-09-24T11:00:00+00:00", alive=lambda process: True)
        first = foreman_reset.reconcile(self.state, plan, "failed", "2026-09-24T11:00:00+00:00", alive=lambda process: False)
        self.assertFalse(first["replayed"])
        # An identical retry replays; a conflicting one is refused.
        again = foreman_reset.reconcile(self.state, plan, "failed", "2026-09-24T11:05:00+00:00", alive=lambda process: False)
        self.assertEqual((again["replayed"], again["result"]), (True, first["result"]))
        with self.assertRaisesRegex(UsageError, r"already ended failed \(reconciled as failed\)"):
            foreman_reset.reconcile(self.state, plan, "delivered", "2026-09-24T12:00:00+00:00", alive=lambda process: False)

    def test_an_interrupted_reset_the_operator_saw_resume_reconciles_as_delivered(self):
        plan = {"pane_id": PANE, "stow": "round-7"}
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid)
        foreman_reset.claim(self.state, plan, supervision_runtime.process_identity(os.getpid()))
        foreman_reset.finish(self.state, plan, "interrupted",
                             {"error": "herdr_error", "message": "x", "details": {}, "resume_prompt": "p"})
        needed = foreman_reset.outstanding(self.state)[0]["needed"]
        self.assertIn("Look at pane {} first".format(PANE), needed)
        self.assertIn("--outcome delivered", needed)
        with self.assertRaisesRegex(UsageError, "already ended interrupted"):
            foreman_reset.reconcile(self.state, plan, "failed", "2026-09-24T11:00:00+00:00")
        row = foreman_reset.reconcile(self.state, plan, "delivered", "2026-09-24T11:00:00+00:00")
        self.assertEqual(row["status"], "reconciled")
        self.assertEqual(foreman_reset.outstanding(self.state), [])

    def test_reconcile_as_delivered_replays_and_refuses_a_later_failure(self):
        plan = {"pane_id": PANE, "stow": "round-7"}
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid)
        foreman_reset.reconcile(self.state, plan, "delivered", "2026-09-24T11:00:00+00:00", alive=lambda process: False)
        self.assertTrue(foreman_reset.reconcile(self.state, plan, "delivered", "2026-09-24T11:05:00+00:00",
                                                alive=lambda process: False)["replayed"])
        with self.assertRaisesRegex(UsageError, "already ended reconciled"):
            foreman_reset.reconcile(self.state, plan, "failed", "2026-09-24T12:00:00+00:00", alive=lambda process: False)

    def test_outstanding_names_the_reconcile_command(self):
        plan = {"pane_id": PANE, "stow": "round-7"}
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid)
        needed = foreman_reset.outstanding(self.state, alive=lambda process: False)[0]["needed"]
        self.assertIn("foreman-reset-reconcile --state {} --pane {} --stow round-7 --outcome failed".format(
            self.state.resolve(), PANE), needed)
        self.assertIn("bash {}".format(foreman_reset.launcher()), needed)
        self.assertTrue(Path(foreman_reset.launcher()).is_file())

    def test_a_caller_outside_herdr_is_not_the_foreman_pane(self):
        with patch("teamlead.cli.memory.show", return_value={"record": READY}), \
             patch("teamlead.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE}, clear=False), \
             patch("teamlead.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            import os as _os
            _os.environ.pop("HERDR_ENV", None)
            code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 1)
        self.assertIn("outside Herdr", err)

    def test_a_herdr_marker_other_than_1_is_outside_herdr(self):
        for marker in ("0", "fixture", ""):
            with self.subTest(marker=marker), \
                 patch("teamlead.cli.memory.show", return_value={"record": READY}), \
                 patch("teamlead.cli.supervision.load", return_value=supervision_data()[0]), \
                 patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": marker}), \
                 patch("teamlead.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
                code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
                self.assertEqual(code, 1)
                self.assertIn("outside Herdr", err)

    def test_a_failure_record_keeps_identifiers_only(self):
        from teamlead.errors import HerdrError as Raw
        leaked = Raw("composer read failed", {"pane_id": PANE, "stderr": "token=secret", "screen": ["$ export KEY=x"],
                                              "pid": 7})
        record = foreman_reset.failure(leaked, "round-7", "/s.json")
        self.assertEqual(record["details"], {"pane_id": PANE, "pid": 7})
        noisy = Raw("herdr agent prompt failed: token=secret\n$ export KEY=x", {})
        self.assertNotIn("secret", foreman_reset.failure(noisy, "round-7", "/s.json")["message"])
        own = UsageError("Stow round-7 is no longer reset-ready.", {})
        self.assertEqual(foreman_reset.failure(own, "round-7", "/s.json")["message"], own.message)

    def test_an_unusable_record_is_itself_outstanding(self):
        foreman_reset.record_path(self.state).write_text("{not json")
        self.assertEqual([item["status"] for item in foreman_reset.outstanding(self.state)], ["record_unusable"])

    def test_the_resume_prompt_on_recovery_keeps_the_settings_the_reset_was_scheduled_with(self):
        plan = {"pane_id": PANE, "stow": "round-7"}
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid,
                               options={"config": "/c/cfg.json", "herdr_bin": "/opt/herdr"})
        with self.assertRaises(foreman_reset.ResetEnded) as caught:
            foreman_reset.replay(self.state, plan, alive=lambda process: False)
        self.assertIn("--config /c/cfg.json --herdr-bin /opt/herdr", caught.exception.details["resume_prompt"])

    def test_versions_and_pids_are_checked_by_type_and_range(self):
        row = {"schema_version": 1, "pane_id": PANE, "stow": "round-7", "status": "delivering",
               "scheduled_at": "2026-09-24T10:00:00+00:00", "options": {}, "process": {"pid": 5, "identity": "x"},
               "result": None}
        def unusable(candidate):
            foreman_reset.record_path(self.state).write_text(json.dumps({"schema_version": 1, "resets": [candidate]}))
            return any(item["status"] == "record_unusable"
                       for item in foreman_reset.outstanding(self.state, alive=lambda process: True))

        self.assertFalse(unusable(row))
        for field, value in (("schema_version", True), ("schema_version", 1.0), ("process", {"pid": 0, "identity": "x"}),
                             ("process", {"pid": -1, "identity": "x"}), ("options", {"config": ""}),
                             ("options", {"other": "x"})):
            with self.subTest(field=field, value=value):
                self.assertTrue(unusable({**row, field: value}))

    def test_a_deliverer_that_cannot_identify_itself_exits_with_the_recovery(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid)
        with patch("teamlead.cli.supervision_runtime.process_identity", side_effect=StateError("ps timed out", {})):
            code, _, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                        client=object())
        self.assertEqual(code, 1)
        emitted = json.loads(err)
        self.assertEqual(emitted["error"], "reset_ended")
        self.assertIn("memory-show", emitted["details"]["resume_prompt"])
        # The operator's recovery needs the record to show the failure.
        row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["result"]["resume_prompt"], emitted["details"]["resume_prompt"])

    def test_fail_unclaimed_leaves_a_row_another_process_moved(self):
        plan = {"pane_id": PANE, "stow": "round-7"}
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid)
        foreman_reset.claim(self.state, plan, supervision_runtime.process_identity(os.getpid()))
        outcome = {"error": "state_error", "message": "x", "details": {}, "resume_prompt": "p"}
        self.assertEqual(foreman_reset.fail_unclaimed(self.state, plan, outcome), "delivering")
        row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]
        self.assertEqual(row["status"], "delivering")

    def test_a_deliverer_that_does_not_own_the_reset_sends_nothing(self):
        code, out, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                      client=object())
        self.assertEqual(code, 0, err)
        self.assertIn("not the scheduled owner", json.loads(out)["skipped"])

    def test_a_refused_preflight_spawns_nothing(self):
        with patch("teamlead.cli.memory.show", return_value={"record": {"id": "s", "kind": "stow", "reset_ready": False}}), \
             patch("teamlead.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1"}), \
             patch("teamlead.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            self.out, self.err = io.StringIO(), io.StringIO()
            code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 1)
        self.assertIn("not reset-ready", err)


if __name__ == "__main__":
    unittest.main()
