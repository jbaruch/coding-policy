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
#  12. Committed registry lock        -> refused, untrack guidance.
#  13. Pinned jbaruch dependency      -> carve-out NOTE, update still runs.
#  14. Portable mode (under tessl)    -> report, never run.
#  15. Fetch fails with a secret URL  -> exit code only, no URL in the status.
#  16. Unreadable acr version         -> reinstall guidance.
#  6b. Herdr session, pinned dep      -> the carve-out NOTE, no update.
#  17. jq only, no timeout utility    -> check and update run (bounded-fetch fallback).
#  18. Neither python3 nor jq         -> no update, warning on stderr.
#  19. origin's default renamed       -> checked against origin's live HEAD.
#  20. Lock not gitignored            -> refused, .gitignore guidance.
#  21. Zero fetch timeout             -> default bound, update runs.
#  22. python3 present but failing    -> jq runs the pin check.
#  23. Fetched ref behind origin's tip -> not updated.
#  24. reference-transaction hook     -> never runs during the safety fetch.
#  25. Secret in ACR's output         -> masked in the status.
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
if [[ "${1:-}" == list ]]; then
  default='{"ok":true,"result":{"dependencies":[{"declaration":{"source":"github:jbaruch/coding-policy","requested":"latest"}}]}}'
  printf '%s\n' "${FAKE_LIST:-$default}"
  exit 0
fi
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
  printf '.agents/\n' > "$seed/.gitignore" || die "cannot write .gitignore"
  git -C "$seed" add agents.yaml .gitignore || die "git add failed"
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
  read_calls
}

context() { python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["additionalContext"])' <<<"$OUT"; }

