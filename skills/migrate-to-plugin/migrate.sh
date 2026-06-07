#!/usr/bin/env bash
# Deterministic mechanics of migrating a legacy `tile.json` plugin to the
# `.tessl-plugin/plugin.json` form. The migrate-to-plugin skill runs this
# first; the agent then reconciles prose terminology (a judgment step the
# script does NOT make — see SKILL.md Step 2).
#
# What this does, in order, only when a legacy manifest is present:
#   1. detect state (tile.json present, plugin.json present)
#   2. `tessl plugin migrate` — synthesize .tessl-plugin/plugin.json
#   3. confirm plugin.json now exists (migrate succeeded)
#   4. rename .tileignore -> .tesslignore (idempotent)
#   5. remove the now-obsolete tile.json (plugin.json is authoritative;
#      the file is git-tracked, so the removal is recoverable)
#   6. `tessl plugin lint`
#   7. scan for residual hardcoded "tile" references the agent must
#      reconcile by hand — a COUNT of a fully-enumerable pattern, not a
#      keep-vs-rename decision (that judgment stays in the skill)
#
# Usage: migrate.sh [path]
#   path   plugin directory (defaults to the current directory)
# Out:   STATUS outcomes emit one JSON object on stdout; pure tool/
#        precondition errors emit only a stderr diagnostic (the missing-jq
#        guard is the lone exception — it emits a minimal JSON envelope so
#        stdout parsers still see it). Parse stdout only on exit 0 or 1.
#          {"status": "migrated"|"already-migrated"|"not-a-plugin",
#           "migrated": bool,            # ran `tessl plugin migrate` now
#           "plugin_json": bool,         # .tessl-plugin/plugin.json exists
#           "tileignore_renamed": bool,  # .tileignore -> .tesslignore
#           "tile_json_removed": bool,   # legacy tile.json deleted
#           "lint_ok": bool|null,        # `tessl plugin lint` passed (null if not run)
#           "residual_tile_refs": N,     # files matching whole-word tile/tiles, case-insensitive
#           "residual_files": [path,...]}
# Exit:  0 not-a-plugin (nothing to migrate), already-migrated, or migrated
#          with lint passing;
#        1 migrated but `tessl plugin lint` failed (agent must address);
#        2 tool/precondition error — jq/tessl missing, path not a directory,
#          or `tessl plugin migrate` produced no manifest (stderr only)

set -euo pipefail

# Scan tracked-and-untracked text files for the whole word "tile"/"tiles",
# case-insensitive, excluding the VCS dir, installed plugins, dependency
# dirs, and the CHANGELOG (its archive legitimately references the legacy
# term). Uses `grep -w` for word boundaries (POSIX-portable, unlike the
# `\b` GNU extension) and `-i` so capitalized "Tile" is not missed. Emits a
# JSON array of matching file paths (relative, sorted) on stdout. Always 0.
scan_residual_files() {
  local files
  # `|| true`: grep exits 1 on no matches, which would trip `set -o
  # pipefail` and abort the migration after it already succeeded.
  files=$(grep -rIliwE 'tiles?' . \
    --exclude-dir=.git \
    --exclude-dir=.tessl \
    --exclude-dir=node_modules \
    --exclude='CHANGELOG.md' 2>/dev/null || true)
  if [[ -z "$files" ]]; then
    echo '[]'
    return 0
  fi
  printf '%s\n' "$files" | sed 's|^\./||' | sort | jq -R . | jq -s .
}

emit() {
  # $1 status, $2 migrated, $3 plugin_json, $4 tileignore_renamed,
  # $5 tile_json_removed, $6 lint_ok (true|false|null), $7 residual_files JSON
  local residual_count
  residual_count=$(jq 'length' <<<"$7")
  jq -n \
    --arg status "$1" \
    --argjson migrated "$2" \
    --argjson plugin_json "$3" \
    --argjson tileignore_renamed "$4" \
    --argjson tile_json_removed "$5" \
    --argjson lint_ok "$6" \
    --argjson residual_count "$residual_count" \
    --argjson residual_files "$7" \
    '{status: $status, migrated: $migrated, plugin_json: $plugin_json,
      tileignore_renamed: $tileignore_renamed, tile_json_removed: $tile_json_removed,
      lint_ok: $lint_ok, residual_tile_refs: $residual_count, residual_files: $residual_files}'
}

main() {
  local path_arg="${1:-.}"

  if ! command -v jq >/dev/null 2>&1; then
    printf '{"status":"not-a-plugin","migrated":false,"plugin_json":false,"tileignore_renamed":false,"tile_json_removed":false,"lint_ok":null,"residual_tile_refs":0,"residual_files":[]}\n'
    echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
    exit 2
  fi
  if ! command -v tessl >/dev/null 2>&1; then
    echo "error: tessl CLI not found on PATH ('command -v tessl') — install it and re-run" >&2
    exit 2
  fi
  if [[ ! -d "$path_arg" ]]; then
    echo "error: '${path_arg}' is not a directory — pass the plugin directory (defaults to '.')" >&2
    exit 2
  fi
  cd "$path_arg"

  local has_tile=false has_plugin=false
  [[ -f tile.json ]] && has_tile=true
  [[ -f .tessl-plugin/plugin.json ]] && has_plugin=true

  # Already migrated: plugin.json is authoritative. Idempotent no-op so the
  # skill can re-run safely; the agent skips straight to reconciliation.
  if [[ "$has_plugin" == true ]]; then
    emit "already-migrated" false true false false null "$(scan_residual_files)"
    exit 0
  fi

  # Neither manifest: not a tessl plugin directory — nothing to migrate. A
  # clean assessment, not a tool error, so emit the status and exit 0; the
  # only exit-2 cases are genuine tool/precondition failures.
  if [[ "$has_tile" == false ]]; then
    emit "not-a-plugin" false false false false null "[]"
    exit 0
  fi

  # Legacy: tile.json present, no plugin.json. Run the conversion.
  if ! tessl plugin migrate >/dev/null 2>&1; then
    echo "error: 'tessl plugin migrate' failed — run it directly in '${path_arg}' to inspect the failure" >&2
    exit 2
  fi
  if [[ ! -f .tessl-plugin/plugin.json ]]; then
    echo "error: 'tessl plugin migrate' did not produce .tessl-plugin/plugin.json — inspect tile.json and re-run" >&2
    exit 2
  fi

  local tileignore_renamed=false
  if [[ -f .tileignore && ! -e .tesslignore ]]; then
    mv .tileignore .tesslignore
    tileignore_renamed=true
  fi

  # plugin.json is now authoritative, so the legacy manifest is obsolete and
  # would only trip `tessl plugin lint`'s coexistence warning. Tracked file,
  # so `git rm`/`rm` is recoverable.
  local tile_json_removed=false
  if git rm -q tile.json >/dev/null 2>&1 || rm -f tile.json; then
    [[ ! -f tile.json ]] && tile_json_removed=true
  fi

  local lint_ok=true rc=0
  if ! tessl plugin lint >/dev/null 2>&1; then
    lint_ok=false
    rc=1
  fi

  emit "migrated" true true "$tileignore_renamed" "$tile_json_removed" "$lint_ok" "$(scan_residual_files)"

  if [[ $rc -ne 0 ]]; then
    echo "migrate: 'tessl plugin lint' failed after migration — inspect the lint output and fix before publishing" >&2
  fi
  exit "$rc"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
