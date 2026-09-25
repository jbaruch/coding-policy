"""The state and config homes move from teamlead to foreman (#501)."""

import fcntl
import io
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import home
from foreman.cli import main
from foreman.errors import StateError, UsageError


class HomeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.state_root, self.config_root = root / "state", root / "config"
        self.state_root.mkdir()
        self.config_root.mkdir()
        self.env = {"XDG_STATE_HOME": str(self.state_root), "XDG_CONFIG_HOME": str(self.config_root)}

    def legacy_home(self):
        """A legacy home whose stores name their canonical state path, and whose history quotes it."""
        old = self.state_root / "teamlead"
        (old / "state.json.memory").mkdir(parents=True)
        (old / "supervision-bindings").mkdir()
        old_state = str(old / "state.json")
        (old / "state.json").write_text(json.dumps({"schema_version": 13, "assignments": []}))
        (old / "state.json.lock").write_text("")
        (old / "state.json.attention.json").write_text(json.dumps({
            "schema_version": 1, "state_path": old_state,
            "events": [{"data": {"evidence": {"ref": "teamlead measure --state " + old_state}}}]}))
        (old / "state.json.memory" / "index.json").write_text(json.dumps({
            "schema_version": 3, "state_path": old_state,
            "records": [{"required_reads": [{"path": old_state}]}]}))
        (old / "supervision-bindings" / "abc.json").write_text(json.dumps({"state_path": old_state}))
        (old / "state.json.supervision.json").write_text(json.dumps({
            "state_path": old_state, "binding": {"state_path": old_state}}))
        (self.config_root / "teamlead").mkdir()
        (self.config_root / "teamlead" / "config.json").write_text('{"schema_version": 4}')
        return old_state

    def read(self, *parts):
        return json.loads((self.state_root / "foreman").joinpath(*parts).read_text())


class MigrateTest(HomeCase):
    def test_moves_both_homes_and_rewrites_only_identity_fields(self):
        old_state = self.legacy_home()
        new_state = str(self.state_root / "foreman" / "state.json")
        result = home.migrate(self.env)
        self.assertEqual([row["moved"] for row in result["homes"]], [True, True])
        for parts in (("state.json.attention.json",), ("state.json.memory", "index.json"),
                      ("supervision-bindings", "abc.json")):
            with self.subTest(parts=parts):
                self.assertEqual(self.read(*parts)["state_path"], new_state)
        supervision = self.read("state.json.supervision.json")
        self.assertEqual((supervision["state_path"], supervision["binding"]["state_path"]), (new_state, new_state))
        # History that quotes the old path is not rewritten, and still resolves through the link.
        self.assertIn(old_state, json.dumps(self.read("state.json.attention.json")["events"]))
        self.assertEqual(self.read("state.json.memory", "index.json")["records"][0]["required_reads"][0]["path"], old_state)
        self.assertTrue(Path(old_state).is_file())
        self.assertTrue((self.state_root / "teamlead").is_symlink())
        self.assertEqual((self.config_root / "foreman" / "config.json").read_text(), '{"schema_version": 4}')

    def test_a_second_run_changes_nothing(self):
        self.legacy_home()
        home.migrate(self.env)
        before = sorted((path, path.read_bytes()) for path in (self.state_root / "foreman").rglob("*.json"))
        again = home.migrate(self.env)
        self.assertEqual([row["moved"] for row in again["homes"]], [False, False])
        self.assertEqual(sorted((path, path.read_bytes()) for path in (self.state_root / "foreman").rglob("*.json")), before)

    def test_a_move_interrupted_before_its_link_is_finished_by_a_rerun(self):
        self.legacy_home()
        (self.state_root / "teamlead").rename(self.state_root / "foreman")
        home.migrate(self.env)
        self.assertTrue((self.state_root / "teamlead").is_symlink())
        self.assertEqual(self.read("state.json.attention.json")["state_path"], str(self.state_root / "foreman" / "state.json"))

    def test_a_held_lock_refuses_and_moves_nothing(self):
        self.legacy_home()
        handle = (self.state_root / "teamlead" / "state.json.lock").open("a")
        self.addCleanup(handle.close)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(UsageError, "holds"):
            home.migrate(self.env)
        self.assertTrue((self.state_root / "teamlead").is_dir())
        self.assertFalse((self.state_root / "foreman").exists())

    def test_a_split_home_is_refused_and_never_merged(self):
        self.legacy_home()
        (self.state_root / "foreman").mkdir()
        with self.assertRaisesRegex(UsageError, "never merged"):
            home.migrate(self.env)
        self.assertEqual(list((self.state_root / "foreman").iterdir()), [])

    def test_nothing_to_move_is_absent(self):
        self.assertEqual([row["status"] for row in home.migrate(self.env)["homes"]], ["absent", "absent"])


class RequireCurrentTest(HomeCase):
    def test_a_legacy_default_home_is_refused_before_anything_is_created(self):
        self.legacy_home()
        with self.assertRaisesRegex(StateError, "migrate-home"):
            home.require_current({"state"}, self.env)
        self.assertFalse((self.state_root / "foreman").exists())

    def test_explicit_paths_are_not_checked(self):
        self.legacy_home()
        home.require_current(set(), self.env)

    def test_the_cli_refuses_a_legacy_default_home_and_runs_migrate_home(self):
        self.legacy_home()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", self.env):
            self.assertEqual(main(["foreman-queue"], stdout=out, stderr=err), 1)
        self.assertIn("migrate-home", err.getvalue())
        self.assertFalse((self.state_root / "foreman").exists())
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", self.env):
            self.assertEqual(main(["migrate-home"], stdout=out, stderr=err), 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["homes"][0]["status"], "current")


if __name__ == "__main__":
    unittest.main()
