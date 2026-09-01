"""Tests for teamlead.state."""

# Standalone-run shim: scripts/run-tests.sh executes each suite as
# `python3 <file>` from the repo root, so put the skill directory (this file's
# grandparent) on sys.path before the package imports below.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from teamlead.errors import StateError
from teamlead.state import (
    MAX_SNAPSHOTS,
    STATE_SCHEMA_VERSION,
    add_assignment,
    add_snapshot,
    default_state_path,
    empty_state,
    latest_snapshot,
    load_state,
    role_counts,
    save_state,
)


class EmptyStateTest(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(
            empty_state(),
            {"schema_version": STATE_SCHEMA_VERSION, "snapshots": [], "assignments": []},
        )


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teamlead-state-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = Path(self.tmp) / "sub" / "state.json"

    def test_missing_file_reads_as_fresh_state(self):
        self.assertEqual(load_state(self.path), empty_state())

    def test_save_creates_parent_directories_and_round_trips(self):
        state = empty_state()
        add_assignment(state, "2026-01-02T03:04:05+00:00", "developer", "grok")
        save_state(self.path, state)
        self.assertTrue(self.path.exists())
        self.assertEqual(load_state(self.path), state)

    def test_save_is_idempotent(self):
        state = empty_state()
        save_state(self.path, state)
        save_state(self.path, state)
        self.assertEqual(load_state(self.path), state)

    def test_save_leaves_no_temp_files_behind(self):
        save_state(self.path, empty_state())
        self.assertEqual(sorted(os.listdir(self.path.parent)), ["state.json"])

    def test_save_replaces_rather_than_appends(self):
        first = empty_state()
        add_assignment(first, "2026-01-02T03:04:05+00:00", "developer", "grok")
        save_state(self.path, first)
        save_state(self.path, empty_state())
        self.assertEqual(load_state(self.path)["assignments"], [])

    def test_file_ends_with_a_newline(self):
        save_state(self.path, empty_state())
        self.assertTrue(self.path.read_text(encoding="utf-8").endswith("}\n"))


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teamlead-state-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = Path(self.tmp) / "state.json"

    def test_malformed_json_tells_the_operator_to_move_it_aside(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(StateError) as caught:
            load_state(self.path)
        self.assertIn(".bak", str(caught.exception))

    def test_unsupported_schema_version_is_refused(self):
        self.path.write_text(
            json.dumps({"schema_version": 99, "snapshots": [], "assignments": []}),
            encoding="utf-8",
        )
        with self.assertRaises(StateError) as caught:
            load_state(self.path)
        self.assertIn("99", str(caught.exception))

    def test_non_array_snapshots_is_refused(self):
        self.path.write_text(
            json.dumps({"schema_version": 1, "snapshots": {}, "assignments": []}),
            encoding="utf-8",
        )
        with self.assertRaises(StateError):
            load_state(self.path)

    def test_non_object_document_is_refused(self):
        self.path.write_text("[]", encoding="utf-8")
        with self.assertRaises(StateError):
            load_state(self.path)


class SnapshotRingTest(unittest.TestCase):
    def test_keeps_only_the_last_twenty(self):
        state = empty_state()
        for index in range(MAX_SNAPSHOTS + 5):
            add_snapshot(state, {"measured_at": "2026-01-02T00:00:{:02d}+00:00".format(index)})
        self.assertEqual(len(state["snapshots"]), MAX_SNAPSHOTS)
        self.assertEqual(state["snapshots"][0]["measured_at"], "2026-01-02T00:00:05+00:00")
        self.assertEqual(state["snapshots"][-1]["measured_at"], "2026-01-02T00:00:24+00:00")

    def test_latest_snapshot_is_the_newest(self):
        state = empty_state()
        self.assertIsNone(latest_snapshot(state))
        add_snapshot(state, {"measured_at": "a"})
        add_snapshot(state, {"measured_at": "b"})
        self.assertEqual(latest_snapshot(state), {"measured_at": "b"})


class RoleCountsTest(unittest.TestCase):
    def test_counts_per_role_and_agent(self):
        state = empty_state()
        add_assignment(state, "2026-01-01T00:00:00+00:00", "developer", "grok")
        add_assignment(state, "2026-01-02T00:00:00+00:00", "developer", "grok")
        add_assignment(state, "2026-01-02T00:00:00+00:00", "tester", "claude")
        self.assertEqual(
            role_counts(state), {"developer": {"grok": 2}, "tester": {"claude": 1}}
        )

    def test_empty_ledger_yields_empty_counts(self):
        self.assertEqual(role_counts(empty_state()), {})

    def test_malformed_ledger_rows_are_ignored(self):
        state = empty_state()
        state["assignments"] = [{"at": "x"}, {"role": "developer", "agent": "grok"}]
        self.assertEqual(role_counts(state), {"developer": {"grok": 1}})


class DefaultPathTest(unittest.TestCase):
    def setUp(self):
        self.original = os.environ.get("XDG_STATE_HOME")
        self.addCleanup(self._restore)

    def _restore(self):
        if self.original is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self.original

    def test_honours_xdg_state_home(self):
        os.environ["XDG_STATE_HOME"] = "/somewhere/state"
        self.assertEqual(default_state_path(), Path("/somewhere/state/teamlead/state.json"))

    def test_falls_back_to_local_state(self):
        os.environ.pop("XDG_STATE_HOME", None)
        self.assertEqual(
            default_state_path(), Path.home() / ".local" / "state" / "teamlead" / "state.json"
        )


if __name__ == "__main__":
    unittest.main()
