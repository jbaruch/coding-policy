#!/usr/bin/env bash
# Copy the fleet-reviewer opt-in files into the consumer repo:
#   .github/fleet-review-enabled            — opt-in marker; the central
#                                             coding-policy-fleet-reviewer App polls
#                                             and reviews every repo carrying it
#   .github/workflows/review-trigger.yml    — PR-time trigger; fires an immediate
#                                             single-PR review so the verdict lands
#                                             before merge (the poll is a backstop)
#   .github/copilot-instructions.md         — the Copilot complementary lane
# and document the one operator secret the trigger needs:
#   .env.example                            — appends a FLEET_DISPATCH_TOKEN entry
#                                             with the repo's Actions-secrets URL
#                                             (no-secrets rule); append-or-create,
#                                             idempotent, never overwrites prior vars
#
# The consumer holds the marker, the trigger workflow, and one FLEET_DISPATCH_TOKEN
# secret that the workflow reads.
#
# Every target is snapshotted before any write and restored if a later write
# fails, so a partial run never leaves a half-written reviewer.
#
# Usage: scaffold.sh [--override]
#   --override   Upgrade mode — overwrite existing template targets. Install mode
#                refuses if any template target already exists (the skill's Step 2
#                gates that). .env.example is always append-or-create in both modes.
# Out:   one JSON object on stdout:
#          {"state":"scaffolded|no-op","override":bool,
#           "files":[{"target":"...","action":"created|overwritten|appended|unchanged"}]}
# Exit:  0 on success (including no-op); non-zero with a stderr diagnostic on
#        failure (targets restored to their prior contents first).

set -euo pipefail

TEMPLATE_DIR=".tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/templates"
# "<source relative to TEMPLATE_DIR>:<target in the consumer repo>"
# The marker and the trigger source carry a `.md` shim extension because tessl
# packaging ships only .md/.sh/.json/.py — a `.yml` or extensionless template is
# dropped from the installed plugin, so scaffold would find no source. Keep the
# `.md` suffix; the target names below are what the consumer actually gets.
MANIFEST=(
  "fleet-review-enabled.md:.github/fleet-review-enabled"
  "review-trigger.yml.md:.github/workflows/review-trigger.yml"
  "copilot-instructions.md:.github/copilot-instructions.md"
)

# .env.example is handled separately from MANIFEST: it is appended-to (not
# overwritten), so a pre-existing one is never refused, and the entry is only
# added when absent (idempotent).
ENV_FILE=".env.example"
ENV_SECRET="FLEET_DISPATCH_TOKEN"

