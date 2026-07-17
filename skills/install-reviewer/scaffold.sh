#!/usr/bin/env bash
# Scaffold the jbaruch/coding-policy PR review workflow pair into a
# consumer repo: ensure the workflows dir exists, copy both packaged
# templates (OpenAI + Anthropic reviewers), compile them with gh-aw,
# mark the lock files as generated via .gitattributes, and document the
# reviewer CI secrets in .env.example per rules/no-secrets.md. Call after
# creating the feature branch and before committing.
#
# Idempotent per rules/file-hygiene.md: re-running is safe — `mkdir -p`
# no-ops if the dir exists, `cp` rewrites the sources from the templates,
# `gh aw compile` rewrites the locks, the .gitattributes append only
# happens when the exact rule line is missing, and the .env.example
# update adds the deep-link header and any missing secrets while
# preserving existing consumer entries. The overwrite-
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
#          {"sources":[...], "locks":[...], "gitattributes":"...", "env_example":"...", "compiled":true, "override":bool}
# Exit:  0 on success; non-zero with stderr diagnostic on failure

set -euo pipefail

# Append the lock-generated-rule marker to $1 if it isn't already
# present, then ensure the file ends with exactly one trailing newline.
# Running twice is a no-op once the file is in canonical state.
#
# EOF normalization runs whenever the file exists, NOT only on the
# marker-absent branch: the bug this function exists to address is
# .gitattributes landing without a trailing newline, and rerunning
# the scaffold on a previously-affected consumer (marker present but
# trailing newline missing) must repair that state. Skipping the
# normalization on marker-present would trap such consumers in the
# broken state until they hand-edit.
#
# Two-stage normalization. The bash-only stage is the AUTHORITATIVE
# fix for the reported missing-newline failure mode and never fails
# silently: if the last byte isn't a newline, append one with printf.
# The perl stage is a follow-up that ALSO collapses multiple trailing
# newlines to one (\s*\z → \n, where \s*\z matches even a non-whitespace
# EOF; \s+\z would no-op on the exact reported failure mode). Perl is
# best-effort so a missing perl binary doesn't fail the scaffold —
# the missing-newline fix landed via the bash stage in that case.
# Echo the 1-based line number of the first line in <file> matching
# <pattern>, or nothing when nothing matches. <mode> is grep's pattern
# flag: -F fixed-string, -E extended-regex.
#
# The explicit rc branch is the point. `grep ... || true` collapses grep's
# exit 1 (no match — a real, expected state) with its exit 2 (unreadable
# file, bad pattern — a fault), so a failed READ reports as "not present"
# and the caller rewrites a file it never actually inspected.
first_match_line() {
  local mode="$1" pattern="$2" file="$3"
  local out rc=0
  # `grep -m1` stops at the first match, so nothing downstream closes a pipe
  # on a still-writing grep. Extract with parameter expansion rather than
  # `| head -1 | cut`: under `pipefail` that pipeline reports the rightmost
  # non-zero status, and a SIGPIPE'd printf (rc 141) would become this
  # function's rc — which the caller assigns under `set -e`, aborting the
  # scaffold on a successful lookup.
  out=$(grep -n -m1 "$mode" -e "$pattern" -- "$file") || rc=$?
  case "$rc" in
    0)
      out="${out%%$'\n'*}"   # first line
      printf '%s\n' "${out%%:*}"   # line number, before grep -n's ':'
      ;;
    1) ;;  # no match: echo nothing, caller treats empty as absent
    *)
      echo "scaffold.sh: grep failed (rc=${rc}) reading ${file} — cannot determine the file's layout; verify it is readable, then re-run" >&2
      return 2
      ;;
  esac
}

