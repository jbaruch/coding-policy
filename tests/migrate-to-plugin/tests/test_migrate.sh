#!/usr/bin/env bash
# Outcome-based tests for migrate.sh. Source the script (its main() guard
# prevents auto-run when sourced), override `tessl` with a shell function,
# and call `main <tempdir>` inside a command substitution so the script's
# `exit` terminates only the subshell. Each test builds its own throwaway
# directory — no shared mutable state, order-independent.
#
# Run: bash tests/migrate-to-plugin/tests/test_migrate.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/../../../skills/migrate-to-plugin" && pwd)/migrate.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: migrate.sh not executable at $SCRIPT" >&2; exit 2; }

# shellcheck disable=SC1090  # ShellCheck cannot resolve the dynamically constructed source path.
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
# cwd; `tessl plugin lint` records its call, emits fixture diagnostics on
# both streams by default, and returns MOCK_LINT_RC (default 0).
# Stdout supports printf escapes for byte fixtures.
tessl() {
  case "$1 ${2:-}" in
    "plugin migrate")
      mkdir -p .tessl-plugin
      printf '{"name":"acme/widget","version":"0.1.0","description":"demo"}\n' > .tessl-plugin/plugin.json
      ;;
    "plugin lint")
      printf 'lint\n' >> .git/lint-calls || return 2
      printf '%b' "${MOCK_LINT_STDOUT-$'lint stdout diagnostic\n'}"
      printf '%s' "${MOCK_LINT_STDERR-$'lint stderr diagnostic\n'}" >&2
      return "${MOCK_LINT_RC:-0}"
      ;;
    *) echo "mock tessl: unsupported invocation: $*" >&2; return 2 ;;
  esac
}

# Build a throwaway git repo with a committed legacy layout: tile.json, a
# .tileignore, and a prose file carrying a residual "tile" reference.
make_legacy_repo() {
  local d; d=$(mktemp -d)
  # Build inside a subshell so the cd is scoped. Propagate any failure to
  # the caller — without this the function would echo "$d" even on a failed
  # build, handing back an uninitialized path the test would then misread.
  if ! (
    cd "$d" || exit 1
    git init -q
    git config user.email t@t.t; git config user.name t
    printf '{"name":"acme/widget","version":"0.1.0"}\n' > tile.json
    printf 'dist/\n' > .tileignore
    mkdir -p rules
    # Capital "Tile" on purpose: locks the case-insensitive residual scan
    # (a case-sensitive grep would miss it and the happy-path assertion below
    # would fail).
    printf 'Install a Tile to get started.\n' > rules/intro.md
    git add -A; git commit -qm init
  ); then
    echo "fatal: failed to build legacy fixture repo under $d" >&2
    rm -rf "$d"
    return 1
  fi
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
  out=$(MOCK_LINT_RC=0 main "$d" 2>"$d/.git/migrate.stderr") || rc=$?
  assert_eq "exit" "0" "$rc" || { rm -rf "$d"; return 1; }
  assert_eq "stderr" "" "$(<"$d/.git/migrate.stderr")" || { rm -rf "$d"; return 1; }
  assert_eq "lint invocations" "lint" "$(<"$d/.git/lint-calls")" || { rm -rf "$d"; return 1; }
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

# Tessl 0.122.0 emits the header, blank line, original source, and caret.
# Include a header-shaped line to catch accidental parsing beyond the first.
yaml_lint_excerpt=$(printf '%s\n' \
  "✘ Skill 'audit-demo': Failed to parse YAML frontmatter: Flow sequence in block collection must be sufficiently indented and end with a ] at line 3, column 38:" \
  '' 'audit_value: [INERT_PRIVATE_METADATA,' '                                     ^' '' \
  "✘ Skill 'lookalike': SKILL.md frontmatter field \"description\" must be at most 1024 characters.")

t_legacy_lint_failure_exits_one() {
  local d; d=$(make_legacy_repo) || return 1
  local expected="$1" out err calls rc=0 persisted=0
  out=$(main "$d" 2>"$d/.git/migrate.stderr") || rc=$?
  err=$(<"$d/.git/migrate.stderr")
  calls=$(<"$d/.git/lint-calls")
  grep -raFq INERT_PRIVATE_METADATA "$d" || persisted=$?
  rm -rf "$d"
  assert_eq "exit" "1" "$rc" || return 1
  assert_eq "lint invocations" "lint" "$calls" || return 1
  assert_eq "stderr" "$expected" "$err" || return 1
  assert_eq "no persisted source marker" "1" "$persisted" || return 1
  jq -es '. == [{status: "migrated", migrated: true, plugin_json: true,
    tileignore_renamed: true, tile_json_removed: true, lint_ok: false,
    residual_tile_refs: 1, residual_files: ["rules/intro.md"]}]' <<<"$out" >/dev/null \
    || { echo "    FAIL: expected one failed migration report on stdout: $out" >&2; return 1; }
}

