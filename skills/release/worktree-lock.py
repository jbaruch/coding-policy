#!/usr/bin/env python3
"""Report whether a worktree is locked, and its lock reason.

Release Step 7 path (B) and the agent-worktree-isolation Cleanup order leave a
locked worktree to its lock holder (#831). The script reads
`git worktree list --porcelain -z`, so a path or reason holding a newline
stays inside its own NUL-terminated field, and picks the stanza whose
`worktree` path resolves to the same directory as the argument.

Contract:
  cwd   : any checkout of the repository that owns the worktree
  argv  : <worktree-path>
  stdout: one JSON object — {"path": "<resolved path>", "locked": <bool>,
          "reason": "<lock reason>" | null}
          locked with a reason: "locked": true, "reason": "<text>"
          locked, no reason supplied: "locked": true, "reason": null
          unlocked: "locked": false, "reason": null
  stderr: diagnostics only.
  exit  : 0 reported,
          1 usage, git absent or failed, or no listed worktree resolves to
            the argument's path.
"""

import json
import os
import subprocess
import sys


def stanzas(raw):
    """Yield each worktree stanza of `--porcelain -z` output as {field: value or None}."""
    stanza = {}
    for field in raw.split(b"\0"):
        if not field:
            if stanza:
                yield stanza
            stanza = {}
            continue
        key, sep, value = field.partition(b" ")
        stanza[os.fsdecode(key)] = os.fsdecode(value) if sep else None
    if stanza:
        yield stanza


def lock_state(raw, target):
    """{"path", "locked", "reason"} for the stanza whose path resolves to target, None if none does."""
    resolved = os.path.realpath(target)
    for stanza in stanzas(raw):
        path = stanza.get("worktree")
        if path is not None and os.path.realpath(path) == resolved:
            locked = "locked" in stanza
            return {"path": resolved, "locked": locked, "reason": stanza["locked"] if locked else None}
    return None


def fail(message):
    print("worktree-lock: {}".format(message), file=sys.stderr)
    return 1


def main(argv):
    if len(argv) != 1:
        return fail("usage: worktree-lock.py <worktree-path>")
    command = ["git", "worktree", "list", "--porcelain", "-z"]
    try:
        done = subprocess.run(command, capture_output=True, check=False)
    except FileNotFoundError:
        return fail("git not found on PATH — install git 2.36 or later, then re-run")
    if done.returncode != 0:
        return fail("`{}` exited {} in {}: {} — run it from a checkout of the repository that "
                    "owns the worktree; git 2.36 or later is required for -z".format(
                        " ".join(command), done.returncode, os.getcwd(),
                        os.fsdecode(done.stderr).strip()))
    state = lock_state(done.stdout, argv[0])
    if state is None:
        return fail("no worktree listed by `{}` in {} resolves to {} — pass the worktree's path and run "
                    "from a checkout of the repository that owns it".format(
                        " ".join(command), os.getcwd(), os.path.realpath(argv[0])))
    print(json.dumps(state))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
