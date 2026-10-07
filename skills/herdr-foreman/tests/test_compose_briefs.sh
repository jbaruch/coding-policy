#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-foreman/compose-briefs.sh.
#
# Templates and values are built in the test, so the assertions are about the
# script's own behavior rather than about the shipped templates' current text
# (rules/testing-standards.md — fixtures built in setup, no binary fixtures).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Substitution     -> shared + per-role values land in the right files.
#   2. Role wins        -> a role value overrides the same shared key.
#   3. Emitted paths    -> the JSON names COMMON.md and every brief.
#   4. Unfilled         -> a placeholder with no value is exit 2, nothing written.
#   5. Unknown key      -> a value no template uses is exit 2, nothing written.
#   6. Atomicity        -> a failing second role leaves the first unwritten.
#   7. Missing template -> exit 1 naming the path.
#   8. Bad JSON         -> exit 1.
#   9. Idempotent       -> a second identical run rewrites the same content.
#  10. Usage            -> exit 1 with a usage line.
#  11. Broken grep      -> a failing scan is exit 3, never "no placeholders".
#  12. Null value       -> exit 2, nothing written (a `null` would render as
#                          the literal text and leave no placeholder behind).
#  13. Object value     -> exit 2 for the same reason.
#  14. Number value     -> accepted; an issue number is legitimate text.
#  23. Judge weighing   -> the shipped judge-weighing brief renders under its
#                          role key, its deliverable in the ruling-file format.
#  24. Report contracts -> the shipped specialist brief renders its criteria
#                          under one Acceptance Criteria section, and the
#                          shipped reviewer and tester briefs ask for VERDICT.
#
# Run: bash skills/herdr-foreman/tests/test_compose_briefs.sh
# Progress goes to stderr; stdout carries one JSON result.
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

mk_templates() { # <dir>
  mkdir -p "$1" || die "could not create $1"
  printf 'Checkout: {{SHARED_CHECKOUT}}\nAuthority: {{AUTHORITY_STATEMENT}}\nContract: {{TEAM_OPERATION}}\n' > "$1/COMMON.md" \
    || die "could not write COMMON.md"
  printf 'Dev on {{BRANCH}} in {{WORKTREE}} for {{ISSUE}}\nReport: {{REPORT}}\n' > "$1/brief-developer.md" \
    || die "could not write brief-developer.md"
  printf 'Tester for {{ISSUE}}\nReport: {{REPORT}}\nPackage: {{REVIEW_PACKAGE}}\nRange: {{REVIEW_BASE}}..{{REVIEW_HEAD}}\n{{SLICE_SCOPE}}\n' > "$1/brief-tester.md" \
    || die "could not write brief-tester.md"
}

