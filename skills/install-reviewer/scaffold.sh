#!/usr/bin/env bash
# Scaffold the jbaruch/coding-policy PR review workflow pair into a
# consumer repo: ensure the workflows dir exists, copy both packaged
# templates (OpenAI + Anthropic reviewers), compile them with gh-aw,
# and mark the lock files as generated via .gitattributes. Call after
# creating the feature branch and before committing.
#
# Idempotent per rules/file-hygiene.md: re-running is safe — `mkdir -p`
# no-ops if the dir exists, `cp` rewrites the sources from the templates,
# `gh aw compile` rewrites the locks, and the .gitattributes append
# only happens when the exact rule line is missing. The overwrite-
# safety guard for pre-existing user content lives in the
# install-reviewer skill, which halts before this script runs if the
# repo already has its own review workflow files (install mode); in
# upgrade mode (--override) the existing files are explicitly snapshot
# and replaced.
#
# Atomic: if compile fails for either template, all this script's
# artifacts are rolled back — every target file is restored to its
# pre-scaffold state from a snapshot (or removed if it didn't exist
# before), and .github/aw/actions-lock.json is restored from its own
# snapshot. The caller never sees a half-scaffolded state, and the
# two reviewers always land together or not at all.
#
# Usage: scaffold.sh [--override]
#   --override    Upgrade existing scaffolded reviewers in place. The
#                 four target files (sources + locks) are snapshot
#                 before being replaced; on compile failure they are
#                 restored to their pre-scaffold contents. Without the
#                 flag, this script assumes Step 2's overwrite refusal
#                 in the skill has already verified no targets exist.
# Out:   one JSON object on stdout:
#          {"sources":[...], "locks":[...], "gitattributes":"...", "compiled":true, "override":bool}
# Exit:  0 on success; non-zero with stderr diagnostic on failure

set -euo pipefail

OVERRIDE_MODE=0
for arg in "$@"; do
  case "$arg" in
    --override) OVERRIDE_MODE=1 ;;
    *) echo "error: unknown argument '$arg' (only --override is recognized)" >&2; exit 2 ;;
  esac
done

# Run from repo root so all relative paths resolve the same way regardless
# of the caller's cwd. Refuse to proceed if we're not inside a git repo.
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "error: not inside a git worktree — run from within the consumer repo" >&2
  exit 1
}
cd "$repo_root"

TEMPLATE_DIR=".tessl/tiles/jbaruch/coding-policy/skills/install-reviewer"
WORKFLOW_DIR=".github/workflows"
ACTIONS_LOCK=".github/aw/actions-lock.json"
GITATTRIBUTES=".gitattributes"
LOCK_GENERATED_RULE='.github/workflows/*.lock.yml linguist-generated=true merge=ours'

# Paired reviewer workflows — both scaffold together.
WORKFLOWS=(review-openai review-anthropic)

