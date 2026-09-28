#!/usr/bin/env python3
"""Remove regenerable build and package caches from idle Herdr reports directories.

Workers pointed GOCACHE, GOMODCACHE, PIP_CACHE_DIR, npm's cache and their
virtualenvs at the round's reports directory, and a tester's home guard copied
whole plugin caches there before and after each run. The evidence (reports,
logs, JSON receipts, diffs) is kilobytes; the caches reached hundreds of
gigabytes (#622). Which directories are safe to delete is one right answer per
input, so the decision lives here (`rules/script-delegation.md`).

Discovery — reports directories come from the ledger, never from a guess.
Every `recovery.dispatches[]` row in the foreman state file records the
`brief` and `common` paths `compose-briefs.sh` wrote into the round's reports
directory (frozen copies sit in its `.dispatched/` subdirectory, whose parent
is taken). The distinct directories those paths name are the candidates. The
state file is read through the owner's loader (`foreman/state.py`
`load_state_checked`) with `persist_migration=False`: nothing is written or
migrated, and a file needing migration, a corrupt one, or one from a newer
build is a `could_not_check`, never an empty ledger. A default state path is
read under the owner's shared home guard (`foreman/home.py` `guard`).

Containment — a candidate is resolved (`os.path.realpath`) and must lie
strictly below the resolved ROOT (default `$XDG_STATE_HOME`, else
`~/.local/state`, `--root` overrides); anything else is skipped as
`outside_root`, and a candidate that is itself a symlink as `symlink`. A candidate that is not a directory is counted `missing`.
Nothing below a candidate is followed through a symlink: every entry is read
with `lstat`, a symlink is never descended into, and a cache directory is
re-proven a real directory with its signature immediately before removal.

Idleness — a candidate is idle when the newest `lstat` mtime of the directory
itself and of every entry below it, caches included, is at least IDLE_HOURS
older than now (`--now` injects the clock). A candidate that is not idle is
skipped whole, so a live worker's build cache is never pulled out mid-build.
A candidate below another idle candidate is covered by that walk.

Cache directories — inside an idle candidate, a directory is removed whole
when its basename and its content signature both match one row of
CACHE_KINDS (names and signatures there). The plugin-cache copy row matches a
`plugins/cache` directory whose grandparent is one of PLUGIN_HOMES: a copy of
an agent home's plugin cache taken by a home guard. Every other file and
directory stays, evidence included. Directories inside a removed cache are
made owner-writable first (the Go module cache is read-only by design).
A cache is renamed to its name plus TOMBSTONE_SUFFIX before removal starts;
a directory carrying that suffix is removed as `interrupted-removal`, so a
removal a killed run cut short is finished by the next one.

Budget — `--budget-sec` stops starting new work once that many seconds have
passed and reports `incomplete: true`; a re-run continues where it stopped.

Contract:
  argv  : [--dry-run] [--root DIR] [--state FILE] [--now EPOCH]
          [--budget-sec N]
  stdin : not read.
  stdout: one JSON object:
            {"schema_version": 1, "dry_run": bool, "root": str,
             "reports_dirs": int, "missing": int,
             "caches": [{"path": str, "kind": str, "bytes": int}],
             "bytes": int,
             "skipped": [{"path": str,
                          "reason": "not_idle"|"outside_root"|"symlink"}],
             "failed": [{"path": str, "error": str}],
             "incomplete": bool, "could_not_check": str|null}
          `caches` lists what was removed (under --dry-run, what would be),
          `bytes` their allocated size. `could_not_check` names why the
          ledger could not be read; nothing is removed then.
  stderr: diagnostics.
  exit  : 0 done (including could_not_check); 2 when any removal failed
          (the JSON still names each); 1 on a usage error.
"""

import argparse
import json
import os
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from foreman import home
from foreman import state as ledger
from foreman.errors import ForemanError

SCHEMA_VERSION = 1

#: Hours without any modification below a reports directory before it is idle.
IDLE_HOURS = 24

#: The first line the Go toolchain writes into every build cache's README.
GO_BUILD_README = b"This directory holds cached build artifacts from the Go build system."

#: Directory names a copied agent home carries its plugin cache under.
PLUGIN_HOMES = frozenset({".claude", ".codex", "codex-home"})

#: A cache is renamed to `<name>` + this suffix before its removal starts, so
#: a removal cut short leaves a directory the next run recognizes and finishes.
TOMBSTONE_SUFFIX = ".prune-report-caches-removing"
TOMBSTONE = "interrupted-removal"


