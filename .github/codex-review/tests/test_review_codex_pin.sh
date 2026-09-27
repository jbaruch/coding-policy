#!/usr/bin/env bash
# Tests for the self-review call in .github/workflows/review-codex.yml: the
# "Run Codex policy review" step must run codex on the pinned model at the
# pinned effort. The step's `run:` block is extracted from the workflow and
# executed with git and codex faked on PATH, so the assertion is on the argv
# codex actually receives — no network, no real Codex. Deterministic, hermetic
# (rules/testing-standards.md).
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
  script=$(extract_run "$WORKFLOW" "$STEP_NAME")
  [[ "$script" == *"codex exec"* ]] || { bad "self_review_pin: step '$STEP_NAME' with a codex exec run block found in $WORKFLOW"; return; }

  local bin work
  bin=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; exit 2; }
  work=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; exit 2; }
  # A `set -e` subshell so any failed fixture write aborts the run
  # (rules/error-handling.md aggregate carve-out, setup-step check).
  if ! (
    set -e
    printf '#!/usr/bin/env bash\nexit 0\n' > "$bin/git"
    cat > "$bin/codex" <<'EOF'
#!/usr/bin/env bash
# Record argv one arg per line, then drain the prompt on stdin.
printf '%s\n' "$@" > "$CODEX_ARGV_LOG"
cat > /dev/null
exit 0
EOF
    chmod +x "$bin/git" "$bin/codex"
    mkdir -p "$work/.github/codex-review"
    echo 'prompt' > "$work/.github/codex-review/prompt.md"
    # GitHub expressions are substituted before the shell sees the block. sed,
    # not ${script//...}: a glob `*` is greedy and would swallow the text
    # between two expressions on one line.
    # shellcheck disable=SC2001
    sed 's/\${{[^}]*}}/main/g' <<< "$script" > "$work/step.sh"
  ); then
    echo "fatal: fixture setup failed" >&2; rmwarn "$bin" "$work"; exit 2
  fi

  local log="$work/argv.log" rc=0 model_ok=1 effort_ok=1 out
  out=$(cd "$work" && PATH="$bin:$PATH" CODEX_ARGV_LOG="$log" bash step.sh 2>&1) || rc=$?
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

# --- a missing step fails loudly rather than passing vacuously ---
t_missing_step() {
  local script
  script=$(extract_run "$WORKFLOW" "No such step")
  if [[ -z "$script" ]]; then ok "missing step extracts nothing"; else bad "missing_step: expected empty extraction"; fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "== review-codex.yml model pin tests =="
  t_self_review_pin
  t_missing_step
  echo "== summary: ${pass} passed, ${fail} failed =="
  [[ "$fail" -eq 0 ]]
fi
