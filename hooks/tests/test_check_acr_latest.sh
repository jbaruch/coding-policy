#!/usr/bin/env bash
# Outcome-based tests for hooks/check-acr-latest.sh.
#
# Every case points ACR_BIN at a fake acr this harness writes, which records its
# arguments and replays a scripted output and exit code (rules/testing-standards.md
# Fixtures — no live registry).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. No agents.yaml        -> silent, acr never called.
#   2. agents.yaml, update   -> acr runs `freshness run --project <root> --policy install`,
#                               and its output becomes one "acr:" status.
#   3. Throttled / no change -> acr prints nothing, the hook prints nothing.
#   4. acr fails             -> a status carrying its output and the diagnose command.
#   5. acr missing           -> a status naming the install command.
#   6. Herdr worker worktree -> silent, acr never called.
#
# Run: bash hooks/tests/test_check_acr_latest.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# A fake acr: appends its argv to $TMP/calls, prints FAKE_OUT, exits FAKE_RC.
mk_fake_acr() {
  cat > "$TMP/acr" <<'FAKE' || die "cannot write the fake acr"
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_CALLS"
if [[ -n "${FAKE_OUT:-}" ]]; then printf '%s\n' "$FAKE_OUT"; fi
exit "${FAKE_RC:-0}"
FAKE
  chmod +x "$TMP/acr" || die "cannot make the fake acr executable"
}

# run <dir> [env...] -> OUT, RC
run() {
  local dir="$1"; shift
  OUT="$(cd "$dir" && env ACR_BIN="$TMP/acr" FAKE_CALLS="$TMP/calls" "$@" bash "$SCRIPT" </dev/null 2>"$TMP/err")"
  RC=$?
}

context() { python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["additionalContext"])' <<<"$OUT"; }

calls() { if [[ -f "$TMP/calls" ]]; then cat "$TMP/calls"; fi; }

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-acr-latest.sh"
  [[ -f "$SCRIPT" ]] || die "hook not found at $SCRIPT"
  command -v python3 >/dev/null || die "python3 required for these tests"
  command -v git >/dev/null || die "git required for these tests"
  TMP="$(mktemp -d -t acr-latest-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  export HOME="$TMP/home" GIT_CONFIG_NOSYSTEM=1
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  mkdir -p "$HOME" || die "cannot create $HOME"
  mk_fake_acr
  FAIL=0; PASS=0

  local plain="$TMP/plain" project
  mkdir -p "$plain" || die "cannot create $plain"
  git -C "$plain" init -q || die "git init failed in $plain"
  project="$TMP/project"
  mkdir -p "$project" || die "cannot create $project"
  git -C "$project" init -q -b main || die "git init failed in $project"
  printf 'agents: [claude-code]\n' > "$project/agents.yaml" || die "cannot write agents.yaml"
  git -C "$project" add agents.yaml || die "git add failed"
  git -C "$project" commit -q -m init || die "git commit failed"
  project="$(cd "$project" && pwd -P)" || die "cannot resolve $project"

  # 1. no agents.yaml -> silent, no acr call.
  run "$plain"
  if [[ $RC -eq 0 && -z "$OUT" && -z "$(calls)" ]]; then pass; else fail "no agents.yaml: expected silence, got OUT=$OUT calls=$(calls)"; fi

  # 2. an update -> install policy on the project root, output as the status.
  run "$project" FAKE_OUT="Updated github:jbaruch/coding-policy v0.3.274 -> v0.3.275"
  if [[ $RC -eq 0 ]] && [[ "$(calls)" == "freshness run --project ${project} --policy install" ]] \
    && [[ "$(context)" == $'Session-start status — acr:\nUpdated github:jbaruch/coding-policy v0.3.274 -> v0.3.275' ]]; then
    pass; else fail "update: expected an install run and its status, got OUT=$OUT calls=$(calls)"; fi
  rm -f "$TMP/calls" || die "cannot reset calls"

  # 3. throttled or nothing to do -> nothing.
  run "$project"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "no change: expected silence, got OUT=$OUT"; fi
  rm -f "$TMP/calls" || die "cannot reset calls"

  # 4. acr fails -> its output and the command to diagnose it.
  run "$project" FAKE_OUT="network unreachable" FAKE_RC=1
  if [[ $RC -eq 0 ]] && context | grep -q "failed (exit 1)" && context | grep -q "network unreachable" \
    && context | grep -qF "freshness run --project ${project} --policy install\` to diagnose"; then
    pass; else fail "acr failure: expected a diagnosable status, got OUT=$OUT"; fi
  rm -f "$TMP/calls" || die "cannot reset calls"

  # 5. acr missing -> the install command.
  OUT="$(cd "$project" && env ACR_BIN="$TMP/no-such-acr" bash "$SCRIPT" </dev/null 2>"$TMP/err")"; RC=$?
  if [[ $RC -eq 0 ]] && context | grep -q "brew install jbaruch/agentic-context-registry/acr"; then
    pass; else fail "acr missing: expected the install command, got OUT=$OUT"; fi

  # 6. a Herdr worker in a linked worktree -> silent, no acr call.
  git -C "$project" worktree add -q "$TMP/worker" -b feat/worker || die "worktree add failed"
  run "$TMP/worker" HERDR_ENV=1 FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$OUT" && -z "$(calls)" ]]; then pass; else fail "worker: expected silence and no acr call, got OUT=$OUT calls=$(calls)"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} failed, ${PASS} passed"; exit 1; fi
  echo "PASSED: all ${PASS} checks"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