# Strip trailing whitespace and collapse EOF whitespace in one compiled
# lock file. Best-effort by design — see the caller for why hard-failing
# after a successful compile is the worse outcome.
#
# Best-effort means "does not exit non-zero"; it does NOT mean silent. The
# previous `|| true` hid which tool was missing, so a consumer whose runner
# lacked perl saw the reviewer flag upstream gh-aw drift on their first PR
# with nothing connecting it to a cleanup that never ran here.
sanitize_lock() {
  local l="$1"
  if sed -i.bak -E 's/[[:space:]]+$//' "$l" 2>/dev/null; then
    # `if ! rm` rather than a bare `rm`: this function is best-effort by
    # design — it runs after the compile-success rollback boundary — and a
    # bare `rm` under `set -e` hard-fails the scaffold on a failed backup
    # cleanup, the exact outcome the leniency exists to prevent.
    if ! rm -f "${l}.bak"; then
      echo "scaffold.sh: warning: could not remove backup ${l}.bak — remove it by hand" >&2
    fi
  else
    echo "scaffold.sh: warning: sed failed on ${l} — skipped trailing-whitespace strip; the reviewer may flag gh-aw drift on the first PR" >&2
  fi
  # perl -0 reads the whole file as one record; \s+\z matches any run of
  # whitespace at the absolute end. Deliberately \s+\z, not the \s*\z used
  # by collapse_trailing_newlines — see that function's caller.
  if ! perl -i -0pe 's/\s+\z/\n/' "$l" 2>/dev/null; then
    echo "scaffold.sh: warning: perl failed on ${l} — skipped EOF-whitespace collapse; the reviewer may flag gh-aw drift on the first PR" >&2
  fi
  return 0
}

# Best-effort snapshot/temp cleanup. Removing a snapshot or temp file must
# never abort the operation: on the rollback path a bare `rm` failing under
# `set -e` would swallow the compile-failure diagnostic below; on the success
# path it would fail a scaffold that already succeeded. Warn, never silence
# (rules/error-handling.md Shell Error Handling). Skips empty args so callers
# can pass an unset optional snapshot without a guard.
discard() {
  local f
  for f in "$@"; do
    if [[ -n "$f" ]] && ! rm -f "$f"; then
      echo "scaffold.sh: warning: could not remove ${f} — remove it by hand" >&2
    fi
  done
}

ensure_gitattributes_marker() {
  local target="$1" rule="$2"
  if [[ ! -f "$target" ]] || ! grep -qxF "$rule" "$target"; then
    if [[ -f "$target" && -s "$target" && -n "$(tail -c 1 "$target")" ]]; then
      printf '\n' >> "$target"
    fi
    printf '%s\n' "$rule" >> "$target"
  fi
  if [[ -f "$target" && -s "$target" && -n "$(tail -c 1 "$target")" ]]; then
    printf '\n' >> "$target"
  fi
  if [[ -f "$target" ]]; then
    collapse_trailing_newlines "$target"
  fi
}

# Collapse trailing whitespace at EOF to a single newline. Best-effort by
# design: the bash stage above already guaranteed the file ENDS with a
# newline, so a missing perl costs cosmetic tidiness, not correctness, and
# hard-failing here would abandon a scaffold that otherwise succeeded.
#
# Best-effort means "does not exit non-zero" — it does NOT mean silent.
# The previous `|| true` swallowed the reason entirely, so a runner without
# perl produced a subtly different file than a runner with one and nothing
# said why. A warning keeps the lenient behaviour and makes the divergence
# explainable.
collapse_trailing_newlines() {
  local file="$1"
  if ! command -v perl >/dev/null; then
    echo "scaffold.sh: warning: perl not found — skipped trailing-newline cleanup on ${file} (file still ends with a newline; cosmetic only)" >&2
    return 0
  fi
  if ! perl -i -0pe 's/\s*\z/\n/' "$file" 2>/dev/null; then
    echo "scaffold.sh: warning: perl failed on ${file} — skipped trailing-newline cleanup (file still ends with a newline; cosmetic only)" >&2
  fi
  return 0
}

