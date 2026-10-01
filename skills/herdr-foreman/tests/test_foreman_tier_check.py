"""foreman-tier-check.py owns the composite verdict and the evidence each row needs.

Every test drives `main()` with the owner CLI replaced by recorded outputs, so
nothing spawns a process or contacts Herdr (#626).
"""

import contextlib
import copy
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "foreman-tier-check.py"
_SPEC = importlib.util.spec_from_file_location("foreman_tier_check", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check)

MEASURED = {"schema_version": 3, "agents": {}, "failed_agents": []}
#: What `foreman verify-foreman` emits for a proven seat.
PROVEN = {
    "configured": True, "agent": "foreman", "pane": "w1:p0",
    "tier": {"model": "sonnet-5", "effort": "medium", "tier_row": "coordination"},
    "argv_verified": True,
    "verified": {"source": "process_argv", "argv": ["claude", "--model", "sonnet-5", "--effort", "medium"],
                 "model": "sonnet-5", "effort": "medium", "pid": 400, "pane_id": "w1:p0"},
}
UNCONFIGURED = {"configured": False, "warning": "add a `foreman` block to config.json, then run start-foreman"}


def proven(**changes):
    result = copy.deepcopy(PROVEN)
    for key, value in changes.items():
        if key == "verified":
            result["verified"].update(value)
        else:
            result[key] = value
    return result


class CompositeTest(unittest.TestCase):
    def verdict(self, verify_output, argv=(), measure=(0, MEASURED)):
        replies = {"measure": (measure[0], json.dumps(measure[1])), "verify-foreman": (0, json.dumps(verify_output))}

        def fake_run(args):
            return replies["measure" if "measure" in args else "verify-foreman"]

        out = io.StringIO()
        with patch.object(check, "run", fake_run), contextlib.redirect_stdout(out):
            code = check.main(list(argv))
        return code, json.loads(out.getvalue())

    def test_a_complete_process_argv_proof_is_ready(self):
        code, rows = self.verdict(PROVEN)
        self.assertEqual(code, 0)
        self.assertEqual(rows["foreman_tier"]["status"], "ok")
        self.assertEqual(rows["headroom"]["status"], "ok")

    def test_an_ok_result_without_its_process_argv_proof_fails(self):
        without = {key: value for key, value in PROVEN.items() if key != "verified"}
        for result in (without,
                       {"configured": True, "argv_verified": True},
                       proven(argv_verified=False),
                       proven(verified={"source": "launch_argv"}),
                       proven(verified={"argv": []})):
            with self.subTest(result=result):
                code, rows = self.verdict(result)
                self.assertEqual(code, 1)
                self.assertEqual(rows["foreman_tier"]["status"], "failed")
                self.assertIn("unproven", rows["foreman_tier"]["reason"])

    def test_a_proof_of_another_tier_fails(self):
        for changes in ({"model": "opus-5"}, {"effort": "high"}):
            with self.subTest(proven=changes):
                code, rows = self.verdict(proven(verified=changes))
                self.assertEqual(code, 1)
                self.assertEqual(rows["foreman_tier"]["status"], "failed")

    def test_unconfigured_with_its_warning_is_ready(self):
        code, rows = self.verdict(UNCONFIGURED)
        self.assertEqual(code, 0)
        self.assertEqual(rows["foreman_tier"]["status"], "unconfigured")
        self.assertIn("warning", rows["foreman_tier"]["detail"])

    def test_unconfigured_without_a_warning_fails(self):
        for result in ({"configured": False}, {"configured": False, "warning": "  "}):
            with self.subTest(result=result):
                code, rows = self.verdict(result)
                self.assertEqual(code, 1)
                self.assertEqual(rows["foreman_tier"]["status"], "failed")

    def test_skipped_headroom_with_an_unconfigured_seat_is_ready(self):
        code, rows = self.verdict(UNCONFIGURED, argv=["--no-measure"])
        self.assertEqual(code, 0)
        self.assertEqual(rows["headroom"]["status"], "skipped")

    def test_a_failed_measurement_is_not_ready(self):
        code, rows = self.verdict(PROVEN, measure=(3, {}))
        self.assertEqual(code, 1)
        self.assertEqual(rows["headroom"]["status"], "failed")

    def test_pending_updates_name_each_owner_relaunch_command(self):
        measured = {
            "agents": {
                "claude": {"error": {"code": "parse_error", "message": "bad usage",
                                      "details": {"pending_cli_update": True}}},
                "codex": {"error": {"code": "parse_error", "message": "other", "details": {}}},
            },
            "failed_agents": ["claude", "codex"],
        }
        code, rows = self.verdict(PROVEN, measure=(1, measured))
        self.assertEqual(code, 1)
        self.assertIn("`foreman relaunch-worker claude`", rows["headroom"]["reason"])
        self.assertNotIn("relaunch-worker codex", rows["headroom"]["reason"])
        self.assertEqual(rows["headroom"]["detail"], measured)


if __name__ == "__main__":
    unittest.main()
