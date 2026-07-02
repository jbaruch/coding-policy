#!/usr/bin/env bash
# Runner-level self-review-bias gate for the paired policy reviewers.
#
# Each gh-aw policy reviewer (review-anthropic / review-openai) used to
# decide self-review-bias INSIDE the agent: it activated, loaded every
# rule file + the PR diff, then ran resolve-author-family.sh, posted a
# `Skipping: self-review-bias` COMMENT, and exited — burning ~400K tokens
# for zero review value. Because this repo's PRs are predominantly
# Claude-authored, the anthropic reviewer self-skipped on nearly every PR.
#
# This script moves the skip decision out of the agent. A cheap gate job
# runs it and skips the `agent` job — where the ~400K-token spend lives —
# when the author-family matches the reviewer's own family, so the agent
# never spins up. (gh-aw composes the gate onto `agent`, so the cheap
# pre_activation/activation framework setup still runs; the token spend,
# not the seconds of slim-runner setup, is what #161 measures.) The policy
# outcome is identical — a pure efficiency move (issue #161).
#
# Two deterministic author-family signals, in declaration-precedence order
# (rules/author-model-declaration.md → Precedence: body line wins):
#   1. The PR body `**Author-Model:**` line — structured, fully-enumerable
#      whitespace-separated canonical ids.
#   2. Fallback: the `Co-authored-by:` trailer EMAIL on the PR's commits.
#      The email is a structured field, not free text — Claude Code emits
#      `<noreply@anthropic.com>` and Codex emits `<noreply@openai.com>`
#      (Codex `commit_attribution` default). This gate maps the email
#      DOMAIN to a family (@anthropic.com → anthropic, @openai.com →
#      openai); it does NOT attempt the free-form display-name
#      normalization (`Claude Opus 4.8 (1M context)` -> `claude-opus-4-8`),
#      which is the regex trap that stays with the agent LLM.
#
# Conservatism (why a false self-skip can't happen):
#   should_skip is true ONLY when a signal resolves to a confident
#   same-family-only result (resolve-author-family.sh decision "skip").
#   Every other case — no signal, a missing declaration (request_changes),
#   or a cross-family / both-run / human-only result (review) — yields
#   should_skip false, so the agent activates and does the robust
#   extraction (including display-name trailers a custom commit_attribution
#   email would hide from this gate) and the authoritative skip exactly as
#   before. A gate that fails to skip costs the old tokens on that one PR;
#   it never drops a needed review.
#
# Family mapping, the skip predicate, and the decision contract live in
# the sibling resolve-author-family.sh — this script composes it, never
# re-implements it (rules/script-as-black-box.md). The email-domain →
# family-token map below is this gate's own deterministic input
# preparation; the resolved family token (`anthropic` / `openai`) is then
# handed to the resolver, which owns the self-vs-paired decision. See
# resolve-author-family.sh (header docstring).
#
# Usage:
#   author-family-gate.sh --reviewer <openai|anthropic> \
#     [--policy-ref <citation>] [--body-file <path>] [--commits-file <path>]
#
#   --reviewer       This workflow's own reviewer family. Required.
#   --policy-ref     Citation forwarded to the resolver. The gate emits
#                    only the skip decision (never a review body), so the
#                    value is immaterial to this script's output; it
#                    defaults to the in-repo policy path. Optional.
#   --body-file      File to read the PR body from. Defaults to stdin, which
#                    is how the gate job feeds `${{ github.event.pull_request.body }}`.
#   --commits-file   File holding the PR's commit message bodies (e.g.
#                    `gh pr view N --json commits -q '.commits[].messageBody'`),
#                    scanned for `Co-authored-by:` trailer emails when the
#                    body has no Author-Model line. Optional; when absent
#                    the gate runs body-line-only.
#
# In:  PR body on stdin (or --body-file); optional commit bodies via --commits-file.
# Out: one JSON object on stdout (last line):
#   {"should_skip": true|false,
#    "decision": "review"|"skip"|"request_changes"|"no-declaration",
#    "reviewer": "<R>",
#    "source": "body"|"trailer"|"none",
#    "tokens": ["<token>", ...]}   // tokens handed to the resolver
#   decision is "no-declaration" (source "none") when neither a body
#   Author-Model line nor a recognized Co-authored-by trailer email was
#   found — the resolver is not consulted and the agent owns the path.
#   Exit 0 on any resolved gate decision; non-zero only on a usage error
#   (unknown/missing --reviewer, missing input file, resolver failure),
#   with a diagnostic on stderr.

set -euo pipefail

die() { echo "author-family-gate: $1" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOLVER="${SCRIPT_DIR}/resolve-author-family.sh"

# Set by main() from the CLI, read by the emit helpers below. Kept as
# globals (not main()-locals) so emit()/resolve_and_emit() see them.
reviewer=""
policy_ref="rules/author-model-declaration.md"

json_str() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; printf '"%s"' "$s"; }

# Emit the gate verdict and exit. Args: should_skip decision source tokens...
emit() {
  local skip="$1" decision="$2" source="$3"; shift 3
  local tok_json="[" i
  for i in "$@"; do
    [[ "$tok_json" != "[" ]] && tok_json+=","
    tok_json+="$(json_str "$i")"
  done
  tok_json+="]"
  printf '{"should_skip":%s,"decision":%s,"reviewer":%s,"source":%s,"tokens":%s}\n' \
    "$skip" "$(json_str "$decision")" "$(json_str "$reviewer")" \
    "$(json_str "$source")" "$tok_json"
  exit 0
}

