#!/usr/bin/env bash
# Resolve the author-model declaration into a reviewer self-gate decision.
#
# The paired policy reviewers (review-openai.md / review-anthropic.md) must
# decide whether the cross-family reviewer runs or the same-family reviewer
# short-circuits. That decision is a pure function of (a) the declared
# model-id tokens and (b) which family THIS reviewer is — fully enumerable
# prefix logic with no natural-language ambiguity. Per
# rules/script-delegation.md it belongs in a script, not LLM prose: a
# reviewer LLM once mis-mapped `claude-opus-4-8` to its own (openai) family
# and falsely self-skipped, leaving an AI-authored PR with zero policy
# review (issue #145).
#
# The LLM keeps ONLY the regex-trap half — extracting/normalizing tokens
# from the PR body `**Author-Model:**` line or free-form `Co-authored-by:`
# trailer display names. It hands this script clean tokens; the script
# never reads the PR body or trailers, so no free-text parsing enters here.
#
# Family mapping (rules/author-model-declaration.md → Model Families):
#   claude-*            -> anthropic
#   gpt-*, codex-*      -> openai
#   gemini-*            -> google
#   human              -> none   (excluded from the family set F)
#   anything else      -> the literal token as an ad-hoc family
#
# Decision (R = this reviewer's family, P = the paired family):
#   no tokens at all              -> request_changes (missing declaration)
#   R in F AND P not in F         -> skip   (same-family; paired reviewer is cross-family)
#   otherwise                    -> review (this reviewer is cross-family, or
#                                            degraded both-run fallback, or human-only)
# The skip path fires ONLY on confident same-family-only resolution, so an
# unrecognized or newer id (mapped to anthropic/google/ad-hoc, never to the
# reviewer's own family by accident) can never produce a false self-skip.
#
# Usage:
#   resolve-author-family.sh --reviewer <openai|anthropic> \
#     --policy-ref <citation> [--] [<model-id-token> ...]
#
#   --reviewer     This workflow's own reviewer family. Required.
#   --policy-ref   Citation string interpolated verbatim into the emitted
#                  review bodies — `rules/author-model-declaration.md` for
#                  the in-repo workflow, `jbaruch/coding-policy:
#                  author-model-declaration` for the consumer template.
#                  Required.
#   tokens         Zero or more already-extracted model-id strings. Zero
#                  tokens means the LLM found no declaration at all.
#
# Out: one JSON object on stdout (last line):
#   {"reviewer": "<R>",
#    "paired": "<P>",
#    "families": ["<family>", ...],   // set F, sorted, "none" excluded
#    "decision": "review" | "skip" | "request_changes",
#    "review_event": "COMMENT" | "REQUEST_CHANGES" | null,
#    "review_body": "<verbatim body for submit_pull_request_review>" | null}
#   review_event/review_body are null when decision is "review" (the
#   reviewer proceeds to the substantive steps and composes its own
#   verdict). Exit 0 on any resolved decision; non-zero only on a usage
#   error (unknown/missing --reviewer or --policy-ref), with a diagnostic
#   on stderr.

set -euo pipefail

die() { echo "resolve-author-family: $1" >&2; exit 2; }

reviewer=""
policy_ref=""
tokens=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reviewer)
      [[ $# -ge 2 ]] || die "--reviewer requires a value"
      reviewer="$2"; shift 2 ;;
    --policy-ref)
      [[ $# -ge 2 ]] || die "--policy-ref requires a value"
      policy_ref="$2"; shift 2 ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do tokens+=("$1"); shift; done ;;
    --*)
      die "unknown flag: $1" ;;
    *)
      tokens+=("$1"); shift ;;
  esac
done

case "$reviewer" in
  openai) paired="anthropic" ;;
  anthropic) paired="openai" ;;
  "") die "--reviewer is required (openai|anthropic)" ;;
  *) die "--reviewer must be 'openai' or 'anthropic', got '$reviewer'" ;;
esac
[[ -n "$policy_ref" ]] || die "--policy-ref is required"

# Map one token to its family (rules/author-model-declaration.md).
family_of() {
  case "$1" in
    claude-*)        echo "anthropic" ;;
    gpt-*|codex-*)   echo "openai" ;;
    gemini-*)        echo "google" ;;
    human)           echo "none" ;;
    *)               echo "$1" ;;   # ad-hoc family = literal token
  esac
}

# Build set F (deduped, "none" excluded), preserving determinism via sort.
declare -a fam_set=()
in_set() { local n="$1" e; for e in "${fam_set[@]:-}"; do [[ "$e" == "$n" ]] && return 0; done; return 1; }

for t in "${tokens[@]:-}"; do
  [[ -n "$t" ]] || continue
  f="$(family_of "$t")"
  [[ "$f" == "none" ]] && continue
  in_set "$f" || fam_set+=("$f")
done

# Sort families for stable output.
if [[ ${#fam_set[@]} -gt 0 ]]; then
  IFS=$'\n' read -r -d '' -a fam_set < <(printf '%s\n' "${fam_set[@]}" | sort && printf '\0')
fi

self_in=false; paired_in=false
in_set "$reviewer" && self_in=true
in_set "$paired" && paired_in=true

# Count non-empty tokens to distinguish "no declaration" from "human-only".
ntokens=0
for t in "${tokens[@]:-}"; do [[ -n "$t" ]] && ntokens=$((ntokens + 1)); done

# JSON string escaper (backslash + double-quote; bodies carry no controls).
json_str() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; printf '"%s"' "$s"; }

if [[ $ntokens -eq 0 ]]; then
  decision="request_changes"
  review_event="REQUEST_CHANGES"
  review_body="Missing Author-Model declaration — add **Author-Model:** to the PR body (or include a model-identifying Co-authored-by trailer). See ${policy_ref}."
elif [[ "$self_in" == true && "$paired_in" == false ]]; then
  decision="skip"
  review_event="COMMENT"
  review_body="Skipping: self-review-bias — author-family ${reviewer}; see ${policy_ref}."
else
  decision="review"
  review_event=""
  review_body=""
fi

# Emit families JSON array.
fam_json="["
for i in "${!fam_set[@]}"; do
  [[ $i -gt 0 ]] && fam_json+=","
  fam_json+="$(json_str "${fam_set[$i]}")"
done
fam_json+="]"

if [[ -n "$review_event" ]]; then
  ev_json="$(json_str "$review_event")"
  bd_json="$(json_str "$review_body")"
else
  ev_json="null"
  bd_json="null"
fi

printf '{"reviewer":%s,"paired":%s,"families":%s,"decision":%s,"review_event":%s,"review_body":%s}\n' \
  "$(json_str "$reviewer")" \
  "$(json_str "$paired")" \
  "$fam_json" \
  "$(json_str "$decision")" \
  "$ev_json" \
  "$bd_json"
