#!/usr/bin/env bash
# Tests for the review paths in .github/workflows/fleet-review.yml (issue #841):
# the cron poll dispatches a single-PR review per PR instead of reviewing
# inline, and a poll dispatch lands in the same per-PR, per-lane concurrency
# group as a consumer's push dispatch. The poll step's `run:` block is executed
# with fleet-poll.sh, gh and both review drivers faked, so the assertions are on
# the dispatches gh receives. No network. Deterministic, hermetic
# (rules/testing-standards.md).
#
# Run: bash .github/codex-review/tests/test_fleet_review_workflow.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORKFLOW="${HERE}/../../workflows/fleet-review.yml"
TRIGGER="${HERE}/../../../skills/onboard-repo/templates/review-trigger.yml.md"
for f in "$WORKFLOW" "$TRIGGER"; do
  [[ -f "$f" && -r "$f" ]] || { echo "fatal: $f not readable" >&2; exit 2; }
done

# The opening of a GitHub Actions expression.
OPEN=$'\x24{{'
POLL_STEP="Dispatch a review of each PR that needs one"
WORKLIST='[{"owner":"jbaruch","repo":"alpha","number":7,"base_ref":"main","head_sha":"aaa"},{"owner":"jbaruch","repo":"beta","number":12,"base_ref":"release/1.x","head_sha":"bbb"}]'

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }

rmwarn() { rm -rf "$@" || echo "test_fleet_review_workflow: warning: could not remove ${*}" >&2; }

# extract_run <step-name>: print the named step's `run: |` block, dedented.
extract_run() {
  awk -v name="$1" '
    function indent(s) { match(s, /^ */); return RLENGTH }
    state == 0 && $0 ~ "^ *- name: " name " *$" { state = 1; next }
    state == 1 && $0 ~ /^ *- name: / { exit }
    state == 1 && $0 ~ /^ *run: \|/ { state = 2; base = indent($0); body = -1; next }
    state == 2 {
      if ($0 ~ /^ *$/) { print ""; next }
      if (indent($0) <= base) { exit }
      if (body < 0) { body = indent($0) }
      print substr($0, body + 1)
    }
  ' "$WORKFLOW"
}

# job_block <job>: print the lines of jobs.<job>.
job_block() {
  awk -v job="$1" '
    $0 ~ "^  " job ":$" { on = 1; next }
    on && /^  [A-Za-z0-9_-]+:$/ { exit }
    on { print }
  ' "$WORKFLOW"
}

# job_key <job> <key>: print a job-level scalar (if) or a concurrency field.
job_key() {
  job_block "$1" | awk -v key="$2" '
    key == "if" && /^    if: / { sub(/^    if: /, ""); print; exit }
    /^    concurrency:$/ { c = 1; next }
    c && /^    [^ ]/ { c = 0 }
    c && $0 ~ "^      " key ": " { sub("^      " key ": ", ""); print; exit }
  '
}

