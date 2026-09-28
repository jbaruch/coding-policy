#!/usr/bin/env python3
"""Outcome tests for skills/herdr-foreman/prune-report-caches.py.

Every case builds its own state root in a temporary directory: a foreman state
file whose dispatch rows name the reports directories, and those directories
filled with cache-shaped and evidence files. Every file is aged to FIXTURE_TIME
and the script judges at NOW, never the wall clock (rules/testing-standards.md
Determinism). The script runs as a subprocess, so each case exercises the
shipped contract: argv, JSON on stdout, exit code.

Covers:
  1. Idle directory      -> every cache kind is removed, every evidence file
                            and every look-alike without a signature is kept.
  2. Dry run             -> the same caches are listed, nothing is removed.
  3. Not idle            -> one recent file keeps the whole directory.
  4. Symlinks            -> a symlinked cache is not followed; a reports
                            directory that is a symlink is skipped.
  5. Outside the root    -> a recorded directory outside the root is skipped.
  6. Missing directory   -> counted, never an error.
  7. Read-only cache     -> a Go module cache with read-only dirs is removed.
  8. Interrupted removal -> a removal cut short leaves each cache's
                            signature in place; the next run finishes it.
  9. Unusable ledger     -> could_not_check, nothing removed, exit 3.
 10. No ledger           -> nothing to do, exit 0.
 11. Out of budget       -> incomplete, nothing started after the budget.
 12. Usage error         -> a non-positive or non-finite budget exits 1.
 13. Active assignment   -> an active supervision enrollment whose report is
                            in the directory keeps it whole, however old.
 14. Unusable supervision-> could_not_check, nothing removed, exit 3.
 15. Swap race           -> a directory replaced by a symlink between the
                            survey and the removal is never entered; the
                            cache behind the link survives.
 16. Mixed directory     -> a directory with a cache's name and signature
                            that also holds a report stays whole.
 17. Enrollment race     -> an enrollment landing after the survey keeps the
                            directory: the check before each removal re-reads
                            supervision under its lock.
 18. Locked supervision  -> a store lock held by a foreman command skips the
                            directory as busy.
 19. Unverified          -> a recorded directory holding none of its recorded
                            briefs, or a frozen copy whose bytes no longer
                            match its name, is skipped.
 20. Replaced cache      -> a cache swapped for an evidence directory of the
                            same name after it was classified is never
                            touched: nothing removed, evidence intact.
 21. Store breaks mid-run-> a supervision store unreadable at removal time
                            stops the run as could_not_check, exit 3.
 22. Evidence beside a module cache -> a `go-mod-cache` holding
                            `cache/download` and an evidence directory
                            (`findings/`, `findings.v1/`, `report@draft/`)
                            stays whole; a genuine module-cache layout goes.
 23. Recorded file       -> a recorded reports directory that is a regular
                            file is counted missing, never a symlink.
"""

import argparse
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "prune-report-caches.py"
sys.path.insert(0, str(HERE.parent))

from foreman import state as ledger
from foreman import supervision

FIXTURE_TIME = 1577836800.0  # 2020-01-01T00:00:00Z
NOW = FIXTURE_TIME + 25 * 3600
AT = "2020-01-01T00:00:00+00:00"
GO_README = "This directory holds cached build artifacts from the Go build system.\nRun \"go clean -cache\".\n"

CACHE_PATHS = {
    "go-build-cache": "developer-evidence/go-cache",
    "go-module-cache": "developer-evidence/go-mod-cache",
    "pip-cache": "developer-evidence/pip-cache",
    "npm-cache": "developer-evidence/npm-cache",
    "virtualenv": "developer-evidence/venv",
    "node-modules": "developer-evidence/node_modules",
    "python-bytecode": "tester-data/home-guard/__pycache__",
    "plugin-cache-copy": "tester-data/home-guard/before/.claude/plugins/cache",
}

