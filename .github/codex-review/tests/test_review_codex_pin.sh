#!/usr/bin/env bash
# Tests for the self-review call in .github/workflows/review-codex.yml: the
# "Run Codex policy review" step must run codex on the pinned model at the
# pinned effort. The step's `run:` block is extracted from the workflow and
# executed with git and codex faked on PATH, so the assertion is on the argv
# codex actually receives — no network, no real Codex. Deterministic, hermetic
# (rules/testing-standards.md).
# The "Read bounded prior feedback" step must fail the job when GitHub history
# cannot be read or the last policy App review ends with a malformed
# blocker marker, so the review posts no verdict.
#
# Run: bash .github/codex-review/tests/test_review_codex_pin.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

WORKFLOW="$(cd "$(dirname "$0")/../../workflows" && pwd)/review-codex.yml"
[[ -f "$WORKFLOW" && -r "$WORKFLOW" ]] || { echo "fatal: review-codex.yml not readable at $WORKFLOW" >&2; exit 2; }

STEP_NAME="Run Codex policy review"

# The reviewer's model pin. Renewing the pin in review-codex.yml means
# renewing it here too — a dropped or misspelled flag must fail this suite.
PIN_MODEL="gpt-5.6-sol"
PIN_EFFORT='model_reasoning_effort="high"'

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }

rmwarn() { rm -rf "$@" || echo "test_review_codex_pin: warning: could not remove ${*}" >&2; }

# extract_run <workflow> <step-name>: print the named step's literal `run: |`
# block with its indentation removed. Prints nothing when the step or its run
# block is absent.
extract_run() {
  awk -v name="$2" '
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
  ' "$1"
}

# has_pair <argv-log> <flag> <value>: true when <flag> is immediately followed
# by <value> in the recorded argv (one arg per line).
has_pair() {
  local log="$1" flag="$2" value="$3" prev="" arg
  while IFS= read -r arg; do
    [[ "$prev" == "$flag" && "$arg" == "$value" ]] && return 0
    prev="$arg"
  done < "$log"
  return 1
}

# --- the self-review step runs codex on the pinned model at the pinned effort ---
t_self_review_pin() {
  local script
  if ! script=$(extract_run "$WORKFLOW" "$STEP_NAME"); then
    bad "self_review_pin: extracting '$STEP_NAME' from $WORKFLOW failed"; return
  fi
  [[ "$script" == *"codex exec"* ]] || { bad "self_review_pin: step '$STEP_NAME' with a codex exec run block found in $WORKFLOW"; return; }

  local bin work
  bin=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; exit 2; }
  work=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; rmwarn "$bin"; exit 2; }
  # A `set -e` subshell so any failed fixture write aborts the run
  # (rules/error-handling.md aggregate carve-out, setup-step check).
  if ! (
    set -e
    printf '#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n' > "$bin/git"
    cat > "$bin/codex" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# Record argv one arg per line, then drain the prompt on stdin.
printf '%s\n' "$@" > "$CODEX_ARGV_LOG"
cat > /dev/null
exit 0
EOF
    chmod +x "$bin/git" "$bin/codex"
    mkdir -p "$work/.github/codex-review"
    cp "$(dirname "$WORKFLOW")/../reviewer_failure.py" "$work/.github/reviewer_failure.py"
    echo 'prompt' > "$work/.github/codex-review/prompt.md"
    printf '{"last_review":null,"items":[]}\n' > "$work/codex-prior-feedback.json"
    printf 'clean\n' > "$work/codex-whitespace-status.txt"
    # GitHub expressions are substituted before the shell sees the block. sed,
    # not ${script//...}: a glob `*` is greedy and would swallow the text
    # between two expressions on one line.
    # shellcheck disable=SC2001
    sed 's/\${{[^}]*}}/main/g' <<< "$script" > "$work/step.sh"
  ); then
    echo "fatal: fixture setup failed" >&2; rmwarn "$bin" "$work"; exit 2
  fi

  local log="$work/argv.log" rc=0 model_ok=1 effort_ok=1 out
  out=$(cd "$work" && PATH="$bin:$PATH" RUNNER_TEMP="$work" CODEX_ARGV_LOG="$log" bash step.sh 2>&1) || rc=$?
  if [[ -f "$log" ]]; then
    has_pair "$log" --model "$PIN_MODEL" && model_ok=0
    has_pair "$log" -c "$PIN_EFFORT" && effort_ok=0
  fi
  rmwarn "$bin" "$work"
  [[ $rc -eq 0 ]]        || { bad "self_review_pin: step exits 0 (rc=$rc, out=$out)"; return; }
  [[ $model_ok -eq 0 ]]  || { bad "self_review_pin: codex argv carries --model $PIN_MODEL"; return; }
  [[ $effort_ok -eq 0 ]] || { bad "self_review_pin: codex argv carries -c $PIN_EFFORT"; return; }
  ok "self-review runs codex with --model $PIN_MODEL and -c $PIN_EFFORT"
}