run() { # <templates> <values-file> <outdir>
  RUN_SEQ=$((RUN_SEQ+1))
  local values="$2"
  # Generic rendering fixtures supply a valid package. The dedicated package
  # suite exercises missing/invalid paths through the unwrapped production CLI.
  if [[ "$1" == "$TPL" ]] && jq -e . "$values" >/dev/null 2>&1; then
    values="$TMP/values.$RUN_SEQ.json"
    jq --arg p "$TMP/package.diff" \
      'if .roles | has("tester") then .roles.tester += {REVIEW_PACKAGE: $p, REVIEW_BASE: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", REVIEW_HEAD: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"} else . end' \
      "$2" > "$values" || die "could not prepare rendering fixture"
  fi
  # Every fixture supplies the team-round contract path every composition
  # requires; the dedicated TEAM_OPERATION cases call the CLI unwrapped.
  if jq -e '(.shared | type) == "object" and (.shared | has("TEAM_OPERATION") | not)' "$values" >/dev/null 2>&1; then
    local with_team="$TMP/values.$RUN_SEQ.team.json"
    jq --arg p "$TMP/package.diff" '.shared.TEAM_OPERATION = $p' "$values" > "$with_team" \
      || die "could not add TEAM_OPERATION to the rendering fixture"
    values="$with_team"
  fi
  if [[ "$1" == "$(dirname "$SCRIPT")/templates" ]]; then
    local with_base="$TMP/values.$RUN_SEQ.base.json"
    jq --arg state "$TMP/owner-state.json" --arg worktree "$TMP/worktrees/compose" \
      '. + {task: "compose-fixture", state: $state} | .roles |= with_entries(if .value | has("WORKTREE") then .value += {WORKTREE: $worktree, BRANCH: "feat/compose"} else . end)' \
      "$values" > "$with_base" || die "could not add owner task/worktree metadata"
    values="$with_base"
  fi
  OUT="$("${COMPOSE_BASH:-bash}" "$SCRIPT" "$1" "$values" "$3" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}

# `run_suite`, not `main`: the entry point every sourced-harness suite shares.
run_suite() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/compose-briefs.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "compose-briefs.sh not found at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"
  TMP="$(mktemp -d -t foreman-compose-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  printf 'Review fixture\n' > "$TMP/package.diff" || die "could not write package fixture"
  TPL="$TMP/templates"; mk_templates "$TPL"
  # Packaged templates consume a real normal-owner provision and task record.
  git init -q --bare -b main "$TMP/origin.git" || die "could not create origin fixture"
  git clone -q "$TMP/origin.git" "$TMP/shared" 2>"$TMP/clone.err" || die "could not clone fixture"
  printf 'fixture\n' > "$TMP/shared/README.md" || die "could not seed fixture"
  git -C "$TMP/shared" add README.md || die "could not stage fixture"
  git -C "$TMP/shared" -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm 'Fixture base' || die "could not commit fixture"
  git -C "$TMP/shared" push -q origin main || die "could not push fixture"
  WORKTREE_ROOT="$TMP/worktrees" bash "$(dirname "$SCRIPT")/provision-worktree.sh" \
    "$TMP/shared" feat/compose "$TMP/worktrees/compose" > "$TMP/provision.json" || die "could not provision fixture"
  jq '{task:"compose-fixture", base_revision:.base_revision, scope:"Compose bounded fixture", allowed_paths:["*"],
       authorization:{source:"fixture operator request",quote:"Compose this task"}}' \
    "$TMP/provision.json" > "$TMP/task.json" || die "could not prepare task input"
  bash "$(dirname "$SCRIPT")/foreman.sh" task --state "$TMP/owner-state.json" \
    --record "$TMP/task.json" --now 2026-01-08T12:00:00Z > "$TMP/task-output.json" || die "could not register fixture task"
  FAIL=0; PASS=0; RUN_SEQ=0

  # 1 + 3. A complete round.
  local v1="$TMP/v1.json" o1="$TMP/out1"
  cat > "$v1" <<'JSON' || die "could not write $v1"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "owner of jbaruch/x"},
  "roles": {
    "developer": {"BRANCH": "feat/x", "WORKTREE": "/wt/dev", "ISSUE": "#7", "REPORT": "/r/dev.md"},
    "tester": {"ISSUE": "#7", "REPORT": "/r/test.md"}
  }
}
JSON
  # Direct CLI calls below read v1 unwrapped, so it carries the required path.
  jq --arg p "$TMP/package.diff" '.shared.TEAM_OPERATION = $p' "$v1" > "$v1.tmp" \
    || die "could not add TEAM_OPERATION to $v1"
  mv "$v1.tmp" "$v1" || die "could not replace $v1"
  run "$TPL" "$v1" "$o1"
  if [[ $RC -eq 0 ]] \
     && grep -q "Checkout: /repo" "$o1/COMMON.md" \
     && grep -q "Dev on feat/x in /wt/dev for #7" "$o1/brief-developer.md" \
     && grep -q "Report: /r/test.md" "$o1/brief-tester.md"; then
    pass; else fail "substitution: got RC=$RC ERR=$ERRTEXT"; fi
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e --arg o "$o1" '
      .common == ($o + "/COMMON.md")
      and .briefs.developer == ($o + "/brief-developer.md")
      and .briefs.tester == ($o + "/brief-tester.md")' >/dev/null 2>&1; then
    pass; else fail "emitted paths: got OUT=$OUT"; fi

  # 2. A role value beats the shared one.
  local v2="$TMP/v2.json" o2="$TMP/out2"
  cat > "$v2" <<'JSON' || die "could not write $v2"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a", "ISSUE": "#1", "REPORT": "/shared.md"},
  "roles": {"tester": {"ISSUE": "#9"}}
}
JSON
  run "$TPL" "$v2" "$o2"
  if [[ $RC -eq 0 ]] && grep -q "Tester for #9" "$o2/brief-tester.md"; then
    pass; else fail "role override: got RC=$RC ERR=$ERRTEXT"; fi

  # 4. A placeholder with no value is a brief that lies to a worker.
  local v4="$TMP/v4.json" o4="$TMP/out4"
  cat > "$v4" <<'JSON' || die "could not write $v4"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"developer": {"BRANCH": "feat/x", "ISSUE": "#7", "REPORT": "/r.md"}}
}
JSON
  run "$TPL" "$v4" "$o4"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "WORKTREE"; then
    pass; else fail "unfilled: expected exit 2 naming WORKTREE, got RC=$RC ERR=$ERRTEXT"; fi
  if [[ ! -e "$o4/brief-developer.md" ]]; then
    pass; else fail "unfilled: nothing may be written on a validation failure"; fi

  # 5. A key no template uses is a value the foreman believes it sent.
  local v5="$TMP/v5.json" o5="$TMP/out5"
  cat > "$v5" <<'JSON' || die "could not write $v5"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"tester": {"ISSUE": "#7", "REPORT": "/r.md", "REPORTS_DIRR": "/typo"}}
}
JSON
  run "$TPL" "$v5" "$o5"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "REPORTS_DIRR"; then
    pass; else fail "unknown key: expected exit 2 naming the typo, got RC=$RC ERR=$ERRTEXT"; fi

  # 6. A round is all-or-nothing: a later role failing leaves no earlier file.
  local v6="$TMP/v6.json" o6="$TMP/out6"
  cat > "$v6" <<'JSON' || die "could not write $v6"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {
    "developer": {"BRANCH": "feat/x", "WORKTREE": "/wt", "ISSUE": "#7", "REPORT": "/r.md"},
    "tester": {"ISSUE": "#7"}
  }
}
JSON
  run "$TPL" "$v6" "$o6"
  if [[ $RC -eq 2 ]] && [[ ! -e "$o6/brief-developer.md" ]] && [[ ! -e "$o6/COMMON.md" ]]; then
    pass; else fail "atomicity: a failed round left files behind (RC=$RC)"; fi

  # 6b. The PACKAGED templates compose from the values Step 7 documents — for
  #     every shipped role, the release brief included. A template that grows
  #     a placeholder nobody documented fails here before it fails a worker.
  local v6b="$TMP/v6b.json" o6b="$TMP/out6b" PKG
  PKG="$(dirname "$SCRIPT")/templates"
  cat > "$v6b" <<'JSON' || die "could not write $v6b"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "owner of jbaruch/x",
             "EXTERNAL_PERMISSION": "none", "TASK_AUTHORIZATION": "Operator request: ship issue #7 in jbaruch/x",
             "AUTHORIZED_ACTIONS": "jbaruch/x: implement, push, create PR, request reviews, reply to review threads, merge, publish, clean up the released branch",
             "ISSUE": "#7", "BRANCH": "feat/x"},
  "roles": {
    "developer": {"WORKTREE": "/wt/dev", "REPORTS_DIR": "/r", "REPORT": "/r/dev.md"},
    "reviewer": {"REPORT": "/r/review.md"},
    "tester": {"WORKTREE": "/wt/test", "REPORTS_DIR": "/r", "REPORT": "/r/test.md"},
    "release": {"WORKTREE": "/wt/dev", "REPORTS_DIR": "/r", "REPORT": "/r/release.md", "WEIGHING_RULING": "none"}
  }
}
JSON
  jq --arg p "$TMP/package.diff" \
    '.shared += {POLICY_INDEX: $p, RELEASE_SKILL: $p, TEAM_OPERATION: $p, GATES: "- AGENTS.md\n- scripts/run-tests.sh"}
     | .roles.reviewer += {REVIEW_PACKAGE: $p, REVIEW_BASE: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", REVIEW_HEAD: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
     | .roles.tester += {REVIEW_PACKAGE: $p, REVIEW_BASE: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", REVIEW_HEAD: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}' \
    "$v6b" > "$TMP/packaged-values.json" || die "could not add packaged review paths"
  v6b="$TMP/packaged-values.json"
  run "$PKG" "$v6b" "$o6b"
  if [[ $RC -eq 0 ]] && grep -Fq "cd $TMP/worktrees/compose && pwd" "$o6b/brief-release.md" \
     && grep -q 'Skill(skill: "release")' "$o6b/brief-release.md"; then
    pass; else fail "packaged templates: expected exit 0 and a filled release brief, got RC=$RC ERR=$ERRTEXT"; fi

  # Every worker receives the team-round contract path through COMMON.md.
  if [[ $RC -eq 0 ]] && grep -Fq "the team-round contract at" "$o6b/COMMON.md" \
     && grep -Fq "\`$TMP/package.diff\`. Read it in full" "$o6b/COMMON.md"; then
    pass; else fail "packaged COMMON.md must name TEAM_OPERATION as a required read"; fi

  # The actual packaged artifacts must distinguish the owner's shipping
  # authorization from additional permission needed in a non-owned repo.
  if [[ $RC -eq 0 ]] \
     && grep -Fq 'Verified repo ownership: **owner of jbaruch/x**' "$o6b/COMMON.md" \
     && grep -Fq 'Operator request: ship issue #7 in jbaruch/x' "$o6b/COMMON.md" \
     && grep -Fq 'jbaruch/x: implement, push, create PR, request reviews, reply to review threads, merge, publish, clean up the released branch' "$o6b/COMMON.md" \
     && grep -Fq 'An owned repository requires no additional non-owner permission;' "$o6b/COMMON.md" \
     && grep -Fq 'its authorized task actions still bind you.' "$o6b/COMMON.md"; then
    pass; else fail "owner release must retain its actual task authorization with no additional non-owner permission"; fi

  local ownership authority_values authority_output
  for ownership in 'owner of jbaruch/x' 'not owner of jbaruch/x'; do
    authority_values="$TMP/authority-${ownership%% *}.json"
    authority_output="$TMP/authority-${ownership%% *}"
    jq --arg ownership "$ownership" \
      '.shared.AUTHORITY_STATEMENT = $ownership
       | .shared.TASK_AUTHORIZATION = "Operator request: inspect issue #7; do not change the repository"
       | .shared.AUTHORIZED_ACTIONS = "none" | .shared.EXTERNAL_PERMISSION = "none"' \
      "$v6b" > "$authority_values" || die "could not build read-only authority fixture"
    run "$PKG" "$authority_values" "$authority_output"
    if [[ $RC -eq 0 ]] \
       && grep -Fq "Verified repo ownership: **$ownership**" "$authority_output/COMMON.md" \
       && grep -Fq 'Authorized task actions and target repo this round: **none**' "$authority_output/COMMON.md" \
       && grep -Fq 'make no repository changes or GitHub writes' "$authority_output/COMMON.md" \
       && grep -Fq 'In a non-owned repository, every write also requires' "$authority_output/COMMON.md" \
       && grep -Fq 'This role grants no additional permission.' "$authority_output/brief-release.md" \
       && grep -Fq 'report BLOCKED' "$authority_output/brief-release.md"; then
      pass; else fail "read-only $ownership must deny writes even in an accidentally selected release role: RC=$RC ERR=$ERRTEXT"; fi
  done

  # A foreman using the old values cannot silently omit task authorization.
  local missing_authority
  for missing_authority in TASK_AUTHORIZATION AUTHORIZED_ACTIONS; do
    jq --arg key "$missing_authority" 'del(.shared[$key])' "$v6b" > "$TMP/authority-missing.json" \
      || die "could not build missing authority fixture"
    run "$PKG" "$TMP/authority-missing.json" "$TMP/missing-$missing_authority"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/missing-$missing_authority" && "$ERRTEXT" == *"$missing_authority"* ]]; then
      pass; else fail "missing $missing_authority must refuse before emitting any brief"; fi
  done

  local missing_policy
  for missing_policy in POLICY_INDEX RELEASE_SKILL TEAM_OPERATION; do
    jq --arg key "$missing_policy" '.shared[$key] = "/absent/policy-artifact.md"' "$v6b" > "$TMP/policy-missing.json" \
      || die "could not build absent policy fixture"
    run "$PKG" "$TMP/policy-missing.json" "$TMP/missing-$missing_policy"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/missing-$missing_policy" && "$ERRTEXT" == *"$missing_policy"* ]]; then
      pass; else fail "unreadable $missing_policy must refuse before emitting any brief"; fi
  done

  # Policy artifacts are shared inputs; role overrides cannot bypass their
  # validation, even when the override is readable or the shared key is absent.
  local override_case override_value
  for missing_policy in POLICY_INDEX RELEASE_SKILL TEAM_OPERATION; do
    for override_case in invalid readable role-only; do
      override_value="/absent/role-policy.md"
      if [[ "$override_case" == readable ]]; then override_value="$TMP/package.diff"; fi
      jq --arg key "$missing_policy" --arg value "$override_value" --arg shape "$override_case" \
        '.roles.tester[$key] = $value | if $shape == "role-only" then del(.shared[$key]) else . end' \
        "$v6b" > "$TMP/policy-override.json" || die "could not build role policy override"
      run "$PKG" "$TMP/policy-override.json" "$TMP/override-$missing_policy-$override_case"
      if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/override-$missing_policy-$override_case" && "$ERRTEXT" == *"$missing_policy"* ]]; then
        pass; else fail "$override_case role $missing_policy must refuse before emitting any brief: $ERRTEXT"; fi
    done
  done

  # 6c. A REPORT path that would wrap the worker's marker line is refused
  #     before any file is written: the wait confirms the complete marker on
  #     one visible row, and a wrap anywhere in it cannot be confirmed.
  local v6c="$TMP/v6c.json" o6c="$TMP/out6c" long_report
  long_report="/very/long/reports/directory/that/keeps/going/and/going/round-3/reports/developer-report-for-issue-12.md"
  cat > "$v6c" <<JSON || die "could not write $v6c"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"tester": {"ISSUE": "#7", "REPORT": "${long_report}"}}
}
JSON
  run "$TPL" "$v6c" "$o6c"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "REPORT for role 'tester'" && [[ ! -e "$o6c" ]]; then
    pass; else fail "long REPORT: expected exit 2 naming the role and no output dir at all, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  FOREMAN_REPORT_PATH_MAX_COLS=200 run "$TPL" "$v6c" "$o6c"
  if [[ $RC -eq 0 ]]; then
    pass; else fail "long REPORT under a raised limit: expected exit 0, got RC=$RC ERR=$ERRTEXT"; fi
  # 6e. A leading-zero override is decimal downstream: `0200` raises the limit
  #     like `200` does, with no octal reparse.
  FOREMAN_REPORT_PATH_MAX_COLS=0200 run "$TPL" "$v6c" "$o6c"
  if [[ $RC -eq 0 ]] && ! printf '%s' "$ERRTEXT" | grep -q "value too great"; then
    pass; else fail "leading-zero limit: expected exit 0 and no octal error, got RC=$RC ERR=$ERRTEXT"; fi

  # 6d. A bad limit override is a precondition failure, never an arithmetic abort.
  FOREMAN_REPORT_PATH_MAX_COLS=soon run "$TPL" "$v6c" "$o6c"
  if [[ $RC -eq 1 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "FOREMAN_REPORT_PATH_MAX_COLS must be a positive integer"; then
    pass; else fail "bad limit override: expected exit 1 naming it, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 7. A role with no template is named, not guessed at.
  local v7="$TMP/v7.json" o7="$TMP/out7"
  cat > "$v7" <<'JSON' || die "could not write $v7"
{"shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"}, "roles": {"scribe": {}}}
JSON
  run "$TPL" "$v7" "$o7"
  if [[ $RC -eq 1 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "brief-scribe.md"; then
    pass; else fail "missing template: expected exit 1 naming it, got RC=$RC ERR=$ERRTEXT"; fi

  # 8. Malformed values.
  local v8="$TMP/v8.json"
  printf '{broken' > "$v8" || die "could not write $v8"
  run "$TPL" "$v8" "$TMP/out8"
  if [[ $RC -eq 1 && -z "$OUT" ]]; then
    pass; else fail "bad JSON: expected exit 1, got RC=$RC"; fi

  # 9. Re-running a round rewrites the same content (idempotent).
  run "$TPL" "$v1" "$o1"
  if [[ $RC -eq 0 ]] && grep -q "Dev on feat/x in /wt/dev for #7" "$o1/brief-developer.md"; then
    pass; else fail "idempotent: second run changed the output (RC=$RC)"; fi

  # 9b. TEAM_OPERATION is required for every composition: a values file
  #     without it refuses before anything is written, whatever the templates.
  jq 'del(.shared.TEAM_OPERATION)' "$v1" > "$TMP/v9b.json" || die "could not build the missing-contract fixture"
  OUT="$(bash "$SCRIPT" "$TPL" "$TMP/v9b.json" "$TMP/out9b" 2>"$TMP/e9b")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out9b" ]] && grep -q "TEAM_OPERATION is required" "$TMP/e9b"; then
    pass; else fail "missing TEAM_OPERATION: expected exit 2 and nothing written, got RC=$RC ERR=$(cat "$TMP/e9b")"; fi

  # 9c. A custom common template that does not render the contract path is
  #     refused, so no brief ships without the required read.
  local nocontract="$TMP/nocontract-templates"
  mk_templates "$nocontract"
  printf 'Checkout: {{SHARED_CHECKOUT}}\nAuthority: {{AUTHORITY_STATEMENT}}\n' > "$nocontract/COMMON.md" \
    || die "could not write the contract-less COMMON.md"
  OUT="$(bash "$SCRIPT" "$nocontract" "$v1" "$TMP/out9c" 2>"$TMP/e9c")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out9c" ]] && grep -q "carries no {{TEAM_OPERATION}} placeholder" "$TMP/e9c"; then
    pass; else fail "contract-less COMMON.md: expected exit 2 and nothing written, got RC=$RC ERR=$(cat "$TMP/e9c")"; fi

  # 9d. A custom common template that carries the placeholder renders the path.
  if [[ -f "$o1/COMMON.md" ]] && grep -Fq "Contract: $TMP/package.diff" "$o1/COMMON.md"; then
    pass; else fail "custom COMMON.md must render the TEAM_OPERATION path"; fi

  # 10. Usage.
  OUT="$(bash "$SCRIPT" 2>"$TMP/e10")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "usage:" "$TMP/e10"; then
    pass; else fail "usage: expected exit 1 with a usage line, got RC=$RC"; fi

  # 11. A scan that cannot run must never report a clean render. A grep that
  #     exits 2 (unreadable input, bad pattern) is a tool failure, and
  #     collapsing it into "no placeholders left" is what would ship an
  #     unrendered brief to a worker.
  local bin="$TMP/brokenbin"
  mkdir -p "$bin" || die "could not create $bin"
  printf '#!/usr/bin/env bash\nexit 2\n' > "$bin/grep" || die "could not write the failing grep"
  chmod +x "$bin/grep" || die "could not chmod the failing grep"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(PATH="$bin:$PATH" bash "$SCRIPT" "$TPL" "$v1" "$TMP/out11" 2>"$TMP/e11")"; RC=$?
  if [[ $RC -eq 3 && -z "$OUT" ]] && grep -Eq "placeholder scan failed|cannot scan placeholders" "$TMP/e11"; then
    pass; else fail "broken grep: expected exit 3 naming the scan, got RC=$RC ERR=$(cat "$TMP/e11")"; fi
  if [[ ! -e "$TMP/out11/COMMON.md" ]]; then
    pass; else fail "broken grep: nothing may be written when the scan failed"; fi

  # 12. `jq -r` prints a JSON null as the four characters `null`, which
  #     substitutes cleanly and leaves no placeholder behind — the brief reads
  #     as fully rendered while telling a worker its worktree is at `null`.
  local v12="$TMP/v12.json" o12="$TMP/out12"
  cat > "$v12" <<'JSON' || die "could not write $v12"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"developer": {"BRANCH": "feat/x", "WORKTREE": null, "ISSUE": "#7", "REPORT": "/r.md"}}
}
JSON
  run "$TPL" "$v12" "$o12"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "WORKTREE (null)"; then
    pass; else fail "null value: expected exit 2 naming it, got RC=$RC ERR=$ERRTEXT"; fi
  if [[ ! -e "$o12/brief-developer.md" && ! -e "$o12/COMMON.md" ]]; then
    pass; else fail "null value: nothing may be written on a validation failure"; fi

  # 13. An object arrives as a JSON fragment the same way.
  local v13="$TMP/v13.json" o13="$TMP/out13"
  cat > "$v13" <<'JSON' || die "could not write $v13"
{
  "shared": {"SHARED_CHECKOUT": {"path": "/repo"}, "AUTHORITY_STATEMENT": "a"},
  "roles": {"tester": {"ISSUE": "#7", "REPORT": "/r.md"}}
}
JSON
  run "$TPL" "$v13" "$o13"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "SHARED_CHECKOUT (object)"; then
    pass; else fail "object value: expected exit 2 naming it, got RC=$RC ERR=$ERRTEXT"; fi

  # 14. A number is legitimate text — an issue number reads the same either way.
  local v14="$TMP/v14.json" o14="$TMP/out14"
  cat > "$v14" <<'JSON' || die "could not write $v14"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"tester": {"ISSUE": 42, "REPORT": "/r.md"}}
}
JSON
  run "$TPL" "$v14" "$o14"
  if [[ $RC -eq 0 ]] && grep -q "Tester for 42" "$o14/brief-tester.md"; then
    pass; else fail "number value: expected it to render, got RC=$RC ERR=$ERRTEXT"; fi

  # 15. A new attempt cannot reuse a prior report, or point two roles at one
  #     destination. Refusal preserves the old file and writes no new briefs.
  local old_report="$TMP/old-report.md" invalid_report v15="$TMP/v15.json"
  printf 'Prior attempt\n' > "$old_report" || die "could not write prior report"
  for invalid_report in "$old_report" "relative/report.md" "/r/report.md"$'\nextra' "/r/report.md"$'\n' "/r/"; do
    jq --arg p "$invalid_report" '.roles.developer.REPORT = $p' "$v1" > "$v15" \
      || die "could not build invalid report fixture"
    run "$TPL" "$v15" "$TMP/out15"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out15" ]] \
       && grep -q 'Prior attempt' "$old_report"; then
      pass; else fail "invalid/reused report: RC=$RC OUT=$OUT ERR=$ERRTEXT path=$invalid_report"; fi
  done
  jq '.roles.tester.REPORT = .roles.developer.REPORT' "$v1" > "$v15" \
    || die "could not build duplicate report fixture"
  run "$TPL" "$v15" "$TMP/out15"
  if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out15" ]] \
     && printf '%s' "$ERRTEXT" | grep -q 'distinct report path'; then
    pass; else fail "shared report destination: RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # A generated briefing artifact cannot stand in for a worker's report.
  jq --arg p "$TMP/out15/brief-developer.md" '.roles.developer.REPORT = $p' "$v1" > "$v15" \
    || die "could not build overlapping report fixture"
  FOREMAN_REPORT_PATH_MAX_COLS=500 run "$TPL" "$v15" "$TMP/out15"
  if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out15" ]] \
     && printf '%s' "$ERRTEXT" | grep -q 'overlaps a generated brief'; then
    pass; else fail "report/brief overlap: RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 15b. A path the worker's `REPORT: <path>` line or a brief carries is
  #      refused for every character foreman/renderable.py refuses, not only
  #      C0 and DEL: a C1 control or U+2028/U+2029 splits the marker across
  #      rows, and a bidi override reorders it (#578).
  local sep sep_label sep_path
  for sep in '\u0085' '\u009b' '\u2028' '\u2029' '\u202e' '\ue000' '\u0060'; do
    sep_label="${sep:2}"
    jq --argjson c "\"${sep}\"" '.roles.developer.REPORT = ("/r/dev" + $c + "x.md")' "$v1" > "$v15" \
      || die "could not build the separator report fixture"
    run "$TPL" "$v15" "$TMP/out15b"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out15b" ]] \
       && printf '%s' "$ERRTEXT" | grep -q "REPORT for role 'developer'"; then
      pass; else fail "REPORT with U+${sep_label}: expected exit 2 and nothing written, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
    # A real file whose name carries the character: the refusal is the
    # character rule, never a missing file.
    sep_path="$(jq -rn --argjson c "\"${sep}\"" --arg d "$TMP" '$d + "/pkg" + $c + ".diff"')" \
      || die "could not build the separator package path"
    printf 'diff\n' > "$sep_path" || die "could not write the separator package"
    jq --arg p "$sep_path" '.roles.tester.REVIEW_PACKAGE = $p' "$v6b" > "$TMP/v15b-package.json" \
      || die "could not build the separator package fixture"
    run "$PKG" "$TMP/v15b-package.json" "$TMP/out15b"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out15b" ]] \
       && printf '%s' "$ERRTEXT" | grep -q "REVIEW_PACKAGE for role 'tester'"; then
      pass; else fail "REVIEW_PACKAGE with U+${sep_label}: expected exit 2, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
    jq --arg p "$sep_path" '.shared.POLICY_INDEX = $p' "$v6b" > "$TMP/v15b-policy.json" \
      || die "could not build the separator policy fixture"
    run "$PKG" "$TMP/v15b-policy.json" "$TMP/out15b"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out15b" ]] \
       && printf '%s' "$ERRTEXT" | grep -q "POLICY_INDEX must be a file path"; then
      pass; else fail "POLICY_INDEX with U+${sep_label}: expected exit 2, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
    rm -f "$sep_path" || die "could not remove the separator package"
  done

  # 15c. A check that cannot run is exit 3, never an accepted path.
  local pyshim="$TMP/pyshim"
  mkdir -p "$pyshim" || die "could not create the python3 shim dir"
  printf '#!/usr/bin/env bash\nset -euo pipefail\nexit 7\n' > "$pyshim/python3" \
    || die "could not write the python3 shim"
  chmod +x "$pyshim/python3" || die "could not make the python3 shim executable"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(PATH="$pyshim:$PATH" bash "$SCRIPT" "$TPL" "$v1" "$TMP/out15c" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  if [[ $RC -eq 3 && -z "$OUT" && ! -e "$TMP/out15c" ]] \
     && printf '%s' "$ERRTEXT" | grep -q "renderable-text check failed"; then
    pass; else fail "broken renderable check: expected exit 3 and nothing written, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 15d. A crash exiting 1 (python3's code for an uncaught exception, such as
  #      a failed import) is a check that could not run, never a refusal.
  local crashshim="$TMP/crashshim"
  mkdir -p "$crashshim" || die "could not create the crash shim dir"
  printf '#!/usr/bin/env bash\nset -euo pipefail\necho "ModuleNotFoundError: No module named foreman" >&2\nexit 1\n' \
    > "$crashshim/python3" || die "could not write the crash shim"
  chmod +x "$crashshim/python3" || die "could not make the crash shim executable"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(PATH="$crashshim:$PATH" bash "$SCRIPT" "$TPL" "$v1" "$TMP/out15d" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  if [[ $RC -eq 3 && -z "$OUT" && ! -e "$TMP/out15d" ]] \
     && printf '%s' "$ERRTEXT" | grep -q "renderable-text check failed"; then
    pass; else fail "crashed renderable check: expected exit 3 and nothing written, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 16. A review SEAT owes its role's review-package checks. The slice narrows
  #     what a reviewer reviews, never what its brief must carry (#434).
  local v16="$TMP/v16.json" o16="$TMP/out16"
  jq --arg p "$TMP/package.diff" \
    '.roles = {"tester#core": (.roles.tester + {REVIEW_PACKAGE: $p, REVIEW_BASE: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", REVIEW_HEAD: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", REPORT: "/r/slice-core.md", SLICE_PATHS: ["src/core/*", "README.md"], SLICE_DIGEST: "0123456789ab"})}' \
    "$v1" > "$v16" || die "could not build seat fixture"
  run "$TPL" "$v16" "$o16"
  if [[ $RC -eq 0 ]] && [[ -f "$o16/brief-tester#core.md" ]]; then
    pass; else fail "seat brief: expected the role's template to render for tester#core, got RC=$RC ERR=$ERRTEXT"; fi
  local seat_invalid
  for seat_invalid in '.["tester#core"].REVIEW_BASE = "not-a-sha"' '.["tester#core"].REVIEW_PACKAGE = "/absent/package.diff"'; do
    jq "(.roles) |= ($seat_invalid)" "$v16" > "$TMP/v16-bad.json" || die "could not build invalid seat fixture"
    run "$TPL" "$TMP/v16-bad.json" "$TMP/out16-bad"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out16-bad" ]] \
       && printf '%s' "$ERRTEXT" | grep -q "tester#core"; then
      pass; else fail "seat review package: an invalid $seat_invalid must refuse, got RC=$RC ERR=$ERRTEXT"; fi
  done

  # 17. The role key names the brief this run WRITES, so a key that is not a
  #     role or a `<role>#<slice>` seat is refused before it reaches a path.
  #     `template_for_role` resolves a seat to its role, so an unchecked key
  #     could take the reviewer template and redirect its output out of the
  #     output directory (#434).
  local bad_key
  for bad_key in 'reviewer#/../../outside' 'reviewer#' 'reviewer#a b' '../developer' 'dev/eloper' 'dev,eloper' 'dev=eloper'; do
    jq --arg k "$bad_key" '.roles = {($k): .roles.developer}' "$v1" > "$TMP/v17.json" \
      || die "could not build the malformed-key fixture"
    run "$TPL" "$TMP/v17.json" "$TMP/out17"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out17" ]] \
       && printf '%s' "$ERRTEXT" | grep -qE "cannot name the brief it writes|cannot address it"; then
      pass; else fail "role key: '$bad_key' must refuse before writing, got RC=$RC ERR=$ERRTEXT"; fi
    rm -rf "$TMP/out17"
  done

  # 17b. A key carrying a newline is split into two pseudo-roles by the
  #      line-oriented read, so the shell test never sees it. Rejected in jq,
  #      before the keys become lines.
  local split_key
  for split_key in '"dev\neloper"' '"dev\u0000eloper"' '"dev\u0007eloper"'; do
    jq --argjson k "$split_key" '.roles = {($k): .roles.developer}' "$v1" > "$TMP/v17b.json" \
      || die "could not build the split-key fixture"
    run "$TPL" "$TMP/v17b.json" "$TMP/out17b"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out17b" ]] \
       && printf '%s' "$ERRTEXT" | grep -q "cannot name the brief it writes"; then
      pass; else fail "role key: a key carrying $split_key must refuse, got RC=$RC ERR=$ERRTEXT"; fi
    rm -rf "$TMP/out17b"
  done

  # 18. Only a seatable responsibility is seated. The shape check alone would
  #     compose a `developer#api` brief that `plan`, `apply` and recovery all
  #     refuse (#434).
  local unseatable
  # The last one matched inside " reviewer tester " under substring membership.
  for unseatable in 'developer#api' 'advisor#core' 'release#a' 'reviewer tester#api'; do
    jq --arg k "$unseatable" '.roles = {($k): .roles.developer}' "$v1" > "$TMP/v18.json" \
      || die "could not build the unseatable-seat fixture"
    run "$TPL" "$TMP/v18.json" "$TMP/out18"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out18" ]] \
       && printf '%s' "$ERRTEXT" | grep -q "only reviewer, tester are seated"; then
      pass; else fail "unseatable seat: '$unseatable' must refuse, got RC=$RC ERR=$ERRTEXT"; fi
    rm -rf "$TMP/out18"
  done

  # 19. A custom unseated role key keeps composing: the planner accepts one and
  #     the composer already resolved `brief-<role>.md` for it.
  #     The composer accepts exactly what the planner emits, so a custom role
  #     cannot pass plan and then fail compose.
  local v19="$TMP/v19.json" o19="$TMP/out19" custom
  for custom in role_v2 reviewer.v2 Role 'foo@bar' 'foo..bar' '.custom' '-custom' 'two words'; do
    cp "$TPL/brief-developer.md" "$TPL/brief-${custom}.md" || die "could not add the custom template"
    jq --arg k "$custom" '.roles = {($k): .roles.developer}' "$v1" > "$v19" || die "could not build the custom-role fixture"
    rm -rf "$o19"
    run "$TPL" "$v19" "$o19"
    if [[ $RC -eq 0 ]] && [[ -f "$o19/brief-${custom}.md" ]]; then
      pass; else fail "custom role: '$custom' must compose, got RC=$RC ERR=$ERRTEXT"; fi
  done

  # 20. A seat's brief names its slice and forbids roaming, rendered from the
  #     seat rather than supplied, so a partitioned round cannot dispatch
  #     several full-surface verdicts (skills/herdr-foreman/references/team-operation.md).
  local o20="$TMP/out20"
  run "$TPL" "$v16" "$o20"
  if [[ $RC -eq 0 ]] && grep -q "Your slice this round is \*\*core\*\*" "$o20/brief-tester#core.md" \
     && grep -q "forms no part of your verdict" "$o20/brief-tester#core.md"; then
    pass; else fail "slice scope: the seat brief must name its slice, got RC=$RC ERR=$ERRTEXT"; fi
  local o20b="$TMP/out20b"
  run "$TPL" "$v1" "$o20b"
  if [[ $RC -eq 0 ]] && ! grep -q "Your slice this round" "$o20b/brief-tester.md"; then
    pass; else fail "slice scope: an unseated tester brief must carry none, got RC=$RC ERR=$ERRTEXT"; fi
  jq '.roles["tester#core"].SLICE_SCOPE = "mine"' "$v16" > "$TMP/v20.json" || die "could not build the supplied-scope fixture"
  run "$TPL" "$TMP/v20.json" "$TMP/out20c"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -q "composed from the seat and its paths, not supplied"; then
    pass; else fail "slice scope: a supplied SLICE_SCOPE must refuse, got RC=$RC ERR=$ERRTEXT"; fi

  # 21. A seat needs the paths its slice owns. A slice name alone leaves the
  #     worker no boundary to resolve, and the composer sees no partition.
  jq 'del(.roles["tester#core"].SLICE_PATHS)' "$v16" > "$TMP/v21.json" || die "could not build the pathless-seat fixture"
  run "$TPL" "$TMP/v21.json" "$TMP/out21"
  if [[ $RC -eq 2 && ! -e "$TMP/out21" ]] && printf '%s' "$ERRTEXT" | grep -q "needs SLICE_PATHS"; then
    pass; else fail "slice paths: a seat without SLICE_PATHS must refuse, got RC=$RC ERR=$ERRTEXT"; fi
  local bad_paths
  for bad_paths in '[]' '"src/core/*"' '[""]' '["   "]' '[1]'; do
    jq --argjson v "$bad_paths" '.roles["tester#core"].SLICE_PATHS = $v' "$v16" > "$TMP/v21b.json" \
      || die "could not build the malformed-paths fixture"
    run "$TPL" "$TMP/v21b.json" "$TMP/out21b"
    if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -q "needs SLICE_PATHS"; then
      pass; else fail "slice paths: '$bad_paths' must refuse, got RC=$RC ERR=$ERRTEXT"; fi
  done
  jq '.roles.developer.SLICE_PATHS = ["src/*"]' "$v1" > "$TMP/v21c.json" || die "could not build the unseated-paths fixture"
  run "$TPL" "$TMP/v21c.json" "$TMP/out21c"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -q "names a slice it does not own"; then
    pass; else fail "slice paths: an unseated role must not carry SLICE_PATHS, got RC=$RC ERR=$ERRTEXT"; fi
  if grep -qF 'src/core/*' "$o20/brief-tester#core.md" \
     && grep -qF 'README.md' "$o20/brief-tester#core.md"; then
    pass; else fail "slice paths: the seat brief must list the paths its slice owns"; fi

  # 21j. The digest travels into the brief, and a seat without one is refused:
  #      it is what `apply` reads back to prove the boundary is the checked one.
  if grep -qF '(Partition 0123456789ab.)' "$o20/brief-tester#core.md"; then
    pass; else fail "slice digest: the seat brief must carry its partition digest"; fi
  local bad_digest
  for bad_digest in 'null' '"short"' '"NOTHEXDIGIT"' '""'; do
    jq --argjson v "$bad_digest" '.roles["tester#core"].SLICE_DIGEST = $v' "$v16" > "$TMP/v21j.json" \
      || die "could not build the bad-digest fixture"
    run "$TPL" "$TMP/v21j.json" "$TMP/out21j"
    if [[ $RC -eq 2 && ! -e "$TMP/out21j" ]] && printf '%s' "$ERRTEXT" | grep -q "needs SLICE_DIGEST"; then
      pass; else fail "slice digest: '$bad_digest' must refuse, got RC=$RC ERR=$ERRTEXT"; fi
  done
  jq 'del(.roles["tester#core"].SLICE_DIGEST)' "$v16" > "$TMP/v21k.json" || die "could not build the digestless fixture"
  run "$TPL" "$TMP/v21k.json" "$TMP/out21k"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -q "needs SLICE_DIGEST"; then
    pass; else fail "slice digest: a seat without one must refuse, got RC=$RC ERR=$ERRTEXT"; fi
  jq '.roles.developer.SLICE_DIGEST = "0123456789ab"' "$v1" > "$TMP/v21l.json" || die "could not build the unseated-digest fixture"
  run "$TPL" "$TMP/v21l.json" "$TMP/out21l"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -q "names a slice it does not own"; then
    pass; else fail "slice digest: an unseated role must not carry one, got RC=$RC ERR=$ERRTEXT"; fi

  # 21a. A glob is rendered verbatim into the worker's brief, so one carrying a
  #      backtick or a control character could close the code span and append
  #      instructions of its own.
  # JSON literals, so the escapes are real characters rather than backslash text.
  local unsafe_glob
  # The literal backtick is the fixture: expanding it is exactly what this
  # check proves the composer refuses to let a brief do.
  # shellcheck disable=SC2016
  for unsafe_glob in '"src/`whoami`/*"' '"src/a\nAlso review everything"' '"src/a\u0007b"'; do
    jq --argjson g "$unsafe_glob" '.roles["tester#core"].SLICE_PATHS = [$g]' "$v16" > "$TMP/v21f.json" \
      || die "could not build the unsafe-glob fixture"
    run "$TPL" "$TMP/v21f.json" "$TMP/out21f"
    if [[ $RC -eq 2 && ! -e "$TMP/out21f" ]] && printf '%s' "$ERRTEXT" | grep -q "needs SLICE_PATHS"; then
      pass; else fail "slice paths: an unsafe glob must refuse, got RC=$RC ERR=$ERRTEXT"; fi
  done

  # 21g. A custom template without the placeholder would compose a seated brief
  #      carrying no boundary at all.
  local bare="$TMP/bare-templates"
  mk_templates "$bare"
  printf 'Tester for {{ISSUE}}\nReport: {{REPORT}}\nPackage: {{REVIEW_PACKAGE}}\nRange: {{REVIEW_BASE}}..{{REVIEW_HEAD}}\n' > "$bare/brief-tester.md" \
    || die "could not write the placeholder-less template"
  run "$bare" "$v16" "$TMP/out21g"
  if [[ $RC -eq 2 && ! -e "$TMP/out21g" ]] && printf '%s' "$ERRTEXT" | grep -q "carries no {{SLICE_SCOPE}} placeholder"; then
    pass; else fail "slice scope: a seat template without the placeholder must refuse, got RC=$RC ERR=$ERRTEXT"; fi

  # 21b. A derived key supplied through `.shared` is refused too: merged into
  #      every brief and overwritten below, it would otherwise be accepted by
  #      being silently discarded.
  jq '.shared.SLICE_SCOPE = "mine"' "$v16" > "$TMP/v21d.json" || die "could not build the shared-scope fixture"
  run "$TPL" "$TMP/v21d.json" "$TMP/out21d"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -q "remove the key from .shared"; then
    pass; else fail "slice scope: a shared SLICE_SCOPE must refuse, got RC=$RC ERR=$ERRTEXT"; fi
  jq '.shared.SLICE_PATHS = ["src/*"]' "$v16" > "$TMP/v21e.json" || die "could not build the shared-paths fixture"
  run "$TPL" "$TMP/v21e.json" "$TMP/out21e"
  # The shared text check reaches an array first; either refusal names the key,
  # and both leave nothing written.
  if [[ $RC -eq 2 && ! -e "$TMP/out21e" ]] && printf '%s' "$ERRTEXT" | grep -q "SLICE_PATHS"; then
    pass; else fail "slice paths: a shared SLICE_PATHS must refuse, got RC=$RC ERR=$ERRTEXT"; fi
  jq '.shared.SLICE_DIGEST = "0123456789ab"' "$v16" > "$TMP/v21f.json" || die "could not build the shared-digest fixture"
  run "$TPL" "$TMP/v21f.json" "$TMP/out21f"
  if [[ $RC -eq 2 && ! -e "$TMP/out21f" ]] && printf '%s' "$ERRTEXT" | grep -q "SLICE_DIGEST belongs to one seat"; then
    pass; else fail "slice digest: a shared SLICE_DIGEST must refuse, got RC=$RC ERR=$ERRTEXT"; fi

  # 21h. A failing slice_scope aborts instead of composing an empty boundary.
  #      Nested in the outer jq's --arg, its non-zero status was discarded and
  #      the seated brief rendered with no slice at all.
  local jqshim="$TMP/jqshim"
  mkdir -p "$jqshim" || die "could not create the jq shim dir"
  {
    printf '#!/bin/sh\n'
    printf 'for a in "$@"; do\n'
    # The shim's own source, written literally: the backtick and `$a` are the
    # text of the generated script, not expansions this shell should perform.
    # shellcheck disable=SC2016
    printf '  case "$a" in *"map(\\"\\`\\""*) exit 4 ;; esac\n'
    printf 'done\n'
    printf 'exec %s "$@"\n' "$(command -v jq)"
  } > "$jqshim/jq" || die "could not write the jq shim"
  chmod +x "$jqshim/jq" || die "could not make the jq shim executable"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(PATH="$jqshim:$PATH" bash "$SCRIPT" "$TPL" "$v16" "$TMP/out21h" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  if [[ $RC -ne 0 && -z "$OUT" && ! -e "$TMP/out21h" ]]; then
    pass; else fail "slice scope: a failing renderer must abort, got RC=$RC OUT=$OUT"; fi

  # 22. The SHIPPED reviewer and tester templates carry the placeholder, so a
  #     real seated round renders the boundary rather than dropping it.
  local shipped shipped_role
  shipped="$(cd "$(dirname "$SCRIPT")/templates" && pwd)" || die "could not resolve the shipped templates"
  for shipped_role in reviewer tester; do
    if grep -q "{{SLICE_SCOPE}}" "$shipped/brief-${shipped_role}.md"; then
      pass; else fail "shipped brief-${shipped_role}.md must carry {{SLICE_SCOPE}}"; fi
  done

  # 23. The shipped judge-weighing brief renders under its own role key, and
  #     its deliverable opens with the ruling-file lines the release script
  #     reads.
  local vw="$TMP/weighing.json" ow="$TMP/out23"
  jq -n --arg p "$TMP/package.diff" \
    '{shared: {SHARED_CHECKOUT: "/repo", AUTHORITY_STATEMENT: "owner of jbaruch/x", EXTERNAL_PERMISSION: "none",
               TASK_AUTHORIZATION: "Operator request: ship issue #7 in jbaruch/x", AUTHORIZED_ACTIONS: "none",
               POLICY_INDEX: $p, RELEASE_SKILL: $p, TEAM_OPERATION: $p, GATES: "- AGENTS.md"},
      roles: {"judge-weighing": {TASK: "issue-7", HEAD: "cccccccccccccccccccccccccccccccccccccccc",
               NOMINATIONS: "1. policy skills/x/run.sh:3 error-handling — churn: added by the last fix",
               INVESTIGATION_REPORT: "none", TREE: "/wt/judge", REPORT: "/r/judge-weighing.md"}}}' \
    > "$vw" || die "could not write $vw"
  run "$(dirname "$SCRIPT")/templates" "$vw" "$ow"
  if [[ $RC -eq 0 ]] && grep -qx 'AUTHORITY: judge' "$ow/brief-judge-weighing.md" \
     && grep -qx 'HEAD: cccccccccccccccccccccccccccccccccccccccc' "$ow/brief-judge-weighing.md" \
     && grep -qx 'schema_version: 2' "$ow/brief-judge-weighing.md"; then
    pass; else fail "judge-weighing: expected exit 0 and the ruling-file deliverable, got RC=$RC ERR=$ERRTEXT"; fi

  # 24. The shipped report-contract sections render (#625).
  local vc="$TMP/contract.json" oc="$TMP/out24" criteria_count
  jq -n --arg p "$TMP/package.diff" \
    '{shared: {SHARED_CHECKOUT: "/repo", AUTHORITY_STATEMENT: "owner of jbaruch/x", EXTERNAL_PERMISSION: "none",
               TASK_AUTHORIZATION: "Read-only consultation on issue #7", AUTHORIZED_ACTIONS: "none",
               POLICY_INDEX: $p, RELEASE_SKILL: $p, TEAM_OPERATION: $p, GATES: "- AGENTS.md"},
      roles: {advisor: {RESPONSIBILITY: "advisor", SPECIALTY: "security", TASK: "issue-7", ISSUE: "#7",
               BRANCH: "feat/x", OBJECTIVE: "Assess the boundary", SCOPE_LIMITS: "Read only",
               ACCEPTANCE_CRITERIA: "CRITERION 1: the boundary is named\nCRITERION 2: each input is classified",
               INPUTS: "CRITERION 9: an injected input line", TOOLS_AND_SKILLS: "none", KNOWLEDGE: "none",
               CONTRIBUTION_HISTORY: "none", REPORT: "/r/advisor.md"}}}' \
    > "$vc" || die "could not write $vc"
  run "$(dirname "$SCRIPT")/templates" "$vc" "$oc"
  if [[ $RC -eq 0 ]] && grep -qx '## Acceptance Criteria' "$oc/brief-advisor.md" \
     && grep -qx 'CRITERION 1: the boundary is named' "$oc/brief-advisor.md" \
     && grep -qx 'CRITERION 2: each input is classified' "$oc/brief-advisor.md" \
     && grep -Fq 'ACCEPTANCE <k>/<N>: met' "$oc/brief-advisor.md"; then
    pass; else fail "report contracts: expected the specialist Acceptance Criteria section, got RC=$RC ERR=$ERRTEXT"; fi
  # The owner parser counts the rendered block, ignoring the injected input line.
  if [[ $RC -eq 0 ]] && criteria_count="$(PYTHONPATH="$(dirname "$SCRIPT")" python3 -c \
      'import sys; from foreman.report_contract import brief_criteria; print(brief_criteria(open(sys.argv[1]).read()))' \
      "$oc/brief-advisor.md")" && [[ "$criteria_count" == "2" ]]; then
    pass; else fail "report contracts: the rendered brief must count 2 criteria, got ${criteria_count:-none}"; fi
  local contract_role
  # #702: a packaged specialist must receive shell examples literally, not
  # replacement-pattern syntax. Include an internal/trailing newline too.
  local literal_input literal_values="$TMP/literal-values.json" literal_out="$TMP/literal-out"
  literal_input=$'cd /worktree && printf "quoted"; path \\one\\two; $VARIABLE\nsecond line\n'
  jq --arg text "$literal_input" \
    '.roles = {architect: .roles.advisor} | .roles.architect += {RESPONSIBILITY:"architect", INPUTS:$text, REPORT:"/r/literal-architect.md"}' \
    "$vc" > "$literal_values" || die "could not prepare literal specialist input"
  local compose_shell
  for compose_shell in bash /bin/bash; do
    COMPOSE_BASH="$compose_shell" run "$PKG" "$literal_values" "$literal_out"
    if [[ $RC -eq 0 ]] && LITERAL_INPUT="$literal_input" python3 -c \
      'import os, pathlib, sys; body=pathlib.Path(sys.argv[1]).read_text(); sys.exit(0 if os.environ["LITERAL_INPUT"] in body and "{{INPUTS}}" not in body else 1)' \
      "$literal_out/brief-architect.md"; then
      pass; else fail "$compose_shell literal specialist input must preserve ampersands, backslashes, quotes and newlines: RC=$RC ERR=$ERRTEXT"; fi
  done

  # Tool failures during rendering refuse atomically with an actionable error.
  local render_bin="$TMP/render-bin" render_failure
  mkdir -p "$render_bin" || die "could not create rendering-tool fixtures"
  local real_jq real_cat
  real_jq="$(command -v jq)" || die "could not resolve jq"
  real_cat="$(command -v cat)" || die "could not resolve cat"
  cat > "$render_bin/jq" <<'SH' || die "could not write jq fixture"
