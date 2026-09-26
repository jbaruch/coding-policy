#!/usr/bin/env bash
# Prune every repository that owns a worktree under the worktree root.
#
# `prune-worktrees.sh` decides one repository's worktrees, and a round runs it
# for the repository in front of it. Worktrees of every other repository kept
# accumulating under the same root. This script finds each repository owning
# an entry under the root and runs the prune once per repository; the decision
# predicate stays in `prune-worktrees.sh` (`rules/script-as-black-box.md`).
#
# Contract:
#   argv  : <worktree-root> [--dry-run]
#           --dry-run is passed through to every prune: same decisions,
#           nothing removed.
#   stdout: one JSON object —
#           {"root":"<abs>","dry_run":bool,
#            "repos":[{"shared":"<abs>","exit":N,"result":{...}}
#                     | {"shared":"<abs>","exit":N,"error":"<stderr>"}],
#            "skipped":[{"path":"<abs>","reason":"<why>"}],
#            "errors":[{"path":"<abs>","exit":N|null,"error":"<stderr>"}]}
#           `result` is that repository's prune-worktrees.sh JSON. `error`
#           replaces it when the prune decided nothing (exit 1: no origin, a
#           failed fetch, ...). Worktrees are found recursively, up to
#           MAX_DEPTH directories below the root; a found checkout is not
#           descended. A skipped entry is not-a-worktree (a direct child of
#           the root holding no checkout), clone (a repository's own main
#           checkout), or broken-worktree (its .git file names a gitdir that
#           no longer exists). An `errors` entry is a worktree git could not
#           read (rev-parse or worktree list failed), with its exit code.
#   stderr: diagnostics, and each prune's stderr prefixed with its repository.
#   exit  : 0 every repository decided cleanly,
#           1 usage, python3 absent, or the root unreadable — no JSON,
#           2 at least one repository failed or reported a failure, or
#             `errors` is non-empty; every other repository still ran.
#   env   : PRUNE_* variables pass through to prune-worktrees.sh.
set -euo pipefail

warn() { printf 'sweep-worktrees: %s\n' "$1" >&2; }

main() {
  local root="" dry=0 arg
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry=1 ;;
      -*) warn "unknown flag '${arg}' — usage: sweep-worktrees.sh <worktree-root> [--dry-run]"; return 1 ;;
      *) if [[ -n "$root" ]]; then warn "usage: sweep-worktrees.sh <worktree-root> [--dry-run]"; return 1; fi; root="$arg" ;;
    esac
  done
  if [[ -z "$root" ]]; then
    warn "usage: sweep-worktrees.sh <worktree-root> [--dry-run]"
    return 1
  fi
  if ! command -v python3 >/dev/null; then
    warn "python3 not found on PATH — install it to sweep worktrees"
    return 1
  fi
  if [[ ! -d "$root" || ! -r "$root" || ! -x "$root" ]]; then
    warn "worktree root ${root} is missing or unreadable — pass the directory holding the worktrees"
    return 1
  fi
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || { warn "cannot resolve the script directory"; return 1; }
  python3 - "$root" "$dry" "${here}/prune-worktrees.sh" <<'PY'
import json
import os
import subprocess
import sys

root, dry, prune = os.path.realpath(sys.argv[1]), sys.argv[2] == "1", sys.argv[3]


#: Directories below the root searched for worktrees; a worktree nested
#: deeper than this is not found.
MAX_DEPTH = 4


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def gitdir_of(path):
    """The gitdir a linked worktree's .git file names, or None when unreadable."""
    try:
        with open(os.path.join(path, ".git"), encoding="utf-8") as handle:
            first = handle.readline().strip()
    except OSError:
        return None
    if not first.startswith("gitdir: "):
        return None
    return os.path.normpath(os.path.join(path, first[len("gitdir: "):]))


def discover(root):
    """Walk the root: yield ("worktree"|"clone", path); a found checkout is not descended."""
    found, empty_tops = [], []
    for top in sorted(os.listdir(root)):
        top_path = os.path.join(root, top)
        before = len(found)
        stack = [(top_path, 0)]
        while stack:
            current, depth = stack.pop()
            if not os.path.isdir(current) or os.path.islink(current):
                continue
            dotgit = os.path.join(current, ".git")
            if os.path.isdir(dotgit):
                found.append(("clone", current)); continue
            if os.path.isfile(dotgit):
                found.append(("worktree", current)); continue
            if depth >= MAX_DEPTH:
                continue
            try:
                children = sorted(os.listdir(current), reverse=True)
            except OSError as exc:
                errors.append({"path": current, "exit": None, "error": "cannot list: {}".format(exc)})
                continue
            stack.extend((os.path.join(current, child), depth + 1) for child in children if child != ".git")
        if len(found) == before:
            empty_tops.append(top_path)
    return found, empty_tops


repos, skipped, errors = {}, [], []
found, empty_tops = discover(root)
skipped += [{"path": path, "reason": "not-a-worktree"} for path in empty_tops]
for kind, path in found:
    if kind == "clone":
        skipped.append({"path": path, "reason": "clone"})
        continue
    gitdir = gitdir_of(path)
    if gitdir is None:
        errors.append({"path": path, "exit": None, "error": "its .git file names no gitdir"})
        continue
    if not os.path.exists(gitdir):
        # Proven stale: the metadata the .git file points at is gone.
        skipped.append({"path": path, "reason": "broken-worktree"})
        continue
    common = git("-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common.returncode != 0:
        errors.append({"path": path, "exit": common.returncode, "error": common.stderr.strip()})
        continue
    listed = git("--git-dir", common.stdout.strip(), "worktree", "list", "--porcelain", "-z")
    if listed.returncode != 0:
        errors.append({"path": path, "exit": listed.returncode, "error": listed.stderr.strip()})
        continue
    shared = next((f[len("worktree "):] for f in listed.stdout.split("\0") if f.startswith("worktree ")), None)
    if not shared or not os.path.isdir(shared):
        errors.append({"path": path, "exit": 0, "error": "its repository lists no main checkout on disk"})
        continue
    repos.setdefault(os.path.realpath(shared), []).append(path)

for entry in errors:
    sys.stderr.write("sweep-worktrees: cannot read the worktree {} (exit {}): {} — inspect it by hand\n".format(
        entry["path"], entry["exit"], entry["error"]))
results, failed = [], bool(errors)
env = dict(os.environ, WORKTREE_ROOT=root)
for shared in sorted(repos):
    run = subprocess.run(["bash", prune, shared, *(["--dry-run"] if dry else [])],
                         capture_output=True, text=True, env=env)
    for line in run.stderr.splitlines():
        sys.stderr.write("sweep-worktrees: {}: {}\n".format(shared, line))
    entry = {"shared": shared, "exit": run.returncode}
    try:
        entry["result"] = json.loads(run.stdout) if run.returncode in (0, 2) else None
    except ValueError:
        entry["result"] = None
    if entry["result"] is None:
        del entry["result"]
        entry["error"] = run.stderr.strip() or "prune-worktrees.sh exited {} with no JSON".format(run.returncode)
    if run.returncode != 0:
        failed = True
    results.append(entry)

print(json.dumps({"root": root, "dry_run": dry, "repos": results, "skipped": skipped, "errors": errors}, sort_keys=True))
sys.exit(2 if failed else 0)
PY
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