main() {
  local sources=()
  local locks=()
  for w in "${WORKFLOWS[@]}"; do
    local src="${TEMPLATE_DIR}/${w}.md"
    if [[ ! -f "$src" ]]; then
      echo "error: template not found at ${src} — run 'tessl install jbaruch/coding-policy' first" >&2
      exit 1
    fi
    sources+=("${WORKFLOW_DIR}/${w}.md")
    locks+=("${WORKFLOW_DIR}/${w}.lock.yml")
  done

  # Snapshot the shared gh-aw action lockfile (if present) so compile-failure
  # rollback can restore it verbatim. Consumer repos with other gh-aw workflows
  # use this file too — losing its prior state would break their action pinning.
  local lock_snapshot=""
  if [[ -f "$ACTIONS_LOCK" ]]; then
    lock_snapshot=$(mktemp -t aw-actions-lock.XXXXXX)
    cp "$ACTIONS_LOCK" "$lock_snapshot"
  fi

  # Defense-in-depth: in install mode the skill's Step 2 has already refused
  # if any target file exists. Re-check here so the script is independently
  # safe — calling scaffold.sh directly without --override should refuse to
  # silently overwrite scaffolded files even if the caller skipped Step 2.
  # Use `-e` (exists, any type) rather than `-f` (regular file only) so a
  # directory, symlink-to-dir, or other non-regular entry at the target path
  # is also caught — the subsequent `cp`/compile would behave unexpectedly
  # otherwise. `-L` covers broken symlinks (whose targets don't exist), which
  # `-e` would miss.
  if (( OVERRIDE_MODE == 0 )); then
    local existing=()
    for f in "${sources[@]}" "${locks[@]}"; do
      { [[ -e "$f" ]] || [[ -L "$f" ]]; } && existing+=("$f")
    done
    if [[ ${#existing[@]} -gt 0 ]]; then
      echo "error: target path(s) already exist: ${existing[*]} — pass --override to upgrade in place, or remove them first" >&2
      exit 1
    fi
  fi

  # Refuse symlinks at any path this script can write to, in BOTH modes.
  # Coverage extends beyond the four reviewer source/lock files to also
  # include `.github/aw/actions-lock.json` (rewritten by `gh aw compile`)
  # and `.gitattributes` (the LOCK_GENERATED_RULE marker may be appended).
  # A symlink at any of these (e.g., review-openai.md → some file outside
  # the repo, or .gitattributes → a shared global config) is an
  # unexpected manual configuration this skill doesn't manage: the
  # subsequent `cp`/compile/append would follow the link and overwrite
  # the target rather than replace the link itself, which can clobber an
  # arbitrary file. Forces the consumer to remove the symlink explicitly
  # before running the skill.
  local writable_paths=("${sources[@]}" "${locks[@]}" "$ACTIONS_LOCK" "$GITATTRIBUTES")
  local symlinks=()
  for f in "${writable_paths[@]}"; do
    [[ -L "$f" ]] && symlinks+=("$f")
  done
  if [[ ${#symlinks[@]} -gt 0 ]]; then
    echo "error: writable path(s) are symlinks: ${symlinks[*]} — this skill does not manage symlinked reviewer/config files; remove the symlink(s) and re-run" >&2
    exit 1
  fi

  # Refuse non-regular paths at any path this script can write to, in
  # BOTH modes (after the symlink filter above). A directory, named pipe,
  # socket, or other non-regular entry at a writable path is an
  # unexpected configuration the skill doesn't manage; the snapshot loop
  # below only handles regular files (`-f`), so a non-regular target
  # would slip past snapshotting and cause `cp`/compile/append to behave
  # unexpectedly with no rollback covering it. Same posture as the
  # symlink refusal — force the consumer to clean up before the upgrade.
  local nonregular=()
  for f in "${writable_paths[@]}"; do
    if [[ -e "$f" ]] && [[ ! -f "$f" ]]; then
      nonregular+=("$f")
    fi
  done
  if [[ ${#nonregular[@]} -gt 0 ]]; then
    echo "error: writable path(s) are not regular files: ${nonregular[*]} — this skill expects regular file targets; remove the non-regular entry/entries and re-run" >&2
    exit 1
  fi

  # Snapshot every existing target (sources + locks) so the compile-failure
  # rollback can restore them. In install mode the guard above means this
  # loop is always a no-op. In override mode every target may already exist
  # with the consumer's previously-scaffolded content — without the snapshot,
  # a compile failure mid-upgrade would leave the consumer with a partially-
  # written workspace and no recovery path.
  local -a target_paths=()
  local -a target_snapshots=()
  for f in "${sources[@]}" "${locks[@]}"; do
    if [[ -f "$f" ]]; then
      local snap
      snap=$(mktemp -t aw-target-snap.XXXXXX)
      cp "$f" "$snap"
      target_paths+=("$f")
      target_snapshots+=("$snap")
    fi
  done

  mkdir -p "$WORKFLOW_DIR"
  for w in "${WORKFLOWS[@]}"; do
    cp "${TEMPLATE_DIR}/${w}.md" "${WORKFLOW_DIR}/${w}.md"
  done

  # Record whether .github/aw/ existed before compile so we can remove the
  # empty directory on rollback if compile created it just to write the lock.
  local aw_dir_existed_before=0
  [[ -d "$(dirname "$ACTIONS_LOCK")" ]] && aw_dir_existed_before=1

  if ! gh aw compile "${WORKFLOWS[@]}" >&2; then
    # Rollback: restore each target file from its snapshot if we took one;
    # remove targets that didn't exist before (and thus have no snapshot).
    # Linear lookup against the target_paths/target_snapshots parallel arrays;
    # we deliberately avoid `declare -A` (associative arrays) because Bash 3.2
    # — the default on macOS — doesn't support them, and consumers running
    # the skill locally on macOS would syntax-error here. With at most 4
    # targets the linear scan is trivially fast.
    local f i snap
    for f in "${sources[@]}" "${locks[@]}"; do
      snap=""
      for (( i=0; i < ${#target_paths[@]}; i++ )); do
        if [[ "${target_paths[$i]}" == "$f" ]]; then
          snap="${target_snapshots[$i]}"
          break
        fi
      done
      if [[ -n "$snap" ]]; then
        cp "$snap" "$f"
        rm -f "$snap"
      else
        rm -f "$f"
      fi
    done
    if [[ -n "$lock_snapshot" ]]; then
      cp "$lock_snapshot" "$ACTIONS_LOCK"
      rm -f "$lock_snapshot"
    else
      # actions-lock.json didn't exist before; if compile created it, remove it.
      rm -f "$ACTIONS_LOCK"
      # If the directory itself didn't exist before and is now empty, remove
      # it too so the rollback leaves no trace.
      if [[ $aw_dir_existed_before -eq 0 ]]; then
        rmdir "$(dirname "$ACTIONS_LOCK")" 2>/dev/null || true
      fi
    fi
    echo "error: 'gh aw compile ${WORKFLOWS[*]}' failed — rolled back ${sources[*]}, ${locks[*]}, and restored prior state of ${ACTIONS_LOCK}" >&2
    exit 1
  fi

  # Compile succeeded — discard every snapshot.
  [[ -n "$lock_snapshot" ]] && rm -f "$lock_snapshot"
  for snap in "${target_snapshots[@]}"; do
    rm -f "$snap"
  done

  # Sanitize the generated lock files. `gh aw compile` emits two formatting
  # drifts that violate rules/code-formatting.md "Basics" in any consumer
  # repo that runs this tile's reviewers against itself:
  #   1. Trailing whitespace on every line of the leading ASCII-art banner
  #      (and a few blank lines elsewhere).
  #   2. Files end with two trailing newlines (\n\n) instead of a single \n.
  # Stripping both here keeps the consumer's working tree compliant
  # immediately after scaffold; without it, the consumer's first PR review
  # would flag its own freshly-scaffolded lockfiles as a formatting violation.
  #
  # Sanitization runs AFTER the compile-success rollback boundary above, so
  # any tool failure here is best-effort cleanup, not grounds for hard-failing
  # and leaving the consumer with half-sanitized locks. perl is standard on
  # Linux/macOS runners but the contract shouldn't rely on that — the
  # `|| true` opts out of `set -e` so a missing perl/sed (or any cleanup
  # error) still lets the scaffold complete. If sanitization is skipped,
  # the upstream gh-aw drift remains in the lock file and the reviewer
  # flags it on the consumer's first PR; that's a worse experience than
  # the sanitization landing, but a strictly better experience than rolling
  # back the whole scaffold after compile succeeded.
  for l in "${locks[@]}"; do
    sed -i.bak -E 's/[[:space:]]+$//' "$l" 2>/dev/null && rm -f "${l}.bak" || true
    # Collapse trailing whitespace at EOF (including blank trailing lines)
    # to a single newline. perl -0 reads the whole file as one record;
    # \s+\z matches any run of whitespace at the absolute end.
    perl -i -0pe 's/\s+\z/\n/' "$l" 2>/dev/null || true
  done

  # Ensure the lock files are marked as generated artifacts per
  # rules/file-hygiene.md. Idempotent — appends only if the exact line
  # is not already present, so existing consumer-managed .gitattributes
  # entries are not clobbered. The wildcard pattern covers both lock files.
  if [[ ! -f "$GITATTRIBUTES" ]] || ! grep -qxF "$LOCK_GENERATED_RULE" "$GITATTRIBUTES"; then
    # If the file exists and doesn't end in a newline, add one first so the
    # appended line lands on its own row.
    if [[ -f "$GITATTRIBUTES" && -s "$GITATTRIBUTES" && -n "$(tail -c 1 "$GITATTRIBUTES")" ]]; then
      printf '\n' >> "$GITATTRIBUTES"
    fi
    printf '%s\n' "$LOCK_GENERATED_RULE" >> "$GITATTRIBUTES"
    # Defensive: the printf above should leave a trailing \n, but in practice
    # the file lands without one (mechanism unidentified). Mirror the lock
    # sanitation to guarantee rules/code-formatting.md's single-newline basic.
    perl -i -0pe 's/\s+\z/\n/' "$GITATTRIBUTES" 2>/dev/null || true
  fi

  local override_json="false"
  (( OVERRIDE_MODE == 1 )) && override_json="true"

  # Emit a JSON summary — arrays of the scaffolded sources and locks so the
  # caller (or a watching human) can see exactly which files landed.
  jq -n \
    --argjson sources "$(printf '%s\n' "${sources[@]}" | jq -R . | jq -s .)" \
    --argjson locks "$(printf '%s\n' "${locks[@]}" | jq -R . | jq -s .)" \
    --arg gitattributes "$GITATTRIBUTES" \
    --argjson override "$override_json" \
    '{sources: $sources, locks: $locks, gitattributes: $gitattributes, compiled: true, override: $override}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