#!/usr/bin/env bash
set -euo pipefail
if [[ "$RENDER_FAILURE" == keys && "$*" == '-r keys[]' ]] \
   || [[ "$RENDER_FAILURE" == value && "$1" == -rj ]]; then
  echo 'fixture jq failure' >&2
  exit 2
fi
exec "$REAL_JQ" "$@"
SH
  cat > "$render_bin/cat" <<'SH' || die "could not write cat fixture"
#!/usr/bin/env bash
set -euo pipefail
if [[ "$RENDER_FAILURE" == template && "$1" == "$TARGET_TEMPLATE" ]]; then
  echo 'fixture template read failure' >&2
  exit 1
fi
exec "$REAL_CAT" "$@"
SH
  chmod +x "$render_bin/jq" "$render_bin/cat" || die "could not enable rendering fixtures"
  for render_failure in keys value template; do
    OUT="$(PATH="$render_bin:$PATH" REAL_JQ="$real_jq" REAL_CAT="$real_cat" \
      RENDER_FAILURE="$render_failure" TARGET_TEMPLATE="$TPL/COMMON.md" \
      bash "$SCRIPT" "$TPL" "$v1" "$TMP/render-failure-$render_failure" \
      2>"$TMP/render-failure.err")"; RC=$?
    if [[ $RC -eq 3 && -z "$OUT" && ! -e "$TMP/render-failure-$render_failure" ]] \
       && grep -Eq 'cannot (read template|enumerate template values)' "$TMP/render-failure.err"; then
      pass; else fail "render $render_failure failure must refuse without partial briefs: RC=$RC"; fi
  done

  for contract_role in reviewer tester; do
    if grep -Fq 'VERDICT: approved' "$o6b/brief-${contract_role}.md"; then
      pass; else fail "report contracts: shipped brief-${contract_role}.md must ask for its VERDICT line"; fi
  done

  echo "─────────────────────────────────────────────" >&2
  printf '{"suite":"test_compose_briefs.sh","passed":%d,"failed":%d}\n' "$PASS" "$FAIL"
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  run_suite "$@"
fi
