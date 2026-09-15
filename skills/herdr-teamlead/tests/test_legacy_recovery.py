"""Public recovery preserves history and refuses new or unrelated violations."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teamlead.cli import main
from teamlead.errors import UsageError
from teamlead.recovery import checkpoint, register_task, validate_work
from teamlead.state import add_assignment, empty_state, load_state_checked

AT = "2026-02-03T12:00:00+00:00"
AUTH = {"source": "operator message", "quote": "Keep Herdr; recover its ledger first"}


class LegacyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "state.json"
        self.backup = self.root / "state.before.json"
        self.request_path = self.root / "request.json"
        self.state = empty_state()
        store = self.state["recovery"]
        register_task(store, {"task": "old-task", "base_revision": "a" * 40,
                             "scope": "parser repair", "allowed_paths": ["src/*"], "authorization": AUTH}, AT)
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(self.state, AT, "developer", "worker", task="old-task", fix_round=fix)
        for index in (1, 2):
            store["checkpoints"].append({"schema_version": 2, "at": AT, "task": "old-task",
                "id": "old-checkpoint-" + str(index), "fix_round": 5, "base_revision": "a" * 40,
                "defect": "parser failure", "previous_attempts": "five attempts", "progress": "one case fixed",
                "change_in_approach": "canonical parsing", "judge_agent": "judge",
                "judge_report": str(self.root / "judge.md"),
                "judge_evidence": {"path": str(self.root / "judge.md"), "sha256": "c" * 64}})
        store["schema_version"] = 8
        del store["legacy_ruling_recoveries"]
        self.write_state()

    def write_state(self):
        self.raw = json.dumps(self.state, indent=2).encode()
        self.path.write_bytes(self.raw)
        self.request = {"id": "recovery-one", "state_sha256": hashlib.sha256(self.raw).hexdigest(),
                        "backup": str(self.backup), "authorization": AUTH}
        self.request_path.write_text(json.dumps(self.request))

    def run_cli(self, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = main(["recover-legacy-rulings", "--state", str(self.path), "--record", str(self.request_path),
                     "--now", AT, *extra], stdout=stdout, stderr=stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_preview_apply_and_exact_retry_preserve_all_history(self):
        _, usable = load_state_checked(self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.run_cli("--dry-run")[0], 0)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.backup.exists())
        code, output, error = self.run_cli()
        self.assertEqual(code, 0, error)
        self.assertFalse(json.loads(output)["replayed"])
        self.assertEqual(self.backup.read_bytes(), self.raw)
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)
        restored, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        expected = copy.deepcopy(self.state)
        expected["recovery"]["schema_version"] = 9
        expected["recovery"]["legacy_ruling_recoveries"] = restored["recovery"]["legacy_ruling_recoveries"]
        self.assertEqual(restored, expected)
        after = self.path.read_bytes()
        code, output, error = self.run_cli()
        self.assertEqual(code, 0, error)
        self.assertTrue(json.loads(output)["replayed"])
        self.assertEqual(self.path.read_bytes(), after)
        self.assertEqual(self.backup.read_bytes(), self.raw)

    def test_recovery_grants_no_correction_or_extra_ruling(self):
        self.assertEqual(self.run_cli()[0], 0)
        restored, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        with self.assertRaises(UsageError):
            validate_work(restored["recovery"], restored["assignments"], "old-task", 6, None, None)
        with self.assertRaisesRegex(UsageError, "already records"):
            checkpoint(restored["recovery"], restored["assignments"], {
                "id": "new-checkpoint", "task": "old-task", "defect": "F", "previous_attempts": "five",
                "progress": "some", "change_in_approach": "different", "judge_report": str(self.root / "new.md"),
                "requested_by": AUTH}, AT, "judge")

    def test_current_duplicate_rulings_still_refuse_without_writes(self):
        row = self.state["recovery"]["checkpoints"][1]
        row.update(schema_version=3, requested_by=AUTH)
        self.write_state()
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.backup.exists())

    def test_unrelated_invalid_history_refuses_without_backup(self):
        self.state["assignments"][0]["clear_reason"] = "fabricated"
        self.write_state()
        code, _, error = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("another validation failure", error)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.backup.exists())

    def test_stale_review_digest_refuses(self):
        self.path.write_bytes(self.raw + b"\n")
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.path.read_bytes(), self.raw + b"\n")
        self.assertFalse(self.backup.exists())

    def test_occupied_backup_and_symlink_refuse(self):
        self.backup.write_bytes(b"other evidence")
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.backup.read_bytes(), b"other evidence")
        self.backup.unlink()
        self.backup.symlink_to(self.path)
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_failed_replacement_retains_exact_backup_and_retry_works(self):
        with patch("teamlead.state.os.replace", side_effect=OSError("fixture replace refused")):
            self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.backup.read_bytes(), self.raw)
        self.assertEqual(self.run_cli()[0], 0)

    def test_changed_checkpoint_invalidates_recovery_receipt(self):
        self.assertEqual(self.run_cli()[0], 0)
        payload = json.loads(self.path.read_text())
        payload["recovery"]["checkpoints"][1]["progress"] = "changed history"
        changed = json.dumps(payload).encode()
        self.path.write_bytes(changed)
        _, usable = load_state_checked(self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), changed)

    def test_new_duplicate_cannot_use_old_receipt(self):
        self.assertEqual(self.run_cli()[0], 0)
        payload = json.loads(self.path.read_text())
        extra = copy.deepcopy(payload["recovery"]["checkpoints"][0])
        extra["id"] = "unapproved-new-citation"
        payload["recovery"]["checkpoints"].append(extra)
        self.path.write_text(json.dumps(payload))
        _, usable = load_state_checked(self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)

    def test_prepended_duplicate_cannot_use_old_receipt(self):
        self.assertEqual(self.run_cli()[0], 0)
        payload = json.loads(self.path.read_text())
        extra = copy.deepcopy(payload["recovery"]["checkpoints"][0])
        extra["id"] = "prepended-citation"
        payload["recovery"]["checkpoints"].insert(0, extra)
        self.path.write_text(json.dumps(payload))
        _, usable = load_state_checked(self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)

    def test_live_lock_refuses_recovery_before_backup(self):
        from teamlead.state import state_lock
        with state_lock(self.path):
            code, _, error = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("owns this state transaction", error)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.backup.exists())

    def test_old_reader_refuses_upgraded_state_without_overwriting(self):
        self.assertEqual(self.run_cli()[0], 0)
        before = self.path.read_bytes()
        with patch("teamlead.recovery.RECOVERY_STORE_VERSION", 8):
            _, usable = load_state_checked(self.path, warn=lambda _: None, persist_migration=False)
        self.assertFalse(usable)
        self.assertEqual(self.path.read_bytes(), before)

    def test_future_store_and_missing_authorization_refuse_without_writes(self):
        self.state["recovery"]["schema_version"] = 10
        self.write_state()
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.backup.exists())
        self.state["recovery"]["schema_version"] = 8
        self.write_state()
        del self.request["authorization"]
        self.request_path.write_text(json.dumps(self.request))
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.backup.exists())


if __name__ == "__main__":
    unittest.main()
