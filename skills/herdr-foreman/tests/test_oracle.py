"""A declared oracle is only a licence if the round's result is compared against it."""

import hashlib
import io
import json
import os
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman.cli import main
from foreman.errors import UsageError
from foreman.oracle import dispatch_oracles, has_oracle_round, pin_oracles, plan_oracle, verify
from foreman.state import add_assignment, empty_state, save_state

TASK = "oracle-fixture"
AT = "2026-02-03T12:00:00+00:00"
TASK_CONTEXT = {"task": TASK, "fix_round": None, "plan": None, "work": None}


class OracleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.result = self.root / "result.diff"
        self.result.write_bytes(b"--- a\n+++ b\n+exact\n")
        self.state = self.root / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, oracle, pins=True, dispatch=True):
        """A saved plan declaring `oracle`, pinned the way `plan` pins it unless `pins` is False.

        With `dispatch`, the ledger also records the round's applied dispatch,
        binding the oracle the way `apply` binds it.
        """
        rounds = {"developer": {"type": "mechanical", "context": {"oracle": oracle}}}
        document = {"assignments": {"developer": "codex"}, "rounds": rounds, "task_context": TASK_CONTEXT}
        if pins:
            document["oracle_pins"] = pin_oracles(rounds)
        path = self.root / "plan.json"
        path.write_text(json.dumps(document))
        if dispatch:
            self.dispatch(document)
        return path

    def dispatch(self, document, role="developer", status="applied", bind=True, agent="codex"):
        """Record one dispatch of `role` under TASK, as `apply` leaves it."""
        state = empty_state()
        add_assignment(state, AT, role, agent, task=TASK)
        row = {"schema_version": 1, "at": AT, "id": "oracle-dispatch", "fingerprint": "e" * 64,
               "role": role, "agent": agent, "task": TASK, "fix_round": None, "plan": None, "work": None,
               "status": status, "assignment_index": 0, "report": None,
               "result": {"schema_version": 1, "task": TASK, "role": role, "agent": agent,
                          "fix_round": None, "status": status}}
        if bind and has_oracle_round(document, role):
            row["oracle"] = dispatch_oracles(document, [role])[role]
        state["recovery"]["dispatches"].append(row)
        save_state(self.state, state)

    def run_cli(self, plan, role="developer", task=TASK):
        out, err = io.StringIO(), io.StringIO()
        code = main(["verify-oracle", "--state", str(self.state), "--plan", str(plan), "--role", role,
                     "--result", str(self.result), "--task", task],
                    stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def edit(self, path, change):
        """Rewrite the saved plan at `path` through `change`, as an edit after dispatch would."""
        document = json.loads(path.read_text())
        change(document)
        path.write_text(json.dumps(document))

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
                path = self.plan({"kind": "patch", "path": str(expected)}, pins=False, dispatch=False)
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
                code, output, error = self.run_cli(self.plan(oracle, dispatch=False))
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
            pin_oracles({"developer": {"type": "mechanical", "context": {"oracle": {"kind": "patch", "path": "/tmp/x\ud800"}}}})

    def test_each_unreadable_file_names_its_own_recovery(self):
        # A missing result is re-passed; a missing oracle is restored and the
        # plan redone. Telling the caller to re-pass an oracle fixes nothing.
        missing = str(self.root / "gone")
        with self.assertRaisesRegex(UsageError, "Cannot read the round result.*Pass the file the round produced"):
            verify({"kind": "digest", "value": "a" * 64}, missing)
        with self.assertRaisesRegex(UsageError, "Cannot read the patch oracle.*Restore the oracle file.*replan"):
            verify({"kind": "patch", "path": missing, "sha256": "a" * 64}, self.result)
        with self.assertRaisesRegex(UsageError, "Cannot read the fixture oracle.*replan"):
            pin_oracles({"developer": {"type": "mechanical", "context": {"oracle": {"kind": "fixture", "path": missing}}}})

    def test_an_oracle_on_a_non_mechanical_round_is_neither_pinned_nor_checked(self):
        # An oracle licenses only a mechanical round, and only that round's
        # file was checked at plan time. One riding on a build round is not
        # read while planning (a FIFO there must not hang `plan`) and
        # `verify-oracle` refuses to gate on it.
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        rounds = {"developer": {"type": "build", "context": {"oracle": {"kind": "patch", "path": str(fifo)}}}}
        self.assertEqual(pin_oracles(rounds), {})
        path = self.root / "plan.json"
        path.write_text(json.dumps({"assignments": {"developer": "codex"}, "rounds": rounds,
                                    "task_context": TASK_CONTEXT}))
        code, output, error = self.run_cli(path)
        self.assertEqual((code, output), (1, ""))
        self.assertIn("not mechanical", error)

    def test_a_non_regular_file_is_refused_without_blocking(self):
        # A FIFO swapped in for the result or the pinned oracle after planning
        # would block the read forever; it is refused before any read.
        expected = self.root / "expected"
        expected.write_bytes(self.result.read_bytes())
        plan = self.plan({"kind": "fixture", "path": str(expected)})
        fifo = self.root / "swapped"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(UsageError, "round result .* is not a regular file"):
            verify({"kind": "digest", "value": "a" * 64}, fifo)
        expected.unlink()
        os.mkfifo(expected)
        code, output, error = self.run_cli(plan)
        self.assertEqual((code, output), (1, ""))
        self.assertIn("is not a regular file", error)

    # coding-policy#585: the plan is a mutable file; the dispatch is what the
    # round was sent against. The gate reads the oracle back from the ledger.
    def test_a_pin_and_oracle_file_edited_after_dispatch_are_refused(self):
        expected = self.root / "expected"
        expected.write_bytes(b"what the round was sent against\n")
        for kind in ("patch", "fixture"):
            with self.subTest(kind=kind):
                path = self.plan({"kind": kind, "path": str(expected)})
                # Both edits together: the file now matches the result and the
                # plan's pin matches the file, so the plan alone checks clean.
                expected.write_bytes(self.result.read_bytes())
                self.edit(path, lambda document: document["oracle_pins"]["developer"].update(
                    sha256=hashlib.sha256(self.result.read_bytes()).hexdigest()))
                code, output, error = self.run_cli(path)
                self.assertEqual((code, output), (1, ""))
                self.assertIn("edited after dispatch", error)
                expected.write_bytes(b"what the round was sent against\n")

    def test_a_digest_edited_after_dispatch_is_refused(self):
        path = self.plan({"kind": "digest", "value": "a" * 64})
        observed = hashlib.sha256(self.result.read_bytes()).hexdigest()
        self.edit(path, lambda document: document["rounds"]["developer"]["context"]["oracle"].update(value=observed))
        code, output, error = self.run_cli(path)
        self.assertEqual((code, output), (1, ""))
        self.assertIn("edited after dispatch", error)

    def test_the_dispatch_bound_oracle_passes_when_the_plan_is_unchanged(self):
        expected = self.root / "expected"
        expected.write_bytes(self.result.read_bytes())
        code, output, error = self.run_cli(self.plan({"kind": "patch", "path": str(expected)}))
        self.assertEqual(code, 0, error)
        self.assertTrue(json.loads(output)["match"])

    def test_a_round_with_no_usable_dispatch_is_refused(self):
        oracle = {"kind": "digest", "value": hashlib.sha256(self.result.read_bytes()).hexdigest()}
        path = self.plan(oracle, dispatch=False)
        document = json.loads(path.read_text())
        cases = (("never dispatched", lambda: None, "has no dispatch"),
                 ("not applied", lambda: self.dispatch(document, status="not_sent"), "not applied"),
                 ("another worker", lambda: self.dispatch(document, agent="claude"),
                  "differs from this plan in agent"),
                 ("bound nothing", lambda: self.dispatch(document, bind=False), "bound no oracle"))
        for label, record, message in cases:
            with self.subTest(label):
                if self.state.exists():
                    self.state.unlink()
                record()
                code, output, error = self.run_cli(path)
                self.assertEqual((code, output), (1, ""))
                self.assertIn(message, error)

    def test_a_plan_for_another_task_is_refused(self):
        oracle = {"kind": "digest", "value": hashlib.sha256(self.result.read_bytes()).hexdigest()}
        code, output, error = self.run_cli(self.plan(oracle), task="another-task")
        self.assertEqual((code, output), (1, ""))
        self.assertIn("not made for task", error)

    def test_dispatch_refuses_an_oracle_file_changed_since_the_plan(self):
        # `apply` binds the bytes the round is sent against, so a file edited
        # between plan and dispatch is refused before anything is sent.
        expected = self.root / "expected"
        expected.write_bytes(b"planned\n")
        rounds = {"developer": {"type": "mechanical", "context": {"oracle": {"kind": "patch", "path": str(expected)}}}}
        document = {"assignments": {"developer": "codex"}, "rounds": rounds, "oracle_pins": pin_oracles(rounds)}
        self.assertEqual(dispatch_oracles(document, ["developer"])["developer"]["sha256"],
                         hashlib.sha256(b"planned\n").hexdigest())
        expected.write_bytes(b"rewritten\n")
        with self.assertRaisesRegex(UsageError, "changed since the plan pinned it"):
            dispatch_oracles(document, ["developer"])
        with self.assertRaisesRegex(UsageError, "pins no content"):
            dispatch_oracles({**document, "oracle_pins": {}}, ["developer"])
        self.assertEqual(dispatch_oracles({"assignments": {"tester": "codex"}}, ["tester"]), {})

if __name__ == "__main__":
    unittest.main()
