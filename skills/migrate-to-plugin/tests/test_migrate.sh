#!/usr/bin/env bash
# Outcome-based tests for migrate.sh. Source the script (its main() guard
# prevents auto-run when sourced), override `tessl` with a shell function,
# and call `main <tempdir>` inside a command substitution so the script's
# `exit` terminates only the subshell. Each test builds its own throwaway
# directory — no shared mutable state, order-independent.
#
# Run: bash skills/migrate-to-plugin/tests/test_migrate.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/migrate.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: migrate.sh not executable at $SCRIPT" >&2; exit 2; }

# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

FAIL_COUNT=0
PASS_COUNT=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  [[ "$expected" == "$actual" ]] && return 0
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $name" >&2
  fi
}

# Canonical mock: `tessl plugin migrate` writes a minimal plugin.json into
# cwd; `tessl plugin lint` returns MOCK_LINT_RC (default 0). Tests redefine
# it per-scenario where they need divergent behavior.
tessl() {
  case "$1 ${2:-}" in
    "plugin migrate")
      mkdir -p .tessl-plugin
      printf '{"name":"acme/widget","version":"0.1.0","description":"demo"}\n' > .tessl-plugin/plugin.json
      ;;
    "plugin lint") return "${MOCK_LINT_RC:-0}" ;;
    *) echo "mock tessl: unsupported invocation: $*" >&2; return 2 ;;
  esac
}

# Build a throwaway git repo with a committed legacy layout: tile.json, a
# .tileignore, and a prose file carrying a residual "tile" reference.
make_legacy_repo() {
  local d; d=$(mktemp -d)
  ( cd "$d" || { echo "fatal: cd into temp repo $d failed" >&2; exit 1; }
    git init -q
    git config user.email t@t.t; git config user.name t
    printf '{"name":"acme/widget","version":"0.1.0"}\n' > tile.json
    printf 'dist/\n' > .tileignore
    mkdir -p rules
    # Capital "Tile" on purpose: locks the case-insensitive residual scan
    # (a case-sensitive grep would miss it and the happy-path assertion below
    # would fail).
    printf 'Install a Tile to get started.\n' > rules/intro.md
    git add -A; git commit -qm init )
  echo "$d"
}

# --- tests ---

t_already_migrated_is_noop() {
  local d; d=$(mktemp -d); mkdir -p "$d/.tessl-plugin"
  printf '{"name":"a/b","version":"1.0.0","description":"x"}\n' > "$d/.tessl-plugin/plugin.json"
  local out rc=0
  out=$(main "$d") || rc=$?
  rm -rf "$d"
  assert_eq "exit" "0" "$rc" || return 1
  assert_eq "status" "already-migrated" "$(jq -r .status <<<"$out")" || return 1
  assert_eq "migrated" "false" "$(jq -r .migrated <<<"$out")" || return 1
}

t_not_a_plugin_is_noop() {
  local d; d=$(mktemp -d)
  local out rc=0
  out=$(main "$d") || rc=$?
  rm -rf "$d"
  # Not a plugin dir is a clean assessment (nothing to migrate), not a tool
  # error — exit 0 with the status on stdout so the skill can branch on it.
  assert_eq "exit" "0" "$rc" || return 1
  assert_eq "status" "not-a-plugin" "$(jq -r .status <<<"$out")"
}

