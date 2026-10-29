#!/usr/bin/env python3
"""Clean opted-in disposable scratch files at native SessionEnd.

Native input is JSON on stdin, bounded by MAX_INPUT_CHARS. stdout is one JSON
result {status, reason, removed}; no result asks the model to continue. Completed
cleanup and expected refusals exit 0; incomplete cleanup exits 1. Diagnostics go
to stderr, never include input contents, and never block native SessionEnd.

Only .coding-policy-disposable beneath the native scratchpad_dir is disposable.
Legacy scratch files, outputs, handoff material and other sessions stay put.
The native runtime owns session_id/scratchpad_dir: Claude Code hook common-input
and scratchpad contracts (v2.1.257+, code.claude.com/docs/en/hooks and
code.claude.com/docs/en/claude-directory#session-scratchpad-directory).
Hosts without that native field supply no cleanup target. No path is inferred
from cwd or a transcript. Clear/resume handoffs and explicit background work
retain their disposable files. No global scan or persistent cleanup state.

Directory handles bind every step; links inside the disposable tree are
unlinked, never followed. All directories/entries must belong to the caller.
Budget/depth limits leave residuals for native retention or later cleanup.
"""

from contextlib import ExitStack
import errno
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time


DISPOSABLE = ".coding-policy-disposable"
MAX_INPUT_CHARS = 65_536
BUDGET_SECONDS = 0.75
MAX_ENTRIES = 25_000
MAX_DEPTH = 64
EXIT_REASONS = frozenset({"other", "logout", "prompt_input_exit"})
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


class _Unsafe(Exception):
    """Unverified local ownership or a changed directory; preserve it."""


class _Budget(Exception):
    """The native handoff must not wait for unbounded filesystem work."""


class _Progress:
    def __init__(self, clock):
        self.clock = clock
        self.deadline = clock() + BUDGET_SECONDS
        self.entries = 0
        self.removed = 0

    def check(self, depth):
        if depth > MAX_DEPTH or self.entries >= MAX_ENTRIES or self.clock() >= self.deadline:
            raise _Budget()
        self.entries += 1


def _result(status, reason, removed=0):
    return {"status": status, "reason": reason, "removed": removed}


def _roots(environ, uid):
    """Native UID temp tree, or an explicitly configured native temp root."""
    name = "claude-" + str(uid)
    if sys.platform == "darwin":
        roots = [Path("/private/tmp") / name, Path("/tmp") / name]
    else:
        roots = [Path(tempfile.gettempdir()) / name]
    custom = environ.get("CLAUDE_CODE_TMPDIR")
    if isinstance(custom, str) and custom and "\0" not in custom:
        base = Path(custom)
        if base.is_absolute() and ".." not in base.parts:
            roots.extend((base / name, base))
    return roots


def _owned(info, uid, *, directory=False):
    if info.st_uid != uid or directory and (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022):
        raise _Unsafe()


def _open_directory(path, uid, *, parent=None, expected=None):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(path, flags, dir_fd=parent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _Unsafe() from error
        raise
    try:
        info = os.fstat(fd)
        if uid is not None:
            _owned(info, uid, directory=True)
        if expected is not None and (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino):
            raise _Unsafe()
    except (OSError, _Unsafe):
        os.close(fd)
        raise
    return fd


def _open_root(root, uid, handles, progress):
    # macOS supplies these fixed system aliases; no other link is resolved.
    parts = root.parts
    if sys.platform == "darwin" and len(parts) > 1 and parts[1] in ("tmp", "var"):
        parts = ("/", "private", *parts[1:])
    fd = _open_directory("/", None)
    handles.callback(os.close, fd)
    bindings = []
    for index, component in enumerate(parts[1:], 1):
        progress.check(0)
        child = _open_directory(component, uid if index == len(parts) - 1 else None, parent=fd)
        handles.callback(os.close, child)
        bindings.append((fd, component, os.fstat(child)))
        fd = child
    return fd, tuple(bindings)


def _verify(bindings):
    for parent, name, expected in bindings:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino) or not stat.S_ISDIR(current.st_mode):
            raise _Unsafe()


