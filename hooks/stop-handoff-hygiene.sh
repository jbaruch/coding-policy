#!/usr/bin/env bash
# Flag leftover local hygiene before the agent hands off (a Stop gate).
#
# A Claude Code `Stop` hook: at handoff it runs deterministic, universal
# git-hygiene checks and, when it finds clearly-actionable leftovers, blocks the
# stop once with an actionable reason so the agent can clean up. We hand-cleaned
# exactly this mess in real sessions — stray merged-and-remote-deleted local
# branches and an abandoned worktree. This mechanizes the pre-handoff cleanup
# rules/language-diagnostics.md endorses ("a Stop or pre-handoff hook running the
# gate mechanizes this") and complements the fleet-wide delete_branch_on_merge
# (that auto-cleans REMOTE branches; this covers the LOCAL branches/worktrees it
# never touches).
#
# Why nativeHooks (not the portable `hooks` tier): blocking a stop is an
# agent-specific contract with no portable consensus form — the `hooks` tier
# wraps the script in `tessl hook run`, which translates `additionalContext`
# (informational), not a stop-block. nativeHooks writes the entry raw to each
# agent's native config, so the agent reads the script's `{"decision":"block"}`
# directly. Claude Code and Codex share the same Stop contract (decision/reason/
# stop_hook_active), so the SAME script is dual-wired. The two entries differ in
# shape by necessity: Claude Code's config takes command + args[], Codex's takes
# a single command string (its config has no args field), so nativeHooks.codex
# uses `bash "<path>"` as one string. Verified by installing into scratch
# consumers and inspecting .claude/settings.json and .codex/config.toml.
#
# Blocking findings (gate the stop, once):
#   - Spent worktrees and local branches, as the owner script decides them:
#     `skills/herdr-foreman/prune-worktrees.sh <shared-checkout> --dry-run`,
#     run under skills/herdr-foreman/bounded-run.sh for STOP_PRUNE_BUDGET_SEC. Its would-remove
#     worktrees and would-delete branches block, with the one command that
#     removes them all (the same script, live). Its idle dirty or unpushed
#     worktrees and unpushed branches are reported, never blocking: that work
#     exists nowhere else and is the operator's call. This hook holds no
#     predicate of its own (rules/script-as-black-box.md). It runs only when the
#     repository has a linked worktree or a local branch besides the checked-out
#     one, so a plain checkout pays no fetch. A failed or timed-out run warns
#     and blocks nothing. It never runs in a Herdr worker session, nor when
#     HERDR_ENV is set and the role cannot be determined (see herdr_role).
#   - Diagnostics findings in the CHANGED set only (uncommitted .sh/.py):
#     lint the .sh with shellcheck, the .py with pyright. Skipped when nothing
#     lintable changed, so a clean handoff costs nothing. An absent engine is
#     blocking (the gate can't clear findings without it) — install and re-check.
#     Inlined here rather than delegated to scripts/run-diagnostics.sh, which the
#     Tessl packer does not ship (only rules/, skills/, hooks/ surfaces publish).
#     Python uses the first executable interpreter in VIRTUAL_ENV, the repo's
#     .venv, then venv, and passes it as --pythonpath. Prefer that environment's
#     pyright when installed; otherwise use PATH. With no environment, retain
#     Pyright's own configuration/default interpreter resolution. Import errors
#     remain blocking; an environment correction never suppresses diagnostics.
# Report-only (never blocks on its own): a dirty working tree — often intentional
#   work-in-progress, surfaced to the user but not trapped.
#
# Loop-safe: reads `stop_hook_active` from stdin and allows immediately when set,
# so it blocks at most once per handoff chain — never traps the agent in a loop.
# Fail-open: any inability to evaluate (no jq, not a git repo, parse failure)
# allows the stop. A hygiene nudge must never wedge a handoff.
#
# Contract:
#   stdin : Claude Code Stop JSON. Only `.stop_hook_active` is read.
#   stdout: on a block, one JSON object {"decision":"block","reason":"<text>"};
#           otherwise nothing.
#   exit  : always 0 (block is expressed in stdout JSON, never via exit code).
#           Best-effort failures warn to stderr and allow the stop.
#   state : none — every check reads live git state.
#   env   : STOP_PRUNE_BUDGET_SEC overrides PRUNE_BUDGET_SEC; WORKTREE_ROOT and
#           the PRUNE_* variables pass through to the owner script.
set -euo pipefail

