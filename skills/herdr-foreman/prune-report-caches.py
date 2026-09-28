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
`outside_root`, and a candidate that is itself a symlink as `symlink`. A
candidate that is not a directory is counted `missing`. Everything after that
runs on file descriptors: ROOT is opened once, the candidate is opened from
it one component at a time with `O_NOFOLLOW` (a component turned symlink is
`symlink`), and every survey read, signature check, rename and removal below
it is `openat`/`fstatat`/`unlinkat` relative to a descriptor reached the same
way. No path is ever re-resolved by name, so a symlink swapped in for any
directory mid-run is never entered: the open fails and the entry is `failed`.

Idleness — a candidate is idle when both hold:
  * no active supervision enrollment (`foreman/supervision.py` `load`, the
    `members[]` rows with `active` true) names a report inside it, or inside
    a directory containing it; an unreadable supervision store is a
    `could_not_check` and nothing is removed;
  * the newest `lstat` mtime of the directory itself and of every entry
    below it, caches included, is at least IDLE_HOURS older than now
    (`--now` injects the clock).
A candidate that is not idle is skipped whole (`active_assignment` or
`not_idle`), so a live worker's build cache is never pulled out mid-build. A
candidate below another idle candidate is covered by that walk. Each live
rename to a tombstone happens under the supervision store's owner lock
(`foreman/state.py` `state_lock` on `supervision.store_path`) after the
active enrollments are re-read under it; a lock held by a foreman command,
or a store unreadable at that moment, skips the rest of the candidate as
`busy` for the next run.

Cache directories — inside an idle candidate, a directory is removed whole
when its basename and its content signature both match one row of
CACHE_KINDS (names and signatures there). A signature also limits the
directory's top-level entries to the kind's own (the *_FILES / *_DIRS sets):
a cache holding a report, a note or a log is not a pure cache and stays
whole. The plugin-cache copy row matches a `plugins/cache` directory, holding
directories alone, whose grandparent is one of PLUGIN_HOMES: a copy of an
agent home's plugin cache taken by a home guard. Every other file and
directory stays, evidence included. The signature is re-checked on the
descriptor immediately before removal. Directories inside a removed cache are
made owner-writable first (the Go module cache is read-only by design).

Interrupted removal — before removal starts, a cache gets a TOMBSTONE_MARKER
file naming its kind, then is renamed to its name plus TOMBSTONE_SUFFIX; the
marker is removed last. A run finding a directory with that suffix AND a
marker naming a known kind removes it as `interrupted-removal`. A run finding
a cache still at its own name with a marker naming its kind (cut short
between marking and renaming) reuses that marker. A suffixed directory
without a valid marker is not a cache and stays.

Budget — `--budget-sec` stops starting new work once that many seconds have
passed and reports `incomplete: true`; a re-run continues where it stopped.

Contract:
  argv  : [--dry-run] [--root DIR] [--state FILE] [--now EPOCH]
          [--budget-sec N] (EPOCH and N finite, N positive).
  stdin : not read.
  stdout: one JSON object:
            {"schema_version": 1, "dry_run": bool, "root": str,
             "reports_dirs": int, "missing": int,
             "caches": [{"path": str, "kind": str, "bytes": int}],
             "bytes": int,
             "skipped": [{"path": str,
                          "reason": "not_idle"|"active_assignment"|"busy"|
                                    "outside_root"|"symlink"}],
             "failed": [{"path": str, "error": str}],
             "incomplete": bool, "could_not_check": str|null}
          `caches` lists what was removed (under --dry-run, what would be),
          `bytes` their allocated size. `could_not_check` names why the
          ledger, the supervision store or ROOT could not be read; nothing
          is removed then.
  stderr: diagnostics.
  exit  : 0 done; 2 when any removal failed; 3 on could_not_check (the
          JSON is printed either way, and stderr names the cause); 1 on a
          usage error. An absent state file is no prior state, exit 0.