#: Evidence dropped into a directory that otherwise carries a cache's name and
#: signature: each such directory must stay whole.
MIXED = {
    "go-build-cache": "mixed/go-cache",
    "go-module-cache": "mixed/go-mod-cache",
    "pip-cache": "mixed/pip-cache",
    "npm-cache": "mixed/npm-cache",
    "virtualenv": "mixed/venv",
    "node-modules": "mixed/node_modules",
    "python-bytecode": "mixed/__pycache__",
    "plugin-cache-copy": "mixed/home/.claude/plugins/cache",
}

EVIDENCE = [
    "report.md",
    "developer-evidence/logs/build.log",
    "developer-evidence/combined.diff",
    "tester-data/home-guard/before.json",
    "tester-data/home-guard/home_guard.py",
    "tester-data/home-guard/before/.claude/settings.json",
    "look-alikes/venv/notes.txt",
    "look-alikes/pip-cache/receipt.json",
    "look-alikes/__pycache__/keep.txt",
    "look-alikes/go-cache/README",
]


def write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def build_cache(root, kind):
    if kind == "go-build-cache":
        write(root / "README", GO_README)
        write(root / "00" / "abc-d")
    elif kind == "go-module-cache":
        write(root / "cache" / "download" / "golang.org" / "x" / "list")
        write(root / "golang.org" / "x" / "mod@v0.1.0" / "go.mod")
    elif kind == "pip-cache":
        write(root / "http" / "1" / "blob")
    elif kind == "npm-cache":
        write(root / "_cacache" / "index-v5" / "entry")
    elif kind == "virtualenv":
        write(root / "pyvenv.cfg", "home = /usr/bin\n")
        write(root / "lib" / "site.py")
    elif kind == "node-modules":
        write(root / ".package-lock.json", "{}")
        write(root / "left-pad" / "index.js")
    elif kind == "python-bytecode":
        write(root / "home_guard.cpython-311.pyc")
    elif kind == "plugin-cache-copy":
        write(root / "jbaruch" / "coding-policy" / "rules" / "a.md")


def age(top, when=FIXTURE_TIME):
    """Set every mtime under `top`, top included, without following links."""
    for current, dirs, files in os.walk(top, topdown=False):
        for name in files + dirs:
            os.utime(os.path.join(current, name), (when, when), follow_symlinks=False)
    os.utime(top, (when, when), follow_symlinks=False)


class Fixture:
    def __init__(self, base):
        self.root = Path(os.path.realpath(base)) / "state"
        self.state = self.root / "foreman" / "state.json"
        self.rows = []

    def record(self, reports_dir, write_brief=True):
        """A dispatch row naming a frozen brief under `reports_dir`, written
        there with the digest its name carries unless `write_brief` is false."""
        index = len(self.rows)
        body = "brief {}\n".format(index)
        digest = hashlib.sha256(body.encode()).hexdigest()[:16]
        brief = Path(reports_dir) / ".dispatched" / "brief-{}.{}.md".format(index, digest)
        if write_brief:
            write(brief, body)
        self.rows.append({"schema_version": 1, "at": AT, "id": "d{}".format(index), "fingerprint": "f{}".format(index),
                          "task": "t{}".format(index), "role": "developer", "agent": "w{}".format(index),
                          "fix_round": None, "status": "not_sent", "plan": None, "work": None,
                          "brief": str(brief), "common": str(Path(reports_dir) / "COMMON.md"),
                          "result": None, "report": None})

    def save(self):
        document = ledger.empty_state()
        document["recovery"]["dispatches"].extend(self.rows)
        ledger.save_state(str(self.state), document)

    def reports(self, name="round/acr9", caches=tuple(CACHE_PATHS)):
        top = self.root / name
        for rel in EVIDENCE:
            write(top / rel)
        write(top / "look-alikes" / "go-cache" / "README", "not a go cache\n")
        for kind in caches:
            build_cache(top / CACHE_PATHS[kind], kind)
        self.record(top)
        return top