#: Wall-clock seconds for the owner script's dry run. It fetches, and a Stop
#: waits on it.
PRUNE_BUDGET_SEC="${STOP_PRUNE_BUDGET_SEC:-20}"

warn() { printf 'stop-handoff-hygiene: %s\n' "$1" >&2; }

# Is this session a Herdr WORKER rather than the foreman?
#
# The team rules reserve the shared checkout and every worktree operation for
# the foreman: a worker "runs no git command against the shared checkout,
# mutating or otherwise" and "never creates, moves, or removes a worktree"
# (skills/herdr-foreman/references/team-operation.md Writers and Checkouts). A hook that tells a
# worker to fast-forward `main` or remove a worktree is instructing it to
# break that rule -- which is exactly what happened in a live round, where the
# worker reported the contradiction and then obeyed the hook.
#
# Herdr exports no foreman/worker flag, so the role is derived from where the
# session sits: the foreman works in the shared checkout, every worker works in a
# linked worktree. In a linked worktree `--git-dir` and `--git-common-dir`
# resolve differently; in the main checkout they are the same.
#
# HERDR_ENV counts when set at all, empty included (rules/agent-team-operation.md
# Two Modes).
#
# Tri-state, so an indeterminate session never reads as "the foreman":
#   0 = a Herdr worker (HERDR_ENV set, linked worktree),
#   1 = the foreman or a standalone agent (HERDR_ENV unset, or a proven main
#       checkout) — the only sessions that run the foreman-only cleanup,
#   2 = unknown — HERDR_ENV set and a role probe failed; treated as a possible
#       worker: no cleanup, and a report says why. ROLE_WHY names the cause.
herdr_role() {
  ROLE_WHY=""
  [[ -n "${HERDR_ENV+x}" ]] || return 1

  local git_dir common_dir rc=0
  local why="Worktree and branch check skipped: this hook could not tell a Herdr worker from the foreman (see the warning above). Run \`git rev-parse --absolute-git-dir --path-format=absolute --git-common-dir\` here to diagnose."
  git_dir="$(git rev-parse --absolute-git-dir 2>&1)" || rc=$?
  if (( rc != 0 )); then
    warn "git rev-parse --absolute-git-dir failed (exit ${rc}: ${git_dir//$'\n'/ }) — cannot tell a Herdr worker from the foreman; skipping the foreman-only worktree check"
    ROLE_WHY="$why"
    return 2
  fi
  rc=0
  common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>&1)" || rc=$?
  if (( rc != 0 )); then
    warn "git rev-parse --git-common-dir failed (exit ${rc}: ${common_dir//$'\n'/ }) — cannot tell a Herdr worker from the foreman; skipping the foreman-only worktree check"
    ROLE_WHY="$why"
    return 2
  fi

  [[ "$git_dir" != "$common_dir" ]] || return 1
  return 0
}

