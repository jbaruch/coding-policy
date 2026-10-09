"""Native model/account errors are terminal facts, never assistant quotations."""

import copy
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foreman import model_unavailability as unavailable
from foreman import cli, recovery, report_delivery
from foreman.assign import assignment_text, tiered_prompt
from foreman.config import Agent
from foreman.errors import UsageError
from foreman.tiers import parse_tiers
from foreman.herdr import HerdrClient
from tests.fakes import FakeRunner, ScriptedReads
from tests.test_cli import CliCase, FreshOwnerNative
from tests import test_recovery as recovery_fixture
from foreman import assign, composer, lifecycle, supervision


SESSION = "native-session-724"
PROMPT = "Read the original common and brief; write the assigned report."
MODEL = "gpt-5.3-codex-spark"
ERROR = {"type": "error", "status": 400, "error": {
    "type": "invalid_request_error",
    "message": "The '{}' model is not supported when using Codex with a ChatGPT account.".format(MODEL)}}


def codex_rows():
    return [
        {"type": "session_meta", "payload": {"id": SESSION, "cli_version": "0.160.1",
            "originator": "codex-tui", "model_provider": "openai"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-724"}},
        {"type": "turn_context", "payload": {"turn_id": "turn-724", "model": MODEL, "effort": "low"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-724", "content_item_kinds": ["user.text"]},
            "content": [{"type": "input_text", "text": PROMPT}]}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-724",
            "last_agent_message": None, "error": {"message": json.dumps(ERROR), "codex_error_info": "other"}}},
    ]


def claude_rows():
    return [
        {"type": "user", "sessionId": SESSION, "version": "2.1.295", "uuid": "user-724",
            "parentUuid": None, "promptSource": "typed", "origin": {"kind": "human"},
            "message": {"role": "user", "content": PROMPT}},
        {"type": "assistant", "sessionId": SESSION, "version": "2.1.295", "uuid": "error-724",
            "parentUuid": "user-724", "isApiErrorMessage": True, "error": "model_not_found",
            "message": {"id": "message-724", "role": "assistant", "model": "<synthetic>",
                "stop_reason": "stop_sequence", "usage": {"input_tokens": 0, "output_tokens": 0,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                "content": [{"type": "text", "text": "There's an issue with the selected model (opus-5). "
                    "It may not exist or you may not have access to it. Run /model to pick a different model."}]}},
    ]


class SourceErrorTests(unittest.TestCase):
    def result(self, rows, kind="codex", session=SESSION):
        body = "\n".join(json.dumps(row) for row in rows)
        return unavailable.source_error(body, kind, session)

    def test_codex_completed_structured_account_failure(self):
        result = self.result(codex_rows())
        assert result is not None
        self.assertEqual(result["model"], MODEL)
        self.assertEqual(result["code"], "model_account_unsupported")
        self.assertEqual(result["account_scope"], "chatgpt")
        self.assertEqual(result["cli_version"], "0.160.1")
        self.assertEqual(result["prompt_sha256"], hashlib.sha256(PROMPT.encode()).hexdigest())
        self.assertNotIn("message", result)
        self.assertNotIn("prompt", result)

    def test_claude_native_synthetic_zero_token_error(self):
        result = self.result(claude_rows(), "claude")
        assert result is not None
        self.assertEqual((result["model"], result["code"], result["account_scope"]),
                         ("opus-5", "model_not_found", "unknown"))
        self.assertEqual(result["cli_version"], "2.1.295")
        self.assertEqual(result["prompt_sha256"], hashlib.sha256(PROMPT.encode()).hexdigest())

    def test_codex_runtime_instructions_and_world_snapshot_are_not_prompts(self):
        rows = codex_rows()
        context = copy.deepcopy(rows[3])
        context["payload"]["internal_chat_message_metadata_passthrough"]["content_item_kinds"] = [
            "agents_md.instructions", "environments.environment_context"]
        context["payload"]["content"][0]["text"] = "native environment, not the assignment"
        developer = copy.deepcopy(context)
        developer["payload"]["role"] = "developer"
        developer["payload"]["internal_chat_message_metadata_passthrough"]["content_item_kinds"] = ["hooks.additional_context"]
        rows[2:2] = [developer, context, {"type": "world_state", "payload": {"full": True, "state": {}}}]
        result = self.result(rows)
        assert result is not None
        self.assertEqual(result["prompt_sha256"], hashlib.sha256(PROMPT.encode()).hexdigest())

    def test_malformed_native_versions_are_unknown_not_parser_crashes(self):
        for value in (None, [], {}, 160):
            rows = codex_rows()
            rows[0]["payload"]["cli_version"] = value
            self.assertIsNone(self.result(rows))
            rows = claude_rows()
            rows[-1]["version"] = value
            self.assertIsNone(self.result(rows, "claude"))

    def test_codex_negative_source_cases_stay_unconfirmed(self):
        cases = []
        for row, key, value in ((0, "id", "other-session"), (0, "model_provider", "gateway"),
                                (0, "cli_version", "unknown"), (2, "model", "other-model"),
                                (4, "turn_id", "other-turn"), (4, "last_agent_message", "quoted error")):
            rows = codex_rows()
            rows[row]["payload"][key] = value
            cases.append(rows)
        for error in ({"message": "quoted text"}, {"message": json.dumps({**ERROR, "status": 429})},
                      {"message": json.dumps({**ERROR, "error": {"type": "server_error", "message": "unavailable"}})}):
            rows = codex_rows()
            rows[-1]["payload"]["error"] = error
            cases.append(rows)
        cases.append(codex_rows()[:-1])
        cases.append(codex_rows() + [{"type": "event_msg", "payload": {"type": "task_started", "turn_id": "new-turn"}}])
        cases.append(codex_rows() + [{"type": "response_item", "payload": {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": json.dumps(ERROR)}]}}])
        for item_type in ("AgentMessage", "ToolCall", "unknown"):
            cases.append(codex_rows() + [{"type": "event_msg", "payload": {
                "type": "item_completed", "thread_id": SESSION, "turn_id": "turn-724",
                "item": {"type": item_type}}}])
        cases.append(codex_rows() + [{"type": "event_msg", "payload": {"type": "unknown"}}])
        cases.append(codex_rows() + [copy.deepcopy(codex_rows()[2])])
        cases.append(codex_rows() + [copy.deepcopy(codex_rows()[-1])])
        cases.append(codex_rows() + [{"type": "event_msg", "payload": {"type": "user_message", "message": PROMPT}}])
        rows = codex_rows()
        rows[3]["payload"]["internal_chat_message_metadata_passthrough"]["content_item_kinds"] = ["environments.environment_context"]
        cases.append(rows)
        for rows in cases:
            with self.subTest(rows=rows):
                self.assertIsNone(self.result(rows))

    def test_claude_negative_source_cases_stay_unconfirmed(self):
        cases = []
        for key, value in (("isApiErrorMessage", False), ("error", "overloaded"),
                           ("sessionId", "other-session"), ("parentUuid", "abandoned"),
                           ("isSidechain", True), ("version", "unknown")):
            rows = claude_rows()
            rows[-1][key] = value
            cases.append(rows)
        for key, value in (("model", "opus-5"), ("stop_reason", None),
                           ("content", [{"type": "tool_use", "id": "tool-724"}])):
            rows = claude_rows()
            rows[-1]["message"][key] = value
            cases.append(rows)
        rows = claude_rows()
        rows[-1]["message"]["usage"]["output_tokens"] = 1
        cases.append(rows)
        rows = claude_rows()
        later = copy.deepcopy(rows[0])
        later.update(uuid="later-user", parentUuid="error-724")
        cases.append(rows + [later])
        for rows in cases:
            with self.subTest(rows=rows):
                self.assertIsNone(self.result(rows, "claude"))

    def test_malformed_or_unverified_provider_source_is_unknown(self):
        for body in ("not JSON", "{}", "[]", '{"type": "session_meta"}\nnull'):
            self.assertIsNone(unavailable.source_error(body, "codex", SESSION))
        self.assertIsNone(self.result(codex_rows(), "grok"))


class LiveErrorProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "missing-report.md"
        self.source = self.root / "native.jsonl"
        self.visible = "native error above the empty composer\n› Ask Codex to do anything"
        self.ansi = "native error above the empty composer\n› \x1b[2mAsk Codex to do anything\x1b[0m"
        self.pane = {"pane_id": "w1:p1", "agent_status": "done", "terminal_id": "terminal-724",
            "revision": 8, "scroll": {"offset_from_bottom": 0}, "agent_session": {
                "source": "herdr:codex", "agent": "codex", "kind": "id", "value": SESSION}}
        self.rows = codex_rows()

    def run_probe(self, after=None, public=False):
        self.source.write_text("\n".join(json.dumps(row) for row in self.rows))
        runner = FakeRunner()
        runner.set("agent get worker", json.dumps({"result": {"agent": self.pane}}))
        runner.set("pane get w1:p1", json.dumps({"result": {"pane": self.pane}}))
        if after is not None:
            runner.responses["pane get w1:p1"] = ScriptedReads([
                json.dumps({"result": {"pane": self.pane}}), json.dumps({"result": {"pane": after}})])
        runner.set("pane read w1:p1", self.visible)
        runner.set("agent read worker", self.ansi)
        with patch.object(report_delivery, "source_path", return_value=self.source):
            if public:
                output, errors = io.StringIO(), io.StringIO()
                with patch.object(sys, "stdin", io.StringIO(self.visible)), patch.object(unavailable, "CONFIRM_SECONDS", 0), \
                        patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "empty-state"),
                                                "XDG_CONFIG_HOME": str(self.root / "empty-config")}):
                    code = cli.main(["probe-unavailable", "--agent", "worker", "--pane", "w1:p1",
                                     "--report", str(self.report), "--lines", "40"],
                                    stdout=output, stderr=errors, client=HerdrClient(runner=runner))
                self.assertEqual(code, 0, errors.getvalue())
                self.assertFalse((self.root / "empty-state").exists())
                self.assertFalse((self.root / "empty-config").exists())
                result = json.loads(output.getvalue())
            else:
                result = unavailable.probe(HerdrClient(runner=runner), "worker", "w1:p1",
                                           str(self.report), self.visible, 40, sleep=lambda _: None)
        self.assertEqual(runner.writes(), [])
        return result

    def test_complete_live_error_yields_source_bound_unavailability_not_delivery(self):
        result = self.run_probe()
        self.assertTrue(result["confirmed"])
        proof = result["unavailability"]
        self.assertEqual(proof["schema_version"], 1)
        self.assertEqual(proof["native_session"], self.pane["agent_session"])
        self.assertEqual((proof["model"], proof["code"]), (MODEL, "model_account_unsupported"))
        self.assertEqual(proof["source"]["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertNotIn("found", result)

    def test_public_probe_is_read_only_and_needs_no_foreman_home(self):
        self.assertTrue(self.run_probe(public=True)["confirmed"])

    def test_claude_native_error_uses_the_same_live_identity_contract(self):
        self.rows = claude_rows()
        self.pane["agent_session"].update(agent="claude", source="herdr:claude")
        self.visible = self.ansi = "native error\n❯ "
        self.assertEqual(self.run_probe()["unavailability"]["model"], "opus-5")

    def test_working_scrolled_changed_or_replaced_panes_do_not_confirm(self):
        for field, value in (("agent_status", "working"), ("revision", 9), ("terminal_id", "other-terminal"),
                             ("agent_session", {**self.pane["agent_session"], "value": "other-session"}),
                             ("scroll", {"offset_from_bottom": 1}), ("pane_id", "w2:p1")):
            with self.subTest(field=field):
                after = {**self.pane, field: value}
                self.assertFalse(self.run_probe(after)["confirmed"])

    def test_occupied_unstyled_or_missing_composer_is_not_terminal_authority(self):
        for ansi in ("native error\n› somebody's draft", "native error\n› Ask Codex to do anything", "native error"):
            with self.subTest(ansi=ansi):
                self.ansi = ansi
                self.assertFalse(self.run_probe()["confirmed"])

    def test_present_report_is_not_unavailability(self):
        self.report.write_text("report bytes")
        self.assertFalse(self.run_probe()["confirmed"])

    def test_missing_oversize_or_non_utf8_source_never_supplies_proof(self):
        self.assertIsNone(unavailable._source(self.source))
        self.source.write_bytes(b"x" * 10)
        with patch.object(unavailable, "SOURCE_MAX_BYTES", 8):
            self.assertIsNone(unavailable._source(self.source))
        self.source.write_bytes(b"\xff")
        with self.assertRaisesRegex(UsageError, "Restore its readable UTF-8"):
            unavailable._source(self.source)


class RefusalSourceBindingTests(LiveErrorProofTests):
    def setUp(self):
        super().setUp()
        self.common, self.brief = self.root / "COMMON.md", self.root / "brief.md"
        self.common.write_text("Read the unchanged task contract.\n")
        self.brief.write_text("Do the assigned work.\nREPORT: " + str(self.report) + "\n")
        tier = {"model": MODEL, "effort": "low"}
        prompt, tier["prompt_hash"] = tiered_prompt(
            assignment_text("tester", str(self.common), str(self.brief)), tier, str(self.common), str(self.brief))
        self.rows[3]["payload"]["content"][0]["text"] = prompt
        self.store = recovery.empty_recovery()
        self.dispatch = {"id": "dispatch-724", "task": "task-724", "agent": "worker", "role": "tester",
            "status": "applied", "provider": "codex", "common": str(self.common), "brief": str(self.brief),
            "result": {"tier": tier}, "launch_scope": {"schema_version": 1, "kind": "codex",
                "window_group": "fixture-chatgpt-account", "worker_kind": "codex-template"}}
        self.store["dispatches"].append(self.dispatch)
        self.binding = {"pane_id": "w1:p1", "native_session": self.pane["agent_session"]}

    def record(self, mutate=None, binding=None):
        payload = {"agent": "worker", "state": "done", "report_path": str(self.report), "found": False,
            "elapsed_seconds": 5, "reason": "terminal_provider_refusal", "unavailability": self.run_probe()["unavailability"]}
        if mutate:
            mutate(payload)
        receipt = self.root / "wait.json"
        receipt.write_text(json.dumps(payload))
        with patch.object(report_delivery, "source_path", return_value=self.source):
            return recovery.record_refusal(self.store, {"dispatch": "dispatch-724", "receipt": str(receipt)},
                "2026-10-08T00:00:00Z", "codex", str(self.report), binding=self.binding if binding is None else binding)

    def test_record_refusal_verifies_original_source_model_prompt_and_scope(self):
        result = self.record()
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["launch_scope"], self.dispatch["launch_scope"])
        self.assertEqual(result["unavailability"]["model"], MODEL)
        self.assertIsNone(self.dispatch.get("report"))
        self.assertEqual(self.record(), result)

    def test_wrong_source_prompt_model_native_session_or_pane_never_records(self):
        cases = [lambda payload: payload["unavailability"].update(model="other-model"),
            lambda payload: payload["unavailability"].update(prompt_sha256="a" * 64),
            lambda payload: payload["unavailability"]["source"].update(sha256="b" * 64),
            lambda payload: payload["unavailability"].update(pane_id="w2:p1"),
            lambda payload: payload["unavailability"]["native_session"].update(value="other-session")]
        for mutate in cases:
            with self.subTest(mutate=mutate), self.assertRaises(UsageError):
                self.record(mutate)
            self.assertNotIn("refusal", self.dispatch)

    def test_legacy_dispatch_does_not_acquire_todays_account_scope(self):
        self.dispatch.pop("launch_scope")
        self.assertIsNone(self.record()["launch_scope"])

    def test_refusal_is_model_and_configured_account_scoped_not_provider_banned(self):
        self.record()
        agent = Agent("codex-template", "codex", "/status", "Weekly limit", "visible", "/new",
                      window_group="fixture-chatgpt-account")
        with self.assertRaisesRegex(UsageError, "model.*unavailable"):
            unavailable.require_available(self.store, agent, MODEL, "2026-10-08T00:00:01Z")
        unavailable.require_available(self.store, agent, "gpt-6-astra", "2026-10-08T00:00:01Z")
        agent.window_group = "another-account"
        unavailable.require_available(self.store, agent, MODEL, "2026-10-08T00:00:01Z")
        agent.kind = "claude"
        unavailable.require_available(self.store, agent, MODEL, "2026-10-08T00:00:01Z")

    def test_planner_skips_only_the_unavailable_model_account_candidate(self):
        self.record()
        first = Agent("codex-template", "codex", "/status", "Weekly limit", "visible", "/new",
            window_group="fixture-chatgpt-account", tiers=parse_tiers({"build": {"model": MODEL, "effort": "low"}}, "codex"))
        other = Agent("other", "codex", "/status", "Weekly limit", "visible", "/new",
            window_group="another-account", tiers=first.tiers)
        refusals = []
        candidates = cli._candidate_tiers(["developer"], [first, other], {},
            refusals=refusals, at="2026-10-08T00:00:01Z", unavailable_store=self.store)
        self.assertNotIn(first.name, candidates["developer"])
        self.assertIn(other.name, candidates["developer"])
        self.assertEqual(refusals[0]["model_unavailability"]["dispatch"], "dispatch-724")

    def test_ungrouped_failure_binds_only_its_template_and_expires_without_access_proof(self):
        self.dispatch["launch_scope"]["window_group"] = ""
        self.record()
        agent = Agent("codex-template", "codex", "/status", "Weekly limit", "visible", "/new")
        self.assertIsNotNone(unavailable.exclusion(self.store, agent, MODEL, "2026-10-08T00:00:01Z"))
        agent.name = "other-template"
        self.assertIsNone(unavailable.exclusion(self.store, agent, MODEL, "2026-10-08T00:00:01Z"))
        agent.name = "codex-template"
        self.assertIsNone(unavailable.exclusion(self.store, agent, MODEL, "2026-10-07T23:59:59Z"))
        expired = (datetime.fromisoformat("2026-10-08T00:00:00+00:00") + unavailable.INTERVAL).isoformat()
        self.assertIsNone(unavailable.exclusion(self.store, agent, MODEL, expired))


class PublicWaitErrorTests(unittest.TestCase):
    """Real shell/Python entrypoints, representative pinned native CLI logs."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "missing-report.md"
        self.fixture = self.root / "pane.json"
        self.calls = self.root / "calls.jsonl"

    def wait(self, kind, rows, *, occupied=False, error_on=None):
        native = {"source": "herdr:" + kind, "agent": kind, "kind": "id", "value": SESSION}
        pane = {"pane_id": "w1:p1", "agent_status": "done", "agent": kind, "name": "worker",
                "agent_session": native, "terminal_id": "terminal-724", "revision": 8,
                "scroll": {"offset_from_bottom": 0}}
        composer = "› \x1b[2mAsk Codex to do anything\x1b[0m" if kind == "codex" else "❯ "
        if occupied:
            composer = ("› " if kind == "codex" else "❯ ") + "an unsent draft"
        self.fixture.write_text(json.dumps({"pane": pane, "visible": "native error\n" + composer,
                                            "ansi": "native error\n" + composer, "error_on": error_on}))
        source_root = self.root / kind / ("sessions/2026/10/08" if kind == "codex" else "projects/fixture-project")
        source_root.mkdir(parents=True, exist_ok=True)
        source = source_root / ("rollout-fixture-" + SESSION + ".jsonl" if kind == "codex" else SESSION + ".jsonl")
        source.write_text("\n".join(json.dumps(row) for row in rows))
        env = {**os.environ, "HERDR_ENV": "1", "HERDR_BIN": str(ROOT / "tests/fixtures/native_error_herdr.py"),
            "CP724_PANE": str(self.fixture), "CP724_CALLS": str(self.calls),
            "CODEX_HOME": str(self.root / "codex"), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
            "XDG_STATE_HOME": str(self.root / "empty-state"), "XDG_CONFIG_HOME": str(self.root / "empty-config"),
            "FOREMAN_REFUSAL_CONFIRM_SEC": "0", "FOREMAN_WAIT_BUDGET_SEC": "5400"}
        result = subprocess.run(["bash", str(ROOT / "wait-report.sh"), "--once", "worker", str(self.report)],
                                capture_output=True, text=True, env=env, timeout=20)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertFalse(any(call[:2] in (["agent", "prompt"], ["pane", "send-keys"], ["pane", "send-text"],
                                         ["agent", "start"], ["agent", "close"]) for call in calls))
        return result, source

    def test_codex_and_claude_errors_exit_five_without_waiting_for_round_budget(self):
        for kind, rows in (("codex", codex_rows()), ("claude", claude_rows())):
            with self.subTest(kind=kind):
                result, source = self.wait(kind, rows)
                self.assertEqual(result.returncode, 5, result.stderr)
                receipt = json.loads(result.stdout)
                self.assertFalse(receipt["found"])
                self.assertEqual(receipt["reason"], "terminal_provider_refusal")
                self.assertEqual(receipt["unavailability"]["source"]["path"], str(source))
                self.assertEqual(receipt["unavailability"]["native_session"]["value"], SESSION)
                self.assertFalse(self.report.exists())

    def test_unknown_source_version_or_draft_keeps_checkpoint_pending(self):
        rows = codex_rows()
        rows[0]["payload"]["cli_version"] = "unknown"
        for case, occupied in ((rows, False), (codex_rows(), True)):
            result, _ = self.wait("codex", case, occupied=occupied)
            self.assertEqual(result.returncode, 1, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["reason"], "checkpoint_pending")
            self.assertNotIn("unavailability", receipt)

    def test_native_probe_tool_fault_is_exit_two_not_pending_or_refusal(self):
        result, _ = self.wait("codex", codex_rows(), error_on="agent read")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("verification failed", result.stderr)


class PublicOwnerModelErrorTests(CliCase):
    def test_native_failure_records_and_moves_unchanged_brief_once(self):
        at, task = "2026-10-08T00:00:00Z", "model-error-724"
        config = json.loads((ROOT / "config.example.json").read_text())
        codex = next(worker for worker in config["worker_kinds"] if worker["name"] == "codex")
        codex["tiers"]["build"] = {"model": MODEL, "effort": "low"}
        self.config.write_text(json.dumps(config))
        snapshot = json.loads(self.snapshot.read_text())
        snapshot["measured_at"] = at
        self.snapshot.write_text(json.dumps(snapshot))
        def invoke(args, native=None):
            self.out, self.err = io.StringIO(), io.StringIO()
            return self.run_cli(self.base() + args, client=native)
        record = self.tmp / "task.json"
        record.write_text(json.dumps({"task": task, "base_revision": "a" * 40, "scope": "Native failure workflow",
            "allowed_paths": ["src/*"], "authorization": {"source": "fixture operator", "quote": "Implement this task."}}))
        code, _, error = invoke(["task", "--record", str(record), "--now", at])
        self.assertEqual(code, 0, error)
        supervision.bind(self.state, {"kind": "id", "value": "fixture-foreman", "cwd": str(self.tmp),
            "herdr_env": "fixture", "pane_id": "foreman-pane"}, at, root=self.tmp / "bindings")
        def plan(excluded, planned_task=task):
            code, output, error = invoke(["plan", "--roles", "developer", "--task", planned_task,
                "--snapshot", str(self.snapshot), "--exclude", "developer=" + excluded, "--now", at])
            return code, json.loads(output) if output else None, error
        with patch("foreman.lifecycle.identity", return_value="developer-codex-724"):
            code, original, error = plan("claude,grok")
        self.assertEqual(code, 0, error)
        native = FreshOwnerNative()
        native.frames = [native.EMPTY]
        report = self.tmp / "original-report.md"
        self.assign_report(self.briefs["developer"], report)
        def apply(plan, expected_report):
            return invoke(["apply", "--assignments", json.dumps(plan), "--task", task,
                "--common", str(self.common), "--brief", "developer=" + str(self.briefs["developer"]),
                "--report", "developer=" + str(expected_report), "--now", at, "--composer-settle", "0"], native)
        real_spawn, real_apply = lifecycle.spawn, assign.apply
        with patch("foreman.cli.lifecycle.spawn", side_effect=lambda *a, **kw: real_spawn(*a, **kw, sleep=lambda _: None)), \
                patch("foreman.cli.apply_assignments", side_effect=lambda *a, **kw: real_apply(*a, **kw, sleep=lambda _: None)):
            code, output, error = apply(original, report)
        self.assertEqual(code, 0, error)
        dispatched = json.loads(output)["applied"][0]
        original_prompt = native.sent_text
        worker = native.agents[dispatched["agent"]]
        worker["agent_status"] = "done"
        source = self.tmp / "native.jsonl"
        rows = codex_rows()
        rows[0]["payload"]["id"] = worker["agent_session"]["value"]
        rows[3]["payload"]["content"][0]["text"] = original_prompt
        source.write_text("\n".join(json.dumps(row) for row in rows))
        viewport = "native error\n" + native.EMPTY
        native.agent_read = lambda *_a, **_kw: viewport
        native.pane_read = lambda *_a, **_kw: viewport
        native.pane_get = lambda pane_id: {**worker, "terminal_id": "terminal-724", "revision": 8,
                                       "scroll": {"offset_from_bottom": 0}}
        before = list(native.events)
        with patch.object(report_delivery, "source_path", return_value=source):
            proof = unavailable.probe(native, dispatched["agent"], dispatched["pane_id"], str(report), viewport, 40,
                                      sleep=lambda _: None)
            self.assertTrue(proof["confirmed"])
            receipt = self.tmp / "wait.json"
            receipt.write_text(json.dumps({"agent": dispatched["agent"], "state": "done", "report_path": str(report),
                "found": False, "elapsed_seconds": 5, "reason": "terminal_provider_refusal",
                "unavailability": proof["unavailability"]}))
            record.write_text(json.dumps({"dispatch": dispatched["dispatch_id"], "receipt": str(receipt)}))
            code, _, error = invoke(["record-refusal", "--record", str(record), "--now", at])
            self.assertEqual(code, 0, error)
        self.assertFalse(any(event[0] in {"prompt", "create", "start", "close", "send_keys", "send_text"}
                             for event in native.events[len(before):]))
        code, _, error = plan("claude,grok", "other-task")
        self.assertEqual(code, 1)
        self.assertIn("unavailable", error)
        with patch("foreman.lifecycle.identity", return_value="developer-claude-724"):
            code, moved, error = plan("codex,grok")
        self.assertEqual(code, 0, error)
        fresh_report = self.tmp / "replacement-report.md"
        at = "2026-10-08T00:00:01Z"
        self.briefs["developer"].write_text(self.briefs["developer"].read_text().replace(str(report), str(fresh_report)))
        native = FreshOwnerNative()
        native.EMPTY, native.frames = "❯ ", ["❯ "]
        with patch("foreman.cli.lifecycle.spawn", side_effect=lambda *a, **kw: real_spawn(*a, **kw, sleep=lambda _: None)), \
                patch("foreman.cli.apply_assignments", side_effect=lambda *a, **kw: real_apply(*a, **kw, sleep=lambda _: None)):
            code, output, error = apply(moved, fresh_report)
        self.assertEqual(code, 0, error)
        saved = json.loads(self.state.read_text())
        self.assertEqual(json.loads(output)["applied"][0]["status"], "applied", output)
        self.assertEqual(len(saved["assignments"]), 2, json.dumps(saved["assignments"], indent=2))
        move = saved["recovery"]["dispatches"][-1]["refusal_move"]
        self.assertEqual(move["from"], dispatched["dispatch_id"])
        self.assertEqual((move["from_provider"], move["provider"]), ("codex", "claude"))
        self.assertEqual(len([event for event in native.events if event[0] == "prompt"]), 1)
        self.assertFalse(report.exists())
        self.assertFalse(fresh_report.exists())
        recovery.validate_store(copy.deepcopy(saved["recovery"]), saved["assignments"])
        # Shared attempt numbers are not a general duplicate-send exemption.
        for mutation in ("missing-move", "legacy-refusal", "tied-time", "earlier-time", "changed-brief"):
            corrupted = copy.deepcopy(saved)
            previous, replacement = corrupted["recovery"]["dispatches"]
            if mutation == "missing-move":
                replacement.pop("refusal_move")
            elif mutation == "legacy-refusal":
                previous["refusal"]["schema_version"] = 1
                previous["refusal"].pop("launch_scope")
                previous["refusal"].pop("unavailability")
            elif mutation == "changed-brief":
                replacement["brief_identity"] = "another-brief"
            else:
                corrupted["assignments"][1]["at"] = ("2026-10-08T00:00:00Z" if mutation == "tied-time"
                                                        else "2026-10-07T23:59:59Z")
            with self.subTest(mutation=mutation), self.assertRaises(UsageError):
                recovery.validate_store(corrupted["recovery"], corrupted["assignments"])
        # A successful move grants no third initial-development dispatch.
        code, _, error = plan("codex,grok")
        self.assertEqual(code, 1, error)
        self.assertIn("do not reset its counter", error)


class NativeAttemptCounterTests(unittest.TestCase):
    def test_extended_handoff_keeps_original_plan_scope_and_next_review_gate(self):
        case = recovery_fixture.RecoveryTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        plan = case.approve()
        case.finish(6)
        original = case.store["dispatches"][-1]
        original["refusal"] = {"schema_version": 2, "provider": "codex"}
        self.assertEqual(recovery.validate_work(case.store, case.history, recovery_fixture.TASK, 6,
                                              plan["id"], recovery_fixture.WORK), plan)
        wrong_work = {**recovery_fixture.WORK, "paths": ["outside/parser.py"]}
        with self.assertRaisesRegex(UsageError, "paths exceed"):
            recovery.validate_work(case.store, case.history, recovery_fixture.TASK, 6, plan["id"], wrong_work)
        with self.assertRaisesRegex(UsageError, "preceding correction"):
            recovery.validate_work(case.store, case.history, recovery_fixture.TASK, 7, plan["id"], recovery_fixture.WORK)
        # Only the later replacement's genuine blocking review licenses fix 7.
        case.history.append({**case.history[-1], "at": "2026-02-03T11:10:00+00:00"})
        replacement = {**original, "id": "replacement", "assignment_index": len(case.history) - 1,
                       "refusal": None, "report": {"verdict": "blocking", "evidence": {
                           "path": str(case.review), "sha256": hashlib.sha256(case.review.read_bytes()).hexdigest()}}}
        case.store["dispatches"].append(replacement)
        self.assertEqual(recovery.validate_work(case.store, case.history, recovery_fixture.TASK, 7,
                                              plan["id"], recovery_fixture.WORK), plan)

    def test_native_handoff_preserves_initial_early_and_extended_attempt_numbers(self):
        for fix in (None, 1, 5):
            with self.subTest(fix=fix):
                assignments = [{"task": "task", "role": "developer", "status": "applied",
                                "fix_round": fix, "at": "2026-10-08T00:00:00Z"}]
                dispatch = {"id": "original", "task": "task", "role": "developer", "status": "applied",
                            "fix_round": fix, "assignment_index": 0,
                            "refusal": {"schema_version": 2, "provider": "codex"}}
                store = recovery.empty_recovery()
                store["dispatches"] = [dispatch]
                before = copy.deepcopy((store, assignments))
                assign.validate_fix_history({"developer": "replacement"}, assignments, "task", fix, recovery=store)
                self.assertEqual((store, assignments), before)
                assignments.append({**assignments[0], "at": "2026-10-08T00:00:01Z"})
                replacement = {**dispatch, "id": "replacement", "assignment_index": 1, "refusal": None}
                store["dispatches"].append(replacement)
                with self.assertRaisesRegex(UsageError, "do not.*reset"):
                    assign.validate_fix_history({"developer": "third"}, assignments, "task", fix, recovery=store)
                replacement["refusal"] = {"schema_version": 2, "provider": "claude"}
                with self.assertRaisesRegex(UsageError, "independent-refusal limit"):
                    assign.validate_fix_history({"developer": "third"}, assignments, "task", fix, recovery=store)


if __name__ == "__main__":
    unittest.main()