# Parse the "owner/repo" slug from a git remote URL. Handles the three
# forms git emits — SCP-style SSH (git@github.com:owner/repo.git), ssh://
# URLs (ssh://git@github.com/owner/repo.git), and HTTPS
# (https://github.com/owner/repo.git), each with an optional trailing
# slash — by stripping any trailing slash/.git suffix and taking the last
# two path segments. The owner segment is bounded by the last `/` OR `:`
# so the SCP host:path separator is handled without a regex. Echoes the
# slug on success; non-zero (no echo) on an empty or unparseable URL.
# Pure function — no git calls — so the enumerable parsing is
# unit-testable in isolation.
parse_repo_slug_from_url() {
  local url="$1"
  [[ -n "$url" ]] || return 1
  # Strip a trailing slash BEFORE the .git suffix so the `.git/` form
  # (a remote URL copied with a trailing slash) still loses both — the
  # reverse order leaves `.git` attached because the string ends in `/`.
  url="${url%/}"
  url="${url%.git}"
  url="${url%/}"
  local repo="${url##*/}"
  local rest="${url%/*}"
  local owner="${rest##*[/:]}"
  [[ -n "$owner" && -n "$repo" && "$owner" != "$rest" ]] || return 1
  printf '%s/%s' "$owner" "$repo"
}

# Resolve the origin remote's owner/repo slug for the .env.example
# secrets-settings deep link. Non-zero (no echo) when origin is unset —
# the caller falls back to a literal placeholder so the file still
# scaffolds.
derive_repo_slug() {
  local url
  url=$(git remote get-url origin 2>/dev/null) || return 1
  parse_repo_slug_from_url "$url"
}