def run_raw(fixture, *extra):
    argv = [sys.executable, str(SCRIPT), "--root", str(fixture.root), "--state", str(fixture.state),
            "--now", str(NOW), *extra]
    environment = dict(os.environ, XDG_STATE_HOME=str(fixture.root))
    return subprocess.run(argv, capture_output=True, text=True, env=environment, check=False)


def run(fixture, *extra):
    """(exit code, parsed result, stderr) for a run that prints its JSON."""
    done = run_raw(fixture, *extra)
    if not done.stdout.strip():
        raise AssertionError("no JSON on stdout (exit {}): {}".format(done.returncode, done.stderr))
    return done.returncode, json.loads(done.stdout), done.stderr


def load_script():
    """The script as a module, for a case that replaces one of its parts."""
    spec = importlib.util.spec_from_file_location("prune_report_caches", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load {}".format(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PruneReportCachesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup)
        self.fx = Fixture(self.temp.name)

    def cleanup(self):
        # A failed case may leave read-only directories behind.
        for current, dirs, _files in os.walk(self.temp.name):
            for name in dirs:
                path = os.path.join(current, name)
                if not os.path.islink(path):
                    os.chmod(path, stat.S_IRWXU)
        self.temp.cleanup()

    def test_idle_directory_loses_every_cache_and_keeps_evidence(self):
        top = self.fx.reports()
        self.fx.save()
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(c["kind"] for c in doc["caches"]), sorted(CACHE_PATHS))
        for kind, rel in CACHE_PATHS.items():
            self.assertFalse(os.path.lexists(top / rel), kind)
        for rel in EVIDENCE:
            self.assertTrue((top / rel).is_file(), rel)
        self.assertGreater(doc["bytes"], 0)
        self.assertEqual(doc["failed"], [])
        self.assertFalse(doc["incomplete"])
        self.assertIsNone(doc["could_not_check"])

    def test_dry_run_removes_nothing(self):
        top = self.fx.reports()
        self.fx.save()
        age(top)
        rc, doc, err = run(self.fx, "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertTrue(doc["dry_run"])
        self.assertEqual(len(doc["caches"]), len(CACHE_PATHS))
        for rel in CACHE_PATHS.values():
            self.assertTrue((top / rel).is_dir(), rel)

    def test_recent_file_keeps_the_whole_directory(self):
        top = self.fx.reports()
        self.fx.save()
        age(top)
        live = top / CACHE_PATHS["go-build-cache"] / "01" / "fresh-d"
        write(live)
        recent = NOW - 3600
        os.utime(live, (recent, recent))
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["caches"], [])
        self.assertEqual(doc["skipped"], [{"path": str(top), "reason": "not_idle"}])
        for rel in CACHE_PATHS.values():
            self.assertTrue((top / rel).is_dir(), rel)

    def test_symlinks_are_never_followed(self):
        outside = Path(os.path.realpath(self.temp.name)) / "outside"
        build_cache(outside / "venv", "virtualenv")
        top = self.fx.reports(caches=())
        os.symlink(outside / "venv", top / "venv")
        linked = self.fx.root / "round" / "linked"
        os.symlink(top, linked)
        self.fx.record(linked)
        self.fx.save()
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["caches"], [])
        self.assertIn({"path": str(linked), "reason": "symlink"}, doc["skipped"])
        self.assertTrue((outside / "venv" / "pyvenv.cfg").is_file())
        self.assertTrue(os.path.islink(top / "venv"))

    def test_directory_outside_the_root_is_skipped(self):
        stray = Path(os.path.realpath(self.temp.name)) / "elsewhere" / "round"
        build_cache(stray / "venv", "virtualenv")
        self.fx.record(stray)
        self.fx.save()
        age(stray)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["skipped"], [{"path": str(stray), "reason": "outside_root"}])
        self.assertTrue((stray / "venv" / "pyvenv.cfg").is_file())

    def test_missing_directory_is_counted(self):
        self.fx.record(self.fx.root / "round" / "gone", write_brief=False)
        self.fx.save()
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["reports_dirs"], doc["missing"], doc["caches"]), (1, 1, []))

    def test_regular_file_candidate_is_counted_missing(self):
        candidate = self.fx.root / "round" / "flat"
        self.fx.record(candidate, write_brief=False)
        write(candidate, "not a directory")
        self.fx.save()
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["reports_dirs"], doc["missing"], doc["skipped"]), (1, 1, []))
        self.assertTrue(candidate.is_file())

    def test_module_cache_with_evidence_directory_stays_whole(self):
        top = self.fx.reports(caches=())
        evidence = ("findings", "findings.v1", "report@draft")
        mixed = {}
        for name in evidence:
            mixed[name] = top / "mixed" / name / "go-mod-cache"
            write(mixed[name] / "cache" / "download" / "golang.org" / "x" / "list")
            write(mixed[name] / name / "report.md", "evidence")
        genuine = top / "developer-evidence" / "go-mod-cache"
        write(genuine / "cache" / "download" / "golang.org" / "x" / "mod" / "@v" / "v0.1.0.zip")
        write(genuine / "cache" / "download" / "dotless" / "@v" / "v1.2.3.zip")
        write(genuine / "golang.org" / "x" / "mod@v0.1.0" / "go.mod")
        write(genuine / "dotless@v1.2.3" / "go.mod")
        self.fx.save()
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual([(c["path"], c["kind"]) for c in doc["caches"]], [(str(genuine), "go-module-cache")])
        self.assertFalse(os.path.lexists(genuine))
        for name, path in mixed.items():
            self.assertTrue((path / name / "report.md").is_file(), name)
            self.assertTrue((path / "cache" / "download" / "golang.org" / "x" / "list").is_file(), name)

    def test_read_only_module_cache_is_removed(self):
        top = self.fx.reports(caches=("go-module-cache",))
        self.fx.save()
        age(top)
        cache = top / CACHE_PATHS["go-module-cache"]
        for current, dirs, _files in os.walk(cache, topdown=False):
            for name in dirs:
                os.chmod(os.path.join(current, name), 0o555)
        os.chmod(cache, 0o555)
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.lexists(cache))
        self.assertEqual([c["kind"] for c in doc["caches"]], ["go-module-cache"])

    def test_interrupted_removal_is_finished_next_run(self):
        top = self.fx.reports(caches=("go-module-cache", "virtualenv"))
        self.fx.save()
        age(top)
        module = load_script()
        real_unlink = os.unlink
        calls = []

        def unlink_then_fail(path, *args, **kwargs):
            calls.append(path)
            if len(calls) > 1:
                raise PermissionError(1, "stand-in interruption")
            return real_unlink(path, *args, **kwargs)

        args = argparse.Namespace(dry_run=False, root=str(self.fx.root), state=str(self.fx.state), now=NOW,
                                  budget_sec=None)
        with mock.patch.object(module.os, "unlink", unlink_then_fail):
            first = module.run(args)
        self.assertEqual(len(first["failed"]), 2)
        self.assertTrue((top / CACHE_PATHS["virtualenv"] / "pyvenv.cfg").is_file())
        self.assertTrue((top / CACHE_PATHS["go-module-cache"] / "cache" / "download").is_dir())
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(c["kind"] for c in doc["caches"]), ["go-module-cache", "virtualenv"])
        self.assertFalse(os.path.lexists(top / CACHE_PATHS["virtualenv"]))
        self.assertFalse(os.path.lexists(top / CACHE_PATHS["go-module-cache"]))

    def test_directory_swapped_for_a_symlink_is_never_entered(self):
        top = self.fx.reports(caches=("virtualenv",))
        self.fx.save()
        age(top)
        outside = Path(os.path.realpath(self.temp.name)) / "outside"
        build_cache(outside / "venv", "virtualenv")
        module = load_script()
        surveyed = module.survey

        def survey_then_swap(cand_fd, cutoff, budget):
            outcome = surveyed(cand_fd, cutoff, budget)
            evidence = top / "developer-evidence"
            evidence.rename(top / "moved-evidence")
            os.symlink(outside, evidence)
            return outcome

        setattr(module, "survey", survey_then_swap)
        args = argparse.Namespace(dry_run=False, root=str(self.fx.root), state=str(self.fx.state), now=NOW,
                                  budget_sec=None)
        result = module.run(args)
        self.assertTrue((outside / "venv" / "pyvenv.cfg").is_file())
        self.assertEqual(result["caches"], [])
        self.assertEqual([f["path"] for f in result["failed"]], [str(top / CACHE_PATHS["virtualenv"])])

    def test_directory_without_its_recorded_briefs_is_skipped(self):
        top = self.fx.reports()
        tampered = self.fx.reports(name="round/tampered")
        self.fx.save()
        for path in (top / ".dispatched").iterdir():
            path.unlink()
        for path in (tampered / ".dispatched").iterdir():
            path.write_text("other bytes\n")
        age(top)
        age(tampered)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["caches"], [])
        self.assertEqual(sorted(s["path"] for s in doc["skipped"] if s["reason"] == "unverified"),
                         sorted([str(top), str(tampered)]))
        self.assertTrue((top / CACHE_PATHS["virtualenv"]).is_dir())

    def test_cache_replaced_after_classification_is_never_touched(self):
        top = self.fx.reports(caches=("virtualenv",))
        self.fx.save()
        age(top)
        module = load_script()
        checked = module.busy
        venv = top / CACHE_PATHS["virtualenv"]

        calls = []

        def swap_then_check(real, active):
            calls.append(real)
            if len(calls) == 2:  # the re-check under the lock, after classification
                venv.rename(top / "moved-venv")
                write(venv / "pyvenv.cfg", "home = /usr/bin\n")
                write(venv / "findings.md", "evidence")
            return checked(real, active)

        setattr(module, "busy", swap_then_check)
        args = argparse.Namespace(dry_run=False, root=str(self.fx.root), state=str(self.fx.state), now=NOW,
                                  budget_sec=None)
        result = module.run(args)
        self.assertEqual(result["caches"], [])
        self.assertEqual([f["path"] for f in result["failed"]], [str(venv)])
        self.assertTrue((venv / "findings.md").is_file())
        self.assertTrue((top / "moved-venv" / "pyvenv.cfg").is_file())

    def test_store_unreadable_at_removal_stops_the_run(self):
        top = self.fx.reports(caches=("virtualenv",))
        self.fx.save()
        self.bind()
        age(top)
        module = load_script()
        surveyed = module.survey
        store = Path(str(supervision.store_path(str(self.fx.state))))

        def survey_then_corrupt(cand_fd, cutoff, budget):
            outcome = surveyed(cand_fd, cutoff, budget)
            store.write_text("{not json")
            return outcome

        setattr(module, "survey", survey_then_corrupt)
        with mock.patch.object(sys, "argv", ["prune-report-caches.py", "--root", str(self.fx.root), "--state",
                                             str(self.fx.state), "--now", str(NOW)]), \
                mock.patch("sys.stdout", new=io.StringIO()) as out:
            code = module.main()
        doc = json.loads(out.getvalue())
        self.assertEqual(code, 3)
        self.assertIsNotNone(doc["could_not_check"])
        self.assertTrue((top / CACHE_PATHS["virtualenv"] / "pyvenv.cfg").is_file())

    def test_cache_holding_evidence_stays_whole(self):
        top = self.fx.reports(caches=())
        for kind, rel in MIXED.items():
            build_cache(top / rel, kind)
            write(top / rel / "findings.md", "evidence")
        self.fx.save()
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["caches"], [])
        for rel in MIXED.values():
            self.assertTrue((top / rel / "findings.md").is_file(), rel)

    def bind(self):
        who = supervision.identity("lead", str(self.fx.root), "fixture", pane_id="lead-pane")
        supervision.bind(str(self.fx.state), who, AT, root=str(self.fx.root / "bindings"))

    def test_enrollment_after_the_survey_keeps_the_directory(self):
        top = self.fx.reports(caches=("virtualenv",))
        self.fx.save()
        self.bind()
        age(top)
        module = load_script()
        surveyed = module.survey
        state = str(self.fx.state)

        def survey_then_enroll(cand_fd, cutoff, budget):
            outcome = surveyed(cand_fd, cutoff, budget)
            supervision.enroll(state, {"id": "d0", "agent": "w0", "task": "t0", "report": str(top / "developer.md"),
                                       "pane_id": "w0-pane", "native_session": None}, AT)
            return outcome

        setattr(module, "survey", survey_then_enroll)
        args = argparse.Namespace(dry_run=False, root=str(self.fx.root), state=state, now=NOW, budget_sec=None)
        result = module.run(args)
        self.assertEqual(result["caches"], [])
        self.assertEqual(result["skipped"], [{"path": str(top), "reason": "active_assignment"}])
        self.assertTrue((top / CACHE_PATHS["virtualenv"] / "pyvenv.cfg").is_file())

    def test_locked_supervision_store_skips_as_busy(self):
        top = self.fx.reports(caches=("virtualenv",))
        self.fx.save()
        self.bind()
        age(top)
        with ledger.state_lock(supervision.store_path(str(self.fx.state))):
            rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["skipped"], [{"path": str(top), "reason": "busy"}])
        self.assertTrue((top / CACHE_PATHS["virtualenv"] / "pyvenv.cfg").is_file())

    def test_active_enrollment_keeps_the_directory(self):
        top = self.fx.reports()
        self.fx.save()
        who = supervision.identity("lead", str(self.fx.root), "fixture", pane_id="lead-pane")
        supervision.bind(str(self.fx.state), who, AT, root=str(self.fx.root / "bindings"))
        supervision.enroll(str(self.fx.state), {"id": "d0", "agent": "w0", "task": "t0",
                                                "report": str(top / "developer.md"), "pane_id": "w0-pane",
                                                "native_session": None}, AT)
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["caches"], [])
        self.assertEqual(doc["skipped"], [{"path": str(top), "reason": "active_assignment"}])
        self.assertTrue((top / CACHE_PATHS["virtualenv"]).is_dir())

    def test_unusable_supervision_store_removes_nothing(self):
        top = self.fx.reports()
        self.fx.save()
        Path(str(self.fx.state) + ".supervision.json").write_text("{not json")
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 3, err)
        self.assertIsNotNone(doc["could_not_check"])
        self.assertEqual(doc["caches"], [])
        self.assertTrue((top / CACHE_PATHS["virtualenv"]).is_dir())

    def test_unusable_ledger_removes_nothing(self):
        top = self.fx.reports()
        self.fx.save()
        self.fx.state.write_text("{not json")
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 3, err)
        self.assertIn("stopped before removing anything more", err)
        self.assertIsNotNone(doc["could_not_check"])
        self.assertEqual(doc["caches"], [])
        self.assertTrue((top / CACHE_PATHS["virtualenv"]).is_dir())

    def test_absent_ledger_is_nothing_to_do(self):
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["reports_dirs"], doc["caches"], doc["could_not_check"]), (0, [], None))

    def test_spent_budget_starts_nothing(self):
        top = self.fx.reports()
        self.fx.save()
        age(top)
        module = load_script()
        out_of_budget = module.OutOfBudget

        class Spent(module.Budget):
            def check(self):
                raise out_of_budget()

        setattr(module, "Budget", Spent)
        args = argparse.Namespace(dry_run=False, root=str(self.fx.root), state=str(self.fx.state), now=NOW,
                                  budget_sec=1.0)
        result = module.run(args)
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["caches"], [])
        for rel in CACHE_PATHS.values():
            self.assertTrue((top / rel).is_dir(), rel)

    def test_bad_budget_is_a_usage_error(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(budget=value):
                done = run_raw(self.fx, "--budget-sec", value)
                self.assertEqual(done.returncode, 1)
                self.assertEqual(done.stdout, "")


if __name__ == "__main__":
    unittest.main()
