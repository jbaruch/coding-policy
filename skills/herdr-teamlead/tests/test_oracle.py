"""A declared oracle is only a licence if the round's result is compared against it."""

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.cli import main
from teamlead.errors import UsageError
from teamlead.oracle import plan_oracle, verify


class OracleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.result = self.root / "result.diff"
        self.result.write_bytes(b"--- a\n+++ b\n+exact\n")

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, oracle):
        path = self.root / "plan.json"
        path.write_text(json.dumps({"assignments": {"developer": "codex"},
                                    "rounds": {"developer": {"type": "mechanical", "context": {"oracle": oracle}}}}))
        return path

    def run_cli(self, plan, role="developer"):
        out, err = io.StringIO(), io.StringIO()
        code = main(["verify-oracle", "--plan", str(plan), "--role", role, "--result", str(self.result)],
                    stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_a_matching_digest_passes_and_a_different_one_is_blocking(self):
        digest = hashlib.sha256(self.result.read_bytes()).hexdigest()
        self.assertTrue(verify({"kind": "digest", "value": digest}, self.result)["match"])
        code, output, error = self.run_cli(self.plan({"kind": "digest", "value": "a" * 64}))
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["match"])
        self.assertIn("blocking finding", error)

    def test_patch_and_fixture_compare_byte_for_byte(self):
        same = self.root / "expected"
        same.write_bytes(self.result.read_bytes())
        other = self.root / "other"
        other.write_bytes(self.result.read_bytes() + b"\n")
        for kind in ("patch", "fixture"):
            with self.subTest(kind=kind):
                self.assertEqual(self.run_cli(self.plan({"kind": kind, "path": str(same)}))[0], 0)
                self.assertEqual(self.run_cli(self.plan({"kind": kind, "path": str(other)}))[0], 1)

    def test_the_oracle_comes_from_the_plan_not_the_caller(self):
        # A role the plan licensed on no oracle has nothing to be checked
        # against, and is refused rather than passed.
        with self.assertRaisesRegex(UsageError, "declares no oracle"):
            plan_oracle({"rounds": {"developer": {"context": {}}}}, "developer")
        code, output, error = self.run_cli(self.plan({"kind": "digest", "value": "a" * 64}), role="tester")
        self.assertEqual((code, output), (1, ""))
        self.assertIn("declares no oracle", error)

    def test_an_unreadable_result_is_a_usage_error_not_a_verdict(self):
        self.result.unlink()
        code, output, error = self.run_cli(self.plan({"kind": "digest", "value": "a" * 64}))
        self.assertEqual((code, output), (1, ""))
        self.assertIn("Cannot read the round result", error)


if __name__ == "__main__":
    unittest.main()