# Ensure $1 documents the reviewer CI secrets per rules/no-secrets.md. A
# secret is "present" when the file already has a line beginning `KEY=`;
# existing consumer content is preserved verbatim. The GH Actions
# secrets-settings deep link ($2 = "owner/repo" slug) must sit "in the
# file header" per no-secrets.md. A link counts as in-header only when
# THIS repo's slug-specific link precedes the first `KEY=` assignment;
# when the file lacks such a header link (no link, a different repo's
# link, or one sitting below the body) the whole reviewer block (header
# comment + any missing KEY= lines) is PREPENDED above the consumer's
# existing entries — not appended below them. When the correct header
# link is already present only the missing KEY= lines are appended.
# Idempotent: every secret present AND the correct header link already
# there is a no-op. EOF is normalized to a single trailing newline per
# rules/code-formatting.md.
ensure_env_example() {
  local target="$1" slug="$2"
  local secrets=(CODEX_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY TESSL_TOKEN)
  local missing=() k
  for k in "${secrets[@]}"; do
    if [[ ! -f "$target" ]] || ! grep -qE "^${k}=" "$target"; then
      missing+=("$k")
    fi
  done
  # The link counts as "in the header" only when THIS repo's link
  # (slug-specific, not just any settings/secrets/actions URL) precedes
  # the first variable assignment (`KEY=`). A link for a different repo,
  # or one sitting below the body, does not satisfy no-secrets.md — both
  # are treated as a missing header link, so the block is prepended to put
  # the correct link at the top.
  local expected_link="https://github.com/${slug}/settings/secrets/actions"
  local link_in_header=0
  if [[ -f "$target" ]]; then
    local link_ln var_ln
    link_ln=$(first_match_line -F "$expected_link" "$target")
    var_ln=$(first_match_line -E '^[A-Za-z_][A-Za-z0-9_]*=' "$target")
    if [[ -n "$link_ln" ]] && { [[ -z "$var_ln" ]] || [[ "$link_ln" -lt "$var_ln" ]]; }; then
      link_in_header=1
    fi
  fi
  if [[ ${#missing[@]} -eq 0 && $link_in_header -eq 1 ]]; then
    return 0
  fi

  if [[ $link_in_header -eq 0 ]]; then
    # No header link yet — prepend the reviewer block so the deep link
    # lands in the file header. Build the block in a tempfile, append the
    # consumer's existing content beneath it, then rewrite target in
    # place (cat > target preserves the original file's permissions).
    local tmp
    tmp=$(mktemp -t aw-env-example.XXXXXX)
    {
      printf '# CI reviewer secrets for the jbaruch/coding-policy gh-aw PR review workflows.\n'
      printf '# Set as GitHub Actions repository secrets:\n'
      printf '#   https://github.com/%s/settings/secrets/actions\n' "$slug"
      printf '# The Codex (OpenAI-family) reviewer reads CODEX_API_KEY or OPENAI_API_KEY\n'
      printf '# (CODEX_API_KEY wins when both are set). ANTHROPIC_API_KEY drives the Claude\n'
      # shellcheck disable=SC2016  # backticks are literal markdown in the generated comment; no shell expansion intended
      printf '# reviewer; TESSL_TOKEN authenticates the `tessl install` step for both.\n'
      if [[ ${#missing[@]} -gt 0 ]]; then
        for k in "${missing[@]}"; do
          printf '%s=\n' "$k"
        done
      fi
    } > "$tmp"
    if [[ -f "$target" && -s "$target" ]]; then
      printf '\n' >> "$tmp"
      cat "$target" >> "$tmp"
    fi
    cat "$tmp" > "$target"
    discard "$tmp"
  else
    # Link already in the header — the header block (with its context) is
    # at the top, but one or more KEY= lines are missing. Append them as a
    # labeled, contextual group rather than bare KEY= lines at EOF, so the
    # reviewer secrets stay grouped-with-context whether or not the header
    # block pre-existed (issue #163). Idempotent: once appended the keys
    # are present, so the early-return guard skips this block on re-runs.
    if [[ -f "$target" && -s "$target" && -n "$(tail -c 1 "$target")" ]]; then
      printf '\n' >> "$target"
    fi
    {
      printf '\n# CI reviewer secret(s) for the jbaruch/coding-policy gh-aw PR review\n'
      printf '# workflows — set as GitHub Actions repository secrets (see the secrets\n'
      printf '# link in the header above).\n'
      for k in "${missing[@]}"; do
        printf '%s=\n' "$k"
      done
    } >> "$target"
  fi

  # Normalize EOF to a single trailing newline — the prepended consumer
  # content may have lacked one. Two-stage, same as ensure_gitattributes_marker.
  if [[ -f "$target" && -s "$target" && -n "$(tail -c 1 "$target")" ]]; then
    printf '\n' >> "$target"
  fi
  collapse_trailing_newlines "$target"
}

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

TEMPLATE_DIR=".tessl/plugins/jbaruch/coding-policy/skills/install-reviewer"
WORKFLOW_DIR=".github/workflows"
ACTIONS_LOCK=".github/aw/actions-lock.json"
GITATTRIBUTES=".gitattributes"
ENV_EXAMPLE=".env.example"
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
  # include `.github/aw/actions-lock.json` (rewritten by `gh aw compile`),
  # `.gitattributes` (the LOCK_GENERATED_RULE marker may be appended), and
  # `.env.example` (reviewer secrets block may be appended).
  # A symlink at any of these (e.g., review-openai.md → some file outside
  # the repo, or .gitattributes → a shared global config) is an
  # unexpected manual configuration this skill doesn't manage: the
  # subsequent `cp`/compile/append would follow the link and overwrite
  # the target rather than replace the link itself, which can clobber an
  # arbitrary file. Forces the consumer to remove the symlink explicitly
  # before running the skill.
  local writable_paths=("${sources[@]}" "${locks[@]}" "$ACTIONS_LOCK" "$GITATTRIBUTES" "$ENV_EXAMPLE")
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
        discard "$snap"
      else
        discard "$f"
      fi
    done
    if [[ -n "$lock_snapshot" ]]; then
      cp "$lock_snapshot" "$ACTIONS_LOCK"
      discard "$lock_snapshot"
    else
      # actions-lock.json didn't exist before; if compile created it, remove it.
      discard "$ACTIONS_LOCK"
      # If the directory itself didn't exist before and is now empty, remove
      # it too so the rollback leaves no trace.
      if [[ $aw_dir_existed_before -eq 0 ]]; then
        # rmdir exits non-zero when the directory is non-empty — expected
        # whenever the consumer has other content there, and the right
        # outcome: a rollback must not delete files it did not create.
        #
        # Probe emptiness with `find`, not `$(ls -A)`: this runs on the
        # error path under `set -e`, where a failing command substitution
        # (an unreadable directory) would abort the rollback before it
        # emits the compile-failure diagnostic below — losing the message
        # the operator actually needs to a cleanup detail. An unreadable
        # directory warns and leaves the directory alone.
        local aw_dir; aw_dir="$(dirname "$ACTIONS_LOCK")"
        if [[ -d "$aw_dir" ]]; then
          local entries find_rc=0
          entries=$(find "$aw_dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null) || find_rc=$?
          if [[ $find_rc -ne 0 ]]; then
            echo "scaffold.sh: warning: could not inspect ${aw_dir} (find rc=${find_rc}) — leaving it in place; remove it by hand if the scaffold created it" >&2
          elif [[ -z "$entries" ]]; then
            rmdir "$aw_dir" || echo "scaffold.sh: warning: could not remove empty ${aw_dir} — remove it by hand" >&2
          fi
        fi
      fi
    fi
    echo "error: 'gh aw compile ${WORKFLOWS[*]}' failed — rolled back ${sources[*]}, ${locks[*]}, and restored prior state of ${ACTIONS_LOCK}" >&2
    exit 1
  fi

  # Compile succeeded — discard every snapshot.
  discard "$lock_snapshot"
  for snap in "${target_snapshots[@]}"; do
    discard "$snap"
  done

  # Sanitize the generated lock files. `gh aw compile` emits two formatting
  # drifts that violate rules/code-formatting.md "Basics" in any consumer
  # repo that runs this plugin's reviewers against itself:
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
  # Linux/macOS runners but the contract shouldn't rely on that. If
  # sanitization is skipped, the upstream gh-aw drift remains in the lock
  # file and the reviewer flags it on the consumer's first PR; that's a worse
  # experience than the sanitization landing, but a strictly better experience
  # than rolling back the whole scaffold after compile succeeded.
  for l in "${locks[@]}"; do
    sanitize_lock "$l"
  done

  # Ensure the lock files are marked as generated artifacts per
  # rules/file-hygiene.md. Idempotent — appends only if the exact line
  # is not already present, so existing consumer-managed .gitattributes
  # entries are not clobbered. The wildcard pattern covers both lock files.
  ensure_gitattributes_marker "$GITATTRIBUTES" "$LOCK_GENERATED_RULE"

  # Document the reviewer CI secrets in .env.example per rules/no-secrets.md.
  # Idempotent merge — appends only the secrets missing from an existing
  # consumer file, never rewrites their content. The deep-link header is
  # built from the origin remote; if origin is unset (preflight normally
  # guarantees it) fall back to a literal placeholder so the file still
  # scaffolds with a fill-in-the-blank link.
  local repo_slug
  repo_slug=$(derive_repo_slug) || repo_slug="<owner>/<repo>"
  ensure_env_example "$ENV_EXAMPLE" "$repo_slug"

  local override_json="false"
  (( OVERRIDE_MODE == 1 )) && override_json="true"

  # Emit a JSON summary — arrays of the scaffolded sources and locks so the
  # caller (or a watching human) can see exactly which files landed.
  jq -n \
    --argjson sources "$(printf '%s\n' "${sources[@]}" | jq -R . | jq -s .)" \
    --argjson locks "$(printf '%s\n' "${locks[@]}" | jq -R . | jq -s .)" \
    --arg gitattributes "$GITATTRIBUTES" \
    --arg env_example "$ENV_EXAMPLE" \
    --argjson override "$override_json" \
    '{sources: $sources, locks: $locks, gitattributes: $gitattributes, env_example: $env_example, compiled: true, override: $override}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