# --- a failed history read or a malformed marker fails its step; no later step
# runs after a failure ---
t_failed_history_fails_job() {
  local script
  script=$(extract_run "$WORKFLOW" "Read bounded prior feedback")
  [[ "$script" == *prior-feedback.py* ]] || { bad "failed_history: history step found in $WORKFLOW"; return; }
  local failing='exit 1' malformed
  malformed=$(cat <<'SH'
case "$*" in
  *graphql*) cat <<'JSON' ;;
{"data":{"node":{"body":"x\n\n<!-- coding-policy-blockers {} -->","author":{"login":"coding-policy-fleet-reviewer"},"commit":{"oid":"1111111111111111111111111111111111111111"},"lastEditedAt":null,"editor":null}}}
JSON
  */reviews*) cat <<'JSON' ;;
[[{"user":{"login":"coding-policy-fleet-reviewer[bot]"},"body":"x\n\n<!-- coding-policy-blockers {} -->",
   "created_at":"2026-11-17T00:00:00Z","commit_id":"1111111111111111111111111111111111111111","node_id":"PRR_1",
   "html_url":"https://github.com/owner/repo/pull/1#pullrequestreview-1"}]]
JSON
  *) echo '[[]]' ;;
esac
SH
)
  local stub expect bin work
  for stub in "$failing" "$malformed"; do
    bin=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; exit 2; }
    work=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; rmwarn "$bin"; exit 2; }
    if ! (
      set -e
      printf '#!/usr/bin/env bash\necho 1111111111111111111111111111111111111111\n' > "$bin/git"
      printf '#!/usr/bin/env bash\n%s\n' "$stub" > "$bin/gh"
      chmod +x "$bin/git" "$bin/gh"
      mkdir -p "$work/.github/codex-review"
      cp "$(dirname "$WORKFLOW")/../codex-review/prior-feedback.py" "$work/.github/codex-review/"
      printf '%s\n' "$script" > "$work/step.sh"
    ); then
      echo "fatal: fixture setup failed" >&2; rmwarn "$bin" "$work"; exit 2
    fi
    expect="history unavailable"
    [[ "$stub" == "$malformed" ]] && expect="pullrequestreview-1 ends with a malformed blocker marker"
    local rc=0 out
    out=$(cd "$work" && PATH="$bin:$PATH" RUNNER_TEMP="$work" GITHUB_REPOSITORY=owner/repo PR_NUMBER=1 REVIEWER_LOGIN='coding-policy-fleet-reviewer[bot]' bash step.sh 2>&1) || rc=$?
    rmwarn "$bin" "$work"
    [[ $rc -ne 0 && "$out" == *"$expect"* ]] || { bad "failed_history: step fails with '$expect' (rc=$rc, out=$out)"; return; }
  done
  ! grep -qE 'always\(\)|failure\(\)|continue-on-error' "$WORKFLOW" \
    || { bad "failed_history: a step in $WORKFLOW can run after a failure, so the review could still post"; return; }
  ok "failed history read or malformed marker fails the job before the review posts"
}

