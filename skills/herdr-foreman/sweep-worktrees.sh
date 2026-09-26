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
#            "errors":[{"path":"<abs>","repo":"<abs>"|null,"exit":N|null,
#                       "error":"<stderr>"}]}
#           `result` is that repository's prune-worktrees.sh JSON. `error`
#           replaces it when the prune decided nothing (exit 1: no origin, a
#           failed fetch, ...). Worktrees are found anywhere below the root; a
#           found checkout, a .git directory and a symlinked directory are
#           not descended; a symlinked .trash, and a symlinked entry inside
#           .trash, are never followed. The root's .trash holds the prune's archived
#           worktrees: each names its repository, so a repository whose only
#           worktrees are archived still gets its prune (and expiry) run,
#           but a trash worktree is never a candidate.
#           A skipped entry is not-a-worktree (a direct child of
#           the root holding no checkout), clone (a repository's own main
#           checkout), or broken-worktree (its .git file names a gitdir that
#           no longer exists). An `errors` entry is a worktree git could not
#           read (rev-parse or worktree list failed), with its exit code and
#           the repository owning it when its gitdir's files name one.
#   stderr: diagnostics, and each prune's stderr prefixed with its repository.
#   exit  : 0 every repository decided cleanly,
#           1 usage, python3, git or bash absent, or the root unreadable — no
#             JSON,
#           2 at least one repository's prune exited non-zero or returned
#             no readable JSON (its entry carries `error`), or `errors` is
#             non-empty; every other repository still ran.
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
  # Every tool the embedded program and the per-repository prune run: checked
  # here, so a missing one is an actionable message, never a traceback.
  local tool
  for tool in python3 git bash; do
    if ! command -v "$tool" >/dev/null; then
      warn "${tool} not found on PATH — install ${tool} (or restore it to PATH) to sweep worktrees"
      return 1
    fi
  done
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


def repo_of(gitdir):
    """The main checkout owning a linked worktree's gitdir, read from its files, or None."""
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as handle:
            common = os.path.normpath(os.path.join(gitdir, handle.read().strip()))
    except OSError:
        return None
    return os.path.dirname(common) if os.path.basename(common) == ".git" else None


def discover(root):
    """Walk the whole root: a checkout found is not descended, nor is .git,
    the prune's .trash, or a symlinked directory."""
    found, empty_tops = [], []
    for top in sorted(os.listdir(root)):
        top_path = os.path.join(root, top)
        if top == ".trash" and not os.path.islink(top_path):
            # The prune's archived worktrees: never pruned as candidates, but
            # each still names a repository whose expiry pass must run.
            try:
                trashed = sorted(os.listdir(top_path))
            except OSError as exc:
                errors.append({"path": top_path, "repo": None, "exit": None, "error": "cannot list: {}".format(exc)})
                continue
            # A symlinked entry is never followed: it could name a checkout
            # outside the root.
            found.extend(("trash", os.path.join(top_path, name)) for name in trashed
                         if not os.path.islink(os.path.join(top_path, name))
                         and os.path.isfile(os.path.join(top_path, name, ".git")))
            continue
        before = len(found)
        stack = [top_path]
        while stack:
            current = stack.pop()
            if os.path.islink(current) or not os.path.isdir(current):
                continue
            dotgit = os.path.join(current, ".git")
            if os.path.isdir(dotgit):
                found.append(("clone", current)); continue
            if os.path.isfile(dotgit):
                found.append(("worktree", current)); continue
            try:
                children = sorted(os.listdir(current), reverse=True)
            except OSError as exc:
                errors.append({"path": current, "repo": None, "exit": None, "error": "cannot list: {}".format(exc)})
                continue
            stack.extend(os.path.join(current, child) for child in children if child != ".git")
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
        errors.append({"path": path, "repo": None, "exit": None, "error": "its .git file names no gitdir"})
        continue
    if not os.path.exists(gitdir):
        # Proven stale: the metadata the .git file points at is gone.
        skipped.append({"path": path, "reason": "broken-worktree"})
        continue
    owner = repo_of(gitdir)
    common = git("-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common.returncode != 0:
        errors.append({"path": path, "repo": owner, "exit": common.returncode, "error": common.stderr.strip()})
        continue
    common_dir = common.stdout.strip()
    if owner is None and os.path.basename(common_dir) == ".git":
        owner = os.path.dirname(common_dir)
    listed = git("--git-dir", common_dir, "worktree", "list", "--porcelain", "-z")
    if listed.returncode != 0:
        errors.append({"path": path, "repo": owner, "exit": listed.returncode, "error": listed.stderr.strip()})
        continue
    shared = next((f[len("worktree "):] for f in listed.stdout.split("\0") if f.startswith("worktree ")), None)
    if not shared or not os.path.isdir(shared):
        errors.append({"path": path, "repo": owner, "exit": 0, "error": "its repository lists no main checkout on disk"})
        continue
    candidates = repos.setdefault(os.path.realpath(shared), [])
    if kind == "worktree":
        candidates.append(path)

for entry in errors:
    if entry["repo"]:
        entry["repo"] = os.path.realpath(entry["repo"])
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
    if not isinstance(entry["result"], dict):
        del entry["result"]
        entry["error"] = run.stderr.strip() or "prune-worktrees.sh exited {} with no JSON".format(run.returncode)
        # A prune whose result cannot be read decided nothing we can report,
        # whatever its exit code said.
        if run.returncode in (0, 2):
            sys.stderr.write("sweep-worktrees: {}: prune-worktrees.sh exited {} without a readable JSON result "
                             "— run it directly to see why\n".format(shared, run.returncode))
        failed = True
    elif run.returncode != 0:
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
