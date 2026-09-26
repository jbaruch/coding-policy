#!/usr/bin/env bash
# Outcome-based tests for hooks/check-acr-latest.sh.
#
# Every case points ACR_BIN at a fake acr this harness writes, which records its
# arguments and replays a scripted output and exit code, and every project
# clones a local bare origin (rules/testing-standards.md Fixtures — no live
# registry, no network).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. No agents.yaml                 -> silent, acr never called.
#   2. Synced, clean                  -> `freshness run --project <root> --policy install`,
#                                        output as one "acr:" status.
#   3. Throttled / no change          -> silent.
#   4. acr fails, path with a space   -> a status with a copyable, quoted command.
#   5. acr missing                    -> a status naming the install command.
#   6. Any Herdr session              -> silent, acr never called.
#   7. Behind origin                  -> a "not updated" status, acr never called.
#   8. Uncommitted changes            -> a "not updated" status, acr never called.
#   9. Fetch fails (origin gone)      -> a "not updated" status, acr never called.
#  10. Outside git, agents.yaml       -> installs (no checkout to sync).
#  11. acr older than ACR_MIN_VERSION -> an upgrade status, acr never run.
#
# Run: bash hooks/tests/test_check_acr_latest.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# A fake acr: appends its argv to $FAKE_CALLS, prints FAKE_OUT, exits FAKE_RC.
mk_fake_acr() {
  cat > "$TMP/acr" <<'FAKE' || die "cannot write the fake acr"
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == --version ]]; then printf '%s (abc123)\n' "${FAKE_VERSION:-0.2.0}"; exit 0; fi
printf '%s\n' "$*" >> "$FAKE_CALLS"
if [[ -n "${FAKE_OUT:-}" ]]; then printf '%s\n' "$FAKE_OUT"; fi
exit "${FAKE_RC:-0}"
FAKE
  chmod +x "$TMP/acr" || die "cannot make the fake acr executable"
}

# A project cloned from a fresh bare origin, carrying a committed agents.yaml.
# Sets PROJECT (resolved path) and ORIGIN.
mk_project() { # <name>
  ORIGIN="$TMP/$1.git"
  local seed="$TMP/$1-seed"
  git init -q --bare -b main "$ORIGIN" || die "git init --bare failed for $ORIGIN"
  git clone -q "$ORIGIN" "$seed" 2>"$TMP/clone.err" || die "clone failed: $(cat "$TMP/clone.err")"
  git -C "$seed" symbolic-ref HEAD refs/heads/main || die "symbolic-ref failed in $seed"
  printf 'agents: [claude-code]\n' > "$seed/agents.yaml" || die "cannot write agents.yaml"
  git -C "$seed" add agents.yaml || die "git add failed"
  git -C "$seed" commit -q -m init || die "git commit failed"
  git -C "$seed" push -q origin main || die "git push failed"
  git clone -q "$ORIGIN" "$TMP/$1" 2>"$TMP/clone.err" || die "clone failed: $(cat "$TMP/clone.err")"
  PROJECT="$(cd "$TMP/$1" && pwd -P)" || die "cannot resolve $TMP/$1"
  SEED="$seed"
}

