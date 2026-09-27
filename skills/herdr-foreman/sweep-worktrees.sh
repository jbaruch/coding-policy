#!/usr/bin/env bash
# Prune every repository with a worktree directory under the worktree root.
#
# `prune-worktrees.sh` decides one repository's worktrees, and a round runs it
# for the repository in front of it. Worktrees of every other repository kept
# accumulating under the same root. This script finds each repository with a
# worktree directory under the root and runs the prune once per repository;
# the decision predicate stays in `prune-worktrees.sh`
# (`rules/script-as-black-box.md`). A repository is found through a
# directory on disk, never through its registrations: a worktree whose
# directory vanished leaves a registration this sweep cannot see. The
# `git worktree prune` each swept repository's prune runs clears it, and
# until then it is inert, holding no files.
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
#                       "error":"<stderr>"}],
#            "report":"<text>"}
#           `report` is the operator-facing summary, ready to relay verbatim:
#           a headline with counts, then one line per dirty or unpushed
#           worktree (path, branch, what is at stake, the operator's
#           command), unpushed branch, other notable kept worktree (NOTABLE,
#           by path), failure and error; other kept worktrees appear only as
#           counts by reason.
#           `result` is that repository's prune-worktrees.sh JSON. `error`
#           replaces it when the prune decided nothing (exit 1: no origin, a
#           failed fetch, ...). Worktrees are found anywhere below the root; a
#           found checkout, a .git directory and a symlinked directory are
#           not descended. Every path is classified with lstat: only a
#           missing path is absent, and any other failure to read one is an
#           `errors` entry, never a skip.
#           A skipped entry is not-a-worktree (a direct child of the root
#           holding no checkout), clone (a repository's own main checkout),
#           symlinked-git (a directory whose .git is a symlink, never
#           followed), or broken-worktree (its .git file names a gitdir that
#           no longer exists). An `errors` entry is a path or worktree that
#           could not be read (listing, lstat, gitdir stat, rev-parse or
#           worktree list failed), with its exit code and the repository
#           owning it when its gitdir's files name one. A `.git` file whose
#           repository registers no worktree at that path (copied or stale)
#           is an `errors` entry with a null repo, and never runs a prune.
#   stderr: diagnostics, and each prune's stderr prefixed with its repository.
#   exit  : 0 every repository decided cleanly,
#           1 usage, python3, git or bash absent, or the root missing or
#             unreadable (checked first, and again after discovery, before
#             any prune: a root replaced or unreadable by then prunes nothing) —
#             no JSON, a repair message on stderr,
#           2 at least one repository's prune exited non-zero or returned
#             no readable JSON (its entry carries `error`), or `errors` is
#             non-empty; every other repository still ran. The root is
#             re-proven before every later prune too: one replaced or
#             unreadable after a prune ran stops the rest, with an `errors`
#             entry for the root naming the repositories not pruned.
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
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || { warn "cannot resolve the script directory of ${BASH_SOURCE[0]} — restore read and search access to the plugin directory, or reinstall the plugin, then re-run"; return 1; }
  python3 - "$root" "$dry" "${here}/prune-worktrees.sh" <<'PY'
import json
import os
import stat
import subprocess
import sys

root, dry, prune = os.path.realpath(sys.argv[1]), sys.argv[2] == "1", sys.argv[3]
# The prune result's shape is checked by the module every reader shares.
sys.path.insert(0, os.path.dirname(prune))
from foreman.prune_result import prune_schema_error


class Run:
    """A finished command with stdout and stderr decoded like file names:
    surrogateescape, so a non-UTF-8 path or diagnostic never raises."""

    def __init__(self, done):
        self.returncode = done.returncode
        self.stdout = done.stdout.decode("utf-8", "surrogateescape")
        self.stderr = done.stderr.decode("utf-8", "surrogateescape")


def git(*args):
    return Run(subprocess.run(["git", *args], capture_output=True))


def gitdir_of(path):
    """The gitdir a linked worktree's .git file names, or None when unreadable."""
    try:
        with open(os.path.join(path, ".git"), encoding="utf-8", errors="surrogateescape") as handle:
            first = handle.readline().strip()
    except OSError:
        return None
    if not first.startswith("gitdir: "):
        return None
    return os.path.normpath(os.path.join(path, first[len("gitdir: "):]))


def repo_of(gitdir):
    """The main checkout owning a linked worktree's gitdir, read from its files, or None."""
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8", errors="surrogateescape") as handle:
            common = os.path.normpath(os.path.join(gitdir, handle.read().strip()))
    except OSError:
        return None
    return os.path.dirname(common) if os.path.basename(common) == ".git" else None