"""

import argparse
import errno
import json
import math
import os
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from foreman import home
from foreman import state as ledger
from foreman import supervision
from foreman.errors import ForemanError

SCHEMA_VERSION = 1

#: Hours without any modification below a reports directory before it is idle.
IDLE_HOURS = 24

#: The first line the Go toolchain writes into every build cache's README.
GO_BUILD_README = b"This directory holds cached build artifacts from the Go build system."

#: Directory names a copied agent home carries its plugin cache under.
PLUGIN_HOMES = frozenset({".claude", ".codex", "codex-home"})

#: A cache is renamed to `<name>` + this suffix before its removal starts.
TOMBSTONE_SUFFIX = ".prune-report-caches-removing"
TOMBSTONE = "interrupted-removal"

#: The file inside a marked cache or a tombstone naming the kind of cache it is.
TOMBSTONE_MARKER = ".prune-report-caches-kind"

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW


def _lstat_at(dir_fd, name):
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _is_dir_at(dir_fd, name):
    st = _lstat_at(dir_fd, name)
    return st is not None and stat.S_ISDIR(st.st_mode)


def _is_file_at(dir_fd, name):
    st = _lstat_at(dir_fd, name)
    return st is not None and stat.S_ISREG(st.st_mode)


def _head_at(dir_fd, name, size):
    """The first `size` bytes of regular file `name`, or None."""
    if not _is_file_at(dir_fd, name):
        return None
    fd = os.open(name, FILE_FLAGS, dir_fd=dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, size)
    finally:
        os.close(fd)


def open_at(dir_fd, name):
    """Open directory `name` in `dir_fd` without following a symlink."""
    return os.open(name, DIR_FLAGS, dir_fd=dir_fd)


def open_rel(base_fd, parts):
    """Open the directory `parts` below `base_fd`, one no-follow step each."""
    fd = os.dup(base_fd)
    for part in parts:
        try:
            child = open_at(fd, part)
        finally:
            os.close(fd)
        fd = child
    return fd


def _top(fd):
    """{name: mode} of the entries directly inside, the kind marker left out."""
    with os.scandir(fd) as entries:
        return {entry.name: entry.stat(follow_symlinks=False).st_mode for entry in entries
                if entry.name != TOMBSTONE_MARKER}


def _only(fd, files=frozenset(), dirs=frozenset(), any_dir=False, dir_name=None):
    """Every top-level entry is one of `files` (regular), one of `dirs`, or
    any directory when `any_dir`, or a directory `dir_name` accepts."""
    for name, mode in _top(fd).items():
        if stat.S_ISREG(mode) and name in files:
            continue
        if stat.S_ISDIR(mode) and (name in dirs or any_dir or (dir_name is not None and dir_name(name))):
            continue
        return False
    return True


#: Top-level entries each cache kind may hold; anything else (a report, a
#: note, a log) means the directory is not a pure cache and it stays.
GO_BUILD_FILES = frozenset({"README", "trim.txt", "testexpire.txt"})
PIP_ENTRIES = frozenset({"http", "http-v2", "wheels", "selfcheck"})
PIP_FILES = frozenset({"selfcheck.json"})
NPM_DIRS = frozenset({"_cacache", "_logs", "_npx", "_prebuilds"})
NPM_FILES = frozenset({"_update-notifier-last-checked", "anonymous-cli-metrics.json"})
VENV_DIRS = frozenset({"bin", "lib", "lib64", "include", "share", "etc", "Scripts", "Lib", "Include"})
VENV_FILES = frozenset({"pyvenv.cfg", ".gitignore", "CACHEDIR.TAG", ".lock"})
NODE_MODULES_FILES = frozenset({".package-lock.json", ".yarn-integrity", ".modules.yaml"})
HEX = frozenset("0123456789abcdef")


def _go_build(fd):
    return (_head_at(fd, "README", len(GO_BUILD_README)) == GO_BUILD_README
            and _only(fd, files=GO_BUILD_FILES, dir_name=lambda name: len(name) == 2 and set(name) <= HEX))


def _go_module(fd):
    if not _is_dir_at(fd, "cache"):
        return False
    cache = open_at(fd, "cache")
    try:
        return _is_dir_at(cache, "download") and _only(fd, any_dir=True)
    finally:
        os.close(cache)


def _pip(fd):
    return any(_is_dir_at(fd, name) for name in PIP_ENTRIES) and _only(fd, files=PIP_FILES, dirs=PIP_ENTRIES)


def _npm(fd):
    return _is_dir_at(fd, "_cacache") and _only(fd, files=NPM_FILES, dirs=NPM_DIRS)


def _venv(fd):
    return _is_file_at(fd, "pyvenv.cfg") and _only(fd, files=VENV_FILES, dirs=VENV_DIRS)


def _node_modules(fd):
    return _is_file_at(fd, ".package-lock.json") and _only(fd, files=NODE_MODULES_FILES, any_dir=True)


def _bytecode(fd, top=True):
    """Only directories and `.pyc` regular files, all the way down."""
    with os.scandir(fd) as entries:
        listed = [(entry.name, entry.stat(follow_symlinks=False).st_mode) for entry in entries]
    for name, mode in listed:
        if top and name == TOMBSTONE_MARKER:
            continue
        if stat.S_ISDIR(mode):
            child = open_at(fd, name)
            try:
                if not _bytecode(child, top=False):
                    return False
            finally:
                os.close(child)
        elif not (stat.S_ISREG(mode) and name.endswith(".pyc")):
            return False
    return True


#: kind -> (basenames, signature over the directory's descriptor). A directory
#: is a cache only when both match.
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
KNOWN_KINDS = frozenset(CACHE_KINDS) | {PLUGIN_CACHE_COPY}


def marker_kind(fd):
    """The known kind a TOMBSTONE_MARKER directly inside names, or None."""
    head = _head_at(fd, TOMBSTONE_MARKER, 64)
    if head is None:
        return None
    kind = head.decode("ascii", "replace").strip()
    return kind if kind in KNOWN_KINDS else None


def classify_at(parent_fd, name, parts):
    """The kind of directory `name` in `parent_fd` (at `parts` below the
    candidate), or None."""
    tomb = name.endswith(TOMBSTONE_SUFFIX)
    copy = len(parts) >= 3 and name == "cache" and parts[-2] == "plugins" and parts[-3] in PLUGIN_HOMES
    if not tomb and not copy and not any(name in names for names, _signature in CACHE_KINDS.values()):
        return None
    fd = open_at(parent_fd, name)
    try:
        if tomb:
            return TOMBSTONE if marker_kind(fd) else None
        if copy:
            return PLUGIN_CACHE_COPY if _only(fd, any_dir=True) else None
        for kind, (names, signature) in CACHE_KINDS.items():
            if name in names and signature(fd):
                return kind
        return None
    finally:
        os.close(fd)


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


def survey(cand_fd, cutoff, budget):
    """(idle, caches) for one opened reports directory; caches as parts tuples
    below it with their kind."""
    if os.fstat(cand_fd).st_mtime > cutoff:
        return False, []
    caches = []
    root_parts: tuple[str, ...] = ()
    stack: list[tuple[tuple[str, ...], bool]] = [(root_parts, False)]
    while stack:
        budget.check()
        parts, inside = stack.pop()
        fd = open_rel(cand_fd, parts)
        try:
            with os.scandir(fd) as entries:
                listed = [(entry.name, entry.stat(follow_symlinks=False)) for entry in entries]
            for name, info in listed:
                if info.st_mtime > cutoff:
                    return False, []
                if not stat.S_ISDIR(info.st_mode):
                    continue
                child = parts + (name,)
                kind = None if inside else classify_at(fd, name, child)
                if kind:
                    caches.append((child, kind))
                stack.append((child, inside or bool(kind)))
        finally:
            os.close(fd)
    return True, caches


def _same(a, b):
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _changed():
    return OSError(errno.EAGAIN, "changed while being removed; re-run to retry")


def remove_at(dir_fd, name, dry_run, marker_last=False):
    """Allocated bytes of entry `name` in directory `dir_fd`; removed unless dry_run.

    Never follows a symlink: a link is unlinked (or counted), never entered.
    A directory whose identity changed between `lstat` and open raises.
    """
    freed = 0
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if stat.S_ISDIR(st.st_mode):
        fd = open_at(dir_fd, name)
        try:
            if not _same(os.fstat(fd), st):
                raise _changed()
            if not dry_run and (st.st_mode & stat.S_IRWXU) != stat.S_IRWXU:
                os.fchmod(fd, st.st_mode | stat.S_IRWXU)
            with os.scandir(fd) as entries:
                children = sorted((entry.name for entry in entries),
                                  key=lambda child: marker_last and child == TOMBSTONE_MARKER)
            for child in children:
                freed += remove_at(fd, child, dry_run)
        finally:
            os.close(fd)
        if not dry_run:
            os.rmdir(name, dir_fd=dir_fd)
    elif not dry_run:
        os.unlink(name, dir_fd=dir_fd)
    return freed + st.st_blocks * 512


def mark(cache_fd, kind):
    """Write the kind marker, or accept one an interrupted run already wrote."""
    try:
        fd = os.open(TOMBSTONE_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=cache_fd)
    except FileExistsError:
        if marker_kind(cache_fd) != kind:
            raise FileExistsError(errno.EEXIST, "holds a {} that does not name {}; remove it by hand".format(
                TOMBSTONE_MARKER, kind)) from None
        return
    try:
        os.write(fd, kind.encode("ascii") + b"\n")
    finally:
        os.close(fd)


def entomb(parent_fd, name, kind):
    """Mark cache `name` with its kind and rename it to a tombstone; the
    tombstone's name."""
    tomb = name + TOMBSTONE_SUFFIX
    if _lstat_at(parent_fd, tomb) is not None:
        raise FileExistsError(errno.EEXIST, "a leftover {} is in the way; remove it by hand".format(tomb))
    st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    cache_fd = open_at(parent_fd, name)
    try:
        if not _same(os.fstat(cache_fd), st):
            raise _changed()
        if (st.st_mode & stat.S_IRWXU) != stat.S_IRWXU:
            os.fchmod(cache_fd, st.st_mode | stat.S_IRWXU)
        mark(cache_fd, kind)
        os.rename(name, tomb, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        if not _same(os.stat(tomb, dir_fd=parent_fd, follow_symlinks=False), st):
            raise _changed()
    finally:
        os.close(cache_fd)
    return tomb


def within(path, root):
    return path != root and path.startswith(root.rstrip(os.sep) + os.sep)


def busy(real, active):
    """Whether an active enrollment's report directory overlaps `real`."""
    return any(dir_ == real or within(dir_, real) or within(real, dir_) for dir_ in active)


def active_report_dirs(state_path):
    """Resolved directories of every active supervision enrollment's report."""
    data = supervision.load(state_path)
    return [os.path.realpath(os.path.dirname(row["assignment"]["report"]))
            for row in data["members"] if row["active"]]


def load_ledger(state_path, default):
    """(document, active report dirs, None) or (None, None, why) — read-only."""
    def warn(message):
        print("prune-report-caches: " + message, file=sys.stderr)

    def read():
        document, usable = ledger.load_state_checked(state_path, warn=warn, persist_migration=False)
        if not usable:
            return None, None, "the foreman state file {} is unreadable or from a newer build".format(state_path)
        return document, active_report_dirs(state_path), None

    try:
        if default:
            with home.guard(False):
                home.require_current({"state"})
                return read()
        return read()
    except ForemanError as exc:
        return None, None, exc.message


def prune_candidate(root_fd, real_root, real, cutoff, budget, dry_run, state_path, result):
    """Survey one resolved candidate and remove its caches when idle.

    Each live removal renames its cache to a tombstone while holding the
    supervision store's owner lock, after re-reading the active enrollments
    under it, so no enrollment lands between the check and the rename. A
    locked or unreadable store stops the candidate (`busy`).
    Returns True when the candidate was idle (its walk covers descendants).
    """
    parts = tuple(os.path.relpath(real, real_root).split(os.sep))
    try:
        cand_fd = open_rel(root_fd, parts)
    except FileNotFoundError:
        result["missing"] += 1
        return False
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            result["skipped"].append({"path": real, "reason": "symlink"})
            return False
        raise
    try:
        idle, caches = survey(cand_fd, cutoff, budget)
        if not idle:
            result["skipped"].append({"path": real, "reason": "not_idle"})
            return False
        for cache_parts, kind in caches:
            budget.check()
            path = os.path.join(real, *cache_parts)
            try:
                parent_fd = open_rel(cand_fd, cache_parts[:-1])
                try:
                    name = cache_parts[-1]
                    if not _is_dir_at(parent_fd, name) or classify_at(parent_fd, name, cache_parts) != kind:
                        continue
                    if not dry_run and kind != TOMBSTONE:
                        try:
                            with ledger.state_lock(supervision.store_path(state_path)):
                                if busy(real, active_report_dirs(state_path)):
                                    result["skipped"].append({"path": real, "reason": "active_assignment"})
                                    return True
                                name = entomb(parent_fd, name, kind)
                        except ForemanError:
                            result["skipped"].append({"path": real, "reason": "busy"})
                            return True
                    size = remove_at(parent_fd, name, dry_run, marker_last=True)
                finally:
                    os.close(parent_fd)
            except OSError as exc:
                result["failed"].append({"path": path, "error": exc.strerror or str(exc)})
                continue
            result["caches"].append({"path": path, "kind": kind, "bytes": size})
            result["bytes"] += size
        return True
    finally:
        os.close(cand_fd)


def run(args):
    result = {"schema_version": SCHEMA_VERSION, "dry_run": args.dry_run, "root": None, "reports_dirs": 0,
              "missing": 0, "caches": [], "bytes": 0, "skipped": [], "failed": [], "incomplete": False,
              "could_not_check": None}
    root = args.root or str(home.roots()["state"])
    result["root"] = root
    state_path = args.state or str(ledger.default_state_path())
    document, active, why = load_ledger(state_path, args.state is None)
    if document is None or active is None:
        result["could_not_check"] = why
        return result
    real_root = os.path.realpath(root)
    try:
        root_fd = os.open(real_root, DIR_FLAGS)
    except FileNotFoundError:
        root_fd = None
    except OSError as exc:
        result["could_not_check"] = "the state root {} cannot be opened: {}".format(real_root, exc.strerror)
        return result
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
            if root_fd is None:
                result["missing"] += 1
                continue
            if any(within(real, done) for done in covered):
                continue
            if busy(real, active):
                result["skipped"].append({"path": real, "reason": "active_assignment"})
                continue
            try:
                if prune_candidate(root_fd, real_root, real, cutoff, budget, args.dry_run, state_path, result):
                    covered.append(real)
            except OSError as exc:
                result["failed"].append({"path": real, "error": exc.strerror or str(exc)})
    except OutOfBudget:
        result["incomplete"] = True
    finally:
        if root_fd is not None:
            os.close(root_fd)
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
    if args.budget_sec is not None and not (math.isfinite(args.budget_sec) and args.budget_sec > 0):
        parser.error("--budget-sec must be a finite positive number of seconds")
    if args.now is not None and not math.isfinite(args.now):
        parser.error("--now must be a finite epoch time")
    return args


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    result = run(args)
    print(json.dumps(result))
    if result["could_not_check"]:
        print("prune-report-caches: nothing removed — {}; restore it, then re-run".format(
            result["could_not_check"]), file=sys.stderr)
        return 3
    for failure in result["failed"]:
        print("prune-report-caches: could not remove {}: {} — check its permissions, then re-run".format(
            failure["path"], failure["error"]), file=sys.stderr)
    return 2 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
