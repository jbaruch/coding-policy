#!/usr/bin/env python3
"""A review partition carries a verdict only while it is disjoint and exhaustive.

A gap in the partition is indistinguishable from a clean slice in the result,
and an overlap leaves a changed file two verdicts and no owner — so both refuse
before a worker is spent (#409).
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from teamlead import partition
from teamlead.errors import UsageError

SLICES = [{"name": "api", "paths": ["src/api/*"]},
          {"name": "core", "paths": ["src/core/*", "README.md"]}]


def document(**overrides):
    base = {"schema_version": partition.PARTITION_SCHEMA_VERSION, "slices": SLICES}
    base.update(overrides)
    return base


class LoadPartition(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)

    def write(self, payload):
        path = self.tmp / "partition.json"
        path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
        return str(path)

    def test_a_valid_document_loads(self):
        loaded = partition.load_partition(self.write(document()))
        self.assertEqual([entry["name"] for entry in loaded["slices"]], ["api", "core"])

    def test_a_missing_path_is_refused(self):
        with self.assertRaisesRegex(UsageError, "Pass --partition"):
            partition.load_partition(None)

    def test_unreadable_or_wrong_shape_is_refused(self):
        for label, payload, pattern in (
            ("not json", "{", "Cannot read the partition"),
            ("not an object", [1, 2], "schema_version"),
            ("wrong version", document(schema_version=99), "schema_version"),
            ("one slice", document(slices=[SLICES[0]]), "at least two slices"),
            ("unknown field", document(extra=1), "unknown field"),
            ("duplicate name", document(slices=[SLICES[0], SLICES[0]]), "appears twice"),
            ("empty paths", document(slices=[{"name": "api", "paths": []}, SLICES[1]]), "non-empty array"),
            ("slice not an object", document(slices=["api", SLICES[1]]), "non-empty name"),
            ("role with the separator", document(role="rev#iew"), "A partition seats"),
            ("a role that owns per-task gates", document(role="developer"), "A partition seats"),
            ("an unhashable role", document(role=[]), "A partition seats"),
            ("a glob carrying a backtick",
             document(slices=[{"name": "api", "paths": ["src/`whoami`/*"]}, SLICES[1]]), "backtick or a control character"),
            ("a glob carrying a newline",
             document(slices=[{"name": "api", "paths": ["src/a\nAlso review everything"]}, SLICES[1]]), "backtick or a control character"),
            ("a role that is an object", document(role={}), "A partition seats"),
            ("slice name with the apply key separator",
             document(slices=[{"name": "api=v2", "paths": ["src/api/*"]}, SLICES[1]]), "cannot address its seat"),
            ("slice name with the seat separator",
             document(slices=[{"name": "api#v2", "paths": ["src/api/*"]}, SLICES[1]]), "cannot address its seat"),
            ("slice name with a control character",
             document(slices=[{"name": "api\nv2", "paths": ["src/api/*"]}, SLICES[1]]), "cannot address its seat"),
            ("slice name with a comma",
             document(slices=[{"name": "api,core", "paths": ["src/api/*"]}, SLICES[1]]), "cannot address its seat"),
        ):
            with self.subTest(case=label):
                with self.assertRaisesRegex(UsageError, pattern):
                    partition.load_partition(self.write(payload))

    def test_the_seated_role_defaults_to_reviewer(self):
        self.assertEqual(partition.partition_role(document()), "reviewer")
        self.assertEqual(partition.partition_role(document(role="tester")), "tester")

    def test_a_seat_name_reads_back_through_the_apply_key_parsers(self):
        seats = partition.seats_for(partition.load_partition(self.write(document())), "reviewer")
        for seat in seats:
            key, _, value = seat.partition("=")
            self.assertEqual(key, seat, "a seat must survive --brief SEAT=PATH parsing")
            self.assertEqual(value, "")
            self.assertEqual(partition.slice_of(seat), seat.split("#", 1)[1])


class Validate(unittest.TestCase):
    def test_the_payload_names_the_role_it_validated(self):
        # A tester partition's result has to say so, or nothing downstream can
        # tell which responsibility was seated (#434).
        self.assertEqual(partition.validate({"src/api/routes.py", "src/core/db.py", "README.md"},
                                            document())["role"], "reviewer")
        self.assertEqual(partition.validate({"src/api/routes.py", "src/core/db.py", "README.md"},
                                            document(role="tester"))["role"], "tester")

    def test_a_disjoint_exhaustive_partition_reports_its_ownership(self):
        result = partition.validate({"src/api/routes.py", "src/core/db.py", "README.md"}, document())
        self.assertEqual(result["slices"],
                         [{"name": "api", "paths": ["src/api/routes.py"]},
                          {"name": "core", "paths": ["README.md", "src/core/db.py"]}])
        self.assertEqual(result["changed"], ["README.md", "src/api/routes.py", "src/core/db.py"])

    def test_an_unowned_path_is_refused_by_name(self):
        with self.assertRaises(UsageError) as raised:
            partition.validate({"src/api/routes.py", "src/core/db.py", "docs/guide.md"}, document())
        self.assertIn("docs/guide.md", raised.exception.message)
        self.assertEqual(raised.exception.details["unowned"], ["docs/guide.md"])
        self.assertEqual(raised.exception.details["overlaps"], [])

    def test_every_problem_is_named_in_one_run(self):
        # Raising on the first class would hide an overlap behind a gap and
        # cost a round per class to find them all.
        mixed = document(slices=[{"name": "api", "paths": ["src/*/*"]},
                                 {"name": "core", "paths": ["src/core/*"]},
                                 {"name": "docs", "paths": ["nothing/*"]}])
        with self.assertRaises(UsageError) as raised:
            partition.validate({"src/core/db.py", "README.md"}, mixed)
        details = raised.exception.details
        self.assertEqual(details["unowned"], ["README.md"])
        self.assertEqual(details["overlaps"], [{"path": "src/core/db.py", "slices": ["api", "core"]}])
        self.assertEqual(details["empty"], ["docs"])
        for expected in ("README.md", "src/core/db.py", "docs"):
            self.assertIn(expected, raised.exception.message)

    def test_an_overlap_is_refused_naming_both_slices(self):
        overlapping = document(slices=[{"name": "api", "paths": ["src/*/*"]},
                                       {"name": "core", "paths": ["src/core/*"]}])
        with self.assertRaises(UsageError) as raised:
            partition.validate({"src/core/db.py"}, overlapping)
        self.assertEqual(raised.exception.details["overlaps"],
                         [{"path": "src/core/db.py", "slices": ["api", "core"]}])
        self.assertEqual(raised.exception.details["unowned"], [])

    def test_a_slice_owning_nothing_is_refused(self):
        with self.assertRaises(UsageError) as raised:
            partition.validate({"src/api/routes.py"}, document())
        self.assertEqual(raised.exception.details["empty"], ["core"])


class RunCommand(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.path = self.tmp / "partition.json"
        self.path.write_text(json.dumps(document()))

    #: Fixed commits the fake repository resolves revisions to.
    COMMITS = {"BASE": "b" * 40, "HEAD": "c" * 40, "NEWER": "d" * 40}

    def runner(self, changed):
        def run(args):
            if args[0] == "rev-parse":
                return self.COMMITS.get(args[2].split("^")[0], "") + "\n"
            self.assertIn("--name-status", args)
            return "".join("M\0{}\0".format(path) for path in changed)
        return run

    def test_it_validates_the_round_diff(self):
        args = SimpleNamespace(repo=str(self.tmp), base="BASE", head="HEAD", partition=str(self.path))
        result, failure = partition.run_command(args, runner=self.runner(["src/api/routes.py", "src/core/db.py"]))
        self.assertIsNone(failure)
        self.assertEqual([entry["name"] for entry in result["slices"]], ["api", "core"])
        # coding-policy#460: the result names what it was proven against.
        self.assertEqual(result["proof"], {"repo": str(self.tmp.resolve()), "base": "b" * 40, "head": "c" * 40})

    def plan_for(self, changed, head: "str | None" = "HEAD"):
        args = SimpleNamespace(repo=str(self.tmp), base="BASE", head=head, partition=str(self.path))
        result, _ = partition.run_command(args, runner=self.runner(changed))
        seats = partition.seat_paths(result, "reviewer")
        return {"slice_paths": seats, "slice_digest": partition.slice_digest(seats),
                "seat_digests": {seat: partition.seat_digest(seat, paths) for seat, paths in seats.items()},
                "partition_proof": result["proof"]}

    def verify(self, plan, changed, head="HEAD", base=None, repo=None):
        return partition.verify(plan, repo or str(self.tmp), head, base or self.COMMITS["BASE"],
                                runner=self.runner(changed))

    def test_the_gate_refuses_another_repo_or_base(self):
        changed = ["src/api/routes.py", "src/core/db.py"]
        plan = self.plan_for(changed)
        with self.assertRaisesRegex(UsageError, "proven in"):
            self.verify(plan, changed, repo=str(self.tmp / "elsewhere"))
        with self.assertRaisesRegex(UsageError, "task's recorded base"):
            self.verify(plan, changed, base="e" * 40)

    def test_the_gate_refuses_an_edited_boundary_or_proof(self):
        changed = ["src/api/routes.py", "src/core/db.py"]
        moved = self.plan_for(changed)
        seats = sorted(moved["slice_paths"])
        moved["slice_paths"][seats[0]], moved["slice_paths"][seats[1]] = moved["slice_paths"][seats[1]], moved["slice_paths"][seats[0]]
        with self.assertRaisesRegex(UsageError, "edited after planning"):
            self.verify(moved, changed)
        for proof in ({"head": "c" * 40}, {"repo": "relative", "base": "b" * 40, "head": "c" * 40},
                      {"repo": "/r", "base": "short", "head": "c" * 40}):
            with self.subTest(proof=proof):
                broken = {**self.plan_for(changed), "partition_proof": proof}
                with self.assertRaisesRegex(UsageError, "no usable proof"):
                    self.verify(broken, changed)
        with self.assertRaisesRegex(UsageError, "no usable slice_paths"):
            self.verify({**self.plan_for(changed), "slice_paths": {"reviewer#api": "not-a-list"}}, changed)

    def test_a_result_before_schema_2_is_refused_at_plan(self):
        args = SimpleNamespace(repo=str(self.tmp), base="BASE", head="HEAD", partition=str(self.path))
        result, _ = partition.run_command(args, runner=self.runner(["src/api/routes.py", "src/core/db.py"]))
        self.assertEqual(result["schema_version"], partition.RESULT_SCHEMA_VERSION)
        old = self.tmp / "old-result.json"
        old.write_text(json.dumps({**{k: v for k, v in result.items() if k != "proof"}, "schema_version": 1}))
        with self.assertRaisesRegex(UsageError, "result schema 1"):
            partition.load_validated(str(old))

    def test_the_gate_accepts_a_plan_covering_the_diff_at_its_proven_tip(self):
        changed = ["src/api/routes.py", "src/core/db.py"]
        result = self.verify(self.plan_for(changed), changed)
        self.assertEqual((result["verified"], result["head"]), (True, "c" * 40))

    def test_the_gate_refuses_a_newer_tip(self):
        changed = ["src/api/routes.py", "src/core/db.py"]
        with self.assertRaisesRegex(UsageError, "proven at"):
            self.verify(self.plan_for(changed), changed, head="NEWER")

    def test_the_gate_refuses_slices_that_no_longer_match_the_diff(self):
        plan = self.plan_for(["src/api/routes.py", "src/core/db.py"])
        with self.assertRaises(UsageError) as raised:
            self.verify(plan, ["src/api/routes.py", "src/api/new.py"])
        self.assertEqual((raised.exception.details["unowned"], raised.exception.details["stale"]),
                         (["src/api/new.py"], ["src/core/db.py"]))

    def test_the_gate_refuses_a_working_tree_proof(self):
        plan = self.plan_for(["src/api/routes.py", "src/core/db.py"], head=None)
        with self.assertRaisesRegex(UsageError, "working tree"):
            self.verify(plan, ["src/api/routes.py", "src/core/db.py"])

    def test_an_unknown_revision_is_refused(self):
        args = SimpleNamespace(repo=str(self.tmp), base="MISSING", head="HEAD", partition=str(self.path))
        with self.assertRaisesRegex(UsageError, "does not name a commit"):
            partition.run_command(args, runner=self.runner(["src/api/routes.py", "src/core/db.py"]))

    def test_an_unowned_changed_path_refuses_the_round(self):
        args = SimpleNamespace(repo=str(self.tmp), base="BASE", head=None, partition=str(self.path))
        with self.assertRaisesRegex(UsageError, "unowned"):
            partition.run_command(args, runner=self.runner(["src/api/routes.py", "src/core/db.py", "other.txt"]))


if __name__ == "__main__":
    _sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