# Execute the workflow's literal command against both PR-head reader contracts.
t_history_reader_compatibility() {
  local script login work revision rc out
  script=$(extract_run "$WORKFLOW" "Read bounded prior feedback")
  [[ "$script" == *prior-feedback.py* ]] || { bad "compatibility: history step missing"; return; }
  login=$(awk '
    /- name: Read bounded prior feedback/ { step = 1; next }
    step && /REVIEWER_LOGIN:/ { print $2; exit }
    step && /run:/ { exit }
  ' "$WORKFLOW")
  [[ "$login" == 'coding-policy-fleet-reviewer[bot]' ]] || { bad "compatibility: workflow must set REVIEWER_LOGIN"; return; }
  work=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; exit 2; }
  if ! (
    set -e
    mkdir -p "$work/bin" "$work/.github/codex-review"
    printf '#!/usr/bin/env bash\nset -euo pipefail\necho 1111111111111111111111111111111111111111\n' > "$work/bin/git"
    printf '#!/usr/bin/env bash\nset -euo pipefail\necho "[[]]"\n' > "$work/bin/gh"
    chmod +x "$work/bin/git" "$work/bin/gh"
    printf '%s\n' "$script" > "$work/step.sh"
    git -C "$(dirname "$WORKFLOW")/../.." show \
      9d0ecd6a50ecb55fb2863a5cfa87e2156a10b42a:.github/codex-review/prior-feedback.py > "$work/old.py"
    cp "$(dirname "$WORKFLOW")/../codex-review/prior-feedback.py" "$work/current.py"
  ); then
    echo "fatal: compatibility fixture setup failed" >&2; rmwarn "$work"; exit 2
  fi
  for revision in old current; do
    if ! cp "$work/$revision.py" "$work/.github/codex-review/prior-feedback.py"; then
      echo "fatal: could not install $revision reader fixture" >&2; rmwarn "$work"; exit 2
    fi
    rc=0
    out=$(cd "$work" && PATH="$work/bin:$PATH" RUNNER_TEMP="$work" GITHUB_REPOSITORY=owner/repo PR_NUMBER=1 REVIEWER_LOGIN="$login" bash step.sh 2>&1) || rc=$?
    if [[ $rc -ne 0 || -n "$out" ]] || ! jq -e '
      .head_sha == "1111111111111111111111111111111111111111"
      and .authority == "historical_feedback_only" and .items == [] and .omitted == 0
    ' "$work/codex-prior-feedback.json" >/dev/null; then
      bad "compatibility: workflow with $revision reader (rc=$rc, out=$out)"; rmwarn "$work"; return
    fi
    ok "workflow history command succeeds with $revision reader"
  done
  rmwarn "$work"
}

# --- a missing step fails loudly rather than passing vacuously ---
t_missing_step() {
  local script
  if ! script=$(extract_run "$WORKFLOW" "No such step"); then
    bad "missing_step: extracting from $WORKFLOW failed, so an empty result proves nothing"; return
  fi
  if [[ -z "$script" ]]; then ok "missing step extracts nothing"; else bad "missing_step: expected empty extraction"; fi
}

t_scoped_app_posting() {
  if python3 - "$WORKFLOW" <<'PY'
import sys
from pathlib import Path

workflow = Path(sys.argv[1]).read_text()
mint = workflow.split("      - name: Mint the policy reviewer App installation token\n")[1].split("      - name:")[0]
post = workflow.split("      - name: Post the review\n")[1]
history = workflow.split("      - name: Read bounded prior feedback\n")[1].split("      - uses:")[0]
assert "uses: actions/create-github-app-token@v3" in mint
for setting in (
    "app-id: ${{ secrets.FLEET_APP_ID }}",
    "private-key: ${{ secrets.FLEET_APP_PRIVATE_KEY }}",
    "owner: ${{ github.repository_owner }}",
    "repositories: ${{ github.event.repository.name }}",
    "permission-pull-requests: write",
):
    assert setting in mint, setting
assert "GH_TOKEN: ${{ steps.apptoken.outputs.token }}" in post
assert "REVIEWER_LOGIN: ${{ steps.apptoken.outputs.app-slug }}[bot]" in post
assert "REVIEWER_LOGIN: coding-policy-fleet-reviewer[bot]" in history
assert "GH_TOKEN: ${{ github.token }}" in history
assert '"$REVIEWED_SHA"' in post
assert "persist-credentials: false" in workflow
assert "pull-requests: write" not in workflow.split("jobs:")[0]
assert workflow.index("Guard against credential leakage") < workflow.index("Mint the policy reviewer App")
PY
  then
    ok "self-review posts through the repo-scoped fleet App with matching history identity"
  else
    bad "self-review App credential, identity, scope or head binding is wrong"
  fi
}

