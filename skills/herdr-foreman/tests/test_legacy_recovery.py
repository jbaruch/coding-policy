"""Schema 9 receipts stay readable; the recovery command is not published."""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from foreman.cli import COMMANDS, build_parser, main
from foreman.errors import UsageError
from foreman.recovery import RECOVERY_STORE_VERSION, checkpoint, register_task, validate_store, validate_work
from foreman.state import add_assignment, empty_state, load_state_checked

AT = "2026-02-03T12:00:00+00:00"
LATER = "2026-02-04T12:00:00+00:00"
AUTH = {"source": "operator message", "quote": "Keep Herdr; recover its ledger first"}
REQUEST = {"source": "operator message", "quote": "Authorize one extra ruling"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SchemaNineCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "state.json"
        self.backup = self.root / "state.before.json"
        self.judge = self.root / "judge.md"
        self.judge.write_text("ruling")
        self.state = empty_state()
        store = self.state["recovery"]
        register_task(store, {"task": "old-task", "base_revision": "a" * 40,
                             "scope": "parser repair", "allowed_paths": ["src/*"], "authorization": AUTH}, AT)
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(self.state, AT, "developer", "worker", task="old-task", fix_round=fix)
        for index in (1, 2):
            store["checkpoints"].append({
                "schema_version": 2, "at": AT, "task": "old-task",
                "id": "old-checkpoint-" + str(index), "fix_round": 5, "base_revision": "a" * 40,
                "defect": "parser failure", "previous_attempts": "five attempts",
                "progress": "one case fixed", "change_in_approach": "canonical parsing",
                "judge_agent": "judge", "judge_report": str(self.judge),
                "judge_evidence": {"path": str(self.judge), "sha256": "c" * 64},
            })
        store["schema_version"] = 8
        del store["legacy_ruling_recoveries"]
        del store["approaches"]
        self.write_state()

    def write_state(self):
        self.raw = json.dumps(self.state, indent=2).encode()
        self.path.write_bytes(self.raw)

    def receipt(self, rec_id="recovery-one:old-task"):
        rows = [row for row in self.state["recovery"]["checkpoints"]
                if row["task"] == "old-task" and "judge_evidence" in row]
        return {
            "schema_version": 1, "id": rec_id, "task": "old-task", "at": AT,
            "authorization": AUTH,
            "backup": {"path": str(self.backup), "sha256": "a" * 64},
            "checkpoints": {row["id"]: digest(row) for row in rows},
            "grants_future_attempts": False,
        }

    def install_receipts(self, extra_assignment=True):
        self.state["recovery"]["schema_version"] = 9
        self.state["recovery"]["legacy_ruling_recoveries"] = [self.receipt()]
        if extra_assignment:
            add_assignment(self.state, LATER, "reviewer", "worker", task="old-task")
        self.write_state()

    def run_status(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = main(["status", "--state", str(self.path)], stdout=stdout, stderr=stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_schema8_legacy_citations_migrate_and_read(self):
        before = copy.deepcopy(self.state)
        restored, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        expected = copy.deepcopy(before)
        expected["recovery"]["schema_version"] = RECOVERY_STORE_VERSION
        expected["recovery"]["legacy_ruling_recoveries"] = []
        expected["recovery"]["approaches"] = []
        self.assertEqual(restored, expected)
        self.assertEqual(restored["recovery"]["checkpoints"], before["recovery"]["checkpoints"])
        self.assertEqual(restored["assignments"], before["assignments"])
        code, output, error = self.run_status()
        self.assertEqual(code, 0, error)
        self.assertIn("old-task", json.loads(output)["tasks"])

    def test_schema9_receipts_and_appended_history_survive_the_stamp(self):
        # Version 10 widens the dispatch role to a seat (#434). A schema-9
        # store is stamped to it, and the stamp is the whole upgrade: every
        # receipt and every appended row reads back unchanged.
        self.install_receipts()
        before = copy.deepcopy(self.state)
        restored, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        expected = copy.deepcopy(before)
        expected["recovery"]["schema_version"] = RECOVERY_STORE_VERSION
        expected["recovery"]["approaches"] = []
        self.assertEqual(restored, expected)
        self.assertEqual(len(restored["recovery"]["legacy_ruling_recoveries"]), 1)
        self.assertEqual(restored["recovery"]["legacy_ruling_recoveries"][0]["checkpoints"],
                         before["recovery"]["legacy_ruling_recoveries"][0]["checkpoints"])
        self.assertEqual(restored["assignments"][-1]["at"], LATER)
        self.assertEqual(restored["assignments"][-1]["role"], "reviewer")
        code, output, error = self.run_status()
        self.assertEqual(code, 0, error)
        self.assertIn("old-task", json.loads(output)["tasks"])

    def test_a_current_store_reads_without_a_rewrite(self):
        self.install_receipts()
        self.state["recovery"]["schema_version"] = RECOVERY_STORE_VERSION
        self.state["recovery"]["approaches"] = []
        self.write_state()
        raw = self.path.read_bytes()
        restored, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(restored["recovery"]["schema_version"], RECOVERY_STORE_VERSION)
        code, _, error = self.run_status()
        self.assertEqual(code, 0, error)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_an_older_store_carrying_a_seat_dispatch_refuses_without_writes(self):
        # A seat reaches the dispatch role only at version 10. A store still
        # stamped 9 carrying one was written by a newer owner, so it is
        # preserved rather than migrated (rules/stateful-artifacts.md).
        self.install_receipts()
        self.state["recovery"]["dispatches"].append({
            "schema_version": 1, "at": AT, "id": "d-1", "task": "old-task",
            "role": "reviewer#api", "agent": "worker", "fix_round": None,
            "plan": None, "work": None, "status": "applied", "result": None,
            "report": None, "assignment_index": None, "fingerprint": "f",
        })
        self.write_state()
        raw = self.path.read_bytes()
        warnings = []
        _, usable = load_state_checked(self.path, warn=warnings.append, persist_migration=True)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertIn("seat-named dispatch", " ".join(warnings))

    def test_an_older_store_whose_saved_result_names_a_seat_refuses(self):
        # A non-applied row keeps its own copy of the role, so checking the
        # top-level one alone let a schema-9 store carrying
        # `result.role = "reviewer#api"` be stamped to 10 instead of preserved.
        self.install_receipts()
        self.state["recovery"]["dispatches"].append({
            "schema_version": 1, "at": AT, "id": "d-2", "task": "old-task",
            "role": "reviewer", "agent": "worker", "fix_round": None,
            "plan": None, "work": None, "status": "sent_but_not_started",
            "result": {"schema_version": 1, "role": "reviewer#api", "status": "sent_but_not_started"},
            "report": None, "assignment_index": None, "fingerprint": "f",
        })
        self.state["recovery"]["schema_version"] = 9
        self.write_state()
        raw = self.path.read_bytes()
        warnings = []
        _, usable = load_state_checked(self.path, warn=warnings.append, persist_migration=True)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertIn("seat-named dispatch", " ".join(warnings))

    def test_changed_or_new_citations_refuse_without_writes(self):
        self.install_receipts()
        raw = self.path.read_bytes()
        payload = json.loads(raw)
        payload["recovery"]["checkpoints"][1]["progress"] = "changed history"
        changed = json.dumps(payload, indent=2).encode()
        self.path.write_bytes(changed)
        _, usable = load_state_checked(self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), changed)
        payload["recovery"]["checkpoints"][1]["progress"] = json.loads(raw)["recovery"]["checkpoints"][1]["progress"]
        extra = copy.deepcopy(payload["recovery"]["checkpoints"][0])
        extra["id"] = "unapproved-new-citation"
        payload["recovery"]["checkpoints"].append(extra)
        appended = json.dumps(payload, indent=2).encode()
        self.path.write_bytes(appended)
        _, usable = load_state_checked(self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), appended)
        payload["recovery"]["checkpoints"].pop()
        extra["id"] = "prepended-citation"
        payload["recovery"]["checkpoints"].insert(0, extra)
        prepended = json.dumps(payload, indent=2).encode()
        self.path.write_bytes(prepended)
        _, usable = load_state_checked(self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), prepended)

    def test_schema8_store_carrying_receipt_collection_refuses_without_writes(self):
        self.state["recovery"]["legacy_ruling_recoveries"] = []
        self.write_state()
        raw = self.path.read_bytes()
        _, usable = load_state_checked(
            self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_duplicate_or_overlapping_receipts_refuse_without_writes(self):
        self.install_receipts()
        overlapping = copy.deepcopy(self.state["recovery"]["legacy_ruling_recoveries"][0])
        overlapping["id"] += ":dup"
        self.state["recovery"]["legacy_ruling_recoveries"].append(overlapping)
        self.write_state()
        overlapped = self.path.read_bytes()
        _, usable = load_state_checked(
            self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), overlapped)
        duplicate = copy.deepcopy(self.state["recovery"]["legacy_ruling_recoveries"][0])
        self.state["recovery"]["legacy_ruling_recoveries"] = [
            self.state["recovery"]["legacy_ruling_recoveries"][0], duplicate]
        self.write_state()
        duplicated = self.path.read_bytes()
        _, usable = load_state_checked(
            self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), duplicated)

    def test_a_checkpoint_missing_its_id_refuses_without_writes(self):
        # A receipt cites a checkpoint by `id`, so a row without one cannot be
        # matched to the receipt that is supposed to account for it. The reader
        # translates the lookup failure into a refusal, and a refused store is
        # never written back -- including on the migrating schema-8 path, where
        # `persist_migration=True` would otherwise save the result (#441).
        self.install_receipts()
        del self.state["recovery"]["checkpoints"][0]["id"]
        self.write_state()
        raw = self.path.read_bytes()
        warnings = []
        _, usable = load_state_checked(self.path, warn=warnings.append, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertIn("'id'", " ".join(warnings))

    def test_a_migrating_checkpoint_missing_its_id_refuses_without_writes(self):
        del self.state["recovery"]["checkpoints"][0]["id"]
        self.write_state()
        raw = self.path.read_bytes()
        warnings = []
        _, usable = load_state_checked(self.path, warn=warnings.append, persist_migration=True)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertIn("'id'", " ".join(warnings))

    def test_malformed_and_unsupported_state_refuses_without_writes(self):
        self.install_receipts()
        cases = []
        payload = json.loads(self.path.read_bytes())
        payload["recovery"]["legacy_ruling_recoveries"] = {}
        cases.append(payload)
        payload = json.loads(self.path.read_bytes())
        payload["recovery"]["legacy_ruling_recoveries"] = [None]
        cases.append(payload)
        payload = json.loads(self.path.read_bytes())
        payload["recovery"]["legacy_ruling_recoveries"][0]["grants_future_attempts"] = True
        cases.append(payload)
        payload = json.loads(self.path.read_bytes())
        del payload["recovery"]["legacy_ruling_recoveries"]
        cases.append(payload)
        payload = json.loads(self.path.read_bytes())
        payload["recovery"]["schema_version"] = RECOVERY_STORE_VERSION + 1
        cases.append(payload)
        for case in cases:
            encoded = json.dumps(case, indent=2).encode()
            self.path.write_bytes(encoded)
            with self.subTest(case=case["recovery"].get("schema_version"),
                              receipts=case["recovery"].get("legacy_ruling_recoveries")):
                _, usable = load_state_checked(self.path, warn=lambda _: None, persist_migration=False)
                self.assertFalse(usable)
                self.assertEqual(self.path.read_bytes(), encoded)

    def test_modern_ruling_bounds_remain_on_version_three(self):
        restored, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        mixed = copy.deepcopy(restored["recovery"])
        current = {
            "schema_version": 3, "at": AT, "task": "old-task", "id": "current-a",
            "fix_round": 5, "base_revision": "a" * 40, "defect": "parser failure",
            "previous_attempts": "five attempts", "progress": "one case fixed",
            "change_in_approach": "canonical parsing", "judge_agent": "judge",
            "judge_report": str(self.judge),
            "judge_evidence": {"path": str(self.judge), "sha256": "c" * 64},
            "requested_by": REQUEST,
        }
        mixed["checkpoints"].append(current)
        validate_store(mixed, restored["assignments"])
        mixed["checkpoints"].append({**copy.deepcopy(current), "id": "current-b"})
        with self.assertRaisesRegex(UsageError, "more than one operator-requested ruling"):
            validate_store(mixed, restored["assignments"])

    def test_correction_and_ruling_limits_stay_exhausted(self):
        self.install_receipts()
        restored, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        before = self.path.read_bytes()
        with self.assertRaises(UsageError):
            validate_work(restored["recovery"], restored["assignments"], "old-task", 6, None, None)
        with self.assertRaisesRegex(UsageError, "already records"):
            checkpoint(restored["recovery"], restored["assignments"], {
                "id": "new-checkpoint", "task": "old-task", "defect": "F",
                "previous_attempts": "five", "progress": "some",
                "change_in_approach": "different", "judge_report": str(self.root / "new.md"),
                "requested_by": REQUEST,
            }, AT, "judge")
        self.assertEqual(self.path.read_bytes(), before)

    def test_schema8_reader_refuses_schema9_without_overwrite(self):
        self.install_receipts()
        before = self.path.read_bytes()
        with patch("foreman.recovery.RECOVERY_STORE_VERSION", 8):
            _, usable = load_state_checked(self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), before)

    def test_recover_legacy_rulings_command_is_absent(self):
        self.assertNotIn("recover-legacy-rulings", COMMANDS)
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args([
                    "recover-legacy-rulings", "--state", str(self.path), "--record", str(self.path),
                ])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice", captured.getvalue())
        self.assertIn("recover-legacy-rulings", captured.getvalue())
        self.assertEqual(self.path.read_bytes(), self.raw)


if __name__ == "__main__":
    unittest.main()