# Delegate the family decision to the resolver — never re-map families here.
# Args: source token...  → emits the verdict (skip only on resolver "skip").
resolve_and_emit() {
  local source="$1"; shift
  local resolver_out decision
  # Invoke via `bash` — tessl publish/install ships plugin files mode
  # 0644, so the installed resolver has no exec bit (issue #166).
  resolver_out="$(bash "$RESOLVER" --reviewer "$reviewer" --policy-ref "$policy_ref" -- "$@")" \
    || die "resolver exited non-zero for tokens: $*"
  if [[ "$resolver_out" =~ \"decision\":\"([a-z_]+)\" ]]; then
    decision="${BASH_REMATCH[1]}"
  else
    die "could not parse decision from resolver output: ${resolver_out}"
  fi
  if [[ "$decision" == "skip" ]]; then
    emit true "$decision" "$source" "$@"
  else
    emit false "$decision" "$source" "$@"
  fi
}

main() {
  local body_file="" commits_file=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --reviewer)
        [[ $# -ge 2 ]] || die "--reviewer requires a value"
        reviewer="$2"; shift 2 ;;
      --policy-ref)
        [[ $# -ge 2 ]] || die "--policy-ref requires a value"
        policy_ref="$2"; shift 2 ;;
      --body-file)
        [[ $# -ge 2 ]] || die "--body-file requires a value"
        body_file="$2"; shift 2 ;;
      --commits-file)
        [[ $# -ge 2 ]] || die "--commits-file requires a value"
        commits_file="$2"; shift 2 ;;
      *)
        die "unknown argument: $1" ;;
    esac
  done

  case "$reviewer" in
    openai|anthropic) ;;
    "") die "--reviewer is required (openai|anthropic)" ;;
    *) die "--reviewer must be 'openai' or 'anthropic', got '$reviewer'" ;;
  esac
  [[ -f "$RESOLVER" && -r "$RESOLVER" ]] || die "resolver not a readable file at ${RESOLVER}"

  # Read the PR body (stdin by default).
  local body
  if [[ -n "$body_file" ]]; then
    [[ -f "$body_file" ]] || die "--body-file not found: ${body_file}"
    body="$(cat "$body_file")"
  else
    body="$(cat)"
  fi

  # --- Signal 1: PR body `**Author-Model:**` line (preferred, wins) -------
  # Extract the FIRST `**Author-Model:**` (or bare `Author-Model:`) line's
  # value, then split it on ASCII whitespace into canonical-id tokens.
  local value="" line
  local found_body=0
  while IFS= read -r line; do
    line="${line%$'\r'}"
    if [[ "$line" =~ ^[[:space:]]*\*\*Author-Model:\*\*[[:space:]]*(.*)$ ]]; then
      value="${BASH_REMATCH[1]}"; found_body=1; break
    fi
    if [[ "$line" =~ ^[[:space:]]*Author-Model:[[:space:]]*(.*)$ ]]; then
      value="${BASH_REMATCH[1]}"; found_body=1; break
    fi
  done <<< "$body"

  if [[ $found_body -eq 1 ]]; then
    # read -ra splits on IFS (whitespace) without glob expansion.
    local -a body_tokens=()
    read -ra body_tokens <<< "$value"
    # A present body line wins over the trailer even when it is empty
    # (rules/author-model-declaration.md Precedence: body beats trailer). A
    # blank `**Author-Model:**` is a malformed/missing declaration — hand
    # the resolver its zero tokens (→ request_changes, should_skip false)
    # and do NOT fall through to trailer parsing, matching the agent's
    # Step 1.
    if [[ ${#body_tokens[@]} -gt 0 ]]; then
      resolve_and_emit body "${body_tokens[@]}"
    else
      resolve_and_emit body
    fi
  fi

  # --- Signal 2: Co-authored-by trailer email on the PR's commits ---------
  # Only consulted when the body carried no Author-Model declaration. Maps
  # the trailer email DOMAIN to a family token; unknown domains (human
  # committers, customized commit_attribution) contribute nothing and fall
  # through to the agent. Dedupe so a multi-commit PR yields each family at
  # most once.
  if [[ -n "$commits_file" ]]; then
    [[ -f "$commits_file" ]] || die "--commits-file not found: ${commits_file}"
    local -a fam_tokens=()
    has_family() { local n="$1" e; for e in "${fam_tokens[@]:-}"; do [[ "$e" == "$n" ]] && return 0; done; return 1; }
    # Match a Co-authored-by trailer (either capitalization) carrying an
    # <email>; capture the email between <...>. The pattern lives in a
    # variable: angle brackets are literal in ERE but bash's `[[ =~ ]]`
    # parser chokes on an inline `<`/`>`, and glibc reads `\<`/`\>` as word
    # boundaries — a variable sidesteps both traps.
    local coauthor_re='[Cc]o-[Aa]uthored-[Bb]y:.*<([^>]+)>'
    local email fam
    # `|| [[ -n "$line" ]]` processes a final line with no trailing newline
    # — gh/jq output usually terminates it, but a message body may not.
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      if [[ "$line" =~ $coauthor_re ]]; then
        email="${BASH_REMATCH[1]}"
        email="${email,,}"   # domains are case-insensitive
        fam=""
        case "$email" in
          *@anthropic.com) fam="anthropic" ;;
          *@openai.com)    fam="openai" ;;
        esac
        if [[ -n "$fam" ]] && ! has_family "$fam"; then
          fam_tokens+=("$fam")
        fi
      fi
    done < "$commits_file"

    if [[ ${#fam_tokens[@]} -gt 0 ]]; then
      resolve_and_emit trailer "${fam_tokens[@]}"
    fi
  fi

  # --- No deterministic signal → defer to the agent -----------------------
  emit false "no-declaration" "none"
}

# Entry-point guard (rules/file-hygiene.md): run main() only when executed
# directly, so the script stays sourceable for testing.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