# --- focused regression: post job source ref and sparse-checkout dependency ---
# Verifies that the post job loads executable scripts from the repository's
# protected default branch (not the PR head) and that every script sourced at
# runtime is included in the sparse-checkout.  Inert: reads only YAML, no
# network or filesystem side effects.
t_post_checkout_trust() {
  # Source-ref check: post job checkout must use the default branch, not the
  # PR head SHA, so a PR contributor cannot substitute their own scripts.
  if python3 - "$WORKFLOW" <<'PY'
import sys, re
from pathlib import Path

workflow = Path(sys.argv[1]).read_text()
post_section = workflow.split("  post:\n")[1]
checkout_block = post_section.split("      - uses: actions/checkout@v7\n")[1].split("      - ")[0]
# Strip comment lines before checking the ref: value so a comment explaining
# the old head.sha behaviour does not trigger a false failure.
active_lines = [l for l in checkout_block.splitlines() if not re.match(r'\s*#', l)]
active_text = "\n".join(active_lines)
assert "pull_request.head.sha" not in active_text, \
    "post checkout ref: line must not use PR head SHA"
assert "repository.default_branch" in active_text, \
    "post checkout ref: line must use repository default_branch"
# Workflow token stays read-only; only the App token posts.
assert "pull-requests: write" not in workflow.split("  post:")[1].split("    steps:")[0], \
    "post job workflow token must not have pull-requests: write"
PY
  then
    ok "post job checkout uses the default branch, not the PR head (source-ref check)"
  else
    bad "post job checkout ref must be github.event.repository.default_branch, not head.sha (source-ref check)"
  fi

  # Cone-mode check: individual file paths require cone-mode disabled; cone mode
  # only accepts directories and silently excludes individual file paths.
  if python3 - "$WORKFLOW" <<'PY'
import sys
from pathlib import Path

workflow = Path(sys.argv[1]).read_text()
post_section = workflow.split("  post:\n")[1]
checkout_block = post_section.split("      - uses: actions/checkout@v7\n")[1].split("      - ")[0]
assert "sparse-checkout-cone-mode: false" in checkout_block, \
    "sparse-checkout-cone-mode: false required for individual file paths"
PY
  then
    ok "post job checkout has sparse-checkout-cone-mode: false (individual file paths require it)"
  else
    bad "post job checkout missing sparse-checkout-cone-mode: false — cone mode rejects individual file paths"
  fi

  # Dependency availability: every script sourced at runtime must be listed in
  # the sparse-checkout, or the post job fails before it can post anything.
  if python3 - "$WORKFLOW" <<'PY'
import sys
from pathlib import Path

workflow = Path(sys.argv[1]).read_text()
post_section = workflow.split("  post:\n")[1]
checkout_block = post_section.split("      - uses: actions/checkout@v7\n")[1].split("      - ")[0]
assert ".github/codex-review/post-review.sh" in checkout_block, "post-review.sh missing"
assert "skills/release/poll-pr-reviews.sh" in checkout_block, "poll-pr-reviews.sh missing"
assert "skills/release/review-lanes.sh" in checkout_block, \
    "review-lanes.sh missing (sourced by poll-pr-reviews.sh)"
PY
  then
    ok "post job sparse-checkout includes post-review.sh and all sourced helpers (dependency check)"
  else
    bad "post job sparse-checkout missing review-lanes.sh or another required helper (dependency check)"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "== review-codex.yml model pin tests =="
  t_scoped_app_posting
  t_self_review_pin
  t_failed_history_fails_job
  t_history_reader_compatibility
  t_missing_step
  t_post_checkout_trust
  echo "== summary: ${pass} passed, ${fail} failed =="
  [[ "$fail" -eq 0 ]]
fi