# Read the fake acr's call log into CALLS; an unreadable log stops the harness
# rather than reading as "no calls".
read_calls() {
  CALLS=""
  if [[ -e "$TMP/calls" ]]; then
    CALLS="$(cat "$TMP/calls")" || die "cannot read $TMP/calls"
  fi
}

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
  if [[ $RC -eq 0 && -z "$OUT" && -z "$CALLS" ]]; then pass; else fail "no agents.yaml: expected silence, got OUT=$OUT calls=$CALLS"; fi

  # 2. synced and clean -> install policy on the project root, output as the status.
  mk_project p2
  run "$PROJECT" FAKE_OUT="Updated github:jbaruch/coding-policy v0.3.274 -> v0.3.275"
  if [[ $RC -eq 0 ]] && [[ "$CALLS" == "freshness run --project ${PROJECT} --policy install" ]] \
    && [[ "$(context)" == $'Session-start status — acr:\nUpdated github:jbaruch/coding-policy v0.3.274 -> v0.3.275' ]]; then
    pass; else fail "synced: expected an install run and its status, got OUT=$OUT calls=$CALLS err=$(cat "$TMP/err")"; fi

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
  OUT="$(cd "$PROJECT" && env -u HERDR_ENV ACR_BIN="$TMP/no-such-acr" bash "$SCRIPT" </dev/null 2>"$TMP/err")"; RC=$?; read_calls
  if [[ $RC -eq 0 ]] && context | grep -q "brew install jbaruch/agentic-context-registry/acr"; then
    pass; else fail "acr missing: expected the install command, got OUT=$OUT"; fi

  # 6. any Herdr session, the foreman's own checkout included -> no update, and
  #    silent when nothing is pinned.
  mk_project p6
  run "$PROJECT" HERDR_ENV=1 FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$OUT" && -z "$CALLS" ]]; then pass; else fail "herdr: expected silence and no acr call, got OUT=$OUT calls=$CALLS"; fi

  # 6b. a Herdr session still runs the read-only carve-out check.
  mk_project p6b
  run "$PROJECT" HERDR_ENV=1 FAKE_OUT="Updated something" FAKE_LIST='{"ok":true,"result":{"dependencies":[{"declaration":{"source":"github:jbaruch/x","requested":"v1"}}]}}'
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "github:jbaruch/x@v1"; then
    pass; else fail "herdr pin check: expected the NOTE and no update, got OUT=$OUT calls=$CALLS"; fi

  # 7. behind origin -> not updated, acr never called.
  mk_project p7
  printf 'more\n' >> "$SEED/agents.yaml" || die "cannot edit seed"
  git -C "$SEED" commit -q -am next || die "seed commit failed"
  git -C "$SEED" push -q origin main || die "seed push failed"
  run "$PROJECT" FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "not updated" && context | grep -q "does not contain \`origin/main\`"; then
    pass; else fail "behind: expected a skip naming origin/main, got OUT=$OUT calls=$CALLS"; fi

  # 8. uncommitted changes -> not updated, acr never called.
  mk_project p8
  printf 'scratch\n' > "$PROJECT/notes.txt" || die "cannot write notes.txt"
  run "$PROJECT" FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "uncommitted changes"; then
    pass; else fail "dirty: expected a skip naming uncommitted changes, got OUT=$OUT calls=$CALLS"; fi

  # 9. fetch fails -> not updated, acr never called.
  mk_project p9
  rm -rf "$ORIGIN" || die "cannot remove $ORIGIN"
  run "$PROJECT" FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "fetching origin failed"; then
    pass; else fail "fetch failure: expected a skip, got OUT=$OUT calls=$CALLS"; fi

  # 10. outside git with agents.yaml -> installs; there is no checkout to sync.
  local nogit="$TMP/nogit"
  mkdir -p "$nogit" || die "cannot create $nogit"
  printf 'agents: [claude-code]\n' > "$nogit/agents.yaml" || die "cannot write agents.yaml"
  nogit="$(cd "$nogit" && pwd -P)" || die "cannot resolve $nogit"
  run "$nogit" GIT_CEILING_DIRECTORIES="$TMP" FAKE_OUT="Updated x"
  if [[ $RC -eq 0 ]] && [[ "$CALLS" == "freshness run --project ${nogit} --policy install" ]]; then
    pass; else fail "outside git: expected an install run, got OUT=$OUT calls=$CALLS err=$(cat "$TMP/err")"; fi

  # 11. acr older than the hook's floor -> an upgrade status, never run.
  mk_project p11
  run "$PROJECT" FAKE_VERSION=0.1.9 FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "acr 0.1.9 is older than" && context | grep -q "brew upgrade"; then
    pass; else fail "old acr: expected an upgrade status and no run, got OUT=$OUT calls=$CALLS"; fi

  # 12. a committed registry lock -> not updated, untrack guidance.
  mk_project p12
  mkdir -p "$PROJECT/.agents" || die "cannot create .agents"
  printf 'lock\n' > "$PROJECT/.agents/registry.lock" || die "cannot write lock"
  git -C "$PROJECT" add -f .agents/registry.lock || die "git add lock failed"
  git -C "$PROJECT" commit -q -m lock || die "commit lock failed"
  git -C "$PROJECT" push -q origin main || die "push lock failed"
  run "$PROJECT" FAKE_OUT="Updated something"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "registry.lock\` is committed" && context | grep -q "git rm --cached"; then
    pass; else fail "tracked lock: expected a refusal with untrack guidance, got OUT=$OUT calls=$CALLS"; fi

  # 13. a pinned jbaruch dependency -> the carve-out NOTE, and the update still runs.
  mk_project p13
  run "$PROJECT" FAKE_OUT="Updated x" FAKE_LIST='{"ok":true,"result":{"dependencies":[{"declaration":{"source":"github:jbaruch/coding-policy","requested":"v0.3.1"}},{"declaration":{"source":"github:other/pkg","requested":"v1"}}]}}'
  if [[ $RC -eq 0 ]] && [[ "$CALLS" == "freshness run --project ${PROJECT} --policy install" ]] \
    && context | grep -q "github:jbaruch/coding-policy@v0.3.1" && ! context | grep -q "other/pkg"; then
    pass; else fail "pinned dep: expected a NOTE naming only the jbaruch pin, got OUT=$OUT calls=$CALLS"; fi

  # 14. portable mode (under tessl) -> report, never run.
  mk_project p14
  run "$PROJECT" SESSION_START_MODE=portable FAKE_OUT="Updated x"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "through tessl"; then
    pass; else fail "portable: expected a report and no run, got OUT=$OUT calls=$CALLS"; fi

  # 15. a failed fetch reports its exit code, never git's message (it can carry a URL).
  mk_project p15
  git -C "$PROJECT" remote set-url origin "https://user:s3cr3t-token@example.invalid/repo.git" || die "set-url failed"
  run "$PROJECT" GIT_TERMINAL_PROMPT=0 FAKE_OUT="Updated x"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "fetching origin failed (exit" && ! context | grep -q "s3cr3t"; then
    pass; else fail "fetch redaction: expected a URL-free failure, got OUT=$OUT"; fi

  # 16. an unreadable version -> reinstall guidance, not "upgrade".
  mk_project p16
  run "$PROJECT" FAKE_VERSION=dev FAKE_OUT="Updated x"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "not a release version" && ! context | grep -q "brew upgrade"; then
    pass; else fail "bad version: expected reinstall guidance, got OUT=$OUT"; fi

  # 17. jq only (no python3): the carve-out check still runs, and the update.
  mk_project p17
  local tools="$TMP/jq-tools" t real
  mkdir -p "$tools" || die "cannot create $tools"
  for t in bash git jq env mktemp cat rm; do
    real="$(command -v "$t")" || die "$t required for these tests"
    ln -sf "$real" "$tools/$t" || die "cannot link $t"
  done
  OUT="$(cd "$PROJECT" && env -u HERDR_ENV PATH="$tools" ACR_BIN="$TMP/acr" FAKE_CALLS="$TMP/calls" FAKE_OUT="Updated x" \
    FAKE_LIST='{"ok":true,"result":{"dependencies":[{"declaration":{"source":"github:jbaruch/y","requested":"v2"}}]}}' \
    "$tools/bash" "$SCRIPT" </dev/null 2>"$TMP/err")"; RC=$?; read_calls
  if [[ $RC -eq 0 ]] && [[ "$CALLS" == "freshness run --project ${PROJECT} --policy install" ]] \
    && jq -r .additionalContext <<<"$OUT" | grep -q "github:jbaruch/y@v2"; then
    pass; else fail "jq only: expected the check and the update (and the no-timeout fetch fallback), got OUT=$OUT calls=$CALLS err=$(cat "$TMP/err")"; fi
  rm -f "$TMP/calls" || die "cannot reset calls"

  # 18. neither python3 nor jq: the check cannot run, so no update.
  mk_project p18
  local bare="$TMP/bare-tools"
  mkdir -p "$bare" || die "cannot create $bare"
  for t in bash git env mktemp cat rm; do
    real="$(command -v "$t")" || die "$t required for these tests"
    ln -sf "$real" "$bare/$t" || die "cannot link $t"
  done
  OUT="$(cd "$PROJECT" && env -u HERDR_ENV PATH="$bare" ACR_BIN="$TMP/acr" FAKE_CALLS="$TMP/calls" FAKE_OUT="Updated x" \
    "$bare/bash" "$SCRIPT" </dev/null 2>"$TMP/err")"; RC=$?; read_calls
  if [[ $RC -eq 0 && -z "$CALLS" ]] && grep -q "neither python3 nor jq" "$TMP/err"; then
    pass; else fail "no JSON tool: expected no update and a warning, got OUT=$OUT calls=$CALLS err=$(cat "$TMP/err")"; fi

  # 19. origin renamed its default branch after the clone: the check follows
  #     origin's live HEAD, not the stale local origin/HEAD.
  mk_project p19
  git -C "$SEED" checkout -q -b develop || die "seed branch failed"
  printf 'develop\n' >> "$SEED/agents.yaml" || die "seed edit failed"
  git -C "$SEED" commit -q -am develop || die "seed commit failed"
  git -C "$SEED" push -q origin develop || die "seed push failed"
  git -C "$ORIGIN" symbolic-ref HEAD refs/heads/develop || die "origin HEAD switch failed"
  run "$PROJECT" FAKE_OUT="Updated x"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "does not contain \`origin/develop\`"; then
    pass; else fail "renamed default: expected a skip naming origin/develop, got OUT=$OUT calls=$CALLS"; fi

  # 20. no ignore rule for the lock -> not updated, .gitignore guidance.
  mk_project p20
  git -C "$PROJECT" rm -q --cached .gitignore || die "untrack .gitignore failed"
  rm "$PROJECT/.gitignore" || die "rm .gitignore failed"
  git -C "$PROJECT" commit -q -m "drop ignore" || die "commit failed"
  git -C "$PROJECT" push -q origin main || die "push failed"
  run "$PROJECT" FAKE_OUT="Updated x"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "is not gitignored" && context | grep -q "Add \`.agents/\` to \`.gitignore\`"; then
    pass; else fail "unignored lock: expected a refusal with .gitignore guidance, got OUT=$OUT calls=$CALLS"; fi

  # 21. a zero fetch timeout never switches the bound off (the default applies).
  mk_project p21
  run "$PROJECT" ACR_LATEST_FETCH_TIMEOUT=0 FAKE_OUT="Updated x"
  if [[ $RC -eq 0 ]] && [[ "$CALLS" == "freshness run --project ${PROJECT} --policy install" ]]; then
    pass; else fail "zero timeout: expected the default bound and an update, got OUT=$OUT err=$(cat "$TMP/err")"; fi

  # 22. python3 present but failing: the pin check falls back to jq.
  mk_project p22
  local broken="$TMP/broken-py"
  mkdir -p "$broken" || die "cannot create $broken"
  printf '#!/usr/bin/env bash\nexit 2\n' > "$broken/python3" || die "cannot write the broken python3"
  chmod +x "$broken/python3" || die "cannot make the broken python3 executable"
  run "$PROJECT" PATH="$broken:$PATH" FAKE_OUT="Updated x" \
    FAKE_LIST='{"ok":true,"result":{"dependencies":[{"declaration":{"source":"github:jbaruch/z","requested":"v3"}}]}}'
  if [[ $RC -eq 0 ]] && [[ "$CALLS" == "freshness run --project ${PROJECT} --policy install" ]] \
    && jq -r .additionalContext <<<"$OUT" | grep -q "github:jbaruch/z@v3"; then
    pass; else fail "broken python3: expected the jq fallback to run the check, got OUT=$OUT calls=$CALLS err=$(cat "$TMP/err")"; fi

  # 23. the fetched ref is not origin's live tip (here the fetch refspec skips
  #     main while origin advances it) -> not updated.
  mk_project p23
  git -C "$SEED" push -q origin main:side || die "side branch push failed"
  git -C "$PROJECT" config remote.origin.fetch "+refs/heads/side:refs/remotes/origin/side" || die "refspec config failed"
  printf 'moved\n' >> "$SEED/agents.yaml" || die "seed edit failed"
  git -C "$SEED" commit -q -am moved || die "seed commit failed"
  git -C "$SEED" push -q origin main || die "seed push failed"
  run "$PROJECT" FAKE_OUT="Updated x"
  if [[ $RC -eq 0 && -z "$CALLS" ]] && context | grep -q "moved during the check"; then
    pass; else fail "stale fetched ref: expected a skip, got OUT=$OUT calls=$CALLS"; fi

  # 24. the safety fetch never runs repo code: a reference-transaction hook
  #     (which a ref-updating fetch triggers) leaves no marker.
  mk_project p24
  printf 'moved\n' >> "$SEED/agents.yaml" || die "seed edit failed"
  git -C "$SEED" commit -q -am moved || die "seed commit failed"
  git -C "$SEED" push -q origin main || die "seed push failed"
  printf '#!/bin/sh\ntouch "%s"\n' "$TMP/ref-hook-ran" > "$PROJECT/.git/hooks/reference-transaction" || die "cannot write the hook"
  chmod +x "$PROJECT/.git/hooks/reference-transaction" || die "cannot make the hook executable"
  run "$PROJECT" FAKE_OUT="Updated x"
  if [[ $RC -eq 0 && ! -e "$TMP/ref-hook-ran" ]]; then
    pass; else fail "repo hooks: the reference-transaction hook ran during the safety fetch (OUT=$OUT)"; fi

  # 25. ACR's output is masked before it reaches the session.
  mk_project p25
  run "$PROJECT" FAKE_RC=1 FAKE_OUT="fatal: https://jb:s3cret@github.com/x.git token ghp_abcDEF123 BEARER upTok3n.x bEaReR mixTok3n Basic YmFzaWNzZWNyZXQ= basic YTpi key sk-shortK"
  if [[ $RC -eq 0 ]] && context | grep -q "github.com/x.git" && ! context | grep -q "s3cret" && ! context | grep -q "abcDEF123" \
    && ! context | grep -q "upTok3n" && ! context | grep -q "mixTok3n" && ! context | grep -q "YmFzaWNzZWNyZXQ" \
    && ! context | grep -q "YTpi" && ! context | grep -q "shortK"; then
    pass; else fail "redaction: expected masked credentials, got OUT=$OUT"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} failed, ${PASS} passed"; exit 1; fi
  echo "PASSED: all ${PASS} checks"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
