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
  8. Interrupted removal -> a leftover tombstone is finished.
  9. Unusable ledger     -> could_not_check, nothing removed, exit 0.
 10. No ledger           -> nothing to do, exit 0.
 11. Out of budget       -> incomplete, nothing started after the budget.
 12. Usage error         -> a non-positive budget exits 1.
"""

import argparse
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "prune-report-caches.py"
sys.path.insert(0, str(HERE.parent))

from foreman import state as ledger

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

    def record(self, reports_dir):
        """A dispatch row naming a frozen brief under `reports_dir`."""
        index = len(self.rows)
        brief = str(Path(reports_dir) / ".dispatched" / "brief-{}.md".format(index))
        self.rows.append({"schema_version": 1, "at": AT, "id": "d{}".format(index), "fingerprint": "f{}".format(index),
                          "task": "t{}".format(index), "role": "developer", "agent": "w{}".format(index),
                          "fix_round": None, "status": "not_sent", "plan": None, "work": None,
                          "brief": brief, "common": str(Path(reports_dir) / "COMMON.md"),
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
        self.fx.record(self.fx.root / "round" / "gone")
        self.fx.save()
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["reports_dirs"], doc["missing"], doc["caches"]), (1, 1, []))

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

    def test_leftover_tombstone_is_finished(self):
        top = self.fx.reports(caches=())
        write(top / "developer-evidence" / "venv.prune-report-caches-removing" / "lib" / "x.py")
        self.fx.save()
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.lexists(top / "developer-evidence" / "venv.prune-report-caches-removing"))
        self.assertEqual([c["kind"] for c in doc["caches"]], ["interrupted-removal"])

    def test_unusable_ledger_removes_nothing(self):
        top = self.fx.reports()
        self.fx.save()
        self.fx.state.write_text("{not json")
        age(top)
        rc, doc, err = run(self.fx)
        self.assertEqual(rc, 0, err)
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

    def test_non_positive_budget_is_a_usage_error(self):
        done = run_raw(self.fx, "--budget-sec", "0")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")


if __name__ == "__main__":
    unittest.main()
