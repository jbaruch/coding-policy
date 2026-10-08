"""The foreman resets its own context only when nothing would be lost (#483)."""

import io
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import fcntl
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import foreman_reset, retrospective, runnable, supervision_runtime
from foreman.herdr import HerdrClient
from foreman.state import state_lock
from foreman.errors import HerdrError, StateError, UsageError
from tests.test_cli import CliCase

PANE = "w9:p1"
#: The foreman's native session as supervision-bind stores it.
SESSION = {"kind": "id", "value": "11111111-1111-4111-8111-111111111111"}
#: The new native session a consumed /clear starts.
CLEARED = "55555555-5555-4555-8555-555555555555"
READY = {"id": "round-7", "kind": "stow", "reset_ready": True}
#: The runnable operator-recovery directive every ended reset carries (#532).
DO_NOT_RERUN = re.escape("Do not run `{}` again".format(runnable.command("foreman-reset")))


def supervision_data(*, active=False, events=False, held=False, hold_kind="handoff"):
    """Real supervision shapes: an enrolled member and, when held, a hold covering it."""
    from foreman import supervision
    member = {"id": "d1", "active": active, "refinements": [],
              "assignment": {"id": "d1", "agent": "worker", "task": "t", "report": "/r.md", "pane_id": "w2:p1",
                             "native_session": None}}
    data = {"binding": {"identity": {"pane_id": PANE, **SESSION}}, "members": [member],
            "events": [{"id": "e1", "seq": 1, "member": "d1"}] if events else [], "acknowledgements": [], "holds": []}
    if held:
        data["holds"].append({"id": READY["id"], "kind": hold_kind, "resumed_at": None, "through": len(data["events"]),
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
        with self.assertRaisesRegex(UsageError, "cannot prepare this reset yet"):
            check(active=True)
        self.assertEqual(check(active=True, held=True)["pane_id"], PANE)

    def test_multiple_current_handoffs_refuse_preflight(self):
        data, _ = supervision_data(active=True, held=True)
        other = dict(data["holds"][0], id="another-stow")
        data["holds"].append(other)
        with self.assertRaisesRegex(UsageError, "without a matching handoff hold"):
            foreman_reset.preflight(READY, data, PANE)

    def test_unhandled_events_always_refuse(self):
        with self.assertRaisesRegex(UsageError, "1 unhandled event"):
            check(events=True, held=True)

    def test_an_unready_stow_refuses(self):
        with self.assertRaisesRegex(UsageError, "not reset-ready"):
            check(stow={"id": "round-7", "kind": "stow", "reset_ready": False})

    def test_a_memory_record_that_is_not_a_stow_refuses(self):
        with self.assertRaisesRegex(UsageError, "not a stow"):
            check(stow={"id": "lesson-1", "kind": "lesson"})

    def test_a_stow_named_like_the_latest_selector_refuses(self):
        with self.assertRaisesRegex(UsageError, "cannot name it exactly"):
            check(stow={"id": "latest", "kind": "stow", "reset_ready": True})

    def test_only_the_bound_foreman_pane_may_reset(self):
        with self.assertRaisesRegex(UsageError, "own pane"):
            check(caller="w2:p1")
        with self.assertRaisesRegex(UsageError, "outside Herdr"):
            check(caller=None)

    def test_no_binding_refuses(self):
        with self.assertRaisesRegex(UsageError, "No foreman is bound"):
            foreman_reset.preflight(READY, {"binding": None, "members": [], "events": [], "acknowledgements": [], "holds": []}, PANE)


class ResumePromptRunsTest(unittest.TestCase):
    """The fresh context has only the prompt, so every command in it must run as written."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="foreman-resume-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.state = self.root / "owner state.json"
        self.herdr = self.root / "herdr-stub"
        self.herdr.write_text('#!/bin/sh\necho \'{"error":{"code":"agent_not_found","message":"no"}}\' >&2\nexit 1\n',
                              encoding="utf-8")
        self.herdr.chmod(0o755)
        ledger = self.root / "TASK-LEDGER.md"
        ledger.write_text("# ledger\n", encoding="utf-8")
        record = self.root / "stow.json"
        record.write_text(json.dumps({"id": "round-7", "capture": "Release is next for task t.",
                                      "unresolved_work": ["Continue at Step 14 for task t."], "gaps": [],
                                      "required_reads": [str(ledger)]}), encoding="utf-8")
        stowed = self.run_command("bash {} memory-stow --state {} --record {}".format(
            shlex.quote(runnable.launcher()), shlex.quote(str(self.state)), shlex.quote(str(record))))
        self.assertEqual(stowed.returncode, 0, stowed.stderr)

    def run_command(self, command):
        env = {key: value for key, value in os.environ.items() if not key.startswith("HERDR")}
        return subprocess.run(shlex.split(command), capture_output=True, text=True, env=env, cwd=str(self.root),
                              check=False)

    def commands(self):
        prompt = foreman_reset.resume_prompt("round-7", str(self.state), herdr_bin=str(self.herdr))
        return re.findall(r"`(bash [^`]+)`", prompt)

    def test_the_offline_commands_run_and_answer_from_the_saved_records(self):
        by_name = {shlex.split(command)[2]: command for command in self.commands()}
        shown = self.run_command(by_name["memory-show"])
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["record"]["id"], "round-7")
        queued = self.run_command(by_name["foreman-queue"])
        self.assertEqual(queued.returncode, 0, queued.stderr)
        self.assertEqual(json.loads(queued.stdout)["queue"], [])

    def test_every_command_is_accepted_by_the_launcher(self):
        commands = self.commands()
        self.assertEqual(len(commands), 8)
        for command in commands:
            filled = (command.replace("<plan|brief|gate|diagnose>", "plan").replace("<task>", "t")
                      .replace("<enrollment-id>", "e1"))
            with self.subTest(command=filled):
                result = self.run_command(filled)
                self.assertNotIn("usage:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


class FakeClient:
    def __init__(self, statuses, kind="claude", sessions=None, pids=None, starts=None):
        self.statuses, self.kind, self.waits = list(statuses), kind, []
        # The foreground pid each `pane process-info` reports in turn, last repeating.
        self.pids = list(pids or [4242])
        # The start-and-command digest each identity probe reports in turn, last repeating.
        self.starts = list(starts or ["started-once"])
        # A scripted list is what each `pane get` reports in turn, last one
        # repeating; unscripted, the pane holds the bound session until the
        # clear is consumed, then the new one the clear started.
        self.sessions = list(sessions) if sessions is not None else None
        self.cleared = False
        self.keystrokes = []

    def identify(self, pid):
        """Stands in for supervision_runtime.process_identity, which reads `ps`."""
        start = self.starts.pop(0) if len(self.starts) > 1 else self.starts[0]
        return {"pid": pid, "identity": start}

    # Slash delivery runs the real client code, so its guards and submits
    # fire exactly where production fires them.
    def send_slash_command(self, pane_id, text, **kw):
        return HerdrClient.send_slash_command(self, pane_id, text, **kw)  # pyright: ignore[reportArgumentType] -- duck-typed stand-in for the client

    def pane_send_text(self, pane_id, text):
        self.keystrokes.append(text)

    def pane_send_keys(self, pane_id, keys):
        self.keystrokes.extend(keys)

    def agent_prompt(self, name, text):
        self.keystrokes.append(text)

    def pane_get(self, pane_id):
        if self.sessions is None:
            value = CLEARED if self.cleared else SESSION["value"]
        else:
            value = self.sessions.pop(0) if len(self.sessions) > 1 else self.sessions[0]
        return {"pane_id": pane_id, "agent_session": {"source": "herdr:" + self.kind, "agent": self.kind,
                                                      "kind": "id", "value": value}}

    def pane_process_info(self, pane_id):
        pid = self.pids.pop(0) if len(self.pids) > 1 else self.pids[0]
        foreground = [] if pid is None else [{"pid": pid, "name": self.kind}]
        return {"pane_id": pane_id, "shell_pid": 100, "foreground_processes": foreground}

    def agent_list(self):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return [{"name": "other", "pane_id": "w2:p1", "agent_status": "idle", "agent": "codex"},
                {"name": "foreman", "pane_id": PANE, "agent_status": status, "agent": self.kind}]

    def agent_wait(self, name, until=(), timeout_ms=None):
        self.waits.append(name)


def worker(name, kind, delivery="type", enters=1):
    return SimpleNamespace(name=name, kind=kind, clear_prompt="/clear", slash_delivery=delivery, slash_enter_count=enters)


class HandoffHoldTest(unittest.TestCase):
    def test_only_a_handoff_hold_lets_the_foreman_reset(self):
        self.assertEqual(check(active=True, held=True, hold_kind="handoff")["pane_id"], PANE)
        with self.assertRaisesRegex(UsageError, "user pause is still open"):
            check(active=True, held=True, hold_kind="waiting_for_user")

    def test_handoff_hold_must_name_the_stow_it_prepares(self):
        data, _ = supervision_data(active=True, held=True, hold_kind="handoff")
        data["holds"][0]["id"] = "another-stow"
        with self.assertRaisesRegex(UsageError, "matching handoff hold"):
            foreman_reset.preflight(READY, data, PANE)


class DeliverTest(unittest.TestCase):
    def run_deliver(self, client, *, screen_changed=True, landed=True, started=None, budget=30, still_ready=lambda: True,
                    native_session=SESSION, claude_delivery="type", extra_enters=0, hook_accepted=True, extra_workers=()):
        calls = []
        ticks = iter(range(0, 10000, 5))

        def command(c, agent, pane, text, **kw):
            # The real delivery: its text and each configured Enter run the guard.
            HerdrClient.deliver_slash_command(c, agent.slash_delivery, agent.name, pane, text,  # pyright: ignore[reportArgumentType] -- duck-typed stand-in for the client
                                              enter_count=agent.slash_enter_count, before_input=kw["before_input"],
                                              after_submit=kw.get("after_submit"))
            # The extra Enters send_command presses while the command still shows.
            for _ in range(extra_enters):
                kw["before_input"]()
                c.pane_send_keys(pane, ["enter"])
                if kw.get("after_submit") is not None:
                    kw["after_submit"]()
            calls.append(("command", agent.name, pane, text))
            c.cleared = screen_changed
            return {"screen_changed": screen_changed}

        def message(c, agent, text, needle, **kw):
            kw["before_input"]()
            calls.append(("message", agent.name, kw["pane_id"], text))
            c.prompt_started = True
            return {"landed": landed, "started": landed if started is None else started}

        with patch("foreman.foreman_reset.send_command", side_effect=command), \
             patch("foreman.foreman_reset.send_message", side_effect=message), \
             patch("foreman.foreman_reset.arm_resume"), \
             patch("foreman.foreman_reset.accepted_resume", side_effect=lambda *args: foreman_reset.pane_session(client, PANE) if hook_accepted else None), \
             patch("foreman.foreman_reset.process_identity", side_effect=client.identify):
            # Shipped Codex config takes two Enters; the first can only accept autocomplete.
            result = foreman_reset.deliver(client, [worker("codex-a", "codex", enters=2),
                                                    worker("claude-a", "claude", delivery=claude_delivery), *extra_workers], PANE, "round-7",
                                           "/state/s.json",
                                           still_ready=still_ready, sleep=lambda seconds: None, clock=lambda: next(ticks),
                                           budget_sec=budget, poll_sec=5, native_session=native_session)
        return result, calls

    def test_waits_for_idle_then_clears_and_sends_the_resume_prompt(self):
        client = FakeClient(["working", "working", "idle"])
        result, calls = self.run_deliver(client)
        self.assertEqual(calls, [("command", "foreman", PANE, "/clear"),
                                 ("message", "foreman", PANE, foreman_reset.guarded_resume("round-7", "/state/s.json", SESSION,
                                     [{"pid": 4242, "identity": "started-once"}]))])
        self.assertIn("memory-show --state /state/s.json --id round-7", calls[1][3])
        self.assertIn("foreman-queue --state /state/s.json", calls[1][3])
        self.assertEqual(client.waits, ["foreman"])
        self.assertTrue(result["cleared"])

    def test_codex_continuation_triggers_the_receipt_instead_of_waiting_for_it_first(self):
        class Deferred(FakeClient):
            def pane_get(self, pane_id):
                value = CLEARED if getattr(self, "prompt_started", False) else SESSION["value"]
                return {"pane_id": pane_id, "agent_session": {"source": "herdr:codex", "agent": "codex", "kind": "id", "value": value}}
        result, calls = self.run_deliver(Deferred(["idle"], kind="codex"))
        self.assertEqual([call[0] for call in calls], ["command", "message"])
        self.assertTrue(result["cleared"])

    def test_every_runtime_requires_hook_acceptance_and_never_repeats_uncertain_input(self):
        for kind in ("claude", "codex", "grok"):
            with self.subTest(kind=kind):
                client = FakeClient(["idle"], kind=kind)
                with self.assertRaises(foreman_reset.SessionInterrupted) as caught:
                    self.run_deliver(client, hook_accepted=False, extra_workers=[worker("grok-a", "grok")])
                self.assertEqual(caught.exception.details["reason"], "reset_input_unverified")
                self.assertTrue(client.prompt_started)

    def test_a_pane_that_never_idles_sends_nothing(self):
        with self.assertRaisesRegex(HerdrError, "(?s)stayed working.*" + DO_NOT_RERUN):
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

    def test_every_resume_command_is_a_runnable_foreman_call(self):
        prompt = foreman_reset.resume_prompt("round-7", "/s.json")
        for command in ("memory-show", "supervision-bind", "supervision-resume", "supervision-status",
                        "supervision-drain", "foreman-queue", "load-set"):
            self.assertIn("`bash {} {} --state /s.json".format(runnable.launcher(), command), prompt)

    def test_the_resume_prompt_routes_to_the_stow_continuation_step(self):
        prompt = foreman_reset.resume_prompt("round-7", "/s.json")
        self.assertIn("Step 17's Resume Route", prompt)
        self.assertIn("continuation step the stow's unresolved work names", prompt)
        self.assertLess(prompt.index("foreman-queue"), prompt.index("Resume Route"))

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
        with self.assertRaisesRegex(HerdrError, "(?s)changed .* before typing.*" + DO_NOT_RERUN):
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
        with self.assertRaisesRegex(UsageError, "(?s)no longer reset-ready.*" + DO_NOT_RERUN):
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
                self.assertIn("foreman.sh {} {}".format(command, flags), prompt)

    def test_an_unnamed_foreman_pane_sends_nothing(self):
        class Unnamed(FakeClient):
            def agent_list(self):
                return [{"pane_id": PANE, "agent_status": "idle", "agent": "claude"}]
        with self.assertRaisesRegex(HerdrError, "(?s)no agent name.*" + DO_NOT_RERUN):
            self.run_deliver(Unnamed(["idle"]))

    def test_a_same_name_same_kind_replacement_session_gets_no_keystroke(self):
        # #523: the operator replaced the foreman while the deliverer waited.
        client = FakeClient(["idle"], sessions=["22222222-2222-4222-8222-222222222222"])
        with self.assertRaisesRegex(foreman_reset.SessionChanged,
                                    "(?s)no longer holds the native session.*" + DO_NOT_RERUN) as caught:
            self.run_deliver(client)
        self.assertNotIsInstance(caught.exception, foreman_reset.DeliveryInterrupted)
        self.assertEqual(client.waits, [])
        # The failed row records why, not a generic Herdr failure.
        record = foreman_reset.failure(caught.exception, "round-7", "/s.json")
        self.assertEqual((record["error"], record["details"]["reason"]), ("reset_session_changed", "native_session_changed"))
        self.assertIn("no longer holds the native session", record["message"])

    def test_a_replacement_between_the_clear_keystrokes_is_interrupted_and_says_why(self):
        # The text guard sees the bound session; the Enter guard sees another.
        client = FakeClient(["idle"], sessions=[SESSION["value"], "22222222-2222-4222-8222-222222222222"])
        with self.assertRaisesRegex(foreman_reset.DeliveryInterrupted, "no longer holds the native session") as caught:
            self.run_deliver(client)
        self.assertEqual(client.keystrokes, ["/clear"])
        record = foreman_reset.failure(caught.exception, "round-7", "/s.json")
        self.assertEqual((record["error"], record["details"]["reason"]), ("reset_session_changed", "native_session_changed"))
        self.assertIn("no longer holds the native session", record["message"])

    def test_a_replacement_between_codex_enters_gets_no_submitting_enter(self):
        # Codex's first Enter only accepts autocomplete; the second submits /clear.
        client = FakeClient(["idle"], kind="codex",
                            sessions=[SESSION["value"], SESSION["value"], "22222222-2222-4222-8222-222222222222"])
        with self.assertRaises(foreman_reset.SessionInterrupted) as caught:
            self.run_deliver(client)
        self.assertEqual(client.keystrokes, ["/clear", "enter"])
        self.assertEqual(foreman_reset.failure(caught.exception, "round-7", "/s.json")["error"], "reset_session_changed")

    def test_the_session_the_clear_starts_does_not_stop_the_resume_prompt(self):
        # The submitting Enter starts a new native session; the resume prompt still goes.
        for kind, bound_reads in (("claude", 2), ("codex", 3)):
            with self.subTest(kind=kind):
                client = FakeClient(["idle"], kind=kind,
                                    sessions=[SESSION["value"]] * bound_reads + ["33333333-3333-4333-8333-333333333333"])
                result, calls = self.run_deliver(client)
                self.assertEqual([call[0] for call in calls], ["command", "message"])
                self.assertEqual(client.keystrokes, ["/clear"] + ["enter"] * (bound_reads - 1))
                self.assertTrue(result["cleared"])

    def test_a_replacement_before_an_extra_enter_gets_no_keystroke(self):
        # The configured Enter did not consume /clear; the composer presses another.
        client = FakeClient(["idle"], sessions=[SESSION["value"], SESSION["value"], "22222222-2222-4222-8222-222222222222"])
        with self.assertRaises(foreman_reset.SessionInterrupted):
            self.run_deliver(client, extra_enters=1)
        self.assertEqual(client.keystrokes, ["/clear", "enter"])

    def test_an_unresolvable_transcript_path_matches_no_session(self):
        class Looping(FakeClient):
            def pane_get(self, pane_id):
                return {"pane_id": pane_id, "agent_session": {"source": "herdr:claude", "agent": "claude", "kind": "path", "value": "/loop/t.jsonl"}}
        with patch("foreman.foreman_reset.supervision.canonical", side_effect=RuntimeError("Symlink loop")):
            self.assertIsNone(foreman_reset.pane_session(Looping(["idle"]), PANE))
            with self.assertRaises(foreman_reset.SessionChanged):
                self.run_deliver(Looping(["idle"]), native_session={"kind": "path", "value": "/loop/t.jsonl"})

    def test_a_transcript_path_with_a_nul_matches_no_session(self):
        class Nul(FakeClient):
            def pane_get(self, pane_id):
                return {"pane_id": pane_id, "agent_session": {"source": "herdr:claude", "agent": "claude", "kind": "path", "value": "/tmp/t\x00.jsonl"}}
        self.assertIsNone(foreman_reset.pane_session(Nul(["idle"]), PANE))
        with self.assertRaises(foreman_reset.SessionChanged):
            self.run_deliver(Nul(["idle"]), native_session={"kind": "path", "value": "/tmp/t.jsonl"})

    def test_a_replacement_after_the_clear_gets_no_resume_prompt(self):
        # Text and Enter find the bound session; the clear's new one is pinned;
        # then another session holds the pane before the resume prompt.
        client = FakeClient(["idle"], sessions=[SESSION["value"], SESSION["value"], CLEARED,
                                                "22222222-2222-4222-8222-222222222222"])
        with self.assertRaisesRegex(foreman_reset.SessionInterrupted, "native session the clear started") as caught:
            self.run_deliver(client)
        self.assertEqual(client.keystrokes, ["/clear", "enter"])
        record = foreman_reset.failure(caught.exception, "round-7", "/s.json")
        self.assertEqual((record["error"], record["details"]["reason"]), ("reset_session_changed", "native_session_changed"))

    def test_a_clear_that_starts_no_new_session_sends_no_resume_prompt(self):
        client = FakeClient(["idle"], sessions=[SESSION["value"]])
        with self.assertRaisesRegex(foreman_reset.DeliveryInterrupted, "no new native session") as caught:
            self.run_deliver(client)
        self.assertEqual(client.keystrokes, ["/clear", "enter"])
        self.assertEqual(foreman_reset.failure(caught.exception, "round-7", "/s.json")["details"]["reason"],
                         "clear_session_unchanged")

    def test_the_resume_prompt_waits_for_herdr_to_report_the_new_session(self):
        # Herdr reports the bound session twice more after the clear, then the new one.
        client = FakeClient(["idle"], sessions=[SESSION["value"]] * 4 + [CLEARED])
        result, calls = self.run_deliver(client)
        self.assertEqual([call[0] for call in calls], ["command", "message"])
        self.assertTrue(result["cleared"])

    def test_a_replacement_before_the_clear_session_is_pinned_gets_no_resume_prompt(self):
        # The new session appears under another process: a replacement, not the clear.
        client = FakeClient(["idle"], pids=[4242, 4242, 5151])
        with self.assertRaisesRegex(foreman_reset.SessionInterrupted, "under another process") as caught:
            self.run_deliver(client)
        self.assertEqual(client.keystrokes, ["/clear", "enter"])
        self.assertEqual(foreman_reset.failure(caught.exception, "round-7", "/s.json")["details"]["reason"],
                         "native_session_changed")

    def test_a_replacement_that_reuses_the_pid_gets_no_resume_prompt(self):
        # Same pid, another start and command line: a process that exec'd in place.
        client = FakeClient(["idle"], starts=["started-once", "started-once", "exec-in-place"])
        with self.assertRaisesRegex(foreman_reset.SessionInterrupted, "under another process"):
            self.run_deliver(client)
        self.assertEqual(client.keystrokes, ["/clear", "enter"])

    def test_a_session_another_integration_reports_is_no_match(self):
        for ref in ({"source": "other:claude", "agent": "claude"}, {"source": "herdr:foreign", "agent": "foreign"},
                    {"agent": "claude"}, {"source": "herdr:claude"}):
            with self.subTest(ref=ref):
                class Foreign(FakeClient):
                    def pane_get(self, pane_id, ref=ref):
                        return {"pane_id": pane_id, "agent_session": {**ref, "kind": "id", "value": SESSION["value"]}}
                self.assertIsNone(foreman_reset.pane_session(Foreign(["idle"]), PANE))
                with self.assertRaises(foreman_reset.SessionChanged):
                    self.run_deliver(Foreign(["idle"]))

    def test_a_pane_with_no_foreground_process_gets_no_keystroke(self):
        client = FakeClient(["idle"], pids=[None])
        with self.assertRaisesRegex(HerdrError, "no foreground process") as caught:
            self.run_deliver(client)
        self.assertNotIsInstance(caught.exception, foreman_reset.DeliveryInterrupted)
        self.assertEqual(client.keystrokes, [])

    def test_a_pasted_clear_is_submitted_by_its_paste(self):
        client = FakeClient(["idle"], sessions=[SESSION["value"], "33333333-3333-4333-8333-333333333333"])
        result, _ = self.run_deliver(client, claude_delivery="paste")
        self.assertEqual(client.keystrokes, ["/clear"])
        self.assertTrue(result["cleared"])

    def test_a_reset_that_recorded_no_session_types_nothing(self):
        with self.assertRaisesRegex(foreman_reset.SessionChanged, "recorded none"):
            self.run_deliver(FakeClient(["idle"]), native_session=None)

    def test_a_transcript_path_session_matches_its_canonical_binding(self):
        class PathClient(FakeClient):
            def pane_get(self, pane_id):
                value = "/tmp/../tmp/cleared.jsonl" if self.cleared else "/tmp/../tmp/t.jsonl"
                return {"pane_id": pane_id, "agent_session": {"source": "herdr:claude", "agent": "claude", "kind": "path", "value": value}}
        bound = {"kind": "path", "value": str(Path("/tmp/t.jsonl").resolve())}
        self.assertEqual(foreman_reset.pane_session(PathClient(["idle"]), PANE), bound)
        result, _ = self.run_deliver(PathClient(["idle"]), native_session=bound)
        self.assertTrue(result["cleared"])

    def test_a_pane_reporting_no_session_or_another_pane_is_no_match(self):
        class Bare(FakeClient):
            def pane_get(self, pane_id):
                return {"pane_id": "w2:p1", "agent_session": {"source": "herdr:claude", "agent": "claude", "kind": "id", "value": SESSION["value"]}}
        self.assertIsNone(foreman_reset.pane_session(Bare(["idle"]), PANE))
        with self.assertRaises(foreman_reset.SessionChanged):
            self.run_deliver(Bare(["idle"]))

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
                                      probe=lambda pid: {"pid": pid, "identity": "proc-%d" % pid}, native_session=SESSION)

    def me(self, pid=1001):
        return {"pid": pid, "identity": "proc-%d" % pid}

    DELIVERED = {"schema_version": foreman_reset.RESET_SCHEMA_VERSION, "pane_id": PANE, "stow": "round-7", "agent": "foreman", "cleared": True,
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
                self.assertIn(foreman_reset.OPERATOR_RECOVERY, caught.exception.message)
                expected = "resume" if status in ("failed", "interrupted") else "memory-show --state"
                self.assertIn(expected, caught.exception.details["resume_prompt"])
                row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]
                self.assertEqual(row["status"], {"scheduled": "failed", "delivering": "interrupted"}.get(status, status))
                self.assertEqual(self.starts, 1)

    def test_a_reused_pid_is_not_the_recorded_deliverer(self):
        original = {"pid": 1001, "identity": "original"}
        foreman_reset.schedule(self.state, self.plan, "2026-09-24T10:00:00+00:00", lambda: 1001,
                               probe=lambda pid: original, native_session=SESSION)
        with patch("foreman.foreman_reset.process_identity", return_value=original):
            live = foreman_reset.replay(self.state, self.plan)
        self.assertIsNotNone(live)
        assert live is not None  # narrowed for the type checker; the assertion above is the check
        self.assertTrue(live["replayed"])
        # The same pid now belongs to another process: the reset is not live.
        with patch("foreman.foreman_reset.process_identity", return_value={"pid": 1001, "identity": "someone-else"}), \
             self.assertRaises(foreman_reset.ResetEnded):
            foreman_reset.replay(self.state, self.plan)

    def test_a_launch_failure_leaves_a_failed_row_with_the_resume_prompt(self):
        def broken():
            raise StateError("spawn failed", {})
        with self.assertRaisesRegex(foreman_reset.ResetEnded, "(?s)spawn failed.*" + DO_NOT_RERUN) as caught:
            foreman_reset.schedule(self.state, self.plan, "2026-09-24T10:00:00+00:00", broken, native_session=SESSION)
        row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]
        self.assertEqual((row["status"], row["process"]), ("failed", None))
        self.assertIn("memory-show", row["result"]["resume_prompt"])
        self.assertEqual(caught.exception.details["resume_prompt"], row["result"]["resume_prompt"])

    def test_a_deliverer_gone_before_identification_fails_the_row(self):
        with self.assertRaisesRegex(foreman_reset.ResetEnded, "exited before it could be identified"):
            foreman_reset.schedule(self.state, self.plan, "2026-09-24T10:00:00+00:00", self.start, probe=lambda pid: None, native_session=SESSION)
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
        newer = json.dumps({"schema_version": foreman_reset.RESET_SCHEMA_VERSION + 1, "resets": [{"shape": "from the future"}]})
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

    def test_a_schema_1_record_migrates_with_no_session_and_keeps_every_outcome(self):
        path = foreman_reset.record_path(self.state)
        def v1(stow, status, result, process):
            return {"schema_version": 1, "pane_id": PANE, "stow": stow, "status": status,
                    "scheduled_at": "2026-09-24T10:00:00+00:00", "options": {}, "process": process, "result": result}
        delivered = {**self.DELIVERED, "schema_version": 1}
        path.write_text(json.dumps({"schema_version": 1, "resets": [
            v1("round-7", "delivered", delivered, self.me()), v1("round-6", "failed", self.FAILURE, None)]}))
        # The owner's read rewrites the record; a delivered reset still replays.
        replayed = foreman_reset.replay(self.state, self.plan, alive=lambda process: False)
        assert replayed is not None, "a migrated delivered reset replays"
        self.assertEqual((replayed["schema_version"], replayed["native_session"]), (3, None))
        document = json.loads(path.read_text())
        self.assertEqual(document["schema_version"], 3)
        self.assertEqual([(row["stow"], row["schema_version"], row["native_session"]) for row in document["resets"]],
                         [("round-7", 3, None), ("round-6", 3, None)])
        self.assertEqual(document["resets"][0]["result"]["schema_version"], 3)
        self.assertEqual([item["stow"] for item in foreman_reset.outstanding(self.state)], ["round-6"])

    def test_catch_up_alone_rewrites_a_schema_1_record(self):
        path = foreman_reset.record_path(self.state)
        row = {"schema_version": 1, "pane_id": PANE, "stow": "round-6", "status": "failed",
               "scheduled_at": "2026-09-24T10:00:00+00:00", "options": {}, "process": None, "result": self.FAILURE}
        path.write_text(json.dumps({"schema_version": 1, "resets": [row]}))
        self.assertEqual([item["stow"] for item in foreman_reset.outstanding(self.state)], ["round-6"])
        self.assertEqual(json.loads(path.read_text())["resets"][0], {**row, "schema_version": 3, "native_session": None, "foreground": None, "accepted_session": None})

    def test_schema_2_migration_preserves_session_without_inventing_hook_acceptance(self):
        path = foreman_reset.record_path(self.state)
        row = {"schema_version": 2, "pane_id": PANE, "stow": "round-7", "status": "delivering",
               "scheduled_at": "2026-09-24T10:00:00+00:00", "options": {}, "process": self.me(),
               "result": None, "native_session": SESSION}
        original = json.dumps({"schema_version": 2, "resets": [row]})
        path.write_text(original)
        with self.assertRaises(foreman_reset.ResetRecordOlder):
            foreman_reset._load(path, migrate_legacy=False)
        self.assertEqual(path.read_text(), original)
        self.assertEqual(foreman_reset.outstanding(self.state, alive=lambda process: True), [])
        self.assertEqual(json.loads(path.read_text())["resets"][0],
                         {**row, "schema_version": 3, "foreground": None, "accepted_session": None})

    def test_malformed_hook_pins_or_acceptance_refuse_without_rewriting(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        path = foreman_reset.record_path(self.state)
        document = json.loads(path.read_text())
        for field, value in (("foreground", []), ("foreground", [{"pid": True, "identity": "fixed"}]),
                             ("foreground", [{"pid": 77, "identity": "fixed"}] * 2),
                             ("accepted_session", {"kind": "id"})):
            with self.subTest(field=field, value=value):
                document["resets"][0][field] = value
                original = json.dumps(document)
                path.write_text(original)
                with self.assertRaises(foreman_reset.ResetRecordUnusable):
                    foreman_reset._load(path, migrate_legacy=False)
                self.assertEqual(path.read_text(), original)
                document["resets"][0][field] = None

    def test_a_schema_1_delivery_still_running_is_rewritten_and_surfaces_once_gone(self):
        path = foreman_reset.record_path(self.state)
        row = {"schema_version": 1, "pane_id": PANE, "stow": "round-7", "status": "delivering",
               "scheduled_at": "2026-09-24T10:00:00+00:00", "options": {}, "process": self.me(), "result": None}
        path.write_text(json.dumps({"schema_version": 1, "resets": [row]}))
        self.assertEqual(foreman_reset.outstanding(self.state, alive=lambda process: True), [])
        self.assertEqual(json.loads(path.read_text())["resets"][0], {**row, "schema_version": 3, "native_session": None, "foreground": None, "accepted_session": None})
        # That build cannot record its outcome now; once it is gone, catch-up names the repair.
        items = foreman_reset.outstanding(self.state, alive=lambda process: False)
        self.assertEqual([(item["stow"], item["status"]) for item in items], [("round-7", "delivering")])
        self.assertIn("foreman-reset-reconcile", items[0]["needed"])

    def test_a_malformed_schema_1_record_is_unusable_and_untouched(self):
        path = foreman_reset.record_path(self.state)
        bad = json.dumps({"schema_version": 1, "resets": [{"schema_version": 1, "pane_id": PANE, "stow": "round-7",
                                                           "status": "failed", "scheduled_at": "2026-09-24T10:00:00+00:00",
                                                           "options": {}, "process": None, "result": self.FAILURE,
                                                           "native_session": SESSION}]})
        path.write_text(bad)
        with self.assertRaises(foreman_reset.ResetRecordUnusable):
            foreman_reset.replay(self.state, self.plan)
        self.assertEqual(path.read_text(), bad)

    def test_a_reset_without_a_bound_session_is_not_scheduled(self):
        for session in (None, {"kind": "id"}, {"kind": "tty", "value": "x"}, {"kind": "id", "value": ""}):
            with self.subTest(session=session):
                with self.assertRaisesRegex(UsageError, "no bound native session"):
                    foreman_reset.schedule(self.state, self.plan, "2026-09-24T10:00:00+00:00", self.start,
                                           native_session=session)
                self.assertFalse(foreman_reset.record_path(self.state).exists())
                self.assertEqual(self.starts, 0)

    def test_the_claimed_row_carries_the_bound_session(self):
        self.schedule("2026-09-24T10:00:00+00:00", True)
        claimed = foreman_reset.claim(self.state, self.plan, self.me())
        assert claimed is not None, "the started deliverer claims its reset"
        self.assertEqual((claimed["status"], claimed["native_session"]), ("delivering", SESSION))

    def test_a_malformed_row_session_is_refused(self):
        for session in ({"kind": "id"}, {"kind": "id", "value": 7}, {"kind": "id", "value": "x", "extra": 1}):
            with self.subTest(session=session):
                row = {"schema_version": 2, "pane_id": PANE, "stow": "round-7", "status": "failed",
                       "scheduled_at": "2026-09-24T10:00:00+00:00", "options": {}, "process": None,
                       "result": self.FAILURE, "native_session": session}
                foreman_reset.record_path(self.state).write_text(json.dumps({"schema_version": 2, "resets": [row]}))
                with self.assertRaises(foreman_reset.ResetRecordUnusable):
                    foreman_reset.replay(self.state, self.plan)

    def test_a_malformed_record_row_is_refused(self):
        foreman_reset.record_path(self.state).write_text(json.dumps(
            {"schema_version": 1, "resets": [{"schema_version": 1, "pane_id": PANE, "stow": "s", "status": ["x"], "scheduled_at": "t", "process": None, "result": None}]}))
        with self.assertRaisesRegex(StateError, "malformed"):
            foreman_reset.replay(self.state, self.plan)


#: A fixed reset time, so no test records a run-dependent timestamp.
RESET_AT = "2026-09-24T10:00:00+00:00"


class ResetCommandTest(CliCase):
    def test_a_reset_with_an_invalid_time_writes_nothing(self):
        with patch("foreman.cli.memory.show", return_value={"record": READY}), \
             patch("foreman.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1"}), \
             patch("foreman.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", "not-a-time"])
        self.assertEqual(code, 1)
        self.assertFalse(foreman_reset.record_path(self.state).exists())

    def test_schedules_a_detached_deliverer_for_the_bound_pane(self):
        spawned = []
        with patch("foreman.cli.memory.show", return_value={"record": READY}), \
             patch("foreman.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1"}), \
             patch("foreman.cli._spawn_detached", side_effect=lambda argv, sink: spawned.append(argv) or 4242), \
             patch("foreman.foreman_reset.process_identity", side_effect=lambda pid: {"pid": pid, "identity": "child"}):
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
        # The row carries the session bound at supervision-bind, for the deliverer's guard.
        self.assertEqual(json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]["native_session"],
                         SESSION)

    def test_a_binding_without_a_native_session_schedules_nothing(self):
        data = supervision_data()[0]
        data["binding"]["identity"] = {"pane_id": PANE}
        with patch("foreman.cli.memory.show", return_value={"record": READY}), \
             patch("foreman.cli.supervision.load", return_value=data), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1"}), \
             patch("foreman.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 1)
        self.assertIn("names no native session", err)
        self.assertFalse(foreman_reset.record_path(self.state).exists())

    def test_the_deliverer_types_nothing_into_a_replacement_session_and_the_row_says_why(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, RESET_AT, os.getpid, native_session=SESSION)
        client = FakeClient(["idle"], sessions=["22222222-2222-4222-8222-222222222222"])
        real = foreman_reset.deliver
        with patch("foreman.cli.memory.show", return_value={"record": READY}), \
             patch("foreman.cli.load_config", return_value=[worker("claude-a", "claude")]), \
             patch("foreman.foreman_reset.deliver", side_effect=lambda *a, **kw: real(*a, sleep=lambda s: None, **kw)), \
             patch("foreman.foreman_reset.send_command", side_effect=lambda *a, **kw: kw["before_input"]()), \
             patch("foreman.foreman_reset.send_message", side_effect=AssertionError("must not type")):
            code, _, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                        client=client)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "reset_ended")
        row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]
        self.assertEqual((row["status"], row["result"]["error"], row["result"]["details"]["reason"]),
                         ("failed", "reset_session_changed", "native_session_changed"))
        self.assertEqual(client.waits, [])

    def schedule_with_env(self, environ, extra=()):
        """Schedule a reset under `environ`; return the deliverer argv and the recorded options."""
        spawned = []
        env = {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1", **environ}
        with patch("foreman.cli.memory.show", return_value={"record": READY}), \
             patch("foreman.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", env), \
             patch("foreman.cli._spawn_detached", side_effect=lambda argv, sink: spawned.append(argv) or 4242), \
             patch("foreman.foreman_reset.process_identity", side_effect=lambda pid: {"pid": pid, "identity": "child"}):
            if "FOREMAN_HERDR_BIN" not in environ:
                os.environ.pop("FOREMAN_HERDR_BIN", None)
            code, _, err = self.run_cli(self.base() + list(extra) + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 0, err)
        options = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][-1]["options"]
        return spawned[0], options

    def test_a_relative_foreman_herdr_bin_reaches_the_deliverer_resolved(self):
        # The deliverer runs from the package directory, so the relative value
        # it would inherit from the environment names another file there (#533).
        argv, options = self.schedule_with_env({"FOREMAN_HERDR_BIN": "bin/herdr"})
        resolved = str(Path("bin/herdr").resolve())
        self.assertEqual(argv[argv.index("--herdr-bin") + 1], resolved)
        self.assertEqual(options["herdr_bin"], resolved)

    def test_the_herdr_bin_flag_wins_over_foreman_herdr_bin(self):
        argv, options = self.schedule_with_env({"FOREMAN_HERDR_BIN": "bin/other"}, ["--herdr-bin", "bin/herdr"])
        resolved = str(Path("bin/herdr").resolve())
        self.assertEqual((argv[argv.index("--herdr-bin") + 1], options["herdr_bin"]), (resolved, resolved))

    def test_a_bare_foreman_herdr_bin_name_stays_a_path_lookup(self):
        argv, options = self.schedule_with_env({"FOREMAN_HERDR_BIN": "herdr-dev"})
        self.assertEqual((argv[argv.index("--herdr-bin") + 1], options["herdr_bin"]), ("herdr-dev", "herdr-dev"))

    def test_no_herdr_bin_setting_passes_no_flag(self):
        for environ in ({}, {"FOREMAN_HERDR_BIN": ""}):
            with self.subTest(environ=environ):
                foreman_reset.record_path(self.state).unlink(missing_ok=True)
                argv, options = self.schedule_with_env(environ)
                self.assertNotIn("--herdr-bin", argv)
                self.assertNotIn("herdr_bin", options)

    def test_a_retry_replays_before_preconditions_that_the_reset_itself_changed(self):
        # A live pid: this test process stands in for the running deliverer.
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        with patch("foreman.cli.memory.show", return_value={"record": {"id": "round-7", "kind": "stow", "reset_ready": False}}), \
             patch("foreman.cli.supervision.load", return_value=supervision_data(active=True)[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1"}), \
             patch("foreman.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            code, out, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["replayed"])

    def test_a_replay_still_refuses_a_caller_outside_the_foreman_pane(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        with patch("foreman.cli.memory.show", return_value={"record": READY}), \
             patch("foreman.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": "w2:p1", "HERDR_ENV": "1"}):
            self.out, self.err = io.StringIO(), io.StringIO()
            code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 1)
        self.assertIn("own pane", err)

    def test_the_deliverer_claims_its_reset_while_the_parent_holds_the_state_lock(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        with state_lock(retrospective.canonical_state(self.state)), \
             patch("foreman.foreman_reset.deliver", return_value={
                 "schema_version": foreman_reset.RESET_SCHEMA_VERSION, "pane_id": PANE, "stow": "round-7", "agent": "foreman", "cleared": True,
                 "resume": {"landed": True, "started": True}}):
            code, out, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                          client=object())
        self.assertEqual(code, 0, err)
        rows = json.loads(foreman_reset.record_path(self.state).read_text())["resets"]
        self.assertEqual(rows[-1]["status"], "delivered")

    def test_a_setup_failure_after_the_claim_fails_the_reset_instead_of_stranding_it(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        with patch("foreman.cli.load_config", side_effect=StateError("config unreadable", {})):
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
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        with patch("foreman.cli.load_config", side_effect=StateError("config unreadable", {})), \
             patch("foreman.foreman_reset.finish", side_effect=foreman_reset.ResetRecordUnusable("record gone", {})):
            code, _, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                        client=object())
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "reset_record_unusable")
        # The row never reached an outcome; once its deliverer is gone, catch-up shows it.
        outstanding = foreman_reset.outstanding(self.state, alive=lambda process: False)
        self.assertEqual([(item["status"], item["resume_prompt"]) for item in outstanding], [("delivering", None)])
        self.assertIn("outcome is unknown", outstanding[0]["needed"])

    def test_a_delivery_the_record_cannot_confirm_says_the_foreman_resumed(self):
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        delivered = {"schema_version": foreman_reset.RESET_SCHEMA_VERSION, "pane_id": PANE, "stow": "round-7", "agent": "foreman", "cleared": True,
                     "resume": {"landed": True, "started": True}}
        with patch("foreman.foreman_reset.deliver", return_value=delivered), \
             patch("foreman.foreman_reset.finish", side_effect=foreman_reset.ResetRecordUnusable("record gone", {})):
            code, _, err = self.run_cli(self.base() + ["foreman-reset-deliver", "--pane", PANE, "--stow", "round-7"],
                                        client=object())
        self.assertEqual(code, 1)
        emitted = json.loads(err)
        self.assertIn("do not recover the pane", emitted["message"])
        self.assertEqual(emitted["details"]["delivered"], delivered)

    def test_catch_up_surfaces_an_outstanding_reset_ahead_of_the_queue(self):
        from foreman import attention_view
        plan = {"pane_id": PANE, "stow": "round-7"}
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
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
        foreman_reset.schedule(self.state, first, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        foreman_reset.claim(self.state, first, me)
        foreman_reset.finish(self.state, first, "failed",
                             {"error": "herdr_error", "message": "boom", "details": {}, "resume_prompt": "p"})
        foreman_reset.schedule(self.state, second, "2026-09-24T11:00:00+00:00", os.getpid, native_session=SESSION)
        foreman_reset.claim(self.state, second, me)
        foreman_reset.finish(self.state, second, "delivered", {
            "schema_version": foreman_reset.RESET_SCHEMA_VERSION, "pane_id": PANE, "stow": "round-8", "agent": "foreman", "cleared": True,
            "resume": {"landed": True, "started": True}})
        self.assertEqual(foreman_reset.outstanding(self.state), [])

    def test_reconcile_closes_a_dead_delivery_either_way(self):
        plan = {"pane_id": PANE, "stow": "round-7"}
        for outcome, status in (("delivered", "reconciled"), ("failed", "failed")):
            with self.subTest(outcome=outcome):
                foreman_reset.record_path(self.state).unlink(missing_ok=True)
                foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid,
                                       options={"config": "/c/cfg.json"}, native_session=SESSION)
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
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
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
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
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
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        # Never claimed, so nothing was typed: a scheduled row cannot be delivered.
        with self.assertRaisesRegex(UsageError, "nothing to reconcile"):
            foreman_reset.reconcile(self.state, plan, "delivered", "2026-09-24T10:30:00+00:00", alive=lambda process: False)
        foreman_reset.claim(self.state, plan, supervision_runtime.process_identity(os.getpid()))
        foreman_reset.reconcile(self.state, plan, "delivered", "2026-09-24T11:00:00+00:00", alive=lambda process: False)
        self.assertTrue(foreman_reset.reconcile(self.state, plan, "delivered", "2026-09-24T11:05:00+00:00",
                                                alive=lambda process: False)["replayed"])
        with self.assertRaisesRegex(UsageError, "already ended reconciled"):
            foreman_reset.reconcile(self.state, plan, "failed", "2026-09-24T12:00:00+00:00", alive=lambda process: False)

    def test_a_reconciled_reset_replays_on_retry_and_starts_nothing(self):
        plan = {"pane_id": PANE, "stow": "round-7"}
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        foreman_reset.claim(self.state, plan, supervision_runtime.process_identity(os.getpid()))
        foreman_reset.reconcile(self.state, plan, "delivered", "2026-09-24T11:00:00+00:00", alive=lambda process: False)
        started = []
        again = foreman_reset.schedule(self.state, plan, "2026-09-24T12:00:00+00:00", lambda: started.append(1),
                                       alive=lambda process: False, native_session=SESSION)
        self.assertEqual((again["status"], again["replayed"], started), ("reconciled", True, []))
        replayed = foreman_reset.replay(self.state, plan, alive=lambda process: False)
        assert replayed is not None, "a reconciled reset replays"
        self.assertEqual(replayed["status"], "reconciled")

    def test_outstanding_names_the_reconcile_command(self):
        plan = {"pane_id": PANE, "stow": "round-7"}
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        needed = foreman_reset.outstanding(self.state, alive=lambda process: False)[0]["needed"]
        self.assertIn("foreman-reset-reconcile --state {} --pane {} --stow round-7 --outcome failed".format(
            self.state.resolve(), PANE), needed)
        self.assertIn("bash {}".format(runnable.launcher()), needed)
        self.assertTrue(Path(runnable.launcher()).is_file())

    def test_a_caller_outside_herdr_is_not_the_foreman_pane(self):
        with patch("foreman.cli.memory.show", return_value={"record": READY}), \
             patch("foreman.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE}, clear=False), \
             patch("foreman.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            import os as _os
            _os.environ.pop("HERDR_ENV", None)
            code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 1)
        self.assertIn("outside Herdr", err)

    def test_the_log_safe_warning_sink_withholds_the_message(self):
        from foreman.cli import _log_safe
        seen = []
        _log_safe(seen.append)("composer read: token=secret $ export KEY=x")
        self.assertEqual(len(seen), 1)
        self.assertNotIn("secret", seen[0])

    def test_any_herdr_marker_value_counts_as_inside_herdr(self):
        # rules/agent-team-operation.md Two Modes: HERDR_ENV set, any value, is a team round.
        for marker in ("", "0", "fixture"):
            with self.subTest(marker=marker), \
                 patch("foreman.cli.memory.show", return_value={"record": READY}), \
                 patch("foreman.cli.supervision.load", return_value=supervision_data()[0]), \
                 patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": marker}), \
                 patch("foreman.cli._spawn_detached", side_effect=lambda argv, sink: 4242), \
                 patch("foreman.foreman_reset.process_identity", side_effect=lambda pid: {"pid": pid, "identity": "c"}):
                foreman_reset.record_path(self.state).unlink(missing_ok=True)
                code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
                self.assertEqual(code, 0, err)

    def test_a_planted_log_link_is_refused_and_nothing_follows_it(self):
        target = self.tmp / "elsewhere.txt"
        log = Path(str(self.state.resolve()) + ".foreman-reset.log")
        log.symlink_to(target)
        with patch("foreman.cli.memory.show", return_value={"record": READY}), \
             patch("foreman.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1"}), \
             patch("foreman.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            code, _, _err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 1)
        self.assertFalse(target.exists())

    def test_an_absent_herdr_marker_is_outside_herdr(self):
        for marker in (None,):
            with self.subTest(marker=marker), \
                 patch("foreman.cli.memory.show", return_value={"record": READY}), \
                 patch("foreman.cli.supervision.load", return_value=supervision_data()[0]), \
                 patch.dict("os.environ", {"HERDR_PANE_ID": PANE}), \
                 patch("foreman.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
                os.environ.pop("HERDR_ENV", None)
                code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
                self.assertEqual(code, 1)
                self.assertIn("outside Herdr", err)

    def test_a_failure_record_keeps_identifiers_only(self):
        from foreman.errors import HerdrError as Raw
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
                               options={"config": "/c/cfg.json", "herdr_bin": "/opt/herdr"}, native_session=SESSION)
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
        foreman_reset.schedule(self.state, {"pane_id": PANE, "stow": "round-7"}, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
        with patch("foreman.cli.supervision_runtime.process_identity", side_effect=StateError("ps timed out", {})):
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
        foreman_reset.schedule(self.state, plan, "2026-09-24T10:00:00+00:00", os.getpid, native_session=SESSION)
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
        with patch("foreman.cli.memory.show", return_value={"record": {"id": "s", "kind": "stow", "reset_ready": False}}), \
             patch("foreman.cli.supervision.load", return_value=supervision_data()[0]), \
             patch.dict("os.environ", {"HERDR_PANE_ID": PANE, "HERDR_ENV": "1"}), \
             patch("foreman.cli._spawn_detached", side_effect=AssertionError("must not spawn")):
            self.out, self.err = io.StringIO(), io.StringIO()
            code, _, err = self.run_cli(self.base() + ["foreman-reset", "--now", RESET_AT])
        self.assertEqual(code, 1)
        self.assertIn("not reset-ready", err)


# The fixture uses the real detached process, identity probe, record lock and
# claim. Only native pane input is replaced with a controlled journal. No
# default Herdr session or production owner state is reachable.
STARTUP_CHILD = r'''
import json, os, sys, time
from pathlib import Path
from foreman import foreman_reset, supervision_runtime
from foreman.state import state_lock
state, mode, release, journal = sys.argv[1:5]
fd = int(sys.argv[-1])
plan = {"pane_id": "w9:p1", "stow": "round-7"}
process = supervision_runtime.process_identity(os.getpid())
if mode == "before_ready":
    raise SystemExit(7)
if mode == "wrong_identity":
    foreman_reset.startup_notify(fd, "ready", {**process, "identity": "0" * 64})
else:
    foreman_reset.startup_notify(fd, "ready", process)
if mode == "before_claim":
    with foreman_reset._waiting_lock(foreman_reset.record_path(state)):
        raise SystemExit(7)
if mode == "false_claim":
    foreman_reset.startup_notify(fd, "claimed", process)
else:
    claimed = foreman_reset.claim(state, plan, process)
    if not claimed:
        foreman_reset.startup_notify(fd, "unclaimed", process)
        raise SystemExit(9)
    if mode == "claimed_failure":
        foreman_reset.finish(state, plan, "failed", {
            "error": "state_error", "message": "fixture failure after claim", "details": {},
            "resume_prompt": foreman_reset.resume_prompt("round-7", state)})
    foreman_reset.startup_notify(fd, "claimed", process)
    if mode == "claimed_failure":
        raise SystemExit(7)
for _ in range(300):
    if Path(release).exists():
        with Path(journal).open("a") as handle:
            handle.write("clear\nresume\n")
        foreman_reset.finish(state, plan, "delivered", {
            "schema_version": foreman_reset.RESET_SCHEMA_VERSION, **plan, "agent": "fixture-foreman", "cleared": True,
            "resume": {"landed": True, "started": True}})
        raise SystemExit(0)
    time.sleep(0.02)
raise SystemExit(8)
'''


class DetachedStartupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reset-startup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "owner state.json"
        self.release = self.root / "release"
        self.journal = self.root / "input"
        self.log = self.root / "reset.log"
        self.child_script = self.root / "child.py"
        self.child_script.write_text(STARTUP_CHILD, encoding="utf-8")
        self.children = []
        self.addCleanup(self.reap)
        self.package = str(Path(__file__).resolve().parents[1])
        self.plan = {"pane_id": PANE, "stow": "round-7"}

    def reap(self):
        # These are Popen-owned fixture children, never native pane processes.
        for child in self.children:
            if child.child.poll() is None:
                child.child.terminate()
            child.child.wait(timeout=5)
            child.close()

    def start(self, mode):
        with self.log.open("ab") as sink:
            child = foreman_reset.DetachedReset(
                [sys.executable, str(self.child_script), str(self.state), mode, str(self.release), str(self.journal)],
                sink, self.package)
        self.children.append(child)
        return child

    def schedule(self, modes):
        choices = iter(modes)
        with patch.dict(os.environ, {"PYTHONPATH": self.package}):
            return foreman_reset.schedule(self.state, self.plan, RESET_AT, lambda: self.start(next(choices)),
                                          native_session=SESSION)

    def finish_once(self):
        self.release.write_text("release", encoding="utf-8")
        for child in self.children:
            child.child.wait(timeout=5)
        self.assertEqual(self.journal.read_text(), "clear\nresume\n")
        document = json.loads(foreman_reset.record_path(self.state).read_text())
        self.assertEqual(len(document["resets"]), 1)
        row = document["resets"][0]
        self.assertEqual((row["status"], row["native_session"], row["scheduled_at"]), ("delivered", SESSION, RESET_AT))

    def test_loaded_identity_and_durable_claim_precede_success(self):
        row = self.schedule(["healthy"])
        self.assertEqual(row["status"], "delivering")
        self.assertEqual(row["process"], supervision_runtime.process_identity(self.children[0].child.pid))
        self.assertFalse(self.journal.exists())
        self.finish_once()

    def test_preclaim_loss_recovers_once_without_duplicate_input(self):
        for mode in ("before_ready", "before_claim", "wrong_identity", "false_claim"):
            with self.subTest(mode=mode):
                # Each subcase gets independent owner records and fixture control.
                foreman_reset.record_path(self.state).unlink(missing_ok=True)
                self.release.unlink(missing_ok=True)
                self.journal.unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                before = len(self.children)
                row = self.schedule([mode, "healthy"])
                launched = self.children[before:]
                self.assertEqual(len(launched), 2)
                self.assertIsNotNone(launched[0].child.poll())
                self.assertEqual(row["process"]["pid"], launched[1].child.pid)
                self.assertFalse(self.journal.exists())
                losses = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertEqual(len(losses), 1)
                self.assertEqual((losses[0]["startup_attempt"], losses[0]["pid"], losses[0]["phase"]),
                                 (1, launched[0].child.pid, "preclaim"))
                self.finish_once()

    def test_exhausted_preclaim_recovery_preserves_a_terminal_no_input_receipt(self):
        with self.assertRaises(foreman_reset.ResetEnded):
            self.schedule(["before_claim", "before_claim"])
        self.assertEqual(len(self.children), foreman_reset.STARTUP_ATTEMPTS)
        self.assertFalse(self.journal.exists())
        row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][0]
        self.assertEqual((row["status"], row["process"], row["result"]["error"]), ("failed", None, "reset_startup_failed"))
        self.assertIn("memory-show", row["result"]["resume_prompt"])
        self.assertEqual(len(self.log.read_text().splitlines()), 2)
        with self.assertRaises(foreman_reset.ResetEnded):
            self.schedule(["healthy"])
        self.assertEqual(len(self.children), foreman_reset.STARTUP_ATTEMPTS)

    def test_a_claimed_failure_never_starts_another_child(self):
        with self.assertRaises(foreman_reset.ResetEnded):
            self.schedule(["claimed_failure", "healthy"])
        self.assertEqual(len(self.children), 1)
        self.assertFalse(self.journal.exists())
        row = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][0]
        self.assertEqual((row["status"], row["result"]["message"]), ("failed", "fixture failure after claim"))

    def test_continuation_survives_the_scheduling_parent_process(self):
        parent_script = self.root / "parent.py"
        parent_script.write_text(
            "import json, sys\nfrom pathlib import Path\nfrom foreman import foreman_reset\n"
            "state, child, release, journal, log, package = sys.argv[1:]\n"
            "with Path(log).open('ab') as sink:\n"
            "    row = foreman_reset.schedule(state, {'pane_id':'w9:p1','stow':'round-7'}, " + repr(RESET_AT) + ",\n"
            "        lambda: foreman_reset.DetachedReset([sys.executable, child, state, 'healthy', release, journal], sink, package),\n"
            "        native_session=" + repr(SESSION) + ")\n"
            "print(json.dumps(row))\n", encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": self.package}
        result = subprocess.run([sys.executable, str(parent_script), str(self.state), str(self.child_script),
                                 str(self.release), str(self.journal), str(self.log), self.package],
                                env=env, capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["status"], "delivering")
        self.assertEqual(supervision_runtime.process_identity(row["process"]["pid"]), row["process"])
        self.assertFalse(self.journal.exists())
        # The scheduling tool/process is gone; release the same detached child.
        self.release.write_text("release", encoding="utf-8")
        final = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][0]
        for _ in range(100):
            final = json.loads(foreman_reset.record_path(self.state).read_text())["resets"][0]
            if final["status"] == "delivered":
                break
            import time
            time.sleep(0.02)
        self.assertEqual(final["status"], "delivered")
        self.assertEqual(self.journal.read_text(), "clear\nresume\n")


if __name__ == "__main__":
    unittest.main()