def kind_of(path):
    """lstat-based: "dir", "file", "link", "other", or None when the path does
    not exist. Only FileNotFoundError is absence: any other failure raises, so
    an unreadable path is never read as missing or as a plain directory."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(mode):
        return "link"
    if stat.S_ISDIR(mode):
        return "dir"
    return "file" if stat.S_ISREG(mode) else "other"


def discover(root):
    """Walk the whole root: a checkout found is not descended, nor is .git or
    a symlinked directory."""
    found, empty_tops = [], []
    for top in sorted(os.listdir(root)):
        top_path = os.path.join(root, top)
        before, reported, failed_before = len(found), len(skipped), len(errors)
        stack = [top_path]
        while stack:
            current = stack.pop()
            try:
                if kind_of(current) != "dir":
                    continue
                dotgit_kind = kind_of(os.path.join(current, ".git"))
            except OSError as exc:
                errors.append({"path": current, "repo": None, "exit": None,
                               "error": "cannot read: {}".format(exc.strerror or exc)})
                continue
            if dotgit_kind == "link":
                # Never followed: it could name a checkout outside the root.
                skipped.append({"path": current, "reason": "symlinked-git"}); continue
            if dotgit_kind == "dir":
                found.append(("clone", current)); continue
            if dotgit_kind == "file":
                found.append(("worktree", current)); continue
            try:
                children = sorted(os.listdir(current), reverse=True)
            except OSError as exc:
                errors.append({"path": current, "repo": None, "exit": None,
                               "error": "cannot list: {}".format(exc.strerror or exc)})
                continue
            stack.extend(os.path.join(current, child) for child in children if child != ".git")
        # A top that held nothing and was not already reported with a reason.
        if len(found) == before and len(skipped) == reported and len(errors) == failed_before:
            empty_tops.append(top_path)
    return found, empty_tops


def root_gone(why):
    sys.stderr.write("sweep-worktrees: the worktree root {} {} during the sweep — restore it, or pass the "
                     "directory holding the worktrees, then re-run; nothing was pruned\n".format(root, why))
    sys.exit(1)


try:
    root_id = os.stat(root)
except OSError as exc:
    root_gone("could not be read ({})".format(exc.strerror or exc))
repos, skipped, errors = {}, [], []
try:
    found, empty_tops = discover(root)
except OSError as exc:
    # The root itself vanished or became unreadable after the shell's check.
    sys.stderr.write("sweep-worktrees: cannot read the worktree root {} ({}) — restore it, or pass the "
                     "directory holding the worktrees; nothing was swept\n".format(root, exc.strerror or exc))
    sys.exit(1)
skipped += [{"path": path, "reason": "not-a-worktree"} for path in empty_tops]
for kind, path in found:
    if kind == "clone":
        skipped.append({"path": path, "reason": "clone"})
        continue
    gitdir = gitdir_of(path)
    if gitdir is None:
        errors.append({"path": path, "repo": None, "exit": None, "error": "its .git file names no gitdir"})
        continue
    try:
        os.stat(gitdir)
    except FileNotFoundError:
        # Proven stale: the metadata the .git file points at is gone.
        skipped.append({"path": path, "reason": "broken-worktree"})
        continue
    except OSError as exc:
        errors.append({"path": path, "repo": None, "exit": None,
                       "error": "cannot read its gitdir {}: {}".format(gitdir, exc.strerror or exc)})
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
    registered = [f[len("worktree "):] for f in listed.stdout.split("\0") if f.startswith("worktree ")]
    if os.path.realpath(path) not in {os.path.realpath(p) for p in registered[1:]}:
        # A copied or stale .git file: the repository it names does not
        # register this path, so nothing about it may run that repository's
        # prune.
        errors.append({"path": path, "repo": None, "exit": 0,
                       "error": "its .git file points at {}, which registers no worktree at this path; "
                                "a copied or stale .git file".format(common_dir)})
        continue
    shared = registered[0] if registered else None
    if not shared or not os.path.isdir(shared):
        errors.append({"path": path, "repo": owner, "exit": 0, "error": "its repository lists no main checkout on disk"})
        continue
    repos.setdefault(os.path.realpath(shared), []).append(path)

for entry in errors:
    if entry["repo"]:
        entry["repo"] = os.path.realpath(entry["repo"])
    sys.stderr.write("sweep-worktrees: cannot read the worktree {} (exit {}): {} — inspect it by hand\n".format(
        entry["path"], entry["exit"], entry["error"]))
def root_changed():
    """Why the root is no longer the directory the walk read, or None: a root
    replaced or made unreadable since then proves nothing it found."""
    try:
        now_id = os.stat(root)
        os.listdir(root)
    except OSError as exc:
        return "became unreadable ({})".format(exc.strerror or exc)
    if not stat.S_ISDIR(now_id.st_mode) or (now_id.st_dev, now_id.st_ino) != (root_id.st_dev, root_id.st_ino):
        return "was replaced"
    return None


why_root = root_changed()
if why_root:
    root_gone(why_root)
results, failed = [], bool(errors)
env = dict(os.environ, WORKTREE_ROOT=root)
for index, shared in enumerate(sorted(repos)):
    # Re-proven before every later prune too: a root changed after an
    # earlier prune stops the rest, and that prune's result stands.
    why_root = root_changed() if index else None
    if why_root:
        left = sorted(repos)[index:]
        errors.append({"path": root, "repo": None, "exit": None,
                       "error": "the worktree root {} during the sweep; {} repositor{} not pruned: {}".format(
                           why_root, len(left), "y was" if len(left) == 1 else "ies were", ", ".join(left))})
        sys.stderr.write("sweep-worktrees: the worktree root {} {} during the sweep — {} not pruned; restore it, "
                         "then re-run\n".format(root, why_root, ", ".join(left)))
        failed = True
        break
    run = Run(subprocess.run(["bash", prune, shared, *(["--dry-run"] if dry else [])],
                             capture_output=True, env=env))
    for line in run.stderr.splitlines():
        sys.stderr.write("sweep-worktrees: {}: {}\n".format(shared, line))
    entry = {"shared": shared, "exit": run.returncode}
    try:
        entry["result"] = json.loads(run.stdout) if run.returncode in (0, 2) else None
    except ValueError:
        entry["result"] = None
    why = None if entry["result"] is None else prune_schema_error(entry["result"])
    if entry["result"] is None or why:
        del entry["result"]
        # A prune whose result cannot be read, or is not in its documented
        # shape, decided nothing we can report, whatever its exit code said.
        if why:
            entry["error"] = "prune-worktrees.sh exited {} with a result holding {}".format(run.returncode, why)
        else:
            entry["error"] = run.stderr.strip() or "prune-worktrees.sh exited {} with no JSON".format(run.returncode)
        if run.returncode in (0, 2):
            sys.stderr.write("sweep-worktrees: {}: prune-worktrees.sh exited {} without a readable JSON result "
                             "— run it directly to see why\n".format(shared, run.returncode))
        failed = True
    elif run.returncode != 0:
        failed = True
    results.append(entry)

#: Kept reasons the operator acts on: listed by path. Every other kept
#: reason is given as a count.
NOTABLE = ("dirty", "unpushed", "locked", "in-use", "changed", "idle-unknown",
           "submodule", "submodule-dirty", "nested-repo")


def report_text():
    """The operator-facing summary, relayed verbatim."""
    lines, counts = [], {}
    removed = deleted = 0
    for repo in results:
        res = repo.get("result")
        if res is None:
            lines.append("Error in {}: {}".format(repo["shared"], repo.get("error", "")))
            continue
        removed += len(res.get("worktrees_removed", []))
        deleted += len(res.get("branches_deleted", []))
        for k in res.get("worktrees_kept", []):
            reason = k["reason"]
            if reason == "dirty":
                lines.append("Kept dirty: {} ({}), {} changed file(s), idle {}h: {}".format(
                    k["path"], k.get("branch") or "detached", k.get("dirty_files"), k.get("age_hours"), k.get("command")))
            elif reason == "unpushed":
                lines.append("Kept unpushed: {} ({}), {} commit(s) origin does not hold, idle {}h: {}".format(
                    k["path"], k.get("branch") or "detached", k.get("unpushed_commits"), k.get("age_hours"), k.get("command")))
            elif reason in NOTABLE:
                extra = " ({})".format(k["lock_reason"]) if k.get("lock_reason") else ""
                lines.append("Kept {}: {}{}".format(reason, k["path"], extra))
            else:
                counts[reason] = counts.get(reason, 0) + 1
        for b in res.get("branches_kept", []):
            if b["reason"] == "unpushed":
                lines.append("Kept unpushed branch {} in {}, {} commit(s), idle {}h: {}".format(
                    b["branch"], repo["shared"], b.get("unpushed_commits"), b.get("age_hours"), b.get("command")))
        for f in res.get("failed", []):
            lines.append("Failed in {}: {}: {}".format(repo["shared"], f["target"], f["error"]))
    for e in errors:
        lines.append("Error reading {}{}: {}".format(e["path"], " ({})".format(e["repo"]) if e.get("repo") else "", e["error"]))
    head = "Worktree sweep{}: {} repositories, {} worktree(s) removed, {} branch(es) deleted".format(
        " (dry run)" if dry else "", len(results), removed, deleted)
    if counts:
        head += "; kept: " + ", ".join("{} {}".format(n, r) for r, n in sorted(counts.items()))
    return "\n".join([head + "."] + lines)


print(json.dumps({"root": root, "dry_run": dry, "repos": results, "skipped": skipped, "errors": errors,
                  "report": report_text()}, sort_keys=True))
sys.exit(2 if failed else 0)
PY
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