# run <dir> [env...] -> OUT, RC
run() {
  local dir="$1"; shift
  rm -f "$TMP/calls" || die "cannot reset calls"
  OUT="$(cd "$dir" && env -u HERDR_ENV ACR_BIN="$TMP/acr" FAKE_CALLS="$TMP/calls" "$@" bash "$SCRIPT" </dev/null 2>"$TMP/err")"
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

  local plain="$TMP/plain"
  mkdir -p "$plain" || die "cannot create $plain"
  git -C "$plain" init -q || die "git init failed in $plain"

  # 1. no agents.yaml -> silent, no acr call.
  run "$plain"
  if [[ $RC -eq 0 && -z "$OUT" && -z "$(calls)" ]]; then pass; else fail "no agents.yaml: expected silence, got OUT=$OUT calls=$(calls)"; fi

  # 2. synced and clean -> install policy on the project root, output as the status.
  mk_project p2
  run "$PROJECT" FAKE_OUT="Updated github:jbaruch/coding-policy v0.3.274 -> v0.3.275"
  if [[ $RC -eq 0 ]] && [[ "$(calls)" == "freshness run --project ${PROJECT} --policy install" ]] \
    && [[ "$(context)" == $'Session-start status — acr:\nUpdated github:jbaruch/coding-policy v0.3.274 -> v0.3.275' ]]; then
    pass; else fail "synced: expected an install run and its status, got OUT=$OUT calls=$(calls) err=$(cat "$TMP/err")"; fi

  # 3. throttled or nothing to do -> nothing.
  mk_project p3
  run "$PROJECT"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "no change: expected silence, got OUT=$OUT"; fi

  # 4. acr fails in a path holding a space -> the diagnose command stays copyable.
  mk_project "p 4"
  run "$PROJECT" FAKE_OUT="network unreachable" FAKE_RC=1
  local quoted
  quoted="$(printf '%q' "$PROJECT")"
  if [[ $RC -eq 0 ]] && context | grep -q "failed (exit 1)" && context | grep -q "network unreachable" \
    && context | grep -qF -- "--project ${quoted} --policy install\` to diagnose"; then
    pass; else fail "acr failure: expected a quoted diagnose command, got OUT=$OUT"; fi

  # 5. acr missing -> the install command.
  mk_project p5
  OUT="$(cd "$PROJECT" && env -u HERDR_ENV ACR_BIN="$TMP/no-such-acr" bash "$SCRIPT" </dev/null 2>"$TMP/err")"; RC=$?
  if [[ $RC -eq 0 ]] && context | grep -q "brew install jbaruch/agentic-context-registry/acr"; then
    pass; else fail "acr missing: expected the install command, got OUT=$OUT"; fi

  # 6. any Herdr session, the foreman's own checkout included -> silent, no acr call.
  mk_project p6
  run "$PROJECT" HERDR_ENV=1 FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$OUT" && -z "$(calls)" ]]; then pass; else fail "herdr: expected silence and no acr call, got OUT=$OUT calls=$(calls)"; fi

  # 7. behind origin -> not updated, acr never called.
  mk_project p7
  printf 'more\n' >> "$SEED/agents.yaml" || die "cannot edit seed"
  git -C "$SEED" commit -q -am next || die "seed commit failed"
  git -C "$SEED" push -q origin main || die "seed push failed"
  run "$PROJECT" FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$(calls)" ]] && context | grep -q "not updated" && context | grep -q "does not contain \`origin/main\`"; then
    pass; else fail "behind: expected a skip naming origin/main, got OUT=$OUT calls=$(calls)"; fi

  # 8. uncommitted changes -> not updated, acr never called.
  mk_project p8
  printf 'scratch\n' > "$PROJECT/notes.txt" || die "cannot write notes.txt"
  run "$PROJECT" FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$(calls)" ]] && context | grep -q "uncommitted changes"; then
    pass; else fail "dirty: expected a skip naming uncommitted changes, got OUT=$OUT calls=$(calls)"; fi

  # 9. fetch fails -> not updated, acr never called.
  mk_project p9
  rm -rf "$ORIGIN" || die "cannot remove $ORIGIN"
  run "$PROJECT" FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$(calls)" ]] && context | grep -q "fetching origin failed"; then
    pass; else fail "fetch failure: expected a skip, got OUT=$OUT calls=$(calls)"; fi

  # 10. outside git with agents.yaml -> installs; there is no checkout to sync.
  local nogit="$TMP/nogit"
  mkdir -p "$nogit" || die "cannot create $nogit"
  printf 'agents: [claude-code]\n' > "$nogit/agents.yaml" || die "cannot write agents.yaml"
  nogit="$(cd "$nogit" && pwd -P)" || die "cannot resolve $nogit"
  run "$nogit" GIT_CEILING_DIRECTORIES="$TMP" FAKE_OUT="Updated x"
  if [[ $RC -eq 0 ]] && [[ "$(calls)" == "freshness run --project ${nogit} --policy install" ]]; then
    pass; else fail "outside git: expected an install run, got OUT=$OUT calls=$(calls) err=$(cat "$TMP/err")"; fi

  # 11. acr older than the hook's floor -> an upgrade status, never run.
  mk_project p11
  run "$PROJECT" FAKE_VERSION=0.1.9 FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$(calls)" ]] && context | grep -q "acr 0.1.9 is older than" && context | grep -q "brew upgrade"; then
    pass; else fail "old acr: expected an upgrade status and no run, got OUT=$OUT calls=$(calls)"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} failed, ${PASS} passed"; exit 1; fi
  echo "PASSED: all ${PASS} checks"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