main() {
  local input active inside
  local -a changed=()
  local -a blocking=() reports=()

  # jq is required to read stop_hook_active and to emit the block JSON safely.
  # Without it we cannot evaluate loop-safety, so fail open (allow the stop).
  if ! command -v jq >/dev/null; then
    warn "jq not found — install jq to enable the handoff-hygiene gate; allowing stop"
    return 0
  fi

  # Read the Stop payload and honor the loop guard: if this stop is already the
  # result of our prior block, allow it — never block twice (no loop).
  input="$(cat)" || input=""
  if ! active="$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>&1)"; then
    warn "could not parse Stop payload (${active}) — allowing stop; report the payload shape as a bug in this hook"
    return 0
  fi
  [[ "$active" == "true" ]] && return 0

  command -v git >/dev/null || { warn "git not found — allowing stop; install git so the handoff check can run"; return 0; }

  # Outside a work tree there is nothing to check. `--is-inside-work-tree` prints
  # true/false and exits 0 inside any repo; exit 128 is the expected "not a git
  # repository" non-result (silent allow). Any other exit is a real failure and
  # is surfaced before failing open (rules/error-handling.md). A bare repo /
  # gitdir ("false") also allows silently.
  rc=0
  inside="$(git rev-parse --is-inside-work-tree 2>&1)" || rc=$?
  if (( rc != 0 )); then
    (( rc == 128 )) || warn "git rev-parse --is-inside-work-tree failed (exit ${rc}: ${inside}) — allowing stop; run \`git status\` here to see why"
    return 0
  fi
  [[ "$inside" == "true" ]] || return 0

  # Branch and worktree cleanup is the foreman's, never a worker's
  # (skills/herdr-foreman/references/team-operation.md Writers and Checkouts). Blocking a worker's
  # stop over leftovers it is forbidden to remove would force it to either
  # disobey the rule or fail to hand off. The foreman's own teardown runs at the
  # end of its round.
  #
  # ONLY that cleanup is suppressed. The diagnostics gate and the dirty-tree
  # report apply to a worker's own changed files, which are its to fix
  # (rules/language-diagnostics.md Gate It Deterministically) -- skipping them
  # here would let a worker hand off findings nobody else is going to see.
  # An indeterminate role runs no cleanup either: a worker whose role probe
  # failed must not be handed the foreman's teardown.
  local role=0
  herdr_role || role=$?
  if (( role == 1 )); then
    read_owner_decisions
  elif (( role == 2 )); then
    reports+=("$ROLE_WHY")
  fi

  run_changed_diagnostics
  check_dirty_tree

  if (( ${#blocking[@]} > 0 )); then
    emit_block
  elif (( ${#reports[@]} > 0 )); then
    # Report-only: surface to the user (stderr) but do not block the handoff.
    local r
    for r in "${reports[@]}"; do warn "$r"; done
  fi
  return 0
}

#: Set by read_inventory: how many worktrees `git worktree list` registers,
#: and the shared checkout's path (the first record), byte for byte.
WT_COUNT=0
WT_SHARED=""

# Read the NUL-framed worktree inventory. A path ending in a newline, or
# holding one, stays one field; the path comes back behind a sentinel so
# command substitution cannot strip its trailing newline. 1 (warned) when
# git or the parse fails.
read_inventory() {
  local dir rc=0 parsed rest
  dir="$(mktemp -d)" || { warn "mktemp failed — skipping the worktree check; make ${TMPDIR:-/tmp} writable"; return 1; }
  git worktree list --porcelain -z >"${dir}/list" 2>"${dir}/err" || rc=$?
  if (( rc != 0 )); then
    warn "\`git worktree list --porcelain -z\` failed (exit ${rc}): $(tr '\n' ' ' < "${dir}/err") — skipping the worktree check; run it here to see why"
    rm -rf "$dir" || warn "could not remove ${dir} — delete it by hand"
    return 1
  fi
  rc=0
  parsed="$(python3 -c '
import sys
with open(sys.argv[1], "rb") as handle:
    fields = handle.read().split(b"\0")
paths = [f[len(b"worktree "):] for f in fields if f.startswith(b"worktree ")]
if not paths:
    sys.exit(3)
sys.stdout.buffer.write(str(len(paths)).encode() + b"\n" + paths[0] + b"x")
' "${dir}/list")" || rc=$?
  rm -rf "$dir" || warn "could not remove ${dir} — delete it by hand"
  if (( rc != 0 )); then
    warn "cannot name the shared checkout from \`git worktree list --porcelain -z\` (exit ${rc}) — skipping the worktree check; run it here to see what it prints"
    return 1
  fi
  WT_COUNT="${parsed%%$'\n'*}"
  rest="${parsed#*$'\n'}"
  WT_SHARED="${rest%x}"
  return 0
}

# Does this repository hold anything the owner script could act on: a linked
# worktree, or a local branch other than the checked-out one? A cheap local
# gate, never a verdict: the owner script decides. Unreadable counts as yes.
has_candidates() {
  local out rc=0
  (( WT_COUNT > 1 )) && return 0
  out="$(git for-each-ref --count=2 --format='%(refname)' refs/heads 2>&1)" || rc=$?
  if (( rc != 0 )); then warn "\`git for-each-ref refs/heads\` failed (exit ${rc}): ${out} — run it here to see why"; return 0; fi
  (( $(grep -c . <<<"$out") > 1 ))
}

# Run the owner script's dry run and turn its decisions into findings: what it
# would remove blocks, what it keeps for the operator is reported.
read_owner_decisions() {
  if ! command -v python3 >/dev/null; then
    warn "python3 not found on PATH — install it; skipping the worktree check"
    return 0
  fi
  read_inventory || return 0
  has_candidates || return 0
  local src dir here shared prune runner out err rc=0
  # Command substitution strips every trailing newline, so the hooks directory
  # never passes through one bare: parameter expansion derives it, and a
  # sentinel carries `pwd` across the strip (#487).
  src="${BASH_SOURCE[0]}"
  case "$src" in
    */*) dir="${src%/*}" ;;
    *) dir=. ;;
  esac
  here="$(CDPATH='' cd -- "${dir:-/}" && pwd && printf x)" || { warn "cannot resolve the hooks directory — skipping the worktree check; restore access to the plugin directory or reinstall the plugin"; return 0; }
  here="${here%x}"
  here="${here%$'\n'}"
  prune="${here}/../skills/herdr-foreman/prune-worktrees.sh"
  runner="${here}/../skills/herdr-foreman/bounded-run.sh"
  if [[ ! -f "$prune" || ! -r "$prune" || ! -f "$runner" || ! -r "$runner" ]]; then
    warn "${prune} or ${runner} is not readable — reinstall the plugin; skipping the worktree check"
    return 0
  fi
  shared="$WT_SHARED"
  err="$(mktemp)" || { warn "mktemp failed — skipping the worktree check; make ${TMPDIR:-/tmp} writable"; return 0; }
  out="$(GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh} -o BatchMode=yes" \
    bash "$runner" "$PRUNE_BUDGET_SEC" bash "$prune" "$shared" --dry-run 2>"$err")" || rc=$?
  if (( rc != 0 && rc != 2 )) || [[ -z "$out" ]]; then
    warn "the worktree check could not run (\`bash ${prune} ${shared} --dry-run\` exited ${rc}: $(tr '\n' ' ' < "$err")) — nothing is blocked on it"
    rm -f "$err" || warn "could not remove ${err} — delete it by hand"
    return 0
  fi
  rm -f "$err" || warn "could not remove ${err} — delete it by hand"
  local frc=0 text kind program records
  # Findings cross from python3 as NUL-framed <kind> <text> records written
  # to a file: a path holding a newline stays inside its record.
  program="$(cat <<'PY'
import json
import sys

remedy, out = sys.argv[1], sys.argv[2]
doc = json.load(sys.stdin)
records = []
removable = ["  - worktree {} ({})".format(r["path"], r.get("branch") or "detached") for r in doc["worktrees_removed"]]
removable += ["  - branch {}".format(b) for b in doc["branches_deleted"]]
if removable:
    records.append(("block", "Spent worktrees and branches origin already holds — remove them with `{}`:\n{}".format(
        remedy, "\n".join(removable))))
for k in doc["worktrees_kept"]:
    if k["reason"] in ("dirty", "unpushed"):
        records.append(("report", "Worktree left for the operator: {} ({}), {} — `{}`".format(
            k["path"], k.get("branch") or "detached", k["reason"], k["command"])))
for b in doc["branches_kept"]:
    if b["reason"] == "unpushed":
        records.append(("report", "Branch left for the operator: {}, {} unpushed commit(s) — `{}`".format(
            b["branch"], b["unpushed_commits"], b["command"])))
for f in doc["failed"]:
    records.append(("report", "The worktree check could not decide {}: {}".format(f["target"], f["error"])))
with open(out, "wb") as handle:
    for kind, text in records:
        handle.write(kind.encode() + b"\0" + text.encode("utf-8", "surrogateescape") + b"\0")
PY
)"
  if ! records="$(mktemp)"; then
    warn "mktemp failed — skipping the worktree check; make ${TMPDIR:-/tmp} writable"
    return 0
  fi
  # A Herdr foreman removes worktrees only through the round's sweep
  # (skills/herdr-foreman/references/team-operation.md Writers and Checkouts); everyone else runs
  # the owner script for this repository.
  local remedy
  if [[ -n "${HERDR_ENV+x}" ]]; then
    remedy="bash $(printf '%q' "${here}/../skills/herdr-foreman/sweep-worktrees.sh") $(printf '%q' "${WORKTREE_ROOT:-${HOME}/.worktrees}")"
  else
    remedy="bash $(printf '%q' "$prune") $(printf '%q' "$shared")"
  fi
  python3 -c "$program" "$remedy" "$records" <<<"$out" || frc=$?
  if (( frc != 0 )); then
    warn "the worktree check's JSON could not be read (python3 exited ${frc}) — nothing is blocked on it; run \`bash ${prune} ${shared} --dry-run\` to see its output"
    rm -f "$records" || warn "could not remove ${records} — delete it by hand"
    return 0
  fi
  while IFS= read -r -d '' kind && IFS= read -r -d '' text; do
    case "$kind" in
      block) blocking+=("$text") ;;
      report) reports+=("$text") ;;
    esac
  done < "$records"
  rm -f "$records" || warn "could not remove ${records} — delete it by hand"
  return 0
}

# Diagnostics on the CHANGED set only: uncommitted .sh/.py files. Skips silently
# when nothing lintable changed. shellcheck the .sh, pyright the .py; findings
# block. A required engine being absent is also blocking (rules/language-
# diagnostics.md Install, Don't Skip — the gate cannot clear findings without it).
run_changed_diagnostics() {
  local repo_root
  if ! repo_root="$(git rev-parse --show-toplevel)"; then
    warn "cannot locate the worktree root — restore repository access and re-run the diagnostics gate"
    return 0
  fi
  if ! cd "$repo_root"; then
    warn "cannot enter ${repo_root} — restore worktree access and re-run the diagnostics gate"
    return 0
  fi
  collect_changed_lintable
  (( ${#changed[@]} > 0 )) || return 0

  local f out
  local -a sh_files=() py_files=()
  for f in "${changed[@]}"; do
    case "$f" in
      *.sh) sh_files+=("$f") ;;
      *.py) py_files+=("$f") ;;
    esac
  done

  if (( ${#sh_files[@]} > 0 )); then
    if command -v shellcheck >/dev/null; then
      if ! out="$(shellcheck "${sh_files[@]}" 2>&1)"; then
        blocking+=("shellcheck findings in changed shell files — fix before handoff:"$'\n'"${out}")
      fi
    else
      blocking+=("shellcheck is not installed but changed .sh files need checking — install shellcheck to clear the pre-handoff diagnostics gate (rules/language-diagnostics.md).")
    fi
  fi

  if (( ${#py_files[@]} > 0 )); then
    local py_interp="" pyright_bin="" env_dir candidate
    local -a py_args=()
    for env_dir in "${VIRTUAL_ENV:-}" "$repo_root/.venv" "$repo_root/venv"; do
      [[ -n "$env_dir" ]] || continue
      for candidate in "$env_dir/bin/python" "$env_dir/Scripts/python.exe"; do
        if [[ -f "$candidate" && -x "$candidate" ]]; then
          py_interp="$candidate"
          break
        fi
      done
      [[ -z "$py_interp" ]] || break
    done
    if [[ -n "$py_interp" ]]; then
      py_args=(--pythonpath "$py_interp")
      for candidate in "${py_interp%/*}/pyright" "${py_interp%/*}/pyright.exe"; do
        if [[ -f "$candidate" && -x "$candidate" ]]; then pyright_bin="$candidate"; break; fi
      done
    fi
    if [[ -z "$pyright_bin" ]]; then
      pyright_bin="$(command -v pyright)" || pyright_bin=""
    fi
    if [[ -n "$pyright_bin" ]]; then
      if ! out="$("$pyright_bin" ${py_args[@]+"${py_args[@]}"} "${py_files[@]}" 2>&1)"; then
        blocking+=("pyright findings in changed Python files — check the project environment and fix before handoff (engine: ${pyright_bin}; interpreter: ${py_interp:-Pyright default/config}):"$'\n'"${out}")
      fi
    else
      blocking+=("pyright is not installed but changed .py files need checking — install pyright to clear the pre-handoff diagnostics gate (rules/language-diagnostics.md).")
    fi
  fi
  return 0
}

# Populate `changed` with uncommitted .sh/.py files: tracked changes vs HEAD (or
# the index in an unborn repo) plus untracked files, NUL-safe.
collect_changed_lintable() {
  # NUL-safe capture via a temp file so the producer's exit status stays
  # observable (a process substitution would hide it); warn on failure and fall
  # open to whatever was collected (rules/error-handling.md).
  local f tmp rc=0
  tmp="$(mktemp)" || { warn "mktemp failed — skipping changed-set diagnostics; make ${TMPDIR:-/tmp} writable"; return 0; }

  rc=0
  if git rev-parse --verify -q HEAD >/dev/null; then
    git diff --name-only -z --diff-filter=ACMR HEAD -- '*.sh' '*.py' > "$tmp" || rc=$?
  else
    git diff --name-only -z --diff-filter=ACMR --cached -- '*.sh' '*.py' > "$tmp" || rc=$?
  fi
  (( rc == 0 )) || warn "git diff failed (exit ${rc}) — changed-set diagnostics may be incomplete; run \`git diff --name-only HEAD\` to see why"
  while IFS= read -r -d '' f; do changed+=("$f"); done < "$tmp"

  rc=0
  git ls-files -z --others --exclude-standard -- '*.sh' '*.py' > "$tmp" || rc=$?
  (( rc == 0 )) || warn "git ls-files failed (exit ${rc}) — untracked changes may be missed; run \`git ls-files --others --exclude-standard\` to see why"
  while IFS= read -r -d '' f; do changed+=("$f"); done < "$tmp"

  rm -f "$tmp" || warn "could not remove temp file ${tmp} — delete it by hand"
  return 0
}

# A dirty working tree is report-only — often intentional WIP, never blocks alone.
check_dirty_tree() {
  local status rc=0
  status="$(git status --porcelain)" || rc=$?
  if (( rc != 0 )); then
    warn "git status failed (exit ${rc}) — skipping the dirty-tree report; run \`git status\` here to see why"
    return 0
  fi
  [[ -n "$status" ]] && reports+=("Working tree has uncommitted changes — commit, stash, or discard before handoff.")
  return 0
}

# Emit Claude Code's native Stop block decision with the assembled reason.
emit_block() {
  local reason section
  reason="Pre-handoff hygiene — resolve these local leftovers before finishing:"
  for section in "${blocking[@]}"; do reason+=$'\n\n'"${section}"; done
  if (( ${#reports[@]} > 0 )); then
    local r
    for r in "${reports[@]}"; do reason+=$'\n\n'"${r}"; done
  fi
  jq -n --arg r "$reason" '{decision: "block", reason: $r}' ||
    warn "could not emit the block decision as JSON — allowing stop; check that jq runs, then retry the handoff"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