def _lstat(path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _is_dir(path):
    st = _lstat(path)
    return st is not None and stat.S_ISDIR(st.st_mode)


def _is_file(path):
    st = _lstat(path)
    return st is not None and stat.S_ISREG(st.st_mode)


def _go_build(path):
    readme = os.path.join(path, "README")
    if not _is_file(readme):
        return False
    with open(readme, "rb") as handle:
        return handle.read(len(GO_BUILD_README)) == GO_BUILD_README


def _go_module(path):
    return _is_dir(os.path.join(path, "cache")) and _is_dir(os.path.join(path, "cache", "download"))


def _pip(path):
    return any(_is_dir(os.path.join(path, name)) for name in ("http", "http-v2", "wheels", "selfcheck"))


def _npm(path):
    return _is_dir(os.path.join(path, "_cacache"))


def _venv(path):
    return _is_file(os.path.join(path, "pyvenv.cfg"))


def _node_modules(path):
    return _is_file(os.path.join(path, ".package-lock.json"))


def _bytecode(path):
    """Only directories and `.pyc` regular files, all the way down."""
    stack = [path]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif not (entry.is_file(follow_symlinks=False) and entry.name.endswith(".pyc")):
                    return False
    return True


#: kind -> (basenames, signature). A directory is a cache only when both match.
CACHE_KINDS = {
    "go-build-cache": (frozenset({"go-cache", "gocache", "go-build", "go-build-cache"}), _go_build),
    "go-module-cache": (frozenset({"go-mod-cache", "go-modcache", "gomodcache", "modcache"}), _go_module),
    "pip-cache": (frozenset({"pip-cache", "pipcache", "pip"}), _pip),
    "npm-cache": (frozenset({"npm-cache", ".npm"}), _npm),
    "virtualenv": (frozenset({"venv", ".venv"}), _venv),
    "node-modules": (frozenset({"node_modules"}), _node_modules),
    "python-bytecode": (frozenset({"__pycache__", "pycache"}), _bytecode),
}

PLUGIN_CACHE_COPY = "plugin-cache-copy"


def classify(path, top):
    """The CACHE_KINDS kind of directory `path` below `top`, or None."""
    name = os.path.basename(path)
    rel = os.path.relpath(path, top).split(os.sep)
    if name.endswith(TOMBSTONE_SUFFIX):
        return TOMBSTONE
    if name == "cache" and len(rel) >= 3 and rel[-2] == "plugins" and rel[-3] in PLUGIN_HOMES:
        return PLUGIN_CACHE_COPY
    for kind, (names, signature) in CACHE_KINDS.items():
        if name in names and signature(path):
            return kind
    return None


def reports_dirs(document):
    """Distinct reports directories named by the ledger's dispatch rows."""
    found = set()
    for row in document.get("recovery", {}).get("dispatches", []):
        for key in ("brief", "common"):
            value = row.get(key)
            if not isinstance(value, str) or not os.path.isabs(value):
                continue
            parent = os.path.dirname(os.path.normpath(value))
            if os.path.basename(parent) == ".dispatched":
                parent = os.path.dirname(parent)
            found.add(parent)
    return sorted(found)


class OutOfBudget(Exception):
    """The run's budget is spent; stop starting new work."""


class Budget:
    def __init__(self, seconds):
        self.deadline = None if seconds is None else time.monotonic() + seconds

    def check(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise OutOfBudget()


def survey(top, cutoff, budget):
    """(idle, caches) for one reports directory; caches as (path, kind)."""
    st = os.lstat(top)
    if st.st_mtime > cutoff:
        return False, []
    caches = []
    stack = [(top, False)]
    while stack:
        budget.check()
        current, inside = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if info.st_mtime > cutoff:
                    return False, []
                if not stat.S_ISDIR(info.st_mode):
                    continue
                kind = None if inside else classify(entry.path, top)
                if kind:
                    caches.append((entry.path, kind))
                stack.append((entry.path, inside or bool(kind)))
    return True, caches


def remove_tree(path, dry_run):
    """Allocated bytes below and including `path`; removed unless dry_run.

    Never follows a symlink: a link is unlinked (or counted), never entered.
    """
    freed = 0
    st = os.lstat(path)
    if stat.S_ISDIR(st.st_mode):
        if not dry_run and (st.st_mode & stat.S_IRWXU) != stat.S_IRWXU:
            os.chmod(path, st.st_mode | stat.S_IRWXU)
        with os.scandir(path) as entries:
            children = [entry.path for entry in entries]
        for child in children:
            freed += remove_tree(child, dry_run)
        if not dry_run:
            os.rmdir(path)
    elif not dry_run:
        os.unlink(path)
    return freed + st.st_blocks * 512


def remove_cache(path, kind, dry_run):
    """Rename to a tombstone first, then remove; bytes freed."""
    if dry_run or kind == TOMBSTONE:
        return remove_tree(path, dry_run)
    tombstone = path + TOMBSTONE_SUFFIX
    if _lstat(tombstone) is not None:
        raise FileExistsError(17, "a leftover {} is in the way; remove it by hand".format(tombstone))
    os.rename(path, tombstone)
    return remove_tree(tombstone, dry_run)


def within(path, root):
    return path != root and path.startswith(root.rstrip(os.sep) + os.sep)


def load_ledger(state_path, default):
    """(document, None) or (None, why) — read-only, never migrating."""
    def warn(message):
        print("prune-report-caches: " + message, file=sys.stderr)

    def read():
        document, usable = ledger.load_state_checked(state_path, warn=warn, persist_migration=False)
        if not usable:
            return None, "the foreman state file {} is unreadable or from a newer build".format(state_path)
        return document, None

    try:
        if default:
            with home.guard(False):
                home.require_current({"state"})
                return read()
        return read()
    except ForemanError as exc:
        return None, exc.message


def run(args):
    result = {"schema_version": SCHEMA_VERSION, "dry_run": args.dry_run, "root": None, "reports_dirs": 0,
              "missing": 0, "caches": [], "bytes": 0, "skipped": [], "failed": [], "incomplete": False,
              "could_not_check": None}
    root = args.root or str(home.roots()["state"])
    result["root"] = root
    state_path = args.state or str(ledger.default_state_path())
    document, why = load_ledger(state_path, args.state is None)
    if document is None:
        result["could_not_check"] = why
        return result
    real_root = os.path.realpath(root)
    now = time.time() if args.now is None else args.now
    cutoff = now - IDLE_HOURS * 3600
    budget = Budget(args.budget_sec)
    candidates = reports_dirs(document)
    result["reports_dirs"] = len(candidates)
    covered = []
    try:
        for candidate in candidates:
            budget.check()
            if os.path.islink(candidate):
                result["skipped"].append({"path": candidate, "reason": "symlink"})
                continue
            real = os.path.realpath(candidate)
            if not within(real, real_root):
                result["skipped"].append({"path": candidate, "reason": "outside_root"})
                continue
            if not _is_dir(real):
                result["missing"] += 1
                continue
            if any(within(real, done) for done in covered):
                continue
            try:
                idle, caches = survey(real, cutoff, budget)
            except OSError as exc:
                result["failed"].append({"path": real, "error": exc.strerror or str(exc)})
                continue
            if not idle:
                result["skipped"].append({"path": real, "reason": "not_idle"})
                continue
            covered.append(real)
            for path, kind in caches:
                budget.check()
                try:
                    if not _is_dir(path) or classify(path, real) != kind:
                        continue
                    size = remove_cache(path, kind, args.dry_run)
                except OSError as exc:
                    result["failed"].append({"path": path, "error": exc.strerror or str(exc)})
                    continue
                result["caches"].append({"path": path, "kind": kind, "bytes": size})
                result["bytes"] += size
    except OutOfBudget:
        result["incomplete"] = True
    return result


class Parser(argparse.ArgumentParser):
    """argparse, with a usage error exiting 1 as the contract says, not 2."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, "prune-report-caches: {} — see --help\n".format(message))


def parse(argv):
    parser = Parser(prog="prune-report-caches.py",
                                     description="Remove build and package caches from idle Herdr reports directories.")
    parser.add_argument("--dry-run", action="store_true", help="report what would be removed; remove nothing")
    parser.add_argument("--root", help="state root the reports directories must lie below")
    parser.add_argument("--state", help="foreman state file (default: the foreman's default state path)")
    parser.add_argument("--now", type=float, help="current time as epoch seconds (tests)")
    parser.add_argument("--budget-sec", type=float, help="stop starting new work after this many seconds")
    args = parser.parse_args(argv)
    if args.budget_sec is not None and args.budget_sec <= 0:
        parser.error("--budget-sec must be positive")
    return args


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    result = run(args)
    print(json.dumps(result))
    for failure in result["failed"]:
        print("prune-report-caches: could not remove {}: {} — check its permissions, then re-run".format(
            failure["path"], failure["error"]), file=sys.stderr)
    return 2 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
