"""Public measurement/cleanup guards; controlled transport, not native trust."""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from foreman import composer, lifecycle, probe_recovery
from foreman.cli import main
from foreman.config import parse_config
from foreman.errors import HerdrError, StateError, UsageError
from foreman.state import save_state
from tests.test_cli import FreshOwnerNative

AT = "2026-10-08T19:00:00+00:00"


class ProbeRecoveryTest(unittest.TestCase):
    def test_actual_measure_path_refuses_replacement_before_usage_input_for_every_runtime(self):
        for kind in ("claude", "codex", "grok"):
            with self.subTest(kind=kind):
                self.state = self.root / (kind + "-before-input.json")
                worker, client = self.worker(kind), self.native(kind)
                ready = []
                original_prepare = lifecycle._prepare_fresh_probe
                original_read = client.agent_read
                def prepared(*args, **options):
                    proof = original_prepare(*args, **options)
                    assert proof is not None
                    ready.append(proof[2]["pid"])
                    return proof
                def replaced_read(name, source=None, lines=None, fmt=None):
                    text = original_read(name, source=source, lines=lines, fmt=fmt)
                    if ready:
                        client.agents[name]["pid"] = ready[0] + 1
                    return text
                client.agent_read = replaced_read
                with patch("foreman.lifecycle._prepare_fresh_probe", side_effect=prepared):
                    result = lifecycle.measure_worker_kinds(client, [worker], AT,
                        state_path=self.state, sleep=lambda _: None)
                self.assertEqual(result["failed_agents"], [kind])
                self.assertIsNone(result["agents"][kind]["headroom_pct"])
                row = probe_recovery.pending(self.state, worker)
                assert row is not None
                self.assertEqual(row["process"]["pid"], ready[0])
                self.assertEqual(row["phase"], "cleanup")
                self.assertFalse(any(event[0] in {"prompt", "close"} for event in client.events))

    def test_missing_startup_proof_never_closes_a_same_name_replacement(self):
        for kind in ("claude", "codex", "grok"):
            with self.subTest(kind=kind):
                self.state = self.root / (kind + "-missing-startup.json")
                worker, client = self.worker(kind), self.native(kind)
                def replaced_before_proof(_client, probe, _pane, _tier, **_options):
                    client.agents[probe.name]["pid"] += 1
                    raise HerdrError("startup proof unavailable", {})
                with patch("foreman.lifecycle._prepare_fresh_probe", side_effect=replaced_before_proof):
                    result = self.measure(worker, client)
                self.assertEqual(result["failed_agents"], [kind])
                message = result["agents"][kind]["error"]["message"]
                self.assertIn("startup proof unavailable", message)
                self.assertIn("original startup binding is missing", message)
                self.assertIn("process-info", message)
                self.assertNotIn("measure --agent", message)
                self.assertIsNone(probe_recovery.pending(self.state, worker))
                self.assertTrue(client.panes)
                self.assertFalse(any(event[0] in {"prompt", "close"} for event in client.events))

    def test_failed_usage_cleanup_is_retained_and_gates_until_owned_resolution(self):
        for kind in ("claude", "codex", "grok"):
            with self.subTest(kind=kind):
                self.state = self.root / (kind + "-cleanup.json")
                worker, client = self.worker(kind), self.native(kind)
                failure = {"agents": {"unused": {"kind": kind, "headroom_pct": None,
                    "error": {"code": "herdr_error", "message": "original usage failure", "details": {}}}}}
                def failed_measure(_client, workers, _at, **_options):
                    return {**failure, "agents": {workers[0].name: failure["agents"]["unused"]}}
                with patch.object(self, "measured", side_effect=failed_measure), \
                        patch.object(client, "pane_close", side_effect=HerdrError("cleanup failed", {})):
                    result = self.measure(worker, client)
                row = probe_recovery.pending(self.state, worker)
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row["phase"], "cleanup")
                self.assertIn("resolve-probe", result["agents"][kind]["error"]["message"])
                self.assertIn("original usage failure", result["agents"][kind]["error"]["message"])
                before = list(client.events)
                self.assertEqual(self.measure(worker, client)["failed_agents"], [kind])
                self.assertEqual(client.events, before)
                self.assertEqual(self.resolve(row, worker, client)["status"], "closed")
                self.assertEqual(client.panes, {})

    def test_replaced_process_is_preserved_and_the_original_probe_remains_gated(self):
        worker, client = self.worker("codex"), self.native("codex")
        original_pid = []
        def replaced(_client, workers, at, **_options):
            live = client.agents[workers[0].name]
            original_pid.append(live["pid"])
            live["pid"] += 1
            return self.measured(_client, workers, at, **_options)
        with patch("foreman.measure.measure", side_effect=replaced):
            result = lifecycle.measure_worker_kinds(client, [worker], AT,
                state_path=self.state, sleep=lambda _: None)
        row = probe_recovery.pending(self.state, worker)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["process"]["pid"], original_pid[0])
        self.assertEqual(result["failed_agents"], ["codex"])
        self.assertFalse(any(event[0] in {"close", "prompt"} for event in client.events))
        with self.assertRaises(HerdrError):
            self.resolve(row, worker, client)
        self.assertFalse(any(event[0] in {"close", "prompt"} for event in client.events))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state.json"
        self.payload = json.loads((Path(_ROOT) / "config.example.json").read_text())

    def worker(self, kind):
        return next(worker for worker in parse_config(self.payload) if worker.kind == kind)

    def native(self, kind, *, dialog=False):
        client = FreshOwnerNative()
        client.EMPTY = {"codex": client.EMPTY, "claude": "❯ ", "grok": "│ ❯"}[kind]
        client.frames = ["Native startup review" if dialog else client.EMPTY]
        return client

    def measured(self, _client, workers, at, **_options):
        return {"agents": {workers[0].name: {"kind": workers[0].kind, "headroom_pct": 86.0, "windows": []}}, "measured_at": at}

    def measure(self, worker, client):
        with patch("foreman.measure.measure", side_effect=self.measured):
            return lifecycle.measure_worker_kinds(client, [worker], AT, state_path=self.state, sleep=lambda _: None)

    def resolve(self, row, worker, client):
        ready = composer.ensure_ready
        with patch("foreman.probe_recovery.ensure_ready", side_effect=lambda *a, **kw: ready(*a, **kw, sleep=lambda _: None)):
            return probe_recovery.resolve(self.state, row["agent"], [worker], client)

    def test_real_success_guard_reaches_measurement_then_owned_cleanup_for_every_runtime(self):
        for kind in ("codex", "claude", "grok"):
            with self.subTest(kind=kind):
                client = self.native(kind)
                result = self.measure(self.worker(kind), client)
                self.assertEqual(result["failed_agents"], [])
                self.assertEqual(result["agents"][kind]["headroom_pct"], 86.0)
                self.assertEqual(client.panes, {})
                self.assertEqual(client.agents, {})
                self.assertFalse(any(event[0] == "prompt" for event in client.events))

    def test_retained_probe_gates_remeasurement_and_resolves_without_receipt_edits(self):
        for kind in ("codex", "claude", "grok"):
            with self.subTest(kind=kind):
                worker, client = self.worker(kind), self.native(kind, dialog=True)
                result = self.measure(worker, client)
                self.assertEqual(result["failed_agents"], [kind])
                row = copy.deepcopy(probe_recovery.pending(self.state, worker))
                assert row is not None
                self.assertIn("resolve-probe", result["agents"][kind]["error"]["message"])
                before = list(client.events)
                result = self.measure(worker, client)
                self.assertEqual(result["failed_agents"], [kind])
                self.assertEqual(client.events, before)
                client.frames = [client.EMPTY]
                resolved = self.resolve(row, worker, client)
                self.assertEqual(resolved["status"], "closed")
                self.assertTrue(resolved["closure"]["closed"])
                self.assertEqual({key: resolved[key] for key in row if key not in {"status", "closure"}},
                                 {key: row[key] for key in row if key not in {"status", "closure"}})
                before = list(client.events)
                self.assertTrue(self.resolve(row, worker, client)["replayed"])
                self.assertEqual(client.events, before)
                self.assertEqual(self.measure(worker, client)["failed_agents"], [])
                self.assertFalse(any(event[0] == "prompt" for event in client.events))

    def test_changed_or_occupied_surfaces_remain_pending_with_no_input_or_close(self):
        for change in ("pid", "session", "pane", "kind", "name", "tier", "working", "blocked", "draft", "late_pid"):
            with self.subTest(change=change):
                # Separate stores keep each scenario's original record intact.
                self.state = self.root / (change + ".json")
                worker, client = self.worker("codex"), self.native("codex", dialog=True)
                self.measure(worker, client)
                row = probe_recovery.pending(self.state, worker)
                assert row is not None
                live = client.agents[row["agent"]]
                client.frames = [client.EMPTY]
                if change == "pid":
                    live["pid"] += 1
                elif change == "session":
                    live["agent_session"]["value"] = "replacement-session"
                elif change in {"pane", "kind", "name"}:
                    live[{"pane": "pane_id", "kind": "agent", "name": "name"}[change]] = "replacement"
                elif change == "tier":
                    live["argv"] = ["codex", "--dangerously-bypass-approvals-and-sandbox", "-m", "gpt-6-astra", "-c", "model_reasoning_effort=high"]
                elif change in {"working", "blocked"}:
                    live["agent_status"] = change
                elif change == "draft":
                    client.frames = ["› actual saved draft"]
                elif change == "late_pid":
                    read = client.agent_read
                    def replaced_on_read(*a, **kw):
                        text = read(*a, **kw)
                        live["pid"] += 1
                        return text
                    client.agent_read = replaced_on_read
                original = probe_recovery.store_path(self.state).read_bytes()
                events = list(client.events)
                with self.assertRaises(HerdrError):
                    self.resolve(row, worker, client)
                self.assertEqual(probe_recovery.store_path(self.state).read_bytes(), original)
                self.assertFalse(any(event[0] in {"prompt", "close"} for event in client.events[len(events):]))

    def test_retained_and_refused_recovery_preserve_the_current_transport_override(self):
        worker, client = self.worker("codex"), self.native("codex", dialog=True)
        client.binary = "/tmp/owned transport with spaces"
        result = self.measure(worker, client)
        row = probe_recovery.pending(self.state, worker)
        assert row is not None
        expected = "--herdr-bin '/tmp/owned transport with spaces'"
        self.assertIn(expected, result["agents"]["codex"]["error"]["message"])
        before = list(client.events)
        self.assertIn(expected, self.measure(worker, client)["agents"]["codex"]["error"]["message"])
        self.assertEqual(client.events, before)
        with self.assertRaises(HerdrError) as caught:
            self.resolve(row, worker, client)
        self.assertIn(expected, caught.exception.details["recovery"]["operation"])
        self.assertFalse(any(event[0] in {"prompt", "close"} for event in client.events[len(before):]))

    def test_newer_gate_refuses_measurement_without_native_activity_or_rewriting(self):
        save_state(probe_recovery.store_path(self.state), {"schema_version": 3, "records": []})
        before = probe_recovery.store_path(self.state).read_bytes()
        client = self.native("codex")
        with self.assertRaises(StateError):
            self.measure(self.worker("codex"), client)
        self.assertEqual(client.events, [])
        self.assertEqual(probe_recovery.store_path(self.state).read_bytes(), before)

    def test_failed_retention_write_keeps_ordinary_preinput_cleanup_in_force(self):
        client = self.native("codex", dialog=True)
        with patch("foreman.probe_recovery.save_state", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.measure(self.worker("codex"), client)
        self.assertEqual(client.panes, {})
        self.assertEqual(client.agents, {})
        self.assertFalse(probe_recovery.store_path(self.state).exists())
        self.assertFalse(any(event[0] == "prompt" for event in client.events))

    def test_original_null_session_cannot_be_replaced_by_later_session(self):
        worker, client = self.worker("codex"), self.native("codex", dialog=True)
        start = client.agent_start
        def start_without_session(name, kind, pane_id, flags):
            result = start(name, kind, pane_id, flags)
            client.agents[name]["agent_session"] = None
            result["agent"]["agent_session"] = None
            return result
        client.agent_start = start_without_session
        self.measure(worker, client)
        row = probe_recovery.pending(self.state, worker)
        assert row is not None
        self.assertIsNone(row["native"])
        client.frames = [client.EMPTY]
        client.agents[row["agent"]]["agent_session"] = {"source": "herdr:codex", "agent": "codex", "kind": "id", "value": "later-session"}
        before = probe_recovery.store_path(self.state).read_bytes()
        with self.assertRaises(HerdrError):
            self.resolve(row, worker, client)
        self.assertEqual(probe_recovery.store_path(self.state).read_bytes(), before)
        self.assertFalse(any(event[0] in {"close", "prompt"} for event in client.events))

    def test_pending_guard_matches_shared_window_but_not_independent_worker(self):
        worker, client = self.worker("codex"), self.native("codex", dialog=True)
        worker.window_group = "shared-test-window"
        self.measure(worker, client)
        peer = self.worker("claude")
        peer.window_group = worker.window_group
        row = probe_recovery.pending(self.state, peer)
        assert row is not None
        self.assertEqual(row["worker_kind"], "codex")
        self.assertIsNone(probe_recovery.pending(self.state, self.worker("grok")))

    def test_malformed_gate_and_changed_config_preserve_original_pending_evidence(self):
        worker, client = self.worker("codex"), self.native("codex", dialog=True)
        self.measure(worker, client)
        row = probe_recovery.pending(self.state, worker)
        assert row is not None
        before = probe_recovery.store_path(self.state).read_bytes()
        with self.assertRaisesRegex(UsageError, "original --config"):
            probe_recovery.resolve(self.state, row["agent"], [worker], client, config_path=self.root / "different.json")
        self.assertEqual(probe_recovery.store_path(self.state).read_bytes(), before)
        for change in ("process", "native", "closure", "tier", "config_sha256"):
            malformed = probe_recovery.load(self.state)
            malformed["records"][0][change] = {} if change != "closure" else {"closed": True}
            save_state(probe_recovery.store_path(self.state), malformed)
            original = probe_recovery.store_path(self.state).read_bytes()
            with self.assertRaises(StateError):
                self.measure(worker, client)
            self.assertEqual(probe_recovery.store_path(self.state).read_bytes(), original)
            save_state(probe_recovery.store_path(self.state), {"schema_version": probe_recovery.SCHEMA_VERSION, "records": [row]})

    def test_owner_migrates_startup_gates_without_changing_original_proof(self):
        worker, client = self.worker("codex"), self.native("codex", dialog=True)
        self.measure(worker, client)
        row = probe_recovery.pending(self.state, worker)
        assert row is not None
        legacy = {key: value for key, value in row.items() if key != "phase"}
        legacy["schema_version"] = 1
        save_state(probe_recovery.store_path(self.state), {"schema_version": 1, "records": [legacy]})
        before = list(client.events)
        migrated = probe_recovery.load(self.state)
        self.assertEqual(client.events, before)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["records"][0], row)
        self.assertEqual(json.loads(probe_recovery.store_path(self.state).read_text()), migrated)

    def test_missing_cleanup_phase_refuses_without_discarding_the_gate(self):
        worker, client = self.worker("codex"), self.native("codex", dialog=True)
        self.measure(worker, client)
        data = probe_recovery.load(self.state)
        del data["records"][0]["phase"]
        save_state(probe_recovery.store_path(self.state), data)
        before = probe_recovery.store_path(self.state).read_bytes()
        with self.assertRaises(StateError):
            probe_recovery.load(self.state)
        self.assertEqual(probe_recovery.store_path(self.state).read_bytes(), before)

    def test_changed_cleanup_config_refuses_before_native_calls(self):
        for change in ("composer_glyph", "composer_placeholders", "composer_ignore_dim", "launch_args"):
            with self.subTest(change=change):
                self.state = self.root / (change + ".json")
                worker, client = self.worker("codex"), self.native("codex", dialog=True)
                self.measure(worker, client)
                row = probe_recovery.pending(self.state, worker)
                assert row is not None
                original = probe_recovery.store_path(self.state).read_bytes()
                setattr(worker, change, {"composer_glyph": "replacement", "composer_placeholders": ("authored draft",),
                    "composer_ignore_dim": not worker.composer_ignore_dim, "launch_args": ("--no-alt-screen",)}[change])
                before = list(client.events)
                with self.assertRaisesRegex(UsageError, "worker config changed"):
                    self.resolve(row, worker, client)
                self.assertEqual(client.events, before)
                self.assertEqual(probe_recovery.store_path(self.state).read_bytes(), original)

    def test_nonretained_startup_failures_emit_measure_recovery_after_actual_cleanup(self):
        for failure in (HerdrError("Fresh probe identity changed", {}), HerdrError("Fresh probe is not idle/done", {}),
                composer._fresh_startup_error("startup_evidence_missing", "Missing ANSI evidence", {}),
                composer._fresh_startup_error("startup_input_occupied", "Draft occupied", {})):
            with self.subTest(failure=failure.message):
                worker, client = self.worker("codex"), self.native("codex")
                original_prepare = lifecycle._prepare_fresh_probe
                def proved_then_refused(*args, **options):
                    proof = original_prepare(*args, **options)
                    setattr(failure, "_probe_startup_observation", proof)
                    raise failure
                with patch("foreman.lifecycle._prepare_fresh_probe", side_effect=proved_then_refused), \
                        patch.object(client, "pane_read", return_value=client.EMPTY):
                    result = self.measure(worker, client)
                error = result["agents"]["codex"]["error"]
                self.assertIn("measure --agent codex", error["message"])
                self.assertIn("closed its unused probe", error["message"])
                self.assertNotIn(" apply", error["message"])
                self.assertNotIn("not_sent", error["message"])
                self.assertIsNone(result["agents"]["codex"]["headroom_pct"])
                self.assertEqual(client.panes, {})
                self.assertEqual(client.agents, {})
                self.assertFalse(any(event[0] == "prompt" for event in client.events))

    def test_public_owner_resolve_command_consumes_its_real_measure_gate(self):
        config = self.root / "config.json"
        config.write_text(json.dumps(self.payload))
        client = self.native("codex", dialog=True)
        base = ["--config", str(config), "--state", str(self.state)]
        with patch("foreman.measure.measure", side_effect=self.measured), \
                patch("foreman.lifecycle._prepare_fresh_probe", wraps=lifecycle._prepare_fresh_probe), \
                patch("foreman.lifecycle.time.sleep", return_value=None):
            # Supply the transport's read-only sleep injection at the owner
            # boundary; normal command selection, persistence and guards run.
            actual = lifecycle.measure_worker_kinds
            with patch("foreman.cli.lifecycle.measure_worker_kinds", side_effect=lambda *a, **kw: actual(*a, **kw, sleep=lambda _: None)):
                code = main(base + ["measure", "--agent", "codex", "--now", AT], stdout=io.StringIO(), stderr=io.StringIO(), client=client)
        self.assertEqual(code, 1)
        row = probe_recovery.pending(self.state, self.worker("codex"))
        assert row is not None
        client.frames = [client.EMPTY]
        out, err = io.StringIO(), io.StringIO()
        code = main(base + ["resolve-probe", "--agent", row["agent"]], stdout=out, stderr=err, client=client)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["status"], "closed")
        self.assertEqual(client.panes, {})


if __name__ == "__main__":
    unittest.main()
