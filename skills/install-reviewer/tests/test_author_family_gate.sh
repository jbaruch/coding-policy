#!/usr/bin/env bash
# Outcome-based tests for author-family-gate.sh — the runner-level
# self-review-bias gate the paired reviewers consult BEFORE the agent
# activates (issue #161). Asserts the should_skip boolean and the
# decision label for each author-family signal:
#   1. the PR body `**Author-Model:**` line (preferred, wins), and
#   2. the `Co-authored-by:` trailer email domain fallback.
#
# The load-bearing invariant: should_skip is true ONLY on a confident
# same-family-only resolution (resolver decision "skip"). Every other
# input — cross-family, both-run, human-only, missing declaration, or no
# signal at all — must yield should_skip false so the agent still
# activates and owns the display-name / customized-email path.
#
# Run: bash skills/install-reviewer/tests/test_author_family_gate.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/author-family-gate.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: author-family-gate.sh not executable at $SCRIPT" >&2; exit 2; }

TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT

FAIL_COUNT=0
PASS_COUNT=0

# Extract a top-level JSON field (string or bare keyword) without jq.
field() {
  local json="$1" key="$2"
  if [[ "$json" =~ \"$key\":\"([^\"]*)\" ]]; then
    printf '%s' "${BASH_REMATCH[1]}"; return 0
  fi
  if [[ "$json" =~ \"$key\":(true|false|null) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"; return 0
  fi
  printf ''
}

check() {
  local name="$1" out="$2" exp_skip="$3" exp_decision="$4" ok=1 skip decision
  skip="$(field "$out" should_skip)"
  decision="$(field "$out" decision)"
  if [[ "$skip" != "$exp_skip" ]]; then
    echo "  FAIL: $name: should_skip expected '$exp_skip', got '$skip'" >&2
    echo "        out: $out" >&2; ok=0
  fi
  if [[ "$decision" != "$exp_decision" ]]; then
    echo "  FAIL: $name: decision expected '$exp_decision', got '$decision'" >&2
    echo "        out: $out" >&2; ok=0
  fi
  if [[ $ok -eq 1 ]]; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

# run_body <name> <reviewer> <exp_skip> <exp_decision> <body>
run_body() {
  local name="$1" reviewer="$2" exp_skip="$3" exp_decision="$4" body="$5" out
  out="$(printf '%s' "$body" | "$SCRIPT" --reviewer "$reviewer")" || {
    echo "  FAIL: $name: script exited non-zero" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1)); return
  }
  check "$name" "$out" "$exp_skip" "$exp_decision"
}

# run_trailer <name> <reviewer> <exp_skip> <exp_decision> <commits-content> [body]
# Writes the commit bodies to a temp file and runs with --commits-file.
# The body defaults to a no-declaration string so the trailer path fires.
run_trailer() {
  local name="$1" reviewer="$2" exp_skip="$3" exp_decision="$4" commits="$5"
  local body="${6:-no author-model declaration in this body}"
  local cf out
  cf="$(mktemp "$TMPDIR_TEST/commits.XXXXXX")"
  printf '%s' "$commits" > "$cf"
  out="$(printf '%s' "$body" | "$SCRIPT" --reviewer "$reviewer" --commits-file "$cf")" || {
    echo "  FAIL: $name: script exited non-zero" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1)); return
  }
  check "$name" "$out" "$exp_skip" "$exp_decision"
}

echo "author-family-gate.sh tests"

# ============ Signal 1: PR body **Author-Model:** line ====================

# --- Same-family body declaration → gate skips (the whole point) ---
run_body "claude body, anthropic reviewer -> SKIP" \
  anthropic true skip '**Author-Model:** claude-opus-4-8'
run_body "gpt body, openai reviewer -> SKIP" \
  openai true skip '**Author-Model:** gpt-5.4'
run_body "bare (unbolded) form still parses -> SKIP" \
  openai true skip 'Author-Model: gpt-5.4'
run_body "mixed human+claude, anthropic reviewer -> SKIP (claude-authored)" \
  anthropic true skip '**Author-Model:** human claude-opus-4-7'

# --- The #145 regression: newer-than-known claude id maps to anthropic ---
run_body "issue-145: claude-opus-4-8, openai reviewer -> NO skip (cross-family)" \
  openai false review '**Author-Model:** claude-opus-4-8'

# --- Cross-family / fallback body declarations → NO skip ---
run_body "gpt body, anthropic reviewer -> NO skip" \
  anthropic false review '**Author-Model:** gpt-5.4'
run_body "both paired families, anthropic reviewer -> NO skip (both-run)" \
  anthropic false review '**Author-Model:** gpt-5.4 claude-opus-4-7'
run_body "human-only, anthropic reviewer -> NO skip" \
  anthropic false review '**Author-Model:** human'
run_body "no Author-Model line -> NO skip (no-declaration)" \
  anthropic false no-declaration 'Just a PR body with no declaration.'

# ============ Signal 2: Co-authored-by trailer email =====================

CLAUDE_TRAILER='fix: something

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>'
CODEX_TRAILER='fix: something

Co-authored-by: Codex <noreply@openai.com>'
MIXED_TRAILER='fix: something

Co-Authored-By: Claude <noreply@anthropic.com>
Co-authored-by: Codex <noreply@openai.com>'
HUMAN_TRAILER='fix: something

Co-authored-by: Jane Dev <jane@users.noreply.github.com>'

# --- Same-family trailer → gate skips (catches "commits by claude") ---
run_trailer "claude trailer, anthropic reviewer -> SKIP" \
  anthropic true skip "$CLAUDE_TRAILER"
run_trailer "codex trailer, openai reviewer -> SKIP" \
  openai true skip "$CODEX_TRAILER"

# --- Cross-family trailer → NO skip ---
run_trailer "claude trailer, openai reviewer -> NO skip (cross-family)" \
  openai false review "$CLAUDE_TRAILER"
run_trailer "codex trailer, anthropic reviewer -> NO skip (cross-family)" \
  anthropic false review "$CODEX_TRAILER"

# --- Mixed AI trailers → both-run, neither skips ---
run_trailer "mixed claude+codex trailers, anthropic reviewer -> NO skip (both-run)" \
  anthropic false review "$MIXED_TRAILER"
run_trailer "mixed claude+codex trailers, openai reviewer -> NO skip (both-run)" \
  openai false review "$MIXED_TRAILER"

# --- Human-only trailer → unknown domain contributes no family ---
run_trailer "human-only trailer, anthropic reviewer -> NO skip (no-declaration)" \
  anthropic false no-declaration "$HUMAN_TRAILER"

# --- Final trailer line with NO trailing newline is still read ---
run_trailer "trailer as unterminated final line, anthropic reviewer -> SKIP" \
  anthropic true skip 'subject line'$'\n\n''Co-Authored-By: Claude <noreply@anthropic.com>'

# ============ Precedence: body line wins over trailer ====================

# gpt body (openai) + claude trailer: the openai reviewer skips on the BODY
# signal; the trailer is never consulted.
run_trailer "body wins over trailer, openai reviewer skips on body" \
  openai true skip "$CLAUDE_TRAILER" '**Author-Model:** gpt-5.4'

# Empty/whitespace body Author-Model line is a PRESENT (malformed) body
# declaration: it wins over the trailer, resolves to request_changes, and
# must NOT self-skip via a same-family trailer.
run_trailer "empty body line + same-family trailer -> NO skip (request_changes, body wins)" \
  anthropic false request_changes "$CLAUDE_TRAILER" '**Author-Model:**   '
run_body "empty body line, no trailer -> NO skip (request_changes)" \
  anthropic false request_changes '**Author-Model:**'
run_body "bare empty Author-Model line -> NO skip (request_changes)" \
  openai false request_changes 'Author-Model: '

# ============ File mode independence (issue #166) ========================

# tessl publish/install ships plugin files mode 0644 — no exec bit. The
# gate must still consult the resolver in that state instead of dying on
# an -x guard and fail-opening. Reproduce the installed layout: copies of
# both scripts with the exec bit stripped, gate invoked via `bash` (as the
# compiled workflow does). A "skip" decision proves the resolver ran —
# only the resolver produces it; the fail-open path yields no output at all.
MODE_DIR="$TMPDIR_TEST/mode644"
mkdir -p "$MODE_DIR"
cp "$SCRIPT" "$(dirname "$SCRIPT")/resolve-author-family.sh" "$MODE_DIR/"
chmod 644 "$MODE_DIR/author-family-gate.sh" "$MODE_DIR/resolve-author-family.sh"
if out="$(printf '%s' '**Author-Model:** claude-opus-4-8' \
    | bash "$MODE_DIR/author-family-gate.sh" --reviewer anthropic)"; then
  check "non-executable (0644) gate+resolver still resolve -> SKIP" \
    "$out" true skip
else
  echo "  FAIL: non-executable (0644) gate+resolver: gate exited non-zero" >&2
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

echo ""
echo "author-family-gate.sh: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
[[ $FAIL_COUNT -eq 0 ]] || exit 1
