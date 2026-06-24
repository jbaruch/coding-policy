#!/usr/bin/env bash
# Outcome-based tests for resolve-author-family.sh — the deterministic
# self-gate decision the paired policy reviewers delegate to. Asserts the
# decision field and (for non-review decisions) the verbatim review_body,
# since the reviewer LLM passes that body straight to
# submit_pull_request_review.
#
# Anchored by the issue #145 regression: a `claude-opus-4-8` author (newer
# than the reviewers' own model set) must resolve to the anthropic family,
# so the OpenAI reviewer RUNS (cross-family) instead of falsely self-skipping.
#
# Run: bash skills/install-reviewer/tests/test_resolve_author_family.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/resolve-author-family.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: resolve-author-family.sh not executable at $SCRIPT" >&2; exit 2; }

REF_REPO="rules/author-model-declaration.md"
REF_PLUGIN="jbaruch/coding-policy: author-model-declaration"

FAIL_COUNT=0
PASS_COUNT=0

# Extract a top-level string/keyword field from the script's JSON line
# without depending on jq (the reviewer sandbox may lack it, matching the
# preflight missing-jq guard).
field() {
  local json="$1" key="$2"
  # string values
  if [[ "$json" =~ \"$key\":\"([^\"]*)\" ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  # null / bare keyword
  if [[ "$json" =~ \"$key\":(null|true|false) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  printf ''
}

# run <name> <reviewer> <policy-ref> <expected-decision> <expected-body-or-NULL> [tokens...]
run() {
  local name="$1" reviewer="$2" ref="$3" exp_decision="$4" exp_body="$5"; shift 5
  local out decision body ok=1
  out="$("$SCRIPT" --reviewer "$reviewer" --policy-ref "$ref" "$@")" || {
    echo "  FAIL: $name: script exited non-zero" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1)); return
  }
  decision="$(field "$out" decision)"
  if [[ "$decision" != "$exp_decision" ]]; then
    echo "  FAIL: $name: decision expected '$exp_decision', got '$decision'" >&2
    echo "        out: $out" >&2
    ok=0
  fi
  if [[ "$exp_body" == "NULL" ]]; then
    body="$(field "$out" review_body)"
    if [[ "$body" != "null" ]]; then
      echo "  FAIL: $name: review_body expected null, got '$body'" >&2
      ok=0
    fi
  else
    body="$(field "$out" review_body)"
    if [[ "$body" != "$exp_body" ]]; then
      echo "  FAIL: $name: review_body mismatch" >&2
      echo "        expected: $exp_body" >&2
      echo "        actual:   $body" >&2
      ok=0
    fi
  fi
  if [[ $ok -eq 1 ]]; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

SKIP_OPENAI="Skipping: self-review-bias — author-family openai; see ${REF_REPO}."
SKIP_ANTHROPIC="Skipping: self-review-bias — author-family anthropic; see ${REF_REPO}."
MISSING_REPO="Missing Author-Model declaration — add **Author-Model:** to the PR body (or include a model-identifying Co-authored-by trailer). See ${REF_REPO}."
MISSING_PLUGIN="Missing Author-Model declaration — add **Author-Model:** to the PR body (or include a model-identifying Co-authored-by trailer). See ${REF_PLUGIN}."
SKIP_OPENAI_PLUGIN="Skipping: self-review-bias — author-family openai; see ${REF_PLUGIN}."

echo "resolve-author-family.sh tests"

# --- The #145 regression: newer-than-known claude id must map to anthropic ---
run "issue-145: claude-opus-4-8 -> openai reviewer RUNS (cross-family)" \
  openai "$REF_REPO" review NULL claude-opus-4-8
run "claude-opus-4-8 -> anthropic reviewer SKIPS (self)" \
  anthropic "$REF_REPO" skip "$SKIP_ANTHROPIC" claude-opus-4-8

# --- Plain same-family skips ---
run "gpt-5.4 -> openai reviewer SKIPS (self)" \
  openai "$REF_REPO" skip "$SKIP_OPENAI" gpt-5.4
run "codex-mini -> openai reviewer SKIPS (self)" \
  openai "$REF_REPO" skip "$SKIP_OPENAI" codex-mini

# --- Plain cross-family runs ---
run "gpt-5.4 -> anthropic reviewer RUNS (cross-family)" \
  anthropic "$REF_REPO" review NULL gpt-5.4
run "gemini-2.5 -> openai reviewer RUNS (cross-family)" \
  openai "$REF_REPO" review NULL gemini-2.5

# --- Mixed / fallback cases ---
run "human + claude -> openai reviewer RUNS (cross-family)" \
  openai "$REF_REPO" review NULL human claude-opus-4-7
run "both paired families -> openai reviewer RUNS (degraded both-run)" \
  openai "$REF_REPO" review NULL gpt-5.4 claude-opus-4-7
run "human-only -> openai reviewer RUNS (neither paired family)" \
  openai "$REF_REPO" review NULL human
run "ad-hoc model -> openai reviewer RUNS (unknown != self)" \
  openai "$REF_REPO" review NULL mistral-large

# --- Missing declaration ---
run "no tokens -> REQUEST_CHANGES (in-repo citation)" \
  openai "$REF_REPO" request_changes "$MISSING_REPO"
run "no tokens -> REQUEST_CHANGES (plugin citation)" \
  anthropic "$REF_PLUGIN" request_changes "$MISSING_PLUGIN"

# --- Citation passthrough on skip (consumer template form) ---
run "gpt-5.4 -> openai SKIP carries plugin citation" \
  openai "$REF_PLUGIN" skip "$SKIP_OPENAI_PLUGIN" gpt-5.4

echo ""
echo "resolve-author-family.sh: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
[[ $FAIL_COUNT -eq 0 ]] || exit 1