def _remove(parent, name, uid, progress, depth=0, *, expected=None, bindings=()):
    progress.check(depth)
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    _owned(info, uid)
    if expected is not None and ((info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino)
                                 or not stat.S_ISDIR(info.st_mode)):
        raise _Unsafe()
    if stat.S_ISDIR(info.st_mode):
        _verify(bindings)
        fd = _open_directory(name, uid, parent=parent, expected=info)
        children = bindings + ((parent, name, info),)
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    _remove(fd, entry.name, uid, progress, depth + 1, bindings=children)
            progress.check(depth)
            _verify(children)
            os.rmdir(name, dir_fd=parent)
        finally:
            os.close(fd)
    else:
        # unlink does not open a FIFO, device or symlink target.
        _verify(bindings)
        os.unlink(name, dir_fd=parent)
    progress.removed += 1


def cleanup(payload, environ=None, *, clock=time.monotonic):
    """Return a bounded local result; only native opted-in storage is removed."""
    environ = os.environ if environ is None else environ
    if not isinstance(payload, dict) or payload.get("hook_event_name") not in ("SessionEnd", "sessionEnd"):
        return _result("skipped", "not_session_end")
    reason = payload.get("reason")
    if not isinstance(reason, str) or reason not in EXIT_REASONS:
        return _result("skipped", "resumable_or_unknown_exit")
    if (environ.get("CLAUDE_JOB_DIR") or payload.get("background_tasks") or payload.get("session_crons")
            or payload.get("background") or payload.get("is_background") or payload.get("agent_id")):
        return _result("skipped", "background_work")
    session = payload.get("session_id")
    raw = payload.get("scratchpad_dir")
    if (not isinstance(session, str) or SESSION_ID.fullmatch(session) is None
            or not isinstance(raw, str) or not raw or len(raw) > 8192 or "\0" in raw):
        return _result("skipped", "missing_native_identity")
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        return _result("skipped", "unsupported_filesystem")
    path = Path(raw)
    if not path.is_absolute() or any(part in (".", "..") for part in raw.split("/")):
        return _result("skipped", "unverified_path")
    uid = os.geteuid()
    selected = None
    for root in _roots(environ, uid):
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        if len(parts) == 3 and parts[1:] == (session, "scratchpad"):
            selected = root, parts
            break
    if selected is None:
        return _result("skipped", "unverified_path")
    root, parts = selected
    progress = _Progress(clock)
    try:
        with ExitStack() as handles:
            fd, bindings = _open_root(root, uid, handles, progress)
            for component in parts:
                progress.check(0)
                child = _open_directory(component, uid, parent=fd)
                handles.callback(os.close, child)
                bindings += ((fd, component, os.fstat(child)),)
                fd = child
            try:
                target = os.stat(DISPOSABLE, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return _result("skipped", "no_disposable_files")
            if not stat.S_ISDIR(target.st_mode):
                return _result("skipped", "unverified_disposable_directory")
            _remove(fd, DISPOSABLE, uid, progress, expected=target, bindings=bindings)
    except FileNotFoundError:
        return _result("incomplete" if progress.removed else "skipped", "directory_disappeared", progress.removed)
    except _Budget:
        return _result("incomplete", "cleanup_budget", progress.removed)
    except _Unsafe:
        return _result("incomplete" if progress.removed else "skipped", "unsafe_directory", progress.removed)
    except OSError:
        return _result("incomplete", "filesystem_error", progress.removed)
    return _result("cleaned", "session_disposables_removed", progress.removed)


def main():
    try:
        raw = sys.stdin.read(MAX_INPUT_CHARS + 1)
        if len(raw) > MAX_INPUT_CHARS:
            result = _result("skipped", "oversized_payload")
        else:
            result = cleanup(json.loads(raw))
    except (ValueError, UnicodeError, RecursionError):
        result = _result("skipped", "invalid_payload")
    except OSError:
        result = _result("incomplete", "filesystem_error")
    if result["status"] == "incomplete":
        print("session-end-cleanup: disposable files may remain; check scratchpad permissions and finish cleanup in this session's disposable folder when safe.", file=sys.stderr)
    print(json.dumps(result))
    return 1 if result["status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
