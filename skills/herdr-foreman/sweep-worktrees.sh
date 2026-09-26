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
#            "skipped":[{"path":"<abs>","reason":"<why>"}]}
#           `result` is that repository's prune-worktrees.sh JSON. `error`
#           replaces it when the prune decided nothing (exit 1: no origin, a
#           failed fetch, ...). A skipped entry is a direct child of the root
#           that is no linked worktree: not-a-worktree (a plain directory or
#           file), clone (a repository's own main checkout), or
#           broken-worktree (its gitdir no longer resolves).
#   stderr: diagnostics, and each prune's stderr prefixed with its repository.
#   exit  : 0 every repository decided cleanly,
#           1 usage, python3 absent, or the root unreadable — no JSON,
#           2 at least one repository failed or reported a failure; every
#             other repository still ran.
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


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def main_worktree(common):
    """The first record of the repository's worktree list is its main checkout."""
    listed = git("--git-dir", common, "worktree", "list", "--porcelain", "-z")
    if listed.returncode != 0:
        return None
    for field in listed.stdout.split("\0"):
        if field.startswith("worktree "):
            return field[len("worktree "):]
    return None


repos, skipped = {}, []
for name in sorted(os.listdir(root)):
    path = os.path.join(root, name)
    dotgit = os.path.join(path, ".git")
    if not os.path.isdir(path) or not os.path.exists(dotgit):
        skipped.append({"path": path, "reason": "not-a-worktree"})
        continue
    if os.path.isdir(dotgit):
        skipped.append({"path": path, "reason": "clone"})
        continue
    common = git("-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    shared = main_worktree(common.stdout.strip()) if common.returncode == 0 else None
    if not shared or not os.path.isdir(shared):
        skipped.append({"path": path, "reason": "broken-worktree"})
        continue
    repos.setdefault(os.path.realpath(shared), []).append(path)

results, failed = [], False
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

print(json.dumps({"root": root, "dry_run": dry, "repos": results, "skipped": skipped}, sort_keys=True))
sys.exit(2 if failed else 0)
PY
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