t_legacy_happy_path() {
  local d; d=$(make_legacy_repo)
  local out rc=0
  out=$(MOCK_LINT_RC=0 main "$d") || rc=$?
  assert_eq "exit" "0" "$rc" || { rm -rf "$d"; return 1; }
  assert_eq "status"             "migrated" "$(jq -r .status <<<"$out")"            || { rm -rf "$d"; return 1; }
  assert_eq "migrated"           "true"     "$(jq -r .migrated <<<"$out")"          || { rm -rf "$d"; return 1; }
  assert_eq "plugin_json"        "true"     "$(jq -r .plugin_json <<<"$out")"       || { rm -rf "$d"; return 1; }
  assert_eq "tileignore_renamed" "true"     "$(jq -r .tileignore_renamed <<<"$out")" || { rm -rf "$d"; return 1; }
  assert_eq "tile_json_removed"  "true"     "$(jq -r .tile_json_removed <<<"$out")"  || { rm -rf "$d"; return 1; }
  assert_eq "lint_ok"            "true"     "$(jq -r .lint_ok <<<"$out")"           || { rm -rf "$d"; return 1; }
  # Side effects on disk.
  [[ -f "$d/.tessl-plugin/plugin.json" ]] || { echo "    FAIL: plugin.json not created" >&2; rm -rf "$d"; return 1; }
  [[ ! -f "$d/tile.json" ]] || { echo "    FAIL: tile.json not removed" >&2; rm -rf "$d"; return 1; }
  [[ -f "$d/.tesslignore" && ! -f "$d/.tileignore" ]] || { echo "    FAIL: .tileignore not renamed" >&2; rm -rf "$d"; return 1; }
  # Residual scan flags the prose file for the agent's reconcile step.
  [[ "$(jq -r '.residual_files | index("rules/intro.md")' <<<"$out")" != "null" ]] \
    || { echo "    FAIL: residual_files missing rules/intro.md: $(jq -c .residual_files <<<"$out")" >&2; rm -rf "$d"; return 1; }
  rm -rf "$d"
}

t_legacy_lint_failure_exits_one() {
  local d; d=$(make_legacy_repo)
  local out rc=0
  out=$(MOCK_LINT_RC=1 main "$d" 2>/dev/null) || rc=$?
  rm -rf "$d"
  assert_eq "exit" "1" "$rc" || return 1
  assert_eq "migrated" "true"  "$(jq -r .migrated <<<"$out")" || return 1
  assert_eq "lint_ok"  "false" "$(jq -r .lint_ok <<<"$out")"
}

t_migrate_without_manifest_exits_two() {
  local d; d=$(make_legacy_repo)
  # Override: migrate "succeeds" (rc 0) but writes no manifest.
  tessl() { case "$1 ${2:-}" in "plugin migrate") return 0 ;; "plugin lint") return 0 ;; *) return 2 ;; esac; }
  local rc=0
  ( main "$d" >/dev/null 2>&1 ) || rc=$?
  rm -rf "$d"
  # Restore canonical mock for subsequent tests.
  tessl() {
    case "$1 ${2:-}" in
      "plugin migrate") mkdir -p .tessl-plugin; printf '{"name":"acme/widget","version":"0.1.0","description":"demo"}\n' > .tessl-plugin/plugin.json ;;
      "plugin lint") return "${MOCK_LINT_RC:-0}" ;;
      *) echo "mock tessl: unsupported invocation: $*" >&2; return 2 ;;
    esac
  }
  assert_eq "exit" "2" "$rc"
}

t_existing_tesslignore_blocks_rename() {
  local d; d=$(make_legacy_repo)
  printf 'keep\n' > "$d/.tesslignore"   # pre-existing target
  local out rc=0
  out=$(main "$d") || rc=$?
  rm -rf "$d"
  assert_eq "exit" "0" "$rc" || return 1
  assert_eq "tileignore_renamed" "false" "$(jq -r .tileignore_renamed <<<"$out")"
}

t_output_is_valid_json_with_documented_shape() {
  local d; d=$(make_legacy_repo)
  local out rc=0 keys
  out=$(main "$d") || rc=$?
  rm -rf "$d"
  echo "$out" | jq -e . >/dev/null || { echo "    FAIL: stdout not valid JSON: $out" >&2; return 1; }
  keys=$(echo "$out" | jq -r 'keys | sort | join(",")')
  assert_eq "keys" "lint_ok,migrated,plugin_json,residual_files,residual_tile_refs,status,tile_json_removed,tileignore_renamed" "$keys"
}

# --- driver ---

echo "== migrate.sh tests =="
run "already-migrated is a no-op (exit 0)"                t_already_migrated_is_noop
run "not-a-plugin is a no-op (exit 0)"                    t_not_a_plugin_is_noop
run "legacy happy path migrates + sanitizes + scans"      t_legacy_happy_path
run "lint failure after migrate exits 1"                  t_legacy_lint_failure_exits_one
run "migrate without manifest exits 2"                    t_migrate_without_manifest_exits_two
run "pre-existing .tesslignore blocks rename"             t_existing_tesslignore_blocks_rename
run "output is valid JSON with documented shape"          t_output_is_valid_json_with_documented_shape

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