# Resolve the GitHub Actions secrets-settings URL for this repo from its origin
# remote (https or ssh form). Preflight already requires an origin remote, so
# real runs resolve a concrete URL; the placeholder only guards odd setups so
# scaffold never fails purely on URL derivation.
derive_settings_url() {
  local url slug
  url=$(git remote get-url origin 2>/dev/null) \
    || { printf '%s' 'https://github.com/<owner>/<repo>/settings/secrets/actions'; return; }
  # Reduce every GitHub remote form to owner/repo: strip an optional scheme
  # (https://, ssh://, git://), an optional user@, the host plus its `:` or `/`
  # separator (covers scp-like `git@host:owner/repo` and `ssh://host/owner/repo`),
  # then a trailing `.git` and slashes.
  slug=$(printf '%s' "$url" | sed -E 's#^[a-zA-Z][a-zA-Z0-9+.-]*://##; s#^[^@/]+@##; s#^[^/:]+[:/]##; s#\.git$##; s#/+$##')
  if [[ "$slug" == */* ]]; then
    printf 'https://github.com/%s/settings/secrets/actions' "$slug"
  else
    printf '%s' 'https://github.com/<owner>/<repo>/settings/secrets/actions'
  fi
}

# The FLEET_DISPATCH_TOKEN documentation block that seeds or is appended to
# .env.example. Carries the settings deep link inline so it is self-contained
# regardless of the file's existing header.
env_block() {
  local url="$1"
  cat <<EOF
# GitHub Actions secret — set in repo Settings → Secrets and variables → Actions,
# NOT copied into .env:
#   ${url}
# ${ENV_SECRET} — the token .github/workflows/review-trigger.yml reads to start the
#   review run in jbaruch/coding-policy. Create it with Actions: write on
#   jbaruch/coding-policy.
${ENV_SECRET}=
EOF
}

main() {
  local OVERRIDE_MODE=0 arg
  for arg in "$@"; do
    case "$arg" in
      --override) OVERRIDE_MODE=1 ;;
      *) echo "error: unknown argument '$arg' (only --override is recognized)" >&2; exit 2 ;;
    esac
  done

  command -v jq >/dev/null 2>&1 \
    || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; exit 2; }

  local repo_root
  repo_root=$(git rev-parse --show-toplevel 2>/dev/null) \
    || { echo "error: not inside a git worktree — run from within the consumer repo" >&2; exit 1; }
  cd "$repo_root"

  local pair src tgt
  for pair in "${MANIFEST[@]}"; do
    src="${TEMPLATE_DIR}/${pair%%:*}"
    [[ -f "$src" ]] || { echo "error: template not found: $src — run 'tessl install jbaruch/coding-policy' first" >&2; exit 1; }
  done

  # Refuse symlink targets; in install mode refuse pre-existing template targets.
  for pair in "${MANIFEST[@]}"; do
    tgt="${pair#*:}"
    [[ -L "$tgt" ]] && { echo "error: ${tgt} is a symlink — refusing to write through it; replace it with a regular file (or remove it) and re-run" >&2; exit 1; }
    if [[ -e "$tgt" && ! -f "$tgt" ]]; then
      echo "error: ${tgt} exists but is not a regular file (directory, FIFO, or device) — refusing to write; remove it and re-run" >&2
      exit 1
    fi
    if (( OVERRIDE_MODE == 0 )) && [[ -e "$tgt" ]]; then
      echo "error: ${tgt} already exists — refusing to overwrite in install mode; re-run in upgrade mode (--override) to refresh" >&2
      exit 1
    fi
  done

  # .env.example: refuse only a symlink / non-regular file. A regular pre-existing
  # one is appended to (never refused), so install mode does not gate on it.
  [[ -L "$ENV_FILE" ]] && { echo "error: ${ENV_FILE} is a symlink — refusing to write through it; replace it with a regular file (or remove it) and re-run" >&2; exit 1; }
  if [[ -e "$ENV_FILE" && ! -f "$ENV_FILE" ]]; then
    echo "error: ${ENV_FILE} exists but is not a regular file (directory, FIFO, or device) — refusing to write; remove it and re-run" >&2
    exit 1
  fi
  if [[ -f "$ENV_FILE" && ! -r "$ENV_FILE" ]]; then
    echo "error: ${ENV_FILE} exists but is not readable — fix its permissions and re-run" >&2
    exit 1
  fi

  # Snapshot existing targets for rollback. snap_existed[i] tracks each; env_existed
  # tracks .env.example. Both are set before the ERR trap is armed.
  local SNAP_DIR i=0
  SNAP_DIR=$(mktemp -d) || { echo "error: mktemp -d failed — check TMPDIR is writable" >&2; exit 1; }
  local -a snap_existed=()
  for pair in "${MANIFEST[@]}"; do
    tgt="${pair#*:}"
    if [[ -f "$tgt" ]]; then cp "$tgt" "${SNAP_DIR}/$i"; snap_existed[i]=1; else snap_existed[i]=0; fi
    i=$((i + 1))
  done
  local env_existed=0
  if [[ -f "$ENV_FILE" ]]; then cp "$ENV_FILE" "${SNAP_DIR}/env"; env_existed=1; fi

  # Best-effort rollback under `set +e` (rules/error-handling.md — warn, never nothing).
  restore() {
    local j=0 p t
    for p in "${MANIFEST[@]}"; do
      t="${p#*:}"
      if (( snap_existed[j] == 1 )); then
        cp "${SNAP_DIR}/$j" "$t" || echo "scaffold.sh: warning: could not restore ${t} — restore it by hand" >&2
      else
        rm -f "$t" || echo "scaffold.sh: warning: could not remove ${t} during rollback — remove it by hand" >&2
      fi
      j=$((j + 1))
    done
    if (( env_existed == 1 )); then
      cp "${SNAP_DIR}/env" "$ENV_FILE" || echo "scaffold.sh: warning: could not restore ${ENV_FILE} — restore it by hand" >&2
    else
      rm -f "$ENV_FILE" || echo "scaffold.sh: warning: could not remove ${ENV_FILE} during rollback — remove it by hand" >&2
    fi
  }
  on_err() {
    local rc=$?
    trap - ERR
    set +e
    restore
    echo "error: scaffold failed (rc=${rc}) — targets restored to their prior contents" >&2
    rm -rf "$SNAP_DIR" || echo "scaffold.sh: warning: could not remove temp dir ${SNAP_DIR} — remove it by hand" >&2
    exit "$rc"
  }
  trap on_err ERR

  # Copy each template to its target.
  local results="[]" action
  i=0
  for pair in "${MANIFEST[@]}"; do
    src="${TEMPLATE_DIR}/${pair%%:*}"; tgt="${pair#*:}"
    mkdir -p "$(dirname "$tgt")"
    action="created"
    (( snap_existed[i] == 1 )) && action="overwritten"
    cp "$src" "$tgt"
    case "$tgt" in *.sh) chmod +x "$tgt" ;; esac
    if (( snap_existed[i] == 1 )) && cmp -s "$tgt" "${SNAP_DIR}/$i"; then action="unchanged"; fi
    results=$(jq -c --arg t "$tgt" --arg a "$action" '. + [{target:$t, action:$a}]' <<<"$results")
    i=$((i + 1))
  done

  # Document FLEET_DISPATCH_TOKEN in .env.example (no-secrets rule). After scaffold
  # the file carries the full block: purpose, source, the repo's Actions-secrets
  # deep link (in the header), and the placeholder assignment. "Already documented"
  # requires BOTH the assignment placeholder AND the deep link — a bare assignment
  # or a prose mention alone is not enough. Otherwise (re)build: prepend the block
  # and drop any pre-existing bare assignment so the placeholder is never duplicated.
  # grep status is read explicitly (0 match / 1 no-match / >=2 read error) per
  # rules/error-handling.md — never collapsed.
  local env_action url
  url=$(derive_settings_url)
  if (( env_existed == 0 )); then
    env_block "$url" > "$ENV_FILE"
    env_action="created"
  else
    local rc_assign=0 rc_link=0
    grep -qE "^[[:space:]]*${ENV_SECRET}=" "$ENV_FILE" || rc_assign=$?
    grep -qF 'settings/secrets/actions'    "$ENV_FILE" || rc_link=$?
    (( rc_assign <= 1 )) || { echo "error: reading ${ENV_FILE} failed (grep exit ${rc_assign}) — check it is a readable text file and re-run" >&2; false; }
    (( rc_link   <= 1 )) || { echo "error: reading ${ENV_FILE} failed (grep exit ${rc_link}) — check it is a readable text file and re-run" >&2; false; }
    if (( rc_assign == 0 && rc_link == 0 )); then
      env_action="unchanged"
    else
      local env_tmp; env_tmp=$(mktemp) || { echo "error: mktemp failed while updating ${ENV_FILE}" >&2; false; }
      # Prepend the block (deep link in header); keep every existing line except a
      # bare placeholder, which the block now supplies. grep -v exit 1 (all lines
      # stripped) is a valid empty remainder; only exit >=2 is a real error.
      { env_block "$url"; printf '\n'; grep -vE "^[[:space:]]*${ENV_SECRET}=" "$ENV_FILE" || (( $? == 1 )); } > "$env_tmp"
      mv "$env_tmp" "$ENV_FILE"
      env_action="appended"
    fi
  fi
  results=$(jq -c --arg t "$ENV_FILE" --arg a "$env_action" '. + [{target:$t, action:$a}]' <<<"$results")

  local state="scaffolded"
  jq -e 'all(.[]; .action=="unchanged")' <<<"$results" >/dev/null && state="no-op"

  local override_json="false"
  (( OVERRIDE_MODE == 1 )) && override_json="true"

  trap - ERR
  rm -rf "$SNAP_DIR"

  jq -n --arg state "$state" --argjson override "$override_json" --argjson files "$results" \
    '{state: $state, override: $override, files: $files}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