t_lint_and_scan_failure_exits_two() {
  local d; d=$(make_legacy_repo) || return 1
  local err calls rc=0 persisted=0
  (
    grep() { return 2; }
    MOCK_LINT_RC=1 MOCK_LINT_STDOUT="$yaml_lint_excerpt"'\000' main "$d"
  ) >"$d/.git/migrate.stdout" 2>"$d/.git/migrate.stderr" || rc=$?
  if [[ -s "$d/.git/migrate.stdout" ]]; then
    echo "    FAIL: expected empty stdout: $(<"$d/.git/migrate.stdout")" >&2
    rm -rf "$d"
    return 1
  fi
  err=$(<"$d/.git/migrate.stderr")
  calls=$(<"$d/.git/lint-calls")
  grep -raFq INERT_PRIVATE_METADATA "$d" || persisted=$?
  rm -rf "$d"
  assert_eq "exit" "2" "$rc" || return 1
  assert_eq "lint invocations" "lint" "$calls" || return 1
  assert_eq "no persisted source marker" "1" "$persisted" || return 1
  assert_eq "stderr" "migrate.sh: residual-file scan failed (grep rc=2) — the tree could not be fully read, so a 'no residual files' result cannot be trusted; check directory permissions, then re-run" "$err"
}

t_migrate_without_manifest_exits_two() {
  local d; d=$(make_legacy_repo)
  local rc=0
  (
    # Override: migrate "succeeds" (rc 0) but writes no manifest.
    tessl() { case "$1 ${2:-}" in "plugin migrate") return 0 ;; "plugin lint") return 0 ;; *) return 2 ;; esac; }
    main "$d"
  ) >/dev/null 2>&1 || rc=$?
  rm -rf "$d"
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
MOCK_LINT_RC=1 MOCK_LINT_STDERR="$yaml_lint_excerpt" \
  run "YAML failure reports only the skill and location" t_legacy_lint_failure_exits_one \
    "migrate: Fix YAML frontmatter in the SKILL.md for skill audit-demo (frontmatter line 3, column 38)."
MOCK_LINT_RC=1 MOCK_LINT_STDOUT=$'startup notice\n\n✘ Skill \'audit-demo\': SKILL.md frontmatter field "description" must be at most 1024 characters.\n' \
  run "description failure reports a fixed repair" t_legacy_lint_failure_exits_one \
    "migrate: Shorten the description in the SKILL.md frontmatter for skill audit-demo to 1024 characters or fewer."
MOCK_LINT_RC=1 MOCK_LINT_STDOUT='✘ Unrecognized diagnostic with INERT_PRIVATE_METADATA' \
  run "unknown lint output reports the status and repair targets" t_legacy_lint_failure_exits_one \
    "migrate: 'tessl plugin lint' exited 1 with no recognized diagnostic. Inspect .tessl-plugin/plugin.json and the frontmatter in its listed SKILL.md files. Run 'tessl --version' to check the CLI."
MOCK_LINT_RC=70 MOCK_LINT_STDOUT='' MOCK_LINT_STDERR='' \
  run "silent lint failure reports the status and repair targets" t_legacy_lint_failure_exits_one \
    "migrate: 'tessl plugin lint' exited 70 with no recognized diagnostic. Inspect .tessl-plugin/plugin.json and the frontmatter in its listed SKILL.md files. Run 'tessl --version' to check the CLI."
run "NUL in lint output with scan failure reports only the scan error" t_lint_and_scan_failure_exits_two
run "migrate without manifest exits 2"                    t_migrate_without_manifest_exits_two
run "pre-existing .tesslignore blocks rename"             t_existing_tesslignore_blocks_rename
run "output is valid JSON with documented shape"          t_output_is_valid_json_with_documented_shape

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