# render <template> <lane> <k=v>...: substitute dispatch inputs and the matrix
# lane into a concurrency group. An unset `reviewer` input takes its default.
render() {
  local out="$1" lane="$2" kv k v; shift 2
  out=${out//"${OPEN} matrix.lane }}"/$lane}
  for k in repo pr base reviewer; do
    v=""; [[ "$k" == reviewer ]] && v=all
    for kv in "$@"; do [[ "${kv%%=*}" == "$k" ]] && v=${kv#*=}; done
    out=${out//"${OPEN} github.event.inputs.${k} }}"/$v}
  done
  printf '%s\n' "$out"
}

# matrix_lanes <job> <reviewer>: the job's matrix lanes for a manual run with
# that `reviewer` input ("" for a scheduled run, which has no inputs), printed
# as a JSON array. Evaluates the GitHub expression with Python's and/or, which
# return an operand the way the expression's && and || do.
matrix_lanes() {
  local expr
  expr=$(job_block "$1" | sed -n 's/^        lane: //p')
  python3 -I - "$expr" "$2" <<'PY'
import json, sys
expr, reviewer = sys.argv[1:]
expr = expr.removeprefix("${{").removesuffix("}}").replace("github.event.inputs.reviewer", repr(reviewer)).replace("&&", " and ").replace("||", " or ")
print(json.dumps(eval(expr, {"__builtins__": {}, "fromJSON": json.loads}), separators=(",", ":")))
PY
}

# run_poll <worklist> <gh-fail-repo>: run the poll step in a fixture tree.
# Sets RC, OUT, GH_LINES and DRIVER_CALLS.
run_poll() {
  local script work
  script=$(extract_run "$POLL_STEP")
  [[ -n "$script" ]] || { echo "fatal: step '$POLL_STEP' has no run block in $WORKFLOW" >&2; exit 2; }
  work=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; exit 2; }
  if ! (
    set -e
    mkdir -p "$work/bin" "$work/tree/.github/codex-review" "$work/tree/.github/claude-review"
    cat > "$work/tree/.github/codex-review/fleet-poll.sh" <<'EOF'
printf '%s\n' "$FAKE_WORKLIST"
EOF
    cat > "$work/tree/.github/codex-review/fleet-review-one.sh" <<'EOF'
echo codex "$@" >> "$DRIVER_LOG"
EOF
    printf 'import os, sys\nopen(os.environ["DRIVER_LOG"], "a").write("claude %%s\\n" %% sys.argv[1:])\n' > "$work/tree/.github/claude-review/review.py"
    cat > "$work/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "${GH_TOKEN} $*" >> "$GH_LOG"
[[ -z "$GH_FAIL_REPO" || " $* " != *" repo=${GH_FAIL_REPO} "* ]] || { echo "HTTP 500" >&2; exit 1; }
EOF
    chmod +x "$work/bin/gh"
    : > "$work/gh.log"; : > "$work/driver.log"
  ); then
    echo "fatal: building the poll fixture in $work failed" >&2; rmwarn "$work"; exit 2
  fi
  OUT=$(cd "$work/tree" && FAKE_WORKLIST="$1" GH_FAIL_REPO="$2" \
    GH_LOG="$work/gh.log" DRIVER_LOG="$work/driver.log" PATH="$work/bin:$PATH" \
    GH_TOKEN=app-token-fixture DISPATCH_TOKEN=dispatch-token-fixture \
    REVIEWER_LOGIN='fixture-app[bot]' REVIEW_LANE=policy GITHUB_REPOSITORY=jbaruch/coding-policy \
    bash -c "$script" 2>&1)
  RC=$?
  GH_LINES=$(cat "$work/gh.log")
  DRIVER_CALLS=$(cat "$work/driver.log")
  rmwarn "$work"
}

DISPATCH_ALPHA='dispatch-token-fixture workflow run fleet-review.yml --repo jbaruch/coding-policy -f repo=alpha -f pr=7 -f base=main -f reviewer=policy'
DISPATCH_BETA='dispatch-token-fixture workflow run fleet-review.yml --repo jbaruch/coding-policy -f repo=beta -f pr=12 -f base=release/1.x -f reviewer=policy'

t_poll_dispatches_instead_of_reviewing() {
  run_poll "$WORKLIST" ""
  [[ "$RC" == 0 ]] || { bad "poll_dispatches: step exits 0, got $RC: $OUT"; return; }
  [[ "$GH_LINES" == "$DISPATCH_ALPHA"$'\n'"$DISPATCH_BETA" ]] \
    || { bad "poll_dispatches: one single-PR dispatch per worklist item with GITHUB_TOKEN, got: $GH_LINES"; return; }
  [[ -z "$DRIVER_CALLS" ]] || { bad "poll_dispatches: the poll runs no review driver, got: $DRIVER_CALLS"; return; }
  local job
  while IFS= read -r job; do
    [[ "$job" == review ]] && continue
    if job_block "$job" | grep -Eq 'fleet-review-one\.sh|claude-review/review\.py'; then
      bad "poll_dispatches: only the review job runs a review driver, job '$job' does too"; return
    fi
  done < <(awk '/^jobs:$/ { j = 1; next } j && /^  [A-Za-z0-9_-]+:$/ { sub(/:$/, ""); print $1 }' "$WORKFLOW")
  [[ "$(job_key review if)" == *"&& github.event.inputs.repo" ]] \
    || { bad "poll_dispatches: the review job runs only for a single-PR dispatch, got if: $(job_key review if)"; return; }
  [[ "$(job_key poll if)" == *"!github.event.inputs.repo" ]] \
    || { bad "poll_dispatches: the poll job never runs for a single-PR dispatch, got if: $(job_key poll if)"; return; }
  ok "poll dispatches each PR through workflow_dispatch and reviews nothing inline"
}

t_poll_failed_dispatch_fails_after_the_rest() {
  run_poll "$WORKLIST" alpha
  [[ "$RC" != 0 ]] || { bad "poll_failed_dispatch: a failed dispatch fails the step"; return; }
  [[ "$OUT" == *"could not dispatch a policy review of alpha#7"* ]] \
    || { bad "poll_failed_dispatch: the warning names the PR, got: $OUT"; return; }
  [[ "$GH_LINES" == *"$DISPATCH_BETA"* ]] || { bad "poll_failed_dispatch: later PRs are still dispatched, got: $GH_LINES"; return; }
  ok "a failed dispatch is reported and the remaining PRs are still dispatched"
}

t_poll_empty_worklist_dispatches_nothing() {
  run_poll '[]' ""
  [[ "$RC" == 0 && -z "$GH_LINES" ]] || { bad "poll_empty: exit 0 with no dispatch, got rc=$RC gh=$GH_LINES"; return; }
  ok "an empty worklist dispatches nothing"
}

t_poll_malformed_worklist_fails() {
  run_poll '{"repo":"alpha"}' ""
  [[ "$RC" != 0 && -z "$GH_LINES" && "$OUT" == *"fleet-poll.sh printed no JSON array"* ]] \
    || { bad "poll_malformed: non-zero, no dispatch, actionable error, got rc=$RC gh=$GH_LINES out=$OUT"; return; }
  ok "a malformed worklist fails loudly without dispatching"
}

t_push_and_poll_share_the_per_pr_group() {
  local group push_inputs=() key poll_policy push_policy push_quality other
  group=$(job_key review group)
  while IFS= read -r key; do
    case "$key" in repo) push_inputs+=(repo=alpha) ;; pr) push_inputs+=(pr=7) ;; base) push_inputs+=(base=main) ;;
      *) bad "share_group: unexpected trigger input '$key' in $TRIGGER"; return ;; esac
  done < <(grep -o -- '-f [a-z]*=' "$TRIGGER" | sed 's/^-f //; s/=$//')
  [[ ${#push_inputs[@]} == 3 ]] || { bad "share_group: the push trigger sends repo, pr and base, got: ${push_inputs[*]}"; return; }
  run_poll "$WORKLIST" ""
  local -a poll_inputs
  read -r -a poll_inputs <<<"$(head -n 1 <<<"$GH_LINES" | grep -o 'repo=[^ ]*\|pr=[^ ]*\|base=[^ ]*\|reviewer=[^ ]*' | tr '\n' ' ')"
  poll_policy=$(render "$group" policy "${poll_inputs[@]}")
  push_policy=$(render "$group" policy "${push_inputs[@]}")
  push_quality=$(render "$group" quality "${push_inputs[@]}")
  other=$(render "$group" policy repo=beta pr=12 base=release/1.x reviewer=policy)
  [[ "$poll_policy" != *"$OPEN"* ]] || { bad "share_group: the group uses only dispatch inputs and the lane, got $group"; return; }
  [[ "$poll_policy" == "$push_policy" ]] \
    || { bad "share_group: a poll dispatch and a push dispatch of one PR's lane share a group, got '$poll_policy' vs '$push_policy'"; return; }
  [[ "$push_policy" != "$push_quality" && "$push_policy" != "$other" ]] \
    || { bad "share_group: lanes and PRs get distinct groups, got '$push_policy' '$push_quality' '$other'"; return; }
  [[ "$(job_key review cancel-in-progress)" == false ]] \
    || { bad "share_group: a queued review never cancels a running one"; return; }
  ok "push and poll dispatches of one PR's lane share the review concurrency group"
}

t_polls_never_overlap() {
  local group
  group=$(job_key poll group)
  [[ -n "$group" && "$(render "$group" policy)" == "$(render "$group" policy repo=x pr=1)" && "$(render "$group" policy)" != *"$OPEN"* ]] \
    || { bad "polls_never_overlap: every poll of a lane takes one fixed group, got '$group'"; return; }
  [[ "$(job_key poll cancel-in-progress)" == false ]] \
    || { bad "polls_never_overlap: a new poll queues instead of cancelling the running one"; return; }
  ok "polls of one lane share one concurrency group and never overlap"
}

# The poll's App token only discovers PRs; a write permission would let it post.
t_poll_app_token_is_read_only() {
  local perms
  perms=$(job_block poll | sed -n 's/^ *\(permission-[a-z-]*: .*\)$/\1/p' | sort | tr '\n' ' ')
  [[ "$perms" == "permission-contents: read permission-pull-requests: read " ]] \
    || { bad "poll_token_read_only: the poll mints its App token with contents and pull-requests read only, got '$perms'"; return; }
  ok "the poll's App token is read-only"
}

t_manual_run_starts_only_the_selected_lane() {
  local job reviewer want got
  for job in poll review; do
    for reviewer in policy quality all ""; do
      case "$reviewer" in policy|quality) want="[\"$reviewer\"]" ;; *) want='["policy","quality"]' ;; esac
      got=$(matrix_lanes "$job" "$reviewer")
      [[ "$got" == "$want" ]] || { bad "selected_lane: ${job} job with reviewer '${reviewer}' runs ${want}, got '${got}'"; return; }
    done
  done
  ok "a manual run starts only the selected lane's poll and review; all and the schedule start both"
}

for t in t_poll_dispatches_instead_of_reviewing t_poll_failed_dispatch_fails_after_the_rest \
  t_poll_empty_worklist_dispatches_nothing t_poll_malformed_worklist_fails \
  t_push_and_poll_share_the_per_pr_group t_polls_never_overlap t_poll_app_token_is_read_only \
  t_manual_run_starts_only_the_selected_lane; do
  "$t"
done

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" == 0 ]]
