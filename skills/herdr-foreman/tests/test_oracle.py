"""A declared oracle is only a licence if the round's result is compared against it."""

import hashlib
import io
import json
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman.cli import main
from foreman.errors import UsageError
from foreman.oracle import pin_oracles, plan_oracle, verify


class OracleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.result = self.root / "result.diff"
        self.result.write_bytes(b"--- a\n+++ b\n+exact\n")

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, oracle, pins=True):
        """A saved plan declaring `oracle`, pinned the way `plan` pins it unless `pins` is False."""
        rounds = {"developer": {"type": "mechanical", "context": {"oracle": oracle}}}
        document = {"assignments": {"developer": "codex"}, "rounds": rounds}
        if pins:
            document["oracle_pins"] = pin_oracles(rounds)
        path = self.root / "plan.json"
        path.write_text(json.dumps(document))
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

    def test_an_oracle_file_edited_after_planning_is_refused_not_compared(self):
        # coding-policy#488: the plan licensed the round on the bytes the file
        # held then. Rewriting the file to match the result must not pass.
        expected = self.root / "expected"
        expected.write_bytes(b"what the plan licensed\n")
        for kind in ("patch", "fixture"):
            with self.subTest(kind=kind):
                plan = self.plan({"kind": kind, "path": str(expected)})
                expected.write_bytes(self.result.read_bytes())
                code, output, error = self.run_cli(plan)
                self.assertEqual((code, output), (1, ""))
                self.assertIn("changed since the plan was written", error)
                expected.write_bytes(b"what the plan licensed\n")

    def test_a_patch_or_fixture_plan_that_pinned_nothing_is_refused(self):
        expected = self.root / "expected"
        expected.write_bytes(self.result.read_bytes())
        cases = (("no pins", None),
                 ("pin for another path", {"developer": {"path": str(self.root / "elsewhere"), "sha256": "a" * 64}}),
                 ("malformed pin", {"developer": {"path": str(expected), "sha256": "not a digest"}}))
        for label, pins in cases:
            with self.subTest(label):
                path = self.plan({"kind": "patch", "path": str(expected)}, pins=False)
                if pins is not None:
                    document = json.loads(path.read_text())
                    document["oracle_pins"] = pins
                    path.write_text(json.dumps(document))
                code, output, error = self.run_cli(path)
                self.assertEqual((code, output), (1, ""))
                self.assertIn("pins no content", error)

    def test_a_malformed_saved_oracle_is_a_usage_error_not_a_traceback(self):
        cases = (("patch with no path", {"kind": "patch"}),
                 ("non-string path", {"kind": "fixture", "path": 7}),
                 ("relative path", {"kind": "patch", "path": "exact.patch"}),
                 ("unknown kind", {"kind": "vibes", "value": "a" * 64}),
                 ("unhashable kind", {"kind": ["patch"], "path": "/x"}),
                 ("short digest", {"kind": "digest", "value": "a" * 63}),
                 ("NUL in the path", {"kind": "patch", "path": "/tmp/a\0b"}),
                 ("not an object", "digest"),
                 ("stray key", {"kind": "digest", "value": "a" * 64, "extra": 1}))
        for label, oracle in cases:
            with self.subTest(label):
                code, output, error = self.run_cli(self.plan(oracle))
                self.assertEqual((code, output), (1, ""))
                self.assertIn("malformed", error)
                self.assertNotIn("Traceback", error)

    def test_memory_stays_bounded_however_large_the_files(self):
        # coding-policy#488: verifying a large result against a large oracle
        # must not hold either file in memory. The peak Python allocation while
        # verifying two 16 MiB files stays far below the size of one of them.
        size = 16 * 1024 * 1024
        block = bytes(range(256)) * 4096
        for path in (self.result, self.root / "expected"):
            with path.open("wb") as handle:
                for _ in range(size // len(block)):
                    handle.write(block)
        plan = self.plan({"kind": "fixture", "path": str(self.root / "expected")})
        tracemalloc.start()
        try:
            code, output, error = self.run_cli(plan)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(code, 0, error)
        self.assertTrue(json.loads(output)["match"])
        self.assertLess(peak, size // 4)

    def test_an_unusable_file_name_is_a_usage_error_not_a_traceback(self):
        # A NUL or a lone surrogate never names a file; opening one raises
        # ValueError, which must surface as the documented usage error.
        for label, oracle, result in (
                ("NUL in the result path", {"kind": "digest", "value": "a" * 64}, "/tmp/a\0b"),
                ("surrogate in the oracle path", {"kind": "fixture", "path": "/tmp/x\ud800", "sha256": "a" * 64},
                 str(self.result))):
            with self.subTest(label):
                with self.assertRaisesRegex(UsageError, "not a usable file name"):
                    verify(oracle, result)
        with self.assertRaisesRegex(UsageError, "not a usable file name"):
            pin_oracles({"developer": {"context": {"oracle": {"kind": "patch", "path": "/tmp/x\ud800"}}}})

if __name__ == "__main__":
    unittest.main()
